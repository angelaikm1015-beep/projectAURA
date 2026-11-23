# projectAURA

A music community app that writes sheet music. Signup and login run on
**Firebase Auth + Firestore**, score generation runs on the **OpenAI API**, with
a **Python/Flask** backend and a **React (Vite)** frontend.

```
backend/          Flask API — validation, account creation, Firestore access
  composer.py     Score generation: briefs the model, assembles the notation
  abc_check.py    Reads the returned notation back and checks the arithmetic
  options.py      Every dropdown list, plus per-instrument clefs and ranges
frontend/         React + Vite app
  src/pages/      LoginPage, SignupPage, WelcomePage, ComposePage
  src/components/ Form fields and page chrome
  src/context/    AuthContext — one Firebase auth subscription for the app
  src/lib/        firebase, api client, validation, generated options
legacy-vanilla/   The pre-React pages, kept until React is confirmed working
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

`backend/.env` needs an `OPENAI_API_KEY` as well as the Firebase settings, or
`/compose` will return a 503 telling you so. Get one from
[platform.openai.com/api-keys](https://platform.openai.com/api-keys); the account
needs billing enabled. `OPENAI_MODEL` defaults to `gpt-6.1-sol` and can be any model
that supports Structured Outputs — see [Choosing a model](#choosing-a-model).

> Port 5001, not 5000 — on macOS, Control Center's AirPlay Receiver listens on
> 5000 and will shadow the dev server. Change `PORT` in `backend/.env` if 5001
> is taken too.

**3. Frontend** (needs Node 18+)

```sh
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to Flask on :5001
```

Run Flask in one terminal and `npm run dev` in another. Vite proxies `/api` to
Flask, so the browser sees one origin and there's no CORS to configure.

The Firebase web config lives in
[frontend/src/lib/firebase-config.js](frontend/src/lib/firebase-config.js) and is
already filled in for `projectaura-8cf5f`. Those values are public by design —
Auth and `firestore.rules` are what protect the data.

**Serving it from Flask instead** (one server, e.g. for deployment):

```sh
cd frontend && npm run build     # emits frontend/dist
python backend/app.py            # http://localhost:5001 serves it
```

**4. Deploy the rules**

```sh
firebase deploy --only firestore:rules
```

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health` | — | Liveness check |
| `GET` | `/api/options` | — | Dropdown contents for the signup and compose forms |
| `POST` | `/api/signup` | — | Validate, create the Auth user, write the profile |
| `GET` | `/api/me` | Bearer | The signed-in user's profile |
| `PATCH` | `/api/me` | Bearer | Update profile fields (not email/password) |
| `POST` | `/api/compose` | Bearer | Generate a score, save it to the library, return it with its `scoreId`. Slow — 30–120s — and it spends money, hence the auth. |
| `GET` | `/api/compose/progress/<id>` | Bearer | How far a running generation has got: stage, percent, elapsed, time left |
| `GET` | `/api/compose/estimate?parts=N` | Bearer | How long a piece of that size usually takes, as a range |
| `GET` | `/api/scores` | Bearer | The signed-in user's library, newest first (summaries only) |
| `PATCH` | `/api/scores` | Bearer | Share, or unshare, the whole library at once |
| `GET` | `/api/scores/<id>` | Bearer | One saved score in full — the owner's, or anyone's shared piece |
| `PATCH` | `/api/scores/<id>` | Bearer | Set one piece's `visibility` (`public`/`private`) and/or its gallery `caption`; both optional, owner only |
| `POST` | `/api/scores/<id>/revise` | Bearer | Change one thing about a piece in plain language, keeping the rest (owner only) |
| `POST` | `/api/scores/<id>/undo` | Bearer | Restore the version from before the last revision (owner only) |
| `DELETE` | `/api/scores/<id>` | Bearer | Delete one piece for good (owner only) |
| `GET` | `/api/gallery` | Bearer | The community feed, newest post first. `?level=` and `?ensemble=` narrow it; the reply carries facet counts for the filter UI |
| `GET` | `/api/users/<uid>` | Bearer | Someone's public page — who they are and everything they've shared |
| `GET` | `/api/me/likes` | Bearer | Pieces this user liked |
| `GET` | `/api/me/saves` | Bearer | Pieces this user saved for later |
| `POST` | `/api/scores/<id>/like` | Bearer | Toggle a like; returns the new count |
| `POST` | `/api/scores/<id>/save` | Bearer | Toggle a save (private to the saver) |
| `GET` | `/api/scores/<id>/comments` | Bearer | Comments on a piece, oldest first |
| `POST` | `/api/scores/<id>/comments` | Bearer | Add a comment |
| `DELETE` | `/api/scores/<id>/comments/<commentId>` | Bearer | Remove your own comment, or any on your own piece |

Likes, saves and comments all check first that the piece is visible to the
reader, so a private piece can be neither liked nor commented on by a stranger.
Filtering and sorting happen in Python rather than in the query, because a
`where` on two fields plus an order needs a composite Firestore index built by
hand — fine at gallery scale, worth revisiting if it grows.

A revision is an edit, not a regeneration: the model is handed the lines it
already wrote and returns only the parts it needed to touch, so asking to fix
the oboe cannot quietly rewrite the violins. Exactly one previous version is
kept, which is what `/undo` restores.

Every generated score is written to Firestore before the response is sent, so
closing the tab never loses a piece. Scores are saved **private**; they become
visible in the gallery only when their owner shares them, one piece at a time.
Asking for a private score that isn't yours returns `404`, not `403` — a stranger
shouldn't be able to learn which ids exist. Sharing writes a caption alongside
the piece and stamps `sharedAt`, so the gallery reads as a feed of posts ordered
by when they were posted rather than when the music was written.

Validation failures come back as `400` with `{"message": ..., "errors": {"<field>": "<message>"}}`,
which the frontend renders under the matching input.

## Pages

Client-side routes, via react-router. Flask falls back to the SPA for any
non-`/api` path, so deep links and refreshes work.

| Route | Component | Purpose |
| --- | --- | --- |
| `/login` | [LoginPage.jsx](frontend/src/pages/LoginPage.jsx) | Log in, "keep me signed in", password reset email |
| `/signup` | [SignupPage.jsx](frontend/src/pages/SignupPage.jsx) | Create an account |
| `/welcome` | [WelcomePage.jsx](frontend/src/pages/WelcomePage.jsx) | Profile summary from `/api/me`, log out |
| `/compose` | [ComposePage.jsx](frontend/src/pages/ComposePage.jsx) | Describe the ensemble, generate and print the sheet music |

[AuthContext](frontend/src/context/AuthContext.jsx) holds the single
`onAuthStateChanged` subscription. It exposes an `initializing` flag, and
[App.jsx](frontend/src/App.jsx) waits for it before routing — Firebase restores a
persisted session asynchronously, so routing on the initial `null` user would
bounce a signed-in user to `/login` on every refresh.

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

[backend/options.py](backend/options.py) is the single source of truth for both
forms — the server validates every submitted value against it. A generated copy is bundled
into [frontend/src/lib/options-data.js](frontend/src/lib/options-data.js) so the
form renders instantly and still works when the API is unreachable; `SignupPage`
then refreshes from `/api/options`, so a running server always wins.

After editing `options.py`:

```sh
python backend/generate_options.py
```

`smoke_test.py` fails if you forget, so the two can't silently drift.

## The compose form

- **Instruments.** A toggle chooses how. **I'll pick the instruments** shows
  every instrument as a checkbox, grouped by family, and each ticked one gets a
  stepper for its number of parts (Violin ×2 is Violin I and II). **Let GPT
  choose** asks only for the size: a chamber group (4–7 parts), a small
  orchestra (8–13) or a full orchestra (14–22).
  [choices.py](backend/choices.py) makes a short, low-effort call that picks an
  ensemble suiting the mood and players. It keeps the result to known
  instruments, their part caps and the size. If the call fails, a standard
  orchestra of that size is used. From then on the piece is written as if the
  user had ticked those instruments, and the score shows which GPT chose and
  why.
- **Musical details, all optional (Auto lets the mood decide):** time
  signature (2/4, 3/4, 4/4, 6/8, 12/8, 5/4), major or minor, key, and length in
  minutes (1–10) or bars (16–200). A chosen time signature or key becomes a
  one-value enum in the plan's JSON schema (`choices.constrain`), so the model
  can't answer with anything else. A mode alone allows any key in that mode. A
  length replaces the level's default in the brief; in minutes, the plan works
  the measure count out from its tempo.

## How the sheet music gets made

There is no OpenAI endpoint that returns sheet music, so
[backend/composer.py](backend/composer.py) does it in two halves.

**The model plans, then writes.** Asking for a whole score in one reply does not
work: the model has to hold every decision and every stave in one answer, and it
gets cut off partway through — worse with every instrument added, which is
exactly when the piece matters most. It is also the wrong shape of task. A
composer doesn't write twenty staves left to right; they settle the piece first
and then write the lines into it.

So it goes in two passes, both using **Structured Outputs** (`json_schema`,
`strict: true`):

1. **The plan** — key, tempo, form, the main theme, what each player does, and a
   chord for *every single bar*. A page of text whatever the ensemble size.
2. **The parts** — written a few at a time, each batch against that same chord
   grid. The batches go out concurrently because they don't need each other's
   output: the grid is the whole contract. A twenty-part orchestra costs about
   the same wall-clock time as a quartet.

Every reply is small enough to finish, and the parts fit together because they
were all written to the same harmony. The correction round is batched the same
way, for the same reason. Notation is **ABC** rather than MusicXML — MusicXML is
far too verbose for a model to keep correct across a dozen staves, while ABC is
compact enough that the model has attention left for the music.

The configured model is a reasoning model, which matters for sizing: a measured
four-part score spent **8,148 tokens thinking and 3,240 writing**. Reasoning is
the larger half and it doesn't shrink as the reply does, so a generous ceiling is
not the same as a generous answer — keeping each reply small is what actually
prevents truncation.

**Python builds the score.** Headers, voice declarations, staff grouping, clefs,
per-part key signatures and MIDI programs are assembled deterministically in
`assemble_abc()`. The model is told never to write a header field, so it *cannot*
put half the ensemble in 4/4 and the other half in 3/4, or forget a clef.

Then [score-view.jsx](frontend/src/components/score-view.jsx) engraves that ABC
into real SVG staves with **abcjs**, in the browser. So the user gets actual
notation — reflowing, printable at full resolution, and playable through the Web
Audio API to hear the piece before rehearsal.

### Light and deep composing

The compose page offers two modes.

* **Light** (the default, `composer.compose`) is what's described above: the
  model writes a chord grid and a few short four-voice phrases, and
  [form.py](backend/form.py) expands them across the ensemble and the form. It
  finishes in a few minutes, but most of each part is arranged by rule.
* **Deep** ([backend/deep.py](backend/deep.py)) has the model write the whole
  score, with every instrument written together so the parts are composed
  against each other:
  1. **Architecture.** One call with high reasoning effort sets out the themes
     (written in the compact notation), the form, the harmony of every bar,
     and each section's key, lead, texture, accompaniment type (`sustained`,
     `arpeggio`, `repeated`, `offbeat`, `pedal`, `countermelody`, `none`) and
     material. Python prepares every key change (ii – V7 of the new key, then
     its tonic) and closes the piece V7 – I.
  2. **The score, a passage at a time.** Passages of up to 32 bars are written
     side by side. Each passage is one call that writes every instrument at
     once, section by section, voicing one shared harmony across the parts. The
     prompt asks for an orchestra of a few layers (tune, bass, harmony, at most
     one countermelody), each played together by several players.
  3. **Engraving in Python.** The model never writes ABC or MusicXML. It writes
     the compact notation described below, and
     [compact.py](backend/compact.py) does the rest. It parses the notation
     and silently repairs mechanical slips (bars that don't add up, notes out
     of range, chords too big for the instrument, broken ties and slurs). It
     moves notes held two beats or more off the chord onto it, balances the
     tune over the accompaniment, and ends every staff with *rit.* and a held
     final chord. Then it writes the ABC the site displays and plays,
     transposed into each player's key with key signatures and accidentals
     worked out. The final notes are stored in the compact form, and
     [musicxml.py](backend/musicxml.py) builds a real MusicXML file from them
     on demand (`GET /api/scores/<id>/musicxml`, the **Download MusicXML**
     button).

  The compact notation is one string per measure, every note at sounding
  pitch:

  ```
  "@p C4:4( D4:8 E4:8 F4:4 G4:4)"   dynamic p, a slur, quarters and eighths
  "G4+B4:2. r:4"                    a chord (dotted half) and a quarter rest
  "E5:2~ E5:8 D5:8 C5:4"            a tie
  "R"                               a whole-measure rest
  ```

  Editing a piece with "ask for a change" rewrites its ABC directly, so an
  edited piece no longer offers MusicXML.

Deep mode takes about 5–15 minutes, depending on ensemble size and level. That
is longer than a proxy or browser will hold a request open, so `POST
/api/compose` with `"mode": "deep"` answers `202` immediately and runs the piece
in a background thread. The page polls `/api/compose/progress/<id>` and collects
the piece from `/api/compose/result/<id>`. It is saved to the library either
way. Tune it with `DEEP_PLAN_EFFORT` (default `high`), `DEEP_WRITE_EFFORT`
(`medium`), `DEEP_CONCURRENCY` (`8` calls at once) and `DEEP_CALL_TIMEOUT`
(`900` seconds).

### The main melody and the solos (deep mode)

[backend/features.py](backend/features.py) makes both certain rather than
hoped for:

- **The main theme.** The plan writes theme A, the main melody, with its own
  chords. It is stated at the start, at the climax (about two-thirds of the way
  through) and near the end, in sections long enough for it and in the home
  key's mode. In each, Python writes the theme into the lead's part, moved into
  the section's key and by whole octaves into the instrument's singing
  register (`compact.SINGING`, `compact.fit_register`). The section's first bars
  take the theme's chords, so tune and harmony agree. The passage writer is
  shown it as already written and composes around it.
- **Solos.** Each piece gets one solo, or two at higher-intermediate and pro
  for pieces of 64 bars or more. They go in middle sections of 4–12 bars, for
  an instrument with a singing voice other than the one that carries the main
  tune. Each solo is composed by a call of its own, with melodic rules, the
  soloist's singing register, and Berlioz and Rimsky-Korsakov on solo writing.
  [backend/melody.py](backend/melody.py) checks the line for awkward leaps
  (tritones, sevenths, beyond an octave), unrecovered leaps, too few steps,
  rests breaking up the phrases, no breath for a wind player, a high note hit
  again and again, equal note values, notes outside the singing register, and
  an ending that stops rather than lands. If it finds faults they go back
  once, and the version with fewer is kept. The passage writers see the solo,
  write rests there and lead into and out of it. Balance puts the others two
  dynamic steps under it and marks it *solo*.

### The orchestration textbooks (RAG)

`rag/processed/all_chunks.jsonl` holds Rimsky-Korsakov's *Principles of
Orchestration* and Berlioz's *Treatise on Instrumentation*, cut into
~500-token passages with their heading path, instruments and topics (see
[rag/processed/README.md](rag/processed/README.md)).
[backend/rag.py](backend/rag.py) retrieves from it with BM25 over the passage
text, boosted by the topics a call needs and the instruments actually in the
ensemble. It needs no embedding service and builds no index: the corpus is
scored in memory in a few milliseconds, and the same request always gets the
same passages.

- **Deep plan:** three passages on composing for the orchestra: tutti,
  balance, doubling, combined groups.
- **Each deep passage call:** three Rimsky-Korsakov passages chosen by that
  passage's accompaniment type and texture. Berlioz's passage on how the
  instrument carrying the tune sings is added for each lead, hand-picked per
  instrument (`BERLIOZ_VOICE`), because his character writing is mixed in with
  key tables and OCR noise.
- **Light plan:** two passages on harmony and doubling.

The chapters on voices are left out unless the ensemble has singers. Notation
examples, which the corpus can't carry, are dropped from the text. Set
`RAG_CORPUS` to use a corpus elsewhere; without one, prompts go without.

### Harmony, endings and balance (both modes)

* **Fast notes.** An accompaniment of sixteenths isn't orchestral writing; it's
  noise under the tune. The prompts ask for mostly quarters and eighths, and the
  pro-level brief no longer demands fast passagework. In deep mode
  `compact.thin` merges any run faster than eighths in the accompaniment, and
  any faster than sixteenths in the tune, into one note per step. Dotted
  figures are left alone. In playback, the overlap between joined notes and the
  ring after a note ends are both capped by the note's own length, so fast runs
  don't smear.

* **The orchestra's harmonic language.** Left to itself the model wrote jazz
  harmony: in three recent pieces, 68–81% of chords were 9ths, 11ths, 13ths or
  6/9s. With every player taking a different tone of a five-note chord, the
  orchestra sounded like a cluster. Every planned chord is now reduced to the
  triad or seventh chord it stands on (`form.orchestral_chord`: Gmaj9 → Gmaj7,
  Am11 → Am7, D13 → D7, C6/9 → C). Diminished, half-diminished, augmented and
  sus4 chords and slash basses are kept. The prompts ask for triads and
  sevenths, mostly plain triads. In deep mode, every accompanying note on a
  beat is moved onto the chord, however short. The tune may still lean on an
  appoggiatura for up to two beats.

* **Harmonic rhythm.** A bar of the chord grid holds one chord, or two separated
  by a space (`"Dm7 G7"`), split on the beat. The prompts ask for chords held
  over two bars where the music settles, and for faster changes into cadences.
  [form.py](backend/form.py)'s `chord_spans` / `chord_pcs_at` is how every
  harmony check reads it.
* **Endings.** Every piece closes on a perfect cadence: the second-to-last bar
  ends on V7 and the last bar is the tonic, in the home key (`close_on_cadence`).
  In light mode the closing section gets its own copy of its phrase, so earlier
  hearings of that phrase keep their harmony. `finish_parts` marks *rit.* over
  the last two bars and holds the final chord under a fermata in every staff.
* **Keyboards inside an orchestra** (three or more parts) stay soft. Their
  dynamics print two steps below the ensemble's, light mode voices them as if
  the section were quieter, deep mode tells the model so, and playback mixes
  them lower.
* **The tune over the accompaniment.** `balance_parts` (light mode) and
  `compact.balance` (deep mode) run on every finished score. Where a section has a lead, every other player is capped a step below
  the section's dynamic, or two steps under a `solo`. The soloist's first note
  is marked *solo*. A tutti with no lead plays together at the section's level.
  Every player's entry in a section carries a dynamic.
* **Solos.** Both planners give at least one player a solo section (two or more
  at higher-intermediate and pro in deep mode), with the rest resting or
  accompanying very lightly.
* **The main melody.** Theme A (phrase A in light mode) is asked to be a tune
  you could sing back, stated near the start and returning at the climax and
  near the end, with other music between. In deep mode, the sections flagged
  `mainTheme` are told to open with theme A exactly.
* **Key changes.** Each one is prepared. The last bar before a new key is that
  key's ii – V7 (`lead_into`), and deep mode starts the new section on its
  tonic (`arrive_in`). Light mode gives the section before a modulation its own
  copy of its phrase so its chorale is composed toward the new key. The prompts
  tell each line to move into it by step.
* **Playable parts.** In light mode, strings, winds, brass and voices are told
  how to mark what a player needs: bowings and slur groups, tonguing, breaths
  and readable rhythms. In deep mode, the compact notation carries slurs,
  staccato, accents and ties, and the engraver keeps every chord within what
  the instrument can play.
* **Repetition.** Light mode writes three to five phrases, roughly 40% of the
  piece's bars, so each is heard fewer times. In deep mode, no part may repeat
  a measure more than twice running, and a returning theme must be transformed.

### Playback

**Following along.** While a piece plays, the bar being heard is tinted on the
sheet across the whole system, every instrument at once. A line moves through
it in time with the sound, and the page scrolls to each new system as the
music reaches it. The position is read from the audio clock on every animation
frame (`position()` in `usePlayer`), so it stays in step and slows with the
closing ritardando. The bar's extent comes from the `abcjs-mm<n>` class abcjs
puts on every note and rest it draws (`usePlayhead` in
[score-view.jsx](frontend/src/components/score-view.jsx)). The form timeline
above the sheet shows the section and bar number, and clicking a section
plays from there.

[orchestra.js](frontend/src/lib/orchestra.js) plays every note from recordings
of real instruments rather than a General MIDI soundfont. abcjs still reads the
ABC into notes, with pitch, timing and dynamics. Each note is then played from
the nearest recording, retuned, through a concert-hall reverb, with each
transposing part played at concert pitch.

Each line is shaped as a player would shape it (`shapeLine`). A new dynamic
arrives part of the way at once and blooms the rest over about a second, even
inside held notes, so a sudden forte grows instead of jumping and a hairpin is
a continuous swell. Accents are a small lift on one note, not a new level.
Notes that follow each other directly in a bowed or blown line overlap slightly
and start without a fresh attack. Timing and level vary by a few milliseconds
and under a decibel. The hall has a short pre-delay, early reflections and a
tail that darkens as it decays, and a gentle high shelf takes the close-miked
edge off the samples.

Dynamics are read as levels in decibels, about 26 dB from pp to ff, and soft
notes are also played darker through a low-pass filter. abcjs's automatic beat
accents are turned off (`nobeataccents`), so only the score's markings change
the level. The master bus uses only a limiter, so quiet passages aren't lifted.

| Source | Licence | Used for |
| --- | --- | --- |
| [tonejs-instruments](https://github.com/nbrosowsky/tonejs-instruments) (mostly Philharmonia Orchestra samples) | CC BY 3.0 | violin, cello, bass, harp, guitars, flute, clarinet, bassoon, saxes, brass, xylophone, piano, organ, harmonium |
| [VSCO 2 Community Edition](https://github.com/sgossner/VSCO-2-CE) | CC0 | viola section, oboe, English horn, piccolo, timpani, marimba, drums |
| MusyngKite (General MIDI) | — | vibraphone, harpsichord, synthesizer, voices (no free real recording found) |

The sources were recorded tens of decibels apart. Each instrument carries a
trim, measured from its samples, that brings it to a common level. VSCO names
notes an octave low (its "C4" sounds C5, measured), and its timpani hits are
named by drum, so their pitches were measured by autocorrelation. Playback
slows over the last two bars and holds the final chord, matching the *rit.* and
fermata in the score. Only the recordings a piece uses are downloaded, on its
first play.

### What makes the parts playable

The brief sent to the model is built from
[`INSTRUMENT_SPECS`](backend/options.py), which carries for every instrument its
clef, its **transposition**, and two written ranges — one a beginner can rely on,
one an advanced player can be asked for.

That is what stops the usual failure modes. A B♭ trumpet part comes back written
a major 2nd above concert pitch with its own key signature; a beginner flute part
stays under G5; a cello reads bass clef and a viola reads alto; piano and harp get
a braced grand staff; a snare drum gets a one-line rhythm staff on MIDI channel 10.

Six answers become the brief:

| Input | Effect on the music |
| --- | --- |
| **Instruments**, and how many of each (browse and hear each one before adding it) | "Violin ×3" becomes Violin I, II and III — three *independent* lines, not one line tripled. Capped per instrument (a section divides, a drum set doesn't); the roster as a whole is uncapped, so a full orchestra is a legitimate ask. |
| **Group** — middle school … professional | How much rehearsal time to assume, and how much doubling the parts need. |
| **Level** — the four bands, by years played | Keys, rhythms, ranges, positions and technique. Writing above a group's level is the worst failure here, so this constrains hardest. |
| **Mood** | The theme of the piece: it picks the key, tempo, register, articulation and harmony. |
| **Conductor — yes** | The music may use tempo changes, fermatas and cued entries, and a **Conductor** tab is generated with an interpretation summary, tempo map, cues by measure and part, rehearsal marks, and beat-pattern guidance. |
| **Conductor — no** | One steady tempo throughout, no rubato, and the cues players need to stay together written into the parts themselves. |

### Nobody takes the model's word for it

Models are fluent about music and unreliable at counting. On the first real run,
`gpt-4o` returned a piece whose bars looked plausible and didn't add up:

```
Violin I bar 1 is 10 units long, needs 8
Violin I bar 2 is 11 units long, needs 8
... 34 defects across 3 parts
```

That is invisible in the JSON and obvious the moment anyone tries to play it. So
[backend/abc_check.py](backend/abc_check.py) reads the returned notation and adds
it up — every bar, in every part, in units of the unit note length. It handles
the syntax that changes a duration (chords, tuplets, broken rhythm, pickups,
repeats) and ignores the syntax that can't (slurs, ties, dynamics, accidentals).
It also extracts pitches, so a part that strays outside the range that level of
player can reach is caught the same way.

When something is wrong, `composer.py` hands the model **its own defect list** —
naming the part and the bar — and asks for a correction. One round only: it
reliably clears arithmetic slips, and past that the model starts recomposing
rather than correcting, at which point a fresh generation is the better answer.
The repair is kept only if it actually reduced the defect count.

Anything still broken ships **with a warning** rather than being presented as
finished. The user gets the music and is told to check the alignment.

### Choosing a model

This matters more than anything else in the pipeline. Early on, with the same
prompt and request, `gpt-4o` produced 34 malformed bars across 3 parts where
`gpt-5.5` was clean on the first pass. Later, on one 15-part brief, the mean
number of distinct pitches per part (the closest cheap measure of "written
rather than generated") was:

| Model | Distinct pitches per part |
| --- | --- |
| `gpt-6.1-sol` | 65.6 |
| `gpt-5.6-sol` | 55.7 |
| `gpt-6-astra` | 55.5 |
| `gpt-6-sol` | 41.3 |
| `gpt-6-luna` | 26.6, and dynamics on only 5 parts of 15 |

The code's default is `gpt-6.1-sol` (`DEFAULT_MODEL` in
[composer.py](backend/composer.py)). `OPENAI_MODEL` in `backend/.env`
overrides it for every call in both modes. Each saved piece records the model
that wrote it, in its `model` field.

Because the model is configurable and model families disagree about `max_tokens`
and `temperature`, an unsupported parameter is detected from OpenAI's own 400,
dropped or renamed, and the call retried — rather than surfacing as a failed
generation.

### Getting the music out

Each tab downloads as `.abc` (opens in MuseScore) or MIDI (imports anywhere).
A deep-composed piece also downloads as MusicXML (the full score). Each tab
prints on its own — the print stylesheet drops the app chrome so the sheet comes
out on white paper.

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
python backend/smoke_test.py          # backend, no credentials needed
python check_frontend_imports.py      # React imports resolve (stopgap, no Node here)
```

Boots the app with Firebase stubbed out — no credentials or network needed — and
checks routing, validation, the auth guard, the signup rollback path, and score
generation with the model call mocked (including the divided-section expansion,
grand-staff bracing, the ragged-measure warning, and the OpenAI failure modes).

To verify against your **real** Firebase project, start the server and run:

```sh
python backend/e2e_test.py
```

It signs up `aura-e2e-delete-me@example.com`, reads the document back out of
Firestore, logs in through Identity Toolkit for a real ID token (the same call
the browser SDK makes), exercises `GET`/`PATCH /api/me` with it, then deletes the
account and the document.
