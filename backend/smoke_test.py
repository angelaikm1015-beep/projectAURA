"""Boot the Flask app with Firebase stubbed and exercise every route.

Run it with no credentials and no network:

    python backend/smoke_test.py

Exits non-zero if any check fails. The one ERROR line in the output is the
deliberate "Firestore write blew up" case that checks the rollback.
"""
import json
import os
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
    cond = bool(cond)
    ok.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + str(extra) if not cond else ''}")

# --- public routes -------------------------------------------------------
r = client.get("/api/health")
check("GET /api/health -> 200", r.status_code == 200 and r.json == {"status": "ok"}, r.json)

r = client.get("/api/options")
check("GET /api/options -> 200", r.status_code == 200)
check("  options has the signup lists",
      {"genders", "affiliations", "instruments", "genres", "nationalities"} <= set(r.json),
      set(r.json))
check("  options has the compose lists",
      {"scoreInstruments", "ensembleGroups", "levels", "moods", "instrumentInfo"} <= set(r.json),
      set(r.json))
check("  exactly 4 playing levels", len(r.json["levels"]) == 4, r.json["levels"])
check("  every offered instrument has an engraving spec",
      all(name in r.json["instrumentInfo"]
          for group in r.json["scoreInstruments"] for name in group["options"]))
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

# --- ABC reading -------------------------------------------------------------
# Everything above rests on these totals being right, and the syntax has more
# corners than it looks like it does.
import abc_check

FOURFOUR = abc_check.units_per_bar("4/4", "1/8")
check("a 4/4 bar at L:1/8 is 8 units", FOURFOUR == 8, FOURFOUR)
check("  3/4 is 6 and 6/8 is 6",
      abc_check.units_per_bar("3/4", "1/8") == 6 and abc_check.units_per_bar("6/8", "1/8") == 6)

for music, bars, valid, why in [
    ("G2 A2 B2 c2 | d8 |",              2, True,  "plain bars"),
    ("(A3 B) A B/2A/2 G4 |",            1, False, "slurs don't hide an overfull bar"),
    ("z8 | z8 |",                       2, True,  "whole-bar rests"),
    ("[CEG]4 [Ace]4 |",                 1, True,  "a chord lasts one duration, not three"),
    ("!f!.C2 ~D2 E2 !fermata!F2 |",     1, True,  "dynamics and decorations carry no time"),
    ("C>D E>F G2 A2 |",                 1, True,  "broken rhythm preserves the total"),
    ("{cde}G2 A2 B2 c2 |",              1, True,  "grace notes carry no time"),
    ("C/2C/2C/2C/2 C2 C2 C2 |",         1, True,  "sixteenths"),
    ("|: G2 A2 B2 c2 :| d8 |",          2, True,  "a leading repeat is not an empty bar"),
    ("[| G2 A2 B2 c2 |]",               1, True,  "'[|' is a barline, not a chord"),
    ("G2 A2 B2 c2 | d8",                2, True,  "no trailing barline"),
    ("C4 | C2 D2 E2 F2 | C4 |",         3, True,  "a pickup plus a matching final bar"),
    ("[K:D]G2 A2 B2 c2 |",              1, True,  "an inline field is not a chord"),
]:
    problems = abc_check.check_part(music, FOURFOUR, "X")
    check(f"  {why}",
          abc_check.bar_count(music) == bars and bool(problems) != valid,
          (abc_check.bar_count(music), problems))

check("  middle C is C4 and lowercase c is an octave up",
      abc_check.abc_pitch("C") == 60 and abc_check.abc_pitch("c") == 72)
check("  octave marks and accidentals apply",
      abc_check.abc_pitch("C,") == 48 and abc_check.abc_pitch("c'") == 84
      and abc_check.abc_pitch("^F") == 66 and abc_check.abc_pitch("_B") == 70)
check("  scientific pitch names parse", abc_check.parse_scientific("Bb1") == 34
      and abc_check.parse_scientific("F#3") == 54 and abc_check.parse_scientific("-") is None)
check("  a note inside a chord is range-checked too",
      bool(abc_check.check_range("[c'e'g']4 z4 |", "G3", "D5", "V")), "chord members skipped")
check("  grace notes are exempt from range",
      not abc_check.check_range("{c'e'}G2 A2 B2 c2 |", "G3", "D5", "V"))

# --- compose ---------------------------------------------------------------
import composer

r = client.post("/api/compose", json={})
check("POST /api/compose no token -> 401", r.status_code == 401, r.status_code)

COMPOSE = {
    "instruments": [{"name": "Violin", "parts": 3}, {"name": "Oboe", "parts": 1},
                    {"name": "Piano", "parts": 1}],
    "ensembleGroup": "high-school", "level": "intermediate",
    "mood": "happy", "conductor": True,
}
AUTHED = {"Authorization": "Bearer good"}
ROSTER = composer.build_roster(COMPOSE["instruments"])

def as_user(payload, **patches):
    with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
        with mock.patch.object(composer, "compose", **patches) if patches else _null():
            return client.post("/api/compose", json=payload, headers=AUTHED)

import contextlib
_null = contextlib.nullcontext

r = as_user({}, side_effect=AssertionError("must not reach the model"))
check("POST /api/compose empty -> 400", r.status_code == 400, r.status_code)
check("  flags all 5 required fields", len(r.json["errors"]) == 5, r.json.get("errors"))

r = as_user({**COMPOSE, "instruments": [{"name": "Kazoo", "parts": 1}]},
            side_effect=AssertionError("must not reach the model"))
check("POST /api/compose unnotatable instrument -> 400",
      r.status_code == 400 and "instruments" in r.json["errors"], r.json)

r = as_user({**COMPOSE, "instruments": [{"name": "Violin", "parts": 9}]},
            side_effect=AssertionError("must not reach the model"))
check("POST /api/compose too many divisi -> 400",
      r.status_code == 400 and "instruments" in r.json["errors"], r.json)

r = as_user({**COMPOSE, "conductor": "yes"},
            side_effect=AssertionError("must not reach the model"))
check("POST /api/compose non-boolean conductor -> 400",
      r.status_code == 400 and "conductor" in r.json["errors"], r.json)

# Happy path, with the model call stubbed — no network, no API key needed.
#
# Generation is two passes: a plan — a few phrases and a form — then the few
# parts the model decorates, phrase by phrase. The fake answers whichever schema
# the call asked for, so the stub follows the real control flow.
FAKE_PLAN = {
    "title": "Sunrise Run", "tempoText": "Allegro", "tempoBpm": 120, "beatUnit": "1/4",
    "timeSignature": "4/4", "concertKey": "G major",
    "programNote": "A bright opener.", "rehearsalTips": "Watch the pickup.",
    "phrases": [
        {"id": "A", "chords": ["G", "D"],
         "soprano": "B2 c2 d4 | A8 |", "alto": "G4 G4 | F8 |",
         "tenor": "D4 D4 | D8 |", "bass": "G,4 G,4 | D,8 |"},
        {"id": "B", "chords": ["Em", "G"],
         "soprano": "e4 g4 | B8 |", "alto": "B4 B4 | G8 |",
         "tenor": "G,4 E4 | D8 |", "bass": "E,4 E,4 | G,8 |"},
    ],
    "form": [
        {"label": "A", "phrase": "A", "fromBar": None, "toBar": None, "transpose": 0,
         "dynamic": "mf", "shape": "steady", "texture": "tutti", "lead": "P1",
         "description": "Statement."},
        {"label": "B", "phrase": "B", "fromBar": None, "toBar": None, "transpose": 5,
         "dynamic": "p", "shape": "crescendo", "texture": "solo", "lead": "P4",
         "description": "Oboe solo, a fourth up."},
        {"label": "A2", "phrase": "A", "fromBar": None, "toBar": None, "transpose": 0,
         "dynamic": "ff", "shape": "steady", "texture": "tutti", "lead": "P2",
         "description": "Return, tune in Violin II."},
        {"label": "Coda", "phrase": "A", "fromBar": 2, "toBar": 2, "transpose": 0,
         "dynamic": "pp", "shape": "steady", "texture": "reduced", "lead": None,
         "description": "Last bar, quietly."},
    ],
    "partPlan": [
        {"partId": "P1", "voice": "soprano", "treatment": "decorated", "role": "melody"},
        {"partId": "P2", "voice": "alto", "treatment": "plain", "role": "harmony"},
        {"partId": "P3", "voice": "tenor", "treatment": "plain", "role": "inner"},
        {"partId": "P4", "voice": "soprano", "treatment": "plain", "role": "solo"},
        {"partId": "P5", "voice": "tenor", "treatment": "sustained", "role": "support"},
    ],
    "conductor": {"overview": "Keep it buoyant.", "gestures": "Light 4.",
                  "tempoMap": [{"measure": 1, "marking": "Allegro", "bpm": 120}],
                  "cues": [{"measure": 3, "part": "Oboe", "text": "Solo."},
                           {"measure": 40, "part": "Oboe", "text": "Past the end."}],
                  "rehearsalMarks": [{"measure": 1, "label": "A", "note": "Balance."}]},
}
FAKE_MEASURES = 2 + 2 + 2 + 1

# The decorated line: Violin I, written once per phrase.
FAKE_MODEL_OUTPUT = {
    "parts": [
        {"partId": "P1", "performanceNote": "Lead.",
         "phrases": [{"phrase": "A", "abc": "(B c) !mf!d2 .d4 | A6 G2 |", "abcLower": None},
                     {"phrase": "B", "abc": "e2 g2 b4 | B8 |", "abcLower": None}]},
    ],
}

# Whole parts in the shape validate_notation() reads, for the checks below
# that exercise it directly.
FULL_PARTS = {
    "timeSignature": "4/4",
    "parts": [
        {"partId": "P1", "abcKey": "G", "abc": "G2 A2 B2 c2 | d8 |",
         "abcLower": None, "performanceNote": "Lead."},
        {"partId": "P2", "abcKey": "G", "abc": "D2 D2 G2 G2 | B8 |",
         "abcLower": None, "performanceNote": "Harmony."},
        {"partId": "P3", "abcKey": "G", "abc": "B,2 B,2 D2 D2 | G8 |",
         "abcLower": None, "performanceNote": "Inner."},
        {"partId": "P4", "abcKey": "G", "abc": "d2 e2 d2 B2 | G8 |",
         "abcLower": None, "performanceNote": "Sing."},
        {"partId": "P5", "abcKey": "G", "abc": "[GBd]4 [Ace]4 | [GBd]8 |",
         "abcLower": "G,,4 D,,4 | G,,8 |", "performanceNote": "Light."},
    ],
}

class FakeCompletion:
    def __init__(self, payload, finish_reason="stop"):
        message = mock.Mock(content=json.dumps(payload))
        self.choices = [mock.Mock(message=message, finish_reason=finish_reason)]

import json

def call_with_model(payload, *part_completions, plan=None):
    """Drive one request through both passes.

    The plan call is answered from `plan`; every call that asks for parts is
    answered from `part_completions` in order (first = the writing pass, second
    = the repair round, if one happens).
    """
    remaining = list(part_completions)

    def answer(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        if name == "plan":
            return FakeCompletion(plan if plan is not None else FAKE_PLAN)
        return remaining.pop(0)

    fake_client = mock.Mock()
    fake_client.chat.completions.create.side_effect = answer
    with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
         mock.patch.object(composer, "client", return_value=fake_client):
        response = client.post("/api/compose", json=payload, headers=AUTHED)
    return response, fake_client.chat.completions.create

r, create = call_with_model(COMPOSE, FakeCompletion(FAKE_MODEL_OUTPUT))
check("POST /api/compose valid -> 201", r.status_code == 201, r.json)

score = r.json.get("score", {})
check("  returns one part per player, not per instrument",
      [p["label"] for p in score.get("parts", [])] ==
      ["Violin I", "Violin II", "Violin III", "Oboe", "Piano"],
      [p.get("label") for p in score.get("parts", [])])

abc = score.get("scoreAbc", "")
check("  full score carries every voice",
      all(f"V:{v} " in abc or f"V:{v}\n" in abc
          for v in ("P1", "P2", "P3", "P4", "P5a", "P5b")), abc[:400])
check("  piano is braced onto a grand staff", "{P5a P5b}" in abc, abc[:400])
check("  headers written by us, exactly once",
      abc.count("M:4/4") == 1 and abc.count("L:1/8") == 1 and abc.count("X:1") == 1, abc[:400])
check("  tempo comes through", "Q:1/4=120" in abc, abc[:400])
check("  each part is a standalone tune",
      all(p["abc"].startswith("X:1") and "T:Sunrise Run" in p["abc"]
          for p in score["parts"]), score["parts"][0]["abc"][:200])
check("  a single part contains only its own voice",
      "V:P2" not in score["parts"][0]["abc"], score["parts"][0]["abc"][:200])
check("  no warnings when every bar adds up", score.get("warnings") == [], score.get("warnings"))
check("  a clean score is not sent for repair", create.call_count == 2, create.call_count)
check("  conductor material returned", bool(score.get("conductor")), score.get("conductor"))
check("  the plan is asked for before the parts",
      [c.kwargs["response_format"]["json_schema"]["name"] for c in create.call_args_list]
      == ["plan", "parts"],
      [c.kwargs["response_format"]["json_schema"]["name"] for c in create.call_args_list])

# The form, not the model, decides the length: four sections from two phrases.
import abc_check as abc_check_mod
written_parts = {w["partId"]: w for w in score.get("written", [])}
check("  the piece is as long as its form", score.get("measureCount") == FAKE_MEASURES,
      score.get("measureCount"))
check("  every part, derived or written, has exactly that many bars",
      all(abc_check_mod.bar_count(w["abc"]) == FAKE_MEASURES for w in written_parts.values()),
      {k: abc_check_mod.bar_count(w["abc"]) for k, w in written_parts.items()})
parts_prompt = create.call_args_list[1].kwargs["messages"][-1]["content"]
check("  the model is asked for phrases, not the whole piece",
      "A = 2 bars, B = 2 bars" in parts_prompt and "WRITE THESE 1 PART" in parts_prompt,
      parts_prompt[-1500:])
check("  only the decorated part reaches the model; the rest are derived",
      "[P1] Violin I" in parts_prompt.split("WRITE THESE")[1]
      and "[P2]" not in parts_prompt.split("WRITE THESE")[1], parts_prompt[-1500:])
check("  the decorated phrase is reused, articulation kept, its own dynamic replaced",
      written_parts["P1"]["abc"].count("(B c) d2 .d4") == 2
      and written_parts["P1"]["abc"].startswith("!mf! (B c) d2"),
      written_parts["P1"]["abc"])
check("  a transposed section changes key and returns home",
      "[K:C]" in written_parts["P2"]["abc"] and "[K:G]" in written_parts["P2"]["abc"],
      written_parts["P2"]["abc"])
check("  the section's dynamic is marked in derived parts too",
      "!ff!" in written_parts["P3"]["abc"] and "!pp!" in written_parts["P3"]["abc"],
      written_parts["P3"]["abc"])
check("  a solo thins the ensemble: the doubling players rest under it",
      written_parts["P3"]["abc"].split("||")[1].strip().startswith("[K:C] z8"),
      written_parts["P3"]["abc"])
check("  the soloist carries the tune in the solo section",
      "z8" not in written_parts["P4"]["abc"].split("||")[1], written_parts["P4"]["abc"])
check("  the piano's left hand reads the bass line, not a copy of the right",
      written_parts["P5"]["abcLower"] and
      written_parts["P5"]["abcLower"] != written_parts["P5"]["abc"],
      written_parts["P5"])
check("  the form's sections come back for the playback timeline",
      [s["name"] for s in score.get("sections", [])] == ["A", "B", "A2", "Coda"]
      and score["sections"][-1]["toMeasure"] == FAKE_MEASURES, score.get("sections"))
check("  conductor cues past the end of the piece are dropped",
      [c["measure"] for c in score["conductor"]["cues"]] == [3], score["conductor"]["cues"])

# Progress: a six-minute request has to be able to say what it is doing.
import progress as progress_mod

progress_mod.start("sm-1", 14, progress_mod.estimate_seconds(14))
first = progress_mod.read("sm-1")
check("a running generation reports a stage and a percent",
      first["stage"] == "planning" and 0 <= first["percent"] < 100, first)
check("  and an estimate that brackets a real 14-part run (313s)",
      first["lowSeconds"] <= 313 <= first["highSeconds"],
      (first["lowSeconds"], first["highSeconds"]))

progress_mod.report("sm-1", "writing", 2, 4)
mid = progress_mod.read("sm-1")
check("  the bar advances as batches land", mid["percent"] > first["percent"],
      (first["percent"], mid["percent"]))

# Batches finish out of order; the bar must never go backwards.
progress_mod.report("sm-1", "writing", 1, 4)
check("  an out-of-order batch cannot rewind it",
      progress_mod.read("sm-1")["percent"] == mid["percent"])

progress_mod.finish("sm-1")
end = progress_mod.read("sm-1")
check("  finishing reads 100% and says so",
      end["percent"] == 100 and end["remainingSeconds"] == 0 and end["label"] == "Finished", end)

check("an unknown generation id is simply not found",
      progress_mod.read("no-such-id") is None)

with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}):
    r = client.get("/api/compose/progress/no-such-id", headers=AUTHED)
check("GET /api/compose/progress unknown -> 404", r.status_code == 404, r.status_code)

r = client.get("/api/compose/progress/sm-1")
check("GET /api/compose/progress no token -> 401", r.status_code == 401, r.status_code)

with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}):
    r = client.get("/api/compose/estimate?parts=24", headers=AUTHED)
check("GET /api/compose/estimate -> a range, not a single promise",
      r.status_code == 200 and r.json["lowSeconds"] < r.json["highSeconds"], r.json)

# Structured Outputs must actually be requested, or the JSON parse is a coin flip.
kwargs = create.call_args.kwargs
check("  model asked for strict structured output",
      kwargs["response_format"]["json_schema"]["strict"] is True, kwargs.get("response_format"))
check("  brief names the transposition for the Bb instruments",
      "MAJOR 2ND ABOVE" in composer.build_user_prompt(
          {**COMPOSE, "instruments": [{"name": "Trumpet", "parts": 1}]},
          composer.build_roster([{"name": "Trumpet", "parts": 1}])))

# Conductor: off means no podium material and a no-conductor brief.
r, _ = call_with_model({**COMPOSE, "conductor": False}, FakeCompletion(FAKE_MODEL_OUTPUT))
check("conductor off -> no conductor material returned",
      r.status_code == 201 and r.json["score"]["conductor"] is None, r.json.get("score", {}).get("conductor"))
check("  and the brief tells the model to hold one tempo",
      "no conductor" in composer.build_user_prompt({**COMPOSE, "conductor": False},
                                                   composer.build_roster(COMPOSE["instruments"])).lower())

# --- notation is checked arithmetically, and repaired ---------------------

# A bar that doesn't add up is the model's most common failure. It must be
# caught here rather than by a player at a music stand.
overfull = json.loads(json.dumps(FULL_PARTS))
overfull["parts"][1]["abc"] = "D2 D2 G2 G2 A2 | B8 |"          # 10 units in a 4/4 bar
found = composer.validate_notation(overfull, ROSTER, "intermediate")
check("an overfull bar is detected",
      any("bar 1 is 10 units long, needs 8" in f for f in found), found)

short = json.loads(json.dumps(FULL_PARTS))
short["parts"][1]["abc"] = "D2 D2 G2 G2 |"                     # one bar, not two
check("a part with the wrong number of bars is detected",
      any("has 1 bars; every part must have exactly 2" in f
          for f in composer.validate_notation(short, ROSTER, "intermediate")),
      composer.validate_notation(short, ROSTER, "intermediate"))

out_of_range = json.loads(json.dumps(FULL_PARTS))
out_of_range["parts"][3]["abc"] = "c'2 e'2 g'2 c''2 | c'8 |"    # oboe, far too high
check("notes above the instrument's range are detected",
      any("above its highest playable note" in f
          for f in composer.validate_notation(out_of_range, ROSTER, "intermediate")),
      composer.validate_notation(out_of_range, ROSTER, "intermediate"))

check("the same notes are allowed when the level permits the full range",
      not any("above its highest" in f
              for f in composer.validate_notation(
                  json.loads(json.dumps(FULL_PARTS)), ROSTER, "pro")))

# A hand-written phrase with a bad bar is caught before it is multiplied out
# across the form, and only that phrase goes back for correction.
BAD_PHRASE = json.loads(json.dumps(FAKE_MODEL_OUTPUT))
BAD_PHRASE["parts"][0]["phrases"][0]["abc"] = "(B c) d2 .d4 B2 | A6 G2 |"     # 10 units
r, create = call_with_model(COMPOSE, FakeCompletion(BAD_PHRASE), FakeCompletion(FAKE_MODEL_OUTPUT))
check("a defective phrase is written, then repaired, after one plan",
      r.status_code == 201 and create.call_count == 3, create.call_count)
repair_prompt = create.call_args.kwargs["messages"][-1]["content"]
check("  the repair brief names the phrase and the bar",
      "phrase A bar 1 is 10 units long, needs 8" in repair_prompt
      and "phrase B" not in repair_prompt.split("AS THEY STAND:")[1].split("Correct")[0],
      repair_prompt[:600])
check("  a successful repair clears the warnings",
      r.json["score"]["warnings"] == [] and r.json["score"]["repaired"] is True,
      (r.json["score"]["warnings"], r.json["score"]["repaired"]))
check("  and the repaired phrase is the one used",
      "(B c) d2 .d4 | A6" in r.json["score"]["written"][0]["abc"], r.json["score"]["written"][0]["abc"])

# Repair no better -> that phrase falls back to the chorale line, so the parts
# still line up and nothing has to be flagged.
r, create = call_with_model(COMPOSE, FakeCompletion(BAD_PHRASE), FakeCompletion(BAD_PHRASE))
lead = r.json["score"]["written"][0]["abc"] if r.status_code == 201 else ""
check("an unrepairable phrase falls back to its chorale line",
      r.status_code == 201 and r.json["score"]["repaired"] is False
      and ".d4 B2" not in lead and abc_check_mod.bar_count(lead) == FAKE_MEASURES,
      (r.status_code, lead))
check("  and the score still lines up, so there is no alignment warning",
      not any("won't line up" in w for w in r.json["score"]["warnings"]),
      r.json["score"]["warnings"])

# A batch that is cut off is retried once. If it still fails, the part plays
# its plain chorale line — the piece is delivered, and the user is told.
cut = lambda: FakeCompletion(FAKE_MODEL_OUTPUT, finish_reason="length")
r, create = call_with_model(COMPOSE, cut(), cut())
check("a cut-off batch is retried before giving up", create.call_count == 3,
      create.call_count)
check("  and the score is still delivered, every part present",
      r.status_code == 201 and len(r.json["score"]["parts"]) == 5, (r.status_code, r.json))
check("  with the plain-line parts named",
      any("Violin I" in w and "plain harmony line" in w for w in r.json["score"]["warnings"]),
      r.json["score"]["warnings"])

# A plan with no usable phrase has no music to build from.
r, _ = call_with_model(COMPOSE, plan={**FAKE_PLAN, "phrases": []})
check("a plan with no music -> 502, not an empty score",
      r.status_code == 502 and "usable music" in r.json["message"], (r.status_code, r.json))

# Beginners never change key mid-piece, whatever the plan asks for.
r, _ = call_with_model({**COMPOSE, "level": "beginner"}, FakeCompletion(FAKE_MODEL_OUTPUT),
                       FakeCompletion(FAKE_MODEL_OUTPUT))
check("a beginner piece stays in one key",
      r.status_code == 201 and not any("[K:" in w["abc"] for w in r.json["score"]["written"]),
      (r.status_code, r.json.get("message"), r.json.get("errors")))

# Harmony is enforced, not hoped for. The chorale's lower voices are moved onto
# the chord on every beat, and a hand-written line that holds a note against
# the chord is never used.
import form as form_mod
from fractions import Fraction as Fr
voices = {"soprano": [[(71, Fr(8))]], "alto": [[(65, Fr(4)), (64, Fr(4))]],
          "tenor": [[(62, Fr(8))]], "bass": [[(43, Fr(8))]]}
moved = form_mod.harmonise(voices, ["G"], Fr(2))
check("a chorale note off the chord on a beat is moved onto it",
      moved == 2 and voices["alto"][0][0][0] % 12 in (7, 11, 2)
      and voices["alto"][0][1][0] % 12 in (7, 11, 2), voices["alto"])
check("  and the bass and the tune, already on the chord, are left alone",
      voices["bass"][0] == [(43, Fr(8))] and voices["soprano"][0] == [(71, Fr(8))])

HELD = json.loads(json.dumps(FAKE_MODEL_OUTPUT))
HELD["parts"][0]["phrases"][0]["abc"] = "(B c) d2 .d4 | c8 |"     # C held over D
r, create = call_with_model(COMPOSE, FakeCompletion(HELD), FakeCompletion(HELD))
check("a line holding a note against the chord goes back for correction",
      create.call_count == 3 and "held against the chord" in
      create.call_args.kwargs["messages"][-1]["content"], create.call_count)
check("  and if it is not fixed, the chorale line is played instead",
      r.status_code == 201 and "c8" not in r.json["score"]["written"][0]["abc"],
      r.json.get("score", {}).get("written", [{}])[0].get("abc"))

# The outline names the chords; each phrase's chorale is its own call. If one
# fails, the piece still arrives, with that phrase voiced from its chords.
OUTLINE = json.loads(json.dumps(FAKE_PLAN))
for phrase in OUTLINE["phrases"]:
    for voice in ("soprano", "alto", "tenor", "bass"):
        phrase.pop(voice)
    phrase["character"] = "A rising tune."
chorale_names = []

def outline_answer(**kwargs):
    name = kwargs["response_format"]["json_schema"]["name"]
    chorale_names.append(name)
    if name == "plan":
        return FakeCompletion(OUTLINE)
    if name == "chorale":
        if "WRITE PHRASE B" in kwargs["messages"][-1]["content"]:
            raise composer.OpenAIError("Request timed out.")
        return FakeCompletion({k: FAKE_PLAN["phrases"][0][k]
                               for k in ("soprano", "alto", "tenor", "bass")})
    return FakeCompletion(FAKE_MODEL_OUTPUT)

fake = mock.Mock()
fake.chat.completions.create.side_effect = outline_answer
with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
     mock.patch.object(composer, "client", return_value=fake):
    r = client.post("/api/compose", json=COMPOSE, headers=AUTHED)
check("each phrase's chorale is written in its own call",
      chorale_names.count("chorale") == 2 and chorale_names[0] == "plan", chorale_names)
check("  a failed chorale still delivers the whole piece, every part, every bar",
      r.status_code == 201 and len(r.json["score"]["parts"]) == 5
      and all(abc_check_mod.bar_count(w["abc"]) == FAKE_MEASURES
              for w in r.json["score"]["written"]), (r.status_code, r.json.get("message")))

# Missing key is a setup mistake; say so instead of returning a generic 500.
composer._client = None
with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
     mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
    r = client.post("/api/compose", json=COMPOSE, headers=AUTHED)
check("missing OPENAI_API_KEY -> 503 explaining the setup",
      r.status_code == 503 and "OPENAI_API_KEY" in r.json["message"], (r.status_code, r.json))
composer._client = None

# Structured Outputs in strict mode rejects a schema that misses any of these,
# and it fails at request time with a 400 rather than anywhere useful.
def walk_schema(node, path="$", found=None):
    found = [] if found is None else found
    if not isinstance(node, dict):
        return found
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "description", "enum", "anyOf"}
    for key in node:
        if key not in allowed:
            found.append(f"{path}: unsupported keyword '{key}'")
    types = node.get("type") if isinstance(node.get("type"), list) else [node.get("type")]
    if "object" in types:
        props = node.get("properties", {})
        if node.get("additionalProperties") is not False:
            found.append(f"{path}: missing additionalProperties: false")
        for missing in sorted(set(props) - set(node.get("required", []))):
            found.append(f"{path}.{missing}: not listed in 'required'")
        for key, child in props.items():
            walk_schema(child, f"{path}.{key}", found)
    if "array" in types:
        walk_schema(node.get("items", {}), f"{path}[]", found)
    return found

# Every schema that actually gets sent, not just one of them: strict mode
# rejects the whole request if any property is missing from "required".
for schema_name in ("PLAN_SCHEMA", "BATCH_SCHEMA", "REPAIR_SCHEMA", "REVISION_SCHEMA"):
    schema_problems = walk_schema(getattr(composer, schema_name))
    check(f"{schema_name} is valid for strict Structured Outputs",
          not schema_problems, schema_problems)

# OPENAI_MODEL is user-configurable and model families disagree about
# max_tokens/temperature. An unsupported parameter must be dropped and retried,
# not surfaced as a failed generation.
param_attempts = []

class PickyCompletions:
    def create(self, **kw):
        param_attempts.append(sorted(k for k in kw
                                     if k in ("max_tokens", "max_completion_tokens", "temperature")))
        if "max_tokens" in kw:
            raise composer.OpenAIError(
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.")
        if kw.get("temperature", 1) != 1:
            raise composer.OpenAIError(
                "Unsupported value: 'temperature' does not support 0.8 with this model.")
        name = kw["response_format"]["json_schema"]["name"]
        return FakeCompletion(FAKE_PLAN if name == "plan" else FAKE_MODEL_OUTPUT)

picky = mock.Mock()
picky.chat.completions = PickyCompletions()
with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
     mock.patch.object(composer, "client", return_value=picky):
    r = client.post("/api/compose", json=COMPOSE, headers=AUTHED)
check("model that rejects max_tokens/temperature still produces a score",
      r.status_code == 201, (r.status_code, r.json))
# Generation makes several calls now — a plan, then the parts — and each one
# has to negotiate the same way, so the sequence repeats rather than running once.
RENAME_THEN_DROP = [["max_tokens", "temperature"],
                    ["max_completion_tokens", "temperature"],
                    ["max_completion_tokens"]]
check("  max_tokens was renamed, then temperature dropped",
      param_attempts[:3] == RENAME_THEN_DROP, param_attempts)
check("  and every later call negotiates the same way",
      len(param_attempts) % 3 == 0
      and all(param_attempts[i:i + 3] == RENAME_THEN_DROP
              for i in range(0, len(param_attempts), 3)), param_attempts)

class BadKey:
    def create(self, **kw):
        raise composer.OpenAIError("Incorrect API key provided: sk-xxx")

badkey = mock.Mock()
badkey.chat.completions = BadKey()
with mock.patch.object(firebase_auth, "verify_id_token", return_value={"uid": "uid-123"}), \
     mock.patch.object(composer, "client", return_value=badkey):
    r = client.post("/api/compose", json=COMPOSE, headers=AUTHED)
check("a rejected API key is explained, not retried into a generic failure",
      r.status_code == 502 and "OPENAI_API_KEY" in r.json["message"], (r.status_code, r.json))

# --- error shapes --------------------------------------------------------
r = client.get("/api/nope")
check("unknown /api/ route -> JSON 404",
      r.status_code == 404 and r.is_json, (r.status_code, r.content_type))

r = client.delete("/api/health")
check("wrong method -> JSON 405 (not 500)",
      r.status_code == 405 and r.is_json, (r.status_code, r.content_type))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
