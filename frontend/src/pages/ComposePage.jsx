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
import { ChipGroup, ChoiceCards, Field, SelectField, TextAreaField } from "../components/fields";
import { GenerationProgress } from "../components/generation-progress";
import { InstrumentPicker, totalParts } from "../components/instrument-picker";
import { ScoreView } from "../components/score-view";
import { useAuth } from "../context/AuthContext";
import {
  ApiError, compose, fetchComposeEstimate, fetchComposeProgress, fetchOptions,
  reviseScore, undoRevision, updateScore, waitForComposition,
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
  mode: "light",
  // Who picks the instruments: the user ticks them, or GPT chooses an ensemble
  // of the size asked for.
  instrumentMode: "manual",
  orchestraScale: "",
  // Optional musical details. "" means Auto — the mood decides.
  timeSignature: "",
  keyMode: "",
  keyRoot: "",
  lengthUnit: "",
  lengthValue: "",
};

const INSTRUMENT_MODES = [
  { value: "manual", label: "I'll pick the instruments" },
  { value: "auto", label: "Let GPT choose" },
];

// Kept in step with ORCHESTRA_SCALES in backend/choices.py.
const SCALE_CHOICES = [
  {
    value: "chamber",
    label: "Chamber group",
    description: "4–7 parts: a string quartet with a wind or two, a wind quintet, a small mixed group.",
    parts: 6,
  },
  {
    value: "small",
    label: "Small orchestra",
    description: "8–13 parts: strings with pairs of winds, horns, perhaps timpani.",
    parts: 11,
  },
  {
    value: "full",
    label: "Full orchestra",
    description: "14–22 parts: full strings, woodwinds, brass, timpani and percussion.",
    parts: 18,
  },
];

const AUTO = { value: "", label: "Auto" };
const TIME_CHOICES = [AUTO, ...["2/4", "3/4", "4/4", "6/8", "12/8", "5/4"]
  .map((value) => ({ value, label: value }))];
const KEY_MODE_CHOICES = [AUTO, { value: "major", label: "Major" }, { value: "minor", label: "Minor" }];
const KEY_ROOT_CHOICES = [AUTO, ...[
  ["C", "C"], ["Db", "D♭"], ["D", "D"], ["Eb", "E♭"], ["E", "E"], ["F", "F"],
  ["F#", "F♯"], ["G", "G"], ["Ab", "A♭"], ["A", "A"], ["Bb", "B♭"], ["B", "B"],
].map(([value, label]) => ({ value, label }))];
const LENGTH_UNITS = [AUTO, { value: "minutes", label: "Minutes" }, { value: "bars", label: "Bars" }];
// Kept in step with LENGTH_LIMITS in backend/choices.py.
const LENGTH_LIMITS = { minutes: [1, 10], bars: [16, 200] };

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

// Two ways to write the piece. Light is quick because the model writes only the
// themes and harmony and the parts are arranged from them by rule; deep has the
// model compose every note of every part, and takes as long as that takes.
const MODE_CHOICES = [
  {
    value: "light",
    label: "Light composing",
    description:
      "Fast — usually a few minutes. GPT writes the themes and harmony, and the " +
      "parts are arranged from them automatically.",
  },
  {
    value: "deep",
    label: "Deep composing",
    description:
      "GPT plans the piece, then writes the full score — every instrument together, " +
      "passage by passage — so the parts are composed against each other. Richer, " +
      "and it takes longer: usually 5–15 minutes.",
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
  const [pendingMode, setPendingMode] = useState("light");
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
  // A deep piece is polled by waitForComposition instead, which needs every
  // reading anyway to know when to collect the result.
  useEffect(() => {
    if (!pending || pendingMode === "deep" || !requestId.current) return undefined;
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
  }, [pending, pendingMode]);

  // What to promise before the button is pressed, keyed to the actual roster —
  // or, when GPT is choosing, to the middle of the size asked for.
  const plannedParts = values.instrumentMode === "auto"
    ? SCALE_CHOICES.find((scale) => scale.value === values.orchestraScale)?.parts || 0
    : totalParts(values.instruments);
  useEffect(() => {
    if (!plannedParts) { setEstimate(null); return undefined; }
    let cancelled = false;
    fetchComposeEstimate(plannedParts, values.mode, values.level)
      .then((range) => { if (!cancelled) setEstimate(range); })
      .catch(() => { /* the button still works without a promise attached */ });
    return () => { cancelled = true; };
  }, [plannedParts, values.mode, values.level]);

  const set = (field) => (event) => {
    const { value } = event.target;
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => ({ ...prev, [field]: null }));
  };

  const choose = (field) => (value) => {
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
    const auto = values.instrumentMode === "auto";
    const [low, high] = LENGTH_LIMITS[values.lengthUnit] || [];
    const length = Number(values.lengthValue);
    return {
      instruments: auto || values.instruments.length ? null : "Pick at least one instrument.",
      orchestraScale: !auto || values.orchestraScale ? null : "Choose how big the orchestra should be.",
      length: !values.lengthUnit || (Number.isInteger(length) && length >= low && length <= high)
        ? null
        : `Choose between ${low} and ${high} ${values.lengthUnit}.`,
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
      instruments: values.instrumentMode === "auto" ? [] : values.instruments,
      ensembleGroup: values.ensembleGroup,
      level: values.level,
      mood: values.mood,
      conductor: values.conductor === "yes",
      notes: values.notes.trim(),
      mode: values.mode,
      instrumentMode: values.instrumentMode,
      orchestraScale: values.instrumentMode === "auto" ? values.orchestraScale : "",
      timeSignature: values.timeSignature,
      keyMode: values.keyMode,
      keyRoot: values.keyRoot,
      length: values.lengthUnit
        ? { unit: values.lengthUnit, value: Number(values.lengthValue) }
        : null,
    };
  }

  async function runCompose(sent) {
    setMessage(null);
    setProgress(null);
    requestId.current = (crypto.randomUUID?.() ?? String(Date.now() + Math.random()));
    setPendingMode(sent.mode);
    setPending(true);
    try {
      let result = await compose({ ...sent, requestId: requestId.current });
      if (result.mode === "deep") {
        result = await waitForComposition(result.requestId, setProgress);
      }
      setScore(result.score);
      // The server's copy of the brief, when it sends one, also says which
      // instruments GPT chose and why.
      setBrief(describeBrief(options, result.request ? { ...sent, ...result.request } : sent));
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
        scoreId={saved?.id}
        brief={brief}
        pending={pending}
        pendingLabel={progress?.label || WAIT_STEPS[step]}
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

        <ChipGroup
          label="Instruments"
          size="large"
          options={INSTRUMENT_MODES}
          value={values.instrumentMode}
          onChange={choose("instrumentMode")}
        />

        {values.instrumentMode === "auto" ? (
          <>
            <ChoiceCards
              label="How big is the orchestra?"
              name="orchestraScale"
              options={SCALE_CHOICES}
              value={values.orchestraScale}
              error={errors.orchestraScale}
              onChange={set("orchestraScale")}
            />
            <p className="hint hint-standalone">
              GPT picks the instruments that suit the mood and your players, and the score
              shows which it chose.
            </p>
          </>
        ) : (
          <InstrumentPicker
            label="Which instruments?"
            groups={options.scoreInstruments}
            info={options.instrumentInfo}
            value={values.instruments}
            error={errors.instruments}
            onChange={setInstruments}
          />
        )}

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

        <p className="section-title">Musical details <span className="section-title__note">(optional)</span></p>
        <p className="hint hint-standalone">
          Leave any of these on Auto and the mood decides.
        </p>

        <ChipGroup
          label="Time signature"
          options={TIME_CHOICES}
          value={values.timeSignature}
          error={errors.timeSignature}
          onChange={choose("timeSignature")}
        />

        <ChipGroup
          label="Major or minor"
          options={KEY_MODE_CHOICES}
          value={values.keyMode}
          error={errors.keyMode}
          onChange={choose("keyMode")}
        />

        <ChipGroup
          label="Key"
          options={KEY_ROOT_CHOICES}
          value={values.keyRoot}
          error={errors.keyRoot}
          onChange={choose("keyRoot")}
        />

        <ChipGroup
          label="Length"
          options={LENGTH_UNITS}
          value={values.lengthUnit}
          error={values.lengthUnit ? null : errors.length}
          onChange={(unit) => {
            setValues((prev) => ({
              ...prev,
              lengthUnit: unit,
              lengthValue: unit === "minutes" ? "3" : unit === "bars" ? "64" : "",
            }));
            setErrors((prev) => ({ ...prev, length: null }));
          }}
        />
        {values.lengthUnit && (
          <Field
            label={values.lengthUnit === "minutes" ? "How many minutes?" : "How many bars?"}
            type="number"
            inputMode="numeric"
            min={LENGTH_LIMITS[values.lengthUnit][0]}
            max={LENGTH_LIMITS[values.lengthUnit][1]}
            value={values.lengthValue}
            error={errors.length}
            hint={`${LENGTH_LIMITS[values.lengthUnit][0]}–${LENGTH_LIMITS[values.lengthUnit][1]} ${values.lengthUnit}.`}
            onChange={set("lengthValue")}
          />
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

        <p className="section-title">How should it be composed?</p>

        <ChoiceCards
          label="Composing mode"
          name="mode"
          options={MODE_CHOICES}
          value={values.mode}
          error={errors.mode}
          onChange={set("mode")}
        />

        <SubmitButton pending={pending} pendingLabel={WAIT_STEPS[step]}>
          Generate the sheet music
        </SubmitButton>

        {pending && (
          <GenerationProgress
            mode={pendingMode}
            progress={progress}
            elapsed={elapsed}
            fallbackLabel={WAIT_STEPS[step]}
          />
        )}

        {!pending && estimate && plannedParts > 0 && (
          <p className="hint hint-standalone">
            {values.mode === "deep" ? "Deep composing " : ""}
            {plannedParts} part{plannedParts === 1 ? "" : "s"} usually takes{" "}
            <strong>{Math.max(1, Math.round(estimate.lowSeconds / 60))}–
            {Math.round(estimate.highSeconds / 60)} minutes</strong>
            {values.mode === "deep"
              ? " — GPT writes the full score, every instrument together, passage by " +
                "passage. It's saved to your library when it's done, even if you close " +
                "this page."
              : " — every bar of every part is written, then checked and corrected " +
                "before you see it."}
          </p>
        )}
      </form>
    </div>
  );
}
