"""Musical form — writing each idea once and building the piece from it.

The model used to write the whole piece out, bar by bar, as a four-voice
chorale, in the one call that cannot be parallelised. That made length the
single most expensive thing a user could ask for, so pieces were capped at
sixteen to thirty-two bars, and the bars it did write were spread thin over a
lot of arithmetic it is bad at.

But music is not written that way either. A piece is a handful of ideas and a
plan for what happens to them: the theme, stated; again, louder, with the tune
in the cellos; a contrasting phrase; the theme a tone higher; a coda built from
its last two bars. So the model now writes exactly that — a few PHRASES, each a
short four-voice chorale, and a FORM that says, section by section, which phrase
plays (or which bars of it), in which key, how loud, who carries the tune, and
how thick the texture is.

Everything else here is arithmetic. Repeating a phrase, moving it to a new key,
handing its melody to a different instrument, thinning the ensemble for a solo,
marking the dynamics in every part — none of that is a musical judgement the
model needs to make bar by bar, and all of it is exact when Python does it. So
a piece can be three times as long as before while the model writes fewer bars
than it used to, and spends its attention on the bars that are actually new.
"""

import re
from fractions import Fraction

import abc_check
import options
import orchestrate

VOICES = ("soprano", "alto", "tenor", "bass")
DYNAMICS = ("pp", "p", "mp", "mf", "f", "ff")
SHAPES = ("steady", "crescendo", "diminuendo")
TEXTURES = ("tutti", "reduced", "solo")

# How far a section may move from home. Further than a fifth either way and a
# transposed phrase drags most instruments to the edge of their range.
MAX_TRANSPOSE = 7

# Dynamics and hairpins inside a model-written phrase. They are taken out
# because that phrase is reused at different levels; the form's dynamic for each
# section is written in instead, the same in every part.
PHRASE_DYNAMIC_RE = re.compile(
    r"!(?:p{1,4}|mp|mf|f{1,4}|crescendo[()]|diminuendo[()]|<[()]|>[()])!\s*")


# ---------------------------------------------------------------------------
# Reading the plan
# ---------------------------------------------------------------------------

def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def read_phrases(plan: dict, units: Fraction) -> dict:
    """{id: {"bars", "chords", "voices": {voice: [bar events]}}}, in concert pitch.

    Every voice is forced to the phrase's bar count and every bar to the right
    length. A chorale bar that is a beat short is held out on its last note
    instead of shifting the rest of the piece out of alignment.
    """
    home = plan.get("concertKey") or "C"
    phrases = {}
    for entry in plan.get("phrases") or []:
        pid = str(entry.get("id") or "").strip()
        if not pid or pid in phrases:
            continue
        read = {voice: orchestrate.read_bars(entry.get(voice) or "", home)
                for voice in VOICES}
        chords = [str(c) for c in entry.get("chords") or []]
        if not read["soprano"] or not read["bass"]:
            # The chorale never came (its call failed or ran out of time). The
            # chords are still known, so voice them here rather than lose the
            # phrase: plainer, but complete and in harmony.
            if not chords:
                continue
            read = voice_chords(chords, units, beat_units(plan.get("timeSignature")))
        bars = len(chords) or max(len(v) for v in read.values())
        chords = (chords + [chords[-1] if chords else ""] * bars)[:bars]

        voices = {}
        for voice in VOICES:
            source = read[voice] or read["soprano" if voice == "alto" else "bass"]
            fitted = [orchestrate.fit_bar(bar, units) for bar in source[:bars]]
            while len(fitted) < bars:
                # Short by a bar or more: hold the last note rather than go silent.
                last = next((m for bar in reversed(fitted) for m, _ in reversed(bar)
                             if m is not None), None)
                fitted.append([(last, units)])
            voices[voice] = fitted
        harmonise(voices, chords, beat_units(plan.get("timeSignature")))
        phrases[pid] = {"bars": bars, "chords": chords, "voices": voices}
    return phrases


# Comfortable four-part ranges, sounding pitch.
VOICE_RANGES = {"soprano": (62, 79), "alto": (55, 74), "tenor": (48, 67), "bass": (38, 57)}


def voice_chords(chords: list, units: Fraction, beat: Fraction = None) -> dict:
    """A plain four-part chorale from chord symbols alone: {voice: [bar events]}.

    The fallback for a phrase the model could not write in time. Bass on the
    root (or the slash bass); the upper voices take the chord's tones, each
    moving to the nearest one available so the lines move by step and hold
    common tones — the textbook way of joining chords. Root, third and fifth
    are all present wherever the chord has them.
    """
    previous = {"soprano": 72, "alto": 67, "tenor": 60, "bass": 48}
    out = {voice: [] for voice in VOICES}
    for symbol in chords:
        bar = {voice: [] for voice in VOICES}
        for chord, start, end in chord_spans(symbol, units, beat or Fraction(2)):
            _voice_one(chord, end - start, previous, bar)
        for voice in VOICES:
            out[voice].append(bar[voice])
    return out


def _voice_one(symbol: str, length: Fraction, previous: dict, out: dict) -> None:
    """Voice one chord for ``length`` units onto the end of each voice's bar."""
    pcs = chord_pcs(symbol)
    match = CHORD_RE.match(symbol or "")
    if not pcs or not match:
        for voice in VOICES:
            out[voice].append((previous[voice], length))
        return
    root = orchestrate.PITCH_OF_KEY.get(match.group(1) + match.group(2), 0)
    bass_pc = orchestrate.PITCH_OF_KEY.get((match.group(4) or "") + (match.group(5) or ""),
                                           root) if match.group(4) else root
    bass = _nearest_pc_in(previous["bass"], bass_pc, *VOICE_RANGES["bass"])

    wanted = [root, (root + 4) % 12 if (root + 4) % 12 in pcs else (root + 3) % 12,
              (root + 7) % 12 if (root + 7) % 12 in pcs else None]
    wanted = [pc for pc in wanted if pc is not None and pc in pcs]
    extra = sorted(pcs - set(wanted) - {bass_pc})
    pool = (wanted + extra + wanted)[:3] if len(wanted) < 3 else wanted[:3]
    if extra and len(wanted) == 3:
        pool = [wanted[0] if wanted[0] != bass_pc else wanted[2], wanted[1], extra[0]]

    chosen, used = {}, set()
    for voice in ("soprano", "alto", "tenor"):
        low, high = VOICE_RANGES[voice]
        options = [pc for pc in pool if pc not in used] or pool
        best = min((_nearest_pc_in(previous[voice], pc, low, high) for pc in options),
                   key=lambda m: abs(m - previous[voice]))
        # Keep the voices in order, top to bottom.
        ceiling = chosen.get("alto" if voice == "tenor" else "soprano")
        if voice != "soprano" and ceiling is not None and best > ceiling:
            best -= 12
        chosen[voice] = best
        used.add(best % 12)
    chosen["bass"] = min(bass, chosen["tenor"] - 3) if bass >= chosen["tenor"] else bass
    for voice in VOICES:
        out[voice].append((chosen[voice], length))
        previous[voice] = chosen[voice]


def _nearest_pc_in(near: int, pc: int, low: int, high: int) -> int:
    candidates = [m for m in range(low, high + 1) if m % 12 == pc] or \
                 [m for m in range(low - 12, high + 13) if m % 12 == pc]
    return min(candidates, key=lambda m: abs(m - near))


CHORD_RE = re.compile(r"\s*([A-G])([#b]?)((?:[^/]|/(?=\d))*)(?:/\s*([A-G])([#b]?))?")


def chord_pcs(symbol: str):
    """'D7/F#' -> {2, 6, 9, 0}: the pitch classes that belong to the chord.

    Covers what a chord grid actually uses — triads, sevenths, sixths, sus,
    diminished, half-diminished, augmented, added and extended tones. Returns
    None for anything it cannot read, and then that bar is left alone.

    A bar holding two chords ('Dm7 G7') gives both chords' tones; use
    chord_pcs_at where the beat is known.
    """
    several = bar_chords(symbol)
    if len(several) > 1:
        sets = [chord_pcs(one) for one in several]
        return set().union(*[pcs for pcs in sets if pcs]) or None
    match = CHORD_RE.match(str(symbol or ""))
    if not match:
        return None
    root = orchestrate.PITCH_OF_KEY.get(match.group(1) + match.group(2))
    if root is None:
        return None
    q = match.group(3).strip()
    low = q.lower()
    minor = (q.startswith("m") and not q.startswith("maj")) or low.startswith("min")
    third = 3 if minor or "dim" in low or "°" in q or "ø" in q else 4
    if "sus2" in low:
        third = 2
    elif "sus" in low:
        third = 5
    fifth = 6 if ("dim" in low or "°" in q or "ø" in q or "b5" in low) else \
        8 if ("aug" in low or "+" in q or "#5" in low) else 7
    tones = {0, third, fifth}
    plain = lambda n: re.search(rf"(?<![#b\d]){n}(?!\d)", q) is not None
    six_nine = "6/9" in q or "69" in q
    extended = (plain("9") or plain("11") or plain("13")) and "add" not in low
    if "maj7" in low or "M7" in q or "Δ" in q or "maj9" in low or "maj13" in low:
        tones.add(11)
    elif "dim7" in low or "°7" in q:
        tones.add(9)
    elif "7" in q or "ø" in q or (extended and not six_nine) or \
            any(alt in q for alt in ("b9", "#9", "#11", "b13")):
        tones.add(10)
    if plain("6") or six_nine:
        tones.add(9)
    if plain("9") or plain("11") or plain("13") or "add2" in low:
        tones.add(2)
    if plain("11") or "add4" in low:
        tones.add(5)
    if plain("13"):
        tones.add(9)
    for text, interval in (("b9", 1), ("#9", 3), ("#11", 6), ("b13", 8)):
        if text in q:
            tones.add(interval)
    pcs = {(root + t) % 12 for t in tones}
    if match.group(4):
        bass = orchestrate.PITCH_OF_KEY.get(match.group(4) + (match.group(5) or ""))
        if bass is not None:
            pcs.add(bass)
    return pcs


# A bar of the chord grid holds one chord, or two separated by a space —
# 'Dm7 G7' — which share the bar beat by beat. Harmony that changes at the same
# rate all the way through is what made the music sound mechanical, so the grid
# is allowed to hold a chord for two bars, or change twice in one approaching a
# cadence.
MAX_CHORDS_PER_BAR = 2


def bar_chords(symbol) -> list:
    """'Dm7 G7' -> ['Dm7', 'G7']; 'C' -> ['C']; '' -> ['']."""
    found = [c for c in re.split(r"[\s,]+", str(symbol or "").strip()) if c]
    return found[:MAX_CHORDS_PER_BAR] or [""]


def chord_spans(symbol, units: Fraction, beat: Fraction) -> list:
    """[(chord, start, end)] across one bar, in units, split on a beat.

    Two chords in 4/4 take two beats each; in 3/4 the first takes two beats and
    the second one, which is how a cadence in triple time is usually felt.
    """
    chords = bar_chords(symbol)
    if len(chords) == 1:
        return [(chords[0], Fraction(0), units)]
    beats = max(1, int(units / beat)) if beat else 1
    split = Fraction(-(-beats // 2)) * beat if beats > 1 else units / 2
    return [(chords[0], Fraction(0), split), (chords[1], split, units)]


def chord_at(symbol, position: Fraction, units: Fraction, beat: Fraction) -> str:
    for chord, start, end in chord_spans(symbol, units, beat):
        if start <= position < end:
            return chord
    return bar_chords(symbol)[-1]


def chord_pcs_at(symbol, position: Fraction, units: Fraction, beat: Fraction):
    return chord_pcs(chord_at(symbol, position, units, beat))


def _nearest_in(midi: int, pcs: set) -> int:
    for step in range(1, 7):
        for candidate in (midi - step, midi + step):
            if candidate % 12 in pcs:
                return candidate
    return midi


def harmonise(voices: dict, chords: list, beat: Fraction) -> int:
    """Put every lower-voice note that lands on a beat onto a tone of its chord.

    The chorale is the whole ensemble's harmony — every derived part doubles
    one of its lines — so a wrong note in it is a wrong note in a dozen parts.
    The model is good at four-part writing and not perfect at it, and the slip
    it makes is an inner voice a step off the chord on a beat. That is fixed
    here, exactly, by moving the note to the nearest chord tone. Off-beat notes
    are left as they are: those are passing notes, and they are the line's
    character.

    The soprano is the tune, so it is held to a looser rule: a quick passing or
    neighbour note on a beat is melody, but a note HELD against the chord — on
    the downbeat for a beat or more, or anywhere for two beats or more — is a
    sustained clash that every instrument doubling the tune would play. That
    note is moved to the nearest chord tone too.
    Returns how many notes were moved.
    """
    moved = 0
    for index, symbol in enumerate(chords):
        if not chord_pcs(symbol):
            continue
        for voice in VOICES:
            bar, position, fixed = voices[voice][index], Fraction(0), []
            units = sum((dur for _, dur in bar), Fraction(0))
            for midi, dur in bar:
                pcs = chord_pcs_at(symbol, position, units, beat) or chord_pcs(symbol)
                on_beat = position % beat == 0
                if voice == "soprano":
                    on_beat = on_beat and dur >= beat
                if midi is not None and on_beat and midi % 12 not in pcs:
                    midi = _nearest_in(midi, pcs)
                    moved += 1
                fixed.append((midi, dur))
                position += dur
            voices[voice][index] = fixed
    return moved


def read_form(plan: dict, phrases: dict, roster: list, level: str) -> list:
    """The sections in order, with measure numbers worked out here.

    Anything the plan gets wrong is corrected rather than trusted: an unknown
    phrase is skipped, a bar range is clamped to the phrase, and a beginner
    piece never changes key — a key change mid-piece is exactly the thing that
    group cannot read yet.
    """
    part_ids = {part["partId"] for part in roster}
    sections, measure = [], 1
    for entry in plan.get("form") or []:
        phrase = phrases.get(str(entry.get("phrase") or "").strip())
        if not phrase:
            continue
        pid = str(entry.get("phrase")).strip()
        first = _clamp(entry.get("fromBar") or 1, 1, phrase["bars"], 1)
        last = _clamp(entry.get("toBar") or phrase["bars"], first, phrase["bars"],
                      phrase["bars"])
        transpose = 0 if level == "beginner" or entry is plan["form"][-1] else \
            _clamp(entry.get("transpose"), -MAX_TRANSPOSE, MAX_TRANSPOSE, 0)
        texture = entry.get("texture") if entry.get("texture") in TEXTURES else "tutti"
        lead = entry.get("lead") if entry.get("lead") in part_ids else None
        if texture == "solo" and not lead:
            texture = "reduced"
        bars = last - first + 1
        sections.append({
            "phrase": pid,
            "from": first - 1, "to": last,            # python slice into the phrase
            "bars": bars,
            "start": measure,
            "end": measure + bars - 1,
            "transpose": transpose,
            "key": orchestrate.shift_key(plan.get("concertKey") or "C", transpose),
            "dynamic": entry.get("dynamic") if entry.get("dynamic") in DYNAMICS else "mf",
            "shape": entry.get("shape") if entry.get("shape") in SHAPES else "steady",
            "texture": texture,
            "lead": lead,
            "label": str(entry.get("label") or pid).strip()[:24] or pid,
            "description": str(entry.get("description") or ""),
        })
        measure += bars

    if not sections:
        # No usable form: play every phrase once, in the order written.
        for pid, phrase in phrases.items():
            sections.append({"phrase": pid, "from": 0, "to": phrase["bars"],
                             "bars": phrase["bars"], "start": measure,
                             "end": measure + phrase["bars"] - 1, "transpose": 0,
                             "key": plan.get("concertKey") or "C", "dynamic": "mf",
                             "shape": "steady", "texture": "tutti", "lead": None,
                             "label": pid, "description": ""})
            measure += phrase["bars"]
    return sections


def summarise(sections: list, phrases: dict) -> dict:
    """The plan fields the rest of the pipeline reads, for the whole piece."""
    harmony, measure = [], 1
    for s in sections:
        for chord in phrases[s["phrase"]]["chords"][s["from"]:s["to"]]:
            harmony.append({"measure": measure,
                            "chord": orchestrate.transpose_chord(chord, s["transpose"], s["key"])})
            measure += 1
    return {
        "measureCount": sum(s["bars"] for s in sections),
        "harmony": harmony,
        "sections": [{"name": s["label"], "fromMeasure": s["start"], "toMeasure": s["end"],
                      "description": s["description"]} for s in sections],
        "dynamicPlan": [{"fromMeasure": s["start"], "toMeasure": s["end"],
                         "level": s["dynamic"], "shape": s["shape"]} for s in sections],
        "melodyLead": [{"fromMeasure": s["start"], "toMeasure": s["end"],
                        "partId": s["lead"], "what": s["description"]}
                       for s in sections if s["lead"]],
        "solos": [{"partId": s["lead"], "fromMeasure": s["start"], "toMeasure": s["end"],
                   "what": s["description"]} for s in sections if s["texture"] == "solo"],
    }


# ---------------------------------------------------------------------------
# Who plays in each section
# ---------------------------------------------------------------------------

def roles(section: dict, roster: list, assigned: dict, written_ids: set) -> dict:
    """{partId: 'lead' | 'play' | 'sustain' | 'rest'} for one section.

    tutti   — everyone.
    reduced — the lead, the model-written lines, and one player per voice;
              doublings drop out, so the repeat of a phrase sounds lighter.
    solo    — the soloist alone over a held bass and one held inner voice. A
              solo the whole orchestra plays through is not a solo.
    """
    lead, texture = section["lead"], section["texture"]
    out = {}
    if texture == "tutti":
        for part in roster:
            out[part["partId"]] = "lead" if part["partId"] == lead else "play"
        return out
    timpani = {p["partId"] for p in roster if p["instrument"] == "Timpani"}

    taken = set()
    for part in roster:
        pid = part["partId"]
        voice = (assigned.get(pid) or {}).get("voice")
        if pid == lead and pid not in timpani:
            out[pid] = "lead"
        elif pid in timpani:
            out[pid] = "rest"                      # timpani are for the tuttis
        elif texture == "reduced":
            if pid in written_ids or not part["pitched"]:
                out[pid] = "play"
            elif voice not in taken:
                taken.add(voice)
                out[pid] = "play"
            else:
                out[pid] = "rest"
        else:                                                   # solo
            if pid in written_ids or not part["pitched"]:
                out[pid] = "rest"
            elif voice == "bass" and "bass" not in taken:
                taken.add("bass")
                out[pid] = "sustain"
            elif voice in ("alto", "tenor") and "inner" not in taken:
                taken.add("inner")
                out[pid] = "sustain"
            else:
                out[pid] = "rest"
    return out


# ---------------------------------------------------------------------------
# Building one part
# ---------------------------------------------------------------------------

def _section_events(phrase: dict, voice: str, section: dict) -> list:
    bars = phrase["voices"][voice][section["from"]:section["to"]]
    return [event for bar in bars for event in bar]


def _best_shift(events: list, low, high, preferred: int) -> int:
    """The octave nearest ``preferred`` that keeps the most notes playable."""
    if low is None:
        return preferred
    options = sorted((preferred, preferred - 1, preferred + 1),
                     key=lambda s: (orchestrate.out_of_range(
                         orchestrate.transpose(events, 12 * s), low, high), s != preferred))
    return options[0]


def _decorate(bars: list, section: dict, entering: bool, previous_dynamic) -> list:
    """Write the section's dynamic, and its hairpin, into one part's bars."""
    if not bars:
        return bars
    bars = list(bars)
    marks = []
    if entering or section["dynamic"] != previous_dynamic:
        marks.append(f"!{section['dynamic']}!")
    if section["shape"] != "steady" and section["bars"] > 1:
        marks.append(f"!{section['shape']}(!")
        bars[-1] = _before_last_note(bars[-1], f"!{section['shape']})!")
    if marks:
        bars[0] = _after_fields(bars[0], " ".join(marks))
    return bars


INLINE_AT_START_RE = re.compile(r"^(\s*\[[A-Za-z]:[^\]]*\]\s*)*")
TOKEN_START_RE = re.compile(r"[\^_=]*[A-Ga-gzx]|\[(?![A-Za-z]:)")


def _after_fields(bar: str, text: str) -> str:
    head = INLINE_AT_START_RE.match(bar).group(0)
    return head + text + " " + bar[len(head):].lstrip()


def _before_last_note(bar: str, text: str) -> str:
    """Insert ``text`` in front of the last note, rest or chord in the bar."""
    last, i, n = None, 0, len(bar)
    while i < n:
        ch = bar[i]
        if ch in "!\"":
            j = bar.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(bar, i):
            j = bar.find("]", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "[":
            last = i
            j = bar.find("]", i)
            i = n if j < 0 else j + 1
            continue
        if TOKEN_START_RE.match(bar, i):
            last = i
            i = TOKEN_START_RE.match(bar, i).end()
            continue
        i += 1
    if last is None:
        return bar + " " + text
    # Keep any accidental attached: back up over ^ _ = that belong to the note.
    while last > 0 and bar[last - 1] in "^_=":
        last -= 1
    return bar[:last] + text + bar[last:]


def percussion_bar(units: Fraction) -> str:
    """A plain pulse: low sound on the downbeat, high on the other beats."""
    beat = Fraction(3) if units % 3 == 0 and units > 4 else Fraction(2)
    tokens, filled = [], Fraction(0)
    while filled < units:
        step = min(beat, units - filled)
        tokens.append(("c" if filled == 0 else "e") + orchestrate._duration(step))
        filled += step
    return " ".join(tokens)


def realise(plan: dict, roster: list, sections: list, phrases: dict, units: Fraction,
            phrase_text: dict) -> list:
    """Every part, full length, built from the phrases and the form.

    ``phrase_text`` is {partId: {phraseId: {"abc", "abcLower"}}} — the lines the
    model decorated, in that part's written pitch in the home key. Every other
    part is derived from the skeleton, and a phrase the model did not deliver
    cleanly falls back to the skeleton too, so no part is ever left short.

    Returns the written parts in the shape the rest of the pipeline uses:
    [{partId, abcKey, abc, abcLower, performanceNote}].
    """
    home = plan.get("concertKey") or "C"
    assigned = {entry["partId"]: entry for entry in plan.get("partPlan") or []}
    written_ids = set(phrase_text)
    section_roles = [roles(s, roster, assigned, written_ids) for s in sections]
    rest = orchestrate.rest_bar(units)
    context = {"written": written_ids,
               "level": plan.get("_level") or "intermediate",
               "beat": beat_units(plan.get("timeSignature")),
               "bpm": plan.get("tempoBpm") or 100}

    out = []
    for part in roster:
        pid = part["partId"]
        spec = assigned.get(pid) or {}
        voice = spec.get("voice") if spec.get("voice") in VOICES else \
            ("bass" if part["clef"] == "bass" else "alto")
        treatment = spec.get("treatment") or "plain"
        transpose = int(part["transpose"])
        low, high = orchestrate.range_of(part)
        home_written = written_key_for(part, home)
        mine = phrase_text.get(pid) or {}

        # One octave for the whole part, chosen from its own voice across the
        # piece, so the line does not jump about between sections.
        own = [e for s, r in zip(sections, section_roles) if r[pid] != "rest"
               for e in _section_events(phrases[s["phrase"]], voice, s)]
        base = orchestrate.fit_octave(orchestrate.transpose(own, -transpose), part,
                                      context["level"]) if part["pitched"] else 0

        upper, lower = [], []
        current_key, last_dynamic, was_resting = home_written, None, True
        for index, (s, r) in enumerate(zip(sections, section_roles)):
            role = r[pid]
            key = written_key_for(part, s["key"])
            change = key != current_key
            current_key = key

            if role == "rest":
                bars_up = [rest] * s["bars"]
                bars_low = [rest] * s["bars"]
            else:
                own_text = mine.get(index)
                # A keyboard inside an orchestra plays the section as though it
                # were two steps quieter: lighter chords, no hammered octaves.
                # Its printed dynamics are lowered to match in finish_parts.
                heard = {**s, "dynamic": SOFTER[s["dynamic"]]} \
                    if accompanies(part, roster) else s
                bars_up, bars_low = _play(part, heard, role, voice, treatment, phrases,
                                          own_text, base, home_written,
                                          key, units, low, high, context)
                if own_text and own_text.get("abc"):
                    # Written for this section: its own dynamics and hairpins stand,
                    # and the section's level is added only if it gave none.
                    bars_up = _level_if_missing(bars_up, s, was_resting, last_dynamic)
                else:
                    bars_up = _decorate(bars_up, s, was_resting, last_dynamic)
                    if bars_low:
                        bars_low = _decorate(bars_low, s, was_resting, last_dynamic)
                last_dynamic = s["dynamic"]
            was_resting = role == "rest"

            if change:
                bars_up[0] = f"[K:{key}] " + bars_up[0]
                if bars_low:
                    bars_low[0] = f"[K:{key}] " + bars_low[0]
            # A double bar where a new section of the form begins.
            joiner = " || " if index and s["label"] != sections[index - 1]["label"] else " | "
            upper.append((joiner, bars_up))
            lower.append((joiner, bars_low or [rest] * s["bars"]))

        note = (next((t.get("performanceNote") for t in mine.values()
                      if t.get("performanceNote")), "") or spec.get("role")
                or f"Plays the {voice} line of the harmony.")
        out.append({
            "partId": pid,
            "abcKey": home_written,
            "abc": _join(upper),
            "abcLower": _join(lower) if part["staves"] == 2 else None,
            "performanceNote": note,
        })
    return out


def written_key_for(part: dict, concert_key: str) -> str:
    if not part["pitched"]:
        return "C"
    return orchestrate.written_key(concert_key, int(part["transpose"]))


def _join(sections: list) -> str:
    text = ""
    for index, (joiner, bars) in enumerate(sections):
        text += ("" if index == 0 else joiner) + " | ".join(bars)
    return text + " |]"


def _play(part, section, role, voice, treatment, phrases, decorated, base,
          home_written, key, units, low, high, context) -> tuple:
    """(upper bars, lower bars or []) for a part that is playing this section."""
    phrase = phrases[section["phrase"]]
    span = slice(section["from"], section["to"])

    if decorated and decorated.get("abc"):
        # Written for this very section, already in its key: used as it is.
        return orchestrate.split_bars(decorated["abc"]), []

    if not part["pitched"]:
        return [percussion_bar(units)] * section["bars"], []

    if part["instrument"] == "Timpani":
        return _timpani(phrase, section, part, key, units), []

    if part["staves"] == 2:
        return keyboard(phrase, section, part, role, key, units, context["level"],
                        context["beat"], context["bpm"])

    line = "soprano" if role == "lead" else guide_voice(section, part["partId"], voice,
                                                         context.get("written", ()))
    sustained = role == "sustain" or (role != "lead" and treatment == "sustained")
    upper = _derive(phrase, line, section, part, base if line == voice else None,
                    key, units, sustained, level=context["level"])
    lower = []
    if part["staves"] == 2:
        # A grand staff gets the bass in the left hand rather than a copy of
        # the right, which is what a pianist reading from a chorale would do.
        lower = _derive(phrase, "bass", section, part, None, key, units, sustained,
                        left_hand=(36, 60))
    return upper, lower


def _timpani(phrase, section, part, key, units) -> list:
    """The bass note struck on each downbeat, as a quarter, then silence.

    Timpani do not play lines. They mark the harmony's foundation at the
    arrivals, which is what makes a tutti sound like an orchestra.
    """
    low, high = orchestrate.range_of(part)
    events = []
    for bar in phrase["voices"]["bass"][section["from"]:section["to"]]:
        root = bar[0][0] if bar and bar[0][0] is not None else None
        if root is None:
            events.append((None, units))
            continue
        root += section["transpose"]
        while low is not None and root < low:
            root += 12
        while high is not None and root > high:
            root -= 12
        events += [(root, Fraction(2)), (None, units - 2)]
    return orchestrate.render_bars(events, key, units)


def _derive(phrase, voice, section, part, base, key, units, sustained,
            left_hand=None, level="intermediate") -> list:
    """One voice of one section, at this player's written pitch and octave.

    ``left_hand`` is a (low, high) range for a piano's lower stave, which reads
    the bass line in its own register rather than the instrument's whole range.
    """
    events = orchestrate.transpose(_section_events(phrase, voice, section),
                                   section["transpose"] - int(part["transpose"]))
    low, high = left_hand or orchestrate.range_of(part)
    if base is None:
        base = orchestrate.octave_shift_for(events, low, high) if left_hand \
            else orchestrate.fit_octave(events, part, level)
    shift = _best_shift(events, low, high, base)
    events = orchestrate.transpose(events, 12 * shift)
    if sustained:
        events = orchestrate.merge_repeats(events)
    return orchestrate.render_bars(events, key, units)


def _move_text(text, part, section, home_written, key, low, high, span) -> list:
    """A model-written phrase, cut to the section and moved into its key."""
    bars = [PHRASE_DYNAMIC_RE.sub("", bar).strip() or "z"
            for bar in orchestrate.split_bars(text)][span]
    if not part["pitched"] or not section["transpose"]:
        return bars
    semitones = section["transpose"]
    if low is not None:
        # The same octave rule as the derived parts: move up or down a whole
        # octave if that keeps more of the line playable.
        events = [e for bar in bars for e in orchestrate.parse_line(bar, home_written)]
        semitones += 12 * _best_shift(orchestrate.transpose(events, semitones),
                                      low, high, 0)
    return [orchestrate.transpose_abc(bar, semitones, home_written, key) for bar in bars]


# ---------------------------------------------------------------------------
# What the model is asked to decorate
# ---------------------------------------------------------------------------

def phrase_line(plan: dict, phrases: dict, part: dict, voice: str, phrase_id: str,
                units: Fraction) -> str:
    """One phrase of a part's skeleton voice, in its written pitch, home key."""
    home = plan.get("concertKey") or "C"
    phrase = phrases[phrase_id]
    whole = {"from": 0, "to": phrase["bars"], "transpose": 0}
    events = orchestrate.transpose(_section_events(phrase, voice, whole),
                                   -int(part["transpose"]))
    events = orchestrate.transpose(
        events, 12 * orchestrate.fit_octave(events, part, plan.get("_level") or "intermediate"))
    return " | ".join(orchestrate.render_bars(events, written_key_for(part, home), units)) + " |"


def check_phrase(text: str, bars: int, units: Fraction, label: str) -> list:
    """Defects in one model-written phrase: wrong bar count, or bars that don't add up."""
    split = orchestrate.split_bars(text or "")
    problems = []
    if len(split) != bars:
        problems.append(f"{label} has {len(split)} bars; it must have exactly {bars}.")
    for index, bar in enumerate(split, start=1):
        lengths = abc_check.bar_lengths(bar)
        length = sum((b.length for b in lengths if b.length > 0), Fraction(0))
        if any(b.uncertain for b in lengths):
            continue
        if length != units:
            problems.append(f"{label} bar {index} is {abc_check._fmt(length)} units long, "
                            f"needs {abc_check._fmt(units)}")
    return problems


# ---------------------------------------------------------------------------
# Keeping the ensemble in harmony
# ---------------------------------------------------------------------------

# A hand-written line may be off the chord on this share of its strong beats —
# an appoggiatura, a suspension — and no more. Past that it is not decorating
# the harmony, it is fighting it, and it is replaced by its chorale line.
MAX_CLASH_SHARE = 0.2

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def beat_units(time_signature: str) -> Fraction:
    """Length of one beat in eighths: a quaver-based compound meter beats in threes."""
    top, _, bottom = str(time_signature or "4/4").partition("/")
    try:
        top, bottom = int(top), int(bottom)
    except ValueError:
        return Fraction(2)
    if bottom == 8 and top % 3 == 0:
        return Fraction(3)
    return Fraction(8, bottom)


def _sounding_at(bar: list, at: Fraction):
    position = Fraction(0)
    for midi, dur in bar:
        if position <= at < position + dur:
            return midi
        position += dur
    return None


def harmony_clashes(text: str, part: dict, phrase: dict, home_written: str,
                    beat: Fraction, label: str) -> list:
    """Where a hand-written line is not on a note of the chorale.

    Checked against all four chorale voices sounding at that moment, in concert
    pitch — so a violin is free to take the alto's note instead of its own, but
    not to sit a semitone off everyone. Two rules:

    * a HELD clash — off the chord on the downbeat for a beat or more, or
      anywhere for two beats or more — is never allowed: every bar of every
      section that phrase is heard in would sour;
    * quick dissonances on a beat (appoggiaturas, accented passing notes) are
      allowed up to MAX_CLASH_SHARE of the beats, and no further.

    Returns [] or one defect string.
    """
    if not part["pitched"] or not text:
        return []
    bars = orchestrate.read_bars(text, home_written)
    chord = phrase["voices"]
    bars = [[(max(m) if isinstance(m, tuple) else m, d) for m, d in bar] for bar in bars]
    checked, off, held, first = 0, 0, None, None

    symbols = phrase.get("chords") or []

    def chorale_at(index, at):
        # The chord is what the four voices sound plus what its symbol names:
        # the keyboard plays the symbol's ninths and sixths even where the
        # chorale voices leave them out.
        pcs = {m % 12 for m in (_sounding_at(chord[v][index], at) for v in VOICES)
               if m is not None}
        units = sum((d for _, d in bars[index]), Fraction(0)) if index < len(bars) else 0
        named = chord_pcs_at(symbols[index], at, units, beat) if index < len(symbols) \
            else None
        return pcs | (named or set())

    for index, bar in enumerate(bars[:phrase["bars"]]):
        position = Fraction(0)
        for midi, dur in bar:
            # Figuration shorter than half a beat — a run that starts on a
            # passing note — is not heard as harmony and is not held to it.
            if midi is not None and position % beat == 0 and dur * 2 >= beat:
                pcs = chorale_at(index, position)
                sounding = (midi + int(part["transpose"])) % 12
                if pcs:
                    checked += 1
                    if sounding not in pcs:
                        off += 1
                        where = (f"bar {index + 1} beat {int(position / beat) + 1}: "
                                 f"{NOTE_NAMES[sounding]} against "
                                 + "-".join(NOTE_NAMES[p] for p in sorted(pcs)))
                        first = first or where
                        if held is None and (dur >= 2 * beat or (position == 0 and dur >= beat)):
                            held = where
            position += dur
    if held:
        return [f"{label} clashes with the harmony: a note is held against the chord "
                f"at {held}. Every held note must be a note of the chord."]
    if checked and off / checked > MAX_CLASH_SHARE:
        return [f"{label} clashes with the harmony on {off} of {checked} beats "
                f"(first at {first}). Be on a note of the chord on every beat."]
    return []


REGISTER = {"soprano": 72, "alto": 65, "tenor": 57, "bass": 45}


def _sounding_middle(part: dict) -> float:
    low, high = orchestrate.range_of(part, "easyRange")
    if low is None:
        low, high = orchestrate.range_of(part)
    if low is None:
        return 60
    return (low + high) / 2 + int(part["transpose"])


def balance_voices(plan: dict, roster: list) -> None:
    """Make sure the four chorale voices are all actually played.

    The plan chooses who plays which line, and left alone it will happily put
    half the orchestra on the tune and nobody on the bass — at which point the
    chorale never sounds as a chord, however well it was written. So: the
    lowest instrument plays the bass, and any voice nobody was given takes a
    player from the most crowded line, the one whose register suits it best.
    """
    # Keyboards play the whole harmony and are left out of the line-counting;
    # timpani only strike the bass and cannot carry a line either.
    pitched = [part for part in roster if part["pitched"] and part["staves"] == 1
               and part["instrument"] != "Timpani"]
    entries = {entry.get("partId"): entry for entry in plan.get("partPlan") or []}
    for part in roster:
        if part["staves"] == 2 or part["instrument"] == "Timpani":
            entries.setdefault(part["partId"], {"partId": part["partId"], "voice": "bass",
                                                "treatment": "plain", "role": ""})
    if not pitched:
        plan["partPlan"] = [entries[p["partId"]] for p in roster if p["partId"] in entries]
        return
    for part in pitched:
        entry = entries.setdefault(part["partId"], {"partId": part["partId"],
                                                    "treatment": "plain", "role": ""})
        if entry.get("voice") not in VOICES:
            entry["voice"] = min(VOICES, key=lambda v: abs(REGISTER[v] - _sounding_middle(part)))

    def players(voice):
        return [p for p in pitched if entries[p["partId"]]["voice"] == voice]

    def natural(part):
        return min(VOICES, key=lambda v: abs(REGISTER[v] - _sounding_middle(part)))

    # No instrument more than one line away from its own register: a tuba does
    # not play the tune, a piccolo does not play the bass. (A section's lead
    # still takes the tune whatever its line — that is a solo, not a doubling.)
    for part in pitched:
        entry = entries[part["partId"]]
        if abs(VOICES.index(entry["voice"]) - VOICES.index(natural(part))) >= 2:
            entry["voice"] = natural(part)

    lowest = min(pitched, key=_sounding_middle)
    if len(pitched) > 1 and entries[lowest["partId"]]["voice"] != "bass":
        entries[lowest["partId"]]["voice"] = "bass"

    # No line takes more than half the ensemble: a chord needs all four voices
    # heard, and twelve players on the tune drown three on the harmony. The
    # plain players whose register sits furthest from the crowded line move to
    # the neighbouring line nearest their register.
    if len(pitched) >= 4:
        cap = max(1, len(pitched) // 2)
        for voice in VOICES:
            crowd = [p for p in players(voice)
                     if entries[p["partId"]].get("treatment") != "decorated"]
            crowd.sort(key=lambda p: -abs(REGISTER[voice] - _sounding_middle(p)))
            i = VOICES.index(voice)
            neighbours = [VOICES[j] for j in (i - 1, i + 1) if 0 <= j < len(VOICES)]
            while len(players(voice)) > cap and crowd:
                part = crowd.pop(0)
                entries[part["partId"]]["voice"] = min(
                    neighbours, key=lambda v: abs(REGISTER[v] - _sounding_middle(part)))

    # And no harmony line left to a single player in a big ensemble: each line
    # gets at least a sixth of it, borrowed from the busier neighbouring line,
    # taking the players whose register suits the thin line best.
    if len(pitched) >= 8:
        floor = len(pitched) // 6
        for voice in VOICES:
            i = VOICES.index(voice)
            while len(players(voice)) < floor:
                neighbours = [VOICES[j] for j in (i - 1, i + 1) if 0 <= j < len(VOICES)]
                donor = max(neighbours, key=lambda v: len(players(v)))
                if len(players(donor)) <= floor:
                    break
                movable = [p for p in players(donor)
                           if entries[p["partId"]].get("treatment") != "decorated"
                           and p is not lowest]
                if not movable:
                    break
                part = min(movable, key=lambda p: abs(REGISTER[voice] - _sounding_middle(p)))
                entries[part["partId"]]["voice"] = voice

    order = ["bass", "soprano", "tenor", "alto"][:len(pitched)]
    for voice in order:
        if players(voice):
            continue
        donors = [p for p in pitched if len(players(entries[p["partId"]]["voice"])) > 1]
        if not donors:
            continue
        donor = min(donors, key=lambda p: (entries[p["partId"]].get("treatment") == "decorated",
                                           abs(REGISTER[voice] - _sounding_middle(p))))
        entries[donor["partId"]]["voice"] = voice
    plan["partPlan"] = [entries[part["partId"]] for part in roster if part["partId"] in entries]


def assign_parts(plan: dict, roster: list, sections: list) -> None:
    """Who plays which line, worked out here rather than asked for.

    Each instrument takes the chorale line nearest its own register. The
    treble instruments the form gives the tune to are 'decorated' — they write
    their own line around the melody — and play the soprano voice; horns hold
    the inner harmony ('sustained', as horns do); everyone else plays their
    line as written. balance_voices() then makes sure all four lines are heard.
    """
    leads = []
    for s in sections:
        if s["lead"] and s["lead"] not in leads:
            leads.append(s["lead"])
    entries = []
    for part in roster:
        middle = _sounding_middle(part)
        voice = min(VOICES, key=lambda v: abs(REGISTER[v] - middle))
        treatment = "plain"
        if (part["partId"] in leads and part["pitched"] and part["staves"] == 1
                and part["instrument"] != "Timpani" and voice in ("soprano", "alto")):
            voice, treatment = "soprano", "decorated"
        elif part["instrument"] == "French horn" and voice in ("alto", "tenor"):
            treatment = "sustained"
        led = [f"mm.{s['start']}-{s['end']}" for s in sections if s["lead"] == part["partId"]]
        role = f"Plays the {voice} line of the harmony"
        role += f"; carries the tune in {', '.join(led)}." if led else "."
        entries.append({"partId": part["partId"], "voice": voice,
                        "treatment": treatment, "role": role})
    plan["partPlan"] = entries


# ---------------------------------------------------------------------------
# Keyboards: the whole harmony, written for two hands
# ---------------------------------------------------------------------------
#
# A piano doubling one chorale line is not a piano part. In a duo it was the
# only harmony instrument, and handing it a single line left the violin over
# a bare bass — no chords at all. A keyboard plays the whole harmony: the bass
# in the left hand, the chord in the right, laid out the way pianists actually
# write it — broken chords when it is quiet, octaves and full chords when it is
# loud, the tune on top with chord tones under it when it leads.
#
# Every pitch is taken from the chord sounding at that beat, so however busy
# the figuration gets, it cannot disagree with anyone else in the ensemble.

LH_RANGE = (31, 57)        # G1 .. A3
RH_RANGE = (57, 86)        # A3 .. D6


def _subdivisions(level: str, beat: Fraction, bpm: int, loud: bool) -> int:
    """Notes per beat for broken-chord figuration."""
    if level in ("beginner",):
        return 1
    if level == "intermediate":
        return 2 if beat == 2 else 3
    fast = (bpm or 100) > (132 if beat == 2 else 100)
    if level == "pro" and not fast:
        return 4 if beat == 2 else 6          # sixteenths
    return 2 if beat == 2 else 3              # eighths


def _beat_chords(phrase: dict, section: dict, beat: Fraction, units: Fraction,
                 shift: int) -> list:
    """[(bar index, beat position, pitch classes, bass, soprano)] for a section."""
    out = []
    for index in range(section["from"], section["to"]):
        symbol = phrase["chords"][index] if index < len(phrase["chords"]) else ""
        position = Fraction(0)
        while position < units:
            symbol_pcs = chord_pcs_at(symbol, position, units, beat)
            sounding = {v: _sounding_at(phrase["voices"][v][index], position) for v in VOICES}
            move = shift + section["transpose"]
            voice_pcs = {(m + move) % 12 for m in sounding.values() if m is not None}
            pcs = {(p + section["transpose"]) % 12 for p in symbol_pcs} if symbol_pcs \
                else voice_pcs
            bass = sounding["bass"]
            top = sounding["soprano"]
            out.append((index, position, pcs or voice_pcs,
                        None if bass is None else bass + move,
                        None if top is None else top + move))
            position += beat
    return out


def _into(midi: int, low: int, high: int) -> int:
    while midi < low:
        midi += 12
    while midi > high:
        midi -= 12
    return midi


def _spaced_tones(bass: int, pcs: set, span: int = 22) -> list:
    """Chord tones rising from the bass, at least a minor third apart."""
    tones = [bass]
    for m in range(bass + 1, bass + span):
        if m % 12 in pcs and m - tones[-1] >= 3:
            tones.append(m)
    return tones


def _rh_chord(pcs: set, near_top: int, size: int, ceiling: int = None) -> tuple:
    """A close-position chord whose top note is the chord tone nearest ``near_top``."""
    top = min((m for m in range(RH_RANGE[0] + 7, RH_RANGE[1] + 1) if m % 12 in pcs
               and (ceiling is None or m <= ceiling)),
              key=lambda m: abs(m - near_top), default=None)
    if top is None:
        return ()
    notes = [top]
    m = top - 1
    while len(notes) < size and m >= RH_RANGE[0]:
        if m % 12 in pcs and m % 12 not in {n % 12 for n in notes}:
            notes.append(m)
        m -= 1
    return tuple(sorted(notes))


def keyboard(phrase: dict, section: dict, part: dict, role: str, key: str,
             units: Fraction, level: str, beat: Fraction, bpm: int) -> tuple:
    """(right hand bars, left hand bars) for a keyboard in one section."""
    shift = -int(part["transpose"])
    loud = section["dynamic"] in ("f", "ff")
    beats = _beat_chords(phrase, section, beat, units, shift)
    sub = _subdivisions(level, beat, bpm, loud)
    advanced = level in ("higher-intermediate", "pro")

    right, left = [], []
    previous_top = 76
    for bar_number in range(section["from"], section["to"]):
        rh, lh = [], []
        bar_beats = [b for b in beats if b[0] == bar_number]
        for _, position, pcs, bass, top in bar_beats:
            length = min(beat, units - position)
            if bass is None:
                lh.append((None, length))
            else:
                low = _into(bass, *LH_RANGE)
                if role == "sustain":
                    lh.append((low, length))
                elif loud and advanced:
                    # Octaves in the bass: the sound of a full keyboard tutti.
                    octave = (low - 12, low) if low - 12 >= 24 else (low, low + 12)
                    lh.append((octave, length))
                elif sub > 1:
                    tones = _spaced_tones(low, pcs)
                    shape = list(range(len(tones))) + list(range(len(tones) - 2, 0, -1))
                    step = length / sub
                    for i in range(sub):
                        lh.append((tones[shape[i % len(shape)]], step))
                else:
                    lh.append((low, length))

            if role == "lead":
                continue                       # the tune is written below, bar by bar
            size = 4 if loud else 3
            if loud and top is not None:
                target = _into(top, RH_RANGE[0] + 12, RH_RANGE[1])
            else:
                target = previous_top
            chord = _rh_chord(pcs, target, size)
            if chord:
                previous_top = max(chord)
            if role == "sustain" or not chord:
                rh.append((chord or None, length))
            elif loud and level == "pro" and beat == 2:
                rh += [(chord, length / 2), (chord, length / 2)]   # driving repeated chords
            elif not loud and advanced and position != 0:
                # Off the beat when it is quiet: the left hand has the pulse.
                rh += [(None, length / 2), (chord, length / 2)]
            else:
                rh.append((chord, length))

        if role == "lead":
            # The tune on top, with the chord under it on every beat — in
            # octaves when it is loud and the player is advanced.
            position = Fraction(0)
            bar_chords = {b[1]: b[2] for b in bar_beats}
            for midi, dur in phrase["voices"]["soprano"][bar_number]:
                if midi is None:
                    rh.append((None, dur))
                else:
                    note = _into(midi + shift + section["transpose"], RH_RANGE[0] + 10,
                                 RH_RANGE[1])
                    pcs = bar_chords.get(position)
                    if pcs is not None and note % 12 in pcs:
                        under = _rh_chord(pcs, note, 3 if loud else 2, ceiling=note)
                        notes = set(under) | {note}
                        if loud and advanced and note - 12 >= RH_RANGE[0]:
                            notes.add(note - 12)
                        rh.append((tuple(sorted(notes)), dur))
                    else:
                        rh.append((note, dur))
                position += dur

        right.append(orchestrate.fit_bar(rh, units))
        left.append(orchestrate.fit_bar(lh, units))

    events_r = [e for bar in right for e in bar]
    events_l = [e for bar in left for e in bar]
    if role == "sustain":
        events_r = orchestrate.merge_repeats(events_r)
        events_l = orchestrate.merge_repeats(events_l)
    return (orchestrate.render_bars(events_r, key, units),
            orchestrate.render_bars(events_l, key, units))



# ---------------------------------------------------------------------------
# Per-section writing for the model's lines
# ---------------------------------------------------------------------------

LEVEL_MARK_RE = re.compile(r"!(?:p{1,4}|mp|mf|f{1,4}|sfz|fp)!")


def _level_if_missing(bars: list, section: dict, entering: bool, previous_dynamic) -> list:
    if not bars or LEVEL_MARK_RE.search(bars[0]):
        return bars
    if entering or section["dynamic"] != previous_dynamic:
        bars = list(bars)
        bars[0] = _after_fields(bars[0], f"!{section['dynamic']}!")
    return bars


def guide_voice(section: dict, part_id: str, voice: str, written_ids) -> str:
    """The chorale line a melody player follows in one section.

    A melody player who is not leading this section does not double whoever
    is: it takes the alto line and writes a counter-melody around it. That is
    the difference between a duo and two people playing the same tune.
    """
    if part_id in written_ids and section["lead"] and section["lead"] != part_id \
            and voice == "soprano":
        return "alto"
    return voice


def section_chorale(phrases: dict, section: dict) -> dict:
    """The chorale under one section: its bars, moved into the section's key."""
    phrase = phrases[section["phrase"]]
    span = slice(section["from"], section["to"])
    return {
        "bars": section["bars"],
        "voices": {v: [[(None if m is None else m + section["transpose"], d) for m, d in bar]
                       for bar in phrase["voices"][v][span]] for v in VOICES},
        "chords": [orchestrate.transpose_chord(c, section["transpose"], section["key"])
                   for c in phrase["chords"][span]],
    }


def statement_numbers(sections: list) -> list:
    """For each section, which hearing of its phrase this is: 1, 2, 3 …"""
    seen, out = {}, []
    for s in sections:
        seen[s["phrase"]] = seen.get(s["phrase"], 0) + 1
        out.append(seen[s["phrase"]])
    return out


def section_line(plan: dict, part: dict, section: dict, voice: str, units: Fraction) -> str:
    """One section of a part's guide line, at written pitch in the section's key."""
    chorale = section_chorale(plan["_phrases"], section)
    events = [e for bar in chorale["voices"][voice] for e in bar]
    events = orchestrate.transpose(events, -int(part["transpose"]))
    events = orchestrate.transpose(
        events, 12 * orchestrate.fit_octave(events, part, plan.get("_level") or "intermediate"))
    key = written_key_for(part, section["key"])
    return " | ".join(orchestrate.render_bars(events, key, units)) + " |"



def snap_held(text: str, part: dict, chorale: dict, key: str, beat: Fraction,
              units: Fraction) -> str:
    """Move a hand-written line's held clashes onto the chord, and nothing else.

    A note that starts on a beat and lasts a beat or more, and is not sounding
    in the chorale at that moment, is moved to the nearest note that is. Every
    other note — passing notes, runs, ornaments — and all the rhythm,
    articulation and dynamics are kept exactly. This rescues a fine line with
    one wrong long note instead of throwing the whole section away.
    """
    if not part["pitched"] or not text:
        return text
    shift = int(part["transpose"])
    bars = orchestrate.split_bars(text)
    fixed = []
    for index, bar in enumerate(bars):
        if index >= chorale["bars"]:
            fixed.append(bar)
            continue
        events = orchestrate.read_bars(bar, key)
        events = events[0] if events else []
        plan, position = [], Fraction(0)
        for midi, dur in events:
            if midi is not None and not isinstance(midi, tuple) and position % beat == 0 \
                    and dur >= beat:
                pcs = {m % 12 for m in (_sounding_at(chorale["voices"][v][index], position)
                                        for v in VOICES) if m is not None}
                symbols = chorale.get("chords") or []
                pcs |= (chord_pcs_at(symbols[index], position, units, beat)
                        if index < len(symbols) else None) or set()
                if pcs and (midi + shift) % 12 not in pcs:
                    plan.append(_nearest_in(midi + shift, pcs) - shift)
                    position += dur
                    continue
            plan.append(None)
            position += dur
        fixed.append(orchestrate.remap_notes(bar, key, plan))
    return " | ".join(fixed) + " |"


# ---------------------------------------------------------------------------
# Fixing a hand-written line's arithmetic in place
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"(?P<pitch>[\^_=]*[A-Ga-g][,']*|[zx])(?P<dur>\d*/*\d*)")


def _last_token(bar: str):
    """(start, duration start, duration end, kind) of the bar's last note/rest/chord."""
    last, i, n = None, 0, len(bar)
    while i < n:
        ch = bar[i]
        if ch in "!\"":
            j = bar.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "{":
            j = bar.find("}", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(bar, i):
            j = bar.find("]", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "[":
            j = bar.find("]", i)
            if j < 0:
                break
            match = abc_check.DURATION_RE.match(bar, j + 1)
            last = (i, j + 1, match.end(), "chord")
            i = match.end()
            continue
        match = _TOKEN_RE.match(bar, i)
        if match and match.group("pitch"):
            last = (i, match.start("dur"), match.end("dur"), "note")
            i = match.end()
            continue
        i += 1
    return last


def fix_bar(bar: str, units: Fraction) -> str:
    """Make one hand-written bar exactly ``units`` long, changing as little as possible.

    Short: a rest fills the gap. Long: the last note is shortened, or dropped
    if the overflow is longer than it. Bars with tuplets or broken rhythm are
    left alone — their arithmetic is not certain enough to edit.
    """
    if re.search(r"\(\d|[<>]", bar):
        return bar
    for _ in range(8):
        length = sum((b.length for b in abc_check.bar_lengths(bar) if b.length > 0),
                     Fraction(0))
        if length == units:
            return bar
        if length < units:
            return bar.rstrip() + " z" + orchestrate._duration(units - length)
        excess = length - units
        token = _last_token(bar)
        if token is None:
            return bar
        start, dur_start, dur_end, kind = token
        current = abc_check._read_duration(bar, dur_start)[0]
        if kind == "note" and current > excess:
            bar = bar[:dur_start] + orchestrate._duration(current - excess) + bar[dur_end:]
        else:
            bar = (bar[:start] + bar[dur_end:]).rstrip()
    return bar


def fix_line(text: str, bars: int, units: Fraction, part: dict, key: str) -> str:
    """Bar lengths, bar count and range of a hand-written section, fixed in place."""
    split = orchestrate.split_bars(text or "")
    if not split:
        return text
    split = [fix_bar(bar, units) for bar in split[:bars]]
    if len(split) == bars - 1:
        split.append(orchestrate.rest_bar(units))     # one bar short: let it breathe
    low, high = orchestrate.range_of(part)
    if part["pitched"] and low is not None:
        fitted = []
        for bar in split:
            events = (orchestrate.read_bars(bar, key) or [[]])[0]
            plan = []
            for midi, _ in events:
                if midi is None or isinstance(midi, tuple):
                    plan.append(None)
                elif midi < low:
                    plan.append(midi + 12 * -(-(low - midi) // 12))
                elif midi > high:
                    plan.append(midi - 12 * -(-(midi - high) // 12))
                else:
                    plan.append(None)
            fitted.append(orchestrate.remap_notes(bar, key, plan))
        split = fitted
    return " | ".join(split) + " |"


# ---------------------------------------------------------------------------
# Balance: the keyboard inside an orchestra
# ---------------------------------------------------------------------------

# In an orchestra the piano is a colour, not the engine: a forte keyboard part
# under a dozen players sounds like a hammer. Its dynamics sit two steps below
# the ensemble's and its writing stays light. On its own, or with one partner,
# it plays at full strength.
SOFTER = {"pppp": "pp", "ppp": "pp", "pp": "pp", "p": "pp", "mp": "p", "mf": "p",
          "f": "mp", "ff": "mf", "fff": "mf", "ffff": "mf"}
DYNAMIC_MARK_RE = re.compile(r"!(p{1,4}|mp|mf|f{1,4}|sfz|fp)!")


def is_keyboard(part: dict) -> bool:
    return options.FAMILY_OF.get(part["instrument"]) == "Keyboard"


def accompanies(part: dict, roster: list) -> bool:
    """A keyboard playing inside an ensemble, which must stay soft."""
    return is_keyboard(part) and len(roster) > 2


def soften_marks(text: str) -> str:
    """Every dynamic in a keyboard line, two steps quieter; sfz becomes an accent."""
    def quieter(match):
        mark = match.group(1)
        if mark == "sfz":
            return "!accent!"
        if mark == "fp":
            return "!p!"
        return f"!{SOFTER[mark]}!"
    return DYNAMIC_MARK_RE.sub(quieter, text or "")


# ---------------------------------------------------------------------------
# The ending: V7 to I, slowing over the last two bars, the last chord held
# ---------------------------------------------------------------------------

RIT_MARK = '"^rit."'
FERMATA = "!fermata!"


def _root_pc(symbol: str):
    match = CHORD_RE.match(str(symbol or ""))
    return orchestrate.PITCH_OF_KEY.get(match.group(1) + match.group(2)) if match else None


def cadence_chords(key: str) -> tuple:
    """(V7, I) of ``key``: ('G7', 'C') in C major, ('E7', 'Am') in A minor."""
    root, minor = orchestrate._split_key(key)
    dominant = orchestrate.transpose_chord(root, 7, key) + "7"
    return dominant, root + ("m" if minor else "")


def close_on_cadence(chords: list, key: str) -> list:
    """The chord grid's last two bars made into a perfect cadence: … V7 | I.

    The penultimate bar keeps its first chord when that leads into the
    dominant ('Dm7 G7'), so the harmony quickens into the close rather than
    simply stopping.
    """
    if not chords:
        return chords
    dominant, tonic = cadence_chords(key)
    chords = list(chords)
    chords[-1] = tonic
    if len(chords) > 1:
        before = bar_chords(chords[-2])
        five = _root_pc(dominant)
        if _root_pc(before[0]) == five:
            chords[-2] = dominant
        else:
            chords[-2] = f"{before[0]} {dominant}" if before[0] else dominant
    return chords


def _bar_spans(music: str) -> list:
    """[(start, end)] of each bar's text, so barlines can be kept as written."""
    spans, start, i, n = [], 0, 0, len(music)
    while i < n:
        ch = music[i]
        if ch in "!\"":
            j = music.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(music, i):
            j = music.find("]", i)
            i = n if j < 0 else j + 1
            continue
        barline = orchestrate.BARLINE_RE.match(music, i)
        if barline:
            spans.append((start, i))
            i = start = barline.end()
            continue
        i += 1
    spans.append((start, n))
    return [(a, b) for a, b in spans if music[a:b].strip()]


INLINE_KEY_RE = re.compile(r"\[K:([^\]]*)\]")


def finish_line(text: str, key: str, units: Fraction, rit: bool = True) -> str:
    """Mark 'rit.' over the last two bars and hold the final chord under a fermata.

    The final bar becomes its first sounding note or chord — the tonic arrival —
    held for the whole bar, so the piece ends on a sustained chord instead of
    stopping mid-figure. Its dynamic is kept.
    """
    if not text:
        return text
    keys = INLINE_KEY_RE.findall(text)
    key = keys[-1].strip() if keys else key
    spans = _bar_spans(text)
    if not spans:
        return text
    a, b = spans[-1]
    last = text[a:b]
    head = INLINE_AT_START_RE.match(last.strip()).group(0)
    events = (orchestrate.read_bars(last, key) or [[]])[0]
    sounding = next((midi for midi, _ in events if midi is not None), None)
    marks = DYNAMIC_MARK_RE.findall(last)
    if sounding is None:
        held = orchestrate.rest_bar(units)
    else:
        held = orchestrate.render_bars([(sounding, units)], key, units)[0]
    lead = (f"!{marks[0]}!" if marks else "") + FERMATA
    new_last = f" {head}{lead}{held} "
    text = text[:a] + new_last + text[b:]

    if rit and len(spans) > 1:
        a, b = spans[-2]
        text = text[:a] + " " + _after_fields(text[a:b].strip(), RIT_MARK) + " " + text[b:]
    return re.sub(r"  +", " ", text)


def finish_parts(parts: list, roster: list, units: Fraction) -> None:
    """Close every staff the same way, in place. See finish_line."""
    by_id = {part["partId"]: part for part in roster}
    for written in parts:
        key = written.get("abcKey") or "C"
        written["abc"] = finish_line(written.get("abc"), key, units)
        if written.get("abcLower"):
            written["abcLower"] = finish_line(written["abcLower"], key, units, rit=False)
        part = by_id.get(written["partId"])
        if part and accompanies(part, roster):
            written["abc"] = soften_marks(written["abc"])
            written["abcLower"] = soften_marks(written.get("abcLower"))


# ---------------------------------------------------------------------------
# Balance: the tune over the accompaniment
# ---------------------------------------------------------------------------

# An orchestra is balanced by its dynamics: where one player carries the tune,
# everyone else is marked a step below so the line sounds through; under a solo,
# two steps. Where nobody leads — a tutti, a chorale — everyone plays the
# section's level together. Accompanying keyboards are lowered separately
# (soften_marks), so they are left alone here.
SOLO_MARK = '"^solo"'
LEVELS = ("pp", "p", "mp", "mf", "f", "ff")
_LEVEL_OF = {"pppp": 0, "ppp": 0, **{level: i for i, level in enumerate(LEVELS)},
             "fff": 5, "ffff": 5}


def quieter(level: str, steps: int) -> str:
    return LEVELS[max(0, _LEVEL_OF.get(level, 3) - steps)]


def _first_sounding(bar: str):
    """Index of the first note or chord in the bar (not a rest), or None."""
    i, n = 0, len(bar)
    while i < n:
        ch = bar[i]
        if ch in "!\"":
            j = bar.find(ch, i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "{":
            j = bar.find("}", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "[" and abc_check.INLINE_FIELD_RE.match(bar, i):
            j = bar.find("]", i)
            i = n if j < 0 else j + 1
            continue
        match = TOKEN_START_RE.match(bar, i)
        if match:
            if match.group(0)[-1] not in "zx":
                return i
            i = match.end()
            continue
        i += 1
    return None


def section_level(section: dict, part_id: str) -> tuple:
    """(level, cap) for one player in one section: what to mark, and the most allowed."""
    lead = section.get("lead")
    if not lead or lead == part_id:
        return section["dynamic"], None
    steps = 2 if section.get("texture") == "solo" else 1
    level = quieter(section["dynamic"], steps)
    return level, level


def balance_line(text: str, part_id: str, sections: list, solo_mark: bool = True) -> str:
    """One staff balanced section by section. See the note above LEVELS."""
    if not text:
        return text
    spans = _bar_spans(text)
    if len(spans) != sum(s["bars"] for s in sections):
        return text
    bars = [text[a:b] for a, b in spans]
    current = None
    for s in sections:
        level, cap = section_level(s, part_id)
        first = None
        for index in range(s["start"] - 1, s["end"]):
            bar = bars[index]
            if cap:
                bar = DYNAMIC_MARK_RE.sub(
                    lambda m: f"!{cap}!" if _LEVEL_OF.get(m.group(1), -1) > _LEVEL_OF[cap]
                    else m.group(0), bar)
            at = _first_sounding(bar)
            if first is None and at is not None:
                first = index
                # A level written anywhere in the entry bar is the writer's choice.
                marked = DYNAMIC_MARK_RE.search(bar)
                add = ""
                if not marked and level != current:
                    add += f"!{level}!"
                if solo_mark and s.get("texture") == "solo" and s.get("lead") == part_id \
                        and "solo" not in bar:
                    add = SOLO_MARK + add
                if add:
                    while at > 0 and bar[at - 1] in "^_=":
                        at -= 1
                    bar = bar[:at] + add + bar[at:]
            marks = [m for m in DYNAMIC_MARK_RE.findall(bar) if m in _LEVEL_OF]
            if marks:
                current = marks[-1]
            bars[index] = bar
    out, last = [], 0
    for (a, b), bar in zip(spans, bars):
        out.append(text[last:a])
        out.append(bar)
        last = b
    out.append(text[last:])
    return "".join(out)


def balance_parts(parts: list, roster: list, sections: list) -> None:
    """Every staff balanced in place: the tune above, accompaniment beneath it.

    ``sections``: [{start, end, bars, dynamic, lead, texture}] in measure order.
    """
    by_id = {part["partId"]: part for part in roster}
    for written in parts:
        part = by_id.get(written["partId"])
        if not part or accompanies(part, roster):
            continue
        written["abc"] = balance_line(written.get("abc"), part["partId"], sections)
        if written.get("abcLower"):
            written["abcLower"] = balance_line(written["abcLower"], part["partId"], sections,
                                               solo_mark=False)


# ---------------------------------------------------------------------------
# Modulation: leading into a new key instead of jumping
# ---------------------------------------------------------------------------

def same_key(a: str, b: str) -> bool:
    (ra, ma), (rb, mb) = orchestrate._split_key(a), orchestrate._split_key(b)
    return ma == mb and orchestrate.PITCH_OF_KEY.get(ra) == orchestrate.PITCH_OF_KEY.get(rb)


def approach_chords(key: str) -> tuple:
    """(ii7, V7) of ``key``: ('Am7', 'D7') into G major, ('F#ø7', 'B7') into E minor."""
    root, minor = orchestrate._split_key(key)
    two = orchestrate.transpose_chord(root, 2, key) + ("ø7" if minor else "m7")
    return two, cadence_chords(key)[0]


def lead_into(chords: list, key: str) -> list:
    """A section's last bar made to lead into ``key``: ii – V7 of the new key.

    A section in a new key that simply starts is a jump. With the old
    section's last bar on the new key's ii and V7, the ear hears the new tonic
    coming, and the arrival is a resolution rather than a jolt.
    """
    if not chords:
        return chords
    chords = list(chords)
    chords[-1] = " ".join(approach_chords(key))
    return chords


def arrive_in(chords: list, key: str) -> list:
    """A section's first bar made the new key's tonic, where the V7 resolves."""
    if not chords:
        return chords
    chords = list(chords)
    tonic = cadence_chords(key)[1]
    if _root_pc(bar_chords(chords[0])[0]) != _root_pc(tonic):
        chords[0] = tonic
    return chords
