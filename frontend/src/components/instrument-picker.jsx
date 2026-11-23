/*
 * Choosing the ensemble.
 *
 * Two decisions live in one control, because musically they're one decision:
 * *which* instruments, and *how many of each*. Picking Violin doesn't say
 * whether you want one violin line or three — Violin I, II and III are three
 * separate parts with different music, and the generator has to be told which.
 *
 * So every instrument is a checkbox, grouped by family the way a score is laid
 * out, and each one that is ticked then carries a stepper for its number of
 * parts. The caps come from the server (a section can divide, a drum set can't)
 * so the form can't ask for something the API rejects.
 */

import { useId, useState } from "react";

import { InstrumentBrowser } from "./instrument-browser";
import { previewInstrument, previewSupported, stopPreview } from "../lib/preview-audio";

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

/** "Violin" x3 -> "Violin I, II, III" — the names that will head the parts. */
export function partLabels(name, count) {
  if (count <= 1) return [name];
  return Array.from({ length: count }, (_, i) => `${name} ${ROMAN[i + 1]}`);
}

export function totalParts(chosen) {
  return chosen.reduce((sum, entry) => sum + entry.parts, 0);
}

export function InstrumentPicker({ label, groups = [], info = {}, value = [], error, onChange }) {
  const generatedId = useId();
  const errorId = `${generatedId}-error`;
  const [browsing, setBrowsing] = useState(false);
  const [playing, setPlaying] = useState(null);
  const canListen = previewSupported();

  async function listen(name) {
    if (playing === name) {
      stopPreview();
      setPlaying(null);
      return;
    }
    setPlaying(name);
    try {
      await previewInstrument({ ...(info[name] || {}), name });
    } catch (error) {
      console.warn("Preview unavailable:", error);
      setPlaying(null);
    }
  }

  const chosenNames = new Set(value.map((entry) => entry.name));
  const used = totalParts(value);

  // Ticking adds the instrument with one part; unticking removes it whatever
  // its part count. Order follows the score, not the order of the clicks.
  const order = groups.flatMap((group) => group.options);
  const toggle = (name) => {
    if (chosenNames.has(name)) {
      onChange(value.filter((entry) => entry.name !== name));
      return;
    }
    const next = [...value, { name, parts: 1 }];
    next.sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
    onChange(next);
  };

  const setParts = (name, parts) =>
    onChange(value.map((entry) => (entry.name === name ? { ...entry, parts } : entry)));

  const remove = (name) => onChange(value.filter((entry) => entry.name !== name));

  return (
    <div className="field">
      <span className="field-label" id={`${generatedId}-label`}>{label}</span>

      <div
        className="checks"
        aria-labelledby={`${generatedId}-label`}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? errorId : undefined}
      >
        {groups.map((group) => (
          <fieldset className="checks__group" key={group.group}>
            <legend className="checks__legend">{group.group}</legend>
            <div className="checks__grid">
              {group.options.map((name) => {
                const checked = chosenNames.has(name);
                return (
                  <label key={name} className={`check${checked ? " is-checked" : ""}`}>
                    <input type="checkbox" checked={checked} onChange={() => toggle(name)} />
                    <span className="check__box" aria-hidden="true" />
                    <span className="check__name">{name}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>

      <div className="picker-row">
        <button type="button" className="btn btn-ghost btn-small" onClick={() => setBrowsing(true)}>
          {canListen ? "Browse & listen to each instrument" : "Browse all instruments"}
        </button>
      </div>

      {value.length > 0 && (
        <ul className="roster">
          {value.map((entry) => {
            const spec = info[entry.name] || { maxParts: 1, clef: "treble" };
            const labels = partLabels(entry.name, entry.parts);
            const canAdd = entry.parts < spec.maxParts;

            return (
              <li className="roster__row" key={entry.name}>
                {canListen && (
                  <button
                    type="button"
                    className="browser__listen"
                    onClick={() => listen(entry.name)}
                    aria-label={`Hear the ${entry.name}`}
                  >
                    {playing === entry.name ? "■" : "▶"}
                  </button>
                )}

                <div className="roster__main">
                  <span className="roster__name">{entry.name}</span>
                  <span className="roster__meta">
                    {labels.length > 1 ? labels.join(" · ") : `${spec.clef} clef`}
                    {spec.transpose ? " · transposing" : ""}
                    {spec.staves === 2 ? " · grand staff" : ""}
                  </span>
                </div>

                <div className="stepper" role="group" aria-label={`${entry.name} parts`}>
                  <button
                    type="button"
                    onClick={() => setParts(entry.name, entry.parts - 1)}
                    disabled={entry.parts <= 1}
                    aria-label={`One fewer ${entry.name} part`}
                  >
                    −
                  </button>
                  <span className="stepper__count" aria-live="polite">{entry.parts}</span>
                  <button
                    type="button"
                    onClick={() => setParts(entry.name, entry.parts + 1)}
                    disabled={!canAdd}
                    aria-label={`One more ${entry.name} part`}
                  >
                    +
                  </button>
                </div>

                <button
                  type="button"
                  className="roster__remove"
                  onClick={() => remove(entry.name)}
                  aria-label={`Remove ${entry.name}`}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <p className="hint">
        {value.length === 0
          ? "Tick every instrument that will be playing — “Browse & listen” lets you hear each one first. Then set how many parts each splits into."
          : `${used} part${used === 1 ? "" : "s"}. Each part gets its own printed sheet.`}
      </p>
      {error && <p className="error" id={errorId}>{error}</p>}

      {browsing && (
        <InstrumentBrowser
          groups={groups}
          info={info}
          value={value}
          onChange={onChange}
          onClose={() => setBrowsing(false)}
        />
      )}
    </div>
  );
}
