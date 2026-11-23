/*
 * The compose answers, in the words the user picked them by.
 *
 * The API stores the brief as the raw values that were sent ("higher-intermediate",
 * "college"), because those are what the generator reads. A score on screen needs
 * the labels instead, and a score reopened from the library has to show the same
 * thing a freshly generated one does — so the mapping lives here, once.
 */

/** Raw compose payload -> the labelled brief ScoreView displays. */
export function describeBrief(options, sent) {
  if (!sent) return null;
  const label = (list, value) => list?.find((item) => item.value === value)?.label || value;

  const keys = { Db: "D♭", Eb: "E♭", "F#": "F♯", Ab: "A♭", Bb: "B♭" };
  const key = [sent.keyRoot && (keys[sent.keyRoot] || sent.keyRoot), sent.keyMode]
    .filter(Boolean).join(" ");
  const length = sent.length?.unit ? `${sent.length.value} ${sent.length.unit}` : "";
  const chosen = [sent.timeSignature, key, length].filter(Boolean).join(" · ");
  const scales = { chamber: "a chamber group", small: "a small orchestra", full: "a full orchestra" };

  return {
    // What the user fixed on the form, if anything ("3/4 · D minor · 3 minutes").
    choices: chosen,
    // "Chosen by GPT for a small orchestra", when the user let it choose.
    instrumentsBy: sent.instrumentMode === "auto"
      ? `Chosen by GPT for ${scales[sent.orchestraScale] || "the orchestra"}`
      : "",
    ensembleWhy: sent.ensembleWhy || "",
    ensemble: label(options.ensembleGroups, sent.ensembleGroup),
    level: label(options.levels, sent.level),
    mood: label(options.moods, sent.mood),
    conductor: sent.conductor,
    notes: sent.notes || "",
  };
}
