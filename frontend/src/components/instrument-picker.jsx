/*
 * Choosing the ensemble.
 *
 * Two decisions live in one control, because musically they're one decision:
 * *which* instruments, and *how many of each*. Picking Violin doesn't say
 * whether you want one violin line or three — Violin I, II and III are three
 * separate parts with different music, and the generator has to be told which.
 *
 * So the dropdown adds an instrument, and each chosen instrument then carries a
 * stepper for its number of parts. The caps come from the server (a section can
 * divide, a drum set can't) so the form can't ask for something the API rejects.
 */

import { useId } from "react";

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
  const selectId = `${generatedId}-add`;
  const errorId = `${generatedId}-error`;

  const chosenNames = new Set(value.map((entry) => entry.name));
  const used = totalParts(value);

  // Anything already chosen drops out of the dropdown — it's re-added by
  // raising its part count, not by picking it twice.
  const available = groups
    .map((group) => ({
      ...group,
      options: group.options.filter((name) => !chosenNames.has(name)),
    }))
    .filter((group) => group.options.length);

  const add = (event) => {
    const name = event.target.value;
    if (!name) return;
    onChange([...value, { name, parts: 1 }]);
    event.target.value = "";
  };

  const setParts = (name, parts) =>
    onChange(value.map((entry) => (entry.name === name ? { ...entry, parts } : entry)));

  const remove = (name) => onChange(value.filter((entry) => entry.name !== name));

  return (
    <div className="field">
      <span className="field-label" id={`${generatedId}-label`}>{label}</span>

      <select
        id={selectId}
        aria-label="Add an instrument"
        defaultValue=""
        onChange={add}
        disabled={!available.length}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? errorId : undefined}
      >
        <option value="" disabled>
          {available.length ? "Add an instrument…" : "Every instrument is already on the list"}
        </option>
        {available.map((group) => (
          <optgroup key={group.group} label={group.group}>
            {group.options.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </optgroup>
        ))}
      </select>

      {value.length > 0 && (
        <ul className="roster">
          {value.map((entry) => {
            const spec = info[entry.name] || { maxParts: 1, clef: "treble" };
            const labels = partLabels(entry.name, entry.parts);
            const canAdd = entry.parts < spec.maxParts;

            return (
              <li className="roster__row" key={entry.name}>
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
          ? "Pick every instrument that will be playing. Add the same instrument once, then set how many parts it splits into."
          : `${used} part${used === 1 ? "" : "s"}. Each part gets its own printed sheet.`}
      </p>
      {error && <p className="error" id={errorId}>{error}</p>}
    </div>
  );
}
