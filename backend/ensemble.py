"""Listening to the parts together.

Every other check in the app reads one line at a time: is this bar the right
length, is this note in range, is this note on the chord. None of them can say
what the parts sound like *together*, which is where an orchestra succeeds or
fails — two lines a semitone apart, a chord with nobody on its third, a cello
and bassoon grinding a third apart in the bass, a viola climbing over the tune.

This module reads a passage of the score the way a conductor reads it, down
the page, at concert pitch:

* ``score_grid`` writes what sounds on every beat, part by part, so the model
  can see the chord the ensemble actually makes rather than imagining it;
* ``listen`` finds the problems that only exist between parts.

Both are handed to the model in deep mode (deep.py), which writes and revises
the parts with them in front of it.
"""

import re
from fractions import Fraction

import abc_check
import form
import orchestrate
from composer import UNIT_NOTE_LENGTH

SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

LOW_REGISTER = 52        # below E3, close intervals turn to mud
MUD_INTERVAL = 4         # a major third or closer, down there
DOUBLING_SHARE = 0.6     # two parts in unison/octaves this often are a doubling
COVER_SHARE = 0.5        # an accompanist above the tune this often covers it
MAX_PROBLEMS = 30        # the most listed for one passage
PER_KIND = 8             # and the most of any one kind

CHORD_RE = re.compile(r"\[(?![A-Za-z]:)([^\]]*)\]")


def note_name(midi: int, flats: bool = False) -> str:
    return f"{(FLATS if flats else SHARPS)[midi % 12]}{midi // 12 - 1}"


# ---------------------------------------------------------------------------
# Reading the passage
# ---------------------------------------------------------------------------

def _bar_notes(bar_events: list, units: Fraction, transpose: int) -> list:
    """[(start, end, sounding midi)] for one bar, rests dropped.

    A bar that doesn't add up (a triplet read at face value) is scaled to fit,
    so every part lines up against the same beats.
    """
    total = sum((d for _, d in bar_events), Fraction(0))
    scale = units / total if total and total != units else Fraction(1)
    at, out = Fraction(0), []
    for midi, duration in bar_events:
        length = duration * scale
        if midi is not None:
            out.append((at, at + length, midi + transpose))
        at += length
    return out


def _chord_pcs(bar_text: str, key: str, transpose: int) -> set:
    """Pitch classes inside a bar's [chords] — the notes below the top one."""
    pcs = set()
    for inner in CHORD_RE.findall(bar_text):
        notes = " ".join(re.findall(r"[\^_=]*[A-Ga-g][,']*", inner))
        for bar in orchestrate.read_bars(notes, key) if notes else []:
            pcs |= {(m + transpose) % 12 for m, _ in bar if m is not None}
    return pcs


def voices(plan: dict, roster: list, written: dict, run: list) -> list:
    """Each pitched staff in the passage: its notes, measure by measure.

    [{"partId", "label", "short", "events": {measure: [(start, end, midi)]},
      "chords": {measure: {pc}}}]
    """
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    out = []
    for part in roster:
        if not part["pitched"]:
            continue
        staves = [("abc", part["label"])]
        if part["staves"] == 2:
            staves.append(("abcLower", f"{part['label']} LH"))
        for field, label in staves:
            events, chords = {}, {}
            for index in run:
                s = plan["_sections"][index]
                text = (written.get(part["partId"], {}).get(index) or {}).get(field)
                if not text:
                    continue
                key = form.written_key_for(part, s["key"])
                raw = orchestrate.split_bars(text)
                for i, bar in enumerate(orchestrate.read_bars(text, key)[:s["bars"]]):
                    measure = s["start"] + i
                    events[measure] = _bar_notes(bar, units, int(part["transpose"]))
                    if i < len(raw):
                        chords[measure] = _chord_pcs(raw[i], key, int(part["transpose"]))
            if events:
                out.append({"partId": part["partId"], "label": label,
                            "events": events, "chords": chords})
    return out


def _sounding(voice: dict, measure: int, at: Fraction):
    """(midi, attacked here?) of what this staff sounds at that moment, or None."""
    for start, end, midi in voice["events"].get(measure, []):
        if start <= at < end:
            return midi, start == at
    return None


def _measures(plan: dict, run: list) -> list:
    return [(plan["_sections"][i], m) for i in run
            for m in range(plan["_sections"][i]["start"], plan["_sections"][i]["end"] + 1)]


def _beats(plan: dict) -> tuple:
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    beat = form.beat_units(plan["timeSignature"])
    count = max(1, int(units / beat))
    return units, beat, [beat * k for k in range(count)]


def _chord_of(plan: dict, measure: int):
    harmony = {h["measure"]: h["chord"] for h in plan.get("harmony") or []}
    return harmony.get(measure)


# ---------------------------------------------------------------------------
# What the model reads: the passage, beat by beat
# ---------------------------------------------------------------------------

def score_grid(plan: dict, roster: list, written: dict, run: list) -> list:
    """What sounds on every beat of the passage, at concert pitch.

    One line per beat: the chord the plan names, then each staff's note, lowest
    first, '~' for a note still held from before. This is the nearest thing the
    model gets to hearing the ensemble.
    """
    staves = voices(plan, roster, written, run)
    if not staves:
        return []
    units, beat, beats = _beats(plan)
    lines = []
    for s, measure in _measures(plan, run):
        flats = orchestrate._prefers_flats(s["key"])
        symbol = _chord_of(plan, measure) or ""
        for number, at in enumerate(beats, 1):
            chord = form.chord_at(symbol, at, units, beat) if symbol else ""
            heard = []
            for voice in staves:
                sound = _sounding(voice, measure, at)
                if sound:
                    heard.append((sound[0], f"{voice['label']} {note_name(sound[0], flats)}"
                                            + ("" if sound[1] else "~")))
            notes = ", ".join(text for _, text in sorted(heard)) or "—"
            lines.append(f"m.{measure}.{number} [{chord}] {notes}")
    return lines


# ---------------------------------------------------------------------------
# What only exists between parts
# ---------------------------------------------------------------------------

def listen(plan: dict, roster: list, written: dict, run: list) -> list:
    """Problems between the parts of one passage, worst first, as sentences."""
    staves = voices(plan, roster, written, run)
    if len(staves) < 2:
        return []
    units, beat, beats = _beats(plan)
    points = [(s, measure, at) for s, measure in _measures(plan, run) for at in beats]
    found = {"parallel": [], "third": [], "clash": [], "mud": [], "cover": []}

    def add(kind, text):
        if len(found[kind]) < PER_KIND:
            found[kind].append(text)

    # What every staff sounds at every beat, once.
    heard = [[_sounding(v, measure, at) for v in staves] for _, measure, at in points]

    # Parallel fifths and octaves between independent lines. Two parts in
    # unison or octaves most of the time are a doubling — orchestration, not
    # an error — and are left alone.
    for a in range(len(staves)):
        for b in range(a + 1, len(staves)):
            if staves[a]["partId"] == staves[b]["partId"]:
                continue
            both = [(x[a][0], x[b][0]) for x in heard if x[a] and x[b]]
            if both and sum(1 for p, q in both if (p - q) % 12 == 0) >= DOUBLING_SHARE * len(both):
                continue
            for k in range(1, len(points)):
                before, now = heard[k - 1], heard[k]
                if not (before[a] and before[b] and now[a] and now[b]):
                    continue
                if not (now[a][1] and now[b][1]):
                    continue                   # both must move to the new beat
                da, db = now[a][0] - before[a][0], now[b][0] - before[b][0]
                if da == 0 or db == 0 or (da > 0) != (db > 0):
                    continue
                was = abs(before[a][0] - before[b][0]) % 12
                is_ = abs(now[a][0] - now[b][0]) % 12
                if was == is_ and is_ in (0, 7):
                    _, measure, at = points[k]
                    kind = "octaves" if is_ == 0 else "fifths"
                    add("parallel", f"m.{measure} beat {int(at / beat) + 1}: "
                                    f"{staves[a]['label']} and {staves[b]['label']} move in "
                                    f"parallel {kind}.")
                    break                      # one per pair is enough to fix it

    # A chord nobody gives its third to sounds hollow.
    for s, measure in _measures(plan, run):
        symbol = _chord_of(plan, measure)
        if not symbol:
            continue
        for chord, start, end in form.chord_spans(symbol, units, beat):
            pcs = form.chord_pcs(chord)
            root = form._root_pc(chord)
            if root is None or not pcs:
                continue
            third = next((t for t in ((root + 3) % 12, (root + 4) % 12) if t in pcs), None)
            if third is None:
                continue                       # sus and power chords have none
            sounding, players = set(), 0
            for voice in staves:
                notes = [m for st, en, m in voice["events"].get(measure, [])
                         if st < end and en > start]
                if notes:
                    players += 1
                    sounding |= {m % 12 for m in notes}
                sounding |= voice["chords"].get(measure, set())
            if players >= 3 and third not in sounding:
                flats = orchestrate._prefers_flats(s["key"])
                add("third", f"m.{measure}: nobody plays the third of {chord} "
                             f"({(FLATS if flats else SHARPS)[third]}) — the chord sounds "
                             "hollow. Give it to an inner part.")

    for k, (s, measure, at) in enumerate(points):
        flats = orchestrate._prefers_flats(s["key"])
        sounding = sorted((x[0], i, x) for i, x in enumerate(heard[k]) if x)
        # Two held notes a semitone (or a minor ninth) apart grind.
        for i in range(len(sounding)):
            for j in range(i + 1, len(sounding)):
                (p, a, _), (q, b, _) = sounding[i], sounding[j]
                if staves[a]["partId"] == staves[b]["partId"] or (q - p) % 12 not in (1, 11):
                    continue
                long_a = any(st <= at < en and en - st >= beat
                             for st, en, m in staves[a]["events"].get(measure, []) if m == p)
                long_b = any(st <= at < en and en - st >= beat
                             for st, en, m in staves[b]["events"].get(measure, []) if m == q)
                if long_a and long_b:
                    add("clash", f"m.{measure} beat {int(at / beat) + 1}: "
                                 f"{staves[a]['label']} {note_name(p, flats)} against "
                                 f"{staves[b]['label']} {note_name(q, flats)} — a held "
                                 "semitone clash.")
        # Close intervals low down are mud.
        low = [(p, a) for p, a, _ in sounding if p < LOW_REGISTER]
        for (p, a), (q, b) in zip(low, low[1:]):
            if staves[a]["partId"] != staves[b]["partId"] and 0 < q - p <= MUD_INTERVAL \
                    and at == 0:
                add("mud", f"m.{measure}: {staves[a]['label']} {note_name(p, flats)} and "
                           f"{staves[b]['label']} {note_name(q, flats)} are {q - p} semitones "
                           "apart below E3 — muddy. Open the spacing to a fifth or more.")
                break

    # The tune must be heard: accompaniment sitting just above it covers it.
    by_part = {}
    for i, v in enumerate(staves):
        by_part.setdefault(v["partId"], i)
    for index in run:
        s = plan["_sections"][index]
        lead = by_part.get(s.get("lead"))
        if lead is None:
            continue
        rows = [k for k, (sec, _, _) in enumerate(points) if sec is s]
        for i, voice in enumerate(staves):
            if voice["partId"] == s["lead"]:
                continue
            both = [(heard[k][lead][0], heard[k][i][0]) for k in rows
                    if heard[k][lead] and heard[k][i]]
            if len(both) < 4:
                continue
            above = sum(1 for tune, other in both if 0 < other - tune <= 12)
            if above >= COVER_SHARE * len(both):
                add("cover", f"s{index + 1} (mm.{s['start']}-{s['end']}): {voice['label']} "
                             f"sits just above the tune ({staves[lead]['label']}) and covers "
                             "it. Move it below the tune or an octave clear of it.")
            if s.get("texture") == "solo":
                same = sum(1 for tune, other in both if tune == other)
                if same >= DOUBLING_SHARE * len(both):
                    add("cover", f"s{index + 1}: {voice['label']} doubles the soloist in "
                                 "unison — a solo is one player. Accompany instead.")

    order = ("clash", "third", "parallel", "cover", "mud")
    return [text for kind in order for text in found[kind]][:MAX_PROBLEMS]
