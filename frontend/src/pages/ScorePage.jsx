/*
 * A saved piece, reopened.
 *
 * Everything a freshly generated score shows, from the library instead of from
 * the generator — same reader, same sheets, same downloads. What changes is the
 * rail's footer: there is no brief in hand to regenerate from, so it offers the
 * way back to the library and to the compose form instead.
 *
 * The route is readable by anyone signed in, and the API decides whether they
 * may see this particular piece. A private score belonging to someone else is a
 * 404, exactly like one that never existed.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AuthCard, BrandHeader, FormMessage } from "../components/chrome";
import { ScoreView } from "../components/score-view";
import { useAuth } from "../context/AuthContext";
import { fetchOptions, fetchScore, reviseScore, undoRevision, updateScore } from "../lib/api";
import { describeBrief } from "../lib/brief";
import { OPTIONS } from "../lib/options-data";

export default function ScorePage() {
  const { id } = useParams();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [saved, setSaved] = useState(null);
  const [options, setOptions] = useState(OPTIONS);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [revising, setRevising] = useState(false);
  const [revision, setRevision] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setSaved(null);
    setError(null);

    fetchScore(id)
      .then((row) => { if (!cancelled) setSaved(row); })
      .catch((err) => { if (!cancelled) setError(err.message); });

    fetchOptions()
      .then((fresh) => { if (!cancelled) setOptions(fresh); })
      .catch((err) => console.warn("Using bundled options —", err.message));

    return () => { cancelled = true; };
  }, [id]);

  /* Sharing sends a visibility and usually a caption; rewording an already
     posted piece sends only the caption. */
  async function handleVisibility(visibility, caption) {
    setSaving(true);
    try {
      const changes = {};
      if (visibility !== undefined) changes.visibility = visibility;
      if (caption !== undefined) changes.caption = caption;
      const updated = await updateScore(id, changes);
      setSaved((current) => ({
        ...current, visibility: updated.visibility, caption: updated.caption,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRevise(request) {
    setRevising(true);
    try {
      const result = await reviseScore(id, request);
      setSaved((current) => ({ ...current, score: result.score, canUndo: true }));
      setRevision({ summary: result.summary });
    } finally {
      setRevising(false);
    }
  }

  async function handleUndo() {
    setRevising(true);
    try {
      const { score: restored } = await undoRevision(id);
      setSaved((current) => ({ ...current, score: restored, canUndo: false }));
      setRevision(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setRevising(false);
    }
  }

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  if (error) {
    return (
      <AuthCard>
        <BrandHeader tagline="That piece isn't available" />
        <FormMessage text={error} />
        <Link className="btn" to="/welcome">Back to my library</Link>
      </AuthCard>
    );
  }

  if (!saved) {
    return (
      <AuthCard>
        <BrandHeader tagline="Opening the score…" />
      </AuthCard>
    );
  }

  return (
    <ScoreView
      score={saved.score}
      brief={describeBrief(options, saved.brief)}
      visibility={saved.visibility}
      caption={saved.caption}
      onVisibilityChange={saved.mine ? handleVisibility : undefined}
      owner={saved.mine ? null : saved.ownerName}
      ownerUid={saved.uid}
      saving={saving}
      onRevise={saved.mine ? handleRevise : undefined}
      onUndo={handleUndo}
      canUndo={Boolean(saved.canUndo)}
      revising={revising}
      revisionSummary={revision?.summary}
      nav={
        <nav className="compose-nav">
          <Link className="link" to="/compose">Compose</Link>
          <Link className="link" to="/gallery">Gallery</Link>
          <Link className="link" to="/welcome">My library</Link>
          <button type="button" className="link link-button" onClick={handleLogout}>
            Log out
          </button>
        </nav>
      }
      footer={
        <>
          <Link className="btn" to="/compose">Write a new piece</Link>
          <Link className="btn btn-ghost btn-small" to="/welcome">Back to my library</Link>
        </>
      }
    />
  );
}
