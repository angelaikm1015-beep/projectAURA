/*
 * Building the ensemble by ear.
 *
 * A dropdown of forty names tells you nothing about what a piece will sound
 * like. Here every instrument can be played before it is chosen, and added one
 * at a time or several at once — the count beside each is how many separate
 * parts it splits into, so "Violin 2" means Violin I and Violin II with
 * different music, not one line played twice.
 *
 * It stays open while you work, because an ensemble is assembled by comparing:
 * hear the viola, hear the cello, take both.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { previewInstrument, previewSupported, stopPreview } from "../lib/preview-audio";

export function InstrumentBrowser({ groups = [], info = {}, value = [], onChange, onClose }) {
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState(null);
  const [audioError, setAudioError] = useState(null);
  const panelRef = useRef(null);
  const canListen = previewSupported();

  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      stopPreview();
    };
  }, [onClose]);

  const chosen = useMemo(
    () => new Map(value.map((entry) => [entry.name, entry.parts])),
    [value]
  );

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return groups
      .map((group) => ({
        ...group,
        options: group.options.filter((name) => name.toLowerCase().includes(needle)),
      }))
      .filter((group) => group.options.length);
  }, [groups, query]);

  async function listen(name) {
    setAudioError(null);
    if (playing === name) {
      stopPreview();
      setPlaying(null);
      return;
    }
    setPlaying(name);
    try {
      await previewInstrument({ ...(info[name] || {}), name });
    } catch (error) {
      setAudioError(error.message);
      setPlaying(null);
    }
  }

  const setParts = (name, parts) => {
    if (parts <= 0) {
      onChange(value.filter((entry) => entry.name !== name));
      return;
    }
    onChange(chosen.has(name)
      ? value.map((entry) => (entry.name === name ? { ...entry, parts } : entry))
      : [...value, { name, parts }]);
  };

  const total = value.reduce((sum, entry) => sum + entry.parts, 0);

  return (
    <div className="modal" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div
        className="modal__panel modal__panel--wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="browser-title"
        tabIndex={-1}
        ref={panelRef}
      >
        <h2 className="modal__title" id="browser-title">Choose your instruments</h2>
        <p className="modal__lede">
          {canListen
            ? "Press ▶ to hear one. Add as many as you like — the number is how many separate parts that instrument splits into."
            : "Add as many as you like — the number is how many separate parts that instrument splits into."}
        </p>

        <input
          type="text"
          className="browser__search"
          placeholder="Search instruments…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search instruments"
        />

        {audioError && <p className="form-message is-error" role="alert">{audioError}</p>}

        <div className="browser__list">
          {shown.map((group) => (
            <section key={group.group} className="browser__group">
              <h3 className="browser__group-name">{group.group}</h3>
              <ul>
                {group.options.map((name) => {
                  const spec = info[name] || { maxParts: 1 };
                  const parts = chosen.get(name) || 0;
                  return (
                    <li key={name} className={`browser__row${parts ? " is-chosen" : ""}`}>
                      {canListen && (
                        <button
                          type="button"
                          className="browser__listen"
                          onClick={() => listen(name)}
                          aria-label={`Hear the ${name}`}
                        >
                          {playing === name ? "■" : "▶"}
                        </button>
                      )}

                      <span className="browser__name">
                        {name}
                        <span className="browser__meta">
                          {[spec.clef && `${spec.clef} clef`,
                            spec.transpose ? "transposing" : null,
                            spec.staves === 2 ? "grand staff" : null]
                            .filter(Boolean).join(" · ")}
                        </span>
                      </span>

                      {parts === 0 ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-small"
                          onClick={() => setParts(name, 1)}
                        >
                          Add
                        </button>
                      ) : (
                        <span className="stepper" role="group" aria-label={`${name} parts`}>
                          <button
                            type="button"
                            onClick={() => setParts(name, parts - 1)}
                            aria-label={`One fewer ${name} part`}
                          >
                            −
                          </button>
                          <span className="stepper__count">{parts}</span>
                          <button
                            type="button"
                            onClick={() => setParts(name, parts + 1)}
                            disabled={parts >= (spec.maxParts || 1)}
                            aria-label={`One more ${name} part`}
                          >
                            +
                          </button>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {shown.length === 0 && (
            <p className="library__empty">Nothing matches “{query}”.</p>
          )}
        </div>

        <div className="modal__actions browser__foot">
          <p className="browser__tally">
            {total === 0
              ? "Nothing chosen yet"
              : `${value.length} instrument${value.length === 1 ? "" : "s"} · ${total} part${total === 1 ? "" : "s"}`}
          </p>
          <button type="button" className="btn btn-small" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
