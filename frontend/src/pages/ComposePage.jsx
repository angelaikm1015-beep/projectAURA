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
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { BrandHeader, FormMessage, SubmitButton } from "../components/chrome";
import { ChoiceCards, SelectField, TextAreaField } from "../components/fields";
import { InstrumentPicker, totalParts } from "../components/instrument-picker";
import { ScoreView } from "../components/score-view";
import { useAuth } from "../context/AuthContext";
import { ApiError, compose, fetchOptions } from "../lib/api";
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

// Shown in turn while the model works, so a two-minute wait reads as progress
// rather than a hang.
const WAIT_STEPS = [
  "Reading the brief…",
  "Choosing a key and tempo for the mood…",
  "Writing the main theme…",
  "Harmonising the other parts…",
  "Writing out every part…",
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
  const [score, setScore] = useState(null);
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
  const maxTotal = options.limits?.maxTotalParts ?? 12;

  function validate() {
    const count = totalParts(values.instruments);
    return {
      instruments: !values.instruments.length
        ? "Pick at least one instrument."
        : count > maxTotal
        ? `That's ${count} parts — ${maxTotal} is the most we can write in one piece.`
        : null,
      ensembleGroup: values.ensembleGroup ? null : "Select who is playing.",
      level: values.level ? null : "Select a playing level.",
      mood: values.mood ? null : "Select a mood.",
      conductor: values.conductor ? null : "Say whether there's a conductor.",
    };
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
    setPending(true);
    setScore(null);

    try {
      const result = await compose({
        instruments: values.instruments,
        ensembleGroup: values.ensembleGroup,
        level: values.level,
        mood: values.mood,
        conductor: values.conductor === "yes",
        notes: values.notes.trim(),
      });
      setScore(result.score);
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

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="compose-page">
      <header className="compose-top">
        <BrandHeader tagline="Describe your ensemble. We'll write the music." />
        <nav className="compose-nav">
          <Link className="link" to="/welcome">Profile</Link>
          <button type="button" className="link link-button" onClick={handleLogout}>
            Log out {user ? `(${user.email})` : ""}
          </button>
        </nav>
      </header>

      {score && <ScoreView score={score} onStartOver={() => setScore(null)} />}

      <form className={`form compose-form${score ? " is-secondary" : ""}`} onSubmit={handleSubmit} noValidate>
        <FormMessage text={message?.text} kind={message?.kind} />

        {score && <p className="section-title">Write another piece</p>}

        <p className="section-title">Who's playing</p>

        <InstrumentPicker
          label="Instruments"
          groups={options.scoreInstruments}
          info={options.instrumentInfo}
          value={values.instruments}
          maxTotal={maxTotal}
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
          <p className="hint hint-standalone" aria-live="polite">
            Writing {totalParts(values.instruments)} parts takes a minute or two — every bar gets
            checked and corrected before you see it. Keep this tab open.
          </p>
        )}
      </form>
    </div>
  );
}
