/*
 * Hearing an instrument before you commit to it.
 *
 * Choosing a viola over a cello is a decision about sound, and a dropdown of
 * names doesn't help you make it. This plays a short phrase in the instrument's
 * own voice, in the register it actually sits in, so the roster is built by ear.
 *
 * It is the same orchestra the finished score plays through (lib/orchestra.js)
 * — the same recordings, the same hall — so what you hear here is what the
 * piece will sound like.
 */

import { previewNotes } from "./orchestra";

const LETTERS = ["C", "D", "E", "F", "G", "A", "B"];
const SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

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

const midiOf = ({ letter, octave }) => 12 * (octave + 1) + SEMITONES[letter];

/** The bottom of the instrument's comfortable range, e.g. "G3-D5" -> G3. */
function bottomOf(easy) {
  const low = String(easy || "").split("-")[0];
  return parsePitch(low) || parsePitch("C4");
}

/**
 * A short rising arpeggio, one octave, starting a little above the bottom of
 * the range so nothing sounds strained. Sounding pitch: the range is written,
 * so the instrument's transposition is added.
 */
function phraseFor(info) {
  if (info.pitched === false) {
    // Unpitched percussion reads as rhythm, not pitch: two sounds, a simple bar.
    return [60, 60, 64, 60, 60, 64];
  }
  const root = step(bottomOf(info.easy), 2);
  return [0, 2, 4, 7, 4, 2, 0].map((s) => midiOf(step(root, s)) + (info.transpose || 0));
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
  return typeof window !== "undefined"
    && Boolean(window.AudioContext || window.webkitAudioContext);
}

/**
 * Play a short phrase in `info`'s voice (`info.name` is the instrument).
 * Resolves when the sound starts, not when it ends; rejects if audio is
 * unavailable or the recordings can't be fetched.
 */
export async function previewInstrument(info) {
  stopPreview();
  if (!previewSupported()) throw new Error("Audio isn't available in this browser.");

  // Created inside the click that asked for it, or browsers refuse to start it.
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  await audioContext.resume();

  // Quarter notes at 132 bpm, as the phrase was always played.
  const performance = await previewNotes(audioContext, info.name, phraseFor(info), 60 / 132);
  current = performance;
  return performance;
}
