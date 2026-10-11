/*
 * Compose — the form that briefs the generator, and the score that comes back.
 *
 * Five answers decide the music: who is playing (the instruments, and how many
 * of each), what kind of group they are, how long they've been playing, the
 * mood the piece should carry, and whether someone is conducting. The backend
 * turns those into a brief for the model; see backend/composer.py.
 *
 * Generation is slow — the model is writing a dozen aligned staves — so the
 * request is left to run and the wait is narrated rather than hidden behind a
 * spinner that looks stuck.
 *
 * The page has two states and only ever shows one of them. Before a score
 * exists it is the brief. Once a score exists the brief steps out of the way
 * entirely and the sheet gets the whole screen; the answers that produced it
 * are carried along and shown beside it, and another piece can be asked for
 * without going back to the form at all.
 */

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AppHeader, PageIntro } from "../components/app-header";
import { FormMessage, SubmitButton } from "../components/chrome";
import { ChoiceCards, SelectField, TextAreaField } from "../components/fields";
import { GenerationProgress } from "../components/generation-progress";
import { InstrumentPicker, totalParts } from "../components/instrument-picker";
import { ScoreView } from "../components/score-view";
import { useAuth } from "../context/AuthContext";
import {
  ApiError, compose, fetchComposeEstimate, fetchComposeProgress, fetchOptions,
  reviseScore, undoRevision, updateScore,
} from "../lib/api";
import { describeBrief } from "../lib/brief";
import { OPTIONS } from "../lib/options-data";

const EMPTY = {
  instruments: [],
  ensembleGroup: "",
  level: "",
  mood: "",
  conductor: "",
  notes: "",
};

const CONDUCTOR_CHOICES = [
  {
    value: "yes",
    label: "Yes, we have a conductor",
    description:
      "The piece can use tempo changes, fermatas and cued entries — and the conductor " +
      "gets their own score with a tempo map, cues and rehearsal marks.",
  },
  {
    value: "no",
    label: "No conductor",
    description:
      "One steady tempo throughout, with the cues players need to stay together " +
      "written into the parts themselves.",
  },
];

// Only used for the second or two before the server's first progress report
// arrives. After that the page shows what is really happening rather than a
// rotating guess.
const WAIT_STEPS = [
  "Reading the brief…",
  "Choosing a key and tempo for the mood…",
  "Writing the main theme…",
  "Working out the harmony, bar by bar…",
  "Deciding what each player does…",
  "Writing the parts…",
  "Adding up every bar…",
  "Correcting anything that doesn't fit…",
  "Engraving the sheets…",
];

export default function ComposePage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [pending, setPending] = useState(false);
  const [step, setStep] = useState(0);
  const [progress, setProgress] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [estimate, setEstimate] = useState(null);
  const requestId = useRef(null);
  const [score, setScore] = useState(null);
  const [brief, setBrief] = useState(null);
  const [saved, setSaved] = useState(null); // { id, visibility } once it is stored
  const [sharing, setSharing] = useState(false);
  const [revising, setRevising] = useState(false);
  const [revision, setRevision] = useState(null); // { summary, canUndo }
  const [options, setOptions] = useState(OPTIONS);

  // Same pattern as signup: render from the bundled copy immediately, prefer the
  // server's lists when they arrive, since the server is what validates.
  useEffect(() => {
    let cancelled = false;
    fetchOptions()
      .then((fresh) => { if (!cancelled) setOptions(fresh); })
      .catch((error) => console.warn("Using bundled options —", error.message));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!pending) return undefined;
    setStep(0);
    const timer = setInterval(
      () => setStep((current) => Math.min(current + 1, WAIT_STEPS.length - 1)),
      9000
    );
    return () => clearInterval(timer);
  }, [pending]);

  // A ticking clock the user can see, independent of the server poll.
  useEffect(() => {
    if (!pending) return undefined;
    setElapsed(0);
    const started = Date.now();
    const timer = setInterval(() => setElapsed((Date.now() - started) / 1000), 500);
    return () => clearInterval(timer);
  }, [pending]);

  // Ask the server where it has got to. A 404 just means it hasn't registered
  // the job yet, or it has already finished — neither is worth showing.
  useEffect(() => {
    if (!pending || !requestId.current) return undefined;
    let stopped = false;
    const id = requestId.current;

    const poll = async () => {
      try {
        const state = await fetchComposeProgress(id);
        if (!stopped) setProgress(state);
      } catch {
        /* not registered yet, or gone */
      }
    };
    poll();
    const timer = setInterval(poll, 2500);
    return () => { stopped = true; clearInterval(timer); };
  }, [pending]);

  // What to promise before the button is pressed, keyed to the actual roster.
  const plannedParts = totalParts(values.instruments);
  useEffect(() => {
    if (!plannedParts) { setEstimate(null); return undefined; }
    let cancelled = false;
    fetchComposeEstimate(plannedParts)
      .then((range) => { if (!cancelled) setEstimate(range); })
      .catch(() => { /* the button still works without a promise attached */ });
    return () => { cancelled = true; };
  }, [plannedParts]);

  const set = (field) => (event) => {
    const { value } = event.target;
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => ({ ...prev, [field]: null }));
  };

  const setInstruments = (instruments) => {
    setValues((prev) => ({ ...prev, instruments }));
    setErrors((prev) => ({ ...prev, instruments: null }));
  };

  const moodHint = options.moods?.find((m) => m.value === values.mood)?.guidance;
  const groupHint = options.ensembleGroups?.find((g) => g.value === values.ensembleGroup)?.guidance;

  function validate() {
    return {
      instruments: values.instruments.length ? null : "Pick at least one instrument.",
      ensembleGroup: values.ensembleGroup ? null : "Select who is playing.",
      level: values.level ? null : "Select a playing level.",
      mood: values.mood ? null : "Select a mood.",
      conductor: values.conductor ? null : "Say whether there's a conductor.",
    };
  }

  /* What actually gets sent. Both the first generation and every "write
     another" after it go out as exactly this, so the second piece is written
     to the same brief as the first. */
  function payload() {
    return {
      instruments: values.instruments,
      ensembleGroup: values.ensembleGroup,
      level: values.level,
      mood: values.mood,
      conductor: values.conductor === "yes",
      notes: values.notes.trim(),
    };
  }

  async function runCompose(sent) {
    setMessage(null);
    setProgress(null);
    requestId.current = (crypto.randomUUID?.() ?? String(Date.now() + Math.random()));
    setPending(true);
    try {
      const result = await compose({ ...sent, requestId: requestId.current });
      setScore(result.score);
      setBrief(describeBrief(options, sent));
      // The API saves every piece as it makes it. Without an id the score is
      // still on screen and still downloadable, it just can't be shared.
      setSaved(result.scoreId
        ? { id: result.scoreId, visibility: "private", caption: "" }
        : null);
      setRevision(null);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length) {
        setErrors(error.fieldErrors);
      }
      setMessage({ text: error.message, kind: "error" });
    } finally {
      setPending(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setMessage(null);

    const found = validate();
    if (Object.values(found).some(Boolean)) {
      setErrors(found);
      setMessage({ text: "Please fix the highlighted fields.", kind: "error" });
      return;
    }
    setErrors({});

    await runCompose(payload());
  }

  /* Both arguments are optional: sharing sends a visibility and usually a
     caption, editing the caption of an already-posted piece sends only the
     caption. */
  /* An edit, not a regeneration: the piece on screen is replaced by the same
     piece with one thing changed. Errors are thrown back so the box that asked
     can show them next to the request. */
  async function handleRevise(request) {
    if (!saved) return;
    setRevising(true);
    try {
      const result = await reviseScore(saved.id, request);
      setScore(result.score);
      setRevision({ summary: result.summary, canUndo: true });
    } finally {
      setRevising(false);
    }
  }

  async function handleUndo() {
    if (!saved) return;
    setRevising(true);
    try {
      const { score: restored } = await undoRevision(saved.id);
      setScore(restored);
      setRevision(null);
    } catch (error) {
      setMessage({ text: error.message, kind: "error" });
    } finally {
      setRevising(false);
    }
  }

  async function handleShare(visibility, caption) {
    if (!saved) return;
    setSharing(true);
    try {
      const changes = {};
      if (visibility !== undefined) changes.visibility = visibility;
      if (caption !== undefined) changes.caption = caption;
      await updateScore(saved.id, changes);
      setSaved((current) => ({ ...current, ...changes }));
    } catch (error) {
      setMessage({ text: error.message, kind: "error" });
    } finally {
      setSharing(false);
    }
  }

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  // The score reader is full-screen and has no room for the top bar, so the
  // same destinations ride along in its rail instead.
  const railNav = (
    <nav className="compose-nav">
      <Link className="link" to="/welcome">My library</Link>
      <Link className="link" to="/gallery">Gallery</Link>
      <button type="button" className="link link-button" onClick={handleLogout}>
        Log out
      </button>
    </nav>
  );

  // A score on screen replaces the brief rather than sitting on top of it: the
  // sheet is the thing the user came for, and it needs every pixel.
  if (score) {
    return (
      <ScoreView
        score={score}
        brief={brief}
        pending={pending}
        pendingLabel={WAIT_STEPS[step]}
        error={message?.kind === "error" ? message.text : null}
        visibility={saved?.visibility}
        caption={saved?.caption}
        onVisibilityChange={saved ? handleShare : undefined}
        saving={sharing}
        onRevise={saved ? handleRevise : undefined}
        onUndo={handleUndo}
        canUndo={Boolean(revision?.canUndo)}
        revising={revising}
        revisionSummary={revision?.summary}
        onRegenerate={() => runCompose(payload())}
        onNewBrief={() => { setScore(null); setMessage(null); }}
        nav={railNav}
      />
    );
  }

  return (
    <div className="compose-page">
      <AppHeader />
      <PageIntro title="Write a new piece">
        Describe your ensemble and we'll write the music. Every piece is saved to
        your library automatically.
      </PageIntro>

      <form className="form compose-form" onSubmit={handleSubmit} noValidate>
        <FormMessage text={message?.text} kind={message?.kind} />

        <p className="section-title">Who's playing</p>

        <InstrumentPicker
          label="Instruments"
          groups={options.scoreInstruments}
          info={options.instrumentInfo}
          value={values.instruments}
          error={errors.instruments}
          onChange={setInstruments}
        />

        <SelectField
          label="What kind of group is this?"
          placeholder="Select the group"
          options={options.ensembleGroups}
          value={values.ensembleGroup}
          error={errors.ensembleGroup}
          onChange={set("ensembleGroup")}
        />
        {groupHint && <p className="hint hint-standalone">{groupHint}</p>}

        <ChoiceCards
          label="How long have they been playing?"
          name="level"
          columns={2}
          options={(options.levels || []).map((level) => ({
            value: level.value,
            label: level.label,
            description: level.years,
          }))}
          value={values.level}
          error={errors.level}
          onChange={set("level")}
        />

        <p className="section-title">The music</p>

        <SelectField
          label="Mood"
          placeholder="Select a mood"
          options={options.moods}
          value={values.mood}
          error={errors.mood}
          onChange={set("mood")}
        />
        {moodHint ? (
          <p className="hint hint-standalone">{moodHint}</p>
        ) : (
          <p className="hint hint-standalone">
            The mood is the theme of the piece — it decides the key, tempo and character.
          </p>
        )}

        <ChoiceCards
          label="Is there a conductor?"
          name="conductor"
          options={CONDUCTOR_CHOICES}
          value={values.conductor}
          error={errors.conductor}
          onChange={set("conductor")}
        />

        <TextAreaField
          label="Anything else? (optional)"
          placeholder="e.g. it's for a graduation concert, keep it under two minutes, feature the cello"
          maxLength={500}
          value={values.notes}
          error={errors.notes}
          onChange={set("notes")}
        />

        <SubmitButton pending={pending} pendingLabel={WAIT_STEPS[step]}>
          Generate the sheet music
        </SubmitButton>

        {pending && (
          <GenerationProgress
            progress={progress}
            elapsed={elapsed}
            fallbackLabel={WAIT_STEPS[step]}
          />
        )}

        {!pending && estimate && plannedParts > 0 && (
          <p className="hint hint-standalone">
            {plannedParts} part{plannedParts === 1 ? "" : "s"} usually takes{" "}
            <strong>{Math.round(estimate.lowSeconds / 60)}–
            {Math.round(estimate.highSeconds / 60)} minutes</strong> — every bar of
            every part is written, then checked and corrected before you see it.
          </p>
        )}
      </form>
    </div>
  );
}
