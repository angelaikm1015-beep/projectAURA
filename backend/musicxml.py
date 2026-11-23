"""The compact score as a real MusicXML file.

A deep-mode piece is stored as the compact notation (compact.py): every
measure of every part as one short string at concert pitch, which is a few
kilobytes where the MusicXML is hundreds. This turns it into a MusicXML 4.0
score-partwise file that MuseScore, Sibelius, Finale and Dorico open: written
pitch for transposing instruments with a <transpose> element, key signatures
that change where the form changes key, clefs, grand staves, ties, slurs,
dynamics, articulations, 'solo' and 'rit.' marks, and the final fermata.
"""

from fractions import Fraction
from xml.sax.saxutils import escape

import compact
import form
import orchestrate

DIVISIONS = 8          # per quarter note: a 32nd is 1
TYPES = {                  # length in eighth notes -> (type, dots)
    Fraction(8): ("whole", 0), Fraction(6): ("half", 1), Fraction(4): ("half", 0),
    Fraction(3): ("quarter", 1), Fraction(2): ("quarter", 0), Fraction(3, 2): ("eighth", 1),
    Fraction(1): ("eighth", 0), Fraction(3, 4): ("16th", 1), Fraction(1, 2): ("16th", 0),
    Fraction(3, 8): ("32nd", 1), Fraction(1, 4): ("32nd", 0),
}
CLEFS = {"treble": ("G", 2), "bass": ("F", 4), "alto": ("C", 3), "perc": ("percussion", None)}
# Where an unpitched hit is drawn on the percussion staff.
HIT_DISPLAY = {"L": ("F", 4), "H": ("C", 5)}


def _divisions(units: Fraction) -> int:
    return int(units * DIVISIONS / 2)


def _clef(sign: str, line, number: int = None) -> str:
    attr = f' number="{number}"' if number else ""
    line_xml = f"<line>{line}</line>" if line else ""
    return f"<clef{attr}><sign>{sign}</sign>{line_xml}</clef>"


def _attributes(part: dict, key: str, beats: str, beat_type: str, first: bool) -> str:
    fifths = orchestrate.Key(key).fifths if part["pitched"] else 0
    xml = "<attributes>"
    if first:
        xml += f"<divisions>{DIVISIONS}</divisions>"
    xml += f"<key><fifths>{fifths}</fifths></key>"
    if first:
        xml += f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>"
        if part["staves"] == 2:
            xml += "<staves>2</staves>" + _clef("G", 2, 1) + _clef("F", 4, 2)
        else:
            xml += _clef(*CLEFS.get(part["clef"], ("G", 2)))
        transpose = int(part["transpose"])
        if transpose and part["pitched"]:
            octaves, rest = divmod(transpose, 12) if transpose > 0 else (-((-transpose) // 12),
                                                                          -((-transpose) % 12))
            diatonic = round(rest * 7 / 12)
            xml += (f"<transpose><diatonic>{diatonic}</diatonic><chromatic>{rest}</chromatic>"
                    + (f"<octave-change>{octaves}</octave-change>" if octaves else "")
                    + "</transpose>")
    return xml + "</attributes>"


def _direction(inner: str, placement: str, staff: int = None) -> str:
    staff_xml = f"<staff>{staff}</staff>" if staff else ""
    return (f'<direction placement="{placement}"><direction-type>{inner}</direction-type>'
            f"{staff_xml}</direction>")


def _events_xml(events: list, part: dict, key: str, staff: int, units: Fraction,
                slur_state: dict) -> str:
    """One staff's notes for one measure."""
    xml = ""
    voice = 1 if staff in (None, 1) else 5
    staff_xml = f"<staff>{staff}</staff>" if staff else ""
    whole_rest = len(events) == 1 and not events[0].sounding
    for event in events:
        if event.text:
            xml += _direction(f"<words>{escape(event.text)}</words>", "above", staff)
        if event.dynamic:
            xml += _direction(f"<dynamics><{event.dynamic}/></dynamics>", "below", staff)
        pieces = compact._pieces(event.dur)
        written = [compact.to_written(n, int(part["transpose"]), key) for n in event.notes]
        for i, piece in enumerate(pieces):
            kind, dots = TYPES.get(piece, ("32nd", 0))
            duration = _divisions(piece)
            tie_in = i > 0 or event_tied_in(event, slur_state)
            tie_out = event.sounding and (i < len(pieces) - 1 or event.tie)
            heads = written or [None] * max(1, len(event.hits))
            for h, note in enumerate(heads):
                xml += "<note>"
                if h:
                    xml += "<chord/>"
                if not event.sounding:
                    xml += '<rest measure="yes"/>' if whole_rest else "<rest/>"
                elif event.hits:
                    step, octave = HIT_DISPLAY[event.hits[h]]
                    xml += (f"<unpitched><display-step>{step}</display-step>"
                            f"<display-octave>{octave}</display-octave></unpitched>")
                else:
                    letter, alter, octave = note
                    xml += (f"<pitch><step>{compact.LETTERS[letter]}</step>"
                            + (f"<alter>{alter}</alter>" if alter else "")
                            + f"<octave>{octave}</octave></pitch>")
                xml += f"<duration>{duration}</duration>"
                if tie_in:
                    xml += '<tie type="stop"/>'
                if tie_out:
                    xml += '<tie type="start"/>'
                xml += f"<voice>{voice}</voice>"
                if not (whole_rest and not event.sounding):
                    xml += f"<type>{kind}</type>" + "<dot/>" * dots
                xml += staff_xml
                notations = ""
                if tie_in:
                    notations += '<tied type="stop"/>'
                if tie_out:
                    notations += '<tied type="start"/>'
                if h == 0 and i == 0 and event.slur_start:
                    notations += '<slur type="start" number="1"/>'
                if h == 0 and i == len(pieces) - 1 and event.slur_stop:
                    notations += '<slur type="stop" number="1"/>'
                if h == 0 and i == 0 and (event.accent or event.staccato):
                    notations += ("<articulations>" + ("<accent/>" if event.accent else "")
                                  + ("<staccato/>" if event.staccato else "")
                                  + "</articulations>")
                if h == 0 and event.fermata:
                    notations += "<fermata/>"
                if notations:
                    xml += f"<notations>{notations}</notations>"
                xml += "</note>"
        slur_state["tied"] = event.tie
    return xml


def event_tied_in(event, state: dict) -> bool:
    """Whether the note before this one tied into it (carried across barlines)."""
    return bool(state.get("tied")) and event.sounding


def build(stored: dict, roster: list) -> str:
    """The stored compact score (see deep.compose) -> MusicXML text."""
    units_per_bar = Fraction(stored["units"])
    beats, _, beat_type = stored["timeSignature"].partition("/")
    sections = stored["sections"]
    by_id = {p["partId"]: p for p in stored["parts"]}
    players = [part for part in roster if part["partId"] in by_id]

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
           '"http://www.musicxml.org/dtds/partwise.dtd">',
           '<score-partwise version="4.0">',
           f"<work><work-title>{escape(stored.get('title') or 'Untitled')}</work-title></work>",
           "<identification><encoding><software>AURA</software></encoding></identification>",
           "<part-list>"]
    for part in players:
        xml.append(f'<score-part id="{part["partId"]}"><part-name>{escape(part["label"])}'
                   "</part-name></score-part>")
    xml.append("</part-list>")

    starts = {s["start"]: s for s in sections}
    ends = {s["end"] for s in sections}
    total = sum(s["bars"] for s in sections)
    for number, part in enumerate(players):
        stored_part = by_id[part["partId"]]
        upper = [compact.read_measure(m, units_per_bar)[0] for m in stored_part["upper"]]
        lower = [compact.read_measure(m, units_per_bar)[0] for m in stored_part.get("lower") or []]
        xml.append(f'<part id="{part["partId"]}">')
        states = ({}, {})
        key = None
        for measure in range(1, total + 1):
            xml.append(f'<measure number="{measure}">')
            section = starts.get(measure)
            if section:
                written = form.written_key_for(part, section["key"])
                if written != key or measure == 1:
                    xml.append(_attributes(part, written, beats, beat_type, measure == 1))
                    key = written
                if section.get("label"):
                    xml.append(_direction(f"<rehearsal>{escape(section['label'])}</rehearsal>",
                                          "above", 1 if part["staves"] == 2 else None))
            if measure == 1 and number == 0:
                xml.append(_direction(f"<words>{escape(stored.get('tempoText') or '')}</words>",
                                      "above").replace("</direction>",
                                                       f'<sound tempo="{stored.get("tempoBpm") or 96}"/>'
                                                       "</direction>"))
            staff = 1 if part["staves"] == 2 else None
            xml.append(_events_xml(upper[measure - 1], part, key, staff, units_per_bar, states[0]))
            if part["staves"] == 2:
                xml.append(f"<backup><duration>{_divisions(units_per_bar)}</duration></backup>")
                bar = lower[measure - 1] if measure - 1 < len(lower) else [compact.Event(units_per_bar)]
                xml.append(_events_xml(bar, part, key, 2, units_per_bar, states[1]))
            if measure == total:
                xml.append('<barline location="right"><bar-style>light-heavy</bar-style></barline>')
            elif measure in ends:
                xml.append('<barline location="right"><bar-style>light-light</bar-style></barline>')
            xml.append("</measure>")
        xml.append("</part>")
    xml.append("</score-partwise>")
    return "\n".join(xml)
