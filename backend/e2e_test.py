"""End-to-end: POST /api/signup over HTTP, then read it back from real Firestore.

Unlike smoke_test.py, this one talks to your actual Firebase project. Start the
server first, then run it:

    python backend/app.py          # in one terminal
    python backend/e2e_test.py     # in another

It creates a clearly-marked test account (aura-e2e-delete-me@example.com) and
deletes it — Auth user *and* Firestore document — in a finally block, whatever
happens. If cleanup ever fails it prints the uid to remove by hand.
"""
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from firebase import init_firebase, db
from firebase_admin import auth as fb_auth

BASE = "http://127.0.0.1:5001"
TEST_EMAIL = "aura-e2e-delete-me@example.com"

ok = []
def check(label, cond, extra=""):
    ok.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  -> ' + str(extra) if not cond else ''}")

# python.org builds on macOS don't trust the system roots, and urllib (unlike
# requests) doesn't pick up certifi on its own. Point it at certifi explicitly so
# the HTTPS call to Google works without a `Install Certificates.command` detour.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()


def call(path, payload=None, method="GET", token=None, base=BASE):
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    context = _SSL_CONTEXT if base.startswith("https") else None
    try:
        with urllib.request.urlopen(req, context=context) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def post(path, payload):
    return call(path, payload, method="POST")


def web_api_key():
    """Read the browser's Firebase apiKey out of frontend/js/firebase-config.js."""
    config = (BACKEND_DIR.parent / "frontend" / "js" / "firebase-config.js").read_text()
    match = re.search(r'apiKey:\s*"([^"]+)"', config)
    return match.group(1) if match and "REPLACE_ME" not in match.group(1) else None


def sign_in_like_the_browser(email, password, api_key):
    """Exchange email+password for a real ID token via Identity Toolkit.

    This is the same REST call the Firebase Web SDK makes on the login page, so
    the token it returns is exactly what a signed-in browser would send us.
    """
    return call(
        f"/v1/accounts:signInWithPassword?key={api_key}",
        {"email": email, "password": password, "returnSecureToken": True},
        method="POST",
        base="https://identitytoolkit.googleapis.com",
    )


init_firebase()

PAYLOAD = {
    "email": TEST_EMAIL, "password": "auraTest2026", "confirm": "auraTest2026",
    "birthdate": "2004-07-21", "gender": "Female", "nationality": "South Korea",
    "affiliation": "college", "instrument": "Violin", "genre": "Classical",
}

# Clear any leftover from a previous run.
try:
    stale = fb_auth.get_user_by_email(TEST_EMAIL)
    fb_auth.delete_user(stale.uid)
    db().collection("users").document(stale.uid).delete()
    print("(cleared a leftover test account)")
except fb_auth.UserNotFoundError:
    pass

uid = None
try:
    status, body = post("/api/signup", PAYLOAD)
    check("POST /api/signup -> 201", status == 201, (status, body))
    if status != 201:
        raise SystemExit(1)
    uid = body["uid"]
    print(f"\n  created uid: {uid}\n")

    # --- Firebase Auth ---
    user = fb_auth.get_user(uid)
    check("Auth user exists in Firebase", user is not None)
    check("  email correct", user.email == TEST_EMAIL, user.email)
    check("  displayName == email (username)", user.display_name == TEST_EMAIL, user.display_name)

    # --- Firestore ---
    snap = db().collection("users").document(uid).get()
    check("Firestore doc users/%s exists" % uid, snap.exists)
    if not snap.exists:
        raise SystemExit(1)

    doc = snap.to_dict()
    print("\n  stored document:")
    for k in sorted(doc):
        print(f"    {k:12} = {doc[k]!r}")
    print()

    expected = {
        "email": TEST_EMAIL, "username": TEST_EMAIL, "birthdate": "2004-07-21",
        "gender": "Female", "nationality": "South Korea", "affiliation": "college",
        "instrument": "Violin", "genre": "Classical",
    }
    for field, want in expected.items():
        check(f"  {field} == {want!r}", doc.get(field) == want, doc.get(field))

    check("  createdAt is a server timestamp", doc.get("createdAt") is not None, doc.get("createdAt"))
    check("  password NOT stored", not any("password" in k.lower() for k in doc), list(doc))
    check("  age NOT stored (derived on read)", "age" not in doc, list(doc))
    check("  no unexpected fields",
          set(doc) == set(expected) | {"createdAt"}, set(doc) - set(expected) - {"createdAt"})

    # --- signup response derives age/label ---
    check("response age derived == 22", body["profile"]["age"] == 22, body["profile"].get("age"))
    check("response affiliationLabel",
          body["profile"]["affiliationLabel"] == "College / University",
          body["profile"].get("affiliationLabel"))

    # --- duplicate signup rejected ---
    status2, body2 = post("/api/signup", PAYLOAD)
    check("duplicate signup -> 409", status2 == 409, (status2, body2))

    # --- login exactly as the browser does, then use the token ---
    api_key = web_api_key()
    if not api_key:
        print("SKIP  login checks — set apiKey in frontend/js/firebase-config.js")
    else:
        status, login = sign_in_like_the_browser(TEST_EMAIL, PAYLOAD["password"], api_key)
        check("login with correct password -> 200", status == 200, (status, login))
        check("  returns an ID token", bool(login.get("idToken")))
        check("  token is for our uid", login.get("localId") == uid, login.get("localId"))

        bad_status, _ = sign_in_like_the_browser(TEST_EMAIL, "wrongPassword1", api_key)
        check("login with wrong password rejected", bad_status >= 400, bad_status)

        id_token = login["idToken"]
        status, me = call("/api/me", token=id_token)
        check("GET /api/me with real token -> 200", status == 200, (status, me))
        check("  returns our profile", me.get("profile", {}).get("email") == TEST_EMAIL, me)
        check("  age derived on read == 22", me.get("profile", {}).get("age") == 22,
              me.get("profile", {}).get("age"))
        check("  affiliationLabel resolved",
              me.get("profile", {}).get("affiliationLabel") == "College / University",
              me.get("profile", {}).get("affiliationLabel"))

        status, _ = call("/api/me", token="not-a-real-token")
        check("GET /api/me with junk token -> 401", status == 401, status)

        # PATCH a profile field, and confirm it lands in Firestore.
        status, patched = call("/api/me", {"instrument": "Cello"}, method="PATCH", token=id_token)
        check("PATCH /api/me -> 200", status == 200, (status, patched))
        check("  instrument updated in response",
              patched.get("profile", {}).get("instrument") == "Cello", patched)
        check("  instrument updated in Firestore",
              db().collection("users").document(uid).get().to_dict().get("instrument") == "Cello")

        status, rejected = call("/api/me", {"instrument": "Kazoo"}, method="PATCH", token=id_token)
        check("PATCH with off-list value -> 400", status == 400, (status, rejected))
        check("  Firestore unchanged after rejected patch",
              db().collection("users").document(uid).get().to_dict().get("instrument") == "Cello")

finally:
    if uid:
        try:
            fb_auth.delete_user(uid)
            db().collection("users").document(uid).delete()
            gone = not db().collection("users").document(uid).get().exists
            check("cleanup: test account + doc deleted", gone)
        except Exception as e:
            print(f"CLEANUP FAILED — remove uid {uid} manually: {e}")

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
