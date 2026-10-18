"""Score generation — turns a compose request into engravable notation.

There is no OpenAI endpoint that returns sheet music, so this module does it in
two halves:

1. **The model writes notes.** A chat completion with Structured Outputs returns
   one JSON object: shared score metadata plus, for each part, a block of ABC
   notation containing only the music. ABC is used rather than MusicXML because
   it is compact enough for a model to keep correct across a dozen staves, and
   the browser can engrave it directly.
2. **This module builds the score.** Headers, voice declarations, staff
   grouping, clefs, per-part key signatures and MIDI programs are assembled here
   in Python, deterministically. The model never writes a header, so it can't
   put the ensemble on two different time signatures or forget a clef.

The model's output is compact by design. It writes each musical idea once — a
few short four-voice phrases — plus a form saying what happens to them, and
form.py expands that into the whole piece. Length costs the model almost
nothing, and its attention goes on the bars that are actually new.

The other half of accuracy is the brief: every part is described to the model
with its clef, its transposition, and the written range that level of player can
actually reach, so a Bb trumpet part comes back written a tone high and a
beginner flute part stays under G5.
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

import abc_check
import form
import options
import orchestrate

# Chosen by measurement rather than by version number. On one 15-part brief,
# mean distinct pitches per part: gpt-6.1-sol 65.6, gpt-6-astra 55.5,
# gpt-5.6-sol 55.7, gpt-6-sol 41.3, gpt-6-luna 26.6 — and luna marked dynamics
# on only 5 parts of 15. Variety per line is the closest cheap proxy we have for
# "does this sound written or generated". See README.
DEFAULT_MODEL = "gpt-6.1-sol"

# The unit note length every part is written in. Fixing it here means one
# duration convention across the score: 1 = eighth, 2 = quarter, 4 = half.
UNIT_NOTE_LENGTH = "1/8"

# Output budget. There is no cap on how many instruments may be asked for, so
# the budget cannot be a constant: every staff is another full line of ABC the
# model has to write out, and a fixed ceiling would silently truncate a large
# ensemble mid-score. Scale it with the roster instead, and let a deployment
# lower the ceiling for a model with a smaller output window.
# At most this many parts are written by the model; the rest are derived from
# the skeleton exactly. Three is enough for a tune, a counter-line and a solo.
MAX_DECORATED = 3

BASE_OUTPUT_TOKENS = 8000
OUTPUT_TOKENS_PER_STAFF = 900
MAX_OUTPUT_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "48000"))

# Budgets for the two passes.
#
# The configured model is a reasoning model, so this ceiling covers the thinking
# as well as the reply — a generous number is not the same as a generous answer,
# which is why one giant request got truncated no matter how high it was set.
# Keeping every reply small is what actually fixes it.
# Measured on this model: a four-part score spent 8,148 tokens thinking and only
# 3,240 writing. Reasoning is the larger half and it does not shrink as the reply
# does, so every call needs room for both.
PLAN_TOKENS = 20000           # the plan is prose and a chord grid: small reply, hard thinking
BATCH_BUDGET = 5000           # visible notation expected from one batch
REASONING_HEADROOM = 14000    # thinking, which shares the same allowance
MAX_PARTS_PER_BATCH = 1      # one part per call: the model writes slowly, so
MAX_CONCURRENT_BATCHES = 6   # many short replies at once beat one long one

# Wall-clock budget for one generation. A piece must be finished inside five
# minutes, so every call gets a timeout from what is left, and the optional
# steps — retrying a batch, the correction round — are skipped when there is not
# time for them. Skipping costs little: a part that misses out plays its exact
# chorale line, which is plainer but always in time and in tune.
#
# The critical path is: outline -> the phrases' chorales (all at once) -> the
# decorated parts (all at once) -> at most one correction round. Each step's
# ceiling leaves room for the steps after it.
TIME_BUDGET = float(os.environ.get("COMPOSE_TIME_BUDGET", "285"))
PLAN_TIMEOUT = 95            # the outline: chords and form, no notes
CHORALE_TIMEOUT = 160        # one phrase's four voices; they run in parallel
PARTS_RESERVE = 75           # kept back from the chorales for the parts pass
MIN_CALL_SECONDS = 40        # below this, a call is not worth starting
PLAN_EFFORT = "low"          # an outline is decisions, not counting
CHORALE_EFFORT = "low"       # measured: medium took 90-110s+ per phrase; Python enforces the chords
PARTS_EFFORT = "low"         # decorating a given line is not hard thinking


def _token_budget(roster: list) -> int:
    """Room to write every staff in ``roster``, within the model's ceiling.

    Grand-staff instruments count twice — a piano part is two written lines.
    """
    staves = sum(part["staves"] for part in roster)
    return min(MAX_OUTPUT_TOKENS, BASE_OUTPUT_TOKENS + OUTPUT_TOKENS_PER_STAFF * staves)

_log = logging.getLogger(__name__)
_client = None


class ComposerError(Exception):
    """Something went wrong that the user should be told about verbatim."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ComposerError(
                "The score generator isn't configured yet: set OPENAI_API_KEY in "
                "backend/.env and restart the server.",
                status=503,
            )
        # Every call passes its own timeout from the generation's time budget.
        # No silent SDK retries: a retried timeout would quietly double the
        # wait, and composer.py already retries a failed batch when there is
        # time for it.
        _client = OpenAI(api_key=api_key, timeout=PLAN_TIMEOUT, max_retries=0)
    return _client


# ---------------------------------------------------------------------------
# Roster — turning "Violin x3" into Violin I, II, III
# ---------------------------------------------------------------------------

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"]

# Short names printed to the left of every system after the first.
SHORT_NAMES = {
    "Violin": "Vln", "Viola": "Vla", "Cello": "Vc", "Double bass": "Cb",
    "Harp": "Hp", "Acoustic guitar": "Gtr", "Electric guitar": "E.Gtr",
    "Bass guitar": "B.Gtr", "Ukulele": "Uke", "Flute": "Fl", "Piccolo": "Picc",
    "Clarinet": "Cl", "Bass clarinet": "B.Cl", "Oboe": "Ob",
    "English horn": "E.Hn", "Bassoon": "Bsn", "Alto saxophone": "A.Sax",
    "Tenor saxophone": "T.Sax", "Baritone saxophone": "B.Sax",
    "Recorder": "Rec", "Trumpet": "Tpt", "French horn": "Hn",
    "Trombone": "Tbn", "Euphonium": "Euph", "Tuba": "Tba", "Timpani": "Timp",
    "Marimba": "Mar", "Xylophone": "Xyl", "Vibraphone": "Vib",
    "Snare drum": "S.D.", "Drum set": "Drs", "Hand percussion": "Perc",
    "Piano": "Pno", "Organ": "Org", "Harpsichord": "Hpschd",
    "Synthesizer": "Synth", "Accordion": "Acc", "Soprano": "S",
    "Alto (voice)": "A", "Tenor (voice)": "T", "Bass (voice)": "B",
}

# Written-to-sounding offsets, spelled out for the model. Getting this wrong is
# the single most common way AI-generated parts come back unplayable.
TRANSPOSITION_TEXT = {
    0:   "concert pitch — write exactly what should sound",
    12:  "sounds one octave HIGHER than written — write it an octave down",
    -12: "sounds one octave LOWER than written — write it an octave up",
    -2:  "in Bb — write a MAJOR 2ND ABOVE concert pitch",
    -7:  "in F — write a PERFECT 5TH ABOVE concert pitch",
    -9:  "in Eb — write a MAJOR 6TH ABOVE concert pitch",
    -14: "in Bb — write a MAJOR 9TH ABOVE concert pitch (octave + tone)",
    -21: "in Eb — write an OCTAVE + MAJOR 6TH ABOVE concert pitch",
}


def build_roster(instruments: list) -> list:
    """Expand ``[{name, parts}]`` into one entry per printed part.

    Three violins become Violin I, II and III, each with its own voice id, so
    the model writes three independent lines rather than one line played three
    times. Grand-staff instruments get two voice ids, braced together later.
    """
    roster = []
    for entry in instruments:
        name = entry["name"]
        spec = options.INSTRUMENT_SPECS[name]
        count = int(entry.get("parts", 1))

        for index in range(1, count + 1):
            label = f"{name} {ROMAN[index]}" if count > 1 else name
            short = SHORT_NAMES.get(name, name[:4])
            position = len(roster) + 1

            roster.append({
                "partId": f"P{position}",
                "instrument": name,
                "label": label,
                "shortLabel": f"{short} {ROMAN[index]}" if count > 1 else short,
                "clef": spec["clef"],
                "transpose": spec["transpose"],
                "easyRange": spec["easy"],
                "fullRange": spec["full"],
                "staves": spec["staves"],
                "pitched": spec["pitched"],
                "midi": spec["midi"],
            })
    return roster


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a professional composer and arranger who writes for school, community \
and professional ensembles, and who knows exactly what players at each stage of \
training can and cannot play.

You write music in ABC notation. Follow these rules without exception.

PITCH
  C D E F G A B  = the octave from middle C upward   (C = middle C = C4)
  c d e f g a b  = the octave above that             (c = C5)
  c' d' ...      = one octave higher again           (c' = C6)
  C, D, ...      = one octave below middle C         (C, = C3)
  C,, ...        = two octaves below middle C        (C,, = C2)
  Accidentals go BEFORE the note: ^C sharp, _B flat, =F natural, ^^C double sharp.
  As in standard notation an accidental holds for the rest of that measure at
  that octave, and is cancelled by the next barline. Restate it in the next
  measure if you still need it.

DURATION — the unit note length is L:1/8, so a plain letter is an EIGHTH note.
  C   = eighth      C2  = quarter     C3  = dotted quarter
  C4  = half        C6  = dotted half C8  = whole
  C/2 = sixteenth   C/4 = thirty-second
  Rests use the same numbers: z = eighth rest, z2 = quarter rest, z8 = whole-bar
  rest in 4/4. A bar of rest in 3/4 is z6.

STRUCTURE
  | barline    || double bar    |] final barline    |: :| repeats
  [CEG]2 = a chord lasting a quarter note.
  C-C = tie.  (CDE) = slur.  .C = staccato.
  !p! !mp! !mf! !f! !ff! before a note set a dynamic.
  !crescendo(! ... !crescendo)! and !diminuendo(! ... !diminuendo)! for hairpins.
  !accent! !tenuto! !fermata! !trill! !upbow! !downbow! as needed.

HARD REQUIREMENTS
  * Output ONLY music in each part's "abc" field. Never write X:, T:, M:, L:,
    K:, Q:, V: or %% lines — those are added for you.
  * Every part must contain EXACTLY the same number of measures, separated by |,
    and every measure must add up to the given time signature. Count them.
  * A part that is resting still needs its measures written out as full-bar
    rests. Never leave a part short.
  * Write TRANSPOSED (written) pitch for transposing instruments, and report the
    matching written key in that part's "abcKey".
  * Keep every note inside the written range you are given for that part.
  * Unpitched percussion is notated on a one-line rhythm staff: use only the
    note letters c and e for two different sounds, plus rests.

MUSICAL REQUIREMENTS
  * Write one real, singable theme and develop it. Do not produce scales,
    arpeggio exercises or aimless noodling.
  * The parts must fit together: check that simultaneous notes form the harmony
    you intend, and that the lowest-sounding part carries a sensible bass line.
  * Respect the difficulty brief exactly, and in BOTH directions. Writing above
    a group's level hands them a piece they cannot play. Writing below it hands
    them a piece that wastes their rehearsal and insults their training. Neither
    is acceptable, and the second is the one that happens by accident: when in
    doubt you will reach for even rhythms and plain diatonic harmony, and for an
    advanced group that is the failure. Meet the demands listed in the brief.
"""


# What the music has to *contain* at each level.
#
# The level guidance in options.py describes the ceiling — how hard the writing
# is allowed to get. On its own that reads as permission, and permission is not
# enough: told only that anything is allowed, the model still writes the safe,
# plain thing. These are the floor, stated as requirements.
LEVEL_DEMANDS = {
    "beginner": [
        "A tune the group will actually enjoy playing. Simple is not the same",
        "as dull: the melody must have a shape and go somewhere, the harmony",
        "must move, and the accompanying parts must have something to do",
        "besides whole notes. At least one dynamic contrast and one moment",
        "where a different section takes the tune.",
    ],
    "intermediate": [
        "This group has played for up to three years and is bored by whole",
        "notes. It MUST contain: sustained sixteenth-note movement somewhere,",
        "dotted rhythms and syncopation, at least one accidental outside the",
        "key, at least two sections with genuinely different material rather",
        "than the same line doubled, a short exposed entry for at least two",
        "different parts, and dynamics and articulation marked throughout.",
    ],
    "higher-intermediate": [
        "Do not mistake this for an intermediate group. These are advanced",
        "players with four to seven years behind them; they play real",
        "repertoire and will be insulted by a simple piece. It MUST contain:",
        "  - chromatic harmony and a modulation to a genuinely different key,",
        "    not a piece that sits in one diatonic key throughout;",
        "  - fast sixteenth-note passagework that has to be practised, built",
        "    from chord tones and stepwise passing notes;",
        "  - sustained cross-rhythm, hemiola and syncopation that does not",
        "    simply fall on the offbeat (the meter itself stays fixed);",
        "  - four genuinely independent lines inside the chorale — each voice",
        "    with its own rhythm and contour, every vertical still a real chord;",
        "  - an extended exposed solo line for at least one part;",
        "  - the full written range, including the top of it;",
        "  - dynamics from pp to ff with hairpins, accents and articulation",
        "    marked on every part.",
        "A clean, safe, four-square diatonic piece is a FAILED response here.",
    ],
    "pro": [
        "These are players who do this for a living, and this piece has to be",
        "worth their rehearsal time. It MUST contain, at minimum:",
        "  - harmony beyond plain diatonic triads: secondary dominants,",
        "    borrowed chords, extended sonorities, and real modulations — every",
        "    one of them named in the chord grid, so the whole ensemble plays it;",
        "  - rhythm beyond straight quarters and eighths: syncopation,",
        "    cross-rhythm and hemiola (the meter itself stays fixed, so a large",
        "    ensemble stays together);",
        "  - independent counterpoint inside the chorale — inner voices and bass",
        "    with their own rhythm and contour, every vertical a real chord;",
        "  - technically demanding passages — fast runs, wide leaps, the",
        "    extremes of the range — built on the chords, never against them;",
        "  - a full dynamic map from pp to ff with hairpins and accents marked;",
        "  - development of the theme, not repetition of it.",
        "Even, four-square diatonic writing is a FAILED response at this level,",
        "however clean it is.",
    ],
}


def _range_line(part, level_value):
    if not part["pitched"]:
        return "unpitched — rhythm staff"
    if level_value in ("beginner", "intermediate"):
        text = f"stay within {part['easyRange']} (written)"
        if level_value == "intermediate":
            text += f", brief excursions toward {part['fullRange']} are fine"
        return text
    return f"{part['fullRange']} (written), full range available"


def build_user_prompt(request: dict, roster: list) -> str:
    group = options.ENSEMBLE_GROUPS_BY_VALUE[request["ensembleGroup"]]
    level = options.LEVELS_BY_VALUE[request["level"]]
    mood = options.MOODS_BY_VALUE[request["mood"]]
    conductor = request["conductor"]

    lines = ["ENSEMBLE — write one part for each line below.", ""]
    for part in roster:
        transposition = TRANSPOSITION_TEXT.get(
            part["transpose"], f"sounds {part['transpose']} semitones from written")
        detail = [
            f"clef {part['clef']}",
            transposition,
            _range_line(part, level["value"]),
        ]
        if part["staves"] == 2:
            detail.append("GRAND STAFF: put the right hand in \"abc\" and the left "
                          "hand in \"abcLower\", both with the same measure count")
        lines.append(f"  [{part['partId']}] {part['label']} — " + "; ".join(detail))

    same_instrument = {}
    for part in roster:
        same_instrument.setdefault(part["instrument"], []).append(part["label"])
    divided = {name: labels for name, labels in same_instrument.items() if len(labels) > 1}
    if divided:
        lines += ["", "DIVIDED SECTIONS — these must be genuinely different lines, "
                      "not the same music copied out:"]
        for name, labels in divided.items():
            lines.append(f"  {name}: {', '.join(labels)} — give the upper part the "
                         "melody more often and the lower parts harmony, "
                         "counter-melody or rhythmic support.")

    # Total length, built by the form from a few phrases (see LENGTHS). The
    # model writes the phrases once, so this no longer costs it bar by bar.
    measures = LENGTHS[level["value"]][0]

    lines += [
        "",
        f"WHO IS PLAYING: {group['label']}.",
        f"  {group['guidance']}",
        "",
        f"PLAYING LEVEL: {level['label']} ({level['years']} of experience).",
        f"  {level['guidance']}",
        "",
        "THIS LEVEL DEMANDS — the writing is wrong if these are missing:",
        *[f"  {line}" for line in LEVEL_DEMANDS[level["value"]]],
        "",
        f"MOOD — this is the theme of the piece: {mood['label'].upper()}.",
        f"  {mood['guidance']}",
        "  The mood is not decoration. Choose the key, tempo, register,",
        "  articulation and harmony so that a listener would name this mood",
        "  without being told it.",
        "",
        f"LENGTH: {measures} measures in total, built from a few phrases.",
        "",
    ]

    if conductor:
        lines += [
            "CONDUCTOR: yes, this ensemble has a conductor.",
            "  Write for a conducted ensemble: you may use tempo changes, fermatas,",
            "  ritardando and accelerando, rubato and entries that need cueing.",
            "  The conductor gets material of their own later — an interpretation",
            "  the podium — an interpretation summary, a tempo map, the cues they",
            "  must give and to whom, rehearsal marks with what to fix at each, and",
            "  concrete beat-pattern and gesture guidance. The conductor's score is",
            "  the full score, so every cue must name a real measure number and a",
            "  real part from the list above.",
        ]
    else:
        lines += [
            "CONDUCTOR: no conductor — the players lead themselves.",
            "  Hold one steady tempo throughout. No rubato, no fermatas, no tempo",
            "  changes that would need someone to co-ordinate them. Give clear",
            "  rhythmic cues inside the parts so entries can be felt, and make sure",
            "  at least one part is always audible enough to keep everyone together.",
        ]

    if request.get("notes"):
        lines += ["", "ALSO REQUESTED BY THE USER (honour this unless it contradicts "
                      "the level or range limits above):", f"  {request['notes']}"]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

def _obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


PART_SCHEMA = _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "abcKey": {"type": "string",
               "description": "WRITTEN key for this part as an ABC key field: "
                              "'G', 'Bb', 'F#m', 'Amin', 'C'."},
    "abc": {"type": "string",
            "description": "Music only, no header fields. Measures separated by |."},
    "abcLower": {"type": ["string", "null"],
                 "description": "Left-hand / lower staff for grand-staff instruments, "
                                "otherwise null."},
    "performanceNote": {"type": "string",
                        "description": "One or two sentences for this player."},
})


CONDUCTOR_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "overview": {"type": "string"},
        "gestures": {"type": "string",
                     "description": "Beat pattern, subdivision, left-hand use, "
                                    "preparatory beats."},
        "tempoMap": {"type": "array", "items": _obj({
            "measure": {"type": "integer"},
            "marking": {"type": "string"},
            "bpm": {"type": "integer"},
        })},
        "cues": {"type": "array", "items": _obj({
            "measure": {"type": "integer"},
            "part": {"type": "string"},
            "text": {"type": "string"},
        })},
        "rehearsalMarks": {"type": "array", "items": _obj({
            "measure": {"type": "integer"},
            "label": {"type": "string"},
            "note": {"type": "string"},
        })},
    },
    "required": ["overview", "gestures", "tempoMap", "cues", "rehearsalMarks"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Two passes: plan the piece, then write the few lines that need a composer
# ---------------------------------------------------------------------------
#
# Asking for a whole orchestral score in one reply does not work. The model has
# to hold every decision and every stave in one answer, and the reply is cut off
# partway through — worse with every instrument added, which is exactly when the
# piece matters most.
#
# So it goes in two passes. First a PLAN — and the plan is compact by design: a
# few PHRASES, each a short four-voice chorale with a chord per bar, and a FORM
# that says what happens to them section by section (see form.py). Repetition,
# transposition, who has the tune and how thick the texture is are all expanded
# from that in Python, exactly. The model writes each idea once, so a piece can
# be long without the model writing long.
#
# Then the PARTS. Most are derived from the chorale arithmetically and never
# reach the model at all. The few that carry the tune are decorated by the
# model — again one phrase at a time, not bar by bar through the whole piece.

# The outline names each phrase and fixes its harmony; the four voices are
# written afterwards, every phrase in its own call, all at once.
PHRASE_SCHEMA = _obj({
    "id": {"type": "string", "description": "Short name the form refers to: 'A', 'B', "
                                            "'C', 'Coda'."},
    "chords": {"type": "array", "items": {"type": "string"},
               "description": "Concert-pitch chord for EACH bar of the phrase, in order: "
                              "'G', 'D7/F#', 'Em', 'Cmaj7'. Its length is the phrase's "
                              "bar count."},
    "character": {"type": "string",
                  "description": "One sentence: the melody's shape and rhythm and how it "
                                 "ends (half cadence, full cadence)."},
})

CHORALE_SCHEMA = _obj({
    "soprano": {"type": "string", "description": "ABC, concert pitch, exactly one bar per chord."},
    "alto": {"type": "string", "description": "ABC, concert pitch, same bar count."},
    "tenor": {"type": "string", "description": "ABC, concert pitch, same bar count."},
    "bass": {"type": "string", "description": "ABC, concert pitch, same bar count."},
})

FORM_ENTRY_SCHEMA = _obj({
    "label": {"type": "string", "description": "Section name printed as a rehearsal "
                                               "letter: 'A', 'A2', 'B', 'Coda'."},
    "phrase": {"type": "string", "description": "The id of the phrase played here."},
    "fromBar": {"type": ["integer", "null"],
                "description": "First bar of the phrase to use (1-based), or null for "
                               "the whole phrase. Use a fragment for development, "
                               "transitions and codas."},
    "toBar": {"type": ["integer", "null"],
              "description": "Last bar of the phrase to use, or null for the end."},
    "transpose": {"type": "integer",
                  "description": "Semitones from the home key, -7 to 7. 0 = home. "
                                 "+2, +5, -3 for a sequence or a new key area."},
    "dynamic": {"type": "string", "enum": ["pp", "p", "mp", "mf", "f", "ff"]},
    "shape": {"type": "string", "enum": ["steady", "crescendo", "diminuendo"]},
    "texture": {"type": "string", "enum": ["tutti", "reduced", "solo"],
                "description": "'tutti' everyone; 'reduced' one player per voice, "
                               "doublings drop out; 'solo' the lead alone over a "
                               "held bass."},
    "lead": {"type": ["string", "null"],
             "description": "partId of the player carrying the melody here. They play "
                            "the soprano line, whatever voice they normally have."},
    "description": {"type": "string", "description": "Five words at most: 'theme in the horns'."},
})

PART_PLAN_SCHEMA = _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "voice": {"type": "string", "enum": ["soprano", "alto", "tenor", "bass"],
              "description": "Which line of the four-voice chorale this player "
                             "realises. Everyone is assigned one, and that is what "
                             "makes a dozen independently-written parts agree."},
    "treatment": {"type": "string", "enum": ["plain", "sustained", "decorated"],
                  "description": "How freely this player treats its voice. "
                                 "'plain' = the voice's own notes and rhythm, the "
                                 "backbone of the ensemble. 'sustained' = the same "
                                 "notes held long underneath. 'decorated' = free "
                                 "rhythm and passing notes around them. Most of an "
                                 "orchestra plays plain or sustained; only the few "
                                 "players carrying the tune are decorated."},
    "role": {"type": "string",
             "description": "One sentence for the player: what they do across the piece."},
})

PLAN_SCHEMA = _obj({
    "title": {"type": "string"},
    "tempoText": {"type": "string", "description": "Italian tempo marking."},
    "tempoBpm": {"type": "integer", "description": "Beats per minute, 30-240."},
    "beatUnit": {"type": "string", "enum": ["1/4", "1/2", "3/8", "1/8"]},
    "timeSignature": {"type": "string", "description": "e.g. '4/4', '3/4', '6/8'."},
    "concertKey": {"type": "string",
                   "description": "The sounding home key, named in two or three words "
                                  "and nothing more: 'G major', 'D minor'. It is printed "
                                  "as a label on the score."},
    "phrases": {"type": "array", "items": PHRASE_SCHEMA,
                "description": "The distinct musical ideas, each planned ONCE: its chord "
                               "per bar and its character. Two to four of them."},
    "form": {"type": "array", "items": FORM_ENTRY_SCHEMA,
             "description": "The piece, section by section, in order. Measures are "
                            "numbered by adding up the sections' bars."},
})

# One batch of model-written parts: each one a line per phrase, not per bar of
# the whole piece.
WRITTEN_PHRASE_SCHEMA = _obj({
    "phrase": {"type": "string", "description": "The phrase id."},
    "abc": {"type": "string", "description": "This part's line for that phrase. "
                                             "Exactly the phrase's bar count."},
    "abcLower": {"type": ["string", "null"],
                 "description": "Left hand for grand-staff instruments, otherwise null."},
})

WRITTEN_PART_SCHEMA = _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "phrases": {"type": "array", "items": WRITTEN_PHRASE_SCHEMA,
                "description": "One entry per phrase, every phrase."},
    "performanceNote": {"type": "string", "description": "One or two sentences for this player."},
})

BATCH_SCHEMA = _obj({"parts": {"type": "array", "items": WRITTEN_PART_SCHEMA}})

# Words for the players and the podium. Written alongside the chorales, not
# before them, so it never adds to the wait.
NOTES_SCHEMA = _obj({
    "programNote": {"type": "string", "description": "2-4 sentences for the players."},
    "rehearsalTips": {"type": "string", "description": "2-3 sentences."},
    "conductor": CONDUCTOR_SCHEMA,
})


# ---------------------------------------------------------------------------
# ABC assembly
# ---------------------------------------------------------------------------

INLINE_FIELD_RE = re.compile(r"\[[A-Za-z]:[^\]]*\]")
# Header fields the model was told not to write. Stripped rather than rejected —
# a stray "K:G" line would otherwise corrupt every staff below it.
STRAY_HEADER_RE = re.compile(r"^(?:[A-Za-z]:.*|%%.*)$", re.MULTILINE)


def _clean(music: str) -> str:
    if not music:
        return ""
    return "\n".join(
        line.rstrip() for line in STRAY_HEADER_RE.sub("", music).splitlines() if line.strip()
    )


def _voice_ids(part) -> list:
    if part["staves"] == 2:
        return [f"{part['partId']}a", f"{part['partId']}b"]
    return [part["partId"]]


def _score_directive(roster) -> str:
    groups = []
    for part in roster:
        ids = _voice_ids(part)
        groups.append("{" + " ".join(ids) + "}" if len(ids) > 1 else ids[0])
    return "%%score " + " ".join(groups)


def _voice_declarations(roster) -> list:
    lines = []
    for part in roster:
        ids = _voice_ids(part)
        # The name sits on the first voice only; on a grand staff it belongs to
        # the brace, not to the upper stave.
        staff = f'V:{ids[0]} clef={part["clef"]}'
        if not part["pitched"]:
            staff += " stafflines=1"
        lines.append(f'{staff} name="{part["label"]}" sname="{part["shortLabel"]}"')
        if len(ids) > 1:
            lines.append(f"V:{ids[1]} clef=bass")
    return lines


def assemble_abc(score: dict, roster: list, only_part_id: str | None = None) -> str:
    """Build a complete ABC tune — the full score, or one player's part.

    Everything structural is written here rather than by the model: one time
    signature, one unit note length, one tempo, correct clefs, and a brace
    around each grand staff.
    """
    parts_by_id = {part["partId"]: part for part in score["parts"]}
    included = [p for p in roster if only_part_id is None or p["partId"] == only_part_id]

    subtitle = score.get("tempoText") or ""
    # Deliberately no second T: naming the player. abcjs treats a second title
    # as a subtitle line, which pushes the first staff to line index 1 — and it
    # only prints the time signature on line 0, so the part would come out
    # without its meter. The player's name reaches the sheet through the voice
    # declaration below, which puts it at the top left where it belongs.
    header = [
        "X:1",
        f"T:{score['title']}",
        "C:Generated by AURA",
        f"M:{score['timeSignature']}",
        f"L:{UNIT_NOTE_LENGTH}",
        f"Q:{score['beatUnit']}={score['tempoBpm']}" + (f' "{subtitle}"' if subtitle else ""),
    ]

    if len(included) > 1 or any(p["staves"] == 2 for p in included):
        header.append(_score_directive(included))
    header += _voice_declarations(included)

    first = parts_by_id.get(included[0]["partId"], {}) if included else {}
    header.append(f"K:{first.get('abcKey') or 'C'}")

    body = []
    for part in included:
        written = parts_by_id.get(part["partId"])
        if not written:
            continue
        ids = _voice_ids(part)
        staves = [_clean(written.get("abc"))]
        if len(ids) > 1:
            staves.append(_clean(written.get("abcLower")) or staves[0])

        for voice_id, music in zip(ids, staves):
            body.append(f"V:{voice_id}")
            if not part["pitched"]:
                body.append("%%MIDI channel 10")
            body.append(f"%%MIDI program {part['midi']}")
            body.append(f"K:{written.get('abcKey') or 'C'}")
            body.append(music)

    return "\n".join(header + body) + "\n"


def _staves(part: dict, written: dict) -> list:
    """[(label, music)] for a part — two entries when it's a grand staff."""
    upper = _clean(written.get("abc"))
    if part["staves"] == 2:
        return [(f"{part['label']} (right hand)", upper),
                (f"{part['label']} (left hand)", _clean(written.get("abcLower")) or upper)]
    return [(part["label"], upper)]


def _range_for(part: dict, level: str) -> tuple:
    """The range a violation is measured against.

    Beginners are held to the easy range, because that is the whole point of the
    level. Above that the easy range is a preference rather than a limit, so only
    the instrument's real range is enforced — flagging every expressive excursion
    would bury the defects that matter.
    """
    text = part["easyRange"] if level == "beginner" else part["fullRange"]
    if "-" not in text or text.strip() == "-":
        return None, None                            # unpitched percussion
    low, _, high = text.partition("-")
    return low, high


def validate_notation(score: dict, roster: list, level: str = "") -> list:
    """Check what came back, arithmetically. Returns defects in plain English.

    This is the part that doesn't take the model's word for anything. Models are
    fluent about music and unreliable at counting, so every bar is added up here
    and every part's bar count compared against the rest. Each defect names the
    part and the bar, which is precisely what the repair round needs to fix it.
    """
    expected = abc_check.units_per_bar(score.get("timeSignature"), UNIT_NOTE_LENGTH)
    by_id = {written["partId"]: written for written in score["parts"]}

    problems, counts = [], {}
    for part in roster:
        written = by_id.get(part["partId"])
        if not written:
            problems.append(f"{part['label']}: no music was returned for this part.")
            continue
        low, high = _range_for(part, level)
        for label, music in _staves(part, written):
            problems.extend(abc_check.check_part(music, expected, label))
            if low and part["pitched"]:
                problems.extend(abc_check.check_range(music, low, high, label))
            counts[label] = abc_check.bar_count(music)

    if len(set(counts.values())) > 1:
        # The length most parts agree on is almost always the intended one.
        target = max(set(counts.values()), key=list(counts.values()).count)
        for label, count in counts.items():
            if count != target:
                problems.append(
                    f"{label} has {count} bars; every part must have exactly {target}.")
    return problems


# Models disagree about parameters: some want max_completion_tokens instead of
# max_tokens, and the reasoning models accept only the default temperature.
# Rather than keep a list of model names that goes stale, take OpenAI at its
# word — it names the offending parameter in the 400 — and retry without it.
UNSUPPORTED_RE = re.compile(
    r"(?:Unsupported|Unrecognized|Invalid) (?:parameter|value)[^']*'([^']+)'")

RENAMES = {"max_tokens": "max_completion_tokens"}


def _create(**kwargs):
    # Terminates: every pass either returns, re-raises, or removes one key.
    while True:
        try:
            return client().chat.completions.create(**kwargs)
        except OpenAIError as error:
            name = next(iter(UNSUPPORTED_RE.findall(str(error))), None)
            if name not in kwargs:
                raise
            value = kwargs.pop(name)
            replacement = RENAMES.get(name)
            if replacement and replacement not in kwargs:
                kwargs[replacement] = value


def _describe(problems: list) -> list:
    """Turn defect strings into something a musician should read.

    A score that still has bad bars is worth handing over — the player can see
    it, print it and regenerate. Shipping it silently as correct is not.
    """
    if not problems:
        return []
    timing = [p for p in problems if "playable note" not in p]
    reach = [p for p in problems if "playable note" in p]
    warnings = []
    for found, opening in ((timing, "Some bars don't add up to the time signature, so "
                                    "the parts won't line up perfectly: "),
                           (reach, "Some notes sit outside what the player can reach: ")):
        if not found:
            continue
        shown = found[:6]
        warning = opening + "; ".join(shown)
        if len(found) > len(shown):
            warning += f"; and {len(found) - len(shown)} more"
        warnings.append(warning + ". Generating again usually clears it.")
    return warnings


# ---------------------------------------------------------------------------
# Revising a piece that already exists
# ---------------------------------------------------------------------------

REVISION_MAX = 400

# A revision returns replacements for the parts it touched, and may adjust the
# few top-level facts a plain-language request can reasonably ask about ("make
# it slower", "call it something else"). Everything is required because strict
# JSON schema demands it; null means "leave this alone".
REVISION_SCHEMA = _obj({
    "summary": {"type": "string",
                "description": "One sentence, for the user, saying what you changed."},
    "parts": {"type": "array", "items": PART_SCHEMA,
              "description": "ONLY the parts you rewrote. Omit every part you left alone."},
    "tempoBpm": {"type": ["integer", "null"], "description": "New tempo, or null to keep it."},
    "tempoText": {"type": ["string", "null"], "description": "New marking, or null to keep it."},
    "title": {"type": ["string", "null"], "description": "New title, or null to keep it."},
})


def _written_from_score_abc(abc_text: str) -> list:
    """Recover the model's raw lines from an assembled full score.

    Pieces saved before revisions existed kept only the assembled tunes. Since
    this module wrote those tunes, it can read them back: after the header, the
    body is a run of ``V:<id>`` blocks, each with its own ``K:`` and then music.
    Grand-staff instruments contribute two voices, ``P7a`` and ``P7b``, which are
    the one part's upper and lower staves.
    """
    lines = (abc_text or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("K:")), None)
    if start is None:
        return []

    voices, current = {}, None
    order = []
    for line in lines[start + 1:]:
        if line.startswith("V:"):
            current = line[2:].strip().split()[0]
            if current not in voices:
                voices[current] = {"key": "C", "music": []}
                order.append(current)
        elif current is None or line.startswith("%%"):
            continue
        elif line.startswith("K:"):
            voices[current]["key"] = line[2:].strip()
        elif line.strip():
            voices[current]["music"].append(line.strip())

    written = {}
    for voice_id in order:
        part_id = voice_id[:-1] if voice_id[-1] in "ab" else voice_id
        entry = written.setdefault(part_id, {"partId": part_id, "abcKey": voices[voice_id]["key"],
                                             "abc": None, "abcLower": None,
                                             "performanceNote": ""})
        music = " ".join(voices[voice_id]["music"])
        if entry["abc"] is None:
            entry["abc"] = music
        else:
            entry["abcLower"] = music
    return list(written.values())


def _written_parts(score: dict) -> list:
    """The editable lines for this piece, however it was saved."""
    if score.get("written"):
        return score["written"]
    recovered = _written_from_score_abc(score.get("scoreAbc") or "")
    if not recovered:
        raise ComposerError("This piece can't be edited — try generating it again.",
                            status=400)
    return recovered


def _current_parts_text(current: list, roster: list) -> list:
    """The music as it stands, part by part, for the model to edit."""
    by_id = {written["partId"]: written for written in current}
    lines = []
    for part in roster:
        written = by_id.get(part["partId"])
        if not written:
            continue
        lines.append(f"  [{part['partId']}] {part['label']} (written key "
                     f"{written.get('abcKey', '?')}):")
        lines.append(f"    {written.get('abc', '')}")
        if written.get("abcLower"):
            lines.append(f"    lower staff: {written['abcLower']}")
    return lines


def revise(score: dict, request: dict, instruction: str) -> dict:
    """Change part of an existing piece, keeping the rest of it intact.

    The whole point is that this is *not* a new piece: the model is handed the
    music it already wrote and asked to hand back only the lines it needed to
    touch. Everything it does not return is kept exactly as it was, so asking to
    fix the oboe cannot quietly rewrite the violins.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise ComposerError("Say what you'd like changed.", status=400)
    if len(instruction) > REVISION_MAX:
        raise ComposerError(f"Keep the request under {REVISION_MAX} characters.", status=400)

    roster = build_roster(request["instruments"])
    current = _written_parts(score)
    level = options.LEVELS_BY_VALUE[request["level"]]
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    expected = abc_check.units_per_bar(score.get("timeSignature"), UNIT_NOTE_LENGTH)

    lines = [
        "Here is a piece you already wrote. The player wants one thing changed.",
        "",
        f"TITLE: {score.get('title')}",
        f"TIME SIGNATURE: {score.get('timeSignature')}   "
        f"TEMPO: {score.get('tempoText')} ({score.get('tempoBpm')} bpm)   "
        f"CONCERT KEY: {score.get('concertKey')}",
        f"LENGTH: {score.get('measureCount')} measures. Every part has exactly this many.",
        "",
        "THE PARTS AS THEY STAND:",
    ]
    lines += _current_parts_text(current, roster)
    lines += [
        "",
        "WHAT THE PLAYER ASKED FOR:",
        f"  {instruction}",
        "",
        f"PLAYING LEVEL: {level['label']} — {level['guidance']}",
        "",
        "RULES FOR THIS EDIT",
        "  * This is an edit, not a new piece. Keep the theme, the key, the",
        "    character and the measure count exactly as they are unless the",
        "    request specifically asks you to change them.",
        "  * Return ONLY the parts you actually rewrote. Any part you do not",
        "    return is kept as it is — so do not return a part you did not change,",
        "    and do not return an unchanged copy 'for completeness'.",
        "  * A rewritten part must still have exactly "
        f"{score.get('measureCount')} measures, and every bar must total "
        f"{abc_check._fmt(expected)} units at L:{UNIT_NOTE_LENGTH}.",
        "  * Whatever you change must still fit against the parts you are leaving",
        "    alone: check the harmony where they sound together.",
        "  * Stay inside each part's playable range and the level above.",
        "  * Set tempoBpm, tempoText and title to null unless the request asked",
        "    for them to change.",
    ]

    try:
        completion = _create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "revision", "strict": True,
                                "schema": REVISION_SCHEMA},
            },
            # Low: an edit should be a considered change, not a reimagining.
            temperature=0.4,
            max_tokens=_token_budget(roster),
        )
    except OpenAIError as error:
        raise ComposerError(_explain(error, model)) from error

    choice = completion.choices[0]
    if choice.finish_reason == "length":
        raise ComposerError("That change was too big to write in one go. Try asking "
                            "for one part at a time.", status=502)
    try:
        revision = json.loads(choice.message.content)
    except (TypeError, ValueError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error

    changed = {written["partId"]: written for written in (revision.get("parts") or [])
               if written.get("partId")}
    touched_meta = {field: revision.get(field) for field in ("tempoBpm", "tempoText", "title")
                    if revision.get(field)}
    if not changed and not touched_meta:
        raise ComposerError("Nothing was changed — try describing the change "
                            "differently, or naming the part you mean.", status=502)

    # Everything the model did not hand back is kept exactly as it was.
    merged = [changed.get(written["partId"], written) for written in current]

    revised = {
        **{key: score.get(key) for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                           "timeSignature", "concertKey", "measureCount",
                                           "programNote", "rehearsalTips")},
        **touched_meta,
        # Older pieces were saved without it; 1/4 is what they were written with.
        "beatUnit": score.get("beatUnit") or "1/4",
        "parts": merged,
    }

    problems = validate_notation(revised, roster, request["level"])

    return {
        **{key: revised[key] for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                         "timeSignature", "concertKey", "measureCount",
                                         "programNote", "rehearsalTips")},
        "conductor": score.get("conductor"),
        "sections": score.get("sections") or [],
        "scoreAbc": assemble_abc(revised, roster),
        "parts": [
            {
                "partId": part["partId"],
                "label": part["label"],
                "instrument": part["instrument"],
                "clef": part["clef"],
                "transposed": part["transpose"] != 0,
                "performanceNote": next(
                    (w.get("performanceNote") for w in merged
                     if w["partId"] == part["partId"]), ""),
                "abc": assemble_abc(revised, roster, only_part_id=part["partId"]),
            }
            for part in roster
            if any(w["partId"] == part["partId"] for w in merged)
        ],
        "written": merged,
        "warnings": _describe(problems),
        "repaired": False,
        "model": model,
        "revisionSummary": revision.get("summary") or "Updated.",
        "changedPartIds": sorted(changed),
    }


# How long the piece is, and how much of it is new material. The second number
# is what the model actually writes; the form builds the first from it. Length
# used to be the most expensive thing a user could ask for — every bar was
# another bar of four-voice chorale in the one call that cannot be parallelised
# — so it was capped at 16 to 32 bars. Now a long piece costs about what a short
# one did, and the model's attention goes on the bars that are actually new.
LENGTHS = {
    "beginner": ("48 to 72", "16 to 24"),
    "intermediate": ("64 to 100", "24 to 32"),
    "higher-intermediate": ("96 to 128", "28 to 36"),
    "pro": ("100 to 150", "32 to 40"),
}

KEY_AREAS = {
    "beginner": "Every section stays in the home key: set transpose to 0 throughout.",
    "intermediate": "At most one section away from home, and only to a close key "
                    "(transpose +5, -5, +7 or +2). Return home well before the end.",
    "higher-intermediate": "At least one real excursion away from home — a transposed "
                           "section or a sequence of them (+2 then +4, say) — before "
                           "the home key returns.",
    "pro": "Travel: at least two different key areas away from home, reached by "
           "transposed sections, a sequence, or a phrase that itself modulates.",
}


def _plan_prompt(request: dict, roster: list) -> str:
    """Ask for the piece itself — its ideas and its form, not the staves."""
    level = request["level"]
    total, unique = LENGTHS[level]
    return "\n".join([
        build_user_prompt(request, roster),
        "",
        "YOUR JOB RIGHT NOW: plan the piece. Do NOT write the individual parts yet.",
        "",
        "HOW THIS PIECE IS WRITTEN",
        "  A piece is a few ideas and what happens to them. So you write each idea",
        "  ONCE, as a PHRASE, and then a FORM that says what happens to it each",
        "  time it comes back. Repeating a phrase, moving it to a new key, handing",
        "  its melody to another instrument, thinning the texture, and marking the",
        "  dynamics in every part are all done for you, exactly, from the form.",
        f"  Write two to four phrases totalling {unique} bars; the form should come",
        f"  to {total} measures. Spend your effort on making those bars excellent —",
        "  they are heard several times each.",
        "",
        "THE PHRASES — plan each one; its four voices are written next, separately.",
        "  * For each phrase give a chord for EVERY bar and a short description of",
        "    its character: the melody's shape and rhythm, the texture, and how it",
        "    ends. Phrases are usually four or eight bars.",
        "  * Phrase A is the main theme. Phrase B must contrast with it: different",
        "    contour, rhythm and harmonic area. Further phrases may be a bridge, a",
        "    development idea, or a closing phrase that cadences firmly home.",
        "  * The chord symbols are binding — every instrument will sit on them — so",
        "    name exactly the chord you mean: 'D7/F#', 'Bbmaj7', 'F#ø7', 'Esus4',",
        "    including every chromatic and borrowed chord. Concert pitch, home key.",
        "  * A phrase followed by more music ends open (a half cadence, a",
        "    dominant); the last section of the form is a phrase at home",
        "    (transpose 0) ending on a full cadence.",
        "",
        "HARMONY THAT GOES SOMEWHERE. A chord grid that sits on I, IV and V is",
        "what makes a piece sound generated. Use inversions, at least one",
        "secondary dominant, a suspension or two that resolve. Each chord should",
        "sound like it was going to follow the one before it.",
        "",
        "THE FORM — the piece, section by section, in order.",
        "  * A section plays one phrase, or a fragment of it (fromBar/toBar): a",
        "    two-bar fragment repeated a step higher is a sequence; the last bars",
        "    of a phrase make a coda; the first bars make a transition.",
        "  * transpose moves a section to another key, measured from home. "
        + KEY_AREAS[level],
        "  * A phrase must never come back unchanged. Each return changes at least",
        "    two of: who leads, the texture, the dynamic, the key. The second A",
        "    should feel like an arrival, not a copy.",
        "  * lead is the partId carrying the tune in that section; they play the",
        "    soprano line in their own register. Pass it between instruments and",
        "    families — a melody that sits in one instrument throughout is the",
        "    commonest way this goes wrong.",
        "  * texture: 'tutti' for arrivals and climaxes, 'reduced' to lighten a",
        "    repeat, 'solo' for one exposed player over a held bass. Give at least",
        "    one player a solo section of four bars or more, unless the ensemble",
        "    has only one or two players.",
        "  * DYNAMICS THAT TRAVEL. Everything at mf is the clearest sign a piece",
        "    was generated. Go somewhere near pp, go somewhere near ff, and put in",
        "    at least one sudden change between sections, not only gradual swells.",
        "  * Measures are numbered by adding up the sections' bars in order.",
        "",
        "Who plays which line of the harmony is worked out from the instruments'",
        "registers and your form, so you do not assign it. Keep every text field",
        "short: this outline is decisions, not prose.",
    ])


# How much freedom a player has with its chorale voice. Most of an orchestra
# has very little, and that is the point: the chord only sounds if most of the
# ensemble is actually playing it.
TREATMENTS = {
    "plain": ("TREATMENT: PLAIN — play your voice's notes, in its rhythm. You may "
              "re-articulate a held note or rest where the texture thins, but do "
              "not add notes of your own. This is the backbone."),
    "sustained": ("TREATMENT: SUSTAINED — play your voice's notes as long held "
                  "values underneath the others, changing only when the harmony "
                  "does. No passing notes at all."),
    "decorated": ("TREATMENT: DECORATED — you may use free rhythm and stepwise "
                  "passing notes around your voice, but you must be on its note "
                  "on every strong beat. You are one of the few parts allowed "
                  "this; the ensemble is relying on the rest being plain."),
}


def _form_lines(plan: dict) -> list:
    lines = []
    phrases = plan["_phrases"]
    for s in plan["_sections"]:
        what = f"phrase {s['phrase']}"
        if s["from"] or s["to"] != phrases[s["phrase"]]["bars"]:
            what += f" bars {s['from'] + 1}-{s['to']}"
        if s["transpose"]:
            what += f", moved {s['transpose']:+d} semitones (to {s['key']})"
        lead = f", tune: [{s['lead']}]" if s["lead"] else ""
        lines.append(f"  mm.{s['start']}-{s['end']} [{s['label']}] {what}; "
                     f"{s['dynamic']} {s['shape']}, {s['texture']}{lead} — {s['description']}")
    return lines


def _batch_prompt(plan: dict, request: dict, roster: list, batch: list) -> str:
    """Ask for a handful of parts — one line per phrase, never the whole piece."""
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    phrases = plan["_phrases"]
    assigned = {entry["partId"]: entry for entry in plan.get("partPlan") or []}

    lines = [
        f"THE PIECE: \"{plan['title']}\" — {plan['concertKey']}, "
        f"{plan['timeSignature']}, {plan['tempoText']} ({plan['tempoBpm']} bpm), "
        f"{plan['measureCount']} measures.",
        "",
        "THE PHRASES — the whole piece is built from these, each written once, as a",
        "four-voice chorale in CONCERT pitch. Every instrument in the ensemble is",
        "playing one of these four lines:",
    ]
    for entry in plan.get("phrases") or []:
        pid = entry.get("id")
        if pid not in phrases:
            continue
        lines.append(f"  PHRASE {pid} — {phrases[pid]['bars']} bars, chords: "
                     + " | ".join(phrases[pid]["chords"]))
        for voice in form.VOICES:
            lines.append(f"    {voice:8}: {entry.get(voice, '')}")

    lines += ["", "THE FORM — how the phrases are used. You do NOT write this out: it is",
              "assembled from your phrases, with the repeats, key changes, texture and",
              "dynamics put in for you."]
    lines += _form_lines(plan)

    lines += ["", "EVERY PART IN THE PIECE, so you know what else is sounding:"]
    for part in roster:
        role = (assigned.get(part["partId"]) or {}).get("role", "")
        mine = " <- WRITE THIS ONE" if part in batch else ""
        lines.append(f"  [{part['partId']}] {part['label']} — {role}{mine}")

    lines += ["", f"WRITE THESE {len(batch)} PART(S) NOW, AND ONLY THESE:"]
    for part in batch:
        transposition = TRANSPOSITION_TEXT.get(
            part["transpose"], f"sounds {part['transpose']} semitones from written")
        detail = [f"clef {part['clef']}", transposition,
                  _range_line(part, request["level"])]
        if part["staves"] == 2:
            detail.append("GRAND STAFF: right hand in \"abc\", left hand in \"abcLower\"")
        lines.append(f"  [{part['partId']}] {part['label']} — " + "; ".join(detail))

        spec = assigned.get(part["partId"]) or {}
        leads = [f"mm.{s['start']}-{s['end']}" for s in plan["_sections"]
                 if s["lead"] == part["partId"]]
        if leads:
            lines.append(f"      You carry the tune in {', '.join(leads)}: write it to be heard.")
        if spec.get("role"):
            lines.append(f"      its role: {spec['role']}")

        if not part["pitched"]:
            lines.append("      Rhythm staff: c = the low sound, e = the high sound, plus")
            lines.append("      rests. Write a groove for each phrase that suits its")
            lines.append("      character and drives without covering the tune.")
            continue

        key = form.written_key_for(part, plan["concertKey"])
        voice = spec.get("voice") if spec.get("voice") in form.VOICES else "soprano"
        lines.append(f"      YOUR LINE, already at your instrument's written pitch in "
                     f"{key} — decorate THIS, do not transpose anything:")
        for pid in phrases:
            lines.append(f"        phrase {pid}: "
                         + form.phrase_line(plan, phrases, part, voice, pid, units))
        lines.append("      " + TREATMENTS.get(spec.get("treatment"), TREATMENTS["decorated"]))

    level = options.LEVELS_BY_VALUE[request["level"]]
    bar_counts = ", ".join(f"{pid} = {p['bars']} bars" for pid, p in phrases.items())
    lines += [
        "",
        f"PLAYING LEVEL: {level['label']} — {level['guidance']}",
        "",
        "RULES",
        "  * Write each phrase ONCE: one entry per phrase id, for every phrase.",
        f"    Bar counts: {bar_counts}. Every bar totals {abc_check._fmt(units)} units "
        f"at L:{UNIT_NOTE_LENGTH}. Count them.",
        "  * No pickups, no repeat signs, no header fields, and no dynamics: the",
        "    dynamic of every section is written in for you from the form. DO mark",
        "    articulation — slurs, staccato, accents, tenuto — because that is",
        "    yours and it is kept.",
        "  * Return ONLY the parts listed immediately above. Nothing else.",
        "",
        "THE ONE RULE THAT MATTERS MOST",
        "  Your line is printed above, already at your instrument's written pitch.",
        "  Every other player is playing one of the same four lines, and most of",
        "  them are playing it exactly as given. You are decorating yours. That",
        "  shared line is the only thing keeping the ensemble in tune.",
        "  * On every strong beat, be on the note your line has for that beat.",
        "    Not near it. On it.",
        "  * Between those, decorate: repeat it, sustain it, or pass stepwise to",
        "    the next one. A passing note falls on a weak beat and resolves by",
        "    step into the line note that follows it.",
        "  * Do not change a pitch because it sounds better alone. Alone it is a",
        "    colour; against the ensemble playing the written harmony it is a",
        "    crunch.",
        "",
        "WRITING A LINE RATHER THAN FILLING A SLOT",
        "  * Give the part its own RHYTHM — that is where your freedom is. Enter",
        "    off the beat, sustain through a bar someone else is busy in, answer a",
        "    phrase. Independent rhythm over shared harmony is what an orchestra",
        "    sounds like; independent harmony is what a pile-up sounds like.",
        "  * Breathe at the seams of the phrase — wind and brass players",
        "    physically have to, and a string line that never lifts sounds",
        "    mechanical.",
        "  * Slur what sings, detach what drives.",
    ]
    return "\n".join(lines)


CHORALE_RULES = [
        "EACH PHRASE IS A FOUR-VOICE CHORALE — THE MOST IMPORTANT THING YOU WILL DO.",
        "Every instrument in the ensemble will be assigned one of these four lines.",
        "That is the only reason a dozen parts written separately can sound like",
        "one ensemble. So it has to be correct four-part writing:",
        "  * every vertical sonority is a real chord — no accidental seconds",
        "    between voices, nothing left unresolved;",
        "  * the chord symbol for each bar is binding: on every beat the alto,",
        "    tenor and bass sit on tones of that chord (notes that are not will",
        "    be moved onto it), so name exactly the chord you mean — 'D7/F#',",
        "    'Bbmaj7', 'F#ø7', 'Esus4' — including every chromatic chord;",
        "  * voices move by step wherever they can and hold common tones;",
        "  * no parallel fifths or octaves between any two voices;",
        "  * sevenths resolve down, leading notes resolve up, suspensions resolve;",
        "  * keep the voices in a sensible spread — close at the top, wider at the",
        "    bottom — and do not let them cross;",
        "  * give the inner voices and the bass rhythm of their own where the tune",
        "    holds, so the chorale breathes rather than marching in block chords.",
        "If the chorale sounds good on its own at a piano, the orchestration will",
        "sound good. If it does not, nothing the players do afterwards can save it.",
        "",
    ]


def _chorale_prompt(outline: dict, request: dict, phrase: dict) -> str:
    """Ask for one phrase's four voices, over chords that are already fixed."""
    level = options.LEVELS_BY_VALUE[request["level"]]
    mood = options.MOODS_BY_VALUE[request["mood"]]
    units = abc_check.units_per_bar(outline["timeSignature"], UNIT_NOTE_LENGTH)
    pid, chords = phrase["id"], phrase["chords"]
    uses = [f"{entry.get('label')}: {entry.get('dynamic')}, {entry.get('texture')}"
            + (f", moved {entry.get('transpose'):+d} semitones" if entry.get("transpose") else "")
            for entry in outline.get("form") or [] if entry.get("phrase") == pid]
    others = [f"  {p['id']}: {' | '.join(p.get('chords') or [])} — {p.get('character', '')}"
              for p in outline.get("phrases") or [] if p.get("id") != pid]
    first = (outline.get("phrases") or [{}])[0].get("id") == pid
    return "\n".join([
        f"THE PIECE: \"{outline['title']}\" — {outline['concertKey']}, "
        f"{outline['timeSignature']}, {outline['tempoText']} ({outline['tempoBpm']} bpm).",
        f"MOOD: {mood['label']} — {mood['guidance']}",
        f"PLAYING LEVEL: {level['label']}.",
        *[f"  {line}" for line in LEVEL_DEMANDS[request["level"]]],
        "",
        f"WRITE PHRASE {pid} NOW — {len(chords)} bars, one chord per bar, binding:",
        "  " + " | ".join(chords),
        f"  Its character: {phrase.get('character', '')}",
        ("  This is phrase A: its soprano IS the main theme of the piece — a tune "
         "someone could sing back after one hearing, with a shape and a high point."
         if first else "  It must contrast with the main theme."),
        "  Where the form uses it: " + ("; ".join(uses) or "throughout"),
        "",
        "THE OTHER PHRASES, for context (being written at the same time):",
        *others,
        "",
        *CHORALE_RULES,
        "FORMAT",
        f"  * Exactly {len(chords)} bars in every voice, every bar totalling "
        f"{abc_check._fmt(units)} units at L:{UNIT_NOTE_LENGTH} (a plain letter is 1, "
        "C2 is 2, C4 is 4). Count them.",
        "  * Concert pitch, read against the home key signature: a note outside",
        "    the key needs its accidental. No pickups, no repeat signs, no header",
        "    fields, no dynamics.",
    ])


def _notes(outline: dict, request: dict, roster: list, model: str, deadline: float) -> dict:
    """Programme note, rehearsal tips and conductor material, from the outline.

    Runs while the chorales are being written, so it costs no time. Never fatal:
    a piece without a programme note is still a piece.
    """
    sections = form.read_form(outline, {p["id"]: {"bars": len(p.get("chords") or [1])}
                                        for p in outline.get("phrases") or [] if p.get("id")},
                              roster, request["level"])
    lines = [
        f"THE PIECE: \"{outline['title']}\" — {outline['concertKey']}, "
        f"{outline['timeSignature']}, {outline['tempoText']} ({outline['tempoBpm']} bpm), "
        f"{sum(s['bars'] for s in sections)} measures.",
        "FORM:",
        *[f"  mm.{s['start']}-{s['end']} [{s['label']}] {s['dynamic']}, {s['texture']}"
          + (f", tune in [{s['lead']}]" if s["lead"] else "") + f" — {s['description']}"
          for s in sections],
        "PLAYERS: " + ", ".join(f"[{p['partId']}] {p['label']}" for p in roster),
        "",
        "Write a short programme note for the players and two or three rehearsal tips.",
    ]
    if request["conductor"]:
        lines += ["Also fill in \"conductor\": an interpretation summary, a tempo map, the",
                  "cues to give and to whom (real measure numbers and player names from",
                  "above), rehearsal marks at the section starts with what to fix at",
                  "each, and concrete beat-pattern and gesture guidance. Keep it brief."]
    else:
        lines.append("Set \"conductor\" to null.")
    left = _seconds_left(deadline) - PARTS_RESERVE
    if left < MIN_CALL_SECONDS:
        return {}
    try:
        completion = _create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": "\n".join(lines)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "notes", "strict": True,
                                             "schema": NOTES_SCHEMA}},
            temperature=0.7,
            max_tokens=PLAN_TOKENS,
            reasoning_effort=PARTS_EFFORT,
            timeout=min(CHORALE_TIMEOUT, left),
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as error:                          # noqa: BLE001
        _log.warning("Programme notes failed; the piece ships without them: %s", error)
        return {}


def _chorale(outline: dict, request: dict, phrase: dict, model: str, deadline: float) -> dict:
    """One phrase's four voices — or {} if it cannot be had in time."""
    left = _seconds_left(deadline) - PARTS_RESERVE
    if left < MIN_CALL_SECONDS:
        return {}
    try:
        completion = _create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": _chorale_prompt(outline, request, phrase)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "chorale", "strict": True,
                                             "schema": CHORALE_SCHEMA}},
            temperature=0.8,
            max_tokens=PLAN_TOKENS,
            reasoning_effort=CHORALE_EFFORT,
            timeout=min(CHORALE_TIMEOUT, left),
        )
        if completion.choices[0].finish_reason == "length":
            return {}
        return json.loads(completion.choices[0].message.content)
    except Exception as error:                          # noqa: BLE001
        # Never fatal: the phrase's chords are known, and form.py voices them.
        _log.warning("Phrase %s chorale failed; voicing its chords instead: %s",
                     phrase.get("id"), error)
        return {}


def _plan(request: dict, roster: list, model: str, deadline: float = None,
          report=None) -> dict:
    """First pass: settle the piece. An outline, then every phrase at once.

    The outline is decisions — key, form, a chord for every bar of every
    phrase, who plays what — and comes back quickly. The four-voice chorales,
    the slow, careful part, are then written one phrase per call, concurrently,
    so the wait is one phrase rather than all of them.
    """
    if deadline is None:
        deadline = time.monotonic() + TIME_BUDGET
    try:
        completion = _create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": _plan_prompt(request, roster)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "plan", "strict": True,
                                             "schema": PLAN_SCHEMA}},
            temperature=0.8,
            max_tokens=PLAN_TOKENS,
            reasoning_effort=PLAN_EFFORT,
            timeout=PLAN_TIMEOUT,
        )
    except OpenAIError as error:
        raise ComposerError(_explain(error, model)) from error

    if completion.choices[0].finish_reason == "length":
        raise ComposerError("The planner ran out of room. Try a shorter piece.", status=502)
    try:
        plan = json.loads(completion.choices[0].message.content)
    except (TypeError, ValueError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error

    if not request["conductor"]:
        plan["conductor"] = None
    _tidy_labels(plan)

    # The chorales, one phrase per call, and the words for the players — all
    # at once.
    phrases = [p for p in plan.get("phrases") or [] if p.get("id") and p.get("chords")]
    pending = [p for p in phrases if not (p.get("soprano") and p.get("bass"))]
    wants_notes = "programNote" not in plan
    if report:
        report("planning", 1, len(pending) + 1)
    if pending or wants_notes:
        done = 1
        with ThreadPoolExecutor(max_workers=len(pending) + 1) as pool:
            futures = {pool.submit(_chorale, plan, request, p, model, deadline): p
                       for p in pending}
            notes = pool.submit(_notes, plan, request, roster, model, deadline) \
                if wants_notes else None
            for future in as_completed(futures):
                futures[future].update(future.result() or {})
                done += 1
                if report:
                    report("planning", done, len(pending) + 1)
            if notes:
                plan.update({key: value for key, value in (notes.result() or {}).items()
                             if key in ("programNote", "rehearsalTips", "conductor")})
    plan.setdefault("programNote", "")
    plan.setdefault("rehearsalTips", "")
    plan.setdefault("conductor", None)
    if not request["conductor"]:
        plan["conductor"] = None

    # Expand the phrases and the form into the whole piece.
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    phrases = form.read_phrases(plan, units)
    if not phrases:
        raise ComposerError("The planner didn't write any usable music this time. "
                            "Please try again.", status=502)
    sections = form.read_form(plan, phrases, roster, request["level"])
    if not plan.get("partPlan"):
        form.assign_parts(plan, roster, sections)
    form.balance_voices(plan, roster)
    plan.update(form.summarise(sections, phrases))
    plan["_phrases"], plan["_sections"] = phrases, sections
    _tidy_conductor(plan)
    return plan


METER_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")


def _tidy_labels(plan: dict) -> None:
    """Keep the two fields that are read as data from arriving as prose.

    Asked for a time signature, the model sometimes answers with the whole plan
    for it — "4/4; mm.17-24 in 7/8; mm.25-28 in 5/4". That string is written
    straight into the ABC ``M:`` field and is also what every bar is measured
    against, so a sentence there is not cosmetic: a piece whose meter reads
    "6/8, with 5/8 episodes" gets silently checked as though it were 4/4, and
    every bar in it is validated against the wrong length.
    """
    meter = METER_RE.search(str(plan.get("timeSignature") or ""))
    plan["timeSignature"] = f"{meter.group(1)}/{meter.group(2)}" if meter else "4/4"

    # The key is a label printed beside the title; modulations belong in the
    # programme note, which is where the schema asks for them.
    key = str(plan.get("concertKey") or "").strip()
    key = re.split(r"[,;(]", key)[0]
    key = METER_RE.sub("", key).strip().rstrip(".").strip()
    plan["concertKey"] = key[:28] or "C major"


def _tidy_conductor(plan: dict) -> None:
    """Drop conductor entries that point past the end of the piece."""
    conductor = plan.get("conductor")
    if not conductor:
        return
    last = plan["measureCount"]
    for field in ("tempoMap", "cues", "rehearsalMarks"):
        conductor[field] = [row for row in conductor.get(field) or []
                            if 1 <= int(row.get("measure") or 0) <= last]


def _unique_bars(plan: dict) -> int:
    return sum(p["bars"] for p in plan["_phrases"].values())


def _batch_size(unique_bars: int, phrase_count: int = 1) -> int:
    """How many parts one reply can finish comfortably.

    A part is roughly twenty tokens a bar once the JSON wrapper is counted, and
    it is written per phrase, so what matters is the new material, not the
    length of the piece. The budget is deliberately a fraction of what the
    model will allow: no single reply should be anywhere near its ceiling.
    """
    per_part = max(200, unique_bars * 22 + phrase_count * 40 + 150)
    return max(1, min(MAX_PARTS_PER_BATCH, BATCH_BUDGET // per_part))


def _call_for_parts(prompt: str, name: str, temperature: float, model: str,
                    deadline: float) -> list:
    """One structured call that returns parts, phrase by phrase."""
    left = _seconds_left(deadline)
    if left < MIN_CALL_SECONDS:
        raise ComposerError("There wasn't time left to write this part.", status=504)
    try:
        completion = _create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": name, "strict": True,
                                             "schema": BATCH_SCHEMA}},
            temperature=temperature,
            max_tokens=BATCH_BUDGET + REASONING_HEADROOM,
            reasoning_effort=PARTS_EFFORT,
            timeout=left - 5,
        )
    except OpenAIError as error:
        raise ComposerError(_explain(error, model)) from error

    if completion.choices[0].finish_reason == "length":
        raise ComposerError("A part was cut off while being written.", status=502)
    try:
        return json.loads(completion.choices[0].message.content)["parts"]
    except (TypeError, ValueError, KeyError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error


def _seconds_left(deadline: float) -> float:
    return deadline - time.monotonic()


def _write_batch(plan: dict, request: dict, roster: list, batch: list, model: str,
                 deadline: float) -> list:
    """One batch of parts. Raises ComposerError so a failure is reported, not silent."""
    returned = _call_for_parts(_batch_prompt(plan, request, roster, batch), "parts", 0.7,
                               model, deadline)
    wanted = {part["partId"] for part in batch}
    return [row for row in returned if row.get("partId") in wanted and row.get("phrases")]


def _write_batch_insisting(plan: dict, request: dict, roster: list, batch: list,
                           model: str, deadline: float) -> tuple:
    """A batch, asked twice if it comes back short — and never fatal.

    A batch failure costs only that batch. It is retried once, and if it still
    will not come, the parts it owed are built from the chorale instead — a
    plainer line, but a correct one, and the rest of the score is delivered.
    """
    written, why = [], None
    try:
        written = _write_batch(plan, request, roster, batch, model, deadline)
    except ComposerError as error:
        why = error
        _log.warning("A batch failed, retrying it: %s", error)

    done = {row["partId"] for row in written}
    still_missing = [part for part in batch if part["partId"] not in done]
    if still_missing:
        try:
            written += _write_batch(plan, request, roster, still_missing, model, deadline)
            why = None
        except ComposerError as error:
            why = error
            _log.warning("A batch failed twice; its parts will be derived: %s", error)
    return written, why


def _model_written(plan: dict, roster: list) -> list:
    """The parts the model writes by hand. Everyone else is derived exactly.

    Every hand-written part is another line inventing notes against a dozen
    that are playing the harmony exactly — and another slow call. Capping it
    here rather than asking nicely in the prompt is what makes the limit real.
    Unpitched percussion is always written: a chorale voice has no rhythm to
    give a snare drum.
    """
    assigned = {entry["partId"]: entry for entry in plan.get("partPlan") or []}
    budget = max(1, min(MAX_DECORATED, len(roster) // 4))

    wants = [part for part in roster if part["pitched"] and part["instrument"] != "Timpani"
             and (assigned.get(part["partId"]) or {}).get("treatment") == "decorated"]
    # If the plan asked for more than its share, the ones carrying the tune win.
    leads = [s["lead"] for s in plan["_sections"] if s["lead"]]
    soloists = [s["lead"] for s in plan["_sections"] if s["texture"] == "solo"]
    priority = {}
    for pid in soloists + leads:
        priority.setdefault(pid, len(priority))
    wants.sort(key=lambda part: priority.get(part["partId"], 99))
    chosen = {part["partId"] for part in wants[:budget]}
    return [part for part in roster if part["partId"] in chosen or not part["pitched"]]


def _write_parts(plan: dict, request: dict, roster: list, model: str, deadline: float,
                 report=None) -> tuple:
    """Second pass: the few hand-written lines, phrase by phrase.

    Returns ({partId: {phraseId: {abc, abcLower, performanceNote}}}, failures).
    The batches go out together; they don't need each other's output.
    """
    to_write = _model_written(plan, roster)
    _log.info("%d of %d parts sent to the model; %d unique bars become %d measures",
              len(to_write), len(roster), _unique_bars(plan), plan["measureCount"])
    if not to_write:
        if report:
            report("writing", 1, 1)
        return {}, []

    size = _batch_size(_unique_bars(plan), len(plan["_phrases"]))
    batches = [to_write[i:i + size] for i in range(0, len(to_write), size)]
    if report:
        report("writing", 0, len(batches))

    rows, failures, finished = [], [], 0
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BATCHES) as pool:
        futures = [pool.submit(_write_batch_insisting, plan, request, roster, batch, model,
                               deadline)
                   for batch in batches]
        for future in as_completed(futures):
            part_of_it, why = future.result()
            rows += part_of_it
            if why:
                failures.append(why)
            finished += 1
            if report:
                report("writing", finished, len(batches))
    return _by_phrase(rows, plan), failures


def _by_phrase(rows: list, plan: dict) -> dict:
    out = {}
    for row in rows:
        mine = out.setdefault(row["partId"], {})
        for entry in row.get("phrases") or []:
            pid = str(entry.get("phrase") or "").strip()
            if pid in plan["_phrases"] and entry.get("abc"):
                mine[pid] = {"abc": entry["abc"], "abcLower": entry.get("abcLower"),
                             "performanceNote": row.get("performanceNote") or ""}
    return {pid: phrases for pid, phrases in out.items() if phrases}


def _check_written(plan: dict, roster: list, written: dict, level: str) -> dict:
    """{partId: {phraseId: [defects]}} for the hand-written phrases."""
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    by_id = {part["partId"]: part for part in roster}
    found = {}
    for part_id, phrases in written.items():
        part = by_id[part_id]
        low, high = _range_for(part, level)
        for phrase_id, text in phrases.items():
            bars = plan["_phrases"][phrase_id]["bars"]
            label = f"{part['label']} phrase {phrase_id}"
            problems = form.check_phrase(text["abc"], bars, units, label)
            if part["staves"] == 2 and text.get("abcLower"):
                problems += form.check_phrase(text["abcLower"], bars, units,
                                              f"{label} (left hand)")
            if low and part["pitched"]:
                problems += abc_check.check_range(text["abc"], low, high, label)
            if not problems:
                # Only once the bars add up is it meaningful to ask what sounds
                # on each beat.
                problems += form.harmony_clashes(
                    text["abc"], part, plan["_phrases"][phrase_id],
                    form.written_key_for(part, plan["concertKey"]),
                    form.beat_units(plan["timeSignature"]), label)
            if problems:
                found.setdefault(part_id, {})[phrase_id] = problems
    return found


MAX_DEFECTS_SHOWN = 60
REPAIR_SCHEMA = BATCH_SCHEMA


def _repair_prompt(plan: dict, roster: list, written: dict, broken: dict, batch: list) -> str:
    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    by_id = {part["partId"]: part for part in roster}
    lines = [
        "You wrote these phrases and some bars do not add up, notes are out of",
        "range, or the line is off the chord on its strong beats. I checked them.",
        "",
        f"The time signature is {plan['timeSignature']} and the unit note length is "
        f"L:{UNIT_NOTE_LENGTH}, so EVERY bar must total exactly {abc_check._fmt(units)} "
        "units, where a plain letter is 1 unit, C2 is 2, C4 is 4, C/2 is a half.",
        "",
        "THE PHRASES TO CORRECT, AS THEY STAND:",
    ]
    for part_id in batch:
        for phrase_id, problems in broken[part_id].items():
            text = written[part_id][phrase_id]
            bars = plan["_phrases"][phrase_id]["bars"]
            lines.append(f"  [{part_id}] {by_id[part_id]['label']}, phrase {phrase_id} "
                         f"(must be {bars} bars):")
            lines.append(f"    {text['abc']}")
            if text.get("abcLower"):
                lines.append(f"    left hand: {text['abcLower']}")
            for problem in problems[:MAX_DEFECTS_SHOWN]:
                lines.append(f"    - {problem}")
    lines += [
        "",
        "Correct the DURATIONS, any notes out of range, and any strong beat that is",
        "not on a note of the chorale sounding at that moment. Keep the pitches,",
        "the articulation and the character of what you wrote — this is a correction,",
        "not a rewrite. Where a bar is too long, shorten a note or drop a repeated",
        "one; where it is too short, lengthen the last note of the bar. Add up every",
        "bar before you return it.",
        "",
        "Return exactly these part(s), each with only the phrases listed for it.",
    ]
    return "\n".join(lines)


def _repair_phrases(plan: dict, roster: list, written: dict, broken: dict, level: str,
                    model: str, deadline: float, report=None) -> tuple:
    """One corrective round on the broken phrases only — a few bars, not a score.

    Kept to one round on purpose: it reliably clears arithmetic slips, and past
    that the model starts recomposing rather than correcting. A phrase is
    replaced only if the correction actually has fewer defects.
    Returns (written, repaired?).
    """
    ids = [part["partId"] for part in roster if part["partId"] in broken]
    size = MAX_PARTS_PER_BATCH
    batches = [ids[i:i + size] for i in range(0, len(ids), size)]
    if report:
        report("fixing", 0, len(batches))

    def one(batch):
        # The correction round is optional: whatever goes wrong in it, the
        # phrases it could not fix fall back to the chorale, and the piece ships.
        try:
            return _call_for_parts(_repair_prompt(plan, roster, written, broken, batch),
                                   "repaired_parts", 0.2, model, deadline)
        except Exception as error:                      # noqa: BLE001
            _log.warning("A repair batch failed; its phrases will be derived: %s", error)
            return []

    returned, finished = [], 0
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BATCHES) as pool:
        for future in as_completed([pool.submit(one, batch) for batch in batches]):
            returned += future.result()
            finished += 1
            if report:
                report("fixing", finished, len(batches))

    candidates = _by_phrase([row for row in returned if row.get("partId") in broken], plan)
    merged = {pid: dict(phrases) for pid, phrases in written.items()}
    improved = False
    for part_id, phrases in candidates.items():
        for phrase_id, text in phrases.items():
            if phrase_id not in broken[part_id]:
                continue
            text["performanceNote"] = (written[part_id].get(phrase_id) or {}) \
                .get("performanceNote", "")
            trial = {part_id: {phrase_id: text}}
            after = _check_written(plan, roster, trial, level).get(part_id, {}).get(phrase_id, [])
            if len(after) < len(broken[part_id][phrase_id]):
                merged[part_id][phrase_id] = text
                improved = True
    return merged, improved


def _drop_misaligned(plan: dict, roster: list, written: dict, level: str) -> list:
    """Remove any phrase that would break the ensemble. Returns the labels hit.

    A phrase with a short bar would pull every part after it out of line, and a
    phrase off the chord on its strong beats would sour every section it is
    heard in. Neither is used: that part plays its chorale voice for that
    phrase instead — plainer, but exactly in time and in tune. A note out of
    range is left in and reported; it does not stop the ensemble playing
    together.
    """
    by_id = {part["partId"]: part for part in roster}
    dropped = []
    for part_id, phrases in _check_written(plan, roster, written, level).items():
        for phrase_id, problems in phrases.items():
            if any("units long" in p or "bars; it must have" in p
                   or "clashes with the harmony" in p for p in problems):
                written[part_id].pop(phrase_id, None)
                dropped.append(f"{by_id[part_id]['label']} (phrase {phrase_id})")
    return dropped


def compose(request: dict, report=None) -> dict:
    """Generate a score. ``request`` must already have passed validation.

    Two passes: plan the piece as phrases and a form, then write the few lines
    that need writing, phrase by phrase. Everything else — every other part,
    every repeat, every key change, every dynamic — is built from those in
    Python, exactly. See form.py.

    ``report(stage, done, total)`` is called as the work proceeds, so the browser
    can say what is actually happening instead of guessing at a duration.
    """
    roster = build_roster(request["instruments"])
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    level = request["level"]
    deadline = time.monotonic() + TIME_BUDGET

    if report:
        report("planning", 0, 1)
    plan = _plan(request, roster, model, deadline, report)

    written, failures = _write_parts(plan, request, roster, model, deadline, report)

    # Count the hand-written bars ourselves, phrase by phrase, before any of
    # them is multiplied out across the form.
    if report:
        report("checking", 0, 1)
    broken = _check_written(plan, roster, written, level)
    if report:
        report("checking", 1, 1)
    repaired = False
    if broken and _seconds_left(deadline) >= MIN_CALL_SECONDS:
        written, repaired = _repair_phrases(plan, roster, written, broken, level, model,
                                            deadline, report)
    dropped = _drop_misaligned(plan, roster, written, level)

    units = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)
    parts = form.realise(plan, roster, plan["_sections"], plan["_phrases"], units, written)

    score = {
        **{key: plan.get(key) for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                          "timeSignature", "concertKey", "measureCount",
                                          "programNote", "rehearsalTips")},
        "conductor": plan.get("conductor") if request["conductor"] else None,
        "parts": parts,
    }

    # The final check is a safety net: derived parts are exact by construction,
    # so anything it finds is a note out of range in a hand-written line.
    problems = validate_notation(score, roster, level)

    if report:
        report("engraving", 1, 1)

    warnings = _describe(problems)
    unwritten = [part["label"] for part in _model_written(plan, roster)
                 if part["partId"] not in written]
    if unwritten:
        warnings.append("These parts couldn't be written in full, so they play the "
                        "plain harmony line instead: " + ", ".join(unwritten)
                        + ". Generating again usually gives them their own lines.")
    if dropped:
        _log.info("Phrases replaced by their chorale line: %s", ", ".join(dropped))

    return {
        "title": score["title"],
        "tempoText": score["tempoText"],
        "tempoBpm": score["tempoBpm"],
        # Stored because a later revision reassembles every part from these, and
        # a missing beat unit would rewrite the tempo line of parts nobody
        # asked to change.
        "beatUnit": score["beatUnit"],
        "timeSignature": score["timeSignature"],
        "concertKey": score["concertKey"],
        "measureCount": score["measureCount"],
        "programNote": score["programNote"],
        "rehearsalTips": score["rehearsalTips"],
        "conductor": score.get("conductor"),
        # The form, so the player can show which section is sounding, and the
        # chord in every bar.
        "sections": plan.get("sections") or [],
        "harmony": plan.get("harmony") or [],
        "scoreAbc": assemble_abc(score, roster),
        "parts": [
            {
                "partId": part["partId"],
                "label": part["label"],
                "instrument": part["instrument"],
                "clef": part["clef"],
                "transposed": part["transpose"] != 0,
                "performanceNote": next(
                    (w.get("performanceNote") for w in score["parts"]
                     if w["partId"] == part["partId"]), ""),
                "abc": assemble_abc(score, roster, only_part_id=part["partId"]),
            }
            for part in roster
        ],
        # The full-length lines, before assembly. Kept because a revision edits
        # these; the assembled tunes above are built from them.
        "written": score["parts"],
        "warnings": warnings,
        "repaired": repaired,
        "model": model,
    }


def _explain(error, model) -> str:
    text = str(error)
    if "api_key" in text or "Incorrect API key" in text or "401" in text:
        return ("OpenAI rejected our API key. Check OPENAI_API_KEY in "
                "backend/.env and restart the server.")
    if "insufficient_quota" in text or "quota" in text:
        return "The OpenAI account has no quota left. Add credit and try again."
    if "rate_limit" in text:
        return "OpenAI is rate-limiting us right now. Wait a moment and try again."
    if "does not exist" in text or "model_not_found" in text:
        return (f"The configured model ({model}) isn't available to this OpenAI "
                "account. Set OPENAI_MODEL in backend/.env to one that is.")
    if "timeout" in text.lower():
        return ("The score took too long to write. Try again with fewer parts.")
    # A bare "Connection error." from the SDK means the request never reached
    # OpenAI at all. On a filtered network — a school or office one especially —
    # the TLS handshake to api.openai.com is cut the moment the hostname is
    # seen, which looks identical to being offline unless we say otherwise.
    # A timeout is a subclass of the connection error, so it has to be checked
    # first or a slow call gets reported as "you are offline".
    if isinstance(error, APITimeoutError) or "timed out" in text.lower():
        return ("The generator took too long to answer and the request was "
                "dropped. Try a shorter piece, or fewer instruments — the "
                "planning step grows with both.")
    if isinstance(error, APIConnectionError) or "connection error" in text.lower():
        return ("Couldn't reach OpenAI. The request never left this machine, so "
                "this is a network problem rather than anything to do with the "
                "music: check you're online, and if you're on a school or office "
                "network, it may be blocking api.openai.com — a phone hotspot or "
                "VPN is the usual way around it.")
    return f"The score generator failed: {text}"
