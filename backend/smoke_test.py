"""Boot the Flask app with Firebase stubbed and exercise every route.

Run it with no credentials and no network:

    python backend/smoke_test.py

Exits non-zero if any check fails. The one ERROR line in the output is the
deliberate "Firestore write blew up" case that checks the rollback.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import firebase as firebase_module
firebase_module.init_firebase = lambda: None

import app as app_module
from firebase_admin import auth as firebase_auth

client = app_module.app.test_client()
ok = []

def check(label, cond, extra=""):
    ok.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + str(extra) if not cond else ''}")

# --- public routes -------------------------------------------------------
r = client.get("/api/health")
check("GET /api/health -> 200", r.status_code == 200 and r.json == {"status": "ok"}, r.json)

r = client.get("/api/options")
check("GET /api/options -> 200", r.status_code == 200)
check("  options has all 5 lists",
      set(r.json) == {"genders", "affiliations", "instruments", "genres", "nationalities"},
      set(r.json))
check("  instruments are grouped", r.json["instruments"][0]["group"] == "Strings")

import generate_options
check("frontend/js/options-data.js is up to date with options.py",
      generate_options.is_current(),
      "run: python backend/generate_options.py")

# The React app is a Vite build, so what "/" should return depends on whether
# frontend/dist exists. Both outcomes are correct — assert the right one.
built = (app_module.FRONTEND_DIR / "index.html").exists()

r = client.get("/")
if built:
    check("GET / serves the built React app", r.status_code == 200 and b"<div id=\"root\">" in r.data,
          r.status_code)
else:
    check("GET / explains the app isn't built yet (503)",
          r.status_code == 503 and "npm run build" in r.json["message"], (r.status_code, r.json))

# Client-side routes must fall through to the SPA, not 404.
r = client.get("/signup")
check("GET /signup falls through to the SPA",
      r.status_code == (200 if built else 503), r.status_code)

# --- signup validation ---------------------------------------------------
r = client.post("/api/signup", json={})
check("POST /api/signup empty -> 400", r.status_code == 400, r.status_code)
check("  returns 9 field errors", len(r.json["errors"]) == 9, r.json.get("errors"))

GOOD = {
    "email": "  Angela@Example.COM ", "password": "violin2026", "confirm": "violin2026",
    "birthdate": "2005-03-14", "gender": "Female", "nationality": "South Korea",
    "affiliation": "college", "instrument": "Violin", "genre": "Classical",
}

r = client.post("/api/signup", json={**GOOD, "confirm": "different"})
check("POST /api/signup mismatched confirm -> 400",
      r.status_code == 400 and r.json["errors"] == {"confirm": "Passwords do not match."}, r.json)

r = client.post("/api/signup", json={**GOOD, "instrument": "Kazoo"})
check("POST /api/signup off-list instrument -> 400",
      r.status_code == 400 and "instrument" in r.json["errors"], r.json)

r = client.post("/api/signup", json={**GOOD, "birthdate": "2021-01-01"})
check("POST /api/signup underage -> 400",
      r.status_code == 400 and "birthdate" in r.json["errors"], r.json)

# --- signup happy path ---------------------------------------------------
written = {}

class FakeDoc:
    def set(self, data): written.update(data)

class FakeDb:
    def collection(self, name):
        written["_collection"] = name
        return self
    def document(self, uid):
        written["_uid"] = uid
        return FakeDoc()

with mock.patch.object(firebase_auth, "create_user",
                       return_value=mock.Mock(uid="uid-123")) as create_user, \
     mock.patch.object(app_module, "db", return_value=FakeDb()):
    r = client.post("/api/signup", json=GOOD)

check("POST /api/signup valid -> 201", r.status_code == 201, r.json)
check("  email normalized before create_user",
      create_user.call_args.kwargs["email"] == "angela@example.com", create_user.call_args)
check("  written to users/uid-123",
      written.get("_collection") == "users" and written.get("_uid") == "uid-123", written)
check("  username == email", written.get("username") == "angela@example.com", written.get("username"))
check("  no password stored", not any("password" in k.lower() for k in written), list(written))
check("  age not stored (derived on read)", "age" not in written, list(written))
check("  response derives age", isinstance(r.json["profile"]["age"], int), r.json["profile"])
check("  response derives affiliationLabel",
      r.json["profile"]["affiliationLabel"] == "College / University", r.json["profile"])

# duplicate email
with mock.patch.object(firebase_auth, "create_user",
                       side_effect=firebase_auth.EmailAlreadyExistsError("dup", None, None)):
    r = client.post("/api/signup", json=GOOD)
check("POST /api/signup duplicate -> 409",
      r.status_code == 409 and "email" in r.json["errors"], (r.status_code, r.json))

# rollback when Firestore write fails
class ExplodingDb:
    def collection(self, name): return self
    def document(self, uid): return self
    def set(self, data): raise RuntimeError("firestore down")

with mock.patch.object(firebase_auth, "create_user", return_value=mock.Mock(uid="uid-456")), \
     mock.patch.object(firebase_auth, "delete_user") as delete_user, \
     mock.patch.object(app_module, "db", return_value=ExplodingDb()):
    r = client.post("/api/signup", json=GOOD)
check("Firestore failure -> 500", r.status_code == 500, r.status_code)
check("  orphan auth user rolled back",
      delete_user.call_args and delete_user.call_args[0][0] == "uid-456", delete_user.call_args)

# --- auth guard ----------------------------------------------------------
r = client.get("/api/me")
check("GET /api/me no token -> 401", r.status_code == 401, r.status_code)

r = client.get("/api/me", headers={"Authorization": "Basic abc"})
check("GET /api/me wrong scheme -> 401", r.status_code == 401, r.status_code)

with mock.patch.object(firebase_auth, "verify_id_token", side_effect=ValueError("bad")):
    r = client.get("/api/me", headers={"Authorization": "Bearer garbage"})
check("GET /api/me bad token -> 401", r.status_code == 401, r.status_code)

class FakeSnapshot:
    exists = True
    def to_dict(self):
        return {"email": "angela@example.com", "username": "angela@example.com",
                "birthdate": "2005-03-14", "gender": "Female", "nationality": "South Korea",
                "affiliation": "high-school", "instrument": "Violin", "genre": "Classical"}

class ReadDb:
    def collection(self, n): return self
    def document(self, u): return self
    def get(self): return FakeSnapshot()

with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
     mock.patch.object(app_module, "db", return_value=ReadDb()):
    r = client.get("/api/me", headers={"Authorization": "Bearer good"})
check("GET /api/me valid token -> 200", r.status_code == 200, r.json)
check("  profile carries derived age + label",
      isinstance(r.json["profile"]["age"], int)
      and r.json["profile"]["affiliationLabel"] == "High school", r.json.get("profile"))

# --- error shapes --------------------------------------------------------
r = client.get("/api/nope")
check("unknown /api/ route -> JSON 404",
      r.status_code == 404 and r.is_json, (r.status_code, r.content_type))

r = client.delete("/api/health")
check("wrong method -> JSON 405 (not 500)",
      r.status_code == 405 and r.is_json, (r.status_code, r.content_type))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
