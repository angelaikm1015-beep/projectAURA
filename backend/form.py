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
            read = voice_chords(chords, units)
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


def voice_chords(chords: list, units: Fraction) -> dict:
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
        pcs = chord_pcs(symbol)
        match = CHORD_RE.match(symbol or "")
        if not pcs or not match:
            for voice in VOICES:
                out[voice].append([(previous[voice], units)])
            continue
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
            out[voice].append([(chosen[voice], units)])
            previous[voice] = chosen[voice]
    return out


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
    """
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
    extended = plain("9") or plain("11") or plain("13")
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
        pcs = chord_pcs(symbol)
        if not pcs:
            continue
        for voice in VOICES:
            bar, position, fixed = voices[voice][index], Fraction(0), []
            for midi, dur in bar:
                on_beat = position % beat == 0
                if voice == "soprano":
                    on_beat = on_beat and (dur >= 2 * beat or (position == 0 and dur >= beat))
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
        transpose = 0 if level == "beginner" else \
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
        base = orchestrate.fit_octave(orchestrate.transpose(own, -transpose), part) \
            if part["pitched"] else 0

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
                bars_up, bars_low = _play(part, s, role, voice, treatment, phrases,
                                          mine.get(s["phrase"]), base, home_written,
                                          key, units, low, high)
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
          home_written, key, units, low, high) -> tuple:
    """(upper bars, lower bars or []) for a part that is playing this section."""
    phrase = phrases[section["phrase"]]
    span = slice(section["from"], section["to"])

    if decorated and decorated.get("abc"):
        return (_move_text(decorated["abc"], part, section, home_written, key, low, high,
                           span),
                _move_text(decorated["abcLower"], part, section, home_written, key,
                           None, None, span) if decorated.get("abcLower") else [])

    if not part["pitched"]:
        return [percussion_bar(units)] * section["bars"], []

    if part["instrument"] == "Timpani":
        return _timpani(phrase, section, part, key, units), []

    line = "soprano" if role == "lead" else voice
    sustained = role == "sustain" or (role != "lead" and treatment == "sustained")
    upper = _derive(phrase, line, section, part, base if line == voice else None,
                    key, units, sustained)
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
            left_hand=None) -> list:
    """One voice of one section, at this player's written pitch and octave.

    ``left_hand`` is a (low, high) range for a piano's lower stave, which reads
    the bass line in its own register rather than the instrument's whole range.
    """
    events = orchestrate.transpose(_section_events(phrase, voice, section),
                                   section["transpose"] - int(part["transpose"]))
    low, high = left_hand or orchestrate.range_of(part)
    if base is None:
        base = orchestrate.octave_shift_for(events, low, high) if left_hand \
            else orchestrate.fit_octave(events, part)
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
    events = orchestrate.transpose(events, 12 * orchestrate.fit_octave(events, part))
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
    checked, off, held, first = 0, 0, None, None

    def chorale_at(index, at):
        return {m % 12 for m in (_sounding_at(chord[v][index], at) for v in VOICES)
                if m is not None}

    for index, bar in enumerate(bars[:phrase["bars"]]):
        position = Fraction(0)
        for midi, dur in bar:
            if midi is not None and position % beat == 0:
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
    pitched = [part for part in roster if part["pitched"]]
    if not pitched:
        return
    entries = {entry.get("partId"): entry for entry in plan.get("partPlan") or []}
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
        if (part["partId"] in leads and part["pitched"]
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
