/*
 * One post in the community feed.
 *
 * A piece somebody made, what they said about it, and the three things another
 * musician wants to do with it: say it's good, say something back, or keep it
 * to look at later. Comments load only when you open them — most posts are
 * scrolled past, and a feed that fetches every conversation up front is a feed
 * that takes a second to appear.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { addComment, deleteComment, fetchComments, toggleLike, toggleSave } from "../lib/api";

const WHEN = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric",
});

function when(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : WHEN.format(date);
}

function HeartIcon({ filled }) {
  return (
    <svg viewBox="0 0 24 24" className="act__icon" aria-hidden="true">
      <path
        d="M12 20.3l-1.4-1.3C5.4 14.4 2 11.3 2 7.6 2 4.9 4.1 3 6.7 3c1.5 0 3 .7 3.9 1.9l1.4 1.8 1.4-1.8C14.3 3.7 15.8 3 17.3 3 19.9 3 22 4.9 22 7.6c0 3.7-3.4 6.8-8.6 11.4L12 20.3z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function BookmarkIcon({ filled }) {
  return (
    <svg viewBox="0 0 24 24" className="act__icon" aria-hidden="true">
      <path
        d="M6 3.8h12v17l-6-4.4-6 4.4v-17z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SpeechIcon() {
  return (
    <svg viewBox="0 0 24 24" className="act__icon" aria-hidden="true">
      <path
        d="M4 5.5h16v11H9.5L5 20.2V16.5H4v-11z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Post({ post, levelLabel, ensembleLabel, onChanged }) {
  const [openComments, setOpenComments] = useState(false);
  const [comments, setComments] = useState(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const facts = [
    post.tempoText, post.timeSignature, post.concertKey,
    `${post.partCount} part${post.partCount === 1 ? "" : "s"}`,
  ].filter(Boolean);

  async function like() {
    setError(null);
    try {
      const { liked, likeCount } = await toggleLike(post.id);
      onChanged(post.id, { liked, likeCount });
    } catch (err) { setError(err.message); }
  }

  async function save() {
    setError(null);
    try {
      const { saved } = await toggleSave(post.id);
      onChanged(post.id, { saved });
    } catch (err) { setError(err.message); }
  }

  async function openThread() {
    const next = !openComments;
    setOpenComments(next);
    if (next && comments === null) {
      try {
        const { comments: rows } = await fetchComments(post.id);
        setComments(rows);
      } catch (err) { setError(err.message); }
    }
  }

  async function send(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { comments: rows } = await addComment(post.id, draft.trim());
      setComments(rows);
      setDraft("");
      onChanged(post.id, { commentCount: rows.length });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(commentId) {
    setBusy(true);
    try {
      const { comments: rows } = await deleteComment(post.id, commentId);
      setComments(rows);
      onChanged(post.id, { commentCount: rows.length });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="post">
      <header className="post__head">
        <Link className="post__by" to={`/u/${post.uid}`}>
          <span className="post__avatar" aria-hidden="true">
            {(post.ownerName || "?").charAt(0).toUpperCase()}
          </span>
          <span className="post__who">
            <span className="post__author">{post.ownerName || "someone"}</span>
            <span className="post__when">{when(post.sharedAt || post.createdAt)}</span>
          </span>
        </Link>
        <div className="post__tags">
          {levelLabel && <span className="tag">{levelLabel}</span>}
          {ensembleLabel && <span className="tag">{ensembleLabel}</span>}
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

      <div className="post__acts">
        <button
          type="button"
          className={`act${post.liked ? " is-on" : ""}`}
          onClick={like}
          aria-pressed={Boolean(post.liked)}
        >
          <HeartIcon filled={post.liked} />
          {post.likeCount || 0}
          <span className="sr-only"> likes</span>
        </button>

        <button
          type="button"
          className={`act${openComments ? " is-on" : ""}`}
          onClick={openThread}
          aria-expanded={openComments}
        >
          <SpeechIcon />
          {post.commentCount || 0}
          <span className="sr-only"> comments</span>
        </button>

        <button
          type="button"
          className={`act act--save${post.saved ? " is-on" : ""}`}
          onClick={save}
          aria-pressed={Boolean(post.saved)}
        >
          <BookmarkIcon filled={post.saved} />
          {post.saved ? "Saved" : "Save"}
        </button>

        <Link className="btn btn-ghost btn-small post__open" to={`/score/${post.id}`}>
          Open the score
        </Link>
      </div>

      {error && <p className="form-message is-error" role="alert">{error}</p>}

      {openComments && (
        <div className="thread">
          {comments === null && <p className="thread__empty">Loading…</p>}
          {comments?.length === 0 && (
            <p className="thread__empty">No comments yet — say the first thing.</p>
          )}

          {comments?.length > 0 && (
            <ul className="thread__list">
              {comments.map((comment) => (
                <li key={comment.id}>
                  <span className="thread__author">{comment.authorName}</span>
                  <span className="thread__text">{comment.text}</span>
                  {comment.mine && (
                    <button
                      type="button"
                      className="thread__remove"
                      onClick={() => remove(comment.id)}
                      disabled={busy}
                      aria-label="Delete this comment"
                    >
                      ×
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          <form className="thread__form" onSubmit={send}>
            <input
              type="text"
              maxLength={500}
              placeholder="Say something about this piece…"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={busy}
              aria-label="Write a comment"
            />
            <button type="submit" className="btn btn-small" disabled={busy || !draft.trim()}>
              Post
            </button>
          </form>
        </div>
      )}
    </li>
  );
}
