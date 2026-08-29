/* Thin client for the Flask API. Attaches the Firebase ID token when needed.
   Paths stay relative: Vite proxies /api in dev, Flask serves both in prod. */

import { auth } from "./firebase";

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
    response = await fetch(path, {
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

/**
 * Generate a score. Slow by nature — the model is writing every part — so this
 * one deliberately has no client-side timeout beyond the browser's own.
 */
export const compose = (payload) =>
  request("/api/compose", { method: "POST", body: payload, authenticated: true });

/* ---------- the library and the gallery ----------
 *
 * Every generated piece is saved automatically, private, at the moment it is
 * made. These calls are for looking through them afterwards and deciding what
 * to share. Listings carry summaries only; the engraved parts come with
 * fetchScore, since a score with thirty parts is a lot to send for a card.
 */

/** Everything the signed-in user has generated, newest first. */
export const fetchMyScores = () => request("/api/scores", { authenticated: true });

/** Every piece anyone has shared. */
export const fetchGallery = () => request("/api/gallery", { authenticated: true });

/** One saved score in full — yours, or anyone's shared piece. */
export const fetchScore = (id) => request(`/api/scores/${id}`, { authenticated: true });

/** Share one piece, or take it back. `visibility` is "public" or "private". */
export const setScoreVisibility = (id, visibility) =>
  request(`/api/scores/${id}`, { method: "PATCH", body: { visibility }, authenticated: true });

/** Share, or unshare, the whole library at once. */
export const setAllScoresVisibility = (visibility) =>
  request("/api/scores", { method: "PATCH", body: { visibility }, authenticated: true });

export const deleteScore = (id) =>
  request(`/api/scores/${id}`, { method: "DELETE", authenticated: true });
