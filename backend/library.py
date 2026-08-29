"""Saved scores — the user's library, and the public gallery.

Generating a piece costs a slow model call and real money, so nothing is thrown
away: every score is written to Firestore the moment it is generated, before the
response goes back to the browser. Losing a tab must not lose the music.

Visibility is per piece, not per account. A score is private when it is written,
and stays private until its owner shares it. That way "share this one" never
means "share everything I have ever made" by accident.

Like profiles, these documents are written only through the Admin SDK here — the
rules in ``firestore.rules`` deny client writes outright, so visibility can only
change through an owner-checked call in this module.
"""

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from firebase import db

SCORES_COLLECTION = "scores"

PRIVATE = "private"
PUBLIC = "public"
VISIBILITIES = (PRIVATE, PUBLIC)

# The listing pages only need enough to draw a card. The engraved parts are the
# bulk of a document — a thirty-part score carries thirty ABC tunes — so they
# are fetched only when a score is actually opened.
SUMMARY_FIELDS = (
    "title", "tempoText", "tempoBpm", "timeSignature", "concertKey",
    "measureCount", "programNote", "visibility", "brief", "ownerName", "uid",
)


class LibraryError(Exception):
    """A saved-score request that can be reported to the user as-is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def save(uid: str, owner_name: str, score: dict, brief: dict) -> str:
    """Store a freshly generated score, private, and return its id."""
    doc_ref = db().collection(SCORES_COLLECTION).document()
    doc_ref.set({
        "uid": uid,
        "ownerName": owner_name,
        "visibility": PRIVATE,
        "score": score,
        "brief": brief,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        # Denormalised out of `score` so a listing can be built without reading
        # every part's notation. Kept in step by this function alone.
        **{field: score.get(field) for field in ("title", "tempoText", "tempoBpm",
                                                 "timeSignature", "concertKey",
                                                 "measureCount", "programNote")},
        "partCount": len(score.get("parts") or []),
    })
    return doc_ref.id


def _summary(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    created = data.get("createdAt")
    return {
        "id": snapshot.id,
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else None,
        "partCount": data.get("partCount", 0),
        "instruments": _instrument_summary(data),
        **{field: data.get(field) for field in SUMMARY_FIELDS},
    }


def _instrument_summary(data: dict) -> list:
    """["Flute ×2", "Cello"] — what the piece is written for, for the card."""
    brief = data.get("brief") or {}
    chosen = brief.get("instruments") or []
    return [
        f"{entry['name']} ×{entry['parts']}" if entry.get("parts", 1) > 1 else entry["name"]
        for entry in chosen if isinstance(entry, dict) and entry.get("name")
    ]


def _newest_first(rows: list) -> list:
    """Sort in Python rather than Firestore.

    An ``order_by`` alongside a ``where`` needs a composite index created by
    hand in the console, and a first-run 500 because an index is missing is a
    miserable way to meet a new feature. Libraries are small; sorting here costs
    nothing and the app works the moment it is deployed.
    """
    return sorted(rows, key=lambda row: row.get("createdAt") or "", reverse=True)


def _mine(uid: str):
    return db().collection(SCORES_COLLECTION).where(filter=FieldFilter("uid", "==", uid))


def list_for_user(uid: str) -> list:
    """Everything this user has generated, newest first."""
    query = _mine(uid)
    return _newest_first([_summary(doc) for doc in query.stream()])


def list_public(limit: int = 100) -> list:
    """Every piece anyone has shared, newest first."""
    query = db().collection(SCORES_COLLECTION).where(
        filter=FieldFilter("visibility", "==", PUBLIC))
    return _newest_first([_summary(doc) for doc in query.stream()])[:limit]


def get(score_id: str, uid: str | None) -> dict:
    """One score in full, if ``uid`` is allowed to see it.

    A missing document and a private one belonging to someone else are the same
    404 on purpose: a stranger should not be able to probe which ids exist.
    """
    snapshot = db().collection(SCORES_COLLECTION).document(score_id).get()
    if not snapshot.exists:
        raise LibraryError("That piece isn't here — it may have been deleted.", 404)

    data = snapshot.to_dict() or {}
    mine = data.get("uid") == uid
    if not mine and data.get("visibility") != PUBLIC:
        raise LibraryError("That piece isn't here — it may have been deleted.", 404)

    return {**_summary(snapshot), "score": data.get("score") or {}, "mine": mine}


def _owned(score_id: str, uid: str):
    doc_ref = db().collection(SCORES_COLLECTION).document(score_id)
    snapshot = doc_ref.get()
    if not snapshot.exists or (snapshot.to_dict() or {}).get("uid") != uid:
        raise LibraryError("That piece isn't here — it may have been deleted.", 404)
    return doc_ref


def set_visibility(score_id: str, uid: str, visibility: str) -> dict:
    """Share one piece, or take it back. Owner only."""
    if visibility not in VISIBILITIES:
        raise LibraryError("Visibility must be 'private' or 'public'.")

    doc_ref = _owned(score_id, uid)
    doc_ref.update({"visibility": visibility, "updatedAt": firestore.SERVER_TIMESTAMP})
    return _summary(doc_ref.get())


def set_all_visibility(uid: str, visibility: str) -> int:
    """Share, or unshare, this user's whole library. Returns how many changed."""
    if visibility not in VISIBILITIES:
        raise LibraryError("Visibility must be 'private' or 'public'.")

    batch = db().batch()
    changed = pending = 0
    for doc in _mine(uid).stream():
        if (doc.to_dict() or {}).get("visibility") == visibility:
            continue
        batch.update(doc.reference, {"visibility": visibility,
                                     "updatedAt": firestore.SERVER_TIMESTAMP})
        changed += 1
        pending += 1
        # Firestore caps a batch at 500 writes, so commit and start a fresh one
        # rather than failing outright on a very large library.
        if pending == 400:
            batch.commit()
            batch = db().batch()
            pending = 0

    if pending:
        batch.commit()
    return changed


def delete(score_id: str, uid: str) -> None:
    """Remove a piece for good. Owner only."""
    _owned(score_id, uid).delete()
