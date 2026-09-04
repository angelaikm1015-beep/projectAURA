/*
 * My library — everything the user has written, with their account details
 * tucked in above it.
 *
 * The music is the point of this page, so the profile is a single compact strip
 * rather than a card full of rows: it answers "am I signed in as the right
 * person" at a glance and folds the rest away behind a toggle. Every score is
 * saved automatically and private; sharing is a decision made here, one piece
 * at a time.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppHeader, PageIntro } from "../components/app-header";
import { FormMessage } from "../components/chrome";
import { ScoreCard } from "../components/score-card";
import { ShareDialog } from "../components/share-dialog";
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
  const { user } = useAuth();

  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);
  const [scores, setScores] = useState(null);
  const [busy, setBusy] = useState(false);
  const [posting, setPosting] = useState(null); // the piece being written about
  const [showDetails, setShowDetails] = useState(false);

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

  // Sharing means writing a caption, so it goes through the dialog. Unsharing
  // needs nothing said, so it happens on the spot. The list is small and already
  // in hand, so both are reflected locally rather than by refetching.
  async function handleShare(id, visibility) {
    if (visibility === "public") {
      setPosting(scores.find((row) => row.id === id) || null);
      return;
    }
    setBusy(true);
    try {
      await setScoreVisibility(id, "private");
      setScores((rows) =>
        rows.map((row) => (row.id === id ? { ...row, visibility: "private" } : row)));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handlePost(id, caption) {
    await setScoreVisibility(id, "public", caption);
    setScores((rows) =>
      rows.map((row) => (row.id === id ? { ...row, visibility: "public", caption } : row)));
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

  const shared = scores?.filter((row) => row.visibility === "public").length ?? 0;

  // The one-line summary. Everything else is behind "Details".
  const summary = profile
    ? [profile.age != null ? `${profile.age}` : null,
       profile.affiliationLabel || profile.affiliation,
       profile.instrument,
       profile.genre].filter(Boolean).join(" · ")
    : "";

  return (
    <div className="library-page">
      <AppHeader />

      <div className="feed-top">
        <PageIntro title="My library">
          Everything you've written. Saved automatically, private until you share it.
        </PageIntro>
        <Link className="fab" to="/compose">
          <span className="fab__plus" aria-hidden="true">+</span>
          Write a new piece
        </Link>
      </div>

      <FormMessage text={error} />

      <section className="profile-strip">
        <span className="post__avatar" aria-hidden="true">
          {(user?.email || "?").charAt(0).toUpperCase()}
        </span>
        <div className="profile-strip__who">
          <span className="profile-strip__email">{user?.email || "Loading…"}</span>
          {summary && <span className="profile-strip__facts">{summary}</span>}
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          aria-expanded={showDetails}
          onClick={() => setShowDetails((open) => !open)}
        >
          {showDetails ? "Hide details" : "Details"}
        </button>
      </section>

      {showDetails && (
        <ul className="profile-list profile-list--compact">
          {ROWS.map(([label, read]) => (
            <li key={label}>
              <span className="k">{label}</span>
              <span className="v">{(profile && read(profile)) || "—"}</span>
            </li>
          ))}
        </ul>
      )}

      <section className="library">
        <div className="library__head">
          {scores?.length > 0 && (
            <p className="library__count">
              {scores.length} piece{scores.length === 1 ? "" : "s"} · {shared} shared
            </p>
          )}

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

      {posting && (
        <ShareDialog
          fixedScore={posting}
          onClose={() => setPosting(null)}
          onShare={handlePost}
        />
      )}
    </div>
  );
}
