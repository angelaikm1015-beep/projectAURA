/* Form field primitives. Each one owns its label, hint and error slot so pages
   don't repeat the markup or the aria wiring. */

import { useId, useState } from "react";

/** Text-ish input (email, date, ...) with label, optional hint, and error. */
export function Field({ label, error, hint, id, ...inputProps }) {
  const generatedId = useId();
  const fieldId = id || generatedId;
  const hintId = `${fieldId}-hint`;
  const errorId = `${fieldId}-error`;

  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <input
        id={fieldId}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={[hint && hintId, error && errorId].filter(Boolean).join(" ") || undefined}
        {...inputProps}
      />
      {hint && <p className="hint" id={hintId}>{hint}</p>}
      {error && <p className="error" id={errorId}>{error}</p>}
    </div>
  );
}

/** Password input with a show/hide toggle. */
export function PasswordField({ label, error, id, ...inputProps }) {
  const generatedId = useId();
  const fieldId = id || generatedId;
  const errorId = `${fieldId}-error`;
  const [visible, setVisible] = useState(false);

  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <div className="password-wrap">
        <input
          id={fieldId}
          type={visible ? "text" : "password"}
          aria-invalid={error ? "true" : undefined}
          aria-describedby={error ? errorId : undefined}
          {...inputProps}
        />
        <button
          type="button"
          className="toggle-password"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
        >
          <EyeIcon />
        </button>
      </div>
      {error && <p className="error" id={errorId}>{error}</p>}
    </div>
  );
}

/**
 * Dropdown. `options` items may be plain strings, {value,label}, or
 * {group, options} for an <optgroup> — the same shapes /api/options returns.
 */
export function SelectField({ label, error, options = [], placeholder, id, ...selectProps }) {
  const generatedId = useId();
  const fieldId = id || generatedId;
  const errorId = `${fieldId}-error`;

  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <select
        id={fieldId}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? errorId : undefined}
        {...selectProps}
      >
        <option value="" disabled>
          {placeholder || "Select…"}
        </option>
        {options.map((item) =>
          item?.group ? (
            <optgroup key={item.group} label={item.group}>
              {item.options.map((child) => (
                <Option key={optionValue(child)} item={child} />
              ))}
            </optgroup>
          ) : (
            <Option key={optionValue(item)} item={item} />
          )
        )}
      </select>
      {error && <p className="error" id={errorId}>{error}</p>}
    </div>
  );
}

const optionValue = (item) => (typeof item === "string" ? item : item.value);

function Option({ item }) {
  const value = optionValue(item);
  return <option value={value}>{typeof item === "string" ? item : item.label}</option>;
}

function EyeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
