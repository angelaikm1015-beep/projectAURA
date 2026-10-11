/*
 * What the generation is actually doing, while it does it.
 *
 * Writing a score takes minutes — five or more for a large ensemble — and the
 * form used to cover that with "takes a minute or two", which was simply untrue.
 * A wait you cannot see the end of is indistinguishable from a hang, so this
 * shows the real thing: which stage the server is in, how many batches of parts
 * are done, how long it has been, and roughly how long is left.
 *
 * The bar advances on work completed, not on time elapsed. A time-based bar
 * lies exactly when the estimate is wrong, which is the problem being fixed.
 */

function clock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

export function GenerationProgress({ progress, elapsed, fallbackLabel }) {
  const percent = progress?.percent ?? 0;
  const label = progress?.label || fallbackLabel || "Starting…";
  const batches = progress && progress.total > 1
    ? ` (${progress.done} of ${progress.total})`
    : "";

  return (
    <section className="genprog" aria-live="polite">
      <div className="genprog__top">
        <span className="genprog__label">{label}{batches}</span>
        <span className="genprog__pct">{percent}%</span>
      </div>

      <div
        className="genprog__track"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Generation progress"
      >
        <div className="genprog__fill" style={{ width: `${Math.max(2, percent)}%` }} />
      </div>

      <p className="genprog__times">
        <span>{clock(elapsed)} elapsed</span>
        {progress?.overrunning
          ? <span className="genprog__over">longer than usual — still working</span>
          : progress?.remainingSeconds
            ? <span>about {clock(progress.remainingSeconds)} left</span>
            : null}
      </p>

      <p className="genprog__note">
        Every bar of every part is written and then checked, so this is minutes
        rather than seconds. Keep this tab open — closing it cancels the piece.
      </p>
    </section>
  );
}
