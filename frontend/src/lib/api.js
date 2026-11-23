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
 * A light piece comes back in the response; a deep one answers 202 at once
 * with `{ requestId, mode: "deep" }`, to be collected with waitForComposition.
 */
export const compose = (payload) =>
  request("/api/compose", { method: "POST", body: payload, authenticated: true });

/**
 * How far a running generation has got. The id is one the client made up and
 * sent with the compose request. A 404 means "not registered yet, or long
 * finished" — the caller treats that as no news rather than as a failure.
 */
export const fetchComposeProgress = (requestId) =>
  request(`/api/compose/progress/${requestId}`, { authenticated: true });

/** How long a piece of this size usually takes in this mode, as a range. */
export const fetchComposeEstimate = (parts, mode = "light", level = "") => {
  const query = new URLSearchParams({ parts, mode });
  if (level) query.set("level", level);
  return request(`/api/compose/estimate?${query}`, { authenticated: true });
};

/** A finished deep composition: `{ score, scoreId, request }`. */
export const fetchComposeResult = (requestId) =>
  request(`/api/compose/result/${requestId}`, { authenticated: true });

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wait for a deep composition, which runs on the server in the background and
 * can take the better part of an hour. Polls its progress (handing every
 * reading to `onProgress`) and resolves with the finished piece.
 *
 * Network blips are ridden out. Only a job the server no longer knows about —
 * it was restarted, say — ends the wait, after a few minutes of silence.
 */
export async function waitForComposition(requestId, onProgress) {
  let lostSince = null;
  for (;;) {
    await pause(4000);
    let state;
    try {
      state = await fetchComposeProgress(requestId);
      lostSince = null;
    } catch (error) {
      if (error.status === 404) {
        lostSince ??= Date.now();
        if (Date.now() - lostSince > 3 * 60 * 1000) {
          throw new ApiError("The server lost track of this piece. If it finished, " +
            "it is in your library; otherwise please generate it again.", 404);
        }
      }
      continue;
    }
    onProgress?.(state);
    if (state.stage === "failed") throw new ApiError(state.error || "Generation failed.", 502);
    if (state.stage === "done") return fetchComposeResult(requestId);
  }
}

/* ---------- the library and the gallery ----------
 *
 * Every generated piece is saved automatically, private, at the moment it is
 * made. These calls are for looking through them afterwards and deciding what
 * to share. Listings carry summaries only; the engraved parts come with
 * fetchScore, since a score with thirty parts is a lot to send for a card.
 */

/** Everything the signed-in user has generated, newest first. */
export const fetchMyScores = () => request("/api/scores", { authenticated: true });

/**
 * A deep-composed piece as a MusicXML file, built by the server from the
 * compact score it is stored as. Resolves to the file's text.
 */
export async function fetchMusicXml(id) {
  const user = auth.currentUser;
  if (!user) throw new ApiError("You need to be logged in.", 401);
  let response;
  try {
    response = await fetch(`/api/scores/${id}/musicxml`, {
      headers: { Authorization: `Bearer ${await user.getIdToken()}` },
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the Flask backend running?", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(payload.message || `Request failed (${response.status}).`,
                       response.status);
  }
  return response.text();
}

/**
 * The community feed, optionally narrowed. Comes back as
 * `{ scores, facets, total }` — the facets are how many shared pieces sit
 * behind each filter choice, so the UI can show counts.
 */
export const fetchGallery = ({ level, ensemble } = {}) => {
  const query = new URLSearchParams();
  if (level) query.set("level", level);
  if (ensemble) query.set("ensemble", ensemble);
  const suffix = query.toString() ? `?${query}` : "";
  return request(`/api/gallery${suffix}`, { authenticated: true });
};

/** Someone else's page: who they are and what they've shared. */
export const fetchAuthor = (uid) => request(`/api/users/${uid}`, { authenticated: true });

/** Pieces this user has liked, or saved for later. */
export const fetchLiked = () => request("/api/me/likes", { authenticated: true });
export const fetchSaved = () => request("/api/me/saves", { authenticated: true });

/** Toggles. Liking returns the new count; saving is private to you. */
export const toggleLike = (id) =>
  request(`/api/scores/${id}/like`, { method: "POST", authenticated: true });

export const toggleSave = (id) =>
  request(`/api/scores/${id}/save`, { method: "POST", authenticated: true });

export const fetchComments = (id) =>
  request(`/api/scores/${id}/comments`, { authenticated: true });

export const addComment = (id, text) =>
  request(`/api/scores/${id}/comments`, { method: "POST", body: { text }, authenticated: true });

export const deleteComment = (id, commentId) =>
  request(`/api/scores/${id}/comments/${commentId}`, { method: "DELETE", authenticated: true });

/** One saved score in full — yours, or anyone's shared piece. */
export const fetchScore = (id) => request(`/api/scores/${id}`, { authenticated: true });

/**
 * Share one piece, take it back, or reword its caption. Both fields are
 * optional: `{ visibility }` alone flips it, `{ caption }` alone edits the post.
 */
export const updateScore = (id, changes) =>
  request(`/api/scores/${id}`, { method: "PATCH", body: changes, authenticated: true });

/** Share one piece, or take it back. `visibility` is "public" or "private". */
export const setScoreVisibility = (id, visibility, caption) =>
  updateScore(id, caption === undefined ? { visibility } : { visibility, caption });

/** Share, or unshare, the whole library at once. */
export const setAllScoresVisibility = (visibility) =>
  request("/api/scores", { method: "PATCH", body: { visibility }, authenticated: true });

/**
 * Ask for a small change to a piece that already exists. Slow — it's another
 * model call — but it edits the music rather than replacing it.
 */
export const reviseScore = (id, changeRequest) =>
  request(`/api/scores/${id}/revise`, {
    method: "POST", body: { request: changeRequest }, authenticated: true,
  });

/** Put back the version from before the last change. */
export const undoRevision = (id) =>
  request(`/api/scores/${id}/undo`, { method: "POST", authenticated: true });

export const deleteScore = (id) =>
  request(`/api/scores/${id}`, { method: "DELETE", authenticated: true });
