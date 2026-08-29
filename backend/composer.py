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

from openai import OpenAI, OpenAIError

import abc_check
import options

DEFAULT_MODEL = "gpt-5.5"

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
        "as dull: the melody must have a shape and go somewhere, and the",
        "accompanying parts must have something to do besides whole notes.",
    ],
    "intermediate": [
        "At least one passage in sixteenth notes at a manageable tempo, at least",
        "one syncopated or dotted figure, at least one dynamic shift the players",
        "have to shape, and one place where a part is briefly exposed.",
    ],
    "higher-intermediate": [
        "Genuine counterpoint — at least one substantial passage where two or",
        "more parts move independently against each other rather than in block",
        "rhythm. At least one of: a meter change, triplets against duple, or a",
        "modulation. At least one exposed solo line, and articulation and",
        "dynamics marked throughout rather than left blank.",
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

    lines += [
        "",
        f"Return every one of the {len(roster)} parts, in the order listed, using "
        "the exact partId given in brackets.",
    ]
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


RESPONSE_SCHEMA = _obj({
    "title": {"type": "string", "description": "Title of the piece."},
    "tempoText": {"type": "string", "description": "Italian tempo marking, e.g. 'Allegro con brio'."},
    "tempoBpm": {"type": "integer", "description": "Beats per minute, 30-240."},
    "beatUnit": {"type": "string", "enum": ["1/4", "1/2", "3/8", "1/8"],
                 "description": "Note value that gets the beat, for the Q: field."},
    "timeSignature": {"type": "string", "description": "e.g. '4/4', '3/4', '6/8'."},
    "concertKey": {"type": "string", "description": "Sounding key, e.g. 'G major', 'D minor'."},
    "measureCount": {"type": "integer", "description": "Measures in every part."},
    "programNote": {"type": "string",
                    "description": "2-4 sentences for the players: what the piece is "
                                   "about and how the mood is achieved."},
    "rehearsalTips": {"type": "string",
                      "description": "2-3 sentences on what this group will find hardest."},
    "parts": {"type": "array", "items": PART_SCHEMA},
    "conductor": {
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
    },
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


REPAIR_SCHEMA = _obj({"parts": {"type": "array", "items": PART_SCHEMA}})

MAX_DEFECTS_SHOWN = 60


def _parts_needing_repair(problems: list, roster: list) -> set:
    """Which partIds are implicated by the defect list."""
    needed = set()
    for part in roster:
        if any(problem.startswith(part["label"]) for problem in problems):
            needed.add(part["partId"])
    return needed


def _repair(score: dict, roster: list, problems: list, messages: list, model: str) -> dict:
    """One corrective round: hand the model its own defects and ask for a fix.

    Kept to a single round on purpose. It reliably clears arithmetic slips, and
    beyond that the model tends to start recomposing rather than correcting —
    at which point a fresh generation is the better answer anyway.
    """
    broken = _parts_needing_repair(problems, roster)
    if not broken:
        return score

    labels = [part["label"] for part in roster if part["partId"] in broken]
    expected = abc_check.units_per_bar(score.get("timeSignature"), UNIT_NOTE_LENGTH)
    shown = problems[:MAX_DEFECTS_SHOWN]
    elided = len(problems) - len(shown)

    instruction = [
        "The parts you just wrote do not add up. I counted them.",
        "",
        f"The time signature is {score['timeSignature']} and the unit note length is "
        f"L:{UNIT_NOTE_LENGTH}, so EVERY bar must total exactly {abc_check._fmt(expected)} "
        "units, where a plain letter is 1 unit, C2 is 2, C4 is 4, C/2 is a half.",
        "",
        "DEFECTS:",
    ]
    instruction += [f"  - {problem}" for problem in shown]
    if elided > 0:
        instruction.append(f"  ...and {elided} more of the same kind.")
    instruction += [
        "",
        f"Rewrite only these parts: {', '.join(labels)}.",
        "",
        "Correct the DURATIONS and any notes that are out of range. Keep the key and "
        "the character of what you "
        "already wrote — this is a correction, not a rewrite. Where a bar is too long, "
        "shorten notes or drop a repeated one; where it is too short, lengthen the last "
        "note of the bar. Add up every bar before you return it, and make sure all parts "
        "finish with the same number of bars.",
    ]

    completion = _create(
        model=model,
        messages=messages + [
            {"role": "assistant", "content": json.dumps({"parts": score["parts"]})},
            {"role": "user", "content": "\n".join(instruction)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "repaired_parts", "strict": True,
                            "schema": REPAIR_SCHEMA},
        },
        temperature=0.2,
        max_tokens=_token_budget(roster),
    )

    if completion.choices[0].finish_reason == "length":
        return score
    try:
        repaired = json.loads(completion.choices[0].message.content)["parts"]
    except (TypeError, ValueError, KeyError):
        return score

    merged = {written["partId"]: written for written in score["parts"]}
    for written in repaired:
        if written.get("partId") in broken:
            merged[written["partId"]] = written
    return {**score, "parts": [merged[part["partId"]] for part in roster
                               if part["partId"] in merged]}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# OPENAI_MODEL is configurable, and model families disagree about these two
# parameters: some want max_completion_tokens instead of max_tokens, and the
# reasoning models accept only the default temperature. Rather than keep a list
# of model names that goes stale, take OpenAI at its word — it names the
# offending parameter in the 400 — and retry without it.
UNSUPPORTED_RE = re.compile(r"(?:Unsupported|Unrecognized|Invalid) (?:parameter|value)[^']*'([^']+)'")

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


def compose(request: dict) -> dict:
    """Generate a score. ``request`` must already have passed validation."""
    roster = build_roster(request["instruments"])
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(request, roster)},
    ]

    try:
        completion = _create(
            model=model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "score", "strict": True, "schema": RESPONSE_SCHEMA},
            },
            # Some warmth helps it commit to a real melodic idea; much more and
            # the counting across parts starts to slip.
            temperature=0.8,
            max_tokens=_token_budget(roster),
        )
    except OpenAIError as error:
        raise ComposerError(_explain(error, model)) from error

    choice = completion.choices[0]
    if choice.finish_reason == "length":
        raise ComposerError(
            "The piece was cut off before it finished. Try fewer parts, or a "
            "lower playing level so the piece is shorter.", status=502)

    try:
        score = json.loads(choice.message.content)
    except (TypeError, ValueError) as error:
        raise ComposerError("The generator returned something we couldn't read. "
                            "Please try again.") from error

    # Add the bars up ourselves. If they don't work, give the model one chance to
    # fix its own arithmetic before we hand anything to a player.
    problems = validate_notation(score, roster, request["level"])
    repaired = False
    if problems:
        try:
            candidate = _repair(score, roster, problems, messages, model)
            remaining = validate_notation(candidate, roster, request["level"])
            # Only keep the repair if it actually helped.
            if len(remaining) < len(problems):
                score, problems, repaired = candidate, remaining, True
        except OpenAIError:
            pass  # The first score still stands; it just keeps its warnings.

    return {
        "title": score["title"],
        "tempoText": score["tempoText"],
        "tempoBpm": score["tempoBpm"],
        "timeSignature": score["timeSignature"],
        "concertKey": score["concertKey"],
        "measureCount": score["measureCount"],
        "programNote": score["programNote"],
        "rehearsalTips": score["rehearsalTips"],
        "conductor": score.get("conductor") if request["conductor"] else None,
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
        "warnings": _describe(problems),
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
    return f"The score generator failed: {text}"
