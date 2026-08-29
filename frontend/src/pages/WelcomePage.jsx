/*
 * Post-login page — the profile, and everything the user has written.
 *
 * The library lives here rather than behind its own route because this is where
 * people land after signing in, and the first question after "who am I" is
 * "where is the piece I made yesterday". Every score is saved automatically and
 * private; sharing is a decision made here, one piece at a time.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthCard, BrandHeader, FormMessage } from "../components/chrome";
import { ScoreCard } from "../components/score-card";
import { useAuth } from "../context/AuthContext";
import {
  deleteScore, fetchMe, fetchMyScores, setAllScoresVisibility, setScoreVisibility,
} from "../lib/api";

const ROWS = [
  ["Username / Email", (p) => p.email],
  ["Age", (p) => (p.age != null ? String(p.age) : null)],
  ["Date of birth", (p) => p.birthdate],
  ["Gender", (p) => p.gender],
  ["Nationality", (p) => p.nationality],
  ["Plays at", (p) => p.affiliationLabel || p.affiliation],
  ["Instrument", (p) => p.instrument],
  ["Favorite genre", (p) => p.genre],
];

export default function WelcomePage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);
  const [scores, setScores] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then(({ profile: loaded }) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    fetchMyScores()
      .then(({ scores: rows }) => {
        if (!cancelled) setScores(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // The list is small and already in hand, so a share is reflected locally
  // rather than by refetching the whole library.
  async function handleShare(id, visibility) {
    setBusy(true);
    try {
      await setScoreVisibility(id, visibility);
      setScores((rows) => rows.map((row) => (row.id === id ? { ...row, visibility } : row)));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleShareAll(visibility) {
    setBusy(true);
    try {
      const { scores: rows } = await setAllScoresVisibility(visibility);
      setScores(rows);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(score) {
    // Deleting a generated piece cannot be undone and it cost a paid model call
    // to make, so it is worth one question.
    if (!window.confirm(`Delete “${score.title}”? This can't be undone.`)) return;
    setBusy(true);
    try {
      await deleteScore(score.id);
      setScores((rows) => rows.filter((row) => row.id !== score.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  const shared = scores?.filter((row) => row.visibility === "public").length ?? 0;

  return (
    <div className="library-page">
      <AuthCard>
        <BrandHeader tagline={user ? `Signed in as ${user.email}` : "Loading your profile…"} />

        <FormMessage text={error} />

        <ul className="profile-list">
          {ROWS.map(([label, read]) => (
            <li key={label}>
              <span className="k">{label}</span>
              <span className="v">{(profile && read(profile)) || "—"}</span>
            </li>
          ))}
        </ul>

        <Link className="btn" to="/compose">
          Create sheet music
        </Link>

        <Link className="btn btn-ghost" to="/gallery">
          Browse the gallery
        </Link>

        <button type="button" className="btn btn-ghost" onClick={handleLogout}>
          Log out
        </button>
      </AuthCard>

      <section className="library">
        <div className="library__head">
          <div>
            <h2 className="library__title">My library</h2>
            {scores?.length > 0 && (
              <p className="library__count">
                {scores.length} piece{scores.length === 1 ? "" : "s"} · {shared} shared
              </p>
            )}
          </div>

          {scores?.length > 0 && (
            <div className="library__bulk">
              <button
                type="button"
                className="btn btn-ghost btn-small"
                onClick={() => handleShareAll("public")}
                disabled={busy}
              >
                Share all
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-small"
                onClick={() => handleShareAll("private")}
                disabled={busy}
              >
                Make all private
              </button>
            </div>
          )}
        </div>

        {!scores && !error && <p className="library__empty">Loading your pieces…</p>}

        {scores?.length === 0 && (
          <p className="library__empty">
            Nothing here yet. Everything you generate is saved automatically and kept
            private until you share it.
          </p>
        )}

        {scores?.length > 0 && (
          <ul className="cards">
            {scores.map((score) => (
              <ScoreCard
                key={score.id}
                score={score}
                busy={busy}
                onShare={handleShare}
                onDelete={handleDelete}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
