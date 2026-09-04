/*
 * Signup.
 *
 * The account is created by the Flask API, not from here: Flask validates the
 * whole payload, then creates the Firebase Auth user and the Firestore profile
 * together. That ordering means a rejected profile never leaves an orphan
 * account behind. Once it succeeds we sign in normally with the Firebase SDK.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthCard, BrandHeader, FormMessage, StrengthMeter, SubmitButton } from "../components/chrome";
import { Field, PasswordField, SelectField } from "../components/fields";
import { ApiError, fetchOptions, signUp } from "../lib/api";
import {
  auth,
  browserLocalPersistence,
  describeAuthError,
  setPersistence,
  signInWithEmailAndPassword,
} from "../lib/firebase";
import { OPTIONS } from "../lib/options-data";
import {
  ageFromBirthdate,
  normalizeEmail,
  validateBirthdate,
  validateEmail,
  validatePassword,
} from "../lib/validate";

const EMPTY = {
  email: "", password: "", confirm: "", birthdate: "",
  gender: "", nationality: "", affiliation: "", instrument: "", genre: "",
};

const today = new Date();
const MAX_BIRTHDATE = today.toISOString().slice(0, 10);
const MIN_BIRTHDATE = `${today.getFullYear() - 120}-01-01`;

export default function SignupPage() {
  const navigate = useNavigate();

  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [pending, setPending] = useState(false);

  // Start from the copy bundled at build time so the dropdowns are never empty,
  // then prefer the server's lists — it's the one that validates the submission.
  const [options, setOptions] = useState(OPTIONS);

  useEffect(() => {
    let cancelled = false;
    fetchOptions()
      .then((fresh) => {
        if (!cancelled) setOptions(fresh);
      })
      .catch((error) => {
        // Staying quiet is deliberate: the bundled copy is already on screen and
        // works. A real problem still surfaces at submit, where it blocks them.
        console.warn("Using bundled dropdown options —", error.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const set = (field) => (event) => {
    const { value } = event.target;
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => ({ ...prev, [field]: null }));
  };

  const age = ageFromBirthdate(values.birthdate);

  function validate() {
    return {
      email: validateEmail(values.email),
      password: validatePassword(values.password),
      confirm: !values.confirm
        ? "Please re-enter your password."
        : values.confirm !== values.password
        ? "Passwords do not match."
        : null,
      birthdate: validateBirthdate(values.birthdate),
      gender: values.gender ? null : "Select a gender.",
      nationality: values.nationality ? null : "Select a nationality.",
      affiliation: values.affiliation ? null : "Select where you play.",
      instrument: values.instrument ? null : "Select an instrument.",
      genre: values.genre ? null : "Select a genre.",
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

    const email = normalizeEmail(values.email);
    try {
      await signUp({ ...values, email });
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length) {
        setErrors(error.fieldErrors);
      }
      setMessage({ text: error.message, kind: "error" });
      setPending(false);
      return;
    }

    // Account exists now. Sign in so the user lands logged in; if that trips,
    // fall back to the login page rather than leaving them stranded.
    setMessage({ text: "Account created! Signing you in…", kind: "success" });
    try {
      await setPersistence(auth, browserLocalPersistence);
      await signInWithEmailAndPassword(auth, email, values.password);
      navigate("/compose", { replace: true });
    } catch (error) {
      console.warn("Auto sign-in after signup failed:", describeAuthError(error));
      navigate("/login", { replace: true, state: { email } });
    }
  }

  return (
    <AuthCard wide>
      <BrandHeader tagline="Create your account and tell us how you play." />

      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormMessage text={message?.text} kind={message?.kind} />

        <p className="section-title">Account</p>

        <Field
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          hint="This is also your username."
          value={values.email}
          error={errors.email}
          onChange={set("email")}
        />

        <div className="row">
          <div>
            <PasswordField
              label="Password"
              autoComplete="new-password"
              placeholder="At least 8 characters"
              value={values.password}
              error={errors.password}
              onChange={set("password")}
            />
            <StrengthMeter password={values.password} />
          </div>

          <PasswordField
            label="Re-enter password"
            autoComplete="new-password"
            placeholder="Type it once more"
            value={values.confirm}
            error={errors.confirm}
            onChange={set("confirm")}
          />
        </div>

        <p className="section-title">About you</p>

        <div className="row">
          <Field
            label="Date of birth"
            type="date"
            min={MIN_BIRTHDATE}
            max={MAX_BIRTHDATE}
            hint={age !== null && age >= 0 && age <= 120 ? `You are ${age} years old.` : undefined}
            value={values.birthdate}
            error={errors.birthdate}
            onChange={set("birthdate")}
          />

          <SelectField
            label="Gender"
            placeholder="Select gender"
            options={options.genders}
            value={values.gender}
            error={errors.gender}
            onChange={set("gender")}
          />
        </div>

        <SelectField
          label="Nationality"
          placeholder="Select nationality"
          options={options.nationalities}
          value={values.nationality}
          error={errors.nationality}
          onChange={set("nationality")}
        />

        <p className="section-title">Your music</p>

        <SelectField
          label="Where do you play?"
          placeholder="Select where you play"
          options={options.affiliations}
          value={values.affiliation}
          error={errors.affiliation}
          onChange={set("affiliation")}
        />

        <div className="row">
          <SelectField
            label="Instrument"
            placeholder="Select instrument"
            options={options.instruments}
            value={values.instrument}
            error={errors.instrument}
            onChange={set("instrument")}
          />

          <SelectField
            label="Favorite genre"
            placeholder="Select genre"
            options={options.genres}
            value={values.genre}
            error={errors.genre}
            onChange={set("genre")}
          />
        </div>

        <SubmitButton pending={pending} pendingLabel="Creating account…">
          Create account
        </SubmitButton>
      </form>

      <p className="form-footer">
        Already have an account? <Link className="link" to="/login">Log in</Link>
      </p>
    </AuthCard>
  );
}
