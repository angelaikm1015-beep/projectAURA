/*
 * Posting a piece to the gallery.
 *
 * Sharing is a small act of authorship, not a checkbox: you pick which piece,
 * you say something about it, and that caption is what other people read first
 * in the feed. So the flow is one dialog — choose, write, post.
 *
 * It serves two entry points. From the gallery's "+" it opens with a list of
 * your pieces to choose from; from a piece you are already looking at it opens
 * on that piece alone. Either way the second half is the same.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

const CAPTION_MAX = 600;

export function ShareDialog({ scores = [], fixedScore = null, onClose, onShare }) {
  const [selectedId, setSelectedId] = useState(fixedScore?.id || scores[0]?.id || null);
  const [caption, setCaption] = useState(fixedScore?.caption || "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const dialogRef = useRef(null);

  // Escape closes it, and focus starts inside so the keyboard doesn't wander
  // off into the page behind.
  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const chosen = fixedScore || scores.find((row) => row.id === selectedId) || null;

  async function submit(event) {
    event.preventDefault();
    if (!chosen) return;
    setPending(true);
    setError(null);
    try {
      await onShare(chosen.id, caption.trim());
      onClose();
    } catch (err) {
      setError(err.message);
      setPending(false);
    }
  }

  return (
    <div className="modal" role="presentation" onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div
        className="modal__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-dialog-title"
        tabIndex={-1}
        ref={dialogRef}
      >
        <form className="modal__form" onSubmit={submit}>
          <h2 className="modal__title" id="share-dialog-title">Share to the gallery</h2>

          {fixedScore ? (
            <p className="modal__lede">
              <strong>{fixedScore.title}</strong> will be visible to everyone signed in.
            </p>
          ) : scores.length ? (
            <>
              <span className="field-label">Which piece?</span>
              <ul className="picker">
                {scores.map((row) => (
                  <li key={row.id}>
                    <label className={`picker__row${row.id === selectedId ? " is-active" : ""}`}>
                      <input
                        type="radio"
                        name="which-piece"
                        value={row.id}
                        checked={row.id === selectedId}
                        onChange={() => {
                          setSelectedId(row.id);
                          setCaption(row.caption || "");
                        }}
                      />
                      <span className="picker__main">
                        <span className="picker__title">{row.title}</span>
                        <span className="picker__facts">
                          {[row.tempoText, row.concertKey,
                            `${row.partCount} part${row.partCount === 1 ? "" : "s"}`]
                            .filter(Boolean).join(" · ")}
                        </span>
                      </span>
                      {row.visibility === "public" && <span className="chip chip-public">Shared</span>}
                    </label>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="modal__lede">
              You haven't written anything yet. Make a piece first and it'll be here
              to share.
            </p>
          )}

          <div className="field">
            <label htmlFor="share-caption">Say something about it</label>
            <textarea
              id="share-caption"
              rows={4}
              maxLength={CAPTION_MAX}
              placeholder="What is it for, what were you going for, what should people listen for?"
              value={caption}
              onChange={(event) => setCaption(event.target.value)}
            />
            <p className="hint">{caption.length} / {CAPTION_MAX}</p>
          </div>

          {error && <p className="form-message is-error" role="alert">{error}</p>}

          <div className="modal__actions">
            <button type="button" className="btn btn-ghost btn-small" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-small" disabled={!chosen || pending}>
              {pending ? "Posting…" : "Post to the gallery"}
            </button>
          </div>

          <p className="modal__foot">
            Nothing to share yet? <Link to="/compose">Write a new piece</Link> — it takes
            a minute or two.
          </p>
        </form>
      </div>
    </div>
  );
}
