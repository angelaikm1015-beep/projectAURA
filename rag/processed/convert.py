"""Convert the two orchestration treatises (.docx) into RAG-ready Markdown + JSONL chunks."""
import docx, re, json, sys, hashlib
from docx.table import Table
from docx.text.paragraph import Paragraph

SRC = sys.argv[1]
OUT = sys.argv[2]
IMG = "[musical example — notation not included]"

# ---------------------------------------------------------------- helpers
def has_image(p):
    return bool(p._p.xpath('.//*[local-name()="drawing" or local-name()="pict"]'))

def iter_block(doc):
    for el in doc.element.body.iterchildren():
        tag = el.tag.split('}')[1]
        if tag == 'p':
            yield Paragraph(el, doc)
        elif tag == 'tbl':
            yield Table(el, doc)

def table_md(t):
    rows = []
    for r in t.rows:
        cells, seen = [], set()
        for c in r.cells:
            if id(c._tc) in seen:
                continue
            seen.add(id(c._tc))
            cells.append(tidy(re.sub(r'\s+', ' ', c.text)).replace('|', '/'))
        if any(cells):
            rows.append(cells)
    alpha = sum(len(re.sub(r'[^A-Za-z]', '', x)) for r in rows for x in r)
    if not rows or alpha < 40:
        words = ' '.join(x for r in rows for x in r if x).strip()
        return (words + ' ' + IMG).strip() if words else IMG
    w = max(len(r) for r in rows)
    rows = [r + [''] * (w - len(r)) for r in rows]
    out = ['| ' + ' | '.join(rows[0]) + ' |', '|' + '---|' * w]
    out += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
    return '\n'.join(out)

def tidy(t):
    t = t.replace('​', '').replace('\xa0', ' ')
    t = re.sub(r'\[(Listen|XML|PDF|MIDI|Enlarge)\]', '', t)
    t = re.sub(r'\s+([;:!?,])', r'\1', t)      # French-style spacing from OCR
    t = re.sub(r'([;:])(?=\S)(?!—)', r'\1 ', t)
    t = re.sub(r'[ \t]+', ' ', t)
    return t.strip()

INSTR = {
 'violin': r'violin', 'viola': r'viola|viole d.amour|alto\b(?! (clarinet|trombone|ophicleide|sax))',
 'violoncello': r'violoncell|\bcello', 'double bass': r'double.?bass|contra.?bass|octo.?bass',
 'harp': r'\bharp', 'guitar': r'guitar', 'mandolin': r'mandolin', 'piano': r'pianoforte|\bthe piano\b|\bpianos\b|piano and celesta',
 'organ': r'\borgan\b|melodium', 'flute': r'flute|piccolo', 'oboe': r'oboe|hautboy',
 'english horn': r'english horn|eng\. horn|corno inglese|cor anglais', 'clarinet': r'clarinet|corno di bassetto|basset',
 'bassoon': r'bassoon|basson|tenoroon|fagott', 'saxophone': r'saxophone|sax.?horn|saxotromba|sax.?tuba',
 'horn': r'\bhorns?\b(?! in the)', 'trumpet': r'trumpet', 'cornet': r'cornet', 'trombone': r'trombone',
 'tuba': r'\btuba|ophicleide|bombardon|serpent', 'timpani': r'kettle.?drum|timpani',
 'percussion': r'cymbal|triangle|tambourine|tambour|gong|tam.?tam|side.?drum|long drum|bass drum|glockenspiel|xylophone|celesta|\bbells?\b',
 'voice': r'\bvoices?\b|chorus|soprano|contralto|tenor\b(?! (trombone|drum|clarinet))|baritone(?! sax)|\bbass voice',
}
TOPICS = {
 'harmony & chord voicing': r'harmon|chord|voicing|distribution of notes|harmonic parts|common chord|triad',
 'doubling': r'doubl|duplicat|reinforc|in octaves|in unison',
 'blend & combination of timbres': r'blend|combin|mingl|mixture|fus|homogene|juxtapos|overlay|superpos|crossing|enclos|interlock',
 'balance & dynamics': r'balance|overpower|predominat|crescendo|diminuendo|sforzando|forte|piano\b|pianissimo|fortissimo|resonance|power',
 'timbre & tone colour': r'timbre|tone.?quality|tone.?colou?r|quality of tone|colou?r|character|expressi|sonorous|sonority',
 'range & register': r'compass|range|register|extent|octave higher|octave lower|low notes|high notes',
 'playing technique': r'bow|pizzicato|tremolo|harmonics|mute|sordin|tongu|shake|trill|legato|staccato|divisi|arpeggio|valve|piston|stopped',
 'melody': r'melod|theme|cantabile|phrase',
 'orchestra layout & size': r'number of players|full orchestra|small orchestra|tutti|desks|placed|arrangement of the orchestra',
 'conducting': r'conduct|beat|baton|time.?beating|rehears',
 'transposition': r'transpos|written a|sounds? an? (octave|fourth|fifth|tone)',
 'accompaniment': r'accompan|recitative',
}

def tags(text, table, min_hits=1):
    low = text.lower()
    hits = sorted(((k, len(re.findall(v, low))) for k, v in table.items()), key=lambda x: -x[1])
    return [k for i, (k, n) in enumerate(hits) if n >= min_hits or (i == 0 and n > 0)]

# ---------------------------------------------------------------- Berlioz
B_ANCHORS = [  # (paragraph index, part, section)
 (0, 'Front matter', 'Title and dedication'),
 (21, 'Introduction', 'Introduction'),
 (30, 'Chapter I — Classification of instruments', 'Division of the means at the composer\'s disposal'),
 (44, 'Stringed instruments played with a bow', 'The Violin'),
 (344, 'Stringed instruments played with a bow', 'The Viola'),
 (386, 'Stringed instruments played with a bow', "The Viole d'Amour"),
 (424, 'Stringed instruments played with a bow', 'The Violoncello'),
 (523, 'Stringed instruments played with a bow', 'The Double-Bass'),
 (770, 'Stringed instruments played with the hand', 'The Harp'),
 (884, 'Stringed instruments played with the hand', 'The Guitar'),
 (939, 'Stringed instruments played with the hand', 'The Mandolin'),
 (961, 'Stringed instruments with keys', 'The Pianoforte'),
 (1042, 'Wind instruments', 'Transposing and non-transposing instruments'),
 (1079, 'Wind instruments with reeds', 'The Hautboy (Oboe)'),
 (1219, 'Wind instruments with reeds', 'The Corno Inglese (English Horn)'),
 (1268, 'Wind instruments with reeds', 'The Bassoon'),
 (1336, 'Wind instruments with reeds', 'The Basson-Quinte (Tenoroon)'),
 (1347, 'Wind instruments with reeds', 'The Double-Bassoon'),
 (1355, 'Wind instruments with reeds', 'The Clarinets'),
 (1434, 'Wind instruments with reeds', 'The Alto Clarinet'),
 (1438, 'Wind instruments with reeds', 'The Bass Clarinet'),
 (1447, 'Wind instruments with reeds', 'The Corno di Bassetto'),
 (1463, 'Wind instruments without reeds', 'The Flute'),
 (1520, 'Wind instruments without reeds', 'The Piccolo Flute and other flutes'),
 (1583, 'Wind instruments with key-boards', 'The Organ'),
 (1636, 'Brass instruments with mouth-pieces', 'The Horn'),
 (1771, 'Brass instruments with mouth-pieces', 'The Horn with pistons or valves'),
 (1791, 'Brass instruments with mouth-pieces', 'The Trumpet'),
 (1855, 'Brass instruments with mouth-pieces', 'The Cornet'),
 (1930, 'Brass instruments with mouth-pieces', 'Trombones'),
 (1969, 'Brass instruments with mouth-pieces', 'The Alto Trombone'),
 (1975, 'Brass instruments with mouth-pieces', 'The Tenor Trombone'),
 (1981, 'Brass instruments with mouth-pieces', 'The Bass Trombone'),
 (2185, 'Brass instruments with mouth-pieces', 'The Alto Trombone with pistons'),
 (2193, 'Brass instruments with mouth-pieces', 'The Bugle and Keyed Bugle'),
 (2215, 'Brass instruments with mouth-pieces', 'The Ophicleides'),
 (2232, 'Brass instruments with mouth-pieces', 'The Bombardon'),
 (2240, 'Brass instruments with mouth-pieces', 'The Bass-Tuba'),
 (2247, 'Instruments with a mouth-piece, of wood', 'The Serpent'),
 (2253, 'Instruments with a mouth-piece, of wood', 'The Russian Bassoon'),
 (2258, 'Voices', 'Voices'),
 (2409, 'Instruments of percussion', 'Overview'),
 (2416, 'Instruments of percussion', 'Kettle-Drums'),
 (2490, 'Instruments of percussion', 'Bells, sets of bells and glockenspiel'),
 (2525, 'Instruments of percussion', 'The Keyed Harmonica'),
 (2533, 'Instruments of percussion', 'Ancient Cymbals'),
 (2539, 'Instruments of percussion', 'The Long Drum'),
 (2552, 'Instruments of percussion', 'Cymbals'),
 (2582, 'Instruments of percussion', 'The Gong (Tam-tam)'),
 (2585, 'Instruments of percussion', 'The Tambourine'),
 (2592, 'Instruments of percussion', 'The Drum and Side-Drum'),
 (2604, 'Instruments of percussion', 'The Triangle and Crescent'),
 (2613, 'New instruments', 'Saxophones'),
 (2632, 'New instruments', 'Sax-horns'),
 (2649, 'New instruments', 'Saxotrombas and Sax-tubas'),
 (2656, 'New instruments', 'The Concertina'),
 (2702, 'New instruments', 'The Melodium Organ'),
 (2717, 'New instruments', 'Pianos and melodiums with prolonged sound'),
 (2742, 'New instruments', 'The Octo-Bass'),
 (2752, 'The Orchestra', 'The Orchestra'),
 (2870, 'The Orchestral Conductor', 'Theory of his art'),
 (3060, None, None),  # index / illustrations list follow — dropped
]
TERM = tuple('.!?;:—"\'”’)')

def is_prose(t):
    words = re.findall(r"[A-Za-z][a-z'’-]+", t)
    toks = t.split()
    if not toks:
        return False
    ratio = len(words) / len(toks)
    if len(toks) >= 8 and ratio >= 0.6:
        return True
    if len(toks) >= 4 and ratio >= 0.75 and t.endswith(TERM) and not re.fullmatch(r"[A-Z][\w ,.'-]{0,40}", t):
        return True
    return False

def berlioz(doc):
    paras = doc.paragraphs
    sections = []
    for (a, part, sec), (b, *_ ) in zip(B_ANCHORS, B_ANCHORS[1:]):
        if part is None:
            break
        blocks = []
        for p in paras[a:b]:
            t = tidy(p.text)
            img = has_image(p)
            if t.upper() == 'MODERN INSTRUMENTATION AND ORCHESTRATION.' or t == 'Page':
                continue
            if t and (is_prose(t) or (part == 'Front matter' and len(t) > 3)):
                if blocks and blocks[-1] != IMG and not blocks[-1].endswith(TERM) and (t[0].islower() or blocks[-1].endswith('-')):
                    prev = blocks[-1]
                    blocks[-1] = (prev[:-1] if prev.endswith('-') else prev + ' ') + t
                elif len(blocks) >= 2 and blocks[-1] == IMG and not blocks[-2].endswith(TERM) and t[0].islower():
                    blocks[-1] = blocks[-2] + ' ' + IMG + ' ' + t   # sentence interrupted by an example
                    blocks.pop(-2)
                else:
                    blocks.append(t)
            if img and (not blocks or blocks[-1] != IMG):
                blocks.append(IMG)
        while blocks and blocks[0] == IMG:
            blocks.pop(0)
        sections.append({'chapter': part, 'section': sec, 'blocks': blocks})
    return sections

# ---------------------------------------------------------------- Rimsky-Korsakov
R_CHAPTERS = {
 'Chapter I': 'Chapter I — General review of orchestral groups',
 'Chapter II': 'Chapter II — Melody',
 'Chapter III': 'Chapter III — Harmony',
 'Chapter IV': 'Chapter IV — Composition of the orchestra',
 'Chapter V': 'Chapter V — Combination of the human voice with orchestra; the stage band',
 'Chapter VI (Supplementary)': 'Chapter VI (Supplementary) — Voices',
}
def rimsky(doc):
    sections, chapter, section, sub, blocks = [], 'Front matter', None, None, []
    sub2 = None
    started = ended = False
    def flush():
        if blocks:
            sections.append({'chapter': chapter, 'section': section or chapter, 'subsection': sub, 'blocks': list(blocks)})
            blocks.clear()
    examples, in_vol2 = [], False
    for el in iter_block(doc):
        if isinstance(el, Table):
            if started and not ended:
                md = table_md(el)
                if md and not (md == IMG and blocks and blocks[-1] == IMG):
                    blocks.append(md)
            continue
        p = el
        t = tidy(p.text.replace('\n', ' '))
        st = p.style.name
        if not started:
            if st.startswith('Heading 1') and t.startswith("Editor’s Preface"):
                started = True
            else:
                continue
        if t == '[VOLUME II]':
            in_vol2 = True
        if in_vol2:
            m = re.match(r'(No\. \d+[ab]?\.) (.*?)(?: No\. \d+\.?[ab]?\. „.*)?$', t)
            if st.startswith('Heading') and m:
                examples.append(f'{m.group(1)} {m.group(2).strip()}')
            continue
        if ended:
            continue
        if st.startswith('Heading'):
            lvl = int(st[-1]) if st[-1].isdigit() else 1
            if t == 'NIKOLAY RIMSKY-KORSAKOV Principles of Orchestration':
                ended = True; flush(); continue
            if not t:
                continue
            flush()
            if lvl == 1:
                if re.match(r'Chapter [IVX]+', t):
                    chapter, section, sub, sub2 = R_CHAPTERS.get(t.rstrip('.'), t.rstrip('.')), None, None, None
                elif t in ('Editor’s Preface.', 'Extract from the Author’s Preface (1891).', 'Extract from the Preface to the last edition.', 'FOOTNOTES'):
                    chapter, section, sub = ('Footnotes' if t == 'FOOTNOTES' else 'Prefaces'), t.rstrip('.').title() if t == 'FOOTNOTES' else t.rstrip('.'), None
                else:
                    section, sub, sub2 = t.rstrip('.:'), None, None
            elif lvl == 2:
                sub = sub2 = t.rstrip('.:')
            else:
                sub = f"{sub2} — {t.rstrip('.:')}" if sub2 else t.rstrip('.:')
            continue
        if t and t not in ('VOLUME II', '**VOLUME II**'):
            if p.style.name.startswith('List'):
                t = '- ' + t
            blocks.append(t)
        elif has_image(p) and blocks and blocks[-1] != IMG:
            blocks.append(IMG)
    flush()
    for s in sections:
        if s['section'] == s['chapter'] and s['blocks'] and s['blocks'][0].isupper():
            s['blocks'] = s['blocks'][1:]
            s['section'] = 'Introduction'
    sections = [s for s in sections if any(b != IMG for b in s['blocks'])]
    if examples:
        sections.append({'chapter': 'Volume II — Musical examples', 'section': 'Index of musical examples (numbers referenced in the text)', 'subsection': None,
                         'blocks': ['\n'.join(f'- {e}' for e in examples[i:i+40]) for i in range(0, len(examples), 40)]})
    return sections

# ---------------------------------------------------------------- chunking
def chunk_sections(sections, meta, target=2200, hard=3200):
    chunks = []
    for s in sections:
        path = [meta['title'], s['chapter'], s['section']] + ([s['subsection']] if s.get('subsection') else [])
        path = [x for i, x in enumerate(path) if x and (i == 0 or x != path[i-1])]
        blocks = [b for b in s['blocks']]
        # split very long blocks at sentence boundaries
        exp = []
        for b in blocks:
            if len(b) <= hard or b.startswith('|'):
                exp.append(b); continue
            cur = ''
            for sent in re.split(r'(?<=[.;!?])\s+', b):
                if len(cur) + len(sent) > target and cur:
                    exp.append(cur.strip()); cur = ''
                cur += sent + ' '
            if cur.strip(): exp.append(cur.strip())
        groups, cur = [], []
        for b in exp:
            if cur and sum(len(x) for x in cur) + len(b) > target:
                groups.append(cur)
                tail = cur[-1] if cur[-1] != IMG and len(cur[-1]) < 600 else None   # 1-paragraph overlap
                cur = [tail] if tail else []
            cur.append(b)
        if cur and any(x != IMG for x in cur):
            if groups and sum(len(x) for x in cur) < 400 and sum(len(x) for x in groups[-1]) < hard:
                groups[-1] += [x for x in cur if x not in groups[-1]]
            else:
                groups.append(cur)
        for gi, g in enumerate(groups):
            while g and g[-1] == IMG: g = g[:-1]
            content = '\n\n'.join(g).strip()
            if len(re.sub(r'\W', '', content)) < 40:
                continue
            header = ' > '.join(path)
            cid = f"{meta['id']}-{len(chunks)+1:04d}"
            chunks.append({
                'id': cid,
                'source_id': meta['id'],
                'title': meta['title'], 'author': meta['author'], 'edition': meta['edition'],
                'chapter': s['chapter'], 'section': s['section'], 'subsection': s.get('subsection'),
                'heading_path': path,
                'part': f'{gi+1}/{len(groups)}',
                'instruments': tags(content, INSTR),
                'topics': tags(content, TOPICS, min_hits=3),
                'has_musical_example': IMG in content,
                'char_count': len(content),
                'text': f'[{header}]\n\n{content}',
                'content': content,
            })
    return chunks

def to_markdown(sections, meta):
    out = [f"# {meta['title']}", '', f"*{meta['author']} — {meta['edition']}*", '', f"> {meta['note']}", '']
    last_ch = last_sec = None
    for s in sections:
        if s['chapter'] != last_ch:
            out += [f"## {s['chapter']}", '']; last_ch = s['chapter']; last_sec = None
        if s['section'] != last_sec and s['section'] != s['chapter']:
            out += [f"### {s['section']}", '']; last_sec = s['section']
        if s.get('subsection'):
            out += [f"#### {s['subsection']}", '']
        for b in s['blocks']:
            out += [f'*{b}*' if b == IMG else b, '']
    return '\n'.join(out)

META = {
 'berlioz': dict(id='berlioz', title='A Treatise on Modern Instrumentation and Orchestration', author='Hector Berlioz',
                 edition='trans. Mary Cowden Clarke, rev. Joseph Bennett (Novello, 1882); public domain',
                 note='Text recovered by OCR from a scan; some OCR errors remain in note names and symbols (e.g. "Bt?" for B♭). Musical examples were images and are marked as omitted.'),
 'rimsky': dict(id='rimsky', title='Principles of Orchestration', author='Nikolay Rimsky-Korsakov',
                edition='ed. Maximilian Steinberg, trans. Edward Agate (Édition Russe de Musique, 1922); Project Gutenberg #33900',
                note='Volume I text in full. Volume II (score examples) is listed as an index only; example numbers cited in the text refer to it.'),
}

if __name__ == '__main__':
    which = 'berlioz' if 'Berlioz' in SRC else 'rimsky'
    doc = docx.Document(SRC)
    sections = berlioz(doc) if which == 'berlioz' else rimsky(doc)
    meta = META[which]
    open(f'{OUT}/{which}.md', 'w').write(to_markdown(sections, meta))
    with open(f'{OUT}/{which}.chunks.jsonl', 'w') as f:
        for c in chunk_sections(sections, meta):
            f.write(json.dumps(c, ensure_ascii=False) + '\n')
