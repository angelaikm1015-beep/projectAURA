"""Turning one harmony into every instrument's part, arithmetically.

The model's job is harmony: four voices that sound right together. Taking those
four voices and handing them to a B-flat clarinet, an F horn and a double bass
is not a musical judgement, it is transposition — and transposition is the one
thing a language model should never be asked to do twelve times in a row.

It had been doing exactly that. Each part was given the concert-pitch line in
words and asked to shift it an octave and transpose it for its instrument, note
by note, in its head. One slip anywhere is not a wrong note, it is a part that
is a semitone out against the rest of the orchestra for the whole piece — and
with a dozen parts each doing their own arithmetic, slips are certain.

So the arithmetic happens here, exactly, and the model never sees it. What comes
out is guaranteed to be the same harmony in every part, because it is the same
notes with a number added to them.

Reading and writing are key-aware: a plain F under K:G is read as F sharp, the
way a player reads it, and a pitch is written back with whatever accidental the
target key signature and the bar so far require. That is what lets a phrase be
moved into a different key — a sequence, a modulation — and still come out
spelled the way a musician would spell it.
"""

import re
from fractions import Fraction

import abc_check

# Kept for to_abc(), which spells without a key signature.
SHARP_NAMES = ["C", "^C", "D", "^D", "E", "F", "^F", "G", "^G", "A", "^A", "B"]
FLAT_NAMES = ["C", "_D", "D", "_E", "E", "F", "_G", "G", "_A", "A", "_B", "B"]

# Major keys by the number of sharps (+) or flats (-) in the signature.
KEY_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}
FIFTHS_TO_KEY = {v: k for k, v in KEY_FIFTHS.items()}

# Relative minors, so a minor key keeps its own signature.
MINOR_OF = {"C": "A", "G": "E", "D": "B", "A": "F#", "E": "C#", "B": "G#",
            "F#": "D#", "C#": "A#", "F": "D", "Bb": "G", "Eb": "C", "Ab": "F",
            "Db": "Bb", "Gb": "Eb", "Cb": "Ab"}
MAJOR_OF = {v: k for k, v in MINOR_OF.items()}

PITCH_OF_KEY = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
                "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
                "A#": 10, "Bb": 10, "B": 11, "Cb": 11}

LETTERS = "CDEFGAB"
NATURAL_PC = [0, 2, 4, 5, 7, 9, 11]
SHARP_ORDER = "FCGDAEB"
ACCIDENTAL_VALUE = {"^^": 2, "^": 1, "=": 0, "_": -1, "__": -2}
ACCIDENTAL_TEXT = {value: text for text, value in ACCIDENTAL_VALUE.items()}

NOTE_OR_REST = re.compile(r"(?P<acc>[\^_=]*)(?P<letter>[A-Ga-gz])(?P<marks>[,']*)")
NOTE_ONLY = re.compile(r"(?P<acc>\^\^|__|\^|_|=)?(?P<letter>[A-Ga-g])(?P<marks>[,']*)")
BARLINE_RE = abc_check.BARLINE_RE

# Where a note sits that is not in the key, by semitones above the tonic, and
# which scale degree it is spelled from: (degree, raised?) — so a C sharp in D
# minor is a raised 7th, not a D flat, and a B flat in C major is a lowered 7th.
CHROMATIC_MAJOR = {1: (1, True), 3: (3, False), 6: (4, True), 8: (6, False), 10: (7, False)}
CHROMATIC_MINOR = {1: (2, False), 4: (3, True), 6: (4, True), 9: (6, True), 11: (7, True)}


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def _split_key(key: str) -> tuple:
    """'G major' / 'Dm' / 'F#min' / 'Bb' -> (root, minor?)."""
    text = str(key or "C").strip()
    minor = bool(re.search(r"\b(m|min|minor)\b|m$", text, re.I)) and "maj" not in text.lower()
    root = re.match(r"\s*([A-Ga-g][#b]?)", text)
    root = root.group(1).capitalize() if root else "C"
    return root, minor


class Key:
    """A key signature, and the spelling rules that go with it."""

    def __init__(self, key: str):
        root, minor = _split_key(key)
        major_root = MAJOR_OF.get(root, root) if minor else root
        self.fifths = KEY_FIFTHS.get(major_root, 0)
        self.minor = minor
        self.tonic_letter = LETTERS.index(root[0])
        self.tonic_pc = PITCH_OF_KEY.get(root, 0)
        self.signature = [0] * 7
        for i in range(abs(self.fifths)):
            letter = SHARP_ORDER[i] if self.fifths > 0 else SHARP_ORDER[6 - i]
            self.signature[LETTERS.index(letter)] = 1 if self.fifths > 0 else -1

    def spell(self, midi: int) -> tuple:
        """MIDI -> (letter index, absolute accidental, octave)."""
        pc = midi % 12
        for letter in range(7):
            if (NATURAL_PC[letter] + self.signature[letter]) % 12 == pc:
                return letter, self.signature[letter], self._octave(midi, letter, self.signature[letter])

        table = CHROMATIC_MINOR if self.minor else CHROMATIC_MAJOR
        degree, _ = table.get((pc - self.tonic_pc) % 12, (1, True))
        letter = (self.tonic_letter + degree - 1) % 7
        accidental = (pc - NATURAL_PC[letter] + 6) % 12 - 6      # into -6..5
        if accidental not in ACCIDENTAL_TEXT:                     # fall back to the nearest
            letter = next(l for l in range(7) if abs((pc - NATURAL_PC[l] + 6) % 12 - 6) <= 1)
            accidental = (pc - NATURAL_PC[letter] + 6) % 12 - 6
        return letter, accidental, self._octave(midi, letter, accidental)

    @staticmethod
    def _octave(midi: int, letter: int, accidental: int) -> int:
        # B sharp and C flat belong to the octave of their letter, not their
        # sound: the octave is read from the natural note the accidental alters.
        return (midi - accidental) // 12 - 1


def shift_key(key: str, semitones: int) -> str:
    """The key ``semitones`` above ``key``: shift_key('G major', 2) -> 'A'."""
    return written_key(key, -int(semitones))


def written_key(concert_key: str, transpose_semitones: int) -> str:
    """The key signature this player reads, given the sounding key.

    ``transpose_semitones`` is written -> sounding, so the written key is the
    concert key moved the other way.
    """
    root, minor = _split_key(concert_key)
    major_root = MAJOR_OF.get(root, root) if minor else root
    fifths = KEY_FIFTHS.get(major_root, 0)

    # Move by fifths: a semitone step of 7 is one fifth, so transposing by s
    # semitones moves the signature by (s * 7) fifths, wrapped sensibly.
    moved = (fifths - transpose_semitones * 7)
    while moved > 7:
        moved -= 12
    while moved < -7:
        moved += 12
    # Seven sharps or flats is legal and unreadable; take the enharmonic key.
    if moved == 7:
        moved = -5
    elif moved == -7:
        moved = 5
    new_major = FIFTHS_TO_KEY.get(moved, "C")
    return (MINOR_OF[new_major] + "m") if minor else new_major


def _prefers_flats(key: str) -> bool:
    return Key(key).fifths < 0


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class _BarState:
    """Accidentals hold to the end of the bar, at that letter and octave."""

    def __init__(self, key: Key):
        self.key = key
        self.held = {}

    def reset(self):
        self.held = {}

    def current(self, letter: int, octave: int) -> int:
        return self.held.get((letter, octave), self.key.signature[letter])

    def read(self, accidental: str, letter_char: str, marks: str) -> int:
        letter = LETTERS.index(letter_char.upper())
        octave = (4 if letter_char.isupper() else 5) + marks.count("'") - marks.count(",")
        if accidental:
            value = ACCIDENTAL_VALUE.get(accidental, 0)
            self.held[(letter, octave)] = value
        else:
            value = self.current(letter, octave)
        return 12 * (octave + 1) + NATURAL_PC[letter] + value

    def write(self, midi: int) -> str:
        letter, accidental, octave = self.key.spell(midi)
        text = ""
        if accidental != self.current(letter, octave):
            text = ACCIDENTAL_TEXT[accidental]
            self.held[(letter, octave)] = accidental
        name = LETTERS[letter]
        if octave >= 5:
            return text + name.lower() + "'" * (octave - 5)
        return text + name + "," * (4 - octave)


def read_bars(music: str, key: str = "C") -> list:
    """ABC -> [[(midi or None, duration), …] per bar], read as a player reads it.

    Bar lines of every kind separate bars; repeat marks are not expanded.
    Dynamics, ornaments and grace notes are dropped: this reads a single melodic
    voice, which is what a skeleton line is. A chord collapses to its top note.
    """
    state = _BarState(Key(key))
    bars, events = [], []
    i, n = 0, len(music or "")

    def close():
        nonlocal events
        if events:
            bars.append(events)
        events = []
        state.reset()

    while i < n:
        ch = music[i]
        if ch in "!\"":
            j = music.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "{":
            j = music.find("}", i)
            i = n if j < 0 else j + 1
            continue
        barline = BARLINE_RE.match(music, i)
        if barline:
            close()
            i = barline.end()
            continue
        if ch == "[":
            if abc_check.INLINE_FIELD_RE.match(music, i):
                j = music.find("]", i)
                if music.startswith("[K:", i):          # a key change mid-part
                    state.key = Key(music[i + 3:j if j > 0 else n])
                    state.reset()
                i = n if j < 0 else j + 1
                continue
            j = music.find("]", i)
            end = j if j > 0 else n
            top = None
            for match in NOTE_ONLY.finditer(music, i + 1, end):
                midi = state.read(match.group("acc") or "", match.group("letter"),
                                  match.group("marks"))
                top = midi if top is None else max(top, midi)
            inner_first = abc_check.NOTE_RE.search(music, i + 1, end)
            inner = abc_check._read_duration(music, inner_first.end())[0] \
                if inner_first and inner_first.end() <= end else Fraction(1)
            dur, i = abc_check._read_duration(music, end + 1 if j > 0 else n)
            events.append((top, inner * dur))
            continue
        if ch in "zx":
            dur, i = abc_check._read_duration(music, i + 1)
            events.append((None, dur))
            continue
        match = NOTE_ONLY.match(music, i)
        if match:
            midi = state.read(match.group("acc") or "", match.group("letter"),
                              match.group("marks"))
            dur, i = abc_check._read_duration(music, match.end())
            events.append((midi, dur))
            continue
        i += 1
    close()
    return bars


def parse_line(music: str, key: str = "C") -> list:
    """ABC -> [(midi or None, duration)] in unit-note-length units."""
    return [event for bar in read_bars(music, key) for event in bar]


def fit_bar(events: list, units: Fraction) -> list:
    """Make one bar exactly ``units`` long: trim what overflows, hold what's short.

    The model's arithmetic is not trusted even in the skeleton. A bar that is a
    quaver short is held out on its last note rather than shifting every bar
    after it, which is what would happen if barlines were simply re-counted.
    """
    out, total = [], Fraction(0)
    for midi, dur in events:
        dur = Fraction(dur)
        if total + dur > units:
            dur = units - total
        if dur > 0:
            out.append((midi, dur))
            total += dur
        if total >= units:
            break
    if total < units:
        if out:
            out[-1] = (out[-1][0], out[-1][1] + units - total)
        else:
            out.append((None, units))
    return out


def split_bars(music: str) -> list:
    """ABC text -> one string per bar, decorations and all.

    Every barline variant is a separator, repeat marks included — the form
    decides what repeats, so a repeat sign inside a phrase would play it twice.
    """
    bars, start, i, n = [], 0, 0, len(music or "")
    while i < n:
        ch = music[i]
        if ch in "!\"":
            j = music.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(music, i):
            j = music.find("]", i)
            i = n if j < 0 else j + 1
            continue
        barline = BARLINE_RE.match(music, i)
        if barline:
            bars.append(music[start:i])
            i = start = barline.end()
            continue
        i += 1
    bars.append(music[start:])
    return [bar.strip() for bar in bars if bar.strip()]


# ---------------------------------------------------------------------------
# Moving it
# ---------------------------------------------------------------------------

def transpose(events: list, semitones: int) -> list:
    return [(None if m is None else m + semitones, d) for m, d in events]


def octave_shift_for(events: list, low_midi: int, high_midi: int,
                     easy_low: int = None, easy_high: int = None) -> int:
    """Octaves to add so the line sits inside the player's range.

    Chooses the shift that puts the most notes in range, then the most in the
    comfortable range, and otherwise moves the line as little as possible. The
    chorale's register IS its voicing: centring every line in its instrument
    would lift a cello's bass line above the viola's tenor and turn the chords
    upside down.
    """
    sounding = [m for m, _ in events if m is not None]
    if not sounding or low_midi is None or high_midi is None:
        return 0
    if easy_low is None or easy_high is None:
        easy_low, easy_high = low_midi, high_midi

    best, best_key = 0, None
    for shift in range(-3, 4):
        moved = [m + 12 * shift for m in sounding]
        inside = sum(1 for m in moved if low_midi <= m <= high_midi)
        easy = sum(1 for m in moved if easy_low <= m <= easy_high)
        centre = abs((sum(moved) / len(moved)) - (low_midi + high_midi) / 2)
        key = (-inside, -easy, abs(shift), centre)
        if best_key is None or key < best_key:
            best, best_key = shift, key
    return best


def out_of_range(events: list, low_midi, high_midi) -> int:
    if low_midi is None or high_midi is None:
        return 0
    return sum(1 for m, _ in events if m is not None and not low_midi <= m <= high_midi)


def transpose_abc(music: str, semitones: int, from_key: str, to_key: str) -> str:
    """Move written ABC by ``semitones``, keeping everything that isn't a pitch.

    Slurs, articulation, durations, ties and grace notes come through untouched;
    only the notes change, and each is respelled for ``to_key`` with whatever
    accidental that key and the bar so far require.
    """
    if not semitones and Key(from_key).signature == Key(to_key).signature:
        return music
    reader, writer = _BarState(Key(from_key)), _BarState(Key(to_key))
    out, i, n = [], 0, len(music or "")
    while i < n:
        ch = music[i]
        if ch in "!\"":
            j = music.find(ch, i + 1)
            j = n if j < 0 else j + 1
            out.append(music[i:j])
            i = j
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(music, i):
            j = music.find("]", i)
            j = n if j < 0 else j + 1
            out.append(music[i:j])
            i = j
            continue
        barline = BARLINE_RE.match(music, i)
        if barline:
            reader.reset()
            writer.reset()
            out.append(barline.group(0))
            i = barline.end()
            continue
        match = NOTE_ONLY.match(music, i)
        if match:
            midi = reader.read(match.group("acc") or "", match.group("letter"),
                               match.group("marks"))
            out.append(writer.write(midi + semitones))
            i = match.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Writing it back out
# ---------------------------------------------------------------------------

def to_abc(midi: int, flats: bool) -> str:
    """MIDI -> an ABC token, ignoring any key signature."""
    name = (FLAT_NAMES if flats else SHARP_NAMES)[midi % 12]
    accidental, letter = (name[:-1], name[-1]) if len(name) > 1 else ("", name)
    octave = midi // 12 - 1          # 60 -> 4

    if octave >= 5:
        token = letter.lower() + "'" * (octave - 5)
    else:
        token = letter + "," * (4 - octave)
    return accidental + token


def _duration(value: Fraction) -> str:
    if value == 1:
        return ""
    if value.denominator == 1:
        return str(value.numerator)
    if value.numerator == 1:
        return f"/{value.denominator}"
    return f"{value.numerator}/{value.denominator}"


def rest_bar(units: Fraction) -> str:
    return "z" + _duration(Fraction(units))


def render_bars(events: list, key: str, units_per_bar: Fraction) -> list:
    """[(midi, duration)] -> one ABC string per bar, spelled for ``key``.

    Barlines are placed by counting, not by trusting anyone's arithmetic, so a
    part generated this way cannot come out with a short bar.
    """
    state = _BarState(Key(key))
    out, bar, filled = [], [], Fraction(0)

    for midi, dur in events:
        remaining = Fraction(dur)
        while remaining > 0:
            room = units_per_bar - filled
            take = min(room, remaining)
            token = "z" if midi is None else state.write(midi)
            bar.append(token + _duration(take))
            # A note split by a barline is tied across it.
            if take < remaining and midi is not None:
                bar.append("-")
            filled += take
            remaining -= take
            if filled >= units_per_bar:
                out.append(" ".join(bar))
                bar, filled = [], Fraction(0)
                state.reset()
    if bar:
        # Pad the last bar so every part ends together.
        short = units_per_bar - filled
        if short > 0:
            bar.append("z" + _duration(short))
        out.append(" ".join(bar))
    return out


def render(events: list, key: str, units_per_bar: Fraction) -> str:
    """[(midi, duration)] -> ABC with barlines every ``units_per_bar``."""
    return " | ".join(render_bars(events, key, units_per_bar)) + " |"


# ---------------------------------------------------------------------------
# One player's part
# ---------------------------------------------------------------------------

def range_of(part: dict, which: str = "fullRange") -> tuple:
    text = part.get(which) or "-"
    return (abc_check.parse_scientific(text.split("-")[0]),
            abc_check.parse_scientific(text.split("-")[-1]))


def fit_octave(events: list, part: dict) -> int:
    """octave_shift_for() against this player's full and comfortable ranges."""
    return octave_shift_for(events, *range_of(part), *range_of(part, "easyRange"))


def derive_part(voice_abc: str, part: dict, concert_key: str,
                units_per_bar: Fraction, sustained: bool = False) -> dict:
    """A skeleton voice, as this instrument actually reads it.

    Exact, and therefore in tune with every other part derived the same way.
    """
    events = [e for bar in read_bars(voice_abc, concert_key)
              for e in fit_bar(bar, units_per_bar)]
    if sustained:
        events = merge_repeats(events)

    written = transpose(events, -int(part.get("transpose", 0)))
    written = transpose(written, 12 * fit_octave(written, part))

    key = written_key(concert_key, int(part.get("transpose", 0)))
    return {"abcKey": key, "abc": render(written, key, units_per_bar)}


def merge_repeats(events: list) -> list:
    """Tie consecutive identical pitches into one long note."""
    merged = []
    for midi, dur in events:
        if merged and merged[-1][0] == midi:
            merged[-1] = (midi, merged[-1][1] + dur)
        else:
            merged.append((midi, Fraction(dur)))
    return merged


_merge_repeats = merge_repeats


# ---------------------------------------------------------------------------
# Chord symbols
# ---------------------------------------------------------------------------

CHORD_ROOT_RE = re.compile(r"(^|/)([A-G])([#b]?)")


def transpose_chord(symbol: str, semitones: int, key: str) -> str:
    """'A7/C#' up 2 in D major -> 'B7/D#'. Roots and slash basses both move."""
    if not semitones:
        return symbol
    flats = _prefers_flats(key)
    names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"] if flats \
        else ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    def move(match):
        pc = PITCH_OF_KEY.get(match.group(2) + match.group(3), 0)
        return match.group(1) + names[(pc + semitones) % 12]
    return CHORD_ROOT_RE.sub(move, str(symbol or ""))
