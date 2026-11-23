"""Is this a melody? A solo line checked the way a composition teacher would.

A solo is the most exposed thing in the piece: one instrument, alone, over
almost nothing. Written in passing, the model's solos leap about, break every
half bar on a rest, never breathe, or hit the same top note five times. Each
solo is therefore composed by a call of its own (deep.py) and read here; if it
has faults, they go back once, with bar numbers, and the version with fewer
faults is kept.

``critique`` reads compact.Event bars at sounding pitch and returns sentences.
"""

from fractions import Fraction

import form

AWKWARD = {6: "a tritone", 10: "a seventh", 11: "a seventh"}
LEAP = 5                 # a fourth or more is a leap that wants recovering
STEP_SHARE = 0.5         # at least this share of the intervals move by step
BREATH_BARS = 4          # a wind player breathes at least this often
WINDS = ("Woodwinds", "Brass", "Voice")


def _notes(bars: list) -> list:
    """[(bar index, start in the bar, duration, top midi)] of every attack.

    A note tied on from the one before is the same note, held longer.
    """
    out, tied = [], False
    for index, events in enumerate(bars):
        at = Fraction(0)
        for event in events:
            if event.notes:
                midi = max(event.midis)
                if tied and out and out[-1][3] == midi:
                    bar, start, dur, _ = out[-1]
                    out[-1] = (bar, start, dur + event.dur, midi)
                else:
                    out.append((index, at, event.dur, midi))
            tied = bool(event.notes) and event.tie
            at += event.dur
    return out


def critique(bars: list, start: int, family: str, chords: list, units: Fraction,
             beat: Fraction, singing: tuple = None) -> list:
    """Faults in a solo line, as sentences naming the measures (numbered from ``start``)."""
    notes = _notes(bars)
    if len(notes) < 4:
        return ["The solo has hardly any notes: write a real melody, phrase after phrase."]
    faults = []
    measure = lambda index: start + index

    # Leaps: none awkward, none wider than an octave, and a big one turns back.
    awkward, wide, unrecovered = [], [], []
    steps = 0
    for k in range(1, len(notes)):
        interval = notes[k][3] - notes[k - 1][3]
        size = abs(interval)
        steps += size <= 2
        if size % 12 in AWKWARD and size < 12:
            awkward.append((measure(notes[k][0]), AWKWARD[size % 12]))
        if size > 12:
            wide.append(measure(notes[k][0]))
        if size >= LEAP and k + 1 < len(notes):
            after = notes[k + 1][3] - notes[k][3]
            if after and (after > 0) == (interval > 0):
                # Two leaps the same way are fine only if they spell one chord.
                pcs = form.chord_pcs(chords[notes[k][0]]) if notes[k][0] < len(chords) else None
                spelled = pcs and all(notes[j][3] % 12 in pcs for j in (k - 1, k, k + 1))
                if not spelled:
                    unrecovered.append(measure(notes[k][0]))
    if awkward:
        faults.append("Awkward leaps: " + ", ".join(f"m.{m} ({name})" for m, name in awkward[:6])
                      + ". Never leap a tritone or a seventh in a melody.")
    if wide:
        faults.append(f"Leaps wider than an octave at mm. {', '.join(map(str, wide[:6]))}.")
    if unrecovered:
        faults.append(f"At mm. {', '.join(map(str, sorted(set(unrecovered))[:6]))} a leap of a "
                      "fourth or more is followed by another the same way: after a leap, turn "
                      "back by step.")
    if steps < STEP_SHARE * (len(notes) - 1):
        faults.append(f"Only {100 * steps // max(1, len(notes) - 1)}% of the line moves by step: "
                      "a melody that sings moves mostly by step, with leaps as events.")

    # Phrasing: no hiccup of rests inside the bars, and room to breathe.
    broken = []
    for index, events in enumerate(bars):
        sounding = [i for i, e in enumerate(events) if e.notes]
        if sounding and any(not e.notes for e in events[sounding[0]:sounding[-1]]):
            broken.append(measure(index))
    if len(broken) > len(bars) // 3:
        faults.append(f"The line keeps stopping for rests inside bars (mm. "
                      f"{', '.join(map(str, broken[:8]))}): phrase it in two- and four-bar "
                      "phrases under slurs, with a breath between phrases, not within them.")
    if family in WINDS:
        run = longest = 0
        for events in bars:
            run = 0 if any(not e.sounding for e in events) else run + 1
            longest = max(longest, run)
        if longest > BREATH_BARS:
            faults.append(f"{longest} bars without a rest: a wind player needs a breath at "
                          f"least every {BREATH_BARS} bars, between phrases.")

    # One high point, not a ceiling hit again and again.
    top = max(n[3] for n in notes)
    hits = [n for n in notes if n[3] == top]
    if len(hits) > 2:
        faults.append(f"The highest note comes {len(hits)} times (mm. "
                      f"{', '.join(str(measure(n[0])) for n in hits[:6])}): save it for one "
                      "high point about two thirds of the way through.")

    # A rhythm with a profile, not a stream of equal values.
    lengths = [n[2] for n in notes]
    common = max(set(lengths), key=lengths.count)
    if lengths.count(common) > 0.8 * len(lengths) and len(lengths) > 8:
        faults.append("Almost every note is the same length: mix long notes with shorter "
                      "ones, and let one rhythmic idea from the theme come back.")

    if singing and singing[0] is not None:
        low, high = singing
        outside = sorted({measure(n[0]) for n in notes if not low <= n[3] <= high})
        if outside:
            faults.append(f"Outside the instrument's singing register at mm. "
                          f"{', '.join(map(str, outside[:8]))}.")

    # It ends: a long note on the chord.
    last = notes[-1]
    pcs = form.chord_pcs(chords[last[0]]) if last[0] < len(chords) else None
    if last[2] < beat or (pcs and last[3] % 12 not in pcs):
        faults.append("The solo stops rather than ending: finish its last phrase on a long "
                      "note that belongs to the chord.")
    return faults
