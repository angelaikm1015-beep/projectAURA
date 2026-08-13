"""AURA backend — Flask + Firebase Auth + Firestore.

Split of responsibilities:

* **Signup** goes through this API. Flask validates the whole payload, creates
  the Firebase Auth user with the Admin SDK, then writes the profile document.
  Doing it in that order means a rejected profile never leaves an orphan account.
* **Login** happens client-side against Firebase Auth, so the password never
  reaches this server and Firebase owns session and token refresh.
* **Everything after login** is an authenticated call here with the Firebase ID
  token in ``Authorization: Bearer <token>``.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from firebase_admin import auth as firebase_auth
from firebase_admin import firestore
from google.api_core import exceptions as google_exceptions
from werkzeug.exceptions import HTTPException

import options
import validators
from firebase import db, init_firebase, require_auth

BACKEND_DIR = Path(__file__).resolve().parent
# The React app is a Vite build; Flask serves the compiled output. In dev you'd
# normally run `npm run dev` instead and let Vite proxy /api here.
FRONTEND_DIR = BACKEND_DIR.parent / "frontend" / "dist"
USERS_COLLECTION = "users"

load_dotenv(BACKEND_DIR / ".env")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5001,http://localhost:5173").split(",")
        if origin.strip()
    ]
    CORS(app, resources={r"/api/*": {"origins": allowed_origins}})

    init_firebase()
    register_routes(app)
    return app


def register_routes(app: Flask) -> None:

    # ---------- frontend ----------

    @app.get("/")
    def index():
        return serve_spa()

    # ---------- public API ----------

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/options")
    def get_options():
        """Dropdown contents for the signup form."""
        return jsonify(options.as_dict())

    @app.post("/api/signup")
    def signup():
        data = request.get_json(silent=True) or {}

        errors = validators.validate_signup(data)
        if errors:
            return jsonify({"message": "Please fix the highlighted fields.",
                            "errors": errors}), 400

        email = validators.normalize_email(data["email"])

        try:
            user = firebase_auth.create_user(
                email=email,
                password=data["password"],
                display_name=email,  # email doubles as the username
            )
        except firebase_auth.EmailAlreadyExistsError:
            return jsonify({
                "message": "An account with this email already exists.",
                "errors": {"email": "An account with this email already exists."},
            }), 409

        profile = validators.build_profile(data, email)
        try:
            db().collection(USERS_COLLECTION).document(user.uid).set(
                {**profile, "createdAt": firestore.SERVER_TIMESTAMP}
            )
        except Exception as error:
            # Don't strand an auth account with no profile behind it.
            firebase_auth.delete_user(user.uid)

            # A missing database is a setup mistake, not a bug — say so plainly
            # instead of hiding it behind a generic 500.
            if isinstance(error, google_exceptions.NotFound) and "database" in str(error):
                app.logger.error("Firestore is not set up: %s", error)
                return jsonify({
                    "message": "Firestore isn't set up for this Firebase project yet. "
                               "Create the database in the Firebase console "
                               "(Build > Firestore Database > Create database), then retry.",
                }), 503
            raise

        return jsonify({"uid": user.uid, "profile": _derive(profile)}), 201

    # ---------- authenticated API ----------

    @app.get("/api/me")
    @require_auth
    def get_me():
        snapshot = db().collection(USERS_COLLECTION).document(g.uid).get()
        if not snapshot.exists:
            return jsonify({"message": "No profile found for this account."}), 404
        return jsonify({"uid": g.uid, "profile": _serialize(snapshot)})

    @app.patch("/api/me")
    @require_auth
    def update_me():
        data = request.get_json(silent=True) or {}

        doc_ref = db().collection(USERS_COLLECTION).document(g.uid)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            return jsonify({"message": "No profile found for this account."}), 404

        # Only profile fields are editable here; email and password are managed
        # through Firebase Auth, not by writing to the document.
        editable = ("birthdate", "gender", "nationality", "affiliation", "instrument", "genre")
        updates = {field: data[field] for field in editable if field in data}
        if not updates:
            return jsonify({"message": "No editable fields were provided."}), 400

        errors = validators.validate_profile({**snapshot.to_dict(), **updates})
        errors = {field: message for field, message in errors.items() if field in updates}
        if errors:
            return jsonify({"message": "Please fix the highlighted fields.",
                            "errors": errors}), 400

        doc_ref.update({**updates, "updatedAt": firestore.SERVER_TIMESTAMP})
        return jsonify({"uid": g.uid, "profile": _serialize(doc_ref.get())})

    # ---------- errors ----------

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"message": "Not found."}), 404
        # Any other path is a client-side route (/login, /signup, ...) — hand it
        # to React and let the router decide.
        return serve_spa()

    @app.errorhandler(Exception)
    def unhandled(error):
        # Let Flask's own 4xx/5xx responses (405, 413, ...) through untouched;
        # only genuine crashes become a generic 500.
        if isinstance(error, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"message": error.description}), error.code
            return error
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        return jsonify({"message": "Something went wrong on our end."}), 500


def serve_spa():
    """Return the built React app, or an actionable message if it isn't built.

    Forgetting `npm run build` otherwise shows up as a bare 404, which looks like
    a routing bug rather than a missing step.
    """
    if not (FRONTEND_DIR / "index.html").exists():
        return jsonify({
            "message": "The React app hasn't been built yet. Run `npm install && npm run build` "
                       "in frontend/, or use `npm run dev` for the Vite dev server on :5173.",
        }), 503
    return send_from_directory(FRONTEND_DIR, "index.html")


AFFILIATION_LABELS = {item["value"]: item["label"] for item in options.AFFILIATIONS}


def _derive(profile: dict) -> dict:
    """Add read-only fields the client shouldn't have to compute.

    Age comes from birthdate on every read, so it can never go stale.
    """
    return {
        **profile,
        "age": validators.age_from_birthdate(profile.get("birthdate")),
        "affiliationLabel": AFFILIATION_LABELS.get(profile.get("affiliation")),
    }


def _serialize(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    for field in ("createdAt", "updatedAt"):
        value = data.get(field)
        if hasattr(value, "isoformat"):
            data[field] = value.isoformat()
    return _derive(data)


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5001)),
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
