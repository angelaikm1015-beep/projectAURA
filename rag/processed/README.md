# Project AURA — RAG corpus (orchestration)

Source documents converted from `.docx` into retrieval-ready text. Built for the AURA music model's two known problems (from *Project Aura 2026_10_25 RAG*):

1. **Instruments don't harmonize with each other.** Each part has a good melody, but together they don't sound like one texture. → mainly *Rimsky-Korsakov*: chord distribution, doubling, juxtaposition, overlaying/crossing/enclosure of parts, combined groups.
2. **Individual instrument timbres sound unrealistic.** → mainly *Berlioz*: range, mechanism, tone quality, and character of every instrument.

## Files

| File | What it is |
|---|---|
| `all_chunks.jsonl` | **Use this for indexing.** Both books, 437 chunks, one JSON object per line |
| `berlioz.chunks.jsonl` | Berlioz only (250 chunks) |
| `rimsky.chunks.jsonl` | Rimsky-Korsakov only (187 chunks) |
| `berlioz.md`, `rimsky.md` | Clean full text with heading structure, for reading or for re-chunking with your own splitter |
| `convert.py` | The script that produced all of the above (`python3 convert.py <book.docx> <out_dir>`) |

## Chunk schema

```json
{
  "id": "rimsky-0109",
  "source_id": "rimsky",
  "title": "Principles of Orchestration",
  "author": "Nikolay Rimsky-Korsakov",
  "edition": "ed. Steinberg, trans. Agate (1922); Project Gutenberg #33900",
  "chapter": "Chapter III — Harmony",
  "section": "Harmony in combined groups",
  "subsection": "A. Combination of wind and brass — 2. Overlaying (superposition), crossing, enclosure of parts",
  "heading_path": ["Principles of Orchestration", "Chapter III — Harmony", "..."],
  "part": "1/3",
  "instruments": ["horn", "bassoon", "trumpet", "clarinet"],
  "topics": ["harmony & chord voicing", "blend & combination of timbres", "doubling"],
  "has_musical_example": true,
  "char_count": 2227,
  "text": "[Principles of Orchestration > Chapter III — Harmony > ...]\n\nIt has already been stated that the bassoon and horn...",
  "content": "It has already been stated that the bassoon and horn..."
}
```

- **Embed `text`.** It begins with the heading path, so a chunk like "the former case…" still carries "Rimsky > Harmony > wind + brass" in its vector. `content` is the same text without the header, for showing in the prompt.
- **Filter with metadata.** For example, for a harmonization prompt, restrict to `topics ∋ "harmony & chord voicing" | "doubling" | "blend & combination of timbres"`. For a timbre prompt, filter on `instruments ∋ "<instrument being generated>"`.
- Chunks are split on section and paragraph boundaries (target ~2,200 characters, ~550 tokens; max ~3,200), with one paragraph of overlap between consecutive chunks of the same section. A chunk never crosses a section boundary.
- `instruments` and `topics` are keyword-derived (ranked by frequency), not hand-labelled. Treat them as filters to boost recall, not as ground truth.

## Known limitations

- **Musical notation is not included.** Both books use images for their score examples. Each one is marked `[musical example — notation not included]`, so the surrounding prose ("write it thus: —") still reads correctly.
- **Berlioz is OCR text** from an 1882 scan. Prose is clean enough for retrieval, but note names and accidentals are often garbled (`Bt?` / `E\?` = B♭ / E♭). Figure labels and lyric fragments from the examples were removed as noise. The section map (`B_ANCHORS` in `convert.py`) was set by hand, because the scan's headings were unreliable.
- **Rimsky-Korsakov Volume II** (the 312 score examples) has no prose, so it is included only as an index chunk, "No. N → work (page)". This lets the model resolve the example numbers cited in the text.

## Loading example (LangChain)

```python
import json
from langchain_core.documents import Document

docs = []
for line in open("all_chunks.jsonl", encoding="utf-8"):
    c = json.loads(line)
    meta = {k: v for k, v in c.items() if k not in ("text", "content")}
    meta = {k: (", ".join(v) if isinstance(v, list) else v) for k, v in meta.items()}  # most vector DBs want flat metadata
    docs.append(Document(page_content=c["text"], metadata=meta))
```
