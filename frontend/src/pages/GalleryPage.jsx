/*
 * The community feed — every piece anyone has chosen to share, newest post first.
 *
 * Three views of the same thing: everything, the pieces you liked, the pieces
 * you kept. And a filter, because "show me what people write for a high school
 * group" is a different question from "show me everything", and the answer to
 * the second one is not useful to a high school teacher.
 *
 * Nothing reaches this page by default. A score is written private and only
 * appears here once its owner posts it, so the feed is opt-in all the way down.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { AppHeader, PageIntro } from "../components/app-header";
import { FormMessage } from "../components/chrome";
import { Post } from "../components/post";
import { ShareDialog } from "../components/share-dialog";
import { fetchGallery, fetchLiked, fetchMyScores, fetchSaved, setScoreVisibility } from "../lib/api";
import { OPTIONS } from "../lib/options-data";

const TABS = [
  { id: "all", label: "Everything" },
  { id: "liked", label: "Liked" },
  { id: "saved", label: "Saved" },
];

export default function GalleryPage() {
  const [tab, setTab] = useState("all");
  const [posts, setPosts] = useState(null);
  const [facets, setFacets] = useState({ levels: {}, ensembleGroups: {} });
  const [level, setLevel] = useState("");
  const [ensemble, setEnsemble] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [mine, setMine] = useState([]);
  const [error, setError] = useState(null);
  const [sharing, setSharing] = useState(false);

  const labels = useMemo(() => ({
    levels: Object.fromEntries((OPTIONS.levels || []).map((l) => [l.value, l.label])),
    ensembles: Object.fromEntries((OPTIONS.ensembleGroups || []).map((g) => [g.value, g.label])),
  }), []);

  useEffect(() => {
    let cancelled = false;
    setPosts(null);
    setError(null);

    const load = tab === "liked" ? fetchLiked()
      : tab === "saved" ? fetchSaved()
      : fetchGallery({ level, ensemble });

    load
      .then((result) => {
        if (cancelled) return;
        setPosts(result.scores);
        // Only the full feed reports facets; the other tabs are already narrow.
        if (result.facets) setFacets(result.facets);
      })
      .catch((err) => { if (!cancelled) setError(err.message); });

    return () => { cancelled = true; };
  }, [tab, level, ensemble]);

  useEffect(() => {
    let cancelled = false;
    fetchMyScores()
      .then(({ scores: rows }) => { if (!cancelled) setMine(rows); })
      .catch((err) => console.warn("Couldn't load your pieces —", err.message));
    return () => { cancelled = true; };
  }, []);

  /* A like or a save changes one row, so patch that row rather than reloading
     the feed and losing the reader's place. */
  const patch = (id, changes) =>
    setPosts((rows) => rows.map((row) => (row.id === id ? { ...row, ...changes } : row)));

  async function handleShare(id, caption) {
    await setScoreVisibility(id, "public", caption);
    const result = await fetchGallery({ level, ensemble });
    setPosts(result.scores);
    setFacets(result.facets);
    setMine((rows) =>
      rows.map((row) => (row.id === id ? { ...row, visibility: "public", caption } : row)));
  }

  const filtered = Boolean(level || ensemble);
  const showFilters = tab === "all";

  return (
    <div className="library-page">
      <AppHeader />

      <div className="feed-top">
        <PageIntro title="Gallery">
          What everyone has been writing. Post one of yours, or start a new piece.
        </PageIntro>
        <button type="button" className="fab" onClick={() => setSharing(true)}>
          <span className="fab__plus" aria-hidden="true">+</span>
          Share a piece
        </button>
      </div>

      <div className="feed-bar">
        <div className="tabs" role="tablist" aria-label="Which pieces">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={tab === entry.id}
              className={`tab${tab === entry.id ? " is-active" : ""}`}
              onClick={() => setTab(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>

        {showFilters && (
          <button
            type="button"
            className={`tab${filtered ? " is-active" : ""}`}
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            Filter{filtered ? ` · ${[level, ensemble].filter(Boolean).length}` : ""}
          </button>
        )}
      </div>

      {showFilters && filtersOpen && (
        <div className="filters">
          <FilterRow
            title="Playing level"
            options={OPTIONS.levels}
            counts={facets.levels}
            value={level}
            onChange={setLevel}
          />
          <FilterRow
            title="Who it's for"
            options={OPTIONS.ensembleGroups}
            counts={facets.ensembleGroups}
            value={ensemble}
            onChange={setEnsemble}
          />
          {filtered && (
            <button
              type="button"
              className="btn btn-ghost btn-small filters__clear"
              onClick={() => { setLevel(""); setEnsemble(""); }}
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      <section className="library">
        <FormMessage text={error} />

        {!posts && !error && <p className="library__empty">Loading…</p>}

        {posts?.length === 0 && (
          <p className="library__empty">
            {tab === "liked" ? "You haven't liked anything yet."
              : tab === "saved" ? "Nothing saved yet. Save a piece and it'll wait for you here."
              : filtered ? "No shared pieces match that filter yet."
              : <>Nothing here yet — be the first. Share a piece you've written, or{" "}
                <Link to="/compose">write a new one</Link>.</>}
          </p>
        )}

        {posts?.length > 0 && (
          <ul className="feed">
            {posts.map((post) => (
              <Post
                key={post.id}
                post={post}
                levelLabel={labels.levels[post.level]}
                ensembleLabel={labels.ensembles[post.ensembleGroup]}
                onChanged={patch}
              />
            ))}
          </ul>
        )}
      </section>

      {sharing && (
        <ShareDialog scores={mine} onClose={() => setSharing(false)} onShare={handleShare} />
      )}
    </div>
  );
}

/** One row of filter chips, each showing how many pieces sit behind it. */
function FilterRow({ title, options = [], counts = {}, value, onChange }) {
  return (
    <div className="filters__row">
      <span className="filters__title">{title}</span>
      <div className="filters__chips">
        <button
          type="button"
          className={`chip-btn${value === "" ? " is-active" : ""}`}
          onClick={() => onChange("")}
        >
          Any
        </button>
        {options.map((option) => {
          const count = counts[option.value] || 0;
          return (
            <button
              key={option.value}
              type="button"
              className={`chip-btn${value === option.value ? " is-active" : ""}`}
              onClick={() => onChange(value === option.value ? "" : option.value)}
              disabled={count === 0 && value !== option.value}
            >
              {option.label}
              <span className="chip-btn__count">{count}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
