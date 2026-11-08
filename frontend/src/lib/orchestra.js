/*
 * An orchestra of recorded instruments, for playing the score back.
 *
 * abcjs plays notation through a General MIDI soundfont, and no GM set sounds
 * like players. This plays every note from recordings of real instruments:
 *
 *  - tonejs-instruments: mostly the Philharmonia Orchestra's free samples
 *    (CC-BY 3.0), compact MP3s;
 *  - VSCO 2 Community Edition by Versilian Studios (CC0): the instruments the
 *    first set lacks — viola section, oboe, piccolo, timpani, marimba, drums;
 *  - MusyngKite, the sample set the app used before, only where neither has a
 *    recording: vibraphone, harpsichord, synthesizer and voices.
 *
 * abcjs still turns the ABC into notes (pitch, timing, dynamics); this file
 * decides how they sound. Each note is played from the recording nearest its
 * pitch, retuned, so only the samples a piece actually uses are downloaded.
 *
 * Levels: the three sets were recorded tens of decibels apart. Every source
 * below carries a trim, measured from its samples' loudest half-second, that
 * brings it to a common level; `balance` then sets the orchestral blend.
 */

const TONEJS = "https://nbrosowsky.github.io/tonejs-instruments/samples/";
// Raw GitHub rather than jsDelivr: jsDelivr refuses parts of this repository
// for exceeding its package size limit.
const VSCO = "https://raw.githubusercontent.com/sgossner/VSCO-2-CE/master/";
const GM = "https://paulrosen.github.io/midi-js-soundfonts/MusyngKite/";

const NOTE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

/** "As4" / "A#4" / "Bb3" / "C4" -> MIDI number. */
function noteToMidi(name) {
  const match = /^([A-G])(s|#|b)?(-?\d)$/.exec(name);
  if (!match) return null;
  const shift = match[2] === "s" || match[2] === "#" ? 1 : match[2] === "b" ? -1 : 0;
  return 12 * (Number(match[3]) + 1) + NOTE[match[1]] + shift;
}

const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

/** A tonejs-instruments folder: one MP3 per listed note. */
const tonejs = (folder, notes, trim, extra = {}) => ({
  trim,
  ...extra,
  samples: notes.split(" ").map((name) => ({
    root: noteToMidi(name),
    url: `${TONEJS}${folder}/${name}.mp3`,
  })),
});

/*
 * VSCO files are named with middle C as C3, an octave below scientific pitch —
 * measured: its "Oboe A#2" sounds at 233 Hz (B-flat 3). So every root is
 * raised an octave here.
 */
const vsco = (pattern, notes, trim, extra = {}) => ({
  trim,
  ...extra,
  samples: notes.split(" ").map((name) => ({
    root: noteToMidi(name.replace("#", "s")) + 12,
    url: VSCO + encodePath(pattern.replace("{}", name)),
  })),
});

/** A MusyngKite instrument: every note recorded, so nothing is retuned. */
const gm = (name, trim, extra = {}) => ({ trim, gm: name, ...extra });

/** Unpitched: the rhythm staff's low and high sounds. */
const hits = (low, high, trims, extra = {}) => ({
  ...extra,
  hits: { low: { url: VSCO + encodePath(low), trim: trims[0] },
          high: { url: VSCO + encodePath(high), trim: trims[1] } },
});

// Release times in seconds: how long a note rings on after it is let go.
const BOWED = { release: 0.35, attack: 0.04 };
const BLOWN = { release: 0.2, attack: 0.02 };
const STRUCK = { release: 1.2, attack: 0.002, ring: true };
const PLUCKED = { release: 0.8, attack: 0.002, ring: true };

const VIOLIN = tonejs("violin", "G3 C4 E4 G4 A4 C5 E5 G5 A5 C6 E6 G6 A6 C7", 1.12,
                      { ...BOWED, balance: 1, pan: -0.35 });
const CLARINET = tonejs("clarinet", "D3 F3 As3 D4 F4 As4 D5 F5 As5 D6", 0.51,
                        { ...BLOWN, balance: 0.85, pan: 0.05 });
const SAX = tonejs("saxophone", "Cs3 Ds3 F3 G3 A4 As3 B3 C4 Cs4 D4 Ds4 E4 F4 Fs4 G4 Gs4 As4 " +
                   "B4 C5 Cs5 D5 Ds5 E5 F5 Fs5 G5 Gs5", 0.6,
                   { ...BLOWN, balance: 0.85, pan: 0.1 });
const TUBA = tonejs("tuba", "F1 As1 F2 As2 D3 F3 As3 D4", 0.76,
                    { ...BLOWN, balance: 1, pan: 0.3 });
const OBOE = vsco("Woodwinds/Oboe/Vib/Oboe_Vib_{}_v1_Main.wav",
                  "A#2 D3 F3 A#3 D4 F4 A#4 D5 F5", 14.5, { ...BLOWN, balance: 0.85, pan: 0 });
const FLUTE = tonejs("flute", "C4 E4 A4 C5 E5 A5 C6 E6 A6 C7", 0.62,
                     { ...BLOWN, balance: 0.85, pan: -0.1 });

/** Every instrument the compose form offers, by the name it uses. */
export const INSTRUMENTS = {
  // Strings
  Violin: VIOLIN,
  Viola: vsco("Strings/Viola Section/susvib/ViolaEns_susvib_{}_v1_1.wav",
              "C2 E2 G2 B2 D3 F3 A3 C4 E4 G4 B4 D5", 9.2, { ...BOWED, balance: 1, pan: 0.15 }),
  Cello: tonejs("cello", "C2 E2 G2 A2 C3 E3 G3 A3 C4 E4 G4 A4 C5", 0.53,
                { ...BOWED, balance: 1, pan: 0.3 }),
  "Double bass": tonejs("contrabass", "Fs1 As1 C2 D2 E2 Gs2 A2 Cs3 E3 Gs3 B3", 0.55,
                        { ...BOWED, balance: 1, pan: 0.4 }),
  Harp: tonejs("harp", "E1 B1 D2 F2 A2 C3 E3 G3 B3 D4 F4 A4 C5 E5 G5 B5 D6 F6 A6 D7 F7", 1.2,
               { ...PLUCKED, release: 1.5, balance: 0.9, pan: -0.3 }),
  "Acoustic guitar": tonejs("guitar-acoustic", "D2 E2 G2 A2 C3 E3 G3 A3 C4 E4 G4 A4 C5 D5",
                            0.83, { ...PLUCKED, balance: 0.9, pan: -0.15 }),
  "Electric guitar": tonejs("guitar-electric", "E2 A2 C3 Fs3 A3 C4 Fs4 A4 C5 Fs5 A5 C6", 1.0,
                            { ...PLUCKED, balance: 0.85, pan: -0.15 }),
  "Bass guitar": tonejs("bass-electric", "E1 G1 As1 Cs2 E2 G2 As2 Cs3 E3 G3 As3 Cs4 E4", 0.95,
                        { ...PLUCKED, release: 0.4, balance: 1, pan: 0.2 }),
  Ukulele: tonejs("guitar-nylon", "Cs4 Fs4 A4 E5 Fs5 A5", 0.84,
                  { ...PLUCKED, balance: 0.85, pan: -0.1 }),

  // Woodwinds
  Flute: FLUTE,
  Piccolo: vsco("Woodwinds/Piccolo/Sus/piccolo_{}_sustain1.wav", "G4 C5 G5 C6 G6", 4.4,
                { ...BLOWN, balance: 0.7, pan: -0.1 }),
  Clarinet: CLARINET,
  "Bass clarinet": CLARINET,
  Oboe: OBOE,
  "English horn": OBOE,
  Bassoon: tonejs("bassoon", "G2 A2 C3 G3 A3 C4 E4 G4 A4 C5", 0.57,
                  { ...BLOWN, balance: 0.9, pan: 0.1 }),
  "Alto saxophone": SAX,
  "Tenor saxophone": SAX,
  "Baritone saxophone": SAX,
  Recorder: FLUTE,

  // Brass
  Trumpet: tonejs("trumpet", "F3 A3 C4 Ds4 F4 G4 As4 D5 F5 A5 C6", 0.8,
                  { ...BLOWN, balance: 1, pan: 0.2 }),
  "French horn": tonejs("french-horn", "A1 C2 Ds2 G2 D3 F3 A3 C4 D5 F5", 0.75,
                        { ...BLOWN, release: 0.3, balance: 0.95, pan: -0.2 }),
  Trombone: tonejs("trombone", "As1 Cs2 Ds2 F2 Gs2 As2 C3 D3 Ds3 F3 Gs3 As3 C4 Cs4 D4 Ds4 F4",
                   0.58, { ...BLOWN, balance: 1, pan: 0.25 }),
  Euphonium: TUBA,
  Tuba: TUBA,

  // Percussion. Timpani roots are measured: the hits are named by drum, not
  // pitch, so each sample's fundamental was found by autocorrelation.
  Timpani: {
    ...STRUCK, release: 1.8, balance: 0.8, pan: 0, trim: 6.2,
    samples: [
      { root: 34.4, url: VSCO + encodePath("Percussion/Timpani/Timpani2_Hit_v3_rr1_Sum.wav") },
      { root: 37.2, url: VSCO + encodePath("Percussion/Timpani/Timpani3_Hit_v3_rr1_Sum.wav") },
      { root: 40.1, url: VSCO + encodePath("Percussion/Timpani/Timpani4_Hit_v3_rr1_Sum.wav") },
      { root: 42.1, url: VSCO + encodePath("Percussion/Timpani/Timpani5_Hit_v3_rr1_Sum.wav") },
    ],
  },
  Marimba: vsco("Percussion/Marimba/Marimba_hit_Outrigger_{}_loud_01.wav",
                "F1 C2 G2 B2 F3 C4 G4 B4 F5 C6", 4.1, { ...STRUCK, balance: 0.85, pan: -0.05 }),
  Xylophone: tonejs("xylophone", "G4 C5 G5 C6 G6 C7 G7 C8", 0.94,
                    { ...STRUCK, release: 0.5, balance: 0.75, pan: -0.05 }),
  Vibraphone: gm("vibraphone", 4.4, { ...STRUCK, release: 1.6, balance: 0.8, pan: -0.05 }),
  "Snare drum": hits("Percussion/Snare2-HitNS_v5_rr1_Sum.wav",
                     "Percussion/Snare2-HitSN_v5_rr1_Sum.wav", [1.16, 10.7],
                     { ...STRUCK, release: 0.3, balance: 0.7, pan: 0.05 }),
  "Drum set": hits("Percussion/BDrumNewhit_v4_rr1_Sum.wav",
                   "Percussion/Snare2-HitNS_v5_rr1_Sum.wav", [2.7, 1.16],
                   { ...STRUCK, release: 0.3, balance: 0.7, pan: 0.05 }),
  "Hand percussion": hits("Percussion/Tumba-HitN_v2_rr1_Sum.wav",
                          "Percussion/Quinto-HitN_v2_rr1_Sum.wav", [16, 11.7],
                          { ...STRUCK, release: 0.3, balance: 0.7, pan: 0.1 }),

  // Keyboards
  Piano: tonejs("piano", "C1 Ds1 Fs1 A1 C2 Ds2 Fs2 A2 C3 Ds3 Fs3 A3 C4 Ds4 Fs4 A4 C5 Ds5 " +
                "Fs5 A5 C6 Ds6 Fs6 A6 C7 Ds7 Fs7 A7 C8", 0.58,
                { ...STRUCK, release: 0.6, balance: 0.9, pan: -0.25 }),
  Organ: tonejs("organ", "C1 Ds1 Fs1 A1 C2 Ds2 Fs2 A2 C3 Ds3 Fs3 A3 C4 Ds4 Fs4 A4 C5 Ds5 " +
                "Fs5 A5 C6", 0.73, { ...BLOWN, release: 0.3, balance: 0.7, pan: 0 }),
  Harpsichord: gm("harpsichord", 6.7, { ...PLUCKED, balance: 0.8, pan: -0.25 }),
  Synthesizer: gm("pad_2_warm", 2.2, { ...BLOWN, release: 0.6, balance: 0.7, pan: 0 }),
  Accordion: tonejs("harmonium", "C2 Ds2 Fs2 A2 C3 Ds3 Fs3 A3 C4 Ds4 F4 A4 C5", 1.0,
                    { ...BLOWN, balance: 0.8, pan: 0.1 }),

  // Voices
  Soprano: gm("choir_aahs", 8.2, { ...BOWED, balance: 0.9, pan: -0.2 }),
  "Alto (voice)": gm("choir_aahs", 8.2, { ...BOWED, balance: 0.9, pan: -0.1 }),
  "Tenor (voice)": gm("choir_aahs", 8.2, { ...BOWED, balance: 0.9, pan: 0.1 }),
  "Bass (voice)": gm("choir_aahs", 8.2, { ...BOWED, balance: 0.9, pan: 0.2 }),
};

// A keyboard inside an orchestra is a colour under the strings, not the
// engine: it plays this much quieter than its own level.
const ACCOMPANYING_KEYBOARD = 0.45;
const KEYBOARDS = new Set(["Piano", "Organ", "Harpsichord", "Synthesizer", "Accordion"]);

/* ---------- loading ---------- */

const buffers = new Map(); // url -> Promise<AudioBuffer>

function load(context, url) {
  if (!buffers.has(url)) {
    buffers.set(url, fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${url}`);
        return response.arrayBuffer();
      })
      .then((data) => context.decodeAudioData(data))
      .catch((error) => {
        buffers.delete(url);   // let a later play try again
        throw error;
      }));
  }
  return buffers.get(url);
}

function sampleFor(spec, pitch) {
  if (spec.gm) {
    const midi = Math.max(21, Math.min(108, Math.round(pitch)));
    return { root: midi, url: `${GM}${spec.gm}-mp3/${FLATS[midi % 12]}${Math.floor(midi / 12) - 1}.mp3` };
  }
  return spec.samples.reduce((best, s) =>
    Math.abs(s.root - pitch) < Math.abs(best.root - pitch) ? s : best);
}

/* ---------- the hall ---------- */

/** A concert-hall tail: decaying stereo noise, about two seconds. */
function hallImpulse(context, seconds = 2.2) {
  const rate = context.sampleRate;
  const length = Math.floor(rate * seconds);
  const impulse = context.createBuffer(2, length, rate);
  for (let channel = 0; channel < 2; channel += 1) {
    const data = impulse.getChannelData(channel);
    for (let i = 0; i < length; i += 1) {
      const t = i / rate;
      const early = t < 0.012 ? 0 : 1;
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 3.2) * early;
    }
  }
  return impulse;
}

function hall(context) {
  const input = context.createGain();
  const dry = context.createGain();
  const wet = context.createGain();
  const reverb = context.createConvolver();
  const compressor = context.createDynamicsCompressor();
  const master = context.createGain();
  reverb.buffer = hallImpulse(context);
  dry.gain.value = 0.82;
  wet.gain.value = 0.32;
  compressor.threshold.value = -16;
  compressor.knee.value = 12;
  compressor.ratio.value = 3;
  compressor.attack.value = 0.01;
  compressor.release.value = 0.25;
  input.connect(dry).connect(compressor);
  input.connect(reverb).connect(wet).connect(compressor);
  compressor.connect(master).connect(context.destination);
  return { input, master };
}

/* ---------- the ending ---------- */

// The last two bars slow gradually to this fraction of the tempo, and the
// final chord is held this many times its written length — a fermata.
const RIT_SLOWEST = 0.6;
const FERMATA = 2.2;

/**
 * Score time (in bars) <-> seconds, with the closing ritardando built in.
 * Before the last two bars the tempo is the written one; across them each
 * bar stretches a little more than the last.
 */
export function makeTiming(secondsPerBar, totalBars) {
  const stretch = 1 / RIT_SLOWEST - 1;
  const from = totalBars >= 3 ? totalBars - 2 : Infinity;
  const toSeconds = (bar) => {
    if (bar <= from) return bar * secondsPerBar;
    const into = bar - from;
    return secondsPerBar * (from + into + (stretch * into * into) / 4);
  };
  const toBars = (seconds) => {
    let low = 0;
    let high = totalBars + 4;
    for (let i = 0; i < 40; i += 1) {
      const mid = (low + high) / 2;
      if (toSeconds(mid) < seconds) low = mid; else high = mid;
    }
    return low;
  };
  return { secondsPerBar, totalBars, toSeconds, toBars, finalBar: totalBars - 1 };
}

/* ---------- playing ---------- */

/**
 * Turn abcjs's flattened sequence into notes in seconds.
 *
 * `voices[i]` describes track i: { instrument, transpose, muted }. abcjs
 * reports written pitch, so each part's transposition is added back here —
 * a B-flat trumpet sounds a tone below what it reads.
 */
export function notesFrom(sequence, voices, timing, meterSize, ensembleSize) {
  const notes = [];
  sequence.tracks.forEach((track, index) => {
    const voice = voices[index];
    if (!voice || voice.muted) return;
    const spec = INSTRUMENTS[voice.instrument] || INSTRUMENTS.Piano;
    const pitches = [...new Set(track.filter((e) => e.cmd === "note").map((e) => e.pitch))]
      .sort((a, b) => a - b);
    const level = (spec.balance ?? 1)
      * (KEYBOARDS.has(voice.instrument) && ensembleSize > 2 ? ACCOMPANYING_KEYBOARD : 1);

    for (const event of track) {
      if (event.cmd !== "note" || !(event.duration > 0)) continue;
      const startBar = event.start / meterSize;
      const endBar = (event.start + event.duration - (event.gap || 0) / 1920) / meterSize;
      const start = timing.toSeconds(startBar);
      let end = timing.toSeconds(endBar);
      if (startBar >= timing.finalBar - 1e-6) end = start + (end - start) * FERMATA;
      // Dynamics arrive as MIDI velocity; loudness follows it on a curve, so
      // pp is genuinely quiet rather than a slightly softer mf.
      const velocity = Math.max(0, Math.min(127, event.volume ?? 90)) / 127;
      notes.push({
        voice: index,
        instrument: voice.instrument,
        spec,
        pitch: spec.hits ? null : event.pitch + (voice.transpose || 0),
        hit: spec.hits ? (event.pitch === pitches[0] ? "low" : "high") : null,
        start,
        end: Math.max(end, start + 0.05),
        gain: level * velocity * velocity,
        pan: spec.pan ?? 0,
      });
    }
  });
  return notes.sort((a, b) => a.start - b.start);
}

/** Download every recording these notes need. */
export async function prepare(context, notes) {
  const urls = new Set();
  for (const note of notes) {
    urls.add(note.hit ? note.spec.hits[note.hit].url : sampleFor(note.spec, note.pitch).url);
  }
  await Promise.all([...urls].map((url) => load(context, url)));
}

/**
 * Play `notes` from `offset` seconds in. Returns { stop(), startedAt, duration }.
 * Notes are scheduled a little ahead of time as playback goes, not all at
 * once: a full score is tens of thousands of notes.
 */
export async function perform(context, notes, offset = 0) {
  const { input, master } = hall(context);
  // Many players at once sum loud; scale the ensemble so tutti fits.
  const voices = new Set(notes.map((n) => n.voice)).size || 1;
  master.gain.value = 0.9 / Math.sqrt(Math.max(1, voices / 2));
  const panners = new Map();
  const panner = (pan) => {
    if (!panners.has(pan)) {
      const node = context.createStereoPanner();
      node.pan.value = pan;
      node.connect(input);
      panners.set(pan, node);
    }
    return panners.get(pan);
  };

  const resolved = await Promise.all(notes.map(async (note) => {
    if (note.hit) {
      const hit = note.spec.hits[note.hit];
      return { buffer: await load(context, hit.url), rate: 1, trim: hit.trim };
    }
    const sample = sampleFor(note.spec, note.pitch);
    return {
      buffer: await load(context, sample.url),
      rate: Math.pow(2, (note.pitch - sample.root) / 12),
      trim: note.spec.trim ?? 1,
    };
  }));

  const t0 = context.currentTime + 0.15 - offset;
  const playing = new Set();
  let next = notes.findIndex((n) => n.start >= offset);
  if (next < 0) next = notes.length;
  const lastEnd = notes.reduce((m, n) => Math.max(m, n.end + (n.spec.release || 0.3)), 0);

  const schedule = () => {
    const horizon = context.currentTime - t0 + 1.2;
    while (next < notes.length && notes[next].start < horizon) {
      const note = notes[next];
      const { buffer, rate, trim } = resolved[next];
      next += 1;
      const at = t0 + note.start;
      const until = t0 + note.end;
      const { attack = 0.01, release = 0.3 } = note.spec;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = rate;
      const gain = context.createGain();
      const peak = note.gain * trim;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(peak, at + attack);
      // Struck and plucked sounds decay on their own; held ones are let go
      // when the note ends.
      gain.gain.setValueAtTime(peak, until);
      gain.gain.setTargetAtTime(0, until, release / 3);
      source.connect(gain).connect(panner(note.pan));
      source.start(at);
      source.stop(until + release * 2 + 0.1);
      playing.add(source);
      source.onended = () => playing.delete(source);
    }
  };
  schedule();
  const timer = setInterval(schedule, 100);

  return {
    startedAt: t0,
    duration: lastEnd,
    stop() {
      clearInterval(timer);
      const now = context.currentTime;
      master.gain.cancelScheduledValues(now);
      master.gain.setValueAtTime(master.gain.value, now);
      master.gain.linearRampToValueAtTime(0, now + 0.08);
      for (const source of playing) {
        try { source.stop(now + 0.1); } catch { /* already stopped */ }
      }
      setTimeout(() => master.disconnect(), 200);
    },
  };
}

/** Play a short phrase on one instrument — the roster preview. */
export async function previewNotes(context, instrument, pitches, secondsPerNote) {
  const spec = INSTRUMENTS[instrument] || INSTRUMENTS.Piano;
  const notes = pitches.map((pitch, i) => ({
    voice: 0,
    instrument,
    spec,
    pitch: spec.hits ? null : pitch,
    hit: spec.hits ? (pitch === Math.min(...pitches) ? "low" : "high") : null,
    start: i * secondsPerNote,
    end: (i + 1) * secondsPerNote * (i === pitches.length - 1 ? 2 : 0.95),
    gain: (spec.balance ?? 1) * 0.6,
    pan: 0,
  }));
  await prepare(context, notes);
  return perform(context, notes, 0);
}
