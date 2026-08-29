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

  return {
    ensemble: label(options.ensembleGroups, sent.ensembleGroup),
    level: label(options.levels, sent.level),
    mood: label(options.moods, sent.mood),
    conductor: sent.conductor,
    notes: sent.notes || "",
  };
}
