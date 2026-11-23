"""The compact score: what the model writes, and what it becomes.

Asking a model for notation it has to get exactly right — ABC with key
signatures, octave marks and transposed parts, or MusicXML, which runs to
thousands of lines for an orchestral score — spends its attention on syntax and
lets wrong notes in: one forgotten sharp under a key signature and a part
clashes with everyone else. So in deep mode the model writes every measure as
one short string, every note spelled out at sounding pitch:

    "@p C4:4( D4:8 E4:8 F4:4 G4:4)"     dynamic p, a slur, quarters and eighths
    "G4+B4:2. r:4"                      a chord (dotted half) and a quarter rest
    "E5:2~ E5:8 D5:8 C5:4"              a tie
    "R"                                 a whole-measure rest

and this module does the rest:

* ``read_part`` parses it and repairs what models actually get wrong — a bar
  that doesn't add up, a note out of range, more notes at once than the
  instrument has, a tie between different notes, a slur that never closes —
  counting every repair;
* ``land_on_chords``, ``balance`` and ``finish`` do the finishing work at the
  level of notes, at concert pitch, where it is simple;
* ``render_abc`` writes the part the site displays and plays, transposed into
  the player's key with the key signature and accidentals worked out; and
  ``to_compact`` writes the final notes back out, small enough to store, for
  musicxml.py to turn into a real MusicXML file.

Durations are counted in eighth notes (ABC's L:1/8), like the rest of the app.
"""

import re
from dataclasses import dataclass, field, replace
from fractions import Fraction

import form
import options
import orchestrate

NOTATION = """\
MEASURE NOTATION — every measure is one string of space-separated tokens, in time order.
  * Note: PITCH:DURATION — C4:4, F#5:8, Bb3:2. Every part is written at SOUNDING
    (concert) pitch, transposing instruments included.
  * Chord, only where the instrument can play several notes: pitches joined by +,
    e.g. C4+E4:2.
  * Rest: r:DURATION, e.g. r:4. A whole measure of rest: R, alone.
  * PITCH is a letter A-G, optionally # ## b bb, then the octave: C4 is middle C,
    B3 the note just below it. Spell each note as it functions (F# in D major, Gb
    in Eb minor).
  * DURATION: 1 whole, 2 half, 4 quarter, 8 eighth, 16 sixteenth, 32 thirty-second;
    a trailing . makes it dotted (4. is a dotted quarter). No tuplets.
  * Marks straight after the duration, in any order: ~ tie into the next note
    (same pitches), ( slur starts, ) slur ends, > accent, ' staccato.
  * Dynamics are tokens of their own, right before the note: @pp @p @mp @mf @f @ff.
  * Percussion without pitch: L for the low sound, H for the high one (L:4, H:8,
    L+H:4).
  Examples in 4/4: "@p C4:4( D4:8 E4:8 F4:4 G4:4)"   "G4+B4:2. r:4"   "E5:2~ E5:8 D5:8 C5:4"   "R"
"""

LETTERS = "CDEFGAB"
NATURAL_PC = [0, 2, 4, 5, 7, 9, 11]
ALTERS = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}
ALTER_TEXT = {0: "", 1: "#", 2: "##", -1: "b", -2: "bb"}
DYNAMICS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff")

_PITCH = r"[A-G](?:##|bb|#|b)?-?\d"
_NOTE_RE = re.compile(rf"^((?:{_PITCH})(?:\+{_PITCH})*|r|[LH](?:\+[LH])?):(\d+)(\.?)([~()>'^]*)$")
_ONE_PITCH_RE = re.compile(r"([A-G])(##|bb|#|b)?(-?\d)")
_TEXT_RE = re.compile(r"^#(\w[\w.]*)$")

# Standard written values, longest first, in eighth notes: what any length is
# broken into for printing (a trimmed 5/8 becomes a half tied to an eighth).
VALUES = [Fraction(8), Fraction(6), Fraction(4), Fraction(3), Fraction(2), Fraction(3, 2),
          Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(3, 8), Fraction(1, 4)]


@dataclass
class Event:
    """One note, chord or rest. ``notes`` are (letter 0-6, alter, octave) at
    sounding pitch; ``hits`` are 'L' / 'H' for unpitched percussion; neither
    means a rest."""
    dur: Fraction
    notes: list = field(default_factory=list)
    hits: list = field(default_factory=list)
    dynamic: str = None
    tie: bool = False
    slur_start: bool = False
    slur_stop: bool = False
    accent: bool = False
    staccato: bool = False
    fermata: bool = False
    text: str = None

    @property
    def sounding(self) -> bool:
        return bool(self.notes or self.hits)

    @property
    def midis(self) -> list:
        return [midi_of(n) for n in self.notes]


def midi_of(note: tuple) -> int:
    letter, alter, octave = note
    return 12 * (octave + 1) + NATURAL_PC[letter] + alter


def spell(midi: int, key: str) -> tuple:
    letter, alter, octave = orchestrate.Key(key).spell(midi)
    return letter, alter, octave


def _moved(note: tuple, octaves: int) -> tuple:
    return note[0], note[1], note[2] + octaves


# ---------------------------------------------------------------------------
# Reading, with repairs
# ---------------------------------------------------------------------------

def _duration(denominator: str, dotted: str):
    try:
        value = int(denominator)
    except ValueError:
        return None
    if value not in (1, 2, 4, 8, 16, 32):
        return None
    units = Fraction(8, value)
    return units * Fraction(3, 2) if dotted else units


def read_measure(text: str, units: Fraction) -> tuple:
    """One measure string -> ([Event], repairs). Always exactly ``units`` long."""
    tokens = str(text or "").split()
    if not tokens or tokens == ["R"]:
        return [Event(units)], 0
    events, repairs, dynamic, words = [], 0, None, None
    for token in tokens:
        if token.startswith("@") and token[1:] in DYNAMICS:
            dynamic = token[1:]
            continue
        text_match = _TEXT_RE.match(token)
        if text_match:
            words = text_match.group(1)
            continue
        if token == "R":
            repairs += 1
            continue
        match = _NOTE_RE.match(token)
        dur = _duration(match.group(2), match.group(3)) if match else None
        if not match or dur is None:
            repairs += 1                                  # unreadable: dropped
            continue
        body, marks = match.group(1), match.group(4)
        event = Event(dur, dynamic=dynamic, text=words, tie="~" in marks,
                      slur_start="(" in marks, slur_stop=")" in marks,
                      accent=">" in marks, staccato="'" in marks, fermata="^" in marks)
        if body in ("L", "H", "L+H", "H+L"):
            event.hits = sorted(set(body.split("+")))
        elif body != "r":
            event.notes = [(LETTERS.index(m.group(1)), ALTERS[m.group(2) or ""], int(m.group(3)))
                           for m in _ONE_PITCH_RE.finditer(body)]
        events.append(event)
        dynamic = words = None

    # The bar must add up: pad with a rest, or cut at the barline.
    total = sum((e.dur for e in events), Fraction(0))
    if total < units:
        events.append(Event(units - total))
        repairs += 1
    elif total > units:
        kept, filled = [], Fraction(0)
        for event in events:
            if filled >= units:
                break
            if filled + event.dur > units:
                event = replace(event, dur=units - filled, tie=False)
            kept.append(event)
            filled += event.dur
        events = kept
        repairs += 1
    return events, repairs


def sounding_range(part: dict) -> tuple:
    """(low, high) MIDI the instrument can sound, or (None, None)."""
    low, _, high = str(part.get("fullRange") or "").partition("-")
    from abc_check import parse_scientific
    low, high = parse_scientific(low.strip()), parse_scientific(high.strip())
    if low is None or high is None:
        return None, None
    shift = int(part["transpose"])
    return low + shift, high + shift


PLUCKED_CHORDS = ("Harp", "Guitar", "Ukulele", "Banjo", "Mandolin")


def max_notes(part: dict) -> int:
    """How many notes this instrument can sound at once, per staff."""
    family = options.FAMILY_OF.get(part["instrument"], "")
    if family == "Keyboard" or any(name in part["instrument"] for name in PLUCKED_CHORDS):
        return 5
    if family == "Strings":
        return 2
    if family == "Percussion" and part["pitched"]:
        return 2
    return 1


def _fit(event: Event, low, high, limit: int, keep_low: bool) -> int:
    """Notes moved into range and thinned to what the instrument can play."""
    repairs = 0
    if low is not None and high - low >= 12:
        fitted = []
        for note in event.notes:
            moved = note
            while midi_of(moved) < low:
                moved = _moved(moved, 1)
            while midi_of(moved) > high:
                moved = _moved(moved, -1)
            repairs += moved != note
            fitted.append(moved)
        event.notes = fitted
    unique = sorted({midi_of(n): n for n in event.notes}.items())
    notes = [n for _, n in unique]
    if len(notes) > limit:
        repairs += 1
        if limit == 1:
            notes = [notes[0] if keep_low else notes[-1]]
        else:
            # The outer voices carry the chord: keep the top and bottom.
            notes = [notes[0], *notes[-(limit - 1):]]
    event.notes = notes
    return repairs


def link(events: list) -> int:
    """Ties only between equal pitches; slurs that open and close."""
    repairs = 0
    for i, event in enumerate(events):
        if event.tie:
            following = events[i + 1] if i + 1 < len(events) else None
            if not following or not event.sounding or \
                    sorted(following.midis) != sorted(event.midis) or following.hits != event.hits:
                event.tie = False
                repairs += 1
    open_slur = None
    for event in events:
        if event.slur_start and event.slur_stop:
            event.slur_start = event.slur_stop = False
        if event.slur_start:
            if open_slur is not None or not event.sounding:
                event.slur_start = False
                repairs += 1
            else:
                open_slur = event
        elif event.slur_stop:
            if open_slur is None:
                event.slur_stop = False
                repairs += 1
            else:
                open_slur = None
    if open_slur is not None:
        open_slur.slur_start = False
        repairs += 1
    return repairs


def read_part(measures: list, bars: int, part: dict, units: Fraction) -> tuple:
    """A part's measure strings -> ([[Event] per bar], repairs), exactly ``bars`` long."""
    texts = [str(m) for m in (measures or [])]
    repairs = abs(len(texts) - bars) if texts else 0
    texts = (texts + ["R"] * bars)[:bars]
    low, high = sounding_range(part) if part["pitched"] else (None, None)
    limit = max_notes(part)
    keep_low = low is not None and (low + high) / 2 < 55
    out = []
    for text in texts:
        events, fixed = read_measure(text, units)
        repairs += fixed
        for event in events:
            if not part["pitched"]:
                if event.notes:                          # pitches on a drum: low or high
                    event.hits = ["L"] if min(event.midis) < 64 else ["H"]
                    event.notes = []
            else:
                if event.hits:
                    event.hits = []
                    repairs += 1
                repairs += _fit(event, low, high, limit, keep_low)
        out.append(events)
    repairs += link([e for bar in out for e in bar])
    return out, repairs


# ---------------------------------------------------------------------------
# Finishing, at concert pitch
# ---------------------------------------------------------------------------

def land_on_chords(bars: list, chords: list, key: str, beat: Fraction, units: Fraction,
                   min_length: Fraction) -> int:
    """A note held ``min_length`` or more, starting on a beat, that is off the
    bar's chord moves to the nearest chord tone. Shorter dissonances — passing
    notes, a one-beat suspension — are music and stay. Returns how many moved."""
    moved = 0
    for events, symbol in zip(bars, chords):
        at = Fraction(0)
        for event in events:
            if event.notes and at % beat == 0 and event.dur >= min_length:
                pcs = form.chord_pcs_at(symbol, at, units, beat) or set()
                if pcs:
                    notes = []
                    for note in event.notes:
                        midi = midi_of(note)
                        if midi % 12 in pcs:
                            notes.append(note)
                        else:
                            notes.append(spell(form._nearest_in(midi, pcs), key))
                            moved += 1
                    event.notes = notes
            at += event.dur
    return moved


def thin(events: list, grid: Fraction) -> int:
    """Notes faster than ``grid`` merged, in place: one note per grid step.

    A run of sixteenths in an accompaniment is not orchestral writing; it is
    noise under the tune. Every group of notes shorter than ``grid`` that fills
    one grid step exactly, starting on it, becomes a single note of that length
    — the first sounding one, which sits on the step and so on the beat
    structure. Dotted figures and anything that crosses a step are left alone.
    Returns how many notes were removed.
    """
    out, at, i, removed = [], Fraction(0), 0, 0
    while i < len(events):
        event = events[i]
        if event.dur >= grid or at % grid:
            out.append(event)
            at += event.dur
            i += 1
            continue
        cell, j, filled = [], i, Fraction(0)
        while j < len(events) and events[j].dur < grid and filled + events[j].dur <= grid:
            cell.append(events[j])
            filled += events[j].dur
            j += 1
        if filled != grid or len(cell) < 2:
            out.append(event)
            at += event.dur
            i += 1
            continue
        first = next((e for e in cell if e.sounding), cell[0])
        out.append(replace(first, dur=grid, tie=cell[-1].tie,
                           slur_start=any(e.slur_start for e in cell),
                           slur_stop=any(e.slur_stop for e in cell),
                           dynamic=next((e.dynamic for e in cell if e.dynamic), None),
                           text=next((e.text for e in cell if e.text), None),
                           accent=any(e.accent for e in cell)))
        removed += len(cell) - 1
        at += grid
        i = j
    events[:] = out
    return removed


SOLO_TEXT = "solo"
RIT_TEXT = "rit."


def balance(bars: list, part_id: str, sections: list, soften: bool) -> None:
    """The tune over the accompaniment, in place (see form.balance_line).

    Where a section has a lead, everyone else is capped a step under its level,
    two under a solo; each entry carries a dynamic; the soloist's first note is
    marked solo. An accompanying keyboard is instead lowered two steps throughout.
    """
    if soften:
        for event in (e for bar in bars for e in bar):
            if event.dynamic:
                event.dynamic = form.SOFTER.get(event.dynamic, event.dynamic)
        return
    current = None
    for s in sections:
        level, cap = form.section_level(s, part_id)
        entered = False
        for index in range(s["start"] - 1, s["end"]):
            events = bars[index]
            for event in events:
                if event.dynamic and cap and form._LEVEL_OF.get(event.dynamic, 0) > \
                        form._LEVEL_OF[cap]:
                    event.dynamic = cap
            first = next((e for e in events if e.sounding), None)
            if first and not entered:
                entered = True
                if not any(e.dynamic for e in events) and level != current:
                    first.dynamic = level
                if s.get("texture") == "solo" and s.get("lead") == part_id:
                    first.text = SOLO_TEXT
            for event in events:
                if event.dynamic:
                    current = event.dynamic


def finish(bars: list, units: Fraction, rit: bool = True) -> None:
    """The last bar becomes its first sounding note or chord, held the whole bar
    under a fermata, with its dynamic; 'rit.' over the bar before. In place."""
    if not bars:
        return
    last = bars[-1]
    first = next((e for e in last if e.sounding), None)
    dynamic = next((e.dynamic for e in last if e.dynamic), None)
    if first:
        bars[-1] = [Event(units, notes=list(first.notes), hits=list(first.hits),
                          dynamic=dynamic, fermata=True)]
    else:
        bars[-1] = [Event(units)]
    if len(bars) > 1:
        before = bars[-2]
        if before and before[-1].tie and not (first and sorted(first.midis) ==
                                              sorted(before[-1].midis)):
            before[-1].tie = False
        if rit and before:
            before[0].text = before[0].text or RIT_TEXT


# ---------------------------------------------------------------------------
# Transposing and writing ABC
# ---------------------------------------------------------------------------

def to_written(note: tuple, transpose: int, written_key: str) -> tuple:
    """A sounding note -> the note the player reads, spelled by interval."""
    if not transpose:
        return note
    target = midi_of(note) - transpose
    steps = round(-transpose * 7 / 12)
    absolute = note[2] * 7 + note[0] + steps
    letter, octave = absolute % 7, absolute // 7
    alter = target - (12 * (octave + 1) + NATURAL_PC[letter])
    if abs(alter) > 2:
        return spell(target, written_key)
    return letter, alter, octave


def _pieces(dur: Fraction) -> list:
    """A length as standard written values, longest first."""
    out, left = [], dur
    while left > 0:
        value = next((v for v in VALUES if v <= left), None)
        if value is None:
            out.append(left)
            break
        out.append(value)
        left -= value
    return out


def _abc_pitch(note: tuple, signature: list, state: dict) -> str:
    letter, alter, octave = note
    current = state.get((letter, octave), signature[letter])
    sign = ""
    if alter != current:
        sign = {2: "^^", 1: "^", 0: "=", -1: "_", -2: "__"}[alter]
        state[(letter, octave)] = alter
    name = LETTERS[letter]
    if octave >= 5:
        return sign + name.lower() + "'" * (octave - 5)
    return sign + name + "," * (4 - octave)


def _abc_event(event: Event, part: dict, key: str, signature: list, state: dict) -> str:
    head = ""
    if event.text:
        head += f'"^{event.text}" '
    if event.dynamic:
        head += f"!{event.dynamic}!"
    if event.fermata:
        head += "!fermata!"
    if event.accent:
        head += "!accent!"
    pieces = _pieces(event.dur)
    if not event.sounding:
        return head + " ".join("z" + orchestrate._duration(p) for p in pieces)
    if event.hits:
        body = "".join({"L": "c", "H": "e"}[h] for h in event.hits)
    else:
        written = [to_written(n, int(part["transpose"]), key) for n in event.notes]
        body = "".join(_abc_pitch(n, signature, state) for n in written)
    chord = len(event.hits) + len(event.notes) > 1
    tokens = []
    for i, piece in enumerate(pieces):
        token = (f"[{body}]" if chord else body) + orchestrate._duration(piece)
        if i < len(pieces) - 1 or event.tie:
            token += "-"
        tokens.append(token)
    first = ("(" if event.slur_start else "") + ("." if event.staccato else "") + tokens[0]
    tokens[0] = head + first
    if event.slur_stop:
        tokens[-1] += ")"
    return " ".join(tokens)


def render_bar(events: list, part: dict, key: str) -> str:
    signature = orchestrate.Key(key).signature
    state = {}
    return " ".join(_abc_event(e, part, key, signature, state) for e in events)


def render_abc(bars: list, part: dict, sections: list) -> tuple:
    """A whole staff -> (ABC with [K:] changes and section double bars, home key)."""
    home = form.written_key_for(part, sections[0]["key"])
    current, text = home, ""
    for index, s in enumerate(sections):
        key = form.written_key_for(part, s["key"])
        rendered = [render_bar(bars[m], part, key) for m in range(s["start"] - 1, s["end"])]
        if key != current:
            rendered[0] = f"[K:{key}] " + rendered[0]
            current = key
        text += ("" if index == 0 else " || ") + " | ".join(rendered)
    return text + " |]", home


# ---------------------------------------------------------------------------
# Writing the compact form back out (what is stored)
# ---------------------------------------------------------------------------

def _compact_value(value: Fraction) -> str:
    for denominator in (1, 2, 4, 8, 16, 32):
        plain = Fraction(8, denominator)
        if value == plain:
            return str(denominator)
        if value == plain * Fraction(3, 2):
            return f"{denominator}."
    return None


def to_compact(events: list) -> str:
    """[Event] -> a measure string read_measure reads back exactly."""
    if len(events) == 1 and not events[0].sounding and not events[0].text \
            and not events[0].dynamic:
        return "R"
    tokens = []
    for event in events:
        if event.text:
            tokens.append(f"#{event.text}")
        if event.dynamic:
            tokens.append(f"@{event.dynamic}")
        if event.hits:
            body = "+".join(event.hits)
        elif event.notes:
            body = "+".join(f"{LETTERS[l]}{ALTER_TEXT[a]}{o}" for l, a, o in event.notes)
        else:
            body = "r"
        pieces = _pieces(event.dur)
        for i, piece in enumerate(pieces):
            marks = ""
            if i == 0 and event.slur_start:
                marks += "("
            if i == 0 and event.accent:
                marks += ">"
            if i == 0 and event.staccato:
                marks += "'"
            if i == 0 and event.fermata:
                marks += "^"
            if event.sounding and (i < len(pieces) - 1 or event.tie):
                marks += "~"
            if i == len(pieces) - 1 and event.slur_stop:
                marks += ")"
            tokens.append(f"{body}:{_compact_value(piece) or '32'}{marks}")
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Moving a written line: into another key, into an instrument's register
# ---------------------------------------------------------------------------

def tonic_shift(from_key: str, to_key: str) -> int:
    """Semitones from one key's tonic to another's, the short way (-6 to +5)."""
    a = orchestrate.PITCH_OF_KEY.get(orchestrate._split_key(from_key)[0], 0)
    b = orchestrate.PITCH_OF_KEY.get(orchestrate._split_key(to_key)[0], 0)
    return (b - a + 6) % 12 - 6


def transposed(bars: list, shift: int, key: str) -> list:
    """Copies of ``bars`` moved by ``shift`` semitones, spelled in ``key``."""
    out = []
    for events in bars:
        moved = []
        for event in events:
            copy = replace(event, notes=[spell(midi_of(n) + shift, key) for n in event.notes],
                           hits=list(event.hits))
            moved.append(copy)
        out.append(moved)
    return out


# Where each instrument's voice sings, at sounding pitch: the register a tune
# is written in, not the whole compass.
SINGING = {
    "Violin": ("G4", "D6"), "Viola": ("D4", "A5"), "Cello": ("D3", "A4"),
    "Double bass": ("E2", "D4"), "Flute": ("G4", "G6"), "Piccolo": ("G5", "G7"),
    "Oboe": ("D4", "D6"), "English horn": ("C4", "G5"), "Clarinet": ("A3", "D6"),
    "Bass clarinet": ("D2", "G4"), "Bassoon": ("D3", "D5"), "French horn": ("C4", "F5"),
    "Trumpet": ("F4", "C6"), "Trombone": ("A2", "F4"), "Euphonium": ("A2", "F4"),
    "Tuba": ("E1", "C4"), "Alto saxophone": ("Db4", "Ab5"), "Tenor saxophone": ("Ab2", "Eb5"),
    "Soprano": ("D4", "A5"), "Alto (voice)": ("A3", "D5"), "Tenor (voice)": ("D3", "A4"),
    "Bass (voice)": ("G2", "D4"), "Harp": ("C4", "C6"), "Piano": ("C4", "C6"),
    "Marimba": ("C4", "C6"), "Xylophone": ("C5", "C7"), "Vibraphone": ("F3", "F6"),
    "Recorder": ("C5", "C7"),
}


def singing_range(part: dict) -> tuple:
    """(low, high) MIDI where the instrument sings best. (None, None) if unpitched."""
    low, high = sounding_range(part)
    if low is None:
        return None, None
    from abc_check import parse_scientific
    known = SINGING.get(part["instrument"])
    if known:
        a, b = parse_scientific(known[0]), parse_scientific(known[1])
        if a is not None and b is not None:
            return max(low, a), min(high, b)
    easy_low, _, easy_high = str(part.get("easyRange") or "").partition("-")
    shift = int(part["transpose"])
    a, b = parse_scientific(easy_low.strip()), parse_scientific(easy_high.strip())
    if a is None or b is None:
        a, b = low, high
    else:
        a, b = a + shift, b + shift
    return max(low, (a + b) // 2 - 7), min(high, b + 4)


def fit_register(bars: list, part: dict) -> list:
    """A tune moved by whole octaves to sit where the instrument sings.

    Every note moves together, so the melody keeps its shape — unlike fitting
    note by note, which folds a tune back on itself at the edges. Of the
    octaves that keep it inside the instrument's range, the one whose middle is
    nearest the middle of its singing register wins.
    """
    low, high = sounding_range(part)
    sing_low, sing_high = singing_range(part)
    midis = sorted(m for events in bars for e in events for m in e.midis)
    if low is None or not midis:
        return bars
    centre, target = midis[len(midis) // 2], (sing_low + sing_high) / 2

    def cost(octaves):
        moved = [m + 12 * octaves for m in midis]
        outside = sum(1 for m in moved if not low <= m <= high)
        return (outside, abs(centre + 12 * octaves - target))

    octaves = min(range(-3, 4), key=cost)
    if not octaves:
        return bars
    return [[replace(e, notes=[_moved(n, octaves) for n in e.notes]) for e in events]
            for events in bars]
