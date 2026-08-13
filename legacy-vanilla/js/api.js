/* Thin client for the Flask API. Attaches the Firebase ID token when needed. */

import { auth } from "./firebase.js";
import { API_BASE } from "./firebase-config.js";

export class ApiError extends Error {
  constructor(message, status, fieldErrors) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors || {};
  }
}

async function request(path, { method = "GET", body, authenticated = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (authenticated) {
    const user = auth.currentUser;
    if (!user) throw new ApiError("You need to be logged in.", 401);
    headers.Authorization = `Bearer ${await user.getIdToken()}`;
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the Flask backend running?", 0);
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      payload.message || `Request failed (${response.status}).`,
      response.status,
      payload.errors
    );
  }
  return payload;
}

/** Dropdown contents for the signup form. */
export const fetchOptions = () => request("/api/options");

/** Create the Firebase Auth user and its Firestore profile in one call. */
export const signUp = (payload) => request("/api/signup", { method: "POST", body: payload });

/** The signed-in user's profile document. */
export const fetchMe = () => request("/api/me", { authenticated: true });

/** Patch profile fields (not email/password — those go through Firebase Auth). */
export const updateMe = (updates) =>
  request("/api/me", { method: "PATCH", body: updates, authenticated: true });
