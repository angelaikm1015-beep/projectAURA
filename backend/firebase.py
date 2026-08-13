"""Firebase Admin SDK setup and the bearer-token guard for protected routes.

The Admin SDK bypasses Firestore security rules, so this backend is the only
writer of user documents; the rules in ``firestore.rules`` deny client writes.
"""

import os
from functools import wraps
from pathlib import Path

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials, firestore
from flask import g, jsonify, request

BACKEND_DIR = Path(__file__).resolve().parent

_db = None


def init_firebase() -> None:
    """Initialise the Admin SDK once, from an explicit key file or ADC.

    Set ``FIREBASE_CREDENTIALS`` to a service-account JSON path, or rely on
    ``GOOGLE_APPLICATION_CREDENTIALS`` / the ambient credentials that Cloud Run
    and App Engine provide.
    """
    global _db
    if firebase_admin._apps:
        return

    key_path = os.environ.get("FIREBASE_CREDENTIALS")
    if key_path:
        # Resolve relative paths against backend/, not the current directory, so
        # the server starts the same way from anywhere.
        path = Path(key_path).expanduser()
        if not path.is_absolute():
            path = (BACKEND_DIR / path).resolve()
        if not path.exists():
            raise RuntimeError(
                f"FIREBASE_CREDENTIALS points at a missing file: {key_path} "
                f"(looked in {path})"
            )
        cred = credentials.Certificate(str(path))
    else:
        cred = credentials.ApplicationDefault()

    options = {}
    if os.environ.get("FIREBASE_PROJECT_ID"):
        options["projectId"] = os.environ["FIREBASE_PROJECT_ID"]

    firebase_admin.initialize_app(cred, options)
    _db = firestore.client()


def db():
    """The Firestore client, initialising Firebase on first use."""
    global _db
    if _db is None:
        init_firebase()
        _db = firestore.client()
    return _db


def require_auth(view):
    """Reject requests without a valid Firebase ID token.

    On success, ``g.uid`` and ``g.token`` hold the verified identity.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"message": "Missing or malformed Authorization header."}), 401

        try:
            decoded = firebase_auth.verify_id_token(header[len("Bearer "):])
        except firebase_auth.ExpiredIdTokenError:
            return jsonify({"message": "Your session expired. Please log in again."}), 401
        except Exception:
            return jsonify({"message": "Invalid authentication token."}), 401

        g.uid = decoded["uid"]
        g.token = decoded
        return view(*args, **kwargs)

    return wrapper
