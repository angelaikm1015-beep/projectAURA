"""Deep composing — the model writes every note of every part.

Light mode (composer.compose) is built for speed: the model writes a chord grid
and a few short four-voice chorales, and form.py multiplies them out across
the ensemble and the form. That is arranging by rule, and it sounds like it.

Deep mode leaves the composing to the model, however long it takes, in the
order a composer works:

1. **The architecture.** One call with the most thinking time: the themes, the
   form section by section, the harmony of every bar (one chord or two, held
   or quickening as the music needs), each section's key, dynamic, material
   and orchestration, and what each player does across the piece. It always
   closes V7 – I.
2. **The melody.** The tune of the whole piece, start to finish, section by
   section in order — each call sees the melody so far, so it is one line with
   one arc rather than a string of separate phrases.
3. **The parts, one at a time.** Each player's line is composed on its own,
   against the melody and the harmony and against every part already written:
   the bass first, then the players who carry the tune, then the inner and
   accompanying parts, percussion last. The piece is cut into passages of up
   to PASSAGE_BARS bars so each reply stays short enough to finish; passages
   are worked on side by side, but inside one the players go strictly in turn.
4. **Review.** Python counts every bar and checks every note against the
   player's range and every held note against the chord, and hands what it
   finds back to the model to correct. It changes notes itself only as a last
   resort, so the staves still line up, and finishes every staff the same way:
   'rit.' over the last two bars and the final chord held under a fermata.

Nothing here is time-boxed. A call gets CALL_TIMEOUT and a retry, and the
browser polls for the result rather than holding a request open (see app.py).
"""

import json
import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAIError

import abc_check
import composer
import form
import options
import orchestrate
from composer import ComposerError, UNIT_NOTE_LENGTH, _obj

_log = logging.getLogger(__name__)

PLAN_EFFORT = os.environ.get("DEEP_PLAN_EFFORT", "high")
WRITE_EFFORT = os.environ.get("DEEP_WRITE_EFFORT", "medium")
REPAIR_EFFORT = os.environ.get("DEEP_REPAIR_EFFORT", "medium")
CALL_TIMEOUT = float(os.environ.get("DEEP_CALL_TIMEOUT", "900"))
CONCURRENCY = int(os.environ.get("DEEP_CONCURRENCY", "6"))
MAX_TOKENS = composer.MAX_OUTPUT_TOKENS

PASSAGE_BARS = 32     # the most bars one player is asked for in one call
SECTION_BARS = 24     # the longest section the plan may have
ATTEMPTS = 2          # a call that fails outright is asked for again
REPAIR_ROUNDS = 2     # correction rounds before Python steps in

# Used only for the estimate shown before the button is pressed.
PLAN_SECONDS = 240
MELODY_SECONDS = 150
PART_SECONDS = 150

# The tune is not any one instrument's: it is written at concert pitch in a
# middle register, and each player who takes it moves it into their own.
TEXTURES = ("tutti", "reduced", "solo")

MELODY = {"partId": "melody", "label": "Melody", "instrument": "Melody", "clef": "treble",
          "transpose": 0, "easyRange": "-", "fullRange": "-", "staves": 1, "pitched": True}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_PLAN_FIELDS = composer.PLAN_SCHEMA["properties"]

THEME_SCHEMA = _obj({
    "id": {"type": "string", "description": "'A', 'B', 'C'."},
    "abc": {"type": "string",
            "description": "The melody in ABC: concert pitch, home key, treble register, "
                           "L:1/8, bars separated by |. Music only."},
    "description": {"type": "string",
                    "description": "One sentence: its character and what makes it memorable."},
})

SECTION_SCHEMA = _obj({
    "label": {"type": "string", "description": "Rehearsal name: 'Intro', 'A', 'B', 'A2', "
                                               "'Development', 'Coda'."},
    "bars": {"type": "integer", "description": "4 to 16."},
    "key": {"type": "string", "description": "The sounding key of this section, two or "
                                             "three words: 'G major', 'E minor'."},
    "chords": {"type": "array", "items": {"type": "string"},
               "description": "Concert-pitch harmony for EACH bar, in order: one chord "
                              "('D7/F#') or two sharing the bar separated by a space "
                              "('Em7 A7'). Repeat a chord in consecutive bars to hold it. "
                              "Its length is the section's bar count."},
    "dynamic": {"type": "string", "enum": list(form.DYNAMICS)},
    "lead": {"type": ["string", "null"],
             "description": "partId of the player carrying the melody, or null."},
    "texture": {"type": "string", "enum": ["tutti", "reduced", "solo"],
                "description": "'solo': the lead alone in the foreground over a thin, "
                               "quiet accompaniment, several players resting."},
    "material": {"type": "string",
                 "description": "Which theme(s) and what happens to them: 'A in the "
                                "relative minor, fragmented', 'new episode on A's head "
                                "motif, in canon'."},
    "orchestration": {"type": "string",
                      "description": "Who plays what: tune, counter-melody, bass line, "
                                     "accompaniment figure, who rests."},
})

PLAN_SCHEMA = _obj({
    **{key: _PLAN_FIELDS[key] for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                          "timeSignature", "concertKey")},
    "themes": {"type": "array", "items": THEME_SCHEMA,
               "description": "Two or three themes, each four to eight bars."},
    "sections": {"type": "array", "items": SECTION_SCHEMA,
                 "description": "The whole piece, in order."},
    "partRoles": {"type": "array", "items": _obj({
        "partId": {"type": "string"},
        "role": {"type": "string", "description": "One sentence for the player."},
    })},
    "programNote": {"type": "string", "description": "2-4 sentences for the players."},
    "rehearsalTips": {"type": "string", "description": "2-3 sentences."},
    "conductor": composer.CONDUCTOR_SCHEMA,
})

_SECTION_TEXT = _obj({
    "section": {"type": "string", "description": "The section id: 's1', 's2' …"},
    "abc": {"type": "string", "description": "Exactly that section's bar count."},
    "abcLower": {"type": ["string", "null"],
                 "description": "Left hand of a grand staff, otherwise null."},
})

MELODY_SCHEMA = _obj({"sections": {"type": "array", "items": _SECTION_TEXT,
                                   "description": "One entry per section asked for."}})

PART_SCHEMA = _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "sections": {"type": "array", "items": _SECTION_TEXT,
                 "description": "One entry per section asked for."},
    "performanceNote": {"type": "string", "description": "One sentence for this player."},
})


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------

def _call(prompt: str, name: str, schema: dict, effort: str, model: str) -> dict:
    try:
        completion = composer._create(
            model=model,
            messages=[{"role": "system", "content": composer.SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": name, "strict": True, "schema": schema}},
            temperature=0.8,
            max_tokens=MAX_TOKENS,
            reasoning_effort=effort,
            timeout=CALL_TIMEOUT,
        )
    except OpenAIError as error:
        raise ComposerError(composer._explain(error, model)) from error
    if completion.choices[0].finish_reason == "length":
        raise ComposerError("The reply was cut off while being written.", status=502)
    try:
        return json.loads(completion.choices[0].message.content)
    except (TypeError, ValueError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error


def _asked(name: str, prompt: str, schema: dict, effort: str, model: str) -> tuple:
    """A call, asked again once if it fails: (reply or None, last error)."""
    error = None
    for _ in range(ATTEMPTS):
        try:
            return _call(prompt, name, schema, effort, model), None
        except ComposerError as failure:
            error = failure
            _log.warning("%s failed: %s", name, failure)
    return None, error


def _by_section(entries: list, wanted: set) -> dict:
    """{section index: {abc, abcLower}} from a reply's section list."""
    out = {}
    for entry in entries or []:
        match = re.search(r"\d+", str(entry.get("section") or ""))
        index = int(match.group(0)) - 1 if match else -1
        if index in wanted and entry.get("abc"):
            out[index] = {"abc": entry["abc"], "abcLower": entry.get("abcLower")}
    return out


# ---------------------------------------------------------------------------
# The architecture
# ---------------------------------------------------------------------------

KEY_AREAS = {
    "beginner": "Every section stays in the home key.",
    "intermediate": "At most one section in a closely related key; home well before the end.",
    "higher-intermediate": "At least one real excursion to a different key before the "
                           "home key returns.",
    "pro": "At least two different key areas away from home, reached by real "
           "modulations, before the home key returns.",
}


def _length(level: str) -> str:
    return composer.LENGTHS[level][0]


def _length_line(level: str) -> str:
    return (f"LENGTH: {_length(level)} measures in total. Every measure of every part "
            "is composed by you — nothing is repeated or derived automatically.")


SOLOS = ("Give the piece real solos: at least one section of four bars or more "
         "(two or more at higher-intermediate and pro) where one player with a singing "
         "voice — oboe, clarinet, flute, horn, cello, violin, trumpet — plays alone over "
         "a few quiet players, the rest resting.")


def _plan_prompt(request: dict, roster: list) -> str:
    level = request["level"]
    return "\n".join([
        composer.build_user_prompt(request, roster, length=_length_line(level)),
        "",
        "YOUR JOB RIGHT NOW: compose the architecture of the piece. Next you will",
        "compose its whole melody, and then every part, one player at a time,",
        "following this plan.",
        "",
        "THEMES — two or three, written out as real melodies in ABC (concert pitch,",
        "home key, treble register, L:1/8), four to eight bars each. Theme A is the",
        "main idea; theme B contrasts with it in contour, rhythm and harmony. These are",
        "the material the whole piece is built from, so make them memorable.",
        "",
        f"SECTIONS — the whole piece in order, 4 to 16 bars each, {_length(level)} bars",
        "in total. It needs a real shape: exposition, contrast, development, a climax,",
        "a varied return and a close. A theme that comes back is never a copy: change",
        "its orchestration, register, harmonisation or counter-melody, and vary the",
        "tune itself — ornament it, extend it, alter its rhythm or ending. No two",
        "sections may have the same material and orchestration.",
        "  * chords: the harmony of every bar, concert pitch, named exactly ('D7/F#',",
        "    'Bbmaj7', 'F#ø7', 'Esus4'). The parts are composed on them, so they are",
        "    binding. Use inversions, secondary dominants, suspensions that resolve;",
        "    avoid a grid that sits on I, IV and V.",
        "  * key: " + KEY_AREAS[level] + " The last section is in the home key.",
        "  * dynamic: go near pp and near ff somewhere, with at least one sudden change.",
        "  * lead: pass the tune between instruments and families.",
        "  * texture: 'tutti' for arrivals and climaxes, 'reduced' for lighter",
        "    passages, 'solo' for one player alone in the foreground. " + SOLOS,
        "  * BALANCE: the orchestra blends. Where a lead carries the tune, everyone",
        "    else accompanies beneath it (marked a step quieter, two under a solo);",
        "    only a tutti with no lead has everyone at the section's level.",
        "  * material and orchestration: say exactly what happens and who plays what,",
        "    so every passage can be composed from this alone.",
        "",
        *composer.HARMONIC_RHYTHM,
        "",
        "partRoles: one entry per player — what they do across the piece.",
        "programNote, rehearsalTips: short. conductor: "
        + ("fill it in with real measure numbers and player names."
           if request["conductor"] else "null."),
    ])


def _plan(request: dict, roster: list, model: str) -> dict:
    plan = _call(_plan_prompt(request, roster), "deep_plan", PLAN_SCHEMA, PLAN_EFFORT, model)
    composer._tidy_labels(plan)
    home = plan["concertKey"]
    part_ids = {part["partId"] for part in roster}

    sections, measure = [], 1
    for entry in plan.get("sections") or []:
        chords = [" ".join(form.bar_chords(c)) for c in entry.get("chords") or []
                  if str(c).strip()]
        try:
            bars = int(entry.get("bars") or len(chords))
        except (TypeError, ValueError):
            bars = len(chords)
        bars = max(1, min(SECTION_BARS, bars or 4))
        filler = chords[-1] if chords else home.split()[0]
        chords = (chords + [filler] * bars)[:bars]
        key = str(entry.get("key") or "").strip() or home
        if request["level"] == "beginner":
            key = home
        sections.append({
            "label": str(entry.get("label") or f"S{len(sections) + 1}").strip()[:24],
            "bars": bars, "start": measure, "end": measure + bars - 1,
            "key": key, "chords": chords,
            "dynamic": entry.get("dynamic") if entry.get("dynamic") in form.DYNAMICS else "mf",
            "lead": entry.get("lead") if entry.get("lead") in part_ids else None,
            "texture": entry.get("texture") if entry.get("texture") in TEXTURES else "tutti",
            "material": str(entry.get("material") or ""),
            "orchestration": str(entry.get("orchestration") or ""),
        })
        measure += bars
    for s in sections:
        if not s["lead"] and s["texture"] == "solo":
            s["texture"] = "reduced"      # a solo needs a soloist
    if not sections:
        raise ComposerError("The planner didn't lay out any sections this time. "
                            "Please try again.", status=502)

    # Every piece closes the same way: home, V7, then the tonic held.
    last = sections[-1]
    last["key"] = home
    last["chords"] = form.close_on_cadence(last["chords"], home)

    plan["_sections"] = sections
    plan["measureCount"] = measure - 1
    plan["sections"] = [{"name": s["label"], "fromMeasure": s["start"],
                         "toMeasure": s["end"], "description": s["material"]}
                        for s in sections]
    plan["harmony"] = [{"measure": s["start"] + i, "chord": chord}
                       for s in sections for i, chord in enumerate(s["chords"])]
    if not request["conductor"]:
        plan["conductor"] = None
    composer._tidy_conductor(plan)
    return plan


def _passages(plan: dict) -> list:
    """Runs of whole sections of at most PASSAGE_BARS bars: [[section index]]."""
    out, run, bars = [], [], 0
    for index, s in enumerate(plan["_sections"]):
        if run and bars + s["bars"] > PASSAGE_BARS:
            out.append(run)
            run, bars = [], 0
        run.append(index)
        bars += s["bars"]
    if run:
        out.append(run)
    return out


def _writing_order(plan: dict, roster: list) -> list:
    """Who is composed first: the bass, then the tune's carriers, then the rest.

    The bass and the melody are the frame every other line is written inside,
    so they come first; percussion, which colours rather than harmonises, last.
    """
    pitched = [p for p in roster if p["pitched"]]

    def lowest(part):
        low = abc_check.parse_scientific(part["fullRange"].partition("-")[0]) or 60
        return low + int(part["transpose"])

    bass = min(pitched, key=lowest) if pitched else None
    leads = {}
    for s in plan["_sections"]:
        if s["lead"]:
            leads[s["lead"]] = leads.get(s["lead"], 0) + s["bars"]
    by_lead = sorted((p for p in pitched if p["partId"] in leads and p is not bass),
                     key=lambda p: -leads[p["partId"]])
    rest = [p for p in pitched if p is not bass and p["partId"] not in leads]
    return ([bass] if bass else []) + by_lead + rest + [p for p in roster if not p["pitched"]]


# ---------------------------------------------------------------------------
# Shared prompt pieces
# ---------------------------------------------------------------------------

def _overview(plan: dict, request: dict, roster: list, here: list) -> list:
    by_id = {part["partId"]: part for part in roster}
    lines = [
        composer.build_user_prompt(request, roster, length=_length_line(request["level"])),
        "",
        f"THE PIECE: \"{plan['title']}\" — {plan['concertKey']}, {plan['timeSignature']}, "
        f"{plan['tempoText']} ({plan['tempoBpm']} bpm), {plan['measureCount']} measures. "
        "It ends with a perfect cadence (V7 – I), slowing over the last two bars, and a "
        "long final chord held under a fermata.",
        "",
        "THE THEMES (concert pitch):",
    ]
    for theme in plan.get("themes") or []:
        lines += [f"  [{theme.get('id')}] {theme.get('description', '')}",
                  f"    {theme.get('abc', '')}"]
    lines += ["", "THE FORM (the whole piece; >> marks the sections you are writing now):"]
    for index, s in enumerate(plan["_sections"]):
        mark = ">>" if index in here else "  "
        lead = f", tune: {by_id[s['lead']]['label']}" if s["lead"] else ""
        if s["texture"] == "solo" and s["lead"]:
            lead = f", SOLO: {by_id[s['lead']]['label']}"
        lines.append(f"{mark} s{index + 1} mm.{s['start']}-{s['end']} [{s['label']}] "
                     f"{s['key']}, {s['dynamic']}{lead}. {s['material']} / {s['orchestration']}")
    return lines


def _section_head(plan: dict, index: int) -> list:
    s = plan["_sections"][index]
    sections = plan["_sections"]
    lines = [
        f"SECTION s{index + 1} [{s['label']}] — mm.{s['start']}-{s['end']}, EXACTLY "
        f"{s['bars']} bars; concert key {s['key']}; {s['dynamic']}.",
        "  harmony: " + " | ".join(f"m.{s['start'] + i} {c}" for i, c in enumerate(s["chords"])),
        f"  material: {s['material']}",
        f"  orchestration: {s['orchestration']}",
    ]
    if index + 1 == len(sections):
        lines.append("  THE END: the last bar is the tonic chord, one note held for the "
                     "whole bar (it will be held under a fermata).")
    return lines


def _rules(beats) -> list:
    return [
        "RULES",
        "  * One entry per section listed, with its id (s1, s2 …).",
        f"  * EXACTLY each section's bar count, every bar totalling {abc_check._fmt(beats)}",
        f"    units at L:{UNIT_NOTE_LENGTH} (a plain letter is 1, C2 is 2, C/2 is a half,",
        "    a triplet (3CDE takes 2). Count every bar before answering.",
        f"  * A bar of rest is z{abc_check._fmt(beats)}; resting bars are still written.",
        "  * No header fields, no [K:] changes, no repeat signs, no pickups, no final |]",
        "    — barlines between sections and key changes are added for you.",
        "  * Harmony: every held note and every note on a beat belongs to the chord",
        "    sounding at that moment (a bar with two chords changes on the beat),",
        "    unless it is a suspension, appoggiatura or passing note resolving by step.",
    ]


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def _final_bar_problem(part: dict, music: str, key: str, units, label: str) -> list:
    bars = orchestrate.split_bars(music or "")
    if not bars or not part["pitched"]:
        return []
    events = (orchestrate.read_bars(bars[-1], key) or [[]])[0]
    notes = [(m, d) for m, d in events if m is not None]
    if notes and (len(notes) > 1 or notes[0][1] != units):
        return [f"{label}: the final bar must be ONE note (or chord) of the tonic held for "
                "the whole bar — the closing chord."]
    return []


def _defects(part: dict, text: dict, index: int, plan: dict, level: str) -> list:
    s = plan["_sections"][index]
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    beat = form.beat_units(plan["timeSignature"])
    key = form.written_key_for(part, s["key"]) if part["pitched"] else "C"
    low, high = composer._range_for(part, level)
    chords = {"chords": s["chords"], "bars": s["bars"],
              "voices": {v: [[] for _ in range(s["bars"])] for v in form.VOICES}}
    staves = [(part["label"], text.get("abc") or "")]
    if part["staves"] == 2:
        staves.append((f"{part['label']} left hand", text.get("abcLower") or ""))

    problems = []
    for label, music in staves:
        label = f"{label} s{index + 1}"
        found = form.check_phrase(music, s["bars"], units, label)
        if low and part["pitched"]:
            found += abc_check.check_range(music, low, high, label)
        if not found:
            found += form.harmony_clashes(music, part, chords, key, beat, label)
            if index + 1 == len(plan["_sections"]):
                found += _final_bar_problem(part, music, key, units, label)
        problems += found
    return problems


# A bar counts as material when it has at least this many notes; held notes
# and rests are allowed to recur.
MOTION_NOTES = 3
_MARKINGS_RE = re.compile(r'!.*?!|".*?"|\((?!\d)|\)|\s')
_NOTE_RE = re.compile(r"[A-Ga-g]")


def _material(bar: str):
    """A bar reduced to its notes and rhythm, or None if it has no motion."""
    plain = _MARKINGS_RE.sub("", bar)
    return plain if len(_NOTE_RE.findall(plain)) >= MOTION_NOTES else None


def _repeats(texts: dict, index: int) -> list:
    """Bars of the melody's section ``index`` that copy what was already played."""
    def bars(i):
        return [_material(b) for b in orchestrate.split_bars((texts.get(i) or {}).get("abc")
                                                               or "")]
    here = bars(index)
    earlier = [b for i in sorted(texts) if i < index for b in bars(i) if b]
    seen = {}
    for b in earlier:
        seen[b] = seen.get(b, 0) + 1
    copied, problems = [], []
    for number, b in enumerate(here, 1):
        if not b:
            continue
        if seen.get(b, 0) >= 2:
            copied.append(number)
        seen[b] = seen.get(b, 0) + 1
    motion = sum(1 for b in here if b)
    if len(copied) >= 2 and len(copied) * 3 >= motion:
        problems.append(
            f"melody s{index + 1}: bars {', '.join(map(str, copied))} of this section are "
            "played for the third time or more, note for note. It sounds repetitive: vary "
            "them — change the rhythm, ornament, alter the contour or the phrase ending — "
            "keeping the harmony.")
    return problems


def _rest_line(bars: int, units) -> str:
    return " | ".join([f"z{abc_check._fmt(units)}"] * bars) + " |"


def _settle(part: dict, text: dict, index: int, plan: dict) -> dict:
    """Last resort, after the model's corrections: make the staves line up."""
    s = plan["_sections"][index]
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    key = form.written_key_for(part, s["key"]) if part["pitched"] else "C"
    fixed = dict(text)
    for field in ("abc", "abcLower"):
        if not text.get(field):
            continue
        line = form.fix_line(text[field], s["bars"], units, part, key)
        if form.check_phrase(line, s["bars"], units, ""):
            line = _rest_line(s["bars"], units)
        fixed[field] = line
    return fixed


def _repair_prompt(plan: dict, part: dict, texts: dict, broken: dict) -> str:
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    who = "the melody" if part is MELODY else f"[{part['partId']}] {part['label']}"
    lines = [f"You composed {who} for \"{plan['title']}\" ({plan['timeSignature']}). "
             "I checked it and found the problems listed under each section.",
             f"Every bar totals {abc_check._fmt(beats)} units at L:{UNIT_NOTE_LENGTH} "
             "(a plain letter is 1, C2 is 2, C/2 is a half).", ""]
    for index, problems in broken.items():
        s = plan["_sections"][index]
        key = form.written_key_for(part, s["key"]) if part["pitched"] else "C"
        lines += [f"s{index + 1} — EXACTLY {s['bars']} bars, written key {key}, harmony "
                  + " | ".join(s["chords"]) + ":",
                  f"  {texts[index].get('abc')}"]
        if texts[index].get("abcLower"):
            lines.append(f"  left hand: {texts[index]['abcLower']}")
        lines += [f"  - {p}" for p in problems[:40]]
        lines.append("")
    lines += ["Correct exactly these problems: durations, bar count, notes out of range,",
              "held or on-the-beat notes off the chord, the closing bar, bars repeated",
              "too often. Keep everything else — the line, its rhythm, its articulation.",
              "Return only these sections."]
    if part is not MELODY:
        lines.append(f"Use partId {part['partId']}.")
    return "\n".join(lines)


def _review(part: dict, texts: dict, plan: dict, level: str, model: str) -> tuple:
    """Check one line, send what is wrong back to the model, settle the rest.

    Returns (texts, sections Python had to settle).
    """
    def found_in(index, text):
        """(defects, repetition): the second is a matter of taste, never settled."""
        hard = _defects(part, text, index, plan, level)
        soft = _repeats({**texts, index: text}, index) if part is MELODY and not hard else []
        return hard, soft

    broken = {i: found for i, text in texts.items() if any(found := found_in(i, text))}
    schema, name = (MELODY_SCHEMA, "melody_fix") if part is MELODY else (PART_SCHEMA, "part_fix")
    for _ in range(REPAIR_ROUNDS):
        if not broken:
            break
        reply, _ = _asked(name, _repair_prompt(plan, part, texts,
                                               {i: h + s for i, (h, s) in broken.items()}),
                          schema, REPAIR_EFFORT, model)
        if not reply:
            break
        for index, text in _by_section(reply.get("sections"), set(broken)).items():
            hard, soft = found_in(index, text)
            was_hard, was_soft = broken[index]
            if len(hard) <= len(was_hard) and len(hard) + len(soft) < len(was_hard) + len(was_soft):
                texts[index] = text
                broken[index] = (hard, soft)
        broken = {i: found for i, found in broken.items() if any(found)}

    settled = []
    for index, (found, _) in broken.items():
        if not found:
            continue
        before = texts[index]
        texts[index] = _settle(part, before, index, plan)
        if texts[index] != before:
            settled.append(f"{part['label']} s{index + 1}")
        _log.info("%s s%d kept with: %s", part["label"], index + 1, "; ".join(found[:3]))
    return texts, settled


# ---------------------------------------------------------------------------
# The melody
# ---------------------------------------------------------------------------

def _melody_prompt(plan: dict, request: dict, roster: list, run: list, melody: dict) -> str:
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    lines = _overview(plan, request, roster, run)
    before = [i for i in sorted(melody) if i < run[0]]
    if before:
        lines += ["", "THE MELODY SO FAR (concert pitch). Develop it — never reuse its bars",
                  "note for note; a returning theme comes back varied:"]
        for index in before:
            lines.append(f"  s{index + 1}: {melody[index]['abc']}")
    lines += ["", "COMPOSE THE MELODY NOW for these sections:"]
    for index in run:
        lines += _section_head(plan, index)
    lines += [
        "",
        "THE MELODY is the tune of the whole piece: the line a listener remembers.",
        "  * One continuous line with an arc across the piece — it grows from the",
        "    themes, develops them, reaches its climax where the form does, and",
        "    comes home. Never mechanical sequences or note-for-note repeats:",
        "    no bar is played more than twice in the piece, and a theme that",
        "    returns is varied (ornamented, re-rhythmed, extended, a new ending).",
        "  * Phrase it like a singer breathes: two- and four-bar phrases with rests",
        "    or long notes between them; vary the rhythm — long notes against",
        "    running ones, syncopation, a held note at each phrase's high point.",
        "  * Concert pitch, read against each section's concert key signature,",
        "    written between G3 and C6 (players move it into their own octave).",
        "  * Where a section's orchestration has no tune (an accompaniment-only",
        "    passage), write the most important line there instead.",
        "  * Mark dynamics, hairpins, slurs and accents.",
        "",
        *_rules(beats),
        "  * abcLower is always null.",
    ]
    return "\n".join(lines)


def _compose_melody(plan: dict, request: dict, roster: list, passages: list, model: str,
                    tick) -> tuple:
    """The whole tune, passage by passage in order: ({section: text}, settled)."""
    melody, errors = {}, []
    for run in passages:
        reply, error = _asked("melody", _melody_prompt(plan, request, roster, run, melody),
                              MELODY_SCHEMA, WRITE_EFFORT, model)
        if error:
            errors.append(error)
        melody.update(_by_section((reply or {}).get("sections"), set(run)))
        tick()
    if not melody:
        raise errors[0] if errors else ComposerError("The melody couldn't be written. "
                                                     "Please try again.")
    melody, settled = _review(MELODY, melody, plan, request["level"], model)
    return melody, settled


# ---------------------------------------------------------------------------
# The parts, one at a time
# ---------------------------------------------------------------------------

def _melody_for(part: dict, plan: dict, melody: dict, index: int) -> str:
    """The tune at this player's written pitch, for a section they carry it in."""
    s = plan["_sections"][index]
    concert = orchestrate.written_key(s["key"], 0)
    return orchestrate.transpose_abc(melody[index]["abc"], -int(part["transpose"]), concert,
                                     form.written_key_for(part, s["key"]))


def _part_prompt(plan: dict, request: dict, roster: list, run: list, part: dict,
                 melody: dict, written: dict, order: list) -> str:
    level = request["level"]
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    by_id = {p["partId"]: p for p in roster}
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}
    position = order.index(part)
    lines = _overview(plan, request, roster, run)

    lines += ["", "THE MELODY FOR THESE BARS (concert pitch) — everything is written to it:"]
    for index in run:
        if index in melody:
            lines.append(f"  s{index + 1}: {melody[index]['abc']}")

    done = [p for p in order[:position] if p["partId"] in written]
    if done:
        lines += ["", "PARTS ALREADY COMPOSED FOR THESE BARS — final; fit yours against them:"]
        for other in done:
            transposition = composer.TRANSPOSITION_TEXT.get(other["transpose"], "")
            lines.append(f"  [{other['partId']}] {other['label']} ({transposition}):")
            for index in run:
                text = written[other["partId"]].get(index)
                if not text:
                    continue
                key = form.written_key_for(other, plan["_sections"][index]["key"])
                lines.append(f"    s{index + 1} (written key {key}): {text['abc']}")
                if text.get("abcLower"):
                    lines.append(f"    s{index + 1} left hand: {text['abcLower']}")
    later = order[position + 1:]
    if later:
        lines += ["", "STILL TO BE COMPOSED AFTER YOU: " + ", ".join(p["label"] for p in later)]

    family = options.FAMILY_OF.get(part["instrument"], "")
    technique = (options.TECHNIQUE.get(family) or {}).get(level, "")
    transposition = composer.TRANSPOSITION_TEXT.get(
        part["transpose"], f"sounds {part['transpose']} semitones from written")
    lines += [
        "",
        f"YOU ARE COMPOSING [{part['partId']}] {part['label']} — one player, on its own, "
        "deliberately.",
        f"  clef {part['clef']}; {transposition}; {composer._range_line(part, level)}.",
    ]
    if roles.get(part["partId"]):
        lines.append(f"  Role in the piece: {roles[part['partId']]}")
    if technique and part["pitched"]:
        lines.append(f"  Technique to use: {technique}")
    if part["staves"] == 2:
        lines.append("  GRAND STAFF: right hand in \"abc\", left hand in \"abcLower\".")
    if form.accompanies(part, roster):
        lines += ["  IN THIS ORCHESTRA THE KEYBOARD IS A SOFT COLOUR: pp to mp, never",
                  "  louder than mf. Light voicings — two or three notes a hand — and",
                  "  sparse figures: arpeggios, held chords, a doubled bass. No hammered",
                  "  block chords, no octaves in both hands, no doubling the whole texture."]
    if not part["pitched"]:
        lines.append("  Rhythm staff: c = low sound, e = high sound. Groove and colour for "
                     "each section's character, sparing in quiet sections.")
    quota = composer.DIFFICULTY_QUOTA.get(level)
    if quota and part["pitched"]:
        lines += ["  Minimum difficulty where this part has real material:",
                  *[f"    * {q}" for q in quota]]
    lines.append("")

    for index in run:
        s = plan["_sections"][index]
        lines += _section_head(plan, index)
        if part["pitched"]:
            lines.append(f"  YOUR WRITTEN KEY: {form.written_key_for(part, s['key'])}")
            level, cap = form.section_level(s, part["partId"])
            if s["texture"] == "solo" and s["lead"] == part["partId"]:
                lines.append(f"  THIS IS YOUR SOLO: you alone in the foreground, {level}, "
                             "expressive and free; it is marked 'solo' for you.")
            elif s["texture"] == "solo":
                lines.append(f"  UNDER A SOLO: rest, or accompany very lightly at {cap} — "
                             "sparse, sustained, never in the soloist's register.")
            elif cap:
                lines.append(f"  YOU ACCOMPANY HERE: at most {cap}, beneath the tune.")
            if s["lead"] == part["partId"] and index in melody:
                lines += ["  YOU CARRY THE MELODY HERE. It is below at your written pitch;",
                          "  move it by octaves to sit well on your instrument, and shape it",
                          "  idiomatically — but it is the tune, so keep its notes:",
                          f"    {_melody_for(part, plan, melody, index)}"]
        lines.append("")

    lines += [
        "HOW TO COMPOSE YOUR PART",
        "  * You are composing, not arranging: your line has its own shape and",
        "    purpose — the tune, a counter-melody, the bass, an inner voice with real",
        "    motion, an accompaniment figure — as the orchestration says.",
        "  * Listen to the melody and to every part above: move when they hold, hold",
        "    when they move, favour contrary motion, never double the tune unless the",
        "    orchestration asks for it. No parallel fifths or octaves against the",
        "    other independent lines.",
        "  * The lowest sounding part carries the bass the harmony names, inversions",
        "    included.",
        "  * Rest when the orchestration says you rest. Mark the dynamic on your first",
        "    note in every section you play, with hairpins and articulation.",
        "  * BLEND: the orchestra is one sound. Unless you carry the tune, play under",
        "    it — the levels above are a ceiling, not a target.",
        "  * Don't copy yourself: when material returns, change your figure — its",
        "    rhythm, register, articulation or pattern — so no passage is a rerun.",
        "  * Approaching the end, slow your motion: longer values in the last two bars,",
        "    and one held note in the final bar.",
        "",
        *_rules(beats),
        f"  * Write pitch in YOUR WRITTEN KEY for each section. Use partId {part['partId']}.",
    ]
    if part["staves"] != 2:
        lines.append("  * abcLower is always null.")
    return "\n".join(lines)


def _compose_part(plan, request, roster, run, part, melody, written, order, model) -> tuple:
    """One player's line for one passage: (texts, settled, error, performance note)."""
    reply, error = _asked("part", _part_prompt(plan, request, roster, run, part, melody,
                                               written, order), PART_SCHEMA, WRITE_EFFORT,
                          model)
    texts = _by_section((reply or {}).get("sections"), set(run))
    note = (reply or {}).get("performanceNote") or ""
    if not texts:
        return {}, [], error, note
    texts, settled = _review(part, texts, plan, request["level"], model)
    return texts, settled, error, note


def _compose_passage(plan, request, roster, run, melody, order, model, tick) -> tuple:
    """Every player, in turn, for one passage."""
    written = {part["partId"]: {} for part in roster}
    notes, settled, errors, unwritten = {}, [], [], []
    for part in order:
        texts, fixed, error, note = _compose_part(plan, request, roster, run, part, melody,
                                                  written, order, model)
        written[part["partId"]].update(texts)
        settled += fixed
        if note:
            notes[part["partId"]] = note
        if error:
            errors.append(error)
        if len(texts) < len(run):
            unwritten.append(part["label"])
        tick()
    return written, notes, settled, errors, unwritten


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _assemble(plan: dict, roster: list, written: dict, notes: dict) -> list:
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}
    sections = plan["_sections"]
    out = []
    for part in roster:
        pid = part["partId"]
        home = form.written_key_for(part, sections[0]["key"])
        current, upper, lower = home, [], []
        for index, s in enumerate(sections):
            text = written[pid].get(index) or {}
            rest = _rest_line(s["bars"], beats)
            up = orchestrate.split_bars(text.get("abc") or rest)
            low = orchestrate.split_bars(text.get("abcLower") or text.get("abc") or rest)
            key = form.written_key_for(part, s["key"])
            if key != current:
                up[0] = f"[K:{key}] " + up[0]
                low[0] = f"[K:{key}] " + low[0]
                current = key
            joiner = " || " if index else " | "
            upper.append((joiner, up))
            lower.append((joiner, low))
        out.append({
            "partId": pid,
            "abcKey": home,
            "abc": form._join(upper),
            "abcLower": form._join(lower) if part["staves"] == 2 else None,
            "performanceNote": notes.get(pid) or roles.get(pid) or "",
        })
    return out


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _bars_for(level: str) -> int:
    numbers = [int(n) for n in re.findall(r"\d+", _length(level))] or [64]
    return sum(numbers) // len(numbers)


def estimate_seconds(part_count: int, level: str = None) -> dict:
    """How long deep composing usually takes, as a range."""
    level = level if level in composer.LENGTHS else "pro"
    passages = math.ceil(_bars_for(level) / (PASSAGE_BARS * 0.8))
    waves = math.ceil(passages / CONCURRENCY)
    # The melody goes passage by passage; then each passage's players go one
    # after another, and about a third of them need a correction round.
    typical = (PLAN_SECONDS + passages * MELODY_SECONDS
               + waves * max(1, part_count) * PART_SECONDS * 1.35)
    return {"lowSeconds": int(typical * 0.6), "highSeconds": int(typical * 1.6),
            "typicalSeconds": int(typical)}


def compose(request: dict, report=None) -> dict:
    """Generate a score with every note composed by the model.

    ``report(stage, done, total)`` works as in composer.compose.
    """
    roster = composer.build_roster(request["instruments"])
    model = os.environ.get("OPENAI_MODEL", composer.DEFAULT_MODEL)
    level = request["level"]

    if report:
        report("planning", 0, 1)
    plan = _plan(request, roster, model)
    if report:
        report("planning", 1, 1)

    passages = _passages(plan)
    order = _writing_order(plan, roster)
    _log.info("Deep: %d measures, %d passages, writing order %s", plan["measureCount"],
              len(passages), ", ".join(p["label"] for p in order))
    lock, counts = threading.Lock(), {"melody": 0, "writing": 0}
    totals = {"melody": len(passages), "writing": len(passages) * len(order)}

    def ticker(stage):
        def tick():
            with lock:
                counts[stage] += 1
                if report:
                    report(stage, counts[stage], totals[stage])
        return tick

    if report:
        report("melody", 0, totals["melody"])
    melody, settled = _compose_melody(plan, request, roster, passages, model,
                                      ticker("melody"))

    if report:
        report("writing", 0, totals["writing"])
    written = {part["partId"]: {} for part in roster}
    notes, errors, unwritten = {}, [], []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(_compose_passage, plan, request, roster, run, melody, order,
                               model, ticker("writing")) for run in passages]
        for future in as_completed(futures):
            texts, passage_notes, fixed, failures, missing = future.result()
            for pid, sections in texts.items():
                written[pid].update(sections)
            for pid, note in passage_notes.items():
                notes.setdefault(pid, note)
            settled += fixed
            errors += failures
            unwritten += missing

    if not any(written.values()):
        raise errors[0] if errors else ComposerError("Nothing could be written this time. "
                                                     "Please try again.")

    if report:
        report("engraving", 0, 1)
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    parts = _assemble(plan, roster, written, notes)
    form.balance_parts(parts, roster, plan["_sections"])
    form.finish_parts(parts, roster, units)
    score = {
        **{key: plan.get(key) for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                          "timeSignature", "concertKey", "measureCount",
                                          "programNote", "rehearsalTips")},
        "conductor": plan.get("conductor") if request["conductor"] else None,
        "parts": parts,
    }
    warnings = composer._describe(composer.validate_notation(score, roster, level))
    if unwritten:
        warnings.append("Some passages couldn't be written and are left as rests for: "
                        + ", ".join(sorted(set(unwritten)))
                        + ". Generating again usually fills them.")
    if settled:
        warnings.append("A few bars were trimmed or padded so the staves line up: "
                        + ", ".join(settled[:6])
                        + (f" and {len(settled) - 6} more" if len(settled) > 6 else "") + ".")
    if report:
        report("engraving", 1, 1)
    return composer.package(score, roster, plan, warnings, bool(settled), model, mode="deep")
