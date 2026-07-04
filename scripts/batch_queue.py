"""Batch-extract match data from queue transcripts and generate scripts/*_data.py modules."""
import pprint
import re
from pathlib import Path

QUEUE = Path(__file__).resolve().parent.parent / "transcripts" / "queue"
TRANSCRIPTS = QUEUE.parent
DUMPS = Path(__file__).resolve().parent / "dumps"

HEADER = '''"""%s — auto-generated from transcript."""
# ruff: noqa: E501
from __future__ import annotations
from typing import Any

RAW: list[dict[tuple[str, int], dict[str, Any]]] = %s
'''

_datetime = __import__('datetime').datetime

def snake(name: str) -> str:
    """Convert event name to snake_case module name."""
    s = re.sub(r'[^a-zA-Z0-9]+', '_', name).strip('_').lower()
    s = re.sub(r'_+', '_', s)
    return s

def clean_name(name: str) -> str:
    """Strip Ref:" prefixes and trailing colons/symbols from parsed names."""
    name = re.sub(r'^Ref:\s*["\']?\s*', '', name)
    name = re.sub(r'["\':;.]+$', '', name)
    return name.strip()


def parse_match_card(text: str) -> list[dict]:
    """Parse transcript to extract match listing."""
    matches = []
    # CJI-style timeline (timestamp Name vs Name); Ref-block fallback lives in the caller.
    # Look for lines like "1:16:50 Luke Griffith vs Pat Downey"
    for line in text.split('\n'):
        line = line.strip()
        # Skip header lines, timestamps-only lines, and lines too short
        if not line or 'Intro' in line:
            continue
        # Try CJI-style: timestamp Name1 vs Name2
        cji_match = re.match(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)\s+vs\.?\s+(.+?)$', line, re.IGNORECASE)
        if cji_match:
            matches.append({
                'a': clean_name(cji_match.group(2)),
                'b': clean_name(cji_match.group(3)),
                'start': cji_match.group(1),
                'end': '',
            })
            continue
        # Try with "vs." at end: timestamp Name1 vs. Name2
        cji_match2 = re.match(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)\s+vs\.\s+(.+?)$', line, re.IGNORECASE)
        if cji_match2:
            matches.append({
                'a': clean_name(cji_match2.group(2)),
                'b': clean_name(cji_match2.group(3)),
                'start': cji_match2.group(1),
                'end': '',
            })
            continue
        # Try Ref-block style: "Name vs Name: (timestamp)" or "Name vs Name (timestamp)"
        vs_match = re.match(r'\s*(.+?)\s+vs\.?\s+(.+?)\s*[:(]\s*([\d:]+)\s*(?:-\s*([\d:]+))?\s*[)]?', line, re.IGNORECASE)
        if vs_match:
            matches.append({
                'a': clean_name(vs_match.group(1)),
                'b': clean_name(vs_match.group(2)),
                'start': vs_match.group(3),
                'end': vs_match.group(4) or '',
            })

    # Deduplicate by (a,b) keeping first occurrence
    seen = set()
    unique = []
    for m in matches:
        key = (m['a'].lower(), m['b'].lower())
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique

def ts_to_sec(ts: str) -> int:
    """Convert timestamp to seconds."""
    parts = [int(x) for x in ts.split(':')]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


# YouTube auto-caption body = repeating "<timestamp>\n<duration label>\n<commentary>".
_TS_LINE = re.compile(r'^(\d{1,2}:\d{2}(?::\d{2})?)$')
_DUR_LINE = re.compile(r'^\d+\s+(segundos?|minutos?|seconds?|minutes?)\b', re.IGNORECASE)
_NOISE = re.compile(r'\[(music|música|laughter|applause|aplausos?|risos?)\]', re.IGNORECASE)

# Submission keywords → display name (preliminary win-type hint for the refiner).
SUB_KEYWORDS = {
    'armbar': 'Armbar', 'triangle': 'Triangle Choke', 'rear naked': 'Rear Naked Choke',
    'heel hook': 'Heel Hook', 'toe hold': 'Toe Hold', 'knee bar': 'Knee Bar',
    'kimura': 'Kimura', 'americana': 'Americana', 'guillotine': 'Guillotine',
    "d'arce": "D'Arce Choke", 'darce': "D'Arce Choke", 'anaconda': 'Anaconda Choke',
    'bicep': 'Bicep Slicer', 'calf slicer': 'Calf Slicer', 'omoplata': 'Omoplata',
    'wrist lock': 'Wrist Lock', 'clock choke': 'Clock Choke', 'ezekiel': 'Ezekiel Choke',
    'north south': 'North-South Choke', 'von flue': 'Von Flue Choke', 'twister': 'Twister',
    'shoulder lock': 'Shoulder Lock', 'foot lock': 'Foot Lock', 'choke': 'Choke',
}


def parse_pbp(text: str) -> list[tuple[int, str]]:
    """Parse the caption body into ``[(sec, commentary)]`` segments — one per timestamp,
    with the duration label and [music]/[laughter] noise stripped. Empty lines dropped."""
    lines = text.split('\n')
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = _TS_LINE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        sec = ts_to_sec(m.group(1))
        buf: list[str] = []
        j = i + 1
        while j < len(lines) and not _TS_LINE.match(lines[j].strip()):
            ln = lines[j].strip()
            if ln and not _DUR_LINE.match(ln):
                buf.append(_NOISE.sub('', ln).strip())
            j += 1
        seg = ' '.join(s for s in buf if s).strip()
        seg = re.sub(r'\s+', ' ', seg)
        if seg:
            out.append((sec, seg))
        i = j
    return out

# Map stems to event names
STUB_EVENTS = {
    "CJI2Day1": "Craig Jones Invitational 2, Day 1",
    "CJI2Day2": "Craig Jones Invitational 2, Day 2",
    "CraigJones": "Craig Jones Compilation",
    "Eddie Bravo Invitational 14 The Absolutes": "EBI 14: The Absolutes",
    "EVENTO COMPLETO FINAL DO JUDÔ - EQUIPES MISTAS OLIMPÍADAS PARIS 2024": "Paris 2024 Olympics - Judo Mixed Team",
    "PGF World 2026 - Week 1 - Opening Day": "PGF World 2026 - Week 1",
    "PGF World 2026 - Week 2 - Things are heating up": "PGF World 2026 - Week 2",
    "PGF World 2026 - Week 3 - This marks the halfway point": "PGF World 2026 - Week 3",
    "PGF World 2026 - Week 4 - The Playoff Race Is On": "PGF World 2026 - Week 4",
    "PGF World 2026 - Week 5 - Regular Season Finale": "PGF World 2026 - Week 5",
    "POLARIS 18 Submission Grappling FULL BJJ EVENT REPLAY": "Polaris 18",
    "Polaris 25 Prelims LIVE Full No-Gi BJJ Grappling Undercard": "Polaris 25",
    "POLARIS 26 LIVE PRELIMS NINE free matches LIVE": "Polaris 26",
    "POLARIS BJJ SQUADS TEAM USA vs TEAM UK & IRELAND Grappling Full Event": "Polaris BJJ Squads",
    "Polaris28Prelims": "Polaris 28",
    "Polaris29": "Polaris 29",
    "Polaris30": "Polaris 30",
    "Polaris31": "Polaris 31",
    "Polaris32": "Polaris 32",
    "Polaris33": "Polaris 33",
    "Polaris34": "Polaris 34",
    "Polaris35": "Polaris 35",
    "Polaris36": "Polaris 36",
    "Polaris37": "Polaris 37 (queue)",
    "Supercut The Entire 2024 ADCC Worlds -65kg Bracket": "ADCC 2024 (-65kg)",
    "Team BJJ Stars vs Team Polaris FULL Squads Matchup Polaris 37": "Team BJJ Stars vs Polaris 37",
    "UFC 320 Free Fight Marathon": "UFC 320",
    "UFC 324 Free Fight Marathon": "UFC 324",
    "UFC 327 Free Fight Marathon": "UFC 327",
    "UFC 328 Free Fight Marathon": "UFC 328",
    "WNO 30 Open Weight Grand Prix Undercard Free Live Prelim Matches": "WNO 30",
}

modules = []
for txt_path in sorted(QUEUE.glob("*.txt")):
    stem = txt_path.stem
    event = STUB_EVENTS.get(stem, stem)
    text = txt_path.read_text(encoding="utf-8", errors="replace")

    matches = parse_match_card(text)
    if not matches:
        print(f"⚠  {stem}: no match card found, trying line-by-line")
        # Fallback: look for "vs" patterns in the Ref block
        ref_match = re.search(r'Ref:\s*(".*?")(?:\s*\n\s*Link:)?', text, re.DOTALL)
        if ref_match:
            ref_text = ref_match.group(1)
            for line in ref_text.split('\n'):
                vs = re.search(r'\s*(.+?)\s+vs\.?\s+(.+?)\s*[:(]\s*([\d:]+)', line, re.IGNORECASE)
                if vs:
                    matches.append({
                        'a': clean_name(vs.group(1)),
                        'b': clean_name(vs.group(2)),
                        'start': vs.group(3),
                        'end': '',
                    })

    if not matches:
        print(f"✗  {stem}: NO matches found — skipping")
        continue

    year = 2025
    if "2024" in stem or "ADCC" in stem:
        year = 2024
    elif "2026" in stem or "PGF" in stem:
        year = 2026

    all_segments = parse_pbp(text)  # cleaned (sec, commentary) segments
    records = []
    for i, m in enumerate(matches[:40]):
        winner = None
        win_type = None
        submission = None

        # Determine the end point for text search
        end_sec = ts_to_sec(m['end']) if m['end'] else None
        start_sec = ts_to_sec(m['start']) if m['start'] else None

        # If no end time, use next match's start or +5min
        if end_sec is None and start_sec is not None:
            if i + 1 < len(matches):
                next_start = ts_to_sec(matches[i+1]['start']) if matches[i+1]['start'] else None
                end_sec = next_start if next_start else start_sec + 600
            else:
                end_sec = start_sec + 600  # last match: +10min

        if end_sec and start_sec:
            window_start = max(0, start_sec)
            window_end = end_sec + 120  # search 2min after end
            lines = text.split('\n')
            relevant = []
            for line in lines:
                ts = re.findall(r'(\d{1,2}:\d{2}(?::\d{2})?)', line)
                for t in ts:
                    sec = ts_to_sec(t)
                    if window_start - 30 <= sec <= window_end:
                        relevant.append(line)
                        break
            relevant_text = '\n'.join(relevant)

            # Check for submission keywords (reliable — mentions the actual sub)
            sub_keywords = {
                'armbar': 'Armbar', 'triangle': 'Triangle Choke', 'rear naked': 'Rear Naked Choke',
                'heel hook': 'Heel Hook', 'toe hold': 'Toe Hold', 'knee bar': 'Knee Bar',
                'kimura': 'Kimura', 'americana': 'Americana', 'choke': 'Choke',
                'foot lock': 'Foot Lock', 'guillotine': 'Guillotine', 'd\'arce': 'D\'Arce Choke',
                'darce': 'D\'Arce Choke', 'anaconda': 'Anaconda Choke', 'bicep': 'Bicep Slicer',
                'calf slicer': 'Calf Slicer', 'omoplata': 'Omoplata', 'wrist lock': 'Wrist Lock',
                'clock choke': 'Clock Choke', 'ezekiel': 'Ezekiel Choke', 'north south': 'North-South Choke',
                'von flue': 'Von Flue Choke', 'twister': 'Twister', 'shoulder lock': 'Shoulder Lock',
            }
            found_sub = None
            for sub_word, display in sub_keywords.items():
                if sub_word in relevant_text.lower():
                    found_sub = display
                    break
            if found_sub:
                submission = found_sub
                win_type = "SUBMISSION"
                # Try to find winner: the submitting athlete's name should appear near sub keyword
                sub_pos = relevant_text.lower().find(sub_word)
                surrounding = relevant_text[sub_pos-50:sub_pos+50].lower()
                a_lower = m['a'].lower()[:10]
                b_lower = m['b'].lower()[:10]
                if a_lower in surrounding and b_lower in surrounding:
                    pass  # both mentioned — can't determine
                elif a_lower in surrounding:
                    winner = m['a']
                elif b_lower in surrounding:
                    winner = m['b']

            # Check for decision/draw only if not already a submission
            if not win_type:
                lower = relevant_text.lower()
                if 'decision' in lower or 'unanimous' in lower or 'split' in lower:
                    win_type = "DECISION"
                elif 'submission' in lower or 'tap' in lower or 'tapped' in lower:
                    win_type = "SUBMISSION"
                elif 'points' in lower or 'advantage' in lower or 'referee decision' in lower:
                    win_type = "POINTS"
                elif 'draw' in lower:
                    win_type = "DRAW"
                # NO default — leave as None if no evidence

        # Per-bout commentary window = the PRELIMINARY structure the refiner reads.
        pbp = []
        if start_sec is not None:
            wend = end_sec or start_sec + 900
            pbp = [{"ts": max(0, s - start_sec), "text": t}
                   for (s, t) in all_segments if start_sec - 10 <= s <= wend][:500]
        records.append({
            "a": m['a'],
            "b": m['b'],
            "winner": winner,
            "win_type": win_type,
            "submission": submission,
            "start": m['start'],
            "year": year,
            "pbp": pbp,
        })

    # Build RAW list
    data = []
    for r in records:
        bt = {}
        if r['winner']:
            bt['winner'] = r['winner']
        bt['method'] = r['win_type'] or "Unknown"
        if r['submission']:
            bt['method'] += f" ({r['submission']})"
        bt['start'] = r['start']
        bt['opponent'] = r['b']
        bt['event'] = event
        bt['weight_class'] = ""
        bt['stage'] = ""
        bt['win_type'] = r['win_type']  # None = unknown
        bt['submission'] = r['submission']
        # PRELIMINARY play-by-play (ts relative to bout start). A refiner LLM reads `pbp`
        # and fills `events` -> [{label, type, actor, successful}]; then `pbp` is dropped.
        bt['pbp'] = r['pbp']
        bt['events'] = []
        data.append({(r['a'], r['year']): bt})

    # Serialize with pprint for readability (greppable per-bout lines, still valid Python literal)
    raw_str = pprint.pformat(data, width=100, sort_dicts=False)

    module_name = snake(stem)
    module_path = DUMPS / f"{module_name}_data.py"

    # Skip if module already exists with real data (has event labels, not just skeleton)
    if module_path.exists():
        existing = module_path.read_text(encoding="utf-8")
        if '"label":' in existing or "'label':" in existing:
            print(f"∼  {stem}: module exists with event data — skipping")
            modules.append((module_name, event, snake(snake(stem))))
            continue

    module_path.write_text(HEADER % (event, raw_str))
    modules.append((module_name, event, snake(snake(stem))))
    print(f"✓  {stem} → scripts/dumps/{module_name}_data.py ({len(data)} matches)")

print(f"\n=== {len(modules)} modules generated ===")

# Output reprocess_all DATASETS entries
print("\nAdd to reprocess_all.py DATASETS:")
for mod, ev, label in modules:
    print(f'    ("scripts.dumps.{mod}_data", "{ev}", "{label}"),')
