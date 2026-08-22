/*
 * The finished score.
 *
 * The API returns ABC notation — the full score as one multi-voice tune, plus a
 * standalone tune per player. abcjs engraves that into real SVG staves here in
 * the browser, so what the user sees is the sheet itself rather than a picture
 * of one: it reflows to the window, prints at full resolution, and can be
 * played back through the Web Audio API to check the piece before rehearsal.
 *
 * Sheets are deliberately printed dark-on-white inside the dark UI. That is what
 * paper looks like, and it is what comes out of the printer.
 */

import { useEffect, useRef, useState } from "react";
import abcjs from "abcjs";

const slug = (text) =>
  String(text || "score").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function saveFile(filename, href) {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function downloadText(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  saveFile(filename, url);
  URL.revokeObjectURL(url);
}

function downloadMidi(filename, abc) {
  // getMidiFile returns one entry per tune in the ABC — ours always holds
  // exactly one — and each entry is a data: URI. Older versions handed back a
  // byte array instead, so accept either rather than betting the button on one.
  const result = abcjs.synth.getMidiFile(abc, { midiOutputType: "encoded" });
  const midi = Array.isArray(result) ? result[0] : result;

  if (typeof midi === "string") return saveFile(filename, midi);

  const url = URL.createObjectURL(new Blob([new Uint8Array(midi)], { type: "audio/midi" }));
  saveFile(filename, url);
  URL.revokeObjectURL(url);
}

/** One engraved sheet. Re-renders whenever the ABC changes. */
function Sheet({ abc, onTune }) {
  const host = useRef(null);

  useEffect(() => {
    if (!host.current) return undefined;
    const [tune] = abcjs.renderAbc(host.current, abc, {
      responsive: "resize",
      add_classes: true,
      staffwidth: 740,
      paddingtop: 12,
      paddingbottom: 24,
      format: { titlefont: "serif 20", subtitlefont: "serif 14", composerfont: "serif 11" },
    });
    onTune?.(tune);
    return () => {
      if (host.current) host.current.innerHTML = "";
    };
  }, [abc, onTune]);

  return <div className="sheet" ref={host} />;
}

/** Play / stop for the sheet on screen. */
function PlaybackButton({ tune }) {
  const synthRef = useRef(null);
  const [state, setState] = useState("idle"); // idle | loading | playing | error
  const supported = typeof abcjs.synth?.supportsAudio === "function" && abcjs.synth.supportsAudio();

  // Never let a tune keep playing after the user switches to another part.
  useEffect(() => {
    return () => {
      synthRef.current?.stop();
      synthRef.current = null;
    };
  }, [tune]);

  useEffect(() => setState("idle"), [tune]);

  if (!supported) return null;

  async function toggle() {
    if (state === "playing") {
      synthRef.current?.stop();
      setState("idle");
      return;
    }
    setState("loading");
    try {
      // The AudioContext has to be created inside the click, or browsers will
      // refuse to start it.
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      await audioContext.resume();

      const synth = new abcjs.synth.CreateSynth();
      await synth.init({ audioContext, visualObj: tune });
      await synth.prime();
      synthRef.current = synth;
      synth.start();
      setState("playing");
    } catch (error) {
      console.warn("Playback failed:", error);
      setState("error");
    }
  }

  return (
    <button type="button" className="btn btn-ghost btn-small" onClick={toggle} disabled={!tune || state === "loading"}>
      {state === "loading" ? "Loading sounds…"
        : state === "playing" ? "■ Stop"
        : state === "error" ? "Playback unavailable"
        : "▶ Play"}
    </button>
  );
}

function ConductorNotes({ conductor }) {
  return (
    <div className="podium">
      <h3>On the podium</h3>
      <p>{conductor.overview}</p>

      <h4>Beat and gesture</h4>
      <p>{conductor.gestures}</p>

      {conductor.tempoMap?.length > 0 && (
        <>
          <h4>Tempo map</h4>
          <table className="podium__table">
            <thead>
              <tr><th>Measure</th><th>Marking</th><th>BPM</th></tr>
            </thead>
            <tbody>
              {conductor.tempoMap.map((row, i) => (
                <tr key={i}><td>{row.measure}</td><td>{row.marking}</td><td>{row.bpm}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {conductor.cues?.length > 0 && (
        <>
          <h4>Cues to give</h4>
          <ul className="podium__list">
            {conductor.cues.map((cue, i) => (
              <li key={i}>
                <span className="podium__bar">m.{cue.measure}</span>
                <strong>{cue.part}</strong> — {cue.text}
              </li>
            ))}
          </ul>
        </>
      )}

      {conductor.rehearsalMarks?.length > 0 && (
        <>
          <h4>Rehearsal marks</h4>
          <ul className="podium__list">
            {conductor.rehearsalMarks.map((mark, i) => (
              <li key={i}>
                <span className="podium__bar">{mark.label} (m.{mark.measure})</span> {mark.note}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function ScoreView({ score, onStartOver }) {
  const [activeId, setActiveId] = useState("full");
  const [tune, setTune] = useState(null);

  const tabs = [
    { id: "full", label: "Full score", abc: score.scoreAbc },
    ...score.parts.map((part) => ({
      id: part.partId,
      label: part.label,
      abc: part.abc,
      note: part.performanceNote,
      badge: part.transposed ? "transposing" : null,
    })),
    ...(score.conductor ? [{ id: "conductor", label: "Conductor", abc: score.scoreAbc }] : []),
  ];

  const active = tabs.find((tab) => tab.id === activeId) || tabs[0];
  const base = slug(score.title);
  const fileBase = active.id === "full" || active.id === "conductor"
    ? `${base}-full-score`
    : `${base}-${slug(active.label)}`;

  return (
    <section className="score">
      <header className="score__head">
        <div>
          <h2 className="score__title">{score.title}</h2>
          <p className="score__facts">
            {score.tempoText} · {score.tempoBpm} bpm · {score.timeSignature} ·{" "}
            {score.concertKey} · {score.measureCount} bars ·{" "}
            {score.parts.length} part{score.parts.length === 1 ? "" : "s"}
          </p>
        </div>
        <button type="button" className="btn btn-ghost btn-small" onClick={onStartOver}>
          Write another
        </button>
      </header>

      {score.warnings?.length > 0 && (
        <div className="score__warnings" role="alert">
          {score.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
        </div>
      )}

      <p className="score__note">{score.programNote}</p>

      <div className="tabs" role="tablist" aria-label="Parts">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active.id}
            className={`tab${tab.id === active.id ? " is-active" : ""}`}
            onClick={() => setActiveId(tab.id)}
          >
            {tab.label}
            {tab.badge && <span className="tab__badge">{tab.badge}</span>}
          </button>
        ))}
      </div>

      <div className="score__toolbar">
        <PlaybackButton tune={tune} />
        <button type="button" className="btn btn-ghost btn-small" onClick={() => window.print()}>
          Print / save PDF
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={() => downloadText(`${fileBase}.abc`, active.abc)}
        >
          Download .abc
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={() => downloadMidi(`${fileBase}.mid`, active.abc)}
        >
          Download MIDI
        </button>
      </div>

      {active.note && <p className="score__partnote">{active.note}</p>}

      <div className="printable">
        <Sheet abc={active.abc} onTune={setTune} />
      </div>

      {active.id === "conductor" && <ConductorNotes conductor={score.conductor} />}

      {active.id === "full" && score.rehearsalTips && (
        <div className="podium">
          <h3>Rehearsing this</h3>
          <p>{score.rehearsalTips}</p>
        </div>
      )}

      <p className="score__footnote">
        The .abc file opens in MuseScore, and MIDI imports into any notation or DAW
        software if you want to edit the piece further.
      </p>
    </section>
  );
}
