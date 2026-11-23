"""What the orchestration textbooks say, put in front of the model.

rag/processed/all_chunks.jsonl is Rimsky-Korsakov's *Principles of
Orchestration* and Berlioz's *Treatise on Instrumentation*, cut into ~500-token
passages with their heading path, the instruments they discuss and their topics
(see rag/processed/README.md). The model already knows of these books; what it
doesn't do unprompted is follow them — how a chord is distributed across the
strings, which winds blend, how to double a line without muddying it. So each
prompt that writes or plans music gets the few passages that bear on it.

Retrieval is lexical (BM25 over the passage text) with boosts from the
metadata — the topics a call needs and the instruments actually in the
ensemble — and a preference for the chapters that are about orchestrating
rather than about one instrument's mechanism. That needs no embedding service
and no index to build: the corpus is small enough to score in memory, and the
same request always retrieves the same passages.
"""

import json
import logging
import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__)

CORPUS = Path(os.environ.get(
    "RAG_CORPUS",
    Path(__file__).resolve().parent.parent / "rag" / "processed" / "all_chunks.jsonl"))
PASSAGE_CHARS = 1400        # the most of one passage put in a prompt
EXAMPLE_RE = re.compile(r"\s*(and not:\s*)?\[musical example — notation not included\]\s*")
WORD_RE = re.compile(r"[a-z]+")
STOP = set("the a an and or of to in is it be by as at on for with that this which are "
           "may can not from its their his been has have was were will would these those "
           "than then so but if such also only more most when where one two".split())

# The app's instruments, in the corpus's vocabulary.
ALIASES = {
    "Violin": "violin", "Viola": "viola", "Cello": "violoncello",
    "Double bass": "double bass", "Harp": "harp", "Acoustic guitar": "guitar",
    "Electric guitar": "guitar", "Bass guitar": "guitar", "Ukulele": "guitar",
    "Flute": "flute", "Piccolo": "flute", "Clarinet": "clarinet", "Bass clarinet": "clarinet",
    "Oboe": "oboe", "English horn": "english horn", "Bassoon": "bassoon",
    "Alto saxophone": "saxophone", "Tenor saxophone": "saxophone",
    "Baritone saxophone": "saxophone", "Recorder": "flute", "Trumpet": "trumpet",
    "French horn": "horn", "Trombone": "trombone", "Euphonium": "tuba", "Tuba": "tuba",
    "Timpani": "timpani", "Marimba": "percussion", "Xylophone": "percussion",
    "Vibraphone": "percussion", "Snare drum": "percussion", "Drum set": "percussion",
    "Hand percussion": "percussion", "Piano": "piano", "Organ": "organ",
    "Harpsichord": "piano", "Synthesizer": "organ", "Accordion": "organ",
    "Soprano": "voice", "Alto (voice)": "voice", "Tenor (voice)": "voice",
    "Bass (voice)": "voice",
}
FAMILY_WORDS = {"Strings": "string strings", "Woodwinds": "wood wind woodwind",
                "Brass": "brass", "Percussion": "percussion", "Keyboard": "piano",
                "Voice": "voice chorus"}
# The chapters about orchestrating — combining, doubling, voicing — count for
# more than those about one instrument's mechanism; prefaces and indexes for none.
CHAPTER_WEIGHT = {"Chapter III": 1.6, "Chapter IV": 1.35, "Chapter II": 1.25, "Chapter I": 1.0}
SKIP_CHAPTERS = ("Prefaces", "Footnotes", "Volume II", "Index")

HARMONY = ("harmony & chord voicing", "doubling", "blend & combination of timbres")
MELODY = ("melody", "doubling", "blend & combination of timbres")
TEXTURE = ("accompaniment", "balance & dynamics", "harmony & chord voicing")


@lru_cache(maxsize=1)
def _corpus():
    """[(chunk, Counter of words, length)] and document frequencies, or ([], {})."""
    try:
        with open(CORPUS, encoding="utf-8") as handle:
            chunks = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError) as error:
        _log.warning("Orchestration corpus unavailable (%s): prompts go without it", error)
        return [], {}, 1.0
    rows = []
    frequency = Counter()
    for chunk in chunks:
        if any(chunk.get("chapter", "").startswith(skip) for skip in SKIP_CHAPTERS):
            continue
        words = Counter(w for w in WORD_RE.findall(chunk.get("text", "").lower())
                        if w not in STOP and len(w) > 2)
        rows.append((chunk, words, sum(words.values())))
        frequency.update(words.keys())
    average = sum(length for _, _, length in rows) / max(1, len(rows))
    return rows, frequency, average


def _bm25(words: Counter, length: int, query: list, frequency: dict, total: int,
          average: float, k1: float = 1.4, b: float = 0.75) -> float:
    score = 0.0
    for term in query:
        tf = words.get(term, 0)
        if not tf:
            continue
        idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
        score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
    return score


VOICE_CHAPTERS = ("Chapter V", "Chapter VI")


def search(query: str, instruments=(), topics=(), limit: int = 4, source: str = None) -> list:
    """The ``limit`` best passages: [chunk], at most one per section.

    ``source`` limits it to one book ('rimsky' or 'berlioz'). The chapters on
    voices are left out unless voices are among ``instruments``.
    """
    rows, frequency, average = _corpus()
    if not rows:
        return []
    terms = [w for w in WORD_RE.findall(query.lower()) if w not in STOP and len(w) > 2]
    wanted = set(instruments)
    scored = []
    for chunk, words, length in rows:
        if source and chunk.get("source_id") != source:
            continue
        if "voice" not in wanted and chunk.get("chapter", "").startswith(VOICE_CHAPTERS):
            continue
        score = _bm25(words, length, terms, frequency, len(rows), average)
        score *= 1 + 0.35 * len(set(chunk.get("topics") or []) & set(topics))
        score *= 1 + 0.15 * min(3, len(set(chunk.get("instruments") or []) & wanted))
        chapter = chunk.get("chapter", "")
        score *= next((w for key, w in CHAPTER_WEIGHT.items() if chapter.startswith(key)), 1.0)
        if chunk.get("source_id") == "berlioz":
            score *= 0.9          # OCR text: as useful, a little noisier
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: -pair[0])
    out, sections = [], set()
    for _, chunk in scored:
        key = (chunk.get("source_id"), chunk.get("section"))
        if key in sections:
            continue
        sections.add(key)
        out.append(chunk)
        if len(out) == limit:
            break
    return out


# Berlioz on how each instrument sounds — chosen by reading, because the
# passages on character are mixed in with key tables and mechanism, and his
# 1882 scan's OCR garbles some of them. An instrument not listed goes without.
BERLIOZ_VOICE = {
    "Violin": "berlioz-0022", "Viola": "berlioz-0025", "Cello": "berlioz-0033",
    "Flute": "berlioz-0086", "Piccolo": "berlioz-0086", "Recorder": "berlioz-0086",
    "Oboe": "berlioz-0064", "Clarinet": "berlioz-0075", "Bass clarinet": "berlioz-0075",
    "Bassoon": "berlioz-0068", "French horn": "berlioz-0108", "Harp": "berlioz-0047",
    "Timpani": "berlioz-0171", "Trombone": "berlioz-0122",
}


def voice_of(instrument: str):
    """Berlioz's passage on how ``instrument`` sounds, or None."""
    wanted = BERLIOZ_VOICE.get(instrument)
    rows, _, _ = _corpus()
    return next((chunk for chunk, _, _ in rows if chunk.get("id") == wanted), None) \
        if wanted else None


def _clean(content: str) -> str:
    text = EXAMPLE_RE.sub(" ", content or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= PASSAGE_CHARS:
        return text
    cut = text.rfind(". ", 0, PASSAGE_CHARS)
    return text[:cut + 1 if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS].rstrip() + " …"


def _where(chunk: dict) -> str:
    author = "Rimsky-Korsakov" if chunk.get("source_id") == "rimsky" else "Berlioz"
    place = " > ".join(x for x in (chunk.get("chapter"), chunk.get("section"),
                                   chunk.get("subsection")) if x)
    return f"{author}, {place}"


def _ensemble_words(roster: list) -> tuple:
    import options                  # here: keeps this module importable on its own
    names = sorted({ALIASES.get(p["instrument"], "") for p in roster} - {""})
    families = sorted({options.FAMILY_OF.get(p["instrument"], "") for p in roster} - {""})
    words = " ".join(names + [FAMILY_WORDS.get(f, "") for f in families])
    return names, words


def guidance(roster: list, focus: str, limit: int = 4, topics=HARMONY, featured=()) -> list:
    """Prompt lines: the passages on ``focus`` for this ensemble, with where each is from.

    ``featured``: instruments (the app's names) carrying the tune, for which a
    passage of Berlioz on that instrument's voice is added.

    ``focus`` is the plain-words question, e.g. "harmony chord distribution
    doubling combined groups sustained". Returns [] if the corpus isn't there.
    """
    names, words = _ensemble_words(roster)
    found = search(f"{focus} {words}", instruments=names, topics=topics, limit=limit,
                   source="rimsky")
    # Berlioz on the instruments that carry the tune here: how they sing.
    for instrument in dict.fromkeys(featured):
        chunk = voice_of(instrument)
        if chunk:
            found.append(chunk)
    if not found:
        return []
    lines = ["FROM THE ORCHESTRATION TEXTBOOKS — the practice to follow (Rimsky-Korsakov, "
             "Principles of Orchestration; Berlioz, Treatise on Instrumentation):"]
    for chunk in found:
        lines += [f"  [{_where(chunk)}]",
                  *[f"    {line}" for line in _clean(chunk.get("content")).splitlines()
                    if line.strip()]]
    return lines
