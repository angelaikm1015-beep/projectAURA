/*
 * One saved piece, as it appears in a list.
 *
 * The same card serves the owner's library and the public gallery, because the
 * two lists want the same facts — what the piece is, who it is written for, when
 * it was made. What differs is the row of controls underneath: the owner gets to
 * share and delete, a visitor gets a byline.
 */

import { Link } from "react-router-dom";

const WHEN = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric",
});

function when(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : WHEN.format(date);
}

export function ScoreCard({ score, onShare, onDelete, busy = false }) {
  const isPublic = score.visibility === "public";
  const facts = [
    score.tempoText,
    score.timeSignature,
    score.concertKey,
    `${score.partCount} part${score.partCount === 1 ? "" : "s"}`,
    when(score.createdAt),
  ].filter(Boolean);

  return (
    <li className="card">
      <div className="card__head">
        <Link className="card__title" to={`/score/${score.id}`}>{score.title}</Link>
        {onShare && (
          <span className={`chip${isPublic ? " chip-public" : ""}`}>
            {isPublic ? "Shared" : "Private"}
          </span>
        )}
      </div>

      <p className="card__facts">{facts.join(" · ")}</p>

      {score.instruments?.length > 0 && (
        <ul className="card__for">
          {score.instruments.map((name) => <li key={name}>{name}</li>)}
        </ul>
      )}

      {score.caption
        ? <p className="card__caption">“{score.caption}”</p>
        : score.programNote && <p className="card__note">{score.programNote}</p>}

      <div className="card__actions">
        <Link className="btn btn-ghost btn-small" to={`/score/${score.id}`}>Open</Link>

        {onShare && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={() => onShare(score.id, isPublic ? "private" : "public")}
            disabled={busy}
          >
            {isPublic ? "Make private" : "Share"}
          </button>
        )}

        {onDelete && (
          <button
            type="button"
            className="btn btn-ghost btn-small btn-danger"
            onClick={() => onDelete(score)}
            disabled={busy}
          >
            Delete
          </button>
        )}

        {!onShare && score.ownerName && (
          <span className="card__by">by {score.ownerName}</span>
        )}
      </div>
    </li>
  );
}
