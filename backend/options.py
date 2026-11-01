"""Canonical option lists for AURA's profile fields.

These are the single source of truth: the frontend fetches them from
``GET /api/options`` to build its dropdowns, and :mod:`validators` checks
submitted values against them. Adding an instrument here makes it selectable
and accepted in one edit.
"""

GENDERS = [
    "Female",
    "Male",
    "Non-binary",
    "Other",
    "Prefer not to say",
]

AFFILIATIONS = [
    {"value": "middle-school", "label": "Middle school"},
    {"value": "high-school", "label": "High school"},
    {"value": "college", "label": "College / University"},
    {"value": "hobby", "label": "Just playing for fun (hobby)"},
]

INSTRUMENTS = [
    {
        "group": "Strings",
        "options": [
            "Violin", "Viola", "Cello", "Double bass", "Harp",
            "Acoustic guitar", "Electric guitar", "Bass guitar", "Ukulele",
        ],
    },
    {
        "group": "Woodwinds",
        "options": [
            "Flute", "Piccolo", "Clarinet", "Bass clarinet", "Oboe",
            "English horn", "Bassoon", "Saxophone", "Recorder",
        ],
    },
    {
        "group": "Brass",
        "options": ["Trumpet", "French horn", "Trombone", "Euphonium", "Tuba"],
    },
    {
        "group": "Percussion",
        "options": [
            "Snare drum", "Drum set", "Timpani", "Marimba",
            "Xylophone", "Vibraphone", "Hand percussion",
        ],
    },
    {
        "group": "Keyboard",
        "options": ["Piano", "Organ", "Harpsichord", "Synthesizer", "Accordion"],
    },
    {
        "group": "Voice & other",
        "options": ["Voice / Vocals", "Traditional instrument", "Other"],
    },
]

GENRES = [
    "Classical", "Jazz", "Blues", "Rock", "Pop", "Hip-hop / Rap", "R&B / Soul",
    "Electronic / EDM", "Folk / Acoustic", "Country", "Latin", "Metal", "Punk",
    "Reggae", "K-pop", "J-pop", "Musical theatre", "Film & game score",
    "Gospel / Worship", "World / Traditional", "Other",
]

NATIONALITIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
    "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan",
    "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria",
    "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia", "Cameroon", "Canada",
    "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros",
    "Congo (Brazzaville)", "Congo (Kinshasa)", "Costa Rica", "Côte d'Ivoire", "Croatia",
    "Cuba", "Cyprus", "Czechia", "Denmark", "Djibouti", "Dominica", "Dominican Republic",
    "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia",
    "Eswatini", "Ethiopia", "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia",
    "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau",
    "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran",
    "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan",
    "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho",
    "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar",
    "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Mauritania",
    "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro",
    "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands",
    "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia",
    "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania",
    "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe",
    "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
    "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea",
    "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland",
    "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo",
    "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu",
    "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States",
    "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen",
    "Zambia", "Zimbabwe",
]


# ---------------------------------------------------------------------------
# Composition options — the score generator's dropdowns
# ---------------------------------------------------------------------------
#
# The lists above describe *a person* (used at signup). Everything below
# describes *a piece to be written*, and is consumed by the compose form and by
# composer.py when it briefs the model.

ENSEMBLE_GROUPS = [
    {
        "value": "elementary-school",
        "label": "Elementary school",
        "guidance": "Very short rehearsal attention span. Unison or two parts at most, "
                    "constant doubling, nothing faster than eighth notes.",
    },
    {
        "value": "middle-school",
        "label": "Middle school",
        "guidance": "Roughly 2-4 rehearsals. Keep every part playable at sight after one "
                    "read-through; double exposed lines so no one is left alone.",
    },
    {
        "value": "high-school",
        "label": "High school",
        "guidance": "Can handle real independence between parts and a short exposed solo, "
                    "but keep page turns and endurance in mind.",
    },
    {
        "value": "college",
        "label": "College / University",
        "guidance": "Music majors. Their juries already demand three-octave scales and "
                    "arpeggios in every key, double stops in thirds, sixths and "
                    "octaves, a virtuoso étude and a concerto movement; their pianists "
                    "audition with Chopin and Liszt études at full tempo. Write at that "
                    "standard — a piece they could sight-read in one pass is too easy.",
    },
    {
        "value": "community",
        "label": "Community / amateur ensemble",
        "guidance": "Wide spread of ability inside one group. Write a secure, doubled core "
                    "and put the difficulty in optional divisi rather than in everyone's part.",
    },
    {
        "value": "hobby",
        "label": "Friends playing for fun (hobby)",
        "guidance": "No conductor culture and little rehearsal. Favour a memorable tune, "
                    "clear repeats, and parts that lock in by ear.",
    },
    {
        "value": "professional",
        "label": "Professional ensemble",
        "guidance": "Orchestral and chamber professionals: one rehearsal, sight-read "
                    "to a high standard. Write idiomatically and at full virtuoso "
                    "difficulty, without hand-holding, and give every section "
                    "something worth practising.",
    },
]

# Exactly the four bands the product defines. `years` is shown in the UI so the
# player picks by experience rather than by guessing what "intermediate" means.
SKILL_LEVELS = [
    {
        "value": "beginner",
        "label": "Beginner",
        "years": "under 1 year",
        "guidance": "Keys of C, G, F or D major. Time in 4/4, 3/4 or 2/4 at a steady "
                    "tempo. Whole, half, quarter and eighth notes, plus the "
                    "dotted-quarter-eighth figure and simple ties. Stay inside the "
                    "easy range. Stepwise motion with leaps up to a sixth. Strings "
                    "stay in first position; winds stay in the first octave they "
                    "learn. Whole-bar rests so players can find their place. Slurs, "
                    "staccato and three dynamic levels are all fair — a first-year "
                    "player can shape a phrase even if they cannot play fast.",
    },
    {
        "value": "intermediate",
        "label": "Intermediate",
        "years": "1 – 3 years",
        "guidance": "Up to four sharps or flats, and accidentals outside the key. "
                    "Sustained sixteenth-note passages at a moderate tempo, dotted "
                    "rhythms, triplets, syncopation and ties across barlines. One "
                    "meter change is fine. Mostly the easy range, reaching into the "
                    "full range at climaxes. Leaps up to an octave. Strings use third "
                    "position and simple double stops; winds cross the break freely. "
                    "Independent lines rather than constant doubling, short exposed "
                    "entries, and the full articulation vocabulary from pp to ff.",
    },
    {
        "value": "higher-intermediate",
        "label": "Higher intermediate",
        "years": "4 – 7 years",
        "guidance": "Conservatory-audition standard — Henle difficulty 5-6, the level "
                    "of a Brahms violin sonata or a Kreutzer étude. Any key, chromatic "
                    "harmony and modulation. Fast sixteenth-note passagework that has "
                    "to be practised, dotted and syncopated rhythm, triplets against "
                    "duple, hemiola. The full written range including the top of it. "
                    "Independent lines rather than doubling. Strings: up to seventh "
                    "position, double stops, spiccato and fast string crossings. "
                    "Winds and brass: the whole range, fast tonguing, wide slurred "
                    "intervals. Piano: both hands busy — broken chords and scale runs "
                    "in sixteenths, octaves. Everything articulated and shaped.",
    },
    {
        "value": "pro",
        "label": "Pro",
        "years": "8+ years",
        "guidance": "Virtuoso standard — Henle difficulty 7-8: a Paganini caprice, the "
                    "Beethoven violin concerto, a Chopin or Liszt concert étude. No "
                    "caution. Chromatic and extended harmony, real modulation, rubato. "
                    "Rapid sixteenth- and thirty-second-note runs, arpeggios across "
                    "the whole instrument, wide leaps, the extremes of range and "
                    "dynamic, cross-rhythm. Strings: high positions, double stops in "
                    "thirds, sixths and octaves, chords, ricochet and harmonics. Winds: "
                    "double tonguing, altissimo, rapid chromatic runs. Write as for "
                    "paid players who will read it once and perform it.",
    },
]

MOODS = [
    {"value": "happy", "label": "Happy",
     "guidance": "Bright major key, brisk lilting tempo, light detached articulation, "
                 "rising melodic contours."},
    {"value": "joyful", "label": "Joyful / celebratory",
     "guidance": "Major key, fanfare gestures, full textures, driving rhythm, "
                 "accents on strong beats."},
    {"value": "playful", "label": "Playful",
     "guidance": "Staccato, unexpected rests and hiccups, call-and-response between "
                 "parts, sudden dynamic drops."},
    {"value": "peaceful", "label": "Peaceful / calm",
     "guidance": "Slow tempo, long slurred lines, sustained harmony, narrow dynamic "
                 "range, gentle suspensions."},
    {"value": "hopeful", "label": "Hopeful",
     "guidance": "Starts sparse and low, gradually adds parts and rises in register; "
                 "suspensions resolving upward."},
    {"value": "nostalgic", "label": "Nostalgic",
     "guidance": "Moderate tempo, warm middle register, a simple tune that returns "
                 "changed, gentle chromatic colouring."},
    {"value": "romantic", "label": "Romantic / tender",
     "guidance": "Singing melody over flowing accompaniment, rubato, expressive "
                 "appoggiaturas, rich seventh chords."},
    {"value": "sad", "label": "Sad",
     "guidance": "Minor key, slow tempo, descending lines, sighing two-note slurs, "
                 "sparse texture, soft dynamics."},
    {"value": "longing", "label": "Longing / yearning",
     "guidance": "Minor or modal, phrases that reach up and fall back, unresolved "
                 "harmony, wide expressive leaps."},
    {"value": "mysterious", "label": "Mysterious",
     "guidance": "Whole-tone or modal colour, quiet sustained low notes, ambiguous "
                 "harmony, unpredictable phrase lengths."},
    {"value": "tense", "label": "Tense / suspenseful",
     "guidance": "Repeated ostinato, dissonant held chords, tremolo, crescendo that "
                 "never resolves, irregular accents."},
    {"value": "angry", "label": "Angry",
     "guidance": "Minor key, fast and forceful, heavy accents, sharp dissonance, "
                 "driving repeated rhythms, loud dynamics, abrupt cut-offs."},
    {"value": "dark", "label": "Dark / ominous",
     "guidance": "Low register, minor mode, slow tread, chromatic descent, "
                 "half-step motion in the bass."},
    {"value": "epic", "label": "Epic / heroic",
     "guidance": "Broad tempo, brass-led fanfares, wide leaps, pedal tones, big "
                 "unison statements of the theme."},
    {"value": "dreamy", "label": "Dreamy",
     "guidance": "Floating harmony, soft dynamics, arpeggiated accompaniment, "
                 "blurred cadences, upper register."},
    {"value": "energetic", "label": "Energetic / driving",
     "guidance": "Fast tempo, relentless rhythmic pulse, syncopation, short punchy "
                 "motifs traded between parts."},
]

# Per-instrument engraving and playback facts. These are what keep the generated
# music *playable*: a Bb trumpet part has to be written a tone above concert
# pitch, a cello reads bass clef, and a beginner flute has no business above G5.
#
#   clef        ABC clef name for the staff
#   transpose   semitones from WRITTEN pitch to SOUNDING pitch (0 = concert)
#   easy        written range a beginner/intermediate player can rely on
#   full        written range an advanced player can be asked for
#   staves      2 = grand staff (brace), 1 = single staff
#   pitched     False = unpitched percussion, notated on a rhythm staff
#   midi        General MIDI program number, for browser playback
#   maxParts    upper bound on "how many of these"
_S = lambda clef, transpose, easy, full, midi, maxParts, staves=1, pitched=True: {
    "clef": clef, "transpose": transpose, "easy": easy, "full": full,
    "midi": midi, "maxParts": maxParts, "staves": staves, "pitched": pitched,
}

INSTRUMENT_SPECS = {
    # --- Strings ---
    "Violin":            _S("treble", 0,   "G3-D5",  "G3-A6",  40, 4),
    "Viola":             _S("alto",   0,   "C3-A4",  "C3-E6",  41, 3),
    "Cello":             _S("bass",   0,   "C2-A3",  "C2-A5",  42, 3),
    "Double bass":       _S("bass",   -12, "E2-G3",  "E2-G4",  43, 2),
    "Harp":              _S("treble", 0,   "C3-C6",  "C1-G7",  46, 2, staves=2),
    "Acoustic guitar":   _S("treble", -12, "E3-G5",  "E3-E6",  24, 2),
    "Electric guitar":   _S("treble", -12, "E3-G5",  "E3-E6",  27, 2),
    "Bass guitar":       _S("bass",   -12, "E2-G3",  "E2-C5",  33, 1),
    "Ukulele":           _S("treble", 0,   "C4-A5",  "C4-A6",  24, 2),

    # --- Woodwinds ---
    "Flute":             _S("treble", 0,   "C4-G5",  "C4-C7",  73, 3),
    "Piccolo":           _S("treble", 12,  "D4-G5",  "D4-C7",  72, 1),
    "Clarinet":          _S("treble", -2,  "E3-C5",  "E3-C6",  71, 3),
    "Bass clarinet":     _S("treble", -14, "E3-C5",  "E3-G5",  71, 1),
    "Oboe":              _S("treble", 0,   "C4-D5",  "Bb3-F6", 68, 2),
    "English horn":      _S("treble", -7,  "D4-D5",  "B3-C6",  69, 1),
    "Bassoon":           _S("bass",   0,   "F2-C4",  "Bb1-Eb5",70, 2),
    "Alto saxophone":    _S("treble", -9,  "C4-C5",  "Bb3-F6", 65, 2),
    "Tenor saxophone":   _S("treble", -14, "C4-C5",  "Bb3-F6", 66, 2),
    "Baritone saxophone":_S("treble", -21, "C4-C5",  "Bb3-F6", 67, 1),
    "Recorder":          _S("treble", 12,  "C4-D5",  "C4-D6",  74, 3),

    # --- Brass ---
    "Trumpet":           _S("treble", -2,  "G3-D5",  "F#3-C6", 56, 3),
    "French horn":       _S("treble", -7,  "G3-D5",  "C3-G5",  60, 4),
    "Trombone":          _S("bass",   0,   "F2-Bb3", "E2-Bb4", 57, 3),
    "Euphonium":         _S("bass",   0,   "F2-Bb3", "E2-Bb4", 58, 2),
    "Tuba":              _S("bass",   0,   "F1-F3",  "D1-F4",  58, 2),

    # --- Percussion ---
    "Timpani":           _S("bass",   0,   "F2-C4",  "D2-C4",  47, 1),
    "Marimba":           _S("treble", 0,   "C4-C6",  "C3-C7",  12, 2, staves=2),
    "Xylophone":         _S("treble", 12,  "C4-C6",  "C4-C7",  13, 1),
    "Vibraphone":        _S("treble", 0,   "C4-C6",  "F3-F6",  11, 1),
    "Snare drum":        _S("perc",   0,   "-",      "-",      38, 1, pitched=False),
    "Drum set":          _S("perc",   0,   "-",      "-",      0,  1, pitched=False),
    "Hand percussion":   _S("perc",   0,   "-",      "-",      0,  2, pitched=False),

    # --- Keyboard ---
    "Piano":             _S("treble", 0,   "C3-C6",  "A0-C8",  0,  2, staves=2),
    "Organ":             _S("treble", 0,   "C3-C6",  "C2-C7",  19, 1, staves=2),
    "Harpsichord":       _S("treble", 0,   "C3-C6",  "F1-F6",  6,  1, staves=2),
    "Synthesizer":       _S("treble", 0,   "C3-C6",  "C2-C7",  81, 2, staves=2),
    "Accordion":         _S("treble", 0,   "C3-C6",  "F2-A6",  21, 1, staves=2),

    # --- Voice ---
    "Soprano":           _S("treble", 0,   "D4-D5",  "C4-A5",  52, 2),
    "Alto (voice)":      _S("treble", 0,   "A3-C5",  "G3-D5",  52, 2),
    "Tenor (voice)":     _S("treble", -12, "D4-D5",  "C4-A5",  52, 2),
    "Bass (voice)":      _S("bass",   0,   "A2-C4",  "E2-D4",  52, 2),
}

del _S

# The compose form's instrument dropdown. Same grouped shape the signup form
# already renders, but drawn from the instruments we can actually notate — so
# "Other" and "Traditional instrument" are deliberately absent.
SCORE_INSTRUMENTS = [
    {"group": "Strings", "options": [
        "Violin", "Viola", "Cello", "Double bass", "Harp",
        "Acoustic guitar", "Electric guitar", "Bass guitar", "Ukulele"]},
    {"group": "Woodwinds", "options": [
        "Flute", "Piccolo", "Clarinet", "Bass clarinet", "Oboe", "English horn",
        "Bassoon", "Alto saxophone", "Tenor saxophone", "Baritone saxophone",
        "Recorder"]},
    {"group": "Brass", "options": [
        "Trumpet", "French horn", "Trombone", "Euphonium", "Tuba"]},
    {"group": "Percussion", "options": [
        "Timpani", "Marimba", "Xylophone", "Vibraphone",
        "Snare drum", "Drum set", "Hand percussion"]},
    {"group": "Keyboard", "options": [
        "Piano", "Organ", "Harpsichord", "Synthesizer", "Accordion"]},
    {"group": "Voice", "options": [
        "Soprano", "Alto (voice)", "Tenor (voice)", "Bass (voice)"]},
]


def as_dict() -> dict:
    """The payload served by ``GET /api/options``."""
    return {
        # signup
        "genders": GENDERS,
        "affiliations": AFFILIATIONS,
        "instruments": INSTRUMENTS,
        "genres": GENRES,
        "nationalities": NATIONALITIES,
        # compose
        "scoreInstruments": SCORE_INSTRUMENTS,
        "ensembleGroups": ENSEMBLE_GROUPS,
        "levels": SKILL_LEVELS,
        "moods": MOODS,
        # Only the parts of INSTRUMENT_SPECS the form needs: how many of this
        # instrument may be requested, what to show as a hint, and enough to
        # sound a preview of it in the browser — the MIDI voice and the register
        # it comfortably sits in.
        "instrumentInfo": {
            name: {"clef": spec["clef"], "maxParts": spec["maxParts"],
                   "staves": spec["staves"], "pitched": spec["pitched"],
                   "transpose": spec["transpose"], "midi": spec["midi"],
                   "easy": spec["easy"]}
            for name, spec in INSTRUMENT_SPECS.items()
        },
    }


# Flattened value sets used for validation.
GENDER_VALUES = set(GENDERS)
AFFILIATION_VALUES = {item["value"] for item in AFFILIATIONS}
INSTRUMENT_VALUES = {name for group in INSTRUMENTS for name in group["options"]}
GENRE_VALUES = set(GENRES)
NATIONALITY_VALUES = set(NATIONALITIES)

ENSEMBLE_GROUP_VALUES = {item["value"] for item in ENSEMBLE_GROUPS}
LEVEL_VALUES = {item["value"] for item in SKILL_LEVELS}
MOOD_VALUES = {item["value"] for item in MOODS}
SCORE_INSTRUMENT_VALUES = set(INSTRUMENT_SPECS)

ENSEMBLE_GROUPS_BY_VALUE = {item["value"]: item for item in ENSEMBLE_GROUPS}
LEVELS_BY_VALUE = {item["value"]: item for item in SKILL_LEVELS}
MOODS_BY_VALUE = {item["value"]: item for item in MOODS}


# What each family is actually asked to do, by level. The general level text
# above sets the standard; these name the techniques, because "make it harder"
# alone produces a faster version of the same easy line. Drawn from what
# conservatory juries and auditions require of each instrument.
FAMILY_OF = {name: group["group"] for group in SCORE_INSTRUMENTS for name in group["options"]}

TECHNIQUE = {
    "Strings": {
        "beginner": "First position only, separate bows and simple slurs of two notes.",
        "intermediate": "Up to third position; simple double stops on an open string; "
                        "slurs of up to four notes; spiccato at moderate tempo.",
        "higher-intermediate": "Up to seventh position with shifts in the line; double "
                               "stops in thirds and sixths; fast string crossings; "
                               "spiccato and sautillé sixteenths; slurred runs of "
                               "eight notes.",
        "pro": "The whole fingerboard up to the top of the E string; double stops in "
               "thirds, sixths and octaves; three- and four-note chords at cadences; "
               "rapid scale and arpeggio runs in sixteenths and thirty-seconds across "
               "all four strings; ricochet, natural harmonics; expressive high "
               "cantabile on the G string.",
    },
    "Woodwinds": {
        "beginner": "The first octave learnt; slurs and tonguing on quarters and eighths.",
        "intermediate": "Crossing the break freely; sixteenth-note runs at moderate tempo.",
        "higher-intermediate": "The full range; fast single tonguing; slurred leaps of "
                               "an octave or more; chromatic runs.",
        "pro": "The full range including the top register; double and triple tonguing; "
               "rapid chromatic and arpeggiated runs; wide slurred leaps; trills.",
    },
    "Brass": {
        "beginner": "The middle register; short phrases with room to breathe.",
        "intermediate": "Lip slurs within the harmonic series; sixteenths at moderate tempo.",
        "higher-intermediate": "The upper register at climaxes; fast tonguing; wide "
                               "lip slurs; fanfare figures.",
        "pro": "The full range including the top; double tonguing; flexible lip slurs "
               "across wide intervals; sustained high lyrical playing.",
    },
    "Percussion": {
        "beginner": "Steady quarters and eighths.",
        "intermediate": "Sixteenths, accents and simple rolls.",
        "higher-intermediate": "Syncopated patterns, rolls with dynamics, fills.",
        "pro": "Complex syncopation, rudiments, dynamic rolls and fills, "
               "four-mallet chords on mallet instruments.",
    },
    "Voice": {
        "beginner": "A narrow, comfortable range; stepwise lines.",
        "intermediate": "An octave and a fourth; simple melismas.",
        "higher-intermediate": "The full range; melismas and wider leaps.",
        "pro": "The full range; coloratura runs, sustained high notes, wide leaps.",
    },
}
