"""Measure-length checking for ABC notation.

The one thing a language model reliably gets wrong when writing music is
arithmetic: bars that don't add up, and parts that drift out of alignment with
each other. Both are invisible in the returned JSON and obvious the moment
anyone tries to play the score.

So we check it here rather than trusting the model. This walks the note stream
and totals each bar's duration in units of L (the unit note length), which is
enough to answer "is every bar the right length, and does every part have the
same number of bars?" — and, when the answer is no, to say exactly which bar is
wrong and by how much, which is what makes an automated repair round possible.

It is not a full ABC parser. It understands only as much syntax as it takes to
add up durations, and deliberately ignores everything that cannot change them:
slurs, ties, accidentals, octave marks, dynamics and broken-rhythm marks.
"""

import re
from fractions import Fraction

NOTE_RE = re.compile(r"[\^_=]*[A-Ga-g][,']*")
DURATION_RE = re.compile(r"(\d*)(/*)(\d*)")
INLINE_FIELD_RE = re.compile(r"\[[A-Za-z]:")
BARLINE_RE = re.compile(r":\|\]|\|\]|\[\||\|\||::|:\||\|:|\|")
TUPLET_RE = re.compile(r"\((\d+)(?::(\d*))?(?::(\d*))?")

# "(p" means p notes in the time of q. These are the ABC defaults for simple
# meters; compound meters differ, which is why a tuplet only ever relaxes the
# check rather than tightening it.
TUPLET_DEFAULTS = {2: 3, 3: 2, 4: 3, 5: 2, 6: 2, 7: 2, 8: 3, 9: 2}

DECORATIONS = set(".~vuHIJLMOPSTRk")

WHOLE_BAR = Fraction(-1)  # marker for Z (multi-measure rest): length unknowable


class Bar:
    __slots__ = ("index", "length", "uncertain")

    def __init__(self, index):
        self.index = index
        self.length = Fraction(0)
        self.uncertain = False


def _read_duration(text, i):
    """Parse a duration suffix at ``i``. Returns (multiplier, next_index)."""
    match = DURATION_RE.match(text, i)
    if not match or not match.group(0):
        return Fraction(1), i

    numerator, slashes, denominator = match.groups()
    value = Fraction(int(numerator) if numerator else 1)
    if slashes:
        value /= int(denominator) if denominator else 2 ** len(slashes)
    return value, match.end()


def bar_lengths(music: str):
    """Total the duration of every bar, in units of the unit note length.

    Repeat marks don't multiply anything — a repeated section is the same bars
    played twice, not more bars.
    """
    bars = [Bar(1)]
    tuplet_remaining, tuplet_ratio = 0, Fraction(1)
    i, n = 0, len(music)

    def add(length):
        nonlocal tuplet_remaining
        if tuplet_remaining:
            length *= tuplet_ratio
            tuplet_remaining -= 1
            bars[-1].uncertain = True
        bars[-1].length += length

    while i < n:
        char = music[i]

        if char in " \t\n\r":
            i += 1
        elif char == "%":                                   # comment to end of line
            end = music.find("\n", i)
            i = n if end == -1 else end
        elif char == "!":                                   # !ff!, !crescendo(!
            end = music.find("!", i + 1)
            i = n if end == -1 else end + 1
        elif char == '"':                                   # "Gm7", "^Solo"
            end = music.find('"', i + 1)
            i = n if end == -1 else end + 1
        elif char == "{":                                   # grace notes: no duration
            end = music.find("}", i)
            i = n if end == -1 else end + 1
        elif char == "(" and i + 1 < n and music[i + 1].isdigit():
            match = TUPLET_RE.match(music, i)
            p = int(match.group(1))
            q = int(match.group(2)) if match.group(2) else TUPLET_DEFAULTS.get(p, 2)
            tuplet_ratio = Fraction(q, p)
            tuplet_remaining = int(match.group(3)) if match.group(3) else p
            i = match.end()
        elif char in "()-<>&":                               # slurs, ties, broken rhythm
            i += 1
        elif char in DECORATIONS:
            i += 1
        elif INLINE_FIELD_RE.match(music, i):               # [K:G], [M:3/4]
            end = music.find("]", i)
            i = n if end == -1 else end + 1
        elif BARLINE_RE.match(music, i):
            i = BARLINE_RE.match(music, i).end()
            # A leading "|:" or a "| |" pair would otherwise open a phantom
            # empty bar and shift every bar number after it.
            if bars[-1].length != 0 or bars[-1].uncertain:
                bars.append(Bar(len(bars) + 1))
        elif char == "[":                                   # chord: one duration for all
            end = music.find("]", i)
            if end == -1:
                i = n
                continue
            inner = music[i + 1:end]
            first = NOTE_RE.search(inner)
            length = _read_duration(inner, first.end())[0] if first else Fraction(1)
            multiplier, i = _read_duration(music, end + 1)
            add(length * multiplier)
        elif char in "zx":                                  # rest
            length, i = _read_duration(music, i + 1)
            add(length)
        elif char == "Z":                                   # multi-measure rest
            count, i = _read_duration(music, i + 1)
            for _ in range(max(1, int(count)) - 1):
                bars[-1].length = WHOLE_BAR
                bars.append(Bar(len(bars) + 1))
            bars[-1].length = WHOLE_BAR
        elif NOTE_RE.match(music, i):
            match = NOTE_RE.match(music, i)
            length, i = _read_duration(music, match.end())
            add(length)
        else:
            i += 1

    while len(bars) > 1 and bars[-1].length == 0:           # trailing barline
        bars.pop()
    return bars


def units_per_bar(time_signature: str, unit_note_length: str) -> Fraction:
    """How many unit-note-lengths fill one bar of this meter."""
    def parse(value, default):
        try:
            top, _, bottom = str(value).partition("/")
            return Fraction(int(top), int(bottom)) if bottom else Fraction(int(top))
        except (TypeError, ValueError, ZeroDivisionError):
            return default

    return parse(time_signature, Fraction(4, 4)) / parse(unit_note_length, Fraction(1, 8))


def check_part(music: str, expected: Fraction, label: str) -> list:
    """Report bars whose length isn't ``expected``, as human-readable strings.

    A short opening bar is a pickup, which is correct as long as the closing bar
    is short by the matching amount — that is how an anacrusis is written.
    """
    bars = bar_lengths(music)
    if not bars or (len(bars) == 1 and bars[0].length == 0):
        return [f"{label}: no music at all."]

    checkable = list(range(len(bars)))
    if len(bars) > 1 and 0 < bars[0].length < expected \
            and bars[0].length + bars[-1].length == expected:
        checkable = checkable[1:-1]                          # pickup + final bar

    problems = []
    for index in checkable:
        bar = bars[index]
        if bar.length == WHOLE_BAR or bar.uncertain:
            continue
        if bar.length != expected:
            problems.append(
                f"{label} bar {bar.index} is {_fmt(bar.length)} units long, needs {_fmt(expected)}")
    return problems


def _fmt(value: Fraction) -> str:
    return str(int(value)) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def bar_count(music: str) -> int:
    return len(bar_lengths(music))


# ---------------------------------------------------------------------------
# Pitch and range
# ---------------------------------------------------------------------------
#
# The other requirement a model can break silently is range. A beginner flute
# part that touches C7, or a cello line written above the treble staff, reads
# perfectly well as JSON and is unplayable by the group it was written for.

SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

SCIENTIFIC_RE = re.compile(r"^([A-Ga-g])([#b]*)(-?\d+)$")

# Skipped wholesale when scanning for pitches.
SKIP_PAIRS = {"!": "!", '"': '"', "{": "}"}


def parse_scientific(name: str):
    """'F#3' or 'Bb1' -> MIDI number. None if it isn't a pitch (e.g. '-')."""
    match = SCIENTIFIC_RE.match(str(name).strip())
    if not match:
        return None
    letter, accidentals, octave = match.groups()
    value = SEMITONES[letter.upper()] + 12 * (int(octave) + 1)
    value += accidentals.count("#") - accidentals.count("b")
    return value


def abc_pitch(token: str):
    """'^c,' -> MIDI number. Uppercase C is middle C (60), lowercase c is 72."""
    match = NOTE_RE.match(token)
    if not match:
        return None
    text = match.group(0)
    letter_at = next(i for i, ch in enumerate(text) if ch.upper() in SEMITONES)
    accidentals, letter, marks = text[:letter_at], text[letter_at], text[letter_at + 1:]

    value = SEMITONES[letter.upper()] + (72 if letter.islower() else 60)
    value += 12 * (marks.count("'") - marks.count(","))
    value += accidentals.count("^") - accidentals.count("_")
    return value


def pitches(music: str):
    """Yield the MIDI pitch of every sounding note, chord members included.

    Grace notes are skipped — they're ornaments, and holding them to the same
    range limit as the line itself produces false alarms.
    """
    i, n = 0, len(music)
    while i < n:
        char = music[i]
        if char in SKIP_PAIRS:
            end = music.find(SKIP_PAIRS[char], i + 1)
            i = n if end == -1 else end + 1
        elif char == "%":
            end = music.find("\n", i)
            i = n if end == -1 else end
        elif INLINE_FIELD_RE.match(music, i):
            end = music.find("]", i)
            i = n if end == -1 else end + 1
        elif NOTE_RE.match(music, i):
            match = NOTE_RE.match(music, i)
            value = abc_pitch(match.group(0))
            if value is not None:
                yield value
            i = match.end()
        else:
            i += 1


def _name(midi: int) -> str:
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


# A key signature can shift a written note by a semitone either way, and this
# scan doesn't apply key signatures. One semitone of slack keeps that from
# becoming a false alarm; anything further out is a real problem.
RANGE_SLACK = 1


def check_range(music: str, low: str, high: str, label: str) -> list:
    """Report notes outside ``low``-``high`` (written pitch, scientific names)."""
    bottom, top = parse_scientific(low), parse_scientific(high)
    if bottom is None or top is None:
        return []                                    # unpitched, or no range given

    below, above = [], []
    for value in pitches(music):
        if value < bottom - RANGE_SLACK:
            below.append(value)
        elif value > top + RANGE_SLACK:
            above.append(value)

    problems = []
    if below:
        problems.append(f"{label} goes down to {_name(min(below))}, below its lowest "
                        f"playable note {low} ({len(below)} note(s))")
    if above:
        problems.append(f"{label} goes up to {_name(max(above))}, above its highest "
                        f"playable note {high} ({len(above)} note(s))")
    return problems
