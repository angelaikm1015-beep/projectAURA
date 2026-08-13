/* Login page — authenticates directly against Firebase Auth. */

import {
  auth,
  browserLocalPersistence,
  browserSessionPersistence,
  describeAuthError,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
} from "./firebase.js";
import {
  $,
  clearFieldErrors,
  initPasswordToggles,
  setFieldError,
  setFormMessage,
  withPendingState,
} from "./ui.js";
import { normalizeEmail, validateEmail } from "./validate.js";

const FIELDS = ["email", "password"];

document.addEventListener("DOMContentLoaded", () => {
  initPasswordToggles();

  const form = $("login-form");
  const submitBtn = $("submit-btn");

  // Signup redirects here after creating an account it couldn't auto-sign-in.
  const justRegistered = sessionStorage.getItem("aura.justRegistered");
  if (justRegistered) {
    sessionStorage.removeItem("aura.justRegistered");
    $("email").value = justRegistered;
    setFormMessage("Account created — log in to continue.", "success");
    $("password").focus();
  }

  FIELDS.forEach((id) => {
    $(id).addEventListener("input", () => setFieldError(id, ""));
  });

  $("forgot").addEventListener("click", async (event) => {
    event.preventDefault();
    const email = $("email").value;
    const emailError = validateEmail(email);
    if (emailError) {
      setFieldError("email", "Enter your email first, then click again.");
      $("email").focus();
      return;
    }
    try {
      await sendPasswordResetEmail(auth, normalizeEmail(email));
    } catch (error) {
      // A missing account still reports success: don't reveal who has an account.
      if (error?.code !== "auth/user-not-found") {
        setFormMessage(describeAuthError(error), "error");
        return;
      }
    }
    setFormMessage(`If an account exists for ${normalizeEmail(email)}, a reset link is on its way.`, "success");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormMessage("");
    clearFieldErrors(FIELDS);

    const email = $("email").value;
    const password = $("password").value;

    const emailError = validateEmail(email);
    // Only check that something was typed — tightened rules shouldn't lock out
    // accounts created under the old ones.
    const passwordError = password ? null : "Password is required.";
    setFieldError("email", emailError);
    setFieldError("password", passwordError);
    if (emailError || passwordError) {
      $(emailError ? "email" : "password").focus();
      return;
    }

    const restore = withPendingState(submitBtn, "Logging in…");
    try {
      // "Keep me signed in" survives a browser restart; otherwise the session
      // ends with the tab.
      await setPersistence(
        auth,
        $("remember").checked ? browserLocalPersistence : browserSessionPersistence
      );
      await signInWithEmailAndPassword(auth, normalizeEmail(email), password);
      window.location.href = "welcome.html";
    } catch (error) {
      setFormMessage(describeAuthError(error), "error");
      $("password").value = "";
      $("password").focus();
      restore();
    }
  });
});
