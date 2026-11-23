"""The two things a listener holds on to: the main melody, and the solos.

Asked politely, the model gives a piece a main theme and a solo or two about
half the time, and a theme that "returns" is often unrecognisable. So in deep
mode both are made certain, here, between the plan and the score:

* **The main theme** (theme A, written in the plan with its own chords) is
  stated at the start, at the climax and near the end. In each of those
  sections Python writes the theme itself into the lead's part — moved into the
  section's key and into the instrument's singing register — and sets the
  section's first bars to the theme's chords, so tune and harmony agree. The
  passage writer sees it as already written and composes around it.
* **Solos** — one, or two at the higher levels — are placed in suitable
  middle sections for instruments with a singing voice, and each is composed
  by a call of its own with nothing else to think about, checked by melody.py
  and sent back once if it has faults. The passage writers lead into it and
  out of it.
"""

import copy
from fractions import Fraction

import compact
import form
import options
import orchestrate

# Instruments with a singing voice, best first: who gets the main theme when
# the plan names nobody, and who gets a solo.
SINGERS = ("Violin", "Oboe", "Flute", "Clarinet", "Cello", "French horn", "Trumpet",
           "English horn", "Viola", "Alto saxophone", "Bassoon", "Soprano", "Trombone",
           "Tenor saxophone", "Euphonium", "Recorder", "Tenor (voice)", "Alto (voice)",
           "Piano", "Harp", "Marimba", "Vibraphone")
MAX_SOLOS = 3
SOLO_BARS = (4, 12)        # a solo section's length
THEME_BARS = 8             # the most of theme A written in for the lead


def _mode(key: str) -> bool:
    return orchestrate._split_key(key)[1]          # True for minor


def _singers(roster: list) -> list:
    rank = {name: i for i, name in enumerate(SINGERS)}
    return sorted((p for p in roster if p["pitched"] and p["instrument"] in rank),
                  key=lambda p: rank[p["instrument"]])


# ---------------------------------------------------------------------------
# The main theme
# ---------------------------------------------------------------------------

def main_theme(plan: dict, units: Fraction):
    """(bars of theme A as Events, its chords) in the home key, or (None, None)."""
    themes = plan.get("themes") or []
    theme = next((t for t in themes if str(t.get("id")).strip().upper() == "A"),
                 themes[0] if themes else None)
    measures = [m for m in (theme or {}).get("measures") or [] if str(m).strip()][:THEME_BARS]
    if not measures:
        return None, None
    bars = [compact.read_measure(m, units)[0] for m in measures]
    if sum(1 for events in bars for e in events if e.notes) < 3:
        return None, None
    chords = [form.orchestral_chord(" ".join(form.bar_chords(c)))
              for c in (theme.get("chords") or []) if str(c).strip()]
    chords = (chords + [""] * len(bars))[:len(bars)]
    return bars, chords


def place_main_theme(plan: dict, roster: list, units: Fraction) -> None:
    """Choose where theme A is heard as itself, and set those sections up.

    At the start, at the climax (about two thirds through) and near the end —
    in sections long enough for it and in the home key's mode, so the tune
    needs only to move, not to change. Each gets ``theme`` = {bars, chords} and
    a lead; the first bars' harmony becomes the theme's own.
    """
    bars, chords = main_theme(plan, units)
    sections = plan["_sections"]
    if not bars:
        return
    home = plan["concertKey"]
    n = len(bars)
    fits = [i for i, s in enumerate(sections)
            if s["bars"] >= n and _mode(s["key"]) == _mode(home) and s["texture"] != "solo"]
    if not fits:
        return
    total = sum(s["bars"] for s in sections)
    chosen = [fits[0]]
    if total >= 48:
        climax = min(fits, key=lambda i: abs(sections[i]["start"] - 0.65 * total))
        if climax not in chosen:
            chosen.append(climax)
    late = [i for i in fits if sections[i]["start"] >= 0.7 * total and i not in chosen
            and (i < len(sections) - 1 or sections[i]["bars"] >= n + 2)]
    if late:
        chosen.append(late[-1] if len(late) == 1 else late[0])
    singers = _singers(roster)
    usual = _usual_lead(plan, singers)
    for i in chosen:
        s = sections[i]
        s["main"] = True
        if not s["lead"] or s["lead"] not in {p["partId"] for p in singers}:
            s["lead"] = usual
        if not s["lead"]:
            continue
        shift = compact.tonic_shift(home, s["key"])
        s["theme"] = {"bars": compact.transposed(bars, shift, s["key"]), "length": n}
        theme_chords = [orchestrate.transpose_chord(c, shift, s["key"]) if c else s["chords"][k]
                        for k, c in enumerate(chords)]
        s["chords"] = theme_chords + s["chords"][n:]


def _usual_lead(plan: dict, singers: list):
    """The player who carries the tune most, among those who can sing it."""
    ids = {p["partId"] for p in singers}
    counts = {}
    for s in plan["_sections"]:
        if s["lead"] in ids:
            counts[s["lead"]] = counts.get(s["lead"], 0) + s["bars"]
    if counts:
        return max(counts, key=counts.get)
    return singers[0]["partId"] if singers else None


def theme_for(section: dict, part: dict) -> list:
    """The theme's bars for this section, in this player's singing register — a
    fresh copy, since the engraver goes on to mark and adjust what it is given."""
    return copy.deepcopy(compact.fit_register(section["theme"]["bars"], part))


# ---------------------------------------------------------------------------
# Solos
# ---------------------------------------------------------------------------

def wanted_solos(level: str, measures: int, roster: list) -> int:
    if len(_singers(roster)) < 1 or len(roster) < 2:
        return 0
    return 2 if level in ("higher-intermediate", "pro") and measures >= 64 else 1


def place_solos(plan: dict, roster: list, level: str) -> None:
    """Make sure the piece has its solos, in good places, for singing instruments."""
    sections = plan["_sections"]
    singers = _singers(roster)
    by_id = {p["partId"]: p for p in singers}
    # A solo the plan gave to an instrument that can't sing one goes to one that can.
    soloists = []
    for s in sections:
        if s["texture"] == "solo":
            if s["lead"] not in by_id:
                s["lead"] = next((p["partId"] for p in singers
                                  if p["partId"] not in soloists), None)
            if s["lead"]:
                soloists.append(s["lead"])
            else:
                s["texture"] = "reduced"
    have = sum(1 for s in sections if s["texture"] == "solo")
    want = min(MAX_SOLOS, wanted_solos(level, plan["measureCount"], roster))
    if have >= want:
        return
    low, high = SOLO_BARS
    taken = {i for i, s in enumerate(sections) if s["texture"] == "solo"}
    candidates = [i for i, s in enumerate(sections)
                  if 0 < i < len(sections) - 1 and low <= s["bars"] <= high
                  and not s.get("theme") and s["texture"] != "solo"
                  and not ({i - 1, i + 1} & taken)]
    # Quieter sections first, then the ones nearest the middle of the piece.
    total = plan["measureCount"]
    candidates.sort(key=lambda i: (sections[i]["texture"] == "tutti",
                                   abs(sections[i]["start"] - total / 2)))
    # A solo shows off a voice the main tune doesn't: another instrument first.
    main_lead = _usual_lead(plan, singers)
    main_instrument = by_id[main_lead]["instrument"] if main_lead else None
    order = ([p["partId"] for p in singers if p["instrument"] != main_instrument]
             + [p["partId"] for p in singers if p["instrument"] == main_instrument])
    for i in candidates:
        if have >= want:
            break
        if {i - 1, i + 1} & taken:
            continue
        soloist = next((pid for pid in order if pid not in soloists), order[0] if order else None)
        if not soloist:
            break
        s = sections[i]
        s["texture"], s["lead"], s["accompaniment"] = "solo", soloist, "sustained"
        if s["dynamic"] in ("f", "ff"):
            s["dynamic"] = "mp"
        s["material"] = (s["material"] + " — as a solo for "
                         + by_id[soloist]["label"]).strip(" —")
        soloists.append(soloist)
        taken.add(i)
        have += 1


def solo_faults(part: dict, written: dict, section: dict, units: Fraction,
                beat: Fraction) -> list:
    """What melody.critique finds in a solo call's line for the soloist."""
    import melody
    bars, _ = compact.read_part(written.get("measures") or [], section["bars"], part, units)
    family = options.FAMILY_OF.get(part["instrument"], "")
    return melody.critique(bars, section["start"], family, section["chords"], units, beat,
                           compact.singing_range(part))
