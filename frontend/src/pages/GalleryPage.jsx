/*
 * The community feed — every piece anyone has chosen to share, newest post first.
 *
 * Each post is one piece plus what its author wrote about it. The caption comes
 * before the musical facts on purpose: the facts are on the score itself, and
 * what a reader wants first is why someone made this.
 *
 * Nothing reaches this page by default. A score is written private and only
 * appears here once its owner posts it, so the feed is opt-in all the way down.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppHeader, PageIntro } from "../components/app-header";
import { FormMessage } from "../components/chrome";
import { ShareDialog } from "../components/share-dialog";
import { fetchGallery, fetchMyScores, setScoreVisibility } from "../lib/api";

const WHEN = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric",
});

function when(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : WHEN.format(date);
}

function Post({ post }) {
  const facts = [
    post.tempoText,
    post.timeSignature,
    post.concertKey,
    `${post.partCount} part${post.partCount === 1 ? "" : "s"}`,
  ].filter(Boolean);

  return (
    <li className="post">
      <header className="post__head">
        <span className="post__avatar" aria-hidden="true">
          {(post.ownerName || "?").charAt(0).toUpperCase()}
        </span>
        <div className="post__who">
          <span className="post__author">{post.ownerName || "someone"}</span>
          <span className="post__when">{when(post.sharedAt || post.createdAt)}</span>
        </div>
      </header>

      {post.caption && <p className="post__caption">{post.caption}</p>}

      <Link className="post__piece" to={`/score/${post.id}`}>
        <span className="post__title">{post.title}</span>
        <span className="post__facts">{facts.join(" · ")}</span>
        {post.instruments?.length > 0 && (
          <span className="post__for">
            {post.instruments.map((name) => <span key={name}>{name}</span>)}
          </span>
        )}
        {post.programNote && <span className="post__note">{post.programNote}</span>}
      </Link>

      <div className="post__actions">
        <Link className="btn btn-ghost btn-small" to={`/score/${post.id}`}>
          Open the score
        </Link>
      </div>
    </li>
  );
}

export default function GalleryPage() {
  const [posts, setPosts] = useState(null);
  const [mine, setMine] = useState([]);
  const [error, setError] = useState(null);
  const [sharing, setSharing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchGallery()
      .then(({ scores: rows }) => { if (!cancelled) setPosts(rows); })
      .catch((err) => { if (!cancelled) setError(err.message); });

    // Loaded up front so the "+" can offer a choice the moment it is opened.
    fetchMyScores()
      .then(({ scores: rows }) => { if (!cancelled) setMine(rows); })
      .catch((err) => console.warn("Couldn't load your pieces —", err.message));

    return () => { cancelled = true; };
  }, []);

  async function handleShare(id, caption) {
    await setScoreVisibility(id, "public", caption);
    const { scores: rows } = await fetchGallery();
    setPosts(rows);
    setMine((current) =>
      current.map((row) => (row.id === id ? { ...row, visibility: "public", caption } : row)));
  }

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

      <section className="library">
        <FormMessage text={error} />

        {!posts && !error && <p className="library__empty">Loading the gallery…</p>}

        {posts?.length === 0 && (
          <p className="library__empty">
            Nothing here yet — be the first. Share a piece you've written, or{" "}
            <Link to="/compose">write a new one</Link>.
          </p>
        )}

        {posts?.length > 0 && (
          <ul className="feed">
            {posts.map((post) => <Post key={post.id} post={post} />)}
          </ul>
        )}
      </section>

      {sharing && (
        <ShareDialog
          scores={mine}
          onClose={() => setSharing(false)}
          onShare={handleShare}
        />
      )}
    </div>
  );
}
