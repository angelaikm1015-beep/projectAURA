/* Firebase app + auth singletons, and the error-code translations we show users. */

import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import {
  browserLocalPersistence,
  browserSessionPersistence,
  createUserWithEmailAndPassword,
  getAuth,
  onAuthStateChanged,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
  signOut,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js";

import { firebaseConfig } from "./firebase-config.js";

export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);

export {
  browserLocalPersistence,
  browserSessionPersistence,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
  signOut,
};

/*
 * Firebase deliberately returns `auth/invalid-credential` for both a wrong
 * password and an unknown email, so attackers can't probe for valid accounts.
 * Keep that property: don't split these into distinct messages.
 */
const AUTH_ERROR_MESSAGES = {
  "auth/invalid-credential": "Email or password is incorrect.",
  "auth/invalid-login-credentials": "Email or password is incorrect.",
  "auth/wrong-password": "Email or password is incorrect.",
  "auth/user-not-found": "Email or password is incorrect.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/missing-password": "Password is required.",
  "auth/user-disabled": "This account has been disabled.",
  "auth/email-already-in-use": "An account with this email already exists.",
  "auth/weak-password": "Password is too weak — use at least 8 characters.",
  "auth/too-many-requests": "Too many attempts. Wait a moment and try again.",
  "auth/network-request-failed": "Network error. Check your connection and try again.",
  "auth/operation-not-allowed":
    "Email/password sign-in isn't enabled for this Firebase project yet.",
};

export function describeAuthError(error) {
  if (error?.code === "auth/invalid-api-key" || error?.code === "auth/configuration-not-found") {
    return "Firebase isn't configured yet — fill in frontend/js/firebase-config.js.";
  }
  return AUTH_ERROR_MESSAGES[error?.code] || "Something went wrong. Please try again.";
}
