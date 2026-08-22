"""Server-side validation for signup and profile updates.

Every validator returns ``{field: message}``; an empty dict means valid. The
frontend runs the same checks for fast feedback, but these are the ones that
decide whether an account is created.
"""

import re
from datetime import date

import options

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

MIN_AGE = 10
MAX_AGE = 120
MIN_PASSWORD_LENGTH = 8


def normalize_email(value) -> str:
    return str(value or "").strip().lower()


def age_from_birthdate(iso: str):
    """Whole years from an ISO ``yyyy-mm-dd`` string, or None if unparseable."""
    try:
        birth = date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def validate_email(value) -> str | None:
    email = normalize_email(value)
    if not email:
        return "Email is required."
    if not EMAIL_RE.match(email):
        return "Enter a valid email address."
    return None


def validate_password(value) -> str | None:
    password = value if isinstance(value, str) else ""
    if not password:
        return "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if not re.search(r"[A-Za-z]", password):
        return "Password must include a letter."
    if not re.search(r"[0-9]", password):
        return "Password must include a number."
    return None


def validate_birthdate(value) -> str | None:
    if not value:
        return "Date of birth is required."
    age = age_from_birthdate(value)
    if age is None:
        return "Enter a valid date."
    if age < 0:
        return "Date of birth cannot be in the future."
    if age < MIN_AGE:
        return f"You must be at least {MIN_AGE} years old to join AURA."
    if age > MAX_AGE:
        return "Enter a valid date of birth."
    return None


def _validate_choice(value, allowed: set, missing: str) -> str | None:
    if not value:
        return missing
    if value not in allowed:
        return "Choose one of the listed options."
    return None


def validate_profile(data: dict) -> dict:
    """Validate the profile half of a signup (everything but the credentials)."""
    return _compact({
        "birthdate": validate_birthdate(data.get("birthdate")),
        "gender": _validate_choice(
            data.get("gender"), options.GENDER_VALUES, "Select a gender."),
        "nationality": _validate_choice(
            data.get("nationality"), options.NATIONALITY_VALUES, "Select a nationality."),
        "affiliation": _validate_choice(
            data.get("affiliation"), options.AFFILIATION_VALUES, "Select where you play."),
        "instrument": _validate_choice(
            data.get("instrument"), options.INSTRUMENT_VALUES, "Select an instrument."),
        "genre": _validate_choice(
            data.get("genre"), options.GENRE_VALUES, "Select a genre."),
    })


def validate_signup(data: dict) -> dict:
    """Validate a full signup payload: credentials plus profile."""
    password = data.get("password")
    confirm = data.get("confirm")

    password_error = validate_password(password)
    if not confirm:
        confirm_error = "Please re-enter your password."
    elif confirm != password:
        confirm_error = "Passwords do not match."
    else:
        confirm_error = None

    errors = {
        "email": validate_email(data.get("email")),
        "password": password_error,
        "confirm": confirm_error,
    }
    errors.update(validate_profile(data))
    return _compact(errors)


def build_profile(data: dict, email: str) -> dict:
    """Shape the validated payload into the Firestore document body.

    Only ``birthdate`` is stored — age is derived on read so it can't go stale.
    """
    return {
        "email": email,
        "username": email,  # email doubles as the username
        "birthdate": str(data["birthdate"]),
        "gender": data["gender"],
        "nationality": data["nationality"],
        "affiliation": data["affiliation"],
        "instrument": data["instrument"],
        "genre": data["genre"],
    }


def _compact(errors: dict) -> dict:
    return {field: message for field, message in errors.items() if message}


# ---------------------------------------------------------------------------
# Composition requests
# ---------------------------------------------------------------------------

MAX_NOTES_LENGTH = 500


def validate_instruments(value) -> str | None:
    """Check the ``[{name, parts}, ...]`` roster from the compose form.

    The part count is the "Violin 1, 2, 3" case: one instrument, several
    independent lines. Each instrument caps its own division (a drum set can't
    be split four ways) and the whole score caps total parts, because every
    extra part is another full staff the model has to keep aligned.
    """
    if not isinstance(value, list) or not value:
        return "Pick at least one instrument."

    total = 0
    seen = set()
    for entry in value:
        if not isinstance(entry, dict):
            return "Choose one of the listed instruments."

        name = entry.get("name")
        spec = options.INSTRUMENT_SPECS.get(name)
        if spec is None:
            return "Choose one of the listed instruments."
        if name in seen:
            return f"{name} is listed twice — set the number of parts instead."
        seen.add(name)

        parts = entry.get("parts", 1)
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            return f"How many {name} parts? Choose at least 1."
        if parts > spec["maxParts"]:
            return f"{name} can be split into at most {spec['maxParts']} part(s)."
        total += parts

    if total > options.MAX_TOTAL_PARTS:
        return (f"That's {total} parts — {options.MAX_TOTAL_PARTS} is the most "
                "we can write in one piece.")
    return None


def validate_composition(data: dict) -> dict:
    """Validate a compose request. Empty dict means it's ready for the model."""
    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        notes_error = "Extra notes must be text."
    elif notes and len(notes) > MAX_NOTES_LENGTH:
        notes_error = f"Keep extra notes under {MAX_NOTES_LENGTH} characters."
    else:
        notes_error = None

    return _compact({
        "instruments": validate_instruments(data.get("instruments")),
        "ensembleGroup": _validate_choice(
            data.get("ensembleGroup"), options.ENSEMBLE_GROUP_VALUES,
            "Select who is playing."),
        "level": _validate_choice(
            data.get("level"), options.LEVEL_VALUES, "Select a playing level."),
        "mood": _validate_choice(
            data.get("mood"), options.MOOD_VALUES, "Select a mood."),
        "conductor": None if isinstance(data.get("conductor"), bool)
                     else "Say whether there's a conductor.",
        "notes": notes_error,
    })
