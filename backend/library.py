"""Saved scores — the user's library, and the public gallery.

Generating a piece costs a slow model call and real money, so nothing is thrown
away: every score is written to Firestore the moment it is generated, before the
response goes back to the browser. Losing a tab must not lose the music.

Visibility is per piece, not per account. A score is private when it is written,
and stays private until its owner shares it. That way "share this one" never
means "share everything I have ever made" by accident.

Sharing is a post, not just a flag: the owner writes a caption saying what the
piece is and why they made it, and the gallery reads as a feed of those posts,
newest share first. ``sharedAt`` is when it went public, which is not when it
was written — a piece made last month and shared today belongs at the top.

Like profiles, these documents are written only through the Admin SDK here — the
rules in ``firestore.rules`` deny client writes outright, so visibility can only
change through an owner-checked call in this module.
"""

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from firebase import db

SCORES_COLLECTION = "scores"
# One document per (user, piece): whether they liked it, whether they saved it.
# Both live together because they are the same shape of fact and a reader
# usually wants both at once.
REACTIONS_COLLECTION = "reactions"
COMMENTS_SUBCOLLECTION = "comments"

PRIVATE = "private"
PUBLIC = "public"
VISIBILITIES = (PRIVATE, PUBLIC)

# The listing pages only need enough to draw a card. The engraved parts are the
# bulk of a document — a thirty-part score carries thirty ABC tunes — so they
# are fetched only when a score is actually opened.
SUMMARY_FIELDS = (
    "title", "tempoText", "tempoBpm", "timeSignature", "concertKey",
    "measureCount", "programNote", "visibility", "brief", "ownerName", "uid",
    "caption",
)

CAPTION_MAX = 600
COMMENT_MAX = 500


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
        "caption": "",
        "sharedAt": None,
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
        "likeCount": 0,
        "commentCount": 0,
    })
    return doc_ref.id


def _summary(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    created = data.get("createdAt")
    shared = data.get("sharedAt")
    return {
        "id": snapshot.id,
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else None,
        "sharedAt": shared.isoformat() if hasattr(shared, "isoformat") else None,
        "partCount": data.get("partCount", 0),
        "canUndo": bool(data.get("previousScore")),
        "likeCount": data.get("likeCount", 0),
        "commentCount": data.get("commentCount", 0),
        "level": (data.get("brief") or {}).get("level"),
        "ensembleGroup": (data.get("brief") or {}).get("ensembleGroup"),
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


def _newest_first(rows: list, key: str = "createdAt") -> list:
    """Sort in Python rather than Firestore.

    An ``order_by`` alongside a ``where`` needs a composite index created by
    hand in the console, and a first-run 500 because an index is missing is a
    miserable way to meet a new feature. Libraries are small; sorting here costs
    nothing and the app works the moment it is deployed.
    """
    return sorted(rows, key=lambda row: row.get(key) or row.get("createdAt") or "",
                  reverse=True)


def _mine(uid: str):
    return db().collection(SCORES_COLLECTION).where(filter=FieldFilter("uid", "==", uid))


def list_for_user(uid: str) -> list:
    """Everything this user has generated, newest first."""
    return _newest_first([_summary(doc) for doc in _mine(uid).stream()])


def list_public(uid: str | None = None, level: str | None = None,
                ensemble: str | None = None, limit: int = 100) -> dict:
    """The feed — every shared piece, most recently posted first.

    Filtering happens here rather than in the query for the same reason the
    sort does: a `where` on two fields plus an order needs a composite index
    built by hand, and the gallery is small enough that this costs nothing.
    """
    query = db().collection(SCORES_COLLECTION).where(
        filter=FieldFilter("visibility", "==", PUBLIC))
    everything = _newest_first([_summary(doc) for doc in query.stream()], "sharedAt")
    rows = [row for row in everything if _matches(row, level, ensemble)][:limit]
    return {"scores": _with_reactions(uid, rows), "facets": _facets(everything),
            "total": len(everything)}


# ---------------------------------------------------------------------------
# Filtering the feed
# ---------------------------------------------------------------------------

def _matches(row: dict, level: str | None, ensemble: str | None) -> bool:
    return ((not level or row.get("level") == level)
            and (not ensemble or row.get("ensembleGroup") == ensemble))


def _facets(rows: list) -> dict:
    """How many shared pieces sit in each category.

    Sent with the feed so the filter can show counts and grey out the choices
    that would come back empty — a filter that leads to nothing is worse than
    no filter at all.
    """
    levels, ensembles = {}, {}
    for row in rows:
        if row.get("level"):
            levels[row["level"]] = levels.get(row["level"], 0) + 1
        if row.get("ensembleGroup"):
            ensembles[row["ensembleGroup"]] = ensembles.get(row["ensembleGroup"], 0) + 1
    return {"levels": levels, "ensembleGroups": ensembles}


# ---------------------------------------------------------------------------
# Likes, saves and comments
# ---------------------------------------------------------------------------

def _reaction_ref(uid: str, score_id: str):
    # A composite id keeps it to one document per person per piece, so a like
    # can never be double-counted by a double click.
    return db().collection(REACTIONS_COLLECTION).document(f"{uid}__{score_id}")


def reactions_for(uid: str, score_ids: list) -> dict:
    """{scoreId: {liked, saved}} for the pieces on screen."""
    if not uid or not score_ids:
        return {}
    refs = [_reaction_ref(uid, score_id) for score_id in score_ids]
    found = {}
    # get_all is one round trip however many pieces are listed.
    for snapshot in db().get_all(refs):
        if not snapshot.exists:
            continue
        data = snapshot.to_dict() or {}
        found[data.get("scoreId")] = {"liked": bool(data.get("liked")),
                                      "saved": bool(data.get("saved"))}
    return found


def _with_reactions(uid: str, rows: list) -> list:
    mine = reactions_for(uid, [row["id"] for row in rows])
    return [{**row, **mine.get(row["id"], {"liked": False, "saved": False})} for row in rows]


def _toggle(uid: str, score_id: str, field: str) -> dict:
    """Flip `liked` or `saved`, and keep the piece's counter honest."""
    get(score_id, uid)  # 404s unless the reader may see it at all

    ref = _reaction_ref(uid, score_id)
    snapshot = ref.get()
    current = (snapshot.to_dict() or {}) if snapshot.exists else {}
    now_on = not current.get(field)

    ref.set({**current, "uid": uid, "scoreId": score_id, field: now_on,
             "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)

    if field == "liked":
        db().collection(SCORES_COLLECTION).document(score_id).update(
            {"likeCount": firestore.Increment(1 if now_on else -1)})

    return {field: now_on}


def toggle_like(uid: str, score_id: str) -> dict:
    result = _toggle(uid, score_id, "liked")
    snapshot = db().collection(SCORES_COLLECTION).document(score_id).get()
    return {**result, "likeCount": max(0, (snapshot.to_dict() or {}).get("likeCount", 0))}


def toggle_save(uid: str, score_id: str) -> dict:
    return _toggle(uid, score_id, "saved")


def _reacted(uid: str, field: str) -> list:
    """The pieces this user liked, or saved — whichever `field` names."""
    query = (db().collection(REACTIONS_COLLECTION)
             .where(filter=FieldFilter("uid", "==", uid))
             .where(filter=FieldFilter(field, "==", True)))
    ids = [(doc.to_dict() or {}).get("scoreId") for doc in query.stream()]
    ids = [score_id for score_id in ids if score_id]
    if not ids:
        return []

    refs = [db().collection(SCORES_COLLECTION).document(score_id) for score_id in ids]
    rows = []
    for snapshot in db().get_all(refs):
        if not snapshot.exists:
            continue
        data = snapshot.to_dict() or {}
        # A piece that has since been made private is no longer readable, unless
        # it was yours all along.
        if data.get("visibility") != PUBLIC and data.get("uid") != uid:
            continue
        rows.append(_summary(snapshot))
    return _with_reactions(uid, _newest_first(rows, "sharedAt"))


def list_liked(uid: str) -> list:
    return _reacted(uid, "liked")


def list_saved(uid: str) -> list:
    return _reacted(uid, "saved")


def list_comments(score_id: str, uid: str) -> list:
    """Oldest first, the way a conversation reads."""
    get(score_id, uid)  # visibility check
    docs = (db().collection(SCORES_COLLECTION).document(score_id)
            .collection(COMMENTS_SUBCOLLECTION).stream())
    rows = []
    for doc in docs:
        data = doc.to_dict() or {}
        created = data.get("createdAt")
        rows.append({
            "id": doc.id,
            "uid": data.get("uid"),
            "authorName": data.get("authorName"),
            "text": data.get("text"),
            "createdAt": created.isoformat() if hasattr(created, "isoformat") else None,
            "mine": data.get("uid") == uid,
        })
    return sorted(rows, key=lambda row: row.get("createdAt") or "")


def add_comment(uid: str, author_name: str, score_id: str, text: str) -> dict:
    if not isinstance(text, str):
        raise LibraryError("A comment has to be text.")
    text = text.strip()
    if not text:
        raise LibraryError("Write something first.")
    if len(text) > COMMENT_MAX:
        raise LibraryError(f"Keep comments under {COMMENT_MAX} characters.")

    get(score_id, uid)  # visibility check
    score_ref = db().collection(SCORES_COLLECTION).document(score_id)
    score_ref.collection(COMMENTS_SUBCOLLECTION).add({
        "uid": uid, "authorName": author_name, "text": text,
        "createdAt": firestore.SERVER_TIMESTAMP,
    })
    score_ref.update({"commentCount": firestore.Increment(1)})
    return {"comments": list_comments(score_id, uid)}


def delete_comment(uid: str, score_id: str, comment_id: str) -> dict:
    """Your own comment, or any comment on your own piece."""
    score_ref = db().collection(SCORES_COLLECTION).document(score_id)
    score = score_ref.get()
    comment_ref = score_ref.collection(COMMENTS_SUBCOLLECTION).document(comment_id)
    comment = comment_ref.get()
    if not comment.exists:
        raise LibraryError("That comment is already gone.", 404)

    owns_comment = (comment.to_dict() or {}).get("uid") == uid
    owns_piece = (score.to_dict() or {}).get("uid") == uid
    if not (owns_comment or owns_piece):
        raise LibraryError("You can only delete your own comments.", 403)

    comment_ref.delete()
    score_ref.update({"commentCount": firestore.Increment(-1)})
    return {"comments": list_comments(score_id, uid)}


def list_by_author(author_uid: str, viewer_uid: str | None = None) -> dict:
    """What one person has shared, newest post first.

    Only their public pieces, even when the viewer is the author — this is the
    page everyone else sees, and it would be confusing if it showed you a
    different shelf from the one you are sharing.
    """
    query = db().collection(SCORES_COLLECTION).where(
        filter=FieldFilter("uid", "==", author_uid))
    rows = [_summary(doc) for doc in query.stream()
            if (doc.to_dict() or {}).get("visibility") == PUBLIC]
    rows = _newest_first(rows, "sharedAt")

    name = rows[0]["ownerName"] if rows else None
    if not name:
        # Nothing shared, but the person may still exist and have private work.
        any_doc = next(iter(query.limit(1).stream()), None)
        if any_doc is None:
            raise LibraryError("No such person.", 404)
        name = (any_doc.to_dict() or {}).get("ownerName")

    return {
        "uid": author_uid,
        "ownerName": name or "someone",
        "scores": _with_reactions(viewer_uid, rows),
        "likesReceived": sum(row.get("likeCount", 0) for row in rows),
    }


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


def update(score_id: str, uid: str, visibility=None, caption=None) -> dict:
    """Share a piece, take it back, or reword the post. Owner only.

    Both fields are optional, so the caption can be edited without touching
    visibility and vice versa.
    """
    if visibility is not None and visibility not in VISIBILITIES:
        raise LibraryError("Visibility must be 'private' or 'public'.")
    if caption is not None:
        if not isinstance(caption, str):
            raise LibraryError("The caption has to be text.")
        caption = caption.strip()
        if len(caption) > CAPTION_MAX:
            raise LibraryError(f"Keep the caption under {CAPTION_MAX} characters.")

    doc_ref = _owned(score_id, uid)
    updates = {"updatedAt": firestore.SERVER_TIMESTAMP}
    if caption is not None:
        updates["caption"] = caption
    if visibility is not None:
        updates["visibility"] = visibility
        # Stamped on the way out, so the feed orders by when a piece was posted
        # rather than when it happened to be written.
        if visibility == PUBLIC:
            updates["sharedAt"] = firestore.SERVER_TIMESTAMP

    doc_ref.update(updates)
    return _summary(doc_ref.get())


def replace_score(score_id: str, uid: str, score: dict) -> dict:
    """Write a revised piece over the old one, keeping one step of history.

    Exactly one previous version is kept. A revision is a small edit that can
    still land badly, and being able to say "no, put it back" once is worth a
    copy; keeping every version would push a large score past Firestore's
    document limit for no real gain.
    """
    doc_ref = _owned(score_id, uid)
    previous = (doc_ref.get().to_dict() or {}).get("score")

    doc_ref.update({
        "score": score,
        "previousScore": previous,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        # The denormalised copies have to move with it or the library card and
        # the piece would disagree.
        **{field: score.get(field) for field in ("title", "tempoText", "tempoBpm",
                                                 "timeSignature", "concertKey",
                                                 "measureCount", "programNote")},
        "partCount": len(score.get("parts") or []),
    })
    return _summary(doc_ref.get())


def undo(score_id: str, uid: str) -> dict:
    """Restore the version from before the last revision."""
    doc_ref = _owned(score_id, uid)
    previous = (doc_ref.get().to_dict() or {}).get("previousScore")
    if not previous:
        raise LibraryError("There's nothing to undo on this piece.", 400)

    doc_ref.update({
        "score": previous,
        "previousScore": None,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        **{field: previous.get(field) for field in ("title", "tempoText", "tempoBpm",
                                                    "timeSignature", "concertKey",
                                                    "measureCount", "programNote")},
        "partCount": len(previous.get("parts") or []),
    })
    return previous


def set_all_visibility(uid: str, visibility: str) -> int:
    """Share, or unshare, this user's whole library. Returns how many changed."""
    if visibility not in VISIBILITIES:
        raise LibraryError("Visibility must be 'private' or 'public'.")

    batch = db().batch()
    changed = pending = 0
    for doc in _mine(uid).stream():
        if (doc.to_dict() or {}).get("visibility") == visibility:
            continue
        fields = {"visibility": visibility, "updatedAt": firestore.SERVER_TIMESTAMP}
        if visibility == PUBLIC:
            fields["sharedAt"] = firestore.SERVER_TIMESTAMP
        batch.update(doc.reference, fields)
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
