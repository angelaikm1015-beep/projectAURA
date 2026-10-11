/*
 * The finished score.
 *
 * The API returns ABC notation — the full score as one multi-voice tune, plus a
 * standalone tune per player. abcjs engraves that into real SVG staves here in
 * the browser, so what the user sees is the sheet itself rather than a picture
 * of one: it reflows to the window, prints at full resolution, and can be
 * played back through the Web Audio API to check the piece before rehearsal.
 *
 * Once a score exists it takes over the screen. Everything the reader needs to
 * know about the piece — its name, who it is written for, what it is about —
 * sits in a rail down the side, and the whole remaining width is paper.
 *
 * Sheets are deliberately printed dark-on-white inside the dark UI. That is what
 * paper looks like, and it is what comes out of the printer.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import abcjs from "abcjs";

import { SOUNDFONT_URL } from "../lib/soundfont";

const slug = (text) =>
  String(text || "score").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/*
 * How big the engraving is drawn.
 *
 * `responsive: "resize"` makes abcjs draw at `staffwidth` and then stretch the
 * finished SVG to fill its container, so the *smaller* the staff width the
 * larger everything lands on screen — notes, clefs, part names and all. Fewer
 * measures per line comes with it: a line of four bars blown up to full width
 * is what makes a sheet readable from a music stand instead of a cramped ribbon
 * of quavers.
 */
const SHEET_SIZES = [
  { id: "regular", label: "Regular", staffwidth: 1000, measuresPerLine: 6 },
  { id: "large", label: "Large", staffwidth: 800, measuresPerLine: 4 },
  { id: "huge", label: "Extra large", staffwidth: 600, measuresPerLine: 3 },
];

const DEFAULT_SIZE = "large";

// Vertical air. abcjs measures these in points; its own defaults (46pt between
// systems, 36pt between the staves inside one) pack a twelve-stave score tight
// enough that the lines run together on paper.
const SHEET_FORMAT = {
  titlefont: "serif 22",
  subtitlefont: "serif 15",
  composerfont: "serif 12",
  voicefont: "serif 13",
  partsfont: "serif 14",
  tempofont: "serif 13",
  annotationfont: "serif 12",
  gchordfont: "serif 13",
  measurefont: "italic 11",
  staffsep: 72,
  sysstaffsep: 46,
  musicspace: 14,
  titlespace: 8,
};

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

/** "Violin ×3, Cello" — the instruments this piece was actually written for. */
function writtenFor(score) {
  const counts = new Map();
  for (const part of score.parts) {
    counts.set(part.instrument, (counts.get(part.instrument) || 0) + 1);
  }
  return [...counts].map(([name, count]) => (count > 1 ? `${name} ×${count}` : name));
}

/*
 * On a full score every system needs its instrument names down the left, or a
 * player cannot tell which line is theirs. On a single player's sheet there is
 * only one line, so repeating the name on every system is noise — it belongs
 * once, at the top left.
 *
 * abcjs would do this itself (full name first system, short name after), but
 * its line-wrapper rebuilds every system from the first one and carries the
 * name along with it. `afterParsing` runs on the wrapped tune just before it is
 * engraved, which is the place to take the repeats back off.
 */
function stripRepeatedNames(tune) {
  let seenStaff = false;
  for (const line of tune.lines || []) {
    if (!line.staff) continue;
    if (seenStaff) {
      for (const staff of line.staff) delete staff.title;
    }
    seenStaff = true;
  }
}

/** One engraved sheet. Re-renders whenever the ABC or the chosen size changes. */
function Sheet({ abc, size, nameEveryLine = true, onTune }) {
  const host = useRef(null);

  useEffect(() => {
    const node = host.current;
    if (!node) return undefined;

    const options = {
      afterParsing: nameEveryLine ? undefined : stripRepeatedNames,
      responsive: "resize",
      add_classes: true,
      staffwidth: size.staffwidth,
      wrap: {
        preferredMeasuresPerLine: size.measuresPerLine,
        minSpacing: 1.5,
        maxSpacing: 2.7,
      },
      // Each system becomes its own <svg> in its own block. On screen that
      // changes nothing; on paper it is what lets a page break fall between two
      // systems instead of slicing one in half.
      oneSvgPerLine: true,
      paddingtop: 16,
      paddingbottom: 32,
      paddingleft: 12,
      paddingright: 12,
      format: SHEET_FORMAT,
    };

    let tune;
    try {
      [tune] = abcjs.renderAbc(node, abc, options);
    } catch (error) {
      // Line wrapping is the one part of this that has to guess at widths, and
      // a sheet with over-long lines beats no sheet at all.
      console.warn("Engraving without line wrapping —", error);
      const { wrap, ...plain } = options;
      [tune] = abcjs.renderAbc(node, abc, plain);
    }

    onTune?.(tune);
    return () => { node.innerHTML = ""; };
  }, [abc, size, nameEveryLine, onTune]);

  return <div className="sheet" ref={host} />;
}

/*
 * Which voices belong to which player.
 *
 * abcjs silences parts by voice *index*, so the mixer needs the order the
 * voices are declared in. That order is written into the ABC itself — the V:
 * lines above the first K: — which makes it the one source that is right for
 * every score, including ones saved before the mixer existed. A grand-staff
 * instrument declares two voices (P9a, P9b) and both belong to the one player.
 */
function voiceIndexesByPart(abc) {
  const header = String(abc || "").split(/^K:/m)[0];
  const ids = [...header.matchAll(/^V:\s*(\S+)/gm)].map((match) => match[1]);

  const byPart = {};
  ids.forEach((id, index) => {
    const partId = /^(.*?)[ab]?$/.exec(id)[1];
    (byPart[partId] ||= []).push(index);
    if (partId !== id) (byPart[id] ||= []).push(index);
  });
  return byPart;
}

/*
 * Playback, and which players are sounding.
 *
 * Muting is applied when the audio is built, not while it runs, so changing the
 * mix mid-piece means rebuilding it: the position is remembered, the parts are
 * re-rendered, and playback resumes where it was. That costs a moment on a long
 * score, which is why the button says so rather than appearing to hang.
 *
 * The mix lives here rather than in the mixer component so that the buttons and
 * the sound can never disagree — one place decides who is playing.
 */
function usePlayer(tune, abc) {
  const synthRef = useRef(null);
  const [state, setState] = useState("idle"); // idle | loading | playing | error
  const [muted, setMuted] = useState(() => new Set());
  const supported = typeof abcjs.synth?.supportsAudio === "function" && abcjs.synth.supportsAudio();

  const voicesByPart = useMemo(() => voiceIndexesByPart(abc), [abc]);

  // A different tune is a different mix; never let the old one keep playing.
  useEffect(() => {
    return () => {
      synthRef.current?.stop();
      synthRef.current = null;
    };
  }, [tune]);

  useEffect(() => {
    setState("idle");
    setMuted(new Set());
  }, [tune]);

  async function build(silenced, startAtSec) {
    // The AudioContext has to be created inside the click, or browsers will
    // refuse to start it.
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.resume();

    const voicesOff = [...silenced].flatMap((partId) => voicesByPart[partId] || []);
    const synth = new abcjs.synth.CreateSynth();
    await synth.init({
      audioContext,
      visualObj: tune,
      options: { voicesOff, soundFontUrl: SOUNDFONT_URL },
    });
    await synth.prime();
    synthRef.current = synth;
    if (startAtSec) synth.seek(startAtSec, "seconds");
    synth.start();
  }

  async function toggle() {
    if (state === "playing") {
      synthRef.current?.stop();
      synthRef.current = null;
      setState("idle");
      return;
    }
    setState("loading");
    try {
      await build(muted, 0);
      setState("playing");
    } catch (error) {
      console.warn("Playback failed:", error);
      setState("error");
    }
  }

  /** Change who is sounding. Takes effect immediately if it is already playing. */
  async function setMix(next) {
    setMuted(next);
    if (state !== "playing" || !synthRef.current) return;

    // Keep the place. pause() hands back how far in we are.
    const at = synthRef.current.pause();
    setState("loading");
    try {
      await build(next, at);
      setState("playing");
    } catch (error) {
      console.warn("Re-mixing failed:", error);
      setState("error");
    }
  }

  return { state, supported, toggle, muted, setMix };
}

function PlayButton({ player, disabled }) {
  if (!player.supported) return null;
  return (
    <button
      type="button"
      className="btn btn-ghost btn-small"
      onClick={player.toggle}
      disabled={disabled || player.state === "loading"}
    >
      {player.state === "loading" ? "Mixing…"
        : player.state === "playing" ? "■ Stop"
        : player.state === "error" ? "Playback unavailable"
        : "▶ Play"}
    </button>
  );
}

/*
 * A speaker per player, sitting on top of the score it belongs to.
 *
 * Listening to a full score as one wall of sound tells you very little. What
 * you actually want is to take the flute out and hear what is left, or start
 * from one line and stack the others back on. So every part gets a speaker you
 * can click, and "Only this" gets you to a single line in one press instead of
 * muting the other eleven by hand.
 */
/* Drawn rather than an emoji: 🔊 and 🔇 are nearly identical in some system
   fonts and absent in others, and this is the one control that has to read
   correctly at a glance. */
function SpeakerIcon({ off }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="mixer__icon">
      <path d="M4 9.5h3.2L12 5.4v13.2L7.2 14.5H4z" />
      {off ? (
        <path className="mixer__icon-mark" d="M15.5 9.5l5 5m0-5l-5 5" />
      ) : (
        <>
          <path className="mixer__icon-wave" d="M15.8 9.2a4 4 0 0 1 0 5.6" />
          <path className="mixer__icon-wave" d="M18.4 6.8a7.5 7.5 0 0 1 0 10.4" />
        </>
      )}
    </svg>
  );
}

function Mixer({ parts, player }) {
  if (!player.supported || parts.length < 2) return null;

  const { muted, setMix } = player;
  const on = parts.length - muted.size;

  const toggle = (partId) => {
    const next = new Set(muted);
    if (next.has(partId)) next.delete(partId); else next.add(partId);
    setMix(next);
  };

  const solo = (partId) => {
    const alone = muted.size === parts.length - 1 && !muted.has(partId);
    // Clicking "Only this" on the part that is already alone puts everyone back.
    setMix(alone ? new Set() : new Set(parts.filter((p) => p.partId !== partId).map((p) => p.partId)));
  };

  return (
    <section className="mixer" aria-label="Instruments you can hear">
      <div className="mixer__head">
        <h3 className="mixer__title">Who you're hearing</h3>
        <p className="mixer__count">{on} of {parts.length} playing</p>
        <div className="mixer__all">
          <button type="button" className="btn btn-ghost btn-small"
            onClick={() => setMix(new Set())} disabled={muted.size === 0}>
            All on
          </button>
          <button type="button" className="btn btn-ghost btn-small"
            onClick={() => setMix(new Set(parts.map((p) => p.partId)))}
            disabled={muted.size === parts.length}>
            All off
          </button>
        </div>
      </div>

      <ul className="mixer__list">
        {parts.map((part) => {
          const off = muted.has(part.partId);
          const alone = !off && muted.size === parts.length - 1;
          return (
            <li key={part.partId} className={`mixer__row${off ? " is-off" : ""}`}>
              <button
                type="button"
                className="mixer__speaker"
                aria-pressed={!off}
                aria-label={`${off ? "Turn on" : "Turn off"} ${part.label}`}
                title={off ? `Turn on ${part.label}` : `Mute ${part.label}`}
                onClick={() => toggle(part.partId)}
              >
                <SpeakerIcon off={off} />
              </button>

              <span className="mixer__label">{part.label}</span>

              <button
                type="button"
                className={`mixer__solo${alone ? " is-active" : ""}`}
                onClick={() => solo(part.partId)}
                title={alone ? "Bring everyone back" : `Hear only ${part.label}`}
              >
                {alone ? "All back" : "Only this"}
              </button>
            </li>
          );
        })}
      </ul>

      <p className="mixer__hint">
        {player.state === "playing"
          ? "Changing the mix while it plays takes a moment to re-mix."
          : "Set the mix, then press Play — or change it while it's playing."}
      </p>
    </section>
  );
}

/*
 * Share, or take it back.
 *
 * Every piece is saved private the moment it is generated, so this is the only
 * thing that ever makes one visible to anyone else — which is why it says
 * plainly which state it is in rather than relying on the look of a switch.
 */
function ShareControl({ visibility, caption = "", saving, onChange }) {
  const [copied, setCopied] = useState(false);
  const [draft, setDraft] = useState(caption);
  const [writing, setWriting] = useState(false);
  const isPublic = visibility === "public";

  // A caption edited elsewhere (or a different piece loaded into the same
  // reader) should replace whatever is sitting in the box.
  useEffect(() => { setDraft(caption); setWriting(false); }, [caption]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.warn("Clipboard unavailable:", error);
    }
  }

  return (
    <section className={`share${isPublic ? " is-public" : ""}`}>
      <p className="share__state">
        <span className="share__dot" aria-hidden="true" />
        {isPublic ? "Posted to the gallery" : "Private to you"}
      </p>
      <p className="share__hint">
        {isPublic
          ? "Anyone signed in can find this piece and read every part."
          : "Saved to your library. Nobody else can see it."}
      </p>

      {isPublic && caption && !writing && (
        <p className="share__caption">“{caption}”</p>
      )}

      {(writing || (!isPublic && draft)) && (
        <label className="share__write">
          <span className="sr-only">Say something about this piece</span>
          <textarea
            rows={3}
            maxLength={600}
            placeholder="Say something about it…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
      )}

      <div className="share__actions">
        {!isPublic && !writing && !draft && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={() => setWriting(true)}
          >
            Share it
          </button>
        )}

        {!isPublic && (writing || draft) && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={() => onChange("public", draft.trim())}
            disabled={saving}
          >
            {saving ? "Posting…" : "Post it"}
          </button>
        )}

        {isPublic && !writing && (
          <>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => onChange("private")}
              disabled={saving}
            >
              Make it private
            </button>
            <button type="button" className="btn btn-ghost btn-small" onClick={copyLink}>
              {copied ? "Link copied" : "Copy link"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => setWriting(true)}
            >
              {caption ? "Edit caption" : "Add a caption"}
            </button>
          </>
        )}

        {isPublic && writing && (
          <>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => onChange(undefined, draft.trim())}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save caption"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => { setDraft(caption); setWriting(false); }}
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </section>
  );
}


/*
 * Asking for a change.
 *
 * Most of what is wrong with a generated piece is small and local — one part
 * sits badly, the tempo is off, an entry is awkward. Regenerating throws away
 * everything that was right in order to fix one thing that wasn't, so this
 * sends the request to the model along with the music it already wrote and
 * changes only the lines it needs to.
 */
function ReviseBox({ activeLabel, isPart, revising, summary, canUndo, onRevise, onUndo }) {
  const [text, setText] = useState("");
  const [error, setError] = useState(null);

  const suggestions = isPart
    ? [`Make the ${activeLabel} part easier to play`,
       `Give the ${activeLabel} more of the melody`,
       `The ${activeLabel} part sounds awkward — rewrite it`]
    : ["Make it slower",
       "Make the ending stronger",
       "Give the inner parts more to do"];

  async function send(request) {
    const asked = (request || text).trim();
    if (!asked) return;
    setError(null);
    try {
      await onRevise(asked);
      setText("");
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="revise">
      <h2 className="score-block__head">Ask for a change</h2>
      <p className="revise__lede">
        Describe what you'd like different. The rest of the piece stays as it is.
      </p>

      <form
        onSubmit={(event) => { event.preventDefault(); send(); }}
        className="revise__form"
      >
        <textarea
          rows={3}
          maxLength={400}
          placeholder={isPart
            ? `e.g. the ${activeLabel} sits too high — bring it down an octave`
            : "e.g. the second half repeats too much — develop it instead"}
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={revising}
        />
        <button type="submit" className="btn btn-small" disabled={revising || !text.trim()}>
          {revising ? "Rewriting…" : "Send"}
        </button>
      </form>

      <ul className="revise__chips">
        {suggestions.map((suggestion) => (
          <li key={suggestion}>
            <button
              type="button"
              onClick={() => send(suggestion)}
              disabled={revising}
            >
              {suggestion}
            </button>
          </li>
        ))}
      </ul>

      {revising && (
        <p className="revise__working" aria-live="polite">
          Rewriting just what you asked about — this takes about as long as a
          short generation.
        </p>
      )}

      {error && <p className="form-message is-error" role="alert">{error}</p>}

      {summary && !revising && (
        <div className="revise__done">
          <p className="revise__summary">{summary}</p>
          {canUndo && (
            <button type="button" className="btn btn-ghost btn-small" onClick={onUndo}>
              Undo that change
            </button>
          )}
        </div>
      )}
    </section>
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

export function ScoreView({
  score,
  brief,
  pending = false,
  pendingLabel = "Writing…",
  error,
  onRegenerate,
  onNewBrief,
  visibility,
  caption,
  onVisibilityChange,
  onRevise,
  onUndo,
  canUndo = false,
  revising = false,
  revisionSummary,
  owner,
  ownerUid,
  saving = false,
  nav,
  footer,
}) {
  const [activeId, setActiveId] = useState("full");
  const [tune, setTune] = useState(null);
  const [sizeId, setSizeId] = useState(DEFAULT_SIZE);

  // A regenerated piece is a different piece: start it at the full score again.
  useEffect(() => setActiveId("full"), [score]);

  const size = SHEET_SIZES.find((option) => option.id === sizeId) || SHEET_SIZES[1];

  const sheets = [
    { id: "full", label: "Full score", abc: score.scoreAbc, nameEveryLine: true },
    ...score.parts.map((part) => ({
      id: part.partId,
      label: part.label,
      abc: part.abc,
      note: part.performanceNote,
      badge: part.transposed ? "transposing" : null,
      nameEveryLine: false,
    })),
    ...(score.conductor
      ? [{ id: "conductor", label: "Conductor", abc: score.scoreAbc, nameEveryLine: true }]
      : []),
  ];

  const active = sheets.find((sheet) => sheet.id === activeId) || sheets[0];
  const player = usePlayer(tune, active.abc);
  const base = slug(score.title);
  const fileBase = active.id === "full" || active.id === "conductor"
    ? `${base}-full-score`
    : `${base}-${slug(active.label)}`;

  const facts = [
    score.tempoText,
    `${score.tempoBpm} bpm`,
    score.timeSignature,
    score.concertKey,
    `${score.measureCount} bars`,
  ].filter(Boolean);

  const tags = [
    brief?.ensemble && { label: "Ensemble", value: brief.ensemble },
    brief?.level && { label: "Level", value: brief.level },
    brief?.mood && { label: "Mood", value: brief.mood },
    brief && { label: "Conductor", value: brief.conductor ? "Yes" : "No" },
  ].filter(Boolean);

  return (
    <div className="score-shell">
      <aside className="score-aside">
        <div className="score-aside__scroll">
          <p className="score-aside__brand">AURA</p>

          <h1 className="score-title">{score.title}</h1>
          <p className="score-facts">{facts.join(" · ")}</p>
          {owner && (
            <p className="score-owner">
              by {ownerUid ? <Link className="link" to={`/u/${ownerUid}`}>{owner}</Link> : owner}
            </p>
          )}

          {onVisibilityChange && <ShareControl
            visibility={visibility}
            caption={caption}
            saving={saving}
            onChange={onVisibilityChange}
          />}

          {onRevise && (
            <ReviseBox
              activeLabel={active.label}
              isPart={!active.nameEveryLine}
              revising={revising}
              summary={revisionSummary}
              canUndo={canUndo}
              onRevise={onRevise}
              onUndo={onUndo}
            />
          )}

          <section className="score-block">
            <h2 className="score-block__head">Written for</h2>
            <ul className="score-forlist">
              {writtenFor(score).map((entry) => <li key={entry}>{entry}</li>)}
            </ul>
            <p className="score-block__sub">
              {score.parts.length} part{score.parts.length === 1 ? "" : "s"} in all
            </p>
          </section>

          {tags.length > 0 && (
            <section className="score-block">
              <h2 className="score-block__head">The brief</h2>
              <dl className="score-tags">
                {tags.map((tag) => (
                  <div className="score-tag" key={tag.label}>
                    <dt>{tag.label}</dt>
                    <dd>{tag.value}</dd>
                  </div>
                ))}
              </dl>
              {brief?.notes && <p className="score-block__sub">“{brief.notes}”</p>}
            </section>
          )}

          <section className="score-block">
            <h2 className="score-block__head">About this piece</h2>
            <p className="score-about">{score.programNote}</p>
          </section>


          {nav && <div className="score-aside__nav">{nav}</div>}
        </div>

        {onRegenerate ? (
          <div className="score-again">
            <button type="button" className="btn" onClick={onRegenerate} disabled={pending}>
              {pending ? pendingLabel : "Write another piece"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={onNewBrief}
              disabled={pending}
            >
              Change the brief
            </button>
            <p className="score-again__hint">
              A new piece, written from the same brief. “Change the brief” takes you
              back to the questions.
            </p>
          </div>
        ) : footer ? (
          <div className="score-again">{footer}</div>
        ) : null}
      </aside>

      <main className="score-main">
        <div className="score-bar">
          {/* One tab per player. Reading your own part matters more than
              reading the full score, so the switch sits on top of the paper
              rather than tucked away in the rail. */}
          <div className="score-tabs" role="tablist" aria-label="Sheets">
            {sheets.map((sheet) => (
              <button
                key={sheet.id}
                type="button"
                role="tab"
                aria-selected={sheet.id === active.id}
                className={`score-tab${sheet.id === active.id ? " is-active" : ""}`}
                onClick={() => setActiveId(sheet.id)}
              >
                {sheet.label}
                {sheet.badge && <span className="score-tab__badge">{sheet.badge}</span>}
              </button>
            ))}
          </div>

          <div className="score-bar__tools">
            <div className="score-tools">
              <PlayButton player={player} disabled={!tune} />
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

            <div className="sheet-size" role="group" aria-label="Sheet size">
              <span className="sheet-size__label">Size</span>
              {SHEET_SIZES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={option.id === size.id}
                  className={`sheet-size__btn${option.id === size.id ? " is-active" : ""}`}
                  onClick={() => setSizeId(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {pending && (
          <p className="score-pending" aria-live="polite">
            {pendingLabel} — the sheet below stays put until the new piece arrives.
          </p>
        )}

        {error && !pending && (
          <p className="form-message is-error" role="alert">{error}</p>
        )}

        {score.warnings?.length > 0 && (
          <div className="score-warnings" role="alert">
            {score.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
          </div>
        )}

        {active.note && <p className="score-partnote">{active.note}</p>}

        {active.nameEveryLine && <Mixer parts={score.parts} player={player} />}

        <div className="printable">
          <Sheet
            abc={active.abc}
            size={size}
            nameEveryLine={active.nameEveryLine}
            onTune={setTune}
          />
        </div>

        {active.id === "conductor" && <ConductorNotes conductor={score.conductor} />}

        {active.id === "full" && score.rehearsalTips && (
          <div className="podium">
            <h3>Rehearsing this</h3>
            <p>{score.rehearsalTips}</p>
          </div>
        )}

        <p className="score-footnote">
          The .abc file opens in MuseScore, and MIDI imports into any notation or DAW
          software if you want to edit the piece further.
        </p>
      </main>
    </div>
  );
}
