/*
 * Somebody else's shelf.
 *
 * Reached by tapping a name in the gallery. It shows only what that person has
 * chosen to share — the same view for everyone, including for themselves, so
 * what you see here is exactly what you are showing other people.
 *
 * Their private work is not here and cannot be reached from here; the API
 * filters it out rather than relying on this page not to draw it.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppHeader } from "../components/app-header";
import { FormMessage } from "../components/chrome";
import { Post } from "../components/post";
import { useAuth } from "../context/AuthContext";
import { fetchAuthor } from "../lib/api";
import { OPTIONS } from "../lib/options-data";

const LEVELS = Object.fromEntries((OPTIONS.levels || []).map((l) => [l.value, l.label]));
const ENSEMBLES = Object.fromEntries((OPTIONS.ensembleGroups || []).map((g) => [g.value, g.label]));

export default function UserPage() {
  const { uid } = useParams();
  const { user } = useAuth();
  const [author, setAuthor] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setAuthor(null);
    setError(null);
    fetchAuthor(uid)
      .then((result) => { if (!cancelled) setAuthor(result); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [uid]);

  const patch = (id, changes) =>
    setAuthor((current) => ({
      ...current,
      scores: current.scores.map((row) => (row.id === id ? { ...row, ...changes } : row)),
    }));

  const isMe = author && user && author.uid === user.uid;
  const count = author?.scores.length ?? 0;

  return (
    <div className="library-page">
      <AppHeader />

      <FormMessage text={error} />

      {author && (
        <>
          <section className="author">
            <span className="author__avatar" aria-hidden="true">
              {(author.ownerName || "?").charAt(0).toUpperCase()}
            </span>
            <div className="author__who">
              <h1 className="author__name">{author.ownerName}</h1>
              <p className="author__stats">
                {count} shared piece{count === 1 ? "" : "s"}
                {author.likesReceived > 0 && ` · ${author.likesReceived} like${author.likesReceived === 1 ? "" : "s"}`}
              </p>
            </div>
            {isMe && (
              <Link className="btn btn-ghost btn-small" to="/welcome">
                This is you — go to My library
              </Link>
            )}
          </section>

          <section className="library">
            {count === 0 && (
              <p className="library__empty">
                {isMe
                  ? <>You haven't shared anything yet. Post a piece from{" "}
                    <Link to="/welcome">your library</Link> and it'll appear here.</>
                  : `${author.ownerName} hasn't shared anything yet.`}
              </p>
            )}

            {count > 0 && (
              <ul className="feed">
                {author.scores.map((post) => (
                  <Post
                    key={post.id}
                    post={post}
                    levelLabel={LEVELS[post.level]}
                    ensembleLabel={ENSEMBLES[post.ensembleGroup]}
                    onChanged={patch}
                  />
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {!author && !error && <p className="library__empty">Loading…</p>}
    </div>
  );
}
