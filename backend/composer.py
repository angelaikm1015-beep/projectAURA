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

The other half of accuracy is the brief: every part is described to the model
with its clef, its transposition, and the written range that level of player can
actually reach, so a Bb trumpet part comes back written a tone high and a
beginner flute part stays under G5.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from openai import APIConnectionError, OpenAI, OpenAIError

import abc_check
import options

# Chosen by measurement, not by version number: against gpt-5.5 on the same
# brief it wrote more varied lines, gave every part somewhere to rest, and
# came back with no bar-counting defects at all. See README.
DEFAULT_MODEL = "gpt-5.6-sol"

# The unit note length every part is written in. Fixing it here means one
# duration convention across the score: 1 = eighth, 2 = quarter, 4 = half.
UNIT_NOTE_LENGTH = "1/8"

# Output budget. There is no cap on how many instruments may be asked for, so
# the budget cannot be a constant: every staff is another full line of ABC the
# model has to write out, and a fixed ceiling would silently truncate a large
# ensemble mid-score. Scale it with the roster instead, and let a deployment
# lower the ceiling for a model with a smaller output window.
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
MAX_PARTS_PER_BATCH = 5
MAX_CONCURRENT_BATCHES = 4


def _token_budget(roster: list) -> int:
    """Room to write every staff in ``roster``, within the model's ceiling.

    Grand-staff instruments count twice — a piano part is two written lines.
    """
    staves = sum(part["staves"] for part in roster)
    return min(MAX_OUTPUT_TOKENS, BASE_OUTPUT_TOKENS + OUTPUT_TOKENS_PER_STAFF * staves)

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
        _client = OpenAI(api_key=api_key, timeout=240.0, max_retries=1)
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
        "  - fast sixteenth-note passagework that has to be practised;",
        "  - a meter change or sustained cross-rhythm, and syncopation that",
        "    does not simply fall on the offbeat;",
        "  - four genuinely independent lines at some point — real",
        "    counterpoint, not a melody with block chords underneath;",
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
        "    borrowed chords, extended sonorities, and a real modulation;",
        "  - rhythm beyond straight quarters and eighths: syncopation,",
        "    cross-rhythm, and mixed or changing meter where the music wants it;",
        "  - sustained independent counterpoint, not melody over accompaniment;",
        "  - at least one technically demanding passage for every section —",
        "    fast runs, wide leaps, or writing at the extremes of the range;",
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

    measures = {"beginner": "16 to 24", "intermediate": "24 to 32",
                "higher-intermediate": "32 to 48", "pro": "32 to 64"}[level["value"]]

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
        f"LENGTH: {measures} measures.",
        "",
    ]

    if conductor:
        lines += [
            "CONDUCTOR: yes, this ensemble has a conductor.",
            "  Write for a conducted ensemble: you may use tempo changes, fermatas,",
            "  ritardando and accelerando, rubato and entries that need cueing.",
            "  Also fill in the \"conductor\" object with material for the person on",
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
            "  Set \"conductor\" to null.",
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
# Two passes: plan the piece, then write the parts
# ---------------------------------------------------------------------------
#
# Asking for a whole orchestral score in one reply does not work. The model has
# to hold every decision and every stave in one answer, and the reply is cut off
# partway through — worse with every instrument added, which is exactly when the
# piece matters most. It is also the wrong shape of task: a composer does not
# write twenty staves left to right, they settle the piece first and then write
# the lines into it.
#
# So it goes in two passes. First a PLAN: key, tempo, form, and a chord for every
# single bar — a page of text whatever the ensemble size. Then the PARTS, a few
# at a time, each batch written against that same chord grid and against the
# lines already written. Every reply is small enough to finish, and the parts fit
# together because they were all written to the same harmony rather than
# improvised alongside each other.

CHORD_SCHEMA = _obj({
    "measure": {"type": "integer", "description": "1-based measure number."},
    "chord": {"type": "string",
              "description": "Concert-pitch chord for that bar, e.g. 'G', 'D7', "
                             "'Em', 'C/E', 'A7/C#'. One per measure, no gaps."},
})

SECTION_SCHEMA = _obj({
    "name": {"type": "string", "description": "e.g. 'A', 'B', 'Coda'."},
    "fromMeasure": {"type": "integer"},
    "toMeasure": {"type": "integer"},
    "description": {"type": "string",
                    "description": "One sentence: what happens here and who leads."},
})

PART_PLAN_SCHEMA = _obj({
    "partId": {"type": "string", "description": "Exactly the id given in the brief."},
    "role": {"type": "string",
             "description": "What this player does across the piece — melody, "
                            "counter-melody, inner harmony, bass, rhythmic "
                            "support — and where that changes."},
})

PLAN_SCHEMA = _obj({
    "title": {"type": "string"},
    "tempoText": {"type": "string", "description": "Italian tempo marking."},
    "tempoBpm": {"type": "integer", "description": "Beats per minute, 30-240."},
    "beatUnit": {"type": "string", "enum": ["1/4", "1/2", "3/8", "1/8"]},
    "timeSignature": {"type": "string", "description": "e.g. '4/4', '3/4', '6/8'."},
    "concertKey": {"type": "string",
                   "description": "The sounding key the piece STARTS and ENDS in, "
                                  "named in two or three words and nothing more: "
                                  "'G major', 'D minor'. Describe any modulations in "
                                  "programNote instead — this string is printed as a "
                                  "label on the score."},
    "measureCount": {"type": "integer", "description": "Measures in every part."},
    "programNote": {"type": "string", "description": "2-4 sentences for the players."},
    "rehearsalTips": {"type": "string", "description": "2-3 sentences."},
    "theme": {"type": "string",
              "description": "The main theme as ABC, in concert pitch, unaccompanied. "
                             "Four to eight bars. This is the tune the piece is built "
                             "from and other parts will be written against it."},
    "bass": {"type": "string",
             "description": "The bass line for the WHOLE piece as ABC, concert pitch, "
                            "one line, measureCount bars. Not just the root of each "
                            "chord: a line that moves, with inversions and passing "
                            "notes where they help it walk. The outer parts decide "
                            "how a piece sounds, so this is written here rather than "
                            "left to whoever gets the bass part."},
    "melodyLead": {"type": "array", "items": _obj({
        "fromMeasure": {"type": "integer"},
        "toMeasure": {"type": "integer"},
        "partId": {"type": "string", "description": "Exactly the id given in the brief."},
        "what": {"type": "string", "description": "What they have — theme, counter-melody, "
                                                  "a solo, an answering phrase."},
    }), "description": "Who carries the line, and where it is handed on. Cover the "
                       "whole piece; the tune must not sit in one instrument throughout."},
    "sections": {"type": "array", "items": SECTION_SCHEMA},
    "harmony": {"type": "array", "items": CHORD_SCHEMA,
                "description": "EXACTLY one entry per measure, 1 to measureCount."},
    "partPlan": {"type": "array", "items": PART_PLAN_SCHEMA,
                 "description": "One entry per part in the brief."},
    "conductor": CONDUCTOR_SCHEMA,
})

# One batch of written parts.
BATCH_SCHEMA = _obj({"parts": {"type": "array", "items": PART_SCHEMA}})


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


REPAIR_SCHEMA = _obj({"parts": {"type": "array", "items": PART_SCHEMA}})

MAX_DEFECTS_SHOWN = 60


def _parts_needing_repair(problems: list, roster: list) -> set:
    """Which partIds are implicated by the defect list."""
    needed = set()
    for part in roster:
        if any(problem.startswith(part["label"]) for problem in problems):
            needed.add(part["partId"])
    return needed


def _defects_by_part(problems: list, roster: list) -> dict:
    """{partId: [its defects]} — so each repair call only sees its own work."""
    grouped = {}
    for part in roster:
        mine = [problem for problem in problems if problem.startswith(part["label"])]
        if mine:
            grouped[part["partId"]] = mine
    return grouped


def _repair_batch(score: dict, roster: list, grouped: dict, batch: list,
                  model: str) -> list:
    """Correct one handful of parts. Returns the rewritten lines, or none."""
    by_id = {part["partId"]: part for part in roster}
    current = {written["partId"]: written for written in score["parts"]}
    expected = abc_check.units_per_bar(score.get("timeSignature"), UNIT_NOTE_LENGTH)

    instruction = [
        "You wrote these parts and the bars do not add up. I counted them.",
        "",
        f"The time signature is {score['timeSignature']} and the unit note length is "
        f"L:{UNIT_NOTE_LENGTH}, so EVERY bar must total exactly "
        f"{abc_check._fmt(expected)} units, where a plain letter is 1 unit, C2 is 2, "
        f"C4 is 4, C/2 is a half. Every part must end with exactly "
        f"{score.get('measureCount')} bars.",
        "",
        "THE PARTS TO CORRECT, AS THEY STAND:",
    ]
    for part_id in batch:
        written = current.get(part_id, {})
        instruction.append(f"  [{part_id}] {by_id[part_id]['label']} "
                           f"(written key {written.get('abcKey')}):")
        instruction.append(f"    {written.get('abc', '')}")
        if written.get("abcLower"):
            instruction.append(f"    lower staff: {written['abcLower']}")

    instruction += ["", "WHAT IS WRONG WITH THEM:"]
    for part_id in batch:
        for problem in grouped.get(part_id, [])[:MAX_DEFECTS_SHOWN]:
            instruction.append(f"  - {problem}")

    instruction += [
        "",
        "Correct the DURATIONS and any notes that are out of range. Keep the key, the "
        "harmony and the character of what you already wrote — this is a correction, "
        "not a rewrite. Where a bar is too long, shorten a note or drop a repeated "
        "one; where it is too short, lengthen the last note of the bar. Add up every "
        "bar before you return it.",
        "",
        f"Return exactly these {len(batch)} part(s) and nothing else.",
    ]

    try:
        completion = _create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": "\n".join(instruction)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "repaired_parts", "strict": True,
                                             "schema": REPAIR_SCHEMA}},
            temperature=0.2,
            max_tokens=BATCH_BUDGET + REASONING_HEADROOM,
        )
    except OpenAIError:
        return []

    if completion.choices[0].finish_reason == "length":
        return []
    try:
        returned = json.loads(completion.choices[0].message.content)["parts"]
    except (TypeError, ValueError, KeyError):
        return []
    return [row for row in returned if row.get("partId") in set(batch)]


def _repair(score: dict, roster: list, problems: list, messages: list, model: str) -> dict:
    """One corrective round, batched the same way the writing is.

    Kept to a single round on purpose: it reliably clears arithmetic slips, and
    beyond that the model starts recomposing rather than correcting, at which
    point a fresh generation is the better answer anyway.

    Batched for the same reason the writing is — a thirty-part score can have a
    hundred defects, and asking for every broken part in one reply is how the
    corrections themselves used to get truncated and silently dropped.
    """
    grouped = _defects_by_part(problems, roster)
    if not grouped:
        return score

    broken = [part["partId"] for part in roster if part["partId"] in grouped]
    size = _batch_size(score.get("measureCount") or 32)
    batches = [broken[i:i + size] for i in range(0, len(broken), size)]

    fixed: list = []
    if len(batches) == 1:
        fixed = _repair_batch(score, roster, grouped, batches[0], model)
    else:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BATCHES) as pool:
            futures = [pool.submit(_repair_batch, score, roster, grouped, batch, model)
                       for batch in batches]
            for future in futures:
                fixed += future.result()

    if not fixed:
        return score

    merged = {written["partId"]: written for written in score["parts"]}
    for written in fixed:
        merged[written["partId"]] = written
    return {**score, "parts": [merged[part["partId"]] for part in roster
                               if part["partId"] in merged]}


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
    shown = problems[:6]
    warning = ("Some bars don't add up to the time signature, so the parts won't "
               "line up perfectly: " + "; ".join(shown))
    if len(problems) > len(shown):
        warning += f"; and {len(problems) - len(shown)} more"
    return [warning + ". Generating again usually clears it."]


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


def _plan_prompt(request: dict, roster: list) -> str:
    """Ask for the piece itself — everything except the notes on the staves."""
    return "\n".join([
        build_user_prompt(request, roster),
        "",
        "YOUR JOB RIGHT NOW: plan the piece. Do NOT write the individual parts yet.",
        "",
        "Decide the title, key, time signature, tempo and length. Lay out the form",
        "as sections, and give a chord for EVERY SINGLE MEASURE from 1 to the",
        "measureCount you chose: that grid is what keeps a dozen independent",
        "parts in the same harmony, so it cannot have gaps.",
        "",
        "Write the two lines that decide how the piece sounds, both in CONCERT",
        "pitch, because everything else is written against them:",
        "  * the THEME — unaccompanied, four to eight bars, a tune someone could",
        "    sing back after hearing it once;",
        "  * the BASS — the whole piece, bar for bar. Give it somewhere to walk:",
        "    use inversions so it steps rather than leaping root to root, and",
        "    let it move against the theme rather than with it.",
        "",
        "HARMONY THAT GOES SOMEWHERE. A chord grid that sits on I, IV and V is",
        "what makes a piece sound generated. Use inversions, at least one",
        "secondary dominant, a suspension or two that resolve, and get somewhere",
        "genuinely different in the middle before coming home. Each chord should",
        "sound like it was going to follow the one before it.",
        "",
        "Then: who has the tune, and where they hand it on — fill melodyLead so",
        "it covers the whole piece. A piece where one instrument holds the melody",
        "from bar 1 to the end is the commonest way this goes wrong.",
        "",
        "Finally, say for each part in the brief what that player does across the",
        "piece. Use the exact partId given in brackets, one entry per part.",
    ])


def _batch_prompt(plan: dict, request: dict, roster: list, batch: list) -> str:
    """Ask for a handful of parts, written into a piece that already exists."""
    plans = {entry["partId"]: entry.get("role", "")
             for entry in plan.get("partPlan") or []}
    expected = abc_check.units_per_bar(plan["timeSignature"], UNIT_NOTE_LENGTH)

    harmony = " | ".join(f"{row['measure']}:{row['chord']}"
                         for row in sorted(plan.get("harmony") or [],
                                           key=lambda r: r.get("measure", 0)))

    lines = [
        f"THE PIECE: \"{plan['title']}\" — {plan['concertKey']}, "
        f"{plan['timeSignature']}, {plan['tempoText']} ({plan['tempoBpm']} bpm), "
        f"{plan['measureCount']} measures.",
        "",
        "THE THEME (concert pitch, for reference — transpose it as needed):",
        f"  {plan.get('theme', '')}",
        "",
        "THE BASS LINE for the whole piece (concert pitch). Your part has to work",
        "against this as well as against the chords — these two are the outer",
        "edges of the harmony:",
        f"  {plan.get('bass', '')}",
        "",
        "FORM:",
    ]
    for section in plan.get("sections") or []:
        lines.append(f"  {section.get('name')}: measures {section.get('fromMeasure')}"
                     f"-{section.get('toMeasure')} — {section.get('description')}")

    lines += [
        "",
        "HARMONY — one chord per measure. Other players are writing their lines to",
        "this same grid at the same time, so it is what makes the parts fit: every",
        "note must belong to the chord of its measure, or be a passing note",
        "resolving into it.",
        f"  {harmony}",
    ]

    leads = plan.get("melodyLead") or []
    if leads:
        lines += ["", "WHO HAS THE TUNE, AND WHERE:"]
        for lead in leads:
            lines.append(f"  mm.{lead.get('fromMeasure')}-{lead.get('toMeasure')}: "
                         f"[{lead.get('partId')}] — {lead.get('what')}")
        lines.append("  Where that is not you, stay out of the way: accompany, answer,")
        lines.append("  or rest. Do not double the tune unless it says so.")

    lines += ["", "EVERY PART IN THE PIECE, so you know what else is sounding:"]
    for part in roster:
        role = plans.get(part["partId"], "")
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
        if plans.get(part["partId"]):
            lines.append(f"      its role: {plans[part['partId']]}")

    level = options.LEVELS_BY_VALUE[request["level"]]
    lines += [
        "",
        f"PLAYING LEVEL: {level['label']} — {level['guidance']}",
        "",
        "RULES",
        f"  * Exactly {plan['measureCount']} measures per part, every bar totalling "
        f"{abc_check._fmt(expected)} units at L:{UNIT_NOTE_LENGTH}. Count them.",
        "  * A resting part still needs its bars written as full-bar rests.",
        "  * Write TRANSPOSED (written) pitch, and report that part's written key",
        "    in \"abcKey\".",
        "  * Return ONLY the parts listed immediately above. Nothing else.",
        "",
        "WRITING A LINE RATHER THAN FILLING A SLOT",
        "  These are the things that make generated music sound generated. Avoid",
        "  all of them.",
        "  * Move by step. When the chord changes, go to the nearest note of the",
        "    new chord and keep any note the two chords share. Leaping to the root",
        "    every bar is what makes a part sound typed rather than written.",
        "  * No parallel fifths or octaves with the bass line above, and do not",
        "    double the leading note. Resolve a seventh downward by step.",
        "  * Give the part its own rhythm. If it moves in the same values at the",
        "    same moments as the theme, it is wallpaper. Enter off the beat,",
        "    sustain through a bar someone else is busy in, answer a phrase.",
        "  * Rest. A part that plays every bar from first to last flattens the",
        "    piece; silence is how an entry gets heard.",
        "  * Phrase in fours or eights, and breathe at the seams — wind and brass",
        "    players physically have to, and a string line that never lifts sounds",
        "    mechanical.",
        "  * Shape it: mark dynamics and articulation where they mean something,",
        "    not uniformly.",
    ]
    return "\n".join(lines)


def _plan(request: dict, roster: list, model: str) -> dict:
    """First pass: settle the piece. Small reply whatever the ensemble size."""
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


def _batch_size(measure_count: int) -> int:
    """How many parts one reply can finish comfortably.

    A part is roughly twenty tokens a bar once the JSON wrapper is counted. The
    budget is deliberately a fraction of what the model will allow, because the
    whole point is that no single reply is anywhere near its ceiling.
    """
    per_part = max(200, measure_count * 22 + 150)
    return max(1, min(MAX_PARTS_PER_BATCH, BATCH_BUDGET // per_part))


def _write_batch(plan: dict, request: dict, roster: list, batch: list, model: str) -> list:
    """One batch of parts. Raises ComposerError so a failure is reported, not silent."""
    try:
        completion = _create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _batch_prompt(plan, request, roster, batch)},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "parts", "strict": True,
                                             "schema": BATCH_SCHEMA}},
            temperature=0.7,
            max_tokens=BATCH_BUDGET + REASONING_HEADROOM,
        )
    except OpenAIError as error:
        raise ComposerError(_explain(error, model)) from error

    if completion.choices[0].finish_reason == "length":
        raise ComposerError(
            "A part was cut off while being written. Try a shorter piece — the "
            "number of instruments is no longer what limits this.", status=502)
    try:
        returned = json.loads(completion.choices[0].message.content)["parts"]
    except (TypeError, ValueError, KeyError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error

    wanted = {part["partId"] for part in batch}
    return [row for row in returned if row.get("partId") in wanted]


def _write_batch_insisting(plan: dict, request: dict, roster: list, batch: list,
                           model: str) -> list:
    """A batch, asked twice if it comes back short.

    A batch occasionally returns an empty list or skips a part. Left alone that
    silently costs the score a whole section, so the gap is asked for again
    before anyone sees it — once, because a second empty answer means something
    is wrong that retrying will not fix.
    """
    written = _write_batch(plan, request, roster, batch, model)
    done = {row["partId"] for row in written}
    still_missing = [part for part in batch if part["partId"] not in done]
    if still_missing:
        written += _write_batch(plan, request, roster, still_missing, model)
    return written


def _write_parts(plan: dict, request: dict, roster: list, model: str) -> list:
    """Second pass: the staves, a few at a time, against the shared plan.

    The batches go out together rather than one after another. They do not need
    each other's output — the plan's theme, chord grid and list of roles is the
    whole contract — so a twenty-part orchestra costs about the same wall-clock
    time as a quartet instead of five times as much.
    """
    size = _batch_size(plan.get("measureCount") or 32)
    batches = [roster[i:i + size] for i in range(0, len(roster), size)]

    if len(batches) == 1:
        return _write_batch_insisting(plan, request, roster, batches[0], model)

    written: list = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BATCHES) as pool:
        futures = {pool.submit(_write_batch_insisting, plan, request, roster,
                               batch, model): batch
                   for batch in batches}
        for future in futures:
            written += future.result()

    # Back into the order the player expects to read them in.
    order = {part["partId"]: index for index, part in enumerate(roster)}
    return sorted(written, key=lambda row: order.get(row["partId"], 0))


def compose(request: dict) -> dict:
    """Generate a score. ``request`` must already have passed validation.

    Two passes: plan the piece, then write the parts a few at a time into it.
    See the note above PLAN_SCHEMA for why it is not done in one.
    """
    roster = build_roster(request["instruments"])
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)

    plan = _plan(request, roster, model)
    written = _write_parts(plan, request, roster, model)

    score = {
        **{key: plan.get(key) for key in ("title", "tempoText", "tempoBpm", "beatUnit",
                                          "timeSignature", "concertKey", "measureCount",
                                          "programNote", "rehearsalTips")},
        "conductor": plan.get("conductor") if request["conductor"] else None,
        "parts": written,
    }

    # Add the bars up ourselves. If they don't work, give the model one chance to
    # fix its own arithmetic before we hand anything to a player.
    problems = validate_notation(score, roster, request["level"])
    repaired = False
    if problems:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(request, roster)}]
        try:
            candidate = _repair(score, roster, problems, messages, model)
            remaining = validate_notation(candidate, roster, request["level"])
            # Only keep the repair if it actually helped.
            if len(remaining) < len(problems):
                score, problems, repaired = candidate, remaining, True
        except OpenAIError:
            pass  # The first score still stands; it just keeps its warnings.

    # Decided here, not before the repair: a part that came back empty is often
    # written by the repair round, and warning about it anyway would contradict
    # the score the user is looking at.
    missing = [part["label"] for part in roster
               if not any(w["partId"] == part["partId"] for w in score["parts"])]

    warnings = _describe(problems)
    if missing:
        warnings.append("These parts came back empty and are missing from the score: "
                        + ", ".join(missing) + ". Generating again usually clears it.")

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
            if any(w["partId"] == part["partId"] for w in score["parts"])
        ],
        # The model's own output, before assembly. Kept because a revision edits
        # these lines; the assembled tunes above are built from them and cannot
        # be edited back into shape.
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
    if isinstance(error, APIConnectionError) or "connection error" in text.lower():
        return ("Couldn't reach OpenAI. The request never left this machine, so "
                "this is a network problem rather than anything to do with the "
                "music: check you're online, and if you're on a school or office "
                "network, it may be blocking api.openai.com — a phone hotspot or "
                "VPN is the usual way around it.")
    return f"The score generator failed: {text}"
