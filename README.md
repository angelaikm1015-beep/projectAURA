# projectAURA

A music community app. Signup and login run on **Firebase Auth + Firestore**,
with a **Python/Flask** backend and a vanilla HTML/CSS/JS frontend (no build step).

```
backend/          Flask API — validation, account creation, Firestore access
frontend/         HTML/CSS/JS pages, served by Flask at /
firestore.rules   Locks Firestore down to backend-only writes
```

## How auth is split

| Step | Runs where | Why |
| --- | --- | --- |
| **Signup** | `POST /api/signup` → Flask | Flask validates the whole payload, *then* creates the Auth user and the Firestore profile. Validating first means a rejected profile never leaves an orphan account. |
| **Login** | Firebase Auth, in the browser | The password never touches our server, and Firebase owns the session and token refresh. |
| **Everything after** | Flask, with `Authorization: Bearer <ID token>` | Flask verifies the token with the Admin SDK before touching Firestore. |

Firestore is never written from the browser. The Admin SDK bypasses security
rules, so `firestore.rules` denies all client writes — a user can read their own
document and nothing else.

## Setup

**1. Firebase console**

- Create a project.
- **Authentication → Sign-in method →** enable **Email/Password**.
- **Firestore Database →** create a database.
- **Project settings → Service accounts →** *Generate new private key*, save it as
  `backend/serviceAccountKey.json` (gitignored).
- **Project settings → General → Your apps →** add a Web app and copy the config.

**2. Backend**

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env      # then fill it in
python backend/app.py                     # http://localhost:5001
```

> Port 5001, not 5000 — on macOS, Control Center's AirPlay Receiver listens on
> 5000 and will shadow the dev server. Change `PORT` in `backend/.env` if 5001
> is taken too.

**3. Frontend**

Paste your web config into [frontend/js/firebase-config.js](frontend/js/firebase-config.js),
then open <http://localhost:5001> — Flask serves the pages, so there's no second
server and no CORS to configure.

> **Open the pages over http, never by double-clicking the .html file.** The
> frontend uses ES modules, and browsers block those over `file://` — the page
> loads but no JavaScript runs at all, so the dropdowns stay empty, validation
> never fires, and nothing submits.

To serve the frontend separately instead (Live Server, Vite, …), set `API_BASE`
in that same file to `"http://localhost:5001"` and add your origin to
`ALLOWED_ORIGINS` in `backend/.env`.

**4. Deploy the rules**

```sh
firebase deploy --only firestore:rules
```

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health` | — | Liveness check |
| `GET` | `/api/options` | — | Dropdown contents for the signup form |
| `POST` | `/api/signup` | — | Validate, create the Auth user, write the profile |
| `GET` | `/api/me` | Bearer | The signed-in user's profile |
| `PATCH` | `/api/me` | Bearer | Update profile fields (not email/password) |

Validation failures come back as `400` with `{"message": ..., "errors": {"<field>": "<message>"}}`,
which the frontend renders under the matching input.

## Pages

| File | Purpose |
| --- | --- |
| [frontend/index.html](frontend/index.html) | Log in, "keep me signed in", password reset email |
| [frontend/signup.html](frontend/signup.html) | Create an account |
| [frontend/welcome.html](frontend/welcome.html) | Profile summary from `/api/me`, log out |

### Signup fields

- **Email** — also the username (stored as `username`, and as the Auth `displayName`)
- **Password** + **re-enter password** — min 8 chars, must contain a letter and a number
- **Date of birth** — date picker; age is *derived* from it on every read, never stored,
  so it can't go stale
- **Gender**, **Nationality** — dropdowns
- **Where you play** — middle school / high school / college / hobby
- **Instrument** — dropdown grouped by family
- **Favorite genre** — dropdown

### Where the dropdown contents live

[backend/options.py](backend/options.py) is the single source of truth — the
server validates every submitted value against it. A generated copy is bundled
into [frontend/js/options-data.js](frontend/js/options-data.js) so the form
renders instantly and still works when the API is unreachable; `signup.js` then
refreshes from `/api/options`, so a running server always wins.

After editing `options.py`:

```sh
python backend/generate_options.py
```

`smoke_test.py` fails if you forget, so the two can't silently drift.

## Firestore document shape

`users/{uid}`:

```json
{
  "email": "angela@example.com",
  "username": "angela@example.com",
  "birthdate": "2005-03-14",
  "gender": "Female",
  "nationality": "South Korea",
  "affiliation": "college",
  "instrument": "Violin",
  "genre": "Classical",
  "createdAt": "<server timestamp>"
}
```

## Tests

```sh
python backend/smoke_test.py
```

Boots the app with Firebase stubbed out — no credentials or network needed — and
checks routing, validation, the auth guard, and the signup rollback path.

To verify against your **real** Firebase project, start the server and run:

```sh
python backend/e2e_test.py
```

It signs up `aura-e2e-delete-me@example.com`, reads the document back out of
Firestore, logs in through Identity Toolkit for a real ID token (the same call
the browser SDK makes), exercises `GET`/`PATCH /api/me` with it, then deletes the
account and the document.
