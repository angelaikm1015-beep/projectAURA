/*
 * Which samples the browser plays notation with.
 *
 * abcjs defaults to FluidR3_GM. MusyngKite is the other General MIDI set it
 * ships support for, and it is warmer on strings and winds — the instruments
 * this app writes for most. Both are sample sets of the same kind, so this is a
 * real improvement but a bounded one: no General MIDI soundfont sounds like a
 * recording of players. For that, the Download MIDI button exists, and MuseScore
 * with Muse Sounds is a different league.
 *
 * One constant so the sheet and the instrument previews can never drift apart
 * and sound like two different orchestras.
 */
export const SOUNDFONT_URL = "https://paulrosen.github.io/midi-js-soundfonts/MusyngKite/";
