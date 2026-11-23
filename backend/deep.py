"""Deep composing — the model writes the whole score, every instrument together.

Light mode (composer.compose) is built for speed: the model writes a chord grid
and a few short four-voice chorales, and form.py multiplies them out across the
ensemble and the form.

Deep mode leaves the composing to the model, in the way that makes an
orchestra sound like one instrument: the parts are written together.

1. **The architecture.** One call with the most thinking time: the themes
   (written out in notes), the form section by section, the harmony of every
   bar (one chord or two, held or quickening), each section's key, dynamic,
   who carries the tune, the texture and the accompaniment type, and what
   happens to the material. Python prepares every key change (ii – V7 of the
   new key, then its tonic) and closes the piece V7 – I.
2. **The score, a passage at a time.** The piece is cut into passages of up to
   PASSAGE_BARS bars, written side by side. Each passage is ONE call that
   writes every instrument at once, section by section — so the harmony is
   voiced across the parts by one composer rather than assembled from parts
   written apart. The model writes the compact notation (compact.py): every
   note spelled out at sounding pitch, one short string a measure, with no key
   signatures, transpositions or octave marks to get wrong.
3. **Python does the engraving.** compact.py reads it and silently repairs what
   models get wrong (a bar that doesn't add up, a note out of range, a chord
   too big for the instrument, a broken tie or slur); moves a long note still
   off the chord onto it; balances the tune over the accompaniment; ends every
   staff with 'rit.' and a held final chord; and writes the ABC the site shows
   and plays, transposed into each player's key. The final notes are stored in
   the compact form, which musicxml.py turns into a real MusicXML file.

Every call is retried once if it fails outright. The browser polls for the
result rather than holding a request open (see app.py).
"""

import json
import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction

from openai import OpenAIError

import abc_check
import choices
import compact
import composer
import ensemble
import features
import form
import options
import orchestrate
import rag
from composer import ComposerError, UNIT_NOTE_LENGTH, _obj

_log = logging.getLogger(__name__)

PLAN_EFFORT = os.environ.get("DEEP_PLAN_EFFORT", "high")
WRITE_EFFORT = os.environ.get("DEEP_WRITE_EFFORT", "medium")
CALL_TIMEOUT = float(os.environ.get("DEEP_CALL_TIMEOUT", "900"))
CONCURRENCY = int(os.environ.get("DEEP_CONCURRENCY", "8"))
MAX_TOKENS = composer.MAX_OUTPUT_TOKENS

PASSAGE_BARS = 32     # the most bars one call writes for the whole orchestra
SECTION_BARS = 24     # the longest section the plan may have
ATTEMPTS = 2          # a call that fails outright is asked for again

# Used only for the estimate shown before the button is pressed.
PLAN_SECONDS = 240
SOLO_SECONDS = 150
PASSAGE_SECONDS = 150
PASSAGE_SECONDS_PER_PART = 20

TEXTURES = ("tutti", "reduced", "solo")
ACCOMPANIMENTS = ("sustained", "arpeggio", "repeated", "offbeat", "pedal",
                  "countermelody", "none")

SYSTEM_PROMPT = """\
You are a professional orchestral composer. You write the full score: every \
instrument together, at sounding (concert) pitch, in the compact measure \
notation you are given — never ABC, MusicXML or anything else. Engraving, \
transposition, key signatures and clefs are done for you afterwards."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_PLAN_FIELDS = composer.PLAN_SCHEMA["properties"]

THEME_SCHEMA = _obj({
    "id": {"type": "string", "description": "'A', 'B', 'C'."},
    "measures": {"type": "array", "items": {"type": "string"},
                 "description": "The theme, one measure string each, in the compact "
                                "notation, home key, concert pitch."},
    "chords": {"type": "array", "items": {"type": "string"},
               "description": "The theme's harmony, one chord (or two) per measure."},
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
    "mainTheme": {"type": "boolean",
                  "description": "true where the main theme (A) is heard as itself — its "
                                 "opening clearly recognisable."},
    "texture": {"type": "string", "enum": list(TEXTURES),
                "description": "'solo': the lead alone in the foreground over the barest "
                               "support, most players resting."},
    "accompaniment": {"type": "string", "enum": list(ACCOMPANIMENTS),
                      "description": "What the players under the tune do, together."},
    "material": {"type": "string",
                 "description": "Which theme(s) and what happens to them: 'A in the "
                                "relative minor, fragmented', 'new episode on A's head "
                                "motif, in canon'."},
    "orchestration": {"type": "string",
                      "description": "The layers and who is in each: tune, bass, harmony, "
                                     "any countermelody; who rests."},
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


def passage_schema(roster: list) -> dict:
    """The score of one passage: every section, every part. Built per request, so
    the partIds are the ensemble's own."""
    part = _obj({
        "partId": {"type": "string", "enum": [p["partId"] for p in roster]},
        "measures": {"type": "array", "items": {"type": "string"},
                     "description": "One measure string each, exactly the section's bars."},
        "lower": {"type": "array", "items": {"type": "string"},
                  "description": "A grand staff's left hand, same count; otherwise []."},
    })
    return _obj({"sections": {"type": "array", "items": _obj({
        "section": {"type": "string", "description": "'s1', 's2' …"},
        "parts": {"type": "array", "items": part, "description": "Every part."},
    })}})


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

# Passages all call at once; this keeps the requests in flight to CONCURRENCY.
_SLOTS = threading.BoundedSemaphore(CONCURRENCY)


def _call(prompt: str, name: str, schema: dict, effort: str, model: str,
          system: str = SYSTEM_PROMPT) -> dict:
    with _SLOTS:
        try:
            completion = composer._create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": name, "strict": True,
                                                 "schema": schema}},
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


def _section_index(label) -> int:
    """'s3' -> 2; anything unreadable -> -1."""
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    return int(digits) - 1 if digits else -1


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

SOLOS = ("Give the piece real solos: at least one section of four bars or more "
         "(two or more at higher-intermediate and pro) where one player with a singing "
         "voice — oboe, clarinet, flute, horn, cello, violin, trumpet — plays alone over "
         "the barest support, the rest resting.")


def _length(level: str) -> str:
    return composer.LENGTHS[level][0]


def _total(request: dict) -> str:
    """How long the plan's sections add up to: the user's length, or the level's."""
    target = choices.length_target(request)
    if not target:
        return f"{_length(request['level'])} bars"
    return f"{target[1]} bars" if target[0] == "bars" else "the LENGTH above"


def _plan_prompt(request: dict, roster: list) -> str:
    level = request["level"]
    return "\n".join([
        composer.build_user_prompt(
            request, roster,
            length=f"LENGTH: {_length(level)} measures in total, every one composed."),
        "",
        "YOUR JOB RIGHT NOW: plan the piece. Next, each passage of it is written as a",
        "full score — every instrument together — by a composer working from this",
        "plan. Passages are written at the same time, so the plan is what they share.",
        "",
        "THEMES — two or three, written out in the measure notation below (home key,",
        "concert pitch), four to eight measures each, each with its chord per measure.",
        "Theme A is THE MAIN MELODY of the piece, its identity: a tune anyone could",
        "sing back after one hearing — a distinctive rhythm, a clear contour with one",
        "high point, a phrase that ends. It is written into the score exactly as you",
        "give it wherever it is stated, so make it complete and beautiful. Theme B",
        "contrasts with it in contour, rhythm and mode.",
        "",
        f"SECTIONS — the whole piece in order, 4 to 16 bars each, {_total(request)}",
        "in total, with a real shape: exposition, contrast, development, one climax",
        "about two thirds of the way through, a varied return and a close. Theme A is",
        "stated near the start and returns at the climax and near the end, opening",
        "intact and the rest transformed (mark those sections mainTheme); other music",
        "lies between. No two sections use the same material in the same way.",
        "  * chords: the harmony of every bar, concert pitch: the whole orchestra plays",
        "    it. Write in the harmonic language of the orchestra — triads and seventh",
        "    chords: I, IV, V, ii, vi, V7, secondary dominants (D7/F# in C), diminished",
        "    sevenths, a sus4 that resolves, inversions ('C/E', 'G7/B'). Most bars are",
        "    plain triads. No jazz chords: no 9ths, 11ths, 13ths, 6/9 or added notes",
        "    (they are reduced to their triad or seventh anyway). Hold a chord two bars",
        "    where the music settles, two in a bar ('Dm G7') where it gathers pace.",
        "    (The ii – V7 into each new key and the closing V7 – I are added for you.)",
        "  * key: " + KEY_AREAS[level],
        "  * dynamic: from near pp to near ff across the piece, building to the climax.",
        "  * lead: pass the tune between instruments and families. texture: 'tutti'",
        "    for arrivals, 'reduced' for lighter passages, 'solo' for one player in",
        "    the foreground. " + SOLOS + " Each solo is composed on its own,",
        "    so say in its material what it grows from.",
        "  * accompaniment: what the players under the tune do together — sustained",
        "    (held chords), arpeggio (broken chords), repeated (pulsing chords),",
        "    offbeat (chords off the beat), pedal (a held bass note), countermelody (one",
        "    second line), none. Neighbouring sections use different ones.",
        "  * orchestration: the layers and who plays in each. The orchestra should",
        "    sound like one instrument: a few layers, each of several players moving",
        "    together, everyone else resting.",
        "",
        "partRoles: one entry per player — what they do across the piece.",
        "programNote, rehearsalTips: short. conductor: "
        + ("fill it in with real measure numbers and player names."
           if request["conductor"] else "null."),
        "",
        *rag.guidance(roster, "composition of the orchestra full tutti balance harmony "
                              "doubling melody combined groups", limit=3,
                      topics=rag.HARMONY + rag.TEXTURE),
        "",
        compact.NOTATION,
    ])


def _plan(request: dict, roster: list, model: str) -> dict:
    plan = _call(_plan_prompt(request, roster), "deep_plan",
                 choices.constrain(PLAN_SCHEMA, request), PLAN_EFFORT, model)
    composer._tidy_labels(plan)
    home = plan["concertKey"]
    part_ids = {part["partId"] for part in roster}

    sections, measure = [], 1
    for entry in plan.get("sections") or []:
        # Triads and sevenths only: see form.orchestral_chord.
        chords = [form.orchestral_chord(" ".join(form.bar_chords(c)))
                  for c in entry.get("chords") or [] if str(c).strip()]
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
            "accompaniment": entry.get("accompaniment")
            if entry.get("accompaniment") in ACCOMPANIMENTS else "sustained",
            "main": bool(entry.get("mainTheme")),
            "material": str(entry.get("material") or ""),
            "orchestration": str(entry.get("orchestration") or ""),
        })
        measure += bars
    if not sections:
        raise ComposerError("The planner didn't lay out any sections this time. "
                            "Please try again.", status=502)
    for s in sections:
        if not s["lead"] and s["texture"] == "solo":
            s["texture"] = "reduced"      # a solo needs a soloist
    if not any(s["main"] for s in sections):
        next((s for s in sections if s["bars"] >= 4), sections[0])["main"] = True

    # The main melody where it must be heard, and the solos.
    plan["_sections"], plan["measureCount"] = sections, measure - 1
    features.place_main_theme(
        plan, roster, abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH))
    features.place_solos(plan, roster, request["level"])

    # Every piece closes the same way: home, V7, then the tonic held.
    last = sections[-1]
    last["key"] = home
    # Every change of key is prepared: ii – V7 of the new key, then its tonic.
    for before, after in zip(sections, sections[1:]):
        before["modulates"] = not form.same_key(before["key"], after["key"])
        if before["modulates"]:
            after["chords"] = form.arrive_in(after["chords"], after["key"])
            before["chords"] = form.lead_into(before["chords"], after["key"])
    last["modulates"] = False
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


def _passages(plan: dict, limit: int = PASSAGE_BARS) -> list:
    """Runs of whole sections of at most ``limit`` bars: [[section index]]."""
    out, run, bars = [], [], 0
    for index, s in enumerate(plan["_sections"]):
        if run and bars + s["bars"] > limit:
            out.append(run)
            run, bars = [], 0
        run.append(index)
        bars += s["bars"]
    if run:
        out.append(run)
    return out


# ---------------------------------------------------------------------------
# Solos: each composed on its own, checked, and sent back once
# ---------------------------------------------------------------------------

SOLO_RULES = [
    "WRITING THE SOLO",
    "  * Sing. Move mostly by step. A leap is an event: a third up to a sixth, an",
    "    octave only to reach the high point. After a leap of a fourth or more, turn",
    "    back by step. Never leap a tritone or a seventh, and never make two leaps",
    "    the same way unless together they spell one chord.",
    "  * Phrase. Two or more phrases of two or four bars, like a question and its",
    "    answer, each under one slur, each rising and settling onto a longer note",
    "    of the chord. Breathe between phrases with a short rest — a wind player",
    "    needs one at least every four bars.",
    "  * One high point: the highest note once, about two thirds of the way",
    "    through, on a strong beat; then the line comes down.",
    "  * Rhythm with a profile: take one rhythmic idea from the themes and let it",
    "    come back changed; long notes among shorter ones, never a stream of equal",
    "    values. Mostly quarters and eighths — expression, not speed.",
    "  * Imply the harmony: chord tones on the strong beats, passing and neighbour",
    "    notes between.",
    "  * Dynamics follow the shape: begin softly, swell to the high point, fall back.",
    "  * Begin where the music before leaves off; end so the music after can follow.",
    "",
    "THE SUPPORT",
    "  * One to three other instruments, soft (pp or p), below the soloist and out",
    "    of its way: a held pedal, or sustained chords that change only with the",
    "    harmony. Nothing that competes with the solo. Leave every other part out.",
]


def solo_schema(roster: list) -> dict:
    part = _obj({
        "partId": {"type": "string", "enum": [p["partId"] for p in roster]},
        "measures": {"type": "array", "items": {"type": "string"}},
        "lower": {"type": "array", "items": {"type": "string"},
                  "description": "A grand staff's left hand, same count; otherwise []."},
    })
    return _obj({"shape": {"type": "string",
                           "description": "One sentence: the phrases and where the high "
                                          "point falls."},
                 "parts": {"type": "array", "items": part,
                           "description": "The soloist first, then one to three supports."}})


def _solo_prompt(plan: dict, request: dict, roster: list, index: int) -> str:
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    sections = plan["_sections"]
    s = sections[index]
    by_id = {p["partId"]: p for p in roster}
    soloist = by_id[s["lead"]]
    low, high = compact.singing_range(soloist)
    mood = options.MOODS_BY_VALUE[request["mood"]]
    lines = [
        f"WRITE THE SOLO for {soloist['label']} [{soloist['partId']}] in \"{plan['title']}\": "
        f"mm.{s['start']}-{s['end']}, EXACTLY {s['bars']} measures, {s['key']}, around "
        f"{s['dynamic']}, {plan['timeSignature']}, {plan['tempoText']} ({plan['tempoBpm']} bpm).",
        "Every note is exposed: it has to be a real melody, one a listener could hum "
        "afterwards.",
        f"Its singing register: {_note(low)} to {_note(high)} (sounding).",
        f"Mood: {mood['label']} — {mood['guidance']}",
        f"What it grows from: {s['material']}",
        "Harmony, bar by bar: " + " | ".join(f"{s['start'] + i} {c}"
                                             for i, c in enumerate(s["chords"])),
    ]
    if s.get("theme"):
        lines.append("It states THE MAIN THEME: begin with theme A exactly, in this key, "
                     "then let it bloom.")
    if index:
        before = sections[index - 1]
        lines.append(f"Before it: s{index} [{before['label']}], {before['key']}, "
                     f"{before['dynamic']} — {before['material']}")
    if index + 1 < len(sections):
        after = sections[index + 1]
        lines.append(f"After it: s{index + 2} [{after['label']}], {after['key']}, "
                     f"{after['dynamic']} — {after['material']}")
    lines += ["", "THE THEMES (home key, concert pitch):"]
    for theme in plan.get("themes") or []:
        lines.append(f"  [{theme.get('id')}] " + " | ".join(theme.get("measures") or [])
                     + f"   — {theme.get('description', '')}")
    lines += ["", *SOLO_RULES, "",
              *rag.guidance(roster, "soli solo melody soft accompaniment sustained", limit=2,
                            topics=rag.MELODY, featured=[soloist["instrument"]]),
              "", *_ensemble_lines(plan, roster), "", compact.NOTATION,
              "HARD RULES",
              f"  1. Every measure adds up to exactly one bar: "
              f"{_bar_length(units, plan['timeSignature'])}. Count every measure.",
              "  2. Every part you write has exactly the section's number of measures, in",
              "     range, never more notes at once than listed.",
              f"  3. The soloist [{soloist['partId']}] first, then the supports; leave every "
              "other part out. lower is [] except for a grand staff."]
    return "\n".join(lines)


def _solo_music(reply: dict, section: dict, soloist: str) -> dict:
    """{partId: {measures, lower}} from a solo call, the soloist included, or {}."""
    out = {}
    for written in (reply or {}).get("parts") or []:
        pid = written.get("partId")
        if pid and pid not in out:
            out[pid] = {"measures": list(written.get("measures") or [])[:section["bars"]],
                        "lower": list(written.get("lower") or [])[:section["bars"]]}
    return out if soloist in out else {}


def _compose_solo(plan: dict, request: dict, roster: list, index: int, model: str) -> dict:
    """One solo: written, checked by melody.critique, rewritten once if it has faults."""
    s = plan["_sections"][index]
    by_id = {p["partId"]: p for p in roster}
    soloist = by_id[s["lead"]]
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    beat = form.beat_units(plan["timeSignature"])
    prompt = _solo_prompt(plan, request, roster, index)
    schema = solo_schema(roster)
    reply, _ = _asked("solo", prompt, schema, PLAN_EFFORT, model)
    music = _solo_music(reply, s, soloist["partId"])
    if not music:
        return {}
    first = features.solo_faults(soloist, music[soloist["partId"]], s, units, beat)
    if first:
        shown = "\n".join(f"  [{pid}] " + " | ".join(w["measures"]) for pid, w in music.items())
        again, _ = _asked("solo", "\n".join([
            prompt, "", "YOUR FIRST VERSION:", shown, "", "WHAT IS WRONG WITH IT:",
            *[f"  - {fault}" for fault in first], "",
            "Write the solo again, fixing all of these and keeping what was good about it."]),
            schema, PLAN_EFFORT, model)
        revised = _solo_music(again, s, soloist["partId"])
        if revised and len(features.solo_faults(soloist, revised[soloist["partId"]], s,
                                                units, beat)) <= len(first):
            music = revised
    _log.info("Deep: solo for %s in mm.%d-%d (%d faults first time)", soloist["label"],
              s["start"], s["end"], len(first))
    return music


# ---------------------------------------------------------------------------
# Writing a passage: the whole orchestra in one call
# ---------------------------------------------------------------------------

def _bar_length(units: Fraction, time_signature: str) -> str:
    """'4/4 = one whole note (1)' — what a full measure adds up to, in the notation."""
    known = {Fraction(8): "a whole note (1)", Fraction(6): "a dotted half (2.)",
             Fraction(4): "a half (2)", Fraction(3): "a dotted quarter (4.)",
             Fraction(2): "a quarter (4)"}
    eighths = f"{units} eighths" if units.denominator == 1 else f"{float(units)} eighths"
    return f"{time_signature} = {known.get(units, eighths)}, e.g. as {eighths}"


def _note(midi) -> str:
    return ensemble.note_name(midi) if midi is not None else "?"


def _ensemble_lines(plan: dict, roster: list) -> list:
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}
    lines = ["THE ENSEMBLE (ranges at sounding pitch):"]
    for part in roster:
        low, high = compact.sounding_range(part) if part["pitched"] else (None, None)
        if not part["pitched"]:
            what = "unpitched: L low sound, H high sound"
        else:
            most = compact.max_notes(part)
            what = (f"{_note(low)}-{_note(high)}, "
                    + ("one note at a time" if most == 1 else f"up to {most} notes at once"))
        if part["staves"] == 2:
            what += "; GRAND STAFF: right hand in measures, left hand in lower"
        if form.accompanies(part, roster):
            what += ("; a soft colour in this orchestra — light voicings, two or three "
                     "notes a hand, no hammered chords")
        role = f" — {roles[part['partId']]}" if roles.get(part["partId"]) else ""
        lines.append(f"  [{part['partId']}] {part['label']}: {what}{role}")
    return lines


def _form_lines(plan: dict, roster: list, here: list) -> list:
    by_id = {part["partId"]: part for part in roster}
    lines = ["THE WHOLE PIECE (>> = the sections you write now):"]
    for index, s in enumerate(plan["_sections"]):
        tags = [s["key"], s["dynamic"], s["texture"], s["accompaniment"]]
        if s["lead"]:
            tags.append(("SOLO " if s["texture"] == "solo" else "tune: ")
                        + by_id[s["lead"]]["label"])
        if s["main"]:
            tags.append("MAIN THEME")
        mark = ">>" if index in here else "  "
        lines.append(f"{mark} s{index + 1} mm.{s['start']}-{s['end']} [{s['label']}] "
                     f"{', '.join(tags)} — {s['material']}")
    return lines


def _section_lines(plan: dict, roster: list, index: int) -> list:
    by_id = {part["partId"]: part for part in roster}
    sections = plan["_sections"]
    s = sections[index]
    lead = by_id[s["lead"]]["label"] if s["lead"] else "nobody in particular"
    lines = [
        f"SECTION s{index + 1} [{s['label']}] — mm.{s['start']}-{s['end']}, EXACTLY {s['bars']} "
        f"measures, {s['key']}, {s['dynamic']}.",
        f"  tune: {lead}; texture {s['texture']}; accompaniment {s['accompaniment']}.",
        f"  material: {s['material']}",
        f"  orchestration: {s['orchestration']}",
        "  harmony: " + " | ".join(f"{s['start'] + i} {c}" for i, c in enumerate(s["chords"])),
    ]
    if s.get("solo_music"):
        lines += [f"  ALREADY WRITTEN — this section is a SOLO for {lead}, composed separately. "
                  "Write every measure of every part here as R; it is filled in afterwards.",
                  "  Lead into it and out of it: the bars before hand over to its first note "
                  "and harmony, the bars after pick up from where it ends. It goes:"]
        for pid, written in s["solo_music"].items():
            if any(m.strip() != "R" for m in written["measures"]):
                lines.append(f"    [{pid}] " + " | ".join(written["measures"]))
    elif s["texture"] == "solo":
        lines.append(f"  SOLO: {lead} sings alone; one to three others hold soft (pp–p) "
                     "notes below it, changing only with the harmony; everyone else R.")
    if s.get("theme"):
        part = by_id[s["lead"]]
        theme = features.theme_for(s, part)
        n = s["theme"]["length"]
        lines += [f"  THE MAIN THEME — ALREADY WRITTEN: mm.{s['start']}-{s['start'] + n - 1} of "
                  f"[{s['lead']}] {part['label']} are theme A, exactly this (concert pitch):",
                  "    " + " | ".join(compact.to_compact(b) for b in theme),
                  f"  Write those notes in that part. Everyone else accompanies it on the "
                  f"harmony shown; from m.{s['start'] + n} the tune continues and develops."]
    elif s["main"]:
        lines.append("  MAIN THEME: theme A's opening here, exact in rhythm and intervals "
                     "(in this key); the rest transformed.")
    if index and not form.same_key(sections[index - 1]["key"], s["key"]):
        lines.append(f"  Arrives in {s['key']} from {sections[index - 1]['key']}.")
    if s.get("modulates"):
        lines.append(f"  Its last bar leads into {sections[index + 1]['key']}: move every "
                     "line into the new key by step.")
    return lines


# What to look up in the textbooks for a passage, by its accompaniment and texture.
LOOK_UP = {
    "sustained": "sustained harmony held chords", "arpeggio": "arpeggio broken chords",
    "repeated": "repeated chords accompaniment", "offbeat": "accompaniment chords rhythm",
    "pedal": "pedal sustained bass", "countermelody": "melody doubling counterpoint",
    "solo": "solo melody soft accompaniment", "tutti": "full tutti",
    "reduced": "harmony",
}


def _reading(plan: dict, roster: list, run: list) -> list:
    """The textbook passages for these bars: their textures, and who has the tune."""
    by_id = {part["partId"]: part for part in roster}
    sections = [plan["_sections"][i] for i in run]
    focus = " ".join(["harmony chord distribution doubling combined groups"]
                     + [LOOK_UP.get(s["accompaniment"], "") for s in sections]
                     + [LOOK_UP.get(s["texture"], "") for s in sections])
    featured = [by_id[s["lead"]]["instrument"] for s in sections if s["lead"]][:2]
    return rag.guidance(roster, focus, limit=3, featured=featured)


def _passage_prompt(plan: dict, request: dict, roster: list, run: list) -> str:
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    sections = plan["_sections"]
    first, last = sections[run[0]], sections[run[-1]]
    level = options.LEVELS_BY_VALUE[request["level"]]
    mood = options.MOODS_BY_VALUE[request["mood"]]
    final = run[-1] == len(sections) - 1

    lines = [
        f"WRITE THE FULL SCORE of mm.{first['start']}-{last['end']} of \"{plan['title']}\" — "
        "every instrument, together, section by section.",
        f"{plan['concertKey']} at home, {plan['timeSignature']}, {plan['tempoText']} "
        f"({plan['tempoBpm']} bpm). Mood: {mood['label']} — {mood['guidance']}",
        f"Players: {level['label']} level — {level['guidance']}",
    ]
    if request.get("notes"):
        lines.append(f"Also asked for: {request['notes']}")
    lines.append("")
    for index in run:
        lines += _section_lines(plan, roster, index) + [""]
    if run[0] > 0:
        before = sections[run[0] - 1]
        lines += [f"BEFORE THESE BARS: s{run[0]} [{before['label']}] in {before['key']}, "
                  f"{before['dynamic']}, {before['texture']} — {before['material']}. "
                  "Pick up from it.", ""]
    if not final:
        after = sections[run[-1] + 1]
        lines += [f"AFTER THESE BARS: s{run[-1] + 2} [{after['label']}] in {after['key']}, "
                  f"{after['dynamic']}, {after['texture']} — {after['material']}. Lead into it; "
                  "don't end on a cadence that sounds final.", ""]

    lines += ["THE THEMES (home key, concert pitch):"]
    for theme in plan.get("themes") or []:
        lines.append(f"  [{theme.get('id')}] " + " | ".join(theme.get("measures") or [])
                     + f"   — {theme.get('description', '')}")
    lines += ["", *_form_lines(plan, roster, run), "", *_ensemble_lines(plan, roster), ""]

    lines += [
        "HOW THE ORCHESTRA SOUNDS",
        "  * This is an orchestra: every instrument together makes ONE sound, like a",
        "    single great instrument. Nobody plays a line of their own that ignores the",
        "    others.",
        "  * The whole orchestra shares one harmony — the chords above, bar by bar.",
        "    On every beat, what sounds together IS that chord: its root, third and",
        "    fifth (and seventh where named), doubled in octaves — no other notes on",
        "    the beat. Voice it like a chorale spread across the orchestra: the bass on",
        "    the chord's bass note, wide spacing low, closer spacing high. A note",
        "    outside the chord only between beats, as a passing or neighbour note in",
        "    the tune.",
        "  * Motion: mostly quarters, eighths and longer notes. The accompaniment",
        "    never moves faster than eighths; the tune uses sixteenths only for a short",
        "    ornament or a run into a climax; no thirty-seconds. (Faster notes are",
        "    merged when the score is engraved.)",
        "  * Legato is the orchestra's normal sound: slurred phrases and held notes",
        "    that overlap into one another. Staccato only where the character asks for",
        "    it, and then played by a whole layer together.",
        "  * One sound, not many: a few layers — the tune, the bass, the harmony, at",
        "    most one countermelody. Players in one layer play together, in one rhythm,",
        "    doubling in octaves or a third or sixth apart, so the layer sounds like a",
        "    single instrument. Doubling the tune in octaves or thirds is welcome.",
        "  * The accompaniment is the type named, played together by its layer:",
        "    sustained = held chords changing with the harmony; arpeggio = broken",
        "    chords; repeated = pulsing chords; offbeat = chords off the beat; pedal =",
        "    one held bass note; countermelody = one second line against the tune.",
        "  * Orchestrate idiomatically: strings carry the foundation, woodwinds colour",
        "    and answer, brass reinforce arrivals and climaxes, timpani mark tonic and",
        "    dominant. Not everyone plays all the time — rests are part of the sound.",
        "  * The tune: phrases of different lengths, a held note where it breathes, a",
        f"    leap where it reaches. Its gestures fit the mood: "
        f"{composer.MOOD_GESTURES.get(request['mood'], '')}. No chromatic runs.",
        "  * Never write the same measure more than twice running in a part; a theme",
        "    that returns is transformed, never pasted.",
    ]
    if final:
        lines += [
            "  * THE ENDING — these bars end the piece. The last section starts nothing new.",
            "    Slow the motion approaching the end: longer notes, the harmony changing",
            "    less often. The melody arrives on the downbeat of the final measure; the",
            "    final measure is the tonic chord alone, held the whole measure by every",
            "    instrument playing — the root in the bass. (The ritardando and the pause",
            "    are performed for you; write the notes plainly.)",
        ]
    lines += ["", *_reading(plan, roster, run)]
    lines += [
        "",
        compact.NOTATION,
        "HARD RULES",
        f"  1. Every measure adds up to exactly one bar: {_bar_length(units, plan['timeSignature'])}.",
        "     Count every measure.",
        "  2. Each part stays in its range and never plays more notes at once than listed.",
        "  3. Every part appears in every section with exactly that section's number of",
        "     measures; a part with nothing to play in a measure writes R.",
        "  4. A dynamic at each part's first entrance and wherever its level changes.",
        "  5. lower is the left hand of a grand staff (same count); for other parts, [].",
        "  6. One entry per section asked for, with its id (" +
        ", ".join(f"s{i + 1}" for i in run) + ").",
    ]
    return "\n".join(lines)


def _write_passage(plan: dict, request: dict, roster: list, run: list, model: str) -> tuple:
    """({section index: {partId: {"measures", "lower"}}}, error)."""
    reply, error = _asked("passage", _passage_prompt(plan, request, roster, run),
                          passage_schema(roster), WRITE_EFFORT, model)
    out = {}
    for entry in (reply or {}).get("sections") or []:
        index = _section_index(entry.get("section"))
        if index not in run:
            continue
        parts = out.setdefault(index, {})
        for written in entry.get("parts") or []:
            pid = written.get("partId")
            if pid and pid not in parts:
                parts[pid] = {"measures": list(written.get("measures") or []),
                              "lower": list(written.get("lower") or [])}
    return out, error


# ---------------------------------------------------------------------------
# Engraving
# ---------------------------------------------------------------------------

def _engrave(plan: dict, request: dict, roster: list, texts: dict) -> tuple:
    """The written passages -> (parts for the score, stored compact, repairs, unwritten).

    ``texts`` is {section index: {partId: {"measures", "lower"}}}.
    """
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    beat = form.beat_units(plan["timeSignature"])
    sections = plan["_sections"]
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}
    parts, stored, repairs, unwritten, thinned = [], [], 0, set(), 0

    for part in roster:
        pid = part["partId"]
        staves = {"upper": [], "lower": []}
        for index, s in enumerate(sections):
            written = (texts.get(index) or {}).get(pid)
            if s.get("solo_music"):
                # A solo was composed on its own: its players, and silence.
                written = s["solo_music"].get(pid) or {"measures": [], "lower": []}
            elif written is None:
                unwritten.add(part["label"])
                written = {"measures": [], "lower": []}
            upper, fixed = compact.read_part(written["measures"], s["bars"], part, units)
            repairs += fixed if written["measures"] else 0
            if s.get("theme") and s["lead"] == pid:
                # The main melody, exactly: written in, not left to the passage.
                theme = features.theme_for(s, part)
                dynamic = next((e.dynamic for e in upper[0] if e.dynamic), None) or s["dynamic"]
                first = next((e for e in theme[0] if e.sounding), None)
                if first and not first.dynamic:
                    first.dynamic = dynamic
                upper[:len(theme)] = theme
            staves["upper"] += upper
            if part["staves"] == 2:
                lower, fixed = compact.read_part(written["lower"], s["bars"], part, units)
                repairs += fixed if written["lower"] else 0
                staves["lower"] += lower

        for name in ("upper", "lower"):
            bars = staves[name]
            if not bars:
                continue
            for s in sections:
                # Motion an orchestra can blend: the accompaniment no faster than
                # eighths, the tune no faster than sixteenths.
                grid = Fraction(1, 2) if s["lead"] == pid else Fraction(1)
                for bar in bars[s["start"] - 1:s["end"]]:
                    thinned += compact.thin(bar, grid)
            if part["pitched"]:
                for s in sections:
                    # The tune may lean on an appoggiatura for a beat; everyone
                    # else is the harmony, so on every beat they are the chord.
                    length = 2 * beat if s["lead"] == pid else Fraction(1, 4)
                    compact.land_on_chords(bars[s["start"] - 1:s["end"]], s["chords"],
                                           s["key"], beat, units, min_length=length)
                # A note moved onto the chord may no longer match the one tied to it.
                compact.link([e for bar in bars for e in bar])
            compact.balance(bars, pid, sections, soften=form.accompanies(part, roster))
            compact.finish(bars, units, rit=name == "upper")

        abc, home = compact.render_abc(staves["upper"], part, sections)
        lower_abc = compact.render_abc(staves["lower"], part, sections)[0] \
            if part["staves"] == 2 else None
        parts.append({"partId": pid, "abcKey": home, "abc": abc, "abcLower": lower_abc,
                      "performanceNote": roles.get(pid) or ""})
        stored.append({"partId": pid,
                       "upper": [compact.to_compact(bar) for bar in staves["upper"]],
                       "lower": [compact.to_compact(bar) for bar in staves["lower"]]})
    _log.info("Deep: %d too-fast notes merged", thinned)
    return parts, stored, repairs, sorted(unwritten)


def _listen(plan: dict, roster: list, parts: list) -> None:
    """Log what the ensemble checks hear in the finished score, passage by passage."""
    try:
        units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
        by_section = {}
        for written in parts:
            part = next(p for p in roster if p["partId"] == written["partId"])
            bars = orchestrate.split_bars(written["abc"])
            for index, s in enumerate(plan["_sections"]):
                chunk = bars[s["start"] - 1:s["end"]]
                chunk = [form.INLINE_KEY_RE.sub("", b) for b in chunk]
                by_section.setdefault(written["partId"], {})[index] = {
                    "abc": " | ".join(chunk) + " |"}
        problems = ensemble.listen(plan, roster, by_section, list(range(len(plan["_sections"]))))
        _log.info("Deep: the finished score has %d ensemble problems%s", len(problems),
                  (": " + "; ".join(problems[:5])) if problems else "")
    except Exception as error:                  # a report, never a failure
        _log.info("Deep: couldn't listen to the score: %s", error)


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
    # The plan, then every passage at once: each is one call for the whole
    # orchestra, longer the more players it writes for.
    per_passage = PASSAGE_SECONDS + PASSAGE_SECONDS_PER_PART * max(1, part_count)
    solo = SOLO_SECONDS * 1.4 if part_count >= 2 else 0     # one wave, sometimes a rewrite
    typical = PLAN_SECONDS + solo + waves * per_passage * 1.2
    return {"lowSeconds": int(typical * 0.6), "highSeconds": int(typical * 1.7),
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

    solos = [i for i, s in enumerate(plan["_sections"]) if s["texture"] == "solo" and s["lead"]]
    if solos:
        if report:
            report("solos", 0, len(solos))
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(solos))) as pool:
            futures = {pool.submit(_compose_solo, plan, request, roster, i, model): i
                       for i in solos}
            for done_count, future in enumerate(as_completed(futures), 1):
                music = future.result()
                if music:
                    plan["_sections"][futures[future]]["solo_music"] = music
                if report:
                    report("solos", done_count, len(solos))

    passages = _passages(plan)
    _log.info("Deep: %d measures in %d passages, each one call for all %d parts; %d solos, "
              "main theme in %d sections", plan["measureCount"], len(passages), len(roster),
              len(solos), sum(1 for s in plan["_sections"] if s.get("theme")))
    lock, done = threading.Lock(), [0]
    if report:
        report("writing", 0, len(passages))
    texts, errors = {}, []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(_write_passage, plan, request, roster, run, model)
                   for run in passages]
        for future in as_completed(futures):
            written, error = future.result()
            texts.update(written)
            if error:
                errors.append(error)
            with lock:
                done[0] += 1
                if report:
                    report("writing", done[0], len(passages))
    if not texts:
        raise errors[0] if errors else ComposerError("Nothing could be written this time. "
                                                     "Please try again.")

    if report:
        report("engraving", 0, 1)
    parts, stored, repairs, unwritten = _engrave(plan, request, roster, texts)
    _listen(plan, roster, parts)
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
                        + ", ".join(unwritten) + ". Generating again usually fills them.")
    _log.info("Deep: %d small repairs made while engraving", repairs)
    if report:
        report("engraving", 1, 1)
    result = composer.package(score, roster, plan, warnings, False, model, mode="deep")
    result["compact"] = {
        "units": str(abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)),
        "timeSignature": plan["timeSignature"], "title": plan["title"],
        "tempoText": plan["tempoText"], "tempoBpm": plan["tempoBpm"],
        "sections": [{"start": s["start"], "end": s["end"], "bars": s["bars"],
                      "key": s["key"], "label": s["label"]} for s in plan["_sections"]],
        "parts": stored,
    }
    result["repairs"] = repairs
    return result
