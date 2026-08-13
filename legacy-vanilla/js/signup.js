/*
 * Signup page.
 *
 * The account is created by the Flask API, not from here: Flask validates the
 * whole payload, then creates the Firebase Auth user and the Firestore profile
 * together. That ordering means a rejected profile never leaves an orphan
 * account behind. Once it succeeds we sign in normally with the Firebase SDK.
 *
 * Note what this module does NOT import at the top: api.js and firebase.js, both
 * of which pull the Firebase SDK off a CDN. A static import of either means one
 * blocked request leaves the whole form dead and the dropdowns empty. They're
 * loaded on submit instead, so the form always renders and fills itself in.
 */

import { API_BASE } from "./firebase-config.js";
import { OPTIONS } from "./options-data.js";
import {
  $,
  applyFieldErrors,
  clearFieldErrors,
  initPasswordToggles,
  populateSelect,
  setFieldError,
  setFormMessage,
  withPendingState,
} from "./ui.js";
import {
  ageFromBirthdate,
  normalizeEmail,
  passwordStrength,
  validateBirthdate,
  validateEmail,
  validatePassword,
} from "./validate.js";

const STRENGTH_LABELS = ["Too weak", "Weak", "Fair", "Good", "Strong"];
const STRENGTH_CLASSES = ["on-weak", "on-weak", "on-fair", "on-good", "on-strong"];

const FIELDS = [
  "email", "password", "confirm", "birthdate",
  "gender", "nationality", "affiliation", "instrument", "genre",
];

const SELECT_FIELDS = ["gender", "nationality", "affiliation", "instrument", "genre"];

document.addEventListener("DOMContentLoaded", () => {
  initPasswordToggles();

  const form = $("signup-form");
  const submitBtn = $("submit-btn");
  const password = $("password");
  const confirm = $("confirm");
  const birthdate = $("birthdate");

  const today = new Date();
  birthdate.max = today.toISOString().slice(0, 10);
  birthdate.min = `${today.getFullYear() - 120}-01-01`;

  FIELDS.forEach((id) => {
    const el = $(id);
    el.addEventListener("input", () => setFieldError(id, ""));
    el.addEventListener("change", () => setFieldError(id, ""));
  });

  password.addEventListener("input", () => {
    renderStrength(password.value);
    if (confirm.value) setFieldError("confirm", "");
  });

  birthdate.addEventListener("change", () => {
    const age = ageFromBirthdate(birthdate.value);
    $("age-hint").textContent =
      age !== null && age >= 0 && age <= 120 ? `You are ${age} years old.` : "";
  });

  // Render from the bundled copy right away so the form is never blank, then
  // let the server's copy override it if it's reachable.
  applyOptions(OPTIONS);
  refreshOptionsFromApi();

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    handleSubmit(submitBtn);
  });
});

async function handleSubmit(submitBtn) {
  setFormMessage("");
  clearFieldErrors(FIELDS);

  const values = Object.fromEntries(FIELDS.map((id) => [id, $(id).value]));

  const errors = {
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

  if (applyFieldErrors(FIELDS, errors)) {
    setFormMessage("Please fix the highlighted fields.", "error");
    return;
  }

  const email = normalizeEmail(values.email);
  const restore = withPendingState(submitBtn, "Creating account…");

  let api;
  let fb;
  try {
    [api, fb] = await Promise.all([import("./api.js"), import("./firebase.js")]);
  } catch {
    setFormMessage(
      "Couldn't load the Firebase SDK. Check your connection and reload the page.",
      "error"
    );
    restore();
    return;
  }

  try {
    await api.signUp({ ...values, email });
  } catch (error) {
    if (error instanceof api.ApiError && Object.keys(error.fieldErrors).length) {
      applyFieldErrors(FIELDS, error.fieldErrors);
    }
    setFormMessage(error.message, "error");
    restore();
    return;
  }

  // Account exists now. Sign in so the user lands logged in; if that trips,
  // fall back to the login page rather than leaving them stranded.
  setFormMessage("Account created! Signing you in…", "success");
  try {
    await fb.setPersistence(fb.auth, fb.browserLocalPersistence);
    await fb.signInWithEmailAndPassword(fb.auth, email, values.password);
    window.location.href = "welcome.html";
  } catch (error) {
    console.warn("Auto sign-in after signup failed:", fb.describeAuthError(error));
    sessionStorage.setItem("aura.justRegistered", email);
    window.location.href = "index.html";
  }
}

/* Fill the five dropdowns, keeping any choice the user has already made. */
function applyOptions(options) {
  const chosen = Object.fromEntries(SELECT_FIELDS.map((id) => [id, $(id).value]));

  populateSelect("gender", options.genders, "Select gender");
  populateSelect("nationality", options.nationalities, "Select nationality");
  populateSelect("affiliation", options.affiliations, "Select where you play");
  populateSelect("instrument", options.instruments, "Select instrument");
  populateSelect("genre", options.genres, "Select genre");

  SELECT_FIELDS.forEach((id) => {
    if (chosen[id]) $(id).value = chosen[id];
  });
}

/* The server is the authority on what's valid, so prefer its lists when we can
   reach it. Staying quiet on failure is deliberate: the bundled copy is already
   on screen and works, so there's nothing for the user to act on. A real problem
   still surfaces at submit time, where it actually blocks them. */
async function refreshOptionsFromApi() {
  try {
    const response = await fetch(`${API_BASE}/api/options`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const options = await response.json();
    if (JSON.stringify(options) !== JSON.stringify(OPTIONS)) applyOptions(options);
  } catch (error) {
    console.warn("Using bundled dropdown options —", error.message);
  }
}

function renderStrength(value) {
  const wrap = $("strength");
  const bars = wrap.querySelectorAll(".strength__bar");

  if (!value) {
    wrap.hidden = true;
    bars.forEach((bar) => (bar.className = "strength__bar"));
    return;
  }

  wrap.hidden = false;
  const score = passwordStrength(value);
  bars.forEach((bar, i) => {
    bar.className = "strength__bar" + (i < score ? ` ${STRENGTH_CLASSES[score]}` : "");
  });
  $("strength-label").textContent = STRENGTH_LABELS[score];
}
