/* GENERATED FILE — do not edit by hand.
 *
 * Source:     backend/options.py
 * Regenerate: python backend/generate_options.py
 *
 * Bundled so the signup dropdowns render instantly and still work when the Flask
 * API is unreachable. SignupPage refreshes from GET /api/options when it can, so
 * a running server always wins.
 */

export const OPTIONS = {
  "genders": [
    "Female",
    "Male",
    "Non-binary",
    "Other",
    "Prefer not to say"
  ],
  "affiliations": [
    {
      "value": "middle-school",
      "label": "Middle school"
    },
    {
      "value": "high-school",
      "label": "High school"
    },
    {
      "value": "college",
      "label": "College / University"
    },
    {
      "value": "hobby",
      "label": "Just playing for fun (hobby)"
    }
  ],
  "instruments": [
    {
      "group": "Strings",
      "options": [
        "Violin",
        "Viola",
        "Cello",
        "Double bass",
        "Harp",
        "Acoustic guitar",
        "Electric guitar",
        "Bass guitar",
        "Ukulele"
      ]
    },
    {
      "group": "Woodwinds",
      "options": [
        "Flute",
        "Piccolo",
        "Clarinet",
        "Bass clarinet",
        "Oboe",
        "English horn",
        "Bassoon",
        "Saxophone",
        "Recorder"
      ]
    },
    {
      "group": "Brass",
      "options": [
        "Trumpet",
        "French horn",
        "Trombone",
        "Euphonium",
        "Tuba"
      ]
    },
    {
      "group": "Percussion",
      "options": [
        "Snare drum",
        "Drum set",
        "Timpani",
        "Marimba",
        "Xylophone",
        "Vibraphone",
        "Hand percussion"
      ]
    },
    {
      "group": "Keyboard",
      "options": [
        "Piano",
        "Organ",
        "Harpsichord",
        "Synthesizer",
        "Accordion"
      ]
    },
    {
      "group": "Voice & other",
      "options": [
        "Voice / Vocals",
        "Traditional instrument",
        "Other"
      ]
    }
  ],
  "genres": [
    "Classical",
    "Jazz",
    "Blues",
    "Rock",
    "Pop",
    "Hip-hop / Rap",
    "R&B / Soul",
    "Electronic / EDM",
    "Folk / Acoustic",
    "Country",
    "Latin",
    "Metal",
    "Punk",
    "Reggae",
    "K-pop",
    "J-pop",
    "Musical theatre",
    "Film & game score",
    "Gospel / Worship",
    "World / Traditional",
    "Other"
  ],
  "nationalities": [
    "Afghanistan",
    "Albania",
    "Algeria",
    "Andorra",
    "Angola",
    "Antigua and Barbuda",
    "Argentina",
    "Armenia",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahamas",
    "Bahrain",
    "Bangladesh",
    "Barbados",
    "Belarus",
    "Belgium",
    "Belize",
    "Benin",
    "Bhutan",
    "Bolivia",
    "Bosnia and Herzegovina",
    "Botswana",
    "Brazil",
    "Brunei",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cambodia",
    "Cameroon",
    "Canada",
    "Central African Republic",
    "Chad",
    "Chile",
    "China",
    "Colombia",
    "Comoros",
    "Congo (Brazzaville)",
    "Congo (Kinshasa)",
    "Costa Rica",
    "Côte d'Ivoire",
    "Croatia",
    "Cuba",
    "Cyprus",
    "Czechia",
    "Denmark",
    "Djibouti",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "Egypt",
    "El Salvador",
    "Equatorial Guinea",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Fiji",
    "Finland",
    "France",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Greece",
    "Grenada",
    "Guatemala",
    "Guinea",
    "Guinea-Bissau",
    "Guyana",
    "Haiti",
    "Honduras",
    "Hungary",
    "Iceland",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Ireland",
    "Israel",
    "Italy",
    "Jamaica",
    "Japan",
    "Jordan",
    "Kazakhstan",
    "Kenya",
    "Kiribati",
    "Kuwait",
    "Kyrgyzstan",
    "Laos",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Madagascar",
    "Malawi",
    "Malaysia",
    "Maldives",
    "Mali",
    "Malta",
    "Marshall Islands",
    "Mauritania",
    "Mauritius",
    "Mexico",
    "Micronesia",
    "Moldova",
    "Monaco",
    "Mongolia",
    "Montenegro",
    "Morocco",
    "Mozambique",
    "Myanmar",
    "Namibia",
    "Nauru",
    "Nepal",
    "Netherlands",
    "New Zealand",
    "Nicaragua",
    "Niger",
    "Nigeria",
    "North Korea",
    "North Macedonia",
    "Norway",
    "Oman",
    "Pakistan",
    "Palau",
    "Palestine",
    "Panama",
    "Papua New Guinea",
    "Paraguay",
    "Peru",
    "Philippines",
    "Poland",
    "Portugal",
    "Qatar",
    "Romania",
    "Russia",
    "Rwanda",
    "Saint Kitts and Nevis",
    "Saint Lucia",
    "Saint Vincent and the Grenadines",
    "Samoa",
    "San Marino",
    "Sao Tome and Principe",
    "Saudi Arabia",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Singapore",
    "Slovakia",
    "Slovenia",
    "Solomon Islands",
    "Somalia",
    "South Africa",
    "South Korea",
    "South Sudan",
    "Spain",
    "Sri Lanka",
    "Sudan",
    "Suriname",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Tajikistan",
    "Tanzania",
    "Thailand",
    "Timor-Leste",
    "Togo",
    "Tonga",
    "Trinidad and Tobago",
    "Tunisia",
    "Turkey",
    "Turkmenistan",
    "Tuvalu",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United Kingdom",
    "United States",
    "Uruguay",
    "Uzbekistan",
    "Vanuatu",
    "Vatican City",
    "Venezuela",
    "Vietnam",
    "Yemen",
    "Zambia",
    "Zimbabwe"
  ],
  "scoreInstruments": [
    {
      "group": "Strings",
      "options": [
        "Violin",
        "Viola",
        "Cello",
        "Double bass",
        "Harp",
        "Acoustic guitar",
        "Electric guitar",
        "Bass guitar",
        "Ukulele"
      ]
    },
    {
      "group": "Woodwinds",
      "options": [
        "Flute",
        "Piccolo",
        "Clarinet",
        "Bass clarinet",
        "Oboe",
        "English horn",
        "Bassoon",
        "Alto saxophone",
        "Tenor saxophone",
        "Baritone saxophone",
        "Recorder"
      ]
    },
    {
      "group": "Brass",
      "options": [
        "Trumpet",
        "French horn",
        "Trombone",
        "Euphonium",
        "Tuba"
      ]
    },
    {
      "group": "Percussion",
      "options": [
        "Timpani",
        "Marimba",
        "Xylophone",
        "Vibraphone",
        "Snare drum",
        "Drum set",
        "Hand percussion"
      ]
    },
    {
      "group": "Keyboard",
      "options": [
        "Piano",
        "Organ",
        "Harpsichord",
        "Synthesizer",
        "Accordion"
      ]
    },
    {
      "group": "Voice",
      "options": [
        "Soprano",
        "Alto (voice)",
        "Tenor (voice)",
        "Bass (voice)"
      ]
    }
  ],
  "ensembleGroups": [
    {
      "value": "elementary-school",
      "label": "Elementary school",
      "guidance": "Very short rehearsal attention span. Unison or two parts at most, constant doubling, nothing faster than eighth notes."
    },
    {
      "value": "middle-school",
      "label": "Middle school",
      "guidance": "Roughly 2-4 rehearsals. Keep every part playable at sight after one read-through; double exposed lines so no one is left alone."
    },
    {
      "value": "high-school",
      "label": "High school",
      "guidance": "Can handle real independence between parts and a short exposed solo, but keep page turns and endurance in mind."
    },
    {
      "value": "college",
      "label": "College / University",
      "guidance": "Music majors. Their juries already demand three-octave scales and arpeggios in every key, double stops in thirds, sixths and octaves, a virtuoso étude and a concerto movement; their pianists audition with Chopin and Liszt études at full tempo. Write at that standard — a piece they could sight-read in one pass is too easy."
    },
    {
      "value": "community",
      "label": "Community / amateur ensemble",
      "guidance": "Wide spread of ability inside one group. Write a secure, doubled core and put the difficulty in optional divisi rather than in everyone's part."
    },
    {
      "value": "hobby",
      "label": "Friends playing for fun (hobby)",
      "guidance": "No conductor culture and little rehearsal. Favour a memorable tune, clear repeats, and parts that lock in by ear."
    },
    {
      "value": "professional",
      "label": "Professional ensemble",
      "guidance": "Orchestral and chamber professionals: one rehearsal, sight-read to a high standard. Write idiomatically and at full virtuoso difficulty, without hand-holding, and give every section something worth practising."
    }
  ],
  "levels": [
    {
      "value": "beginner",
      "label": "Beginner",
      "years": "under 1 year",
      "guidance": "Keys of C, G, F or D major. Time in 4/4, 3/4 or 2/4 at a steady tempo. Whole, half, quarter and eighth notes, plus the dotted-quarter-eighth figure and simple ties. Stay inside the easy range. Stepwise motion with leaps up to a sixth. Strings stay in first position; winds stay in the first octave they learn. Whole-bar rests so players can find their place. Slurs, staccato and three dynamic levels are all fair — a first-year player can shape a phrase even if they cannot play fast."
    },
    {
      "value": "intermediate",
      "label": "Intermediate",
      "years": "1 – 3 years",
      "guidance": "Up to four sharps or flats, and accidentals outside the key. Sustained sixteenth-note passages at a moderate tempo, dotted rhythms, triplets, syncopation and ties across barlines. One meter change is fine. Mostly the easy range, reaching into the full range at climaxes. Leaps up to an octave. Strings use third position and simple double stops; winds cross the break freely. Independent lines rather than constant doubling, short exposed entries, and the full articulation vocabulary from pp to ff."
    },
    {
      "value": "higher-intermediate",
      "label": "Higher intermediate",
      "years": "4 – 7 years",
      "guidance": "Conservatory-audition standard — Henle difficulty 5-6, the level of a Brahms violin sonata or a Kreutzer étude. Any key, chromatic harmony and modulation. Fast sixteenth-note passagework that has to be practised, dotted and syncopated rhythm, triplets against duple, hemiola. The full written range including the top of it. Independent lines rather than doubling. Strings: up to seventh position, double stops, spiccato and fast string crossings. Winds and brass: the whole range, fast tonguing, wide slurred intervals. Piano: both hands busy — broken chords and scale runs in sixteenths, octaves. Everything articulated and shaped."
    },
    {
      "value": "pro",
      "label": "Pro",
      "years": "8+ years",
      "guidance": "Virtuoso standard — Henle difficulty 7-8: a Paganini caprice, the Beethoven violin concerto, a Chopin or Liszt concert étude. No caution. Chromatic and extended harmony, real modulation, rubato. Rapid sixteenth- and thirty-second-note runs, arpeggios across the whole instrument, wide leaps, the extremes of range and dynamic, cross-rhythm. Strings: high positions, double stops in thirds, sixths and octaves, chords, ricochet and harmonics. Winds: double tonguing, altissimo, rapid chromatic runs. Write as for paid players who will read it once and perform it."
    }
  ],
  "moods": [
    {
      "value": "happy",
      "label": "Happy",
      "guidance": "Bright major key, brisk lilting tempo, light detached articulation, rising melodic contours."
    },
    {
      "value": "joyful",
      "label": "Joyful / celebratory",
      "guidance": "Major key, fanfare gestures, full textures, driving rhythm, accents on strong beats."
    },
    {
      "value": "playful",
      "label": "Playful",
      "guidance": "Staccato, unexpected rests and hiccups, call-and-response between parts, sudden dynamic drops."
    },
    {
      "value": "peaceful",
      "label": "Peaceful / calm",
      "guidance": "Slow tempo, long slurred lines, sustained harmony, narrow dynamic range, gentle suspensions."
    },
    {
      "value": "hopeful",
      "label": "Hopeful",
      "guidance": "Starts sparse and low, gradually adds parts and rises in register; suspensions resolving upward."
    },
    {
      "value": "nostalgic",
      "label": "Nostalgic",
      "guidance": "Moderate tempo, warm middle register, a simple tune that returns changed, gentle chromatic colouring."
    },
    {
      "value": "romantic",
      "label": "Romantic / tender",
      "guidance": "Singing melody over flowing accompaniment, rubato, expressive appoggiaturas, rich seventh chords."
    },
    {
      "value": "sad",
      "label": "Sad",
      "guidance": "Minor key, slow tempo, descending lines, sighing two-note slurs, sparse texture, soft dynamics."
    },
    {
      "value": "longing",
      "label": "Longing / yearning",
      "guidance": "Minor or modal, phrases that reach up and fall back, unresolved harmony, wide expressive leaps."
    },
    {
      "value": "mysterious",
      "label": "Mysterious",
      "guidance": "Whole-tone or modal colour, quiet sustained low notes, ambiguous harmony, unpredictable phrase lengths."
    },
    {
      "value": "tense",
      "label": "Tense / suspenseful",
      "guidance": "Repeated ostinato, dissonant held chords, tremolo, crescendo that never resolves, irregular accents."
    },
    {
      "value": "angry",
      "label": "Angry",
      "guidance": "Minor key, fast and forceful, heavy accents, sharp dissonance, driving repeated rhythms, loud dynamics, abrupt cut-offs."
    },
    {
      "value": "dark",
      "label": "Dark / ominous",
      "guidance": "Low register, minor mode, slow tread, chromatic descent, half-step motion in the bass."
    },
    {
      "value": "epic",
      "label": "Epic / heroic",
      "guidance": "Broad tempo, brass-led fanfares, wide leaps, pedal tones, big unison statements of the theme."
    },
    {
      "value": "dreamy",
      "label": "Dreamy",
      "guidance": "Floating harmony, soft dynamics, arpeggiated accompaniment, blurred cadences, upper register."
    },
    {
      "value": "energetic",
      "label": "Energetic / driving",
      "guidance": "Fast tempo, relentless rhythmic pulse, syncopation, short punchy motifs traded between parts."
    }
  ],
  "instrumentInfo": {
    "Violin": {
      "clef": "treble",
      "maxParts": 4,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 40,
      "easy": "G3-D5"
    },
    "Viola": {
      "clef": "alto",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 41,
      "easy": "C3-A4"
    },
    "Cello": {
      "clef": "bass",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 42,
      "easy": "C2-A3"
    },
    "Double bass": {
      "clef": "bass",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -12,
      "midi": 43,
      "easy": "E2-G3"
    },
    "Harp": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 46,
      "easy": "C3-C6"
    },
    "Acoustic guitar": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -12,
      "midi": 24,
      "easy": "E3-G5"
    },
    "Electric guitar": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -12,
      "midi": 27,
      "easy": "E3-G5"
    },
    "Bass guitar": {
      "clef": "bass",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": -12,
      "midi": 33,
      "easy": "E2-G3"
    },
    "Ukulele": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 24,
      "easy": "C4-A5"
    },
    "Flute": {
      "clef": "treble",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 73,
      "easy": "C4-G5"
    },
    "Piccolo": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": 12,
      "midi": 72,
      "easy": "D4-G5"
    },
    "Clarinet": {
      "clef": "treble",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": -2,
      "midi": 71,
      "easy": "E3-C5"
    },
    "Bass clarinet": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": -14,
      "midi": 71,
      "easy": "E3-C5"
    },
    "Oboe": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 68,
      "easy": "C4-D5"
    },
    "English horn": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": -7,
      "midi": 69,
      "easy": "D4-D5"
    },
    "Bassoon": {
      "clef": "bass",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 70,
      "easy": "F2-C4"
    },
    "Alto saxophone": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -9,
      "midi": 65,
      "easy": "C4-C5"
    },
    "Tenor saxophone": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -14,
      "midi": 66,
      "easy": "C4-C5"
    },
    "Baritone saxophone": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": -21,
      "midi": 67,
      "easy": "C4-C5"
    },
    "Recorder": {
      "clef": "treble",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": 12,
      "midi": 74,
      "easy": "C4-D5"
    },
    "Trumpet": {
      "clef": "treble",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": -2,
      "midi": 56,
      "easy": "G3-D5"
    },
    "French horn": {
      "clef": "treble",
      "maxParts": 4,
      "staves": 1,
      "pitched": true,
      "transpose": -7,
      "midi": 60,
      "easy": "G3-D5"
    },
    "Trombone": {
      "clef": "bass",
      "maxParts": 3,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 57,
      "easy": "F2-Bb3"
    },
    "Euphonium": {
      "clef": "bass",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 58,
      "easy": "F2-Bb3"
    },
    "Tuba": {
      "clef": "bass",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 58,
      "easy": "F1-F3"
    },
    "Timpani": {
      "clef": "bass",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 47,
      "easy": "F2-C4"
    },
    "Marimba": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 12,
      "easy": "C4-C6"
    },
    "Xylophone": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": 12,
      "midi": 13,
      "easy": "C4-C6"
    },
    "Vibraphone": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 11,
      "easy": "C4-C6"
    },
    "Snare drum": {
      "clef": "perc",
      "maxParts": 1,
      "staves": 1,
      "pitched": false,
      "transpose": 0,
      "midi": 38,
      "easy": "-"
    },
    "Drum set": {
      "clef": "perc",
      "maxParts": 1,
      "staves": 1,
      "pitched": false,
      "transpose": 0,
      "midi": 0,
      "easy": "-"
    },
    "Hand percussion": {
      "clef": "perc",
      "maxParts": 2,
      "staves": 1,
      "pitched": false,
      "transpose": 0,
      "midi": 0,
      "easy": "-"
    },
    "Piano": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 0,
      "easy": "C3-C6"
    },
    "Organ": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 19,
      "easy": "C3-C6"
    },
    "Harpsichord": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 6,
      "easy": "C3-C6"
    },
    "Synthesizer": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 81,
      "easy": "C3-C6"
    },
    "Accordion": {
      "clef": "treble",
      "maxParts": 1,
      "staves": 2,
      "pitched": true,
      "transpose": 0,
      "midi": 21,
      "easy": "C3-C6"
    },
    "Soprano": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 52,
      "easy": "D4-D5"
    },
    "Alto (voice)": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 52,
      "easy": "A3-C5"
    },
    "Tenor (voice)": {
      "clef": "treble",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": -12,
      "midi": 52,
      "easy": "D4-D5"
    },
    "Bass (voice)": {
      "clef": "bass",
      "maxParts": 2,
      "staves": 1,
      "pitched": true,
      "transpose": 0,
      "midi": 52,
      "easy": "A2-C4"
    }
  }
};
