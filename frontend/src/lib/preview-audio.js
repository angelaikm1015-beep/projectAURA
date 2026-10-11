/*
 * Hearing an instrument before you commit to it.
 *
 * Choosing a viola over a cello is a decision about sound, and a dropdown of
 * names doesn't help you make it. This plays a short phrase in the instrument's
 * own voice, in the register it actually sits in, so the roster is built by ear.
 *
 * It is the same machinery the finished score plays through — abcjs builds a
 * tiny tune, the soundfont supplies the timbre — so what you hear here is what
 * the piece will sound like.
 */

import abcjs from "abcjs";

import { SOUNDFONT_URL } from "./soundfont";

const LETTERS = ["C", "D", "E", "F", "G", "A", "B"];

/** "F#4" / "Bb2" / "G3" -> { letter, accidental, octave }, or null. */
function parsePitch(text) {
  const match = /^([A-Ga-g])([#b]?)(-?\d)$/.exec(String(text || "").trim());
  if (!match) return null;
  return {
    letter: match[1].toUpperCase(),
    accidental: match[2],
    octave: Number(match[3]),
  };
}

/** Scientific pitch -> ABC, where middle C (C4) is "C" and C5 is "c". */
function toAbc({ letter, accidental, octave }) {
  const mark = accidental === "#" ? "^" : accidental === "b" ? "_" : "";
  if (octave >= 5) return mark + letter.toLowerCase() + "'".repeat(octave - 5);
  return mark + letter + ",".repeat(Math.max(0, 4 - octave));
}

/** Step up the C-major letters from a starting pitch. */
function step(pitch, steps) {
  const index = LETTERS.indexOf(pitch.letter) + steps;
  return {
    letter: LETTERS[((index % 7) + 7) % 7],
    // Accidentals are dropped deliberately: this is a timbre demo, and a plain
    // major arpeggio reads as "this is the instrument" rather than as music.
    accidental: "",
    octave: pitch.octave + Math.floor(index / 7),
  };
}

/** The bottom of the instrument's comfortable range, e.g. "G3-D5" -> G3. */
function bottomOf(easy) {
  const low = String(easy || "").split("-")[0];
  return parsePitch(low) || parsePitch("C4");
}

/**
 * A short rising arpeggio, one octave, starting a little above the bottom of
 * the range so nothing sounds strained.
 */
function phraseFor(info) {
  if (info.pitched === false) {
    // Unpitched percussion reads as rhythm, not pitch: two sounds, a simple bar.
    return { music: "c2 c e c2 c e |", channel: 10 };
  }
  const root = step(bottomOf(info.easy), 2);
  const notes = [0, 2, 4, 7, 4, 2, 0]
    .map((s) => toAbc(step(root, s)));
  return {
    music: `${notes.slice(0, 4).map((n) => `${n}2`).join(" ")} | ${notes.slice(4).map((n) => `${n}2`).join(" ")}4 |`,
    channel: null,
  };
}

function abcFor(info) {
  const { music, channel } = phraseFor(info);
  return [
    "X:1",
    "M:4/4",
    "L:1/8",
    "Q:1/4=132",
    `V:1 clef=${info.pitched === false ? "perc stafflines=1" : info.clef || "treble"}`,
    "K:C",
    "V:1",
    channel ? `%%MIDI channel ${channel}` : null,
    `%%MIDI program ${info.midi ?? 0}`,
    music,
  ].filter(Boolean).join("\n");
}

// One instrument at a time: starting a second preview stops the first, so
// clicking down a list doesn't pile voices on top of each other.
let current = null;
let audioContext = null;

export function stopPreview() {
  current?.stop();
  current = null;
}

export function previewSupported() {
  return typeof abcjs.synth?.supportsAudio === "function" && abcjs.synth.supportsAudio();
}

/**
 * Play a short phrase in `info`'s voice. Resolves when the sound starts, not
 * when it ends; rejects if audio is unavailable.
 */
export async function previewInstrument(info) {
  stopPreview();
  if (!previewSupported()) throw new Error("Audio isn't available in this browser.");

  // Created inside the click that asked for it, or browsers refuse to start it.
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  await audioContext.resume();

  const [tune] = abcjs.renderAbc("*", abcFor(info));
  const synth = new abcjs.synth.CreateSynth();
  await synth.init({
    audioContext,
    visualObj: tune,
    // The range is written pitch; this is what the audience actually hears.
    options: { midiTranspose: info.transpose || 0, soundFontUrl: SOUNDFONT_URL },
  });
  await synth.prime();
  current = synth;
  synth.start();
  return synth;
}
