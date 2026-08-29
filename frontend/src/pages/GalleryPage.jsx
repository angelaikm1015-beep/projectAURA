/*
 * The public gallery — every piece anyone has chosen to share.
 *
 * Nothing reaches this page by default. A score is written private and only
 * appears here once its owner shares it, so the list is opt-in all the way
 * down.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { BrandHeader, FormMessage } from "../components/chrome";
import { ScoreCard } from "../components/score-card";
import { useAuth } from "../context/AuthContext";
import { fetchGallery } from "../lib/api";

export default function GalleryPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [scores, setScores] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchGallery()
      .then(({ scores: rows }) => { if (!cancelled) setScores(rows); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, []);

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="library-page">
      <header className="compose-top">
        <BrandHeader tagline="Pieces the community has shared." />
        <nav className="compose-nav">
          <Link className="link" to="/welcome">My library</Link>
          <Link className="link" to="/compose">Compose</Link>
          <button type="button" className="link link-button" onClick={handleLogout}>
            Log out {user ? `(${user.email})` : ""}
          </button>
        </nav>
      </header>

      <section className="library">
        <div className="library__head">
          <h2 className="library__title">Gallery</h2>
          {scores && (
            <p className="library__count">
              {scores.length} shared piece{scores.length === 1 ? "" : "s"}
            </p>
          )}
        </div>

        <FormMessage text={error} />

        {!scores && !error && <p className="library__empty">Loading…</p>}

        {scores?.length === 0 && (
          <p className="library__empty">
            Nothing has been shared yet. Write a piece and share it from your library —
            it will show up here.
          </p>
        )}

        {scores?.length > 0 && (
          <ul className="cards">
            {scores.map((score) => <ScoreCard key={score.id} score={score} />)}
          </ul>
        )}
      </section>
    </div>
  );
}
