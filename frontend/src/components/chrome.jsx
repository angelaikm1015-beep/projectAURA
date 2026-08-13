/* Page furniture shared by all three screens. */

import { passwordStrength } from "../lib/validate";

export function AuthCard({ wide, children }) {
  return <main className={wide ? "auth-card wide" : "auth-card"}>{children}</main>;
}

export function BrandHeader({ tagline }) {
  return (
    <header className="brand">
      <span className="brand__mark" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
        </svg>
      </span>
      <h1 className="brand__name">AURA</h1>
      {tagline && <p className="brand__tagline">{tagline}</p>}
    </header>
  );
}

/** Banner at the top of a form. `kind` is "error" or "success". */
export function FormMessage({ text, kind = "error" }) {
  if (!text) return null;
  return (
    <p className={`form-message is-${kind}`} role="alert" aria-live="polite">
      {text}
    </p>
  );
}

export function SubmitButton({ pending, pendingLabel, children, ...props }) {
  return (
    <button type="submit" className="btn" disabled={pending} {...props}>
      {pending ? pendingLabel : children}
    </button>
  );
}

const STRENGTH_LABELS = ["Too weak", "Weak", "Fair", "Good", "Strong"];
const STRENGTH_CLASSES = ["on-weak", "on-weak", "on-fair", "on-good", "on-strong"];

export function StrengthMeter({ password }) {
  if (!password) return null;
  const score = passwordStrength(password);

  return (
    <div className="strength">
      <div className="strength__bars" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className={`strength__bar${i < score ? ` ${STRENGTH_CLASSES[score]}` : ""}`}
          />
        ))}
      </div>
      <span className="strength__label">{STRENGTH_LABELS[score]}</span>
    </div>
  );
}
