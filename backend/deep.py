"""Deep composing — the model writes every note of every part.

Light mode (composer.compose) is built for speed: the model writes a chord grid
and a few short four-voice chorales, and form.py multiplies them out across
the ensemble and the form. That is arranging by rule, and it sounds like it.

Deep mode leaves the composing to the model, however long it takes:

1. **The architecture.** One call with the most thinking time: the themes as
   real melodies, the form section by section, a chord for every bar of the
   piece, each section's key, dynamic, material and orchestration, and what
   each player does across the piece.
2. **The parts, passage by passage.** Every passage of up to UNIT_BARS bars is
   composed for the whole ensemble, a few staves per call. Later calls for the
   same bars are shown what the earlier ones wrote, so each new line is written
   against real notes, not a guess. Passages run concurrently; inside a
   passage the groups go in order.
3. **Review.** Python counts every bar, checks every note against the player's
   range and every held note against the chord, and hands what it finds back to
   the model to correct. Python changes notes itself only as a last resort, so
   that the staves still line up.

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

UNIT_BARS = 16        # the longest passage composed in one call
GROUP_STAVES = 6      # staves composed together in one call
ATTEMPTS = 2          # a passage that fails outright is asked for again
REPAIR_ROUNDS = 2     # correction rounds before Python steps in

# Used only for the estimate shown before the button is pressed.
PLAN_SECONDS = 240
CALL_SECONDS = 210


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
               "description": "Concert-pitch chord for EACH bar, in order. Its length is "
                              "the section's bar count."},
    "dynamic": {"type": "string", "enum": list(form.DYNAMICS)},
    "lead": {"type": ["string", "null"],
             "description": "partId of the player carrying the melody, or null."},
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

PASSAGE_SCHEMA = _obj({"parts": {"type": "array", "items": _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "abc": {"type": "string", "description": "This part's music for the passage."},
    "abcLower": {"type": ["string", "null"],
                 "description": "Left hand of a grand staff, otherwise null."},
    "performanceNote": {"type": "string", "description": "One sentence for this player."},
})}})


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


def _plan_prompt(request: dict, roster: list) -> str:
    level = request["level"]
    return "\n".join([
        composer.build_user_prompt(request, roster, length=_length_line(level)),
        "",
        "YOUR JOB RIGHT NOW: compose the architecture of the piece. You will then",
        "compose every part yourself, passage by passage, following this plan.",
        "",
        "THEMES — two or three, written out as real melodies in ABC (concert pitch,",
        "home key, treble register, L:1/8), four to eight bars each. Theme A is the",
        "main idea; theme B contrasts with it in contour, rhythm and harmony. These are",
        "the material the whole piece is built from, so make them memorable.",
        "",
        f"SECTIONS — the whole piece in order, 4 to 16 bars each, {_length(level)} bars",
        "in total. It needs a real shape: exposition, contrast, development, a climax,",
        "a varied return and a close. A theme that comes back is never a copy: change",
        "its orchestration, register, harmonisation or counter-melody.",
        "  * chords: one per bar, concert pitch, named exactly ('D7/F#', 'Bbmaj7',",
        "    'F#ø7', 'Esus4'). The parts will be composed on them, so they are binding.",
        "    Use inversions, secondary dominants, suspensions that resolve; avoid a",
        "    grid that sits on I, IV and V.",
        "  * key: " + KEY_AREAS[level],
        "  * dynamic: go near pp and near ff somewhere, with at least one sudden change.",
        "  * lead: pass the tune between instruments and families.",
        "  * material and orchestration: say exactly what happens and who plays what,",
        "    so every passage can be composed from this alone.",
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
        chords = [str(c).strip() for c in entry.get("chords") or [] if str(c).strip()]
        try:
            bars = int(entry.get("bars") or len(chords))
        except (TypeError, ValueError):
            bars = len(chords)
        bars = max(1, min(32, bars or 4))
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
            "material": str(entry.get("material") or ""),
            "orchestration": str(entry.get("orchestration") or ""),
        })
        measure += bars
    if not sections:
        raise ComposerError("The planner didn't lay out any sections this time. "
                            "Please try again.", status=502)

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


# ---------------------------------------------------------------------------
# Passages and groups
# ---------------------------------------------------------------------------

def _passages(plan: dict) -> list:
    """Sections cut into runs of at most UNIT_BARS bars, evenly."""
    units = []
    for index, s in enumerate(plan["_sections"]):
        pieces = math.ceil(s["bars"] / UNIT_BARS)
        size = math.ceil(s["bars"] / pieces)
        for offset in range(0, s["bars"], size):
            bars = min(size, s["bars"] - offset)
            units.append({**s, "section": index, "first": offset == 0,
                          "start": s["start"] + offset, "end": s["start"] + offset + bars - 1,
                          "bars": bars, "chords": s["chords"][offset:offset + bars]})
    return units


def _groups(unit: dict, roster: list) -> list:
    """The players of one passage, in the order they are composed.

    The melody comes first, so everyone after writes against it; then score
    order, a few staves at a time.
    """
    ordered = sorted(roster, key=lambda part: part["partId"] != unit["lead"])
    groups, current, staves = [], [], 0
    for part in ordered:
        if current and staves + part["staves"] > GROUP_STAVES:
            groups.append(current)
            current, staves = [], 0
        current.append(part)
        staves += part["staves"]
    if current:
        groups.append(current)
    return groups


def _player_line(part: dict, unit: dict, request: dict, roles: dict) -> str:
    level = request["level"]
    transposition = composer.TRANSPOSITION_TEXT.get(
        part["transpose"], f"sounds {part['transpose']} semitones from written")
    family = options.FAMILY_OF.get(part["instrument"], "")
    technique = (options.TECHNIQUE.get(family) or {}).get(level, "")
    detail = [f"clef {part['clef']}", transposition, composer._range_line(part, level),
              f"WRITTEN KEY HERE: {form.written_key_for(part, unit['key'])}"]
    if part["staves"] == 2:
        detail.append("GRAND STAFF: right hand in \"abc\", left hand in \"abcLower\"")
    line = f"  [{part['partId']}] {part['label']} — " + "; ".join(detail) + "."
    if roles.get(part["partId"]):
        line += f"\n      Role in the piece: {roles[part['partId']]}"
    if technique and part["pitched"]:
        line += f"\n      Technique to use: {technique}"
    return line


def _passage_prompt(plan: dict, request: dict, roster: list, units: list, index: int,
                    group: list, written: dict, later: list) -> str:
    unit = units[index]
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    by_id = {part["partId"]: part for part in roster}
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}

    lines = [
        composer.build_user_prompt(request, roster, length=_length_line(request["level"])),
        "",
        f"THE PIECE: \"{plan['title']}\" — {plan['concertKey']}, {plan['timeSignature']}, "
        f"{plan['tempoText']} ({plan['tempoBpm']} bpm), {plan['measureCount']} measures.",
        "",
        "THE THEMES (concert pitch):",
    ]
    for theme in plan.get("themes") or []:
        lines += [f"  [{theme.get('id')}] {theme.get('description', '')}",
                  f"    {theme.get('abc', '')}"]
    lines += ["", "THE FORM (the whole piece; >> marks where you are):"]
    for s in plan["_sections"]:
        here = ">>" if s["start"] <= unit["start"] <= s["end"] else "  "
        lead = f", tune: {by_id[s['lead']]['label']}" if s["lead"] else ""
        lines.append(f"{here} mm.{s['start']}-{s['end']} [{s['label']}] {s['key']}, "
                     f"{s['dynamic']}{lead}. {s['material']} / {s['orchestration']}")

    before = units[index - 1] if index else None
    after = units[index + 1] if index + 1 < len(units) else None
    lines += [
        "",
        f"NOW COMPOSE mm.{unit['start']}-{unit['end']}: EXACTLY {unit['bars']} bars of "
        f"section [{unit['label']}]" + ("" if unit["first"] else " (continuing it)") + ".",
        f"  key (concert): {unit['key']}; dynamic: {unit['dynamic']}",
        "  chords: " + " | ".join(f"m.{unit['start'] + i} {c}"
                                  for i, c in enumerate(unit["chords"])),
        f"  material: {unit['material']}",
        f"  orchestration: {unit['orchestration']}",
        ("  comes after: " + " | ".join(before["chords"][-2:]) + f" (end of [{before['label']}])")
        if before else "  this is the opening of the piece.",
        ("  leads into: " + " | ".join(after["chords"][:2]) + f" (start of [{after['label']}])")
        if after else "  this is the end of the piece: close it.",
    ]

    if written:
        lines += ["", "ALREADY COMPOSED FOR THESE BARS — final; write your parts against them:"]
        for pid, text in written.items():
            part = by_id[pid]
            lines.append(f"  [{pid}] {part['label']} (written key "
                         f"{form.written_key_for(part, unit['key'])}; "
                         f"{composer.TRANSPOSITION_TEXT.get(part['transpose'], '')}):")
            lines.append(f"    {text['abc']}")
            if text.get("abcLower"):
                lines.append(f"    left hand: {text['abcLower']}")

    lines += ["", "COMPOSE THESE PARTS NOW:",
              *[_player_line(part, unit, request, roles) for part in group]]
    if later:
        lines += ["", "STILL TO BE COMPOSED AFTER YOU (leave them room): "
                  + ", ".join(part["label"] for part in later)]

    quota = composer.DIFFICULTY_QUOTA.get(request["level"])
    if quota:
        lines += ["", "MINIMUM DIFFICULTY for parts with real material here:",
                  *[f"  * {q}" for q in quota]]

    lines += [
        "",
        "RULES",
        "  * One entry per part listed under COMPOSE THESE PARTS NOW, with its partId.",
        f"  * EXACTLY {unit['bars']} bars in every part, every bar totalling "
        f"{abc_check._fmt(beats)} units at L:{UNIT_NOTE_LENGTH} (a plain letter is 1, C2 is 2,",
        "    C/2 is a half, a triplet (3CDE takes 2). Count every bar before answering.",
        f"  * A part that rests here still writes its bars: z{abc_check._fmt(beats)} per bar.",
        "  * Written pitch, in the WRITTEN KEY given for that part. No header fields, no",
        "    [K:] changes, no repeat signs, no pickups, no final |] — barlines between",
        "    passages and key changes are added for you.",
        "  * Mark the dynamic on the first note of every part that plays, and phrase it:",
        "    hairpins, slurs, accents, staccato, tenuto.",
        "",
        "HOW TO COMPOSE IT",
        "  * You are composing, not arranging. Every part has a line with its own shape",
        "    and purpose — the tune, a counter-melody, a bass line, an inner voice with",
        "    real motion, an accompaniment figure — as the orchestration says.",
        "  * Harmony: every held note and every note on a beat belongs to that bar's",
        "    chord, unless it is a suspension, appoggiatura or passing note that",
        "    resolves by step. Check each vertical against the parts already composed.",
        "  * Independent lines move in contrary and oblique motion; no parallel fifths",
        "    or octaves between them (doubling a line at the octave is fine). The",
        "    lowest part carries the bass the chords name, inversions included.",
        "  * Lead into the next passage: the last bar should want to go where it goes.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def _defects(part: dict, text: dict, unit: dict, plan: dict, level: str) -> list:
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    beat = form.beat_units(plan["timeSignature"])
    key = form.written_key_for(part, unit["key"])
    low, high = composer._range_for(part, level)
    chords = {"chords": unit["chords"], "bars": unit["bars"],
              "voices": {v: [[] for _ in range(unit["bars"])] for v in form.VOICES}}
    staves = [(part["label"], text.get("abc") or "")]
    if part["staves"] == 2:
        staves.append((f"{part['label']} left hand", text.get("abcLower") or ""))

    problems = []
    for label, music in staves:
        found = form.check_phrase(music, unit["bars"], units, label)
        if low and part["pitched"]:
            found += abc_check.check_range(music, low, high, label)
        if not found:
            found += form.harmony_clashes(music, part, chords, key, beat, label)
        problems += found
    return problems


def _repair_prompt(plan: dict, unit: dict, roster: list, rows: dict, broken: dict) -> str:
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    by_id = {part["partId"]: part for part in roster}
    lines = [
        f"You composed these parts for mm.{unit['start']}-{unit['end']} of "
        f"\"{plan['title']}\" ({plan['timeSignature']}, concert key {unit['key']}).",
        "I checked them and found the problems listed under each.",
        "",
        "chords: " + " | ".join(f"m.{unit['start'] + i} {c}"
                                for i, c in enumerate(unit["chords"])),
        f"Every part has EXACTLY {unit['bars']} bars, each totalling "
        f"{abc_check._fmt(beats)} units at L:{UNIT_NOTE_LENGTH} (a plain letter is 1, C2 is 2, "
        "C/2 is a half).",
        "",
    ]
    for pid, problems in broken.items():
        part = by_id[pid]
        lines.append(f"[{pid}] {part['label']} — written key "
                     f"{form.written_key_for(part, unit['key'])}, range {part['fullRange']}:")
        lines.append(f"  {rows[pid].get('abc')}")
        if rows[pid].get("abcLower"):
            lines.append(f"  left hand: {rows[pid]['abcLower']}")
        lines += [f"  - {p}" for p in problems[:40]]
        lines.append("")
    lines += [
        "Correct exactly these problems: durations, bar count, notes out of range, held",
        "or on-the-beat notes off the chord. Keep everything else — the line, its",
        "rhythm, its articulation. Return only the parts listed above.",
    ]
    return "\n".join(lines)


def _rows(reply: dict, wanted: set) -> dict:
    return {row["partId"]: row for row in reply.get("parts") or []
            if row.get("partId") in wanted and row.get("abc")}


def _rest_line(bars: int, units) -> str:
    return " | ".join([f"z{abc_check._fmt(units)}"] * bars) + " |"


def _settle(part: dict, text: dict, unit: dict, plan: dict) -> dict:
    """Last resort, after the model's corrections: make the staves line up."""
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    key = form.written_key_for(part, unit["key"])
    fixed = dict(text)
    for field in ("abc", "abcLower"):
        if not text.get(field):
            continue
        line = form.fix_line(text[field], unit["bars"], units, part, key)
        if form.check_phrase(line, unit["bars"], units, ""):
            line = _rest_line(unit["bars"], units)
        fixed[field] = line
    return fixed


def _compose_group(plan: dict, request: dict, roster: list, units: list, index: int,
                   group: list, written: dict, later: list, model: str) -> tuple:
    """One group's lines for one passage: (rows, bars Python had to settle,
    the last error, labels of parts never written)."""
    unit = units[index]
    level = request["level"]
    rows, missing, error = {}, list(group), None
    for _ in range(ATTEMPTS):
        if not missing:
            break
        try:
            reply = _call(_passage_prompt(plan, request, roster, units, index, missing,
                                          written, later), "passage", PASSAGE_SCHEMA,
                          WRITE_EFFORT, model)
            rows.update(_rows(reply, {p["partId"] for p in missing}))
        except ComposerError as failure:
            error = failure
            _log.warning("mm.%d-%d failed: %s", unit["start"], unit["end"], failure)
        missing = [part for part in group if part["partId"] not in rows]

    by_id = {part["partId"]: part for part in group}
    broken = {pid: found for pid, row in rows.items()
              if (found := _defects(by_id[pid], row, unit, plan, level))}
    for _ in range(REPAIR_ROUNDS):
        if not broken:
            break
        try:
            reply = _call(_repair_prompt(plan, unit, roster, rows, broken), "passage_fix",
                          PASSAGE_SCHEMA, REPAIR_EFFORT, model)
        except ComposerError as failure:
            _log.warning("Correction of mm.%d-%d failed: %s", unit["start"], unit["end"],
                         failure)
            break
        for pid, row in _rows(reply, set(broken)).items():
            row["performanceNote"] = rows[pid].get("performanceNote") or ""
            after = _defects(by_id[pid], row, unit, plan, level)
            if len(after) < len(broken[pid]):
                rows[pid] = row
                broken[pid] = after
        broken = {pid: found for pid, found in broken.items() if found}

    notes = []
    for pid, found in broken.items():
        settled = _settle(by_id[pid], rows[pid], unit, plan)
        if settled != rows[pid] and any("units long" in p or "bars;" in p or "goes " in p
                                        for p in found):
            notes.append(f"{by_id[pid]['label']} mm.{unit['start']}-{unit['end']}")
        rows[pid] = settled
        _log.info("mm.%d-%d %s kept with: %s", unit["start"], unit["end"],
                  by_id[pid]["label"], "; ".join(found[:3]))
    return rows, notes, error, [part["label"] for part in missing]


def _compose_passage(plan, request, roster, units, index, model, tick) -> tuple:
    written, notes, errors, unwritten = {}, [], [], []
    groups = _groups(units[index], roster)
    for position, group in enumerate(groups):
        later = [part for g in groups[position + 1:] for part in g]
        rows, fixed, error, missing = _compose_group(plan, request, roster, units, index,
                                                     group, written, later, model)
        written.update(rows)
        notes += fixed
        unwritten += missing
        if error:
            errors.append(error)
        tick()
    return written, notes, errors, unwritten


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _assemble(plan: dict, roster: list, units: list, written: list) -> list:
    beats = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    roles = {r.get("partId"): r.get("role") for r in plan.get("partRoles") or []}
    out = []
    for part in roster:
        pid = part["partId"]
        home = form.written_key_for(part, units[0]["key"])
        current, upper, lower, note = home, [], [], ""
        for index, unit in enumerate(units):
            text = written[index].get(pid) or {}
            rest = _rest_line(unit["bars"], beats)
            up = orchestrate.split_bars(text.get("abc") or rest)
            low = orchestrate.split_bars(text.get("abcLower") or text.get("abc") or rest)
            key = form.written_key_for(part, unit["key"])
            if key != current:
                up[0] = f"[K:{key}] " + up[0]
                low[0] = f"[K:{key}] " + low[0]
                current = key
            joiner = " || " if index and unit["first"] else " | "
            upper.append((joiner, up))
            lower.append((joiner, low))
            note = note or text.get("performanceNote") or ""
        out.append({
            "partId": pid,
            "abcKey": home,
            "abc": form._join(upper),
            "abcLower": form._join(lower) if part["staves"] == 2 else None,
            "performanceNote": note or roles.get(pid) or "",
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
    passages = math.ceil(_bars_for(level) / 14)
    groups = math.ceil(max(1, part_count) / GROUP_STAVES)
    waves = math.ceil(passages / CONCURRENCY)
    # A passage's groups run in order, and about a third of them need a
    # correction round.
    typical = PLAN_SECONDS + waves * groups * CALL_SECONDS * 1.35
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

    units = _passages(plan)
    total = sum(len(_groups(unit, roster)) for unit in units)
    _log.info("Deep: %d measures, %d passages, %d calls before corrections",
              plan["measureCount"], len(units), total)
    lock, done = threading.Lock(), [0]

    def tick():
        with lock:
            done[0] += 1
            if report:
                report("writing", done[0], total)

    if report:
        report("writing", 0, total)
    written = [{} for _ in units]
    settled, errors, unwritten = [], [], []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(_compose_passage, plan, request, roster, units, index, model,
                               tick): index for index in range(len(units))}
        for future in as_completed(futures):
            rows, notes, failures, missing = future.result()
            written[futures[future]] = rows
            settled += notes
            errors += failures
            unwritten += missing

    if not any(written):
        raise errors[0] if errors else ComposerError("Nothing could be written this time. "
                                                     "Please try again.")

    if report:
        report("engraving", 0, 1)
    score = {
        **{key: plan.get(key) for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                          "timeSignature", "concertKey", "measureCount",
                                          "programNote", "rehearsalTips")},
        "conductor": plan.get("conductor") if request["conductor"] else None,
        "parts": _assemble(plan, roster, units, written),
    }
    warnings = composer._describe(composer.validate_notation(score, roster, level))
    if unwritten:
        names = sorted(set(unwritten))
        warnings.append("Some passages couldn't be written and are left as rests for: "
                        + ", ".join(names) + ". Generating again usually fills them.")
    if settled:
        warnings.append("A few bars were trimmed or padded so the staves line up: "
                        + ", ".join(settled[:6])
                        + (f" and {len(settled) - 6} more" if len(settled) > 6 else "") + ".")
    if report:
        report("engraving", 1, 1)
    return composer.package(score, roster, plan, warnings, bool(settled), model, mode="deep")
