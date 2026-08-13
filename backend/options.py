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


def as_dict() -> dict:
    """The payload served by ``GET /api/options``."""
    return {
        "genders": GENDERS,
        "affiliations": AFFILIATIONS,
        "instruments": INSTRUMENTS,
        "genres": GENRES,
        "nationalities": NATIONALITIES,
    }


# Flattened value sets used for validation.
GENDER_VALUES = set(GENDERS)
AFFILIATION_VALUES = {item["value"] for item in AFFILIATIONS}
INSTRUMENT_VALUES = {name for group in INSTRUMENTS for name in group["options"]}
GENRE_VALUES = set(GENRES)
NATIONALITY_VALUES = set(NATIONALITIES)
