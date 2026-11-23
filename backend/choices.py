"""The optional choices on the compose form, and the instruments GPT picks.

Everything here is optional. A user who leaves a choice on "Auto" gets what the
model thinks the mood calls for, exactly as before; a user who picks one gets it
for certain. "For certain" is enforced where it can be: a chosen time signature
or key becomes a one-value enum in the plan's JSON schema (``constrain``), so
the model cannot answer with anything else. Length can't be a schema rule —
the model picks the tempo, and the measure count follows from it — so it is
stated as a target the plan is asked to meet.

The other half is the instruments. A user can tick them one by one, or say only
how big the orchestra is and let the model choose an ensemble that suits the
mood and the players (``choose_instruments``). That call is short and cheap; if
it fails, a standard orchestra of that size is used, so the piece still gets
written.
"""

import copy
import json
import logging
import os

import options

_log = logging.getLogger(__name__)

TIME_SIGNATURES = ("2/4", "3/4", "4/4", "6/8", "12/8", "5/4")
KEY_ROOTS = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
KEY_MODES = ("major", "minor")
# A minor key is spelled the way it is written: C# minor, not D-flat minor.
MINOR_SPELLING = {"Db": "C#", "Ab": "G#"}
LENGTH_LIMITS = {"minutes": (1, 10), "bars": (16, 200)}
INSTRUMENT_MODES = ("manual", "auto")

# How big an orchestra "Let GPT choose" writes for, in parts (printed lines).
ORCHESTRA_SCALES = {
    "chamber": {"label": "Chamber group", "parts": (4, 7),
                "description": "Four to seven players: a string quartet with a wind "
                               "or two, a wind quintet, a small mixed group."},
    "small": {"label": "Small orchestra", "parts": (8, 13),
              "description": "Eight to thirteen parts: strings with pairs of winds, "
                             "horns, perhaps timpani."},
    "full": {"label": "Full orchestra", "parts": (14, 22),
             "description": "Fourteen to twenty-two parts: full strings, woodwinds, "
                            "brass, timpani and percussion."},
}

# What each size gets when the model can't be asked: the classical orchestra,
# trimmed to fit.
PRESETS = {
    "chamber": [("Violin", 2), ("Viola", 1), ("Cello", 1), ("Flute", 1), ("Clarinet", 1)],
    "small": [("Flute", 1), ("Oboe", 1), ("Clarinet", 1), ("Bassoon", 1), ("French horn", 2),
              ("Violin", 2), ("Viola", 1), ("Cello", 1), ("Double bass", 1)],
    "full": [("Flute", 2), ("Oboe", 2), ("Clarinet", 2), ("Bassoon", 2), ("French horn", 2),
             ("Trumpet", 2), ("Trombone", 1), ("Timpani", 1), ("Violin", 2), ("Viola", 1),
             ("Cello", 1), ("Double bass", 1)],
}

CHOOSE_TIMEOUT = 180


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _length(value):
    """{"unit", "value"} -> (unit, int) or None if absent."""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        return "bad"
    unit, amount = value.get("unit"), value.get("value")
    if unit not in LENGTH_LIMITS or not isinstance(amount, int) or isinstance(amount, bool):
        return "bad"
    low, high = LENGTH_LIMITS[unit]
    return (unit, amount) if low <= amount <= high else "bad"


def validate(data: dict) -> dict:
    """Errors for the optional fields, by field name; only the ones that are wrong."""
    errors = {}
    if data.get("timeSignature") not in (None, "", *TIME_SIGNATURES):
        errors["timeSignature"] = "Choose one of the listed time signatures."
    if data.get("keyRoot") not in (None, "", *KEY_ROOTS):
        errors["keyRoot"] = "Choose one of the listed keys."
    if data.get("keyMode") not in (None, "", *KEY_MODES):
        errors["keyMode"] = "Choose major or minor."
    if _length(data.get("length")) == "bad":
        unit = (data.get("length") or {}).get("unit") if isinstance(data.get("length"), dict) \
            else None
        low, high = LENGTH_LIMITS.get(unit, (1, 10))
        errors["length"] = (f"Choose between {low} and {high} {unit}." if unit in LENGTH_LIMITS
                            else "Choose a length in minutes or bars.")
    if data.get("instrumentMode", "manual") not in INSTRUMENT_MODES:
        errors["instrumentMode"] = "Choose the instruments yourself or let GPT choose."
    if data.get("instrumentMode") == "auto" and data.get("orchestraScale") not in ORCHESTRA_SCALES:
        errors["orchestraScale"] = "Choose how big the orchestra should be."
    return errors


def auto_instruments(data: dict) -> bool:
    return data.get("instrumentMode") == "auto"


# ---------------------------------------------------------------------------
# What the plan is told, and held to
# ---------------------------------------------------------------------------

def key_name(root: str, mode: str) -> str:
    if mode == "minor":
        root = MINOR_SPELLING.get(root, root)
    return f"{root} {mode}"


def allowed_keys(request: dict):
    """The home keys the plan may choose from, or None for any."""
    root, mode = request.get("keyRoot") or None, request.get("keyMode") or None
    if root and mode:
        return [key_name(root, mode)]
    if root:
        return [key_name(root, "major"), key_name(root, "minor")]
    if mode:
        return [key_name(r, mode) for r in KEY_ROOTS]
    return None


def constrain(schema: dict, request: dict) -> dict:
    """The plan schema with the user's choices made into one-value enums."""
    keys = allowed_keys(request)
    time_signature = request.get("timeSignature") or None
    if not keys and not time_signature:
        return schema
    schema = copy.deepcopy(schema)
    fields = schema["properties"]
    if time_signature and "timeSignature" in fields:
        fields["timeSignature"]["enum"] = [time_signature]
    if keys and "concertKey" in fields:
        fields["concertKey"]["enum"] = keys
    return schema


def length_target(request: dict):
    return _length(request.get("length")) or None


def length_line(request: dict):
    """The LENGTH line for a chosen length, or None to keep the level's default."""
    target = length_target(request)
    if not target:
        return None
    unit, amount = target
    if unit == "bars":
        return f"LENGTH: {amount} measures in total (the user chose this; keep to it)."
    plural = "s" if amount != 1 else ""
    return (f"LENGTH: about {amount} minute{plural} of music (the user chose this). Work out "
            "the measure count from your tempo: measures = minutes x beats per minute / "
            f"beats per measure — e.g. {amount} minute{plural} at 96 bpm in 4/4 is about "
            f"{round(amount * 96 / 4)} measures.")


def choice_lines(request: dict) -> list:
    """What the user fixed, said plainly to the planner."""
    lines = []
    if request.get("timeSignature"):
        lines.append(f"  * Time signature: {request['timeSignature']} throughout.")
    keys = allowed_keys(request)
    if keys and len(keys) == 1:
        lines.append(f"  * Home key: {keys[0]}.")
    elif request.get("keyRoot"):
        lines.append(f"  * Home key on {request['keyRoot']} — major or minor as the mood asks.")
    elif request.get("keyMode"):
        lines.append(f"  * A {request['keyMode']} key — which one is your choice.")
    if not lines:
        return []
    return ["THE USER CHOSE (these are fixed):", *lines]


# ---------------------------------------------------------------------------
# Choosing the instruments
# ---------------------------------------------------------------------------

def _names() -> list:
    return [name for group in options.SCORE_INSTRUMENTS for name in group["options"]]


def _schema() -> dict:
    entry = {"type": "object", "additionalProperties": False, "required": ["name", "parts"],
             "properties": {"name": {"type": "string", "enum": _names()},
                            "parts": {"type": "integer",
                                      "description": "How many separate lines: Violin "
                                                     "with 2 parts is Violin I and II."}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["instruments", "why"],
            "properties": {"instruments": {"type": "array", "items": entry},
                           "why": {"type": "string",
                                   "description": "One sentence: why this ensemble suits "
                                                  "the piece."}}}


def _prompt(data: dict, scale: dict) -> str:
    mood = options.MOODS_BY_VALUE[data["mood"]]
    level = options.LEVELS_BY_VALUE[data["level"]]
    group = options.ENSEMBLE_GROUPS_BY_VALUE[data["ensembleGroup"]]
    low, high = scale["parts"]
    caps = ", ".join(f"{name} {spec['maxParts']}" for name, spec in
                     options.INSTRUMENT_SPECS.items() if spec["maxParts"] > 1)
    lines = [
        f"Choose the instruments for a new piece: a {scale['label'].lower()} of {low} to "
        f"{high} parts in total (a part is one printed line; Violin with 2 parts is Violin I "
        "and II).",
        f"Mood: {mood['label']} — {mood['guidance']}",
        f"Players: {group['label']}, {level['label']} level — {level['guidance']}",
    ]
    if data.get("notes"):
        lines.append(f"The user also asked: {data['notes']}")
    lines += [
        "",
        "Choose as an orchestrator would for this mood and these players:",
        "  * A balanced ensemble whose sections can carry a tune, a bass and the",
        "    harmony between them; strings are the core of most orchestras.",
        "  * Colour that serves the mood — a harp or vibraphone for dreamy",
        "    music, brass and timpani for epic music — but nothing exotic for its",
        "    own sake. Guitars, saxophones, drum set, synthesizer and voices only if",
        "    the mood or the user's request clearly calls for them.",
        "  * Instruments these players are likely to have.",
        f"  * Most parts per instrument: {caps}; every other instrument 1.",
    ]
    return "\n".join(lines)


def _tidy(chosen: list, scale: dict) -> list:
    """Known instruments, each once, within its part cap, in score order, to size."""
    order = {name: i for i, name in enumerate(_names())}
    merged = {}
    for entry in chosen:
        name = entry.get("name") if isinstance(entry, dict) else None
        spec = options.INSTRUMENT_SPECS.get(name)
        if not spec:
            continue
        try:
            parts = int(entry.get("parts") or 1)
        except (TypeError, ValueError):
            parts = 1
        merged[name] = max(1, min(spec["maxParts"], merged.get(name, 0) + parts))
    roster = [{"name": name, "parts": merged[name]}
              for name in sorted(merged, key=lambda n: order[n])]
    _, high = scale["parts"]
    while sum(e["parts"] for e in roster) > high and roster:
        biggest = max(roster, key=lambda e: e["parts"])
        if biggest["parts"] > 1:
            biggest["parts"] -= 1
        else:
            roster.pop()
    return roster


def preset(scale_name: str) -> list:
    return [{"name": name, "parts": parts} for name, parts in PRESETS[scale_name]]


def choose_instruments(data: dict) -> tuple:
    """([{name, parts}], why): the ensemble GPT picks for this brief and size.

    Falls back to a standard orchestra of that size if the call fails or comes
    back too small to be the size asked for.
    """
    import composer                      # here: composer imports this module

    scale = ORCHESTRA_SCALES[data["orchestraScale"]]
    model = os.environ.get("OPENAI_MODEL", composer.DEFAULT_MODEL)
    try:
        completion = composer._create(
            model=model,
            messages=[{"role": "system", "content": "You are an experienced orchestrator."},
                      {"role": "user", "content": _prompt(data, scale)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "ensemble", "strict": True,
                                             "schema": _schema()}},
            temperature=0.7,
            max_tokens=4000,
            reasoning_effort="low",
            timeout=CHOOSE_TIMEOUT,
        )
        reply = json.loads(completion.choices[0].message.content)
        roster = _tidy(reply.get("instruments") or [], scale)
        low, _ = scale["parts"]
        if sum(e["parts"] for e in roster) >= low:
            return roster, str(reply.get("why") or "").strip()
        _log.warning("The chosen ensemble was too small; using the standard one.")
    except Exception as error:            # any failure: the preset still writes the piece
        _log.warning("Choosing the instruments failed, using the standard ensemble: %s",
                     error)
    return preset(data["orchestraScale"]), ""
