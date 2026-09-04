/*
 * The public gallery — every piece anyone has chosen to share.
 *
 * Nothing reaches this page by default. A score is written private and only
 * appears here once its owner shares it, so the list is opt-in all the way
 * down.
 */

import { useEffect, useState } from "react";

import { AppHeader, PageIntro } from "../components/app-header";
import { FormMessage } from "../components/chrome";
import { ScoreCard } from "../components/score-card";
import { fetchGallery } from "../lib/api";

export default function GalleryPage() {
  const [scores, setScores] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchGallery()
      .then(({ scores: rows }) => { if (!cancelled) setScores(rows); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="library-page">
      <AppHeader />
      <PageIntro title="Gallery">
        Pieces the community has chosen to share.
      </PageIntro>

      <section className="library">
        {scores?.length > 0 && (
          <div className="library__head">
            <p className="library__count">
              {scores.length} shared piece{scores.length === 1 ? "" : "s"}
            </p>
          </div>
        )}

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
