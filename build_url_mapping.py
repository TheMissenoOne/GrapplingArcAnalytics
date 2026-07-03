"""Build url_mapping.json: event→URL + per-match {athlete, timestamp}."""
from pathlib import Path
import json, ast, re, os

ROOT = Path("/home/vetor/GrapplingArc")
OUT = Path("/home/vetor/GrapplingArc/GrapplingArcAnalytics/url_mapping.json")

# Final URL map (from crossref + search)
EVENT_URLS = {
    "2024NCAA": "https://www.youtube.com/watch?v=r9_ktnu-zeU",
    "2025NCAA": "https://www.youtube.com/watch?v=epXN_WwO6cY",
    "2026NCAA": "https://www.youtube.com/watch?v=MlancZWswSk",
    "ADCC 2022 (+99kg)": "https://www.youtube.com/watch?v=7wr1sMq2cu8",
    "ADCC 2022 (-88kg)": "https://www.youtube.com/watch?v=MyUe7tIEWHc",
    "ADCC 2022 (-99kg)": "https://www.youtube.com/watch?v=Lorr3RBNwFw",
    "ADCC 2022 (Absolute)": "https://www.youtube.com/watch?v=9Bmb-ReK3AY",
    "ADCC 2022 (Finals)": "https://www.youtube.com/watch?v=RZ1UPMN7FNQ",
    "ADCC 2022 (Women)": "https://www.youtube.com/watch?v=n_dkNnfjx4s",
    "ADCC 2024 (+99kg)": "https://www.youtube.com/watch?v=hF7HSiZveoA",
    "ADCC 2024 (Absolute)": "https://www.youtube.com/watch?v=mvxE73j-Njc",
    "ADCC Trials 2022 (South America Finals)": "https://www.youtube.com/watch?v=nDc0L96J6i4",
    "ADCC Trials 2023 (East Coast Finals)": "https://www.youtube.com/watch?v=loMqIK8s9a4",
    "ADCC Trials 2023 (East Coast Semifinals)": "https://www.youtube.com/watch?v=WCz5jauTn4M",
    "ADCC Trials 2024 (West Coast Finals)": "https://www.youtube.com/watch?v=_MfGk9NMy0U",
    "CJI": "https://www.youtube.com/watch?v=IyneYOMCvI0",
    "IBJJF 2023 (No-Gi Worlds, Black Belt Finals)": "https://www.youtube.com/watch?v=22QAtq917NI",
    "IBJJF 2025 (Top 10)": "https://www.youtube.com/watch?v=6Tt1tixCogw",
    "Polaris 37": "https://www.youtube.com/watch?v=TQAlgxSUivk",
    "Spyder King of Kings": "https://www.youtube.com/watch?v=dQWfia0LQFQ",
    "UFC": None,
    "UFC 325": "https://www.youtube.com/watch?v=PqTBCISwu7k",
    "WNO 20": "https://www.youtube.com/watch?v=7AyoRIBXvUc",
    "WNO 22": "https://www.youtube.com/watch?v=hABPApjT64g",
    "WNO 24": "https://www.youtube.com/watch?v=tu2TiQ61Dh8",
    "WNO 31": "https://www.youtube.com/watch?v=q7o5aoyy5HM",
    "khabib": "https://www.youtube.com/watch?v=A4i6pLRF9iY",
    "Musumeci UFC BJJ": "https://www.youtube.com/watch?v=tU9XUAtAJG8",
}

# File stem -> event title mapping
FILE_EVENTS = {
    "2024NCAA": "2024NCAA",
    "2025NCAA": "2025NCAA",
    "2026NCAA": "2026NCAA",
    "ADCC2022+99kg": "ADCC 2022 (+99kg)",
    "ADCC2022-88kg": "ADCC 2022 (-88kg)",
    "ADCC2022-99kg": "ADCC 2022 (-99kg)",
    "ADCC2022-ABS": "ADCC 2022 (Absolute)",
    "ADCC2022-Finals": "ADCC 2022 (Finals)",
    "ADCC2022Women": "ADCC 2022 (Women)",
    "ADCC2024+99kg": "ADCC 2024 (+99kg)",
    "ADCC2024-ABS": "ADCC 2024 (Absolute)",
    "ADCCTrials2022SouthAmericaFinals": "ADCC Trials 2022 (South America Finals)",
    "ADCCTrials2023EastCoastFinals": "ADCC Trials 2023 (East Coast Finals)",
    "ADCCTrials2023EastCoastSemifinals": "ADCC Trials 2023 (East Coast Semifinals)",
    "ADCCTrials2024WestCoastFinals": "ADCC Trials 2024 (West Coast Finals)",
    "CJI": "CJI",
    "IBJJF2023-Worlds-BlackBeltFinals": "IBJJF 2023 (No-Gi Worlds, Black Belt Finals)",
    "IBJJF2025top10": "IBJJF 2025 (Top 10)",
    "khabib": "khabib",
    "Polaris37": "Polaris 37",
    "SpyderKingOfKings": "Spyder King of Kings",
    "UFC": "UFC",
    "UFC325": "UFC 325",
    "WNO20": "WNO 20",
    "WNO22": "WNO 22",
    "WNO24": "WNO 24",
    "WNO31": "WNO 31",
    "Musumeci": "Musumeci UFC BJJ",
}

def parse_start(raw: str) -> int | None:
    """Convert '1:26:53' or '2:23' to seconds."""
    if not raw or raw == "?":
        return None
    parts = [int(x) for x in raw.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 1:
        return parts[0]
    return None

def parse_py_file(filepath: Path) -> list[dict]:
    """Extract matches from a .py dict literal file."""
    content = filepath.read_text(encoding="utf-8")
    try:
        data = ast.literal_eval(content)
    except Exception as e:
        print(f"  Parse error {filepath.name}: {e}")
        return []

    matches = []
    if isinstance(data, dict):
        # { (name, year): {...} }
        for (athlete_name, year), info in data.items():
            matches.append({
                "athlete": athlete_name,
                "year": year,
                "opponent": info.get("opponent", ""),
                "event": info.get("event", ""),
                "winner": info.get("winner", ""),
                "method": info.get("method", ""),
                "start": info.get("start", ""),
                "seconds": parse_start(info.get("start", "")),
            })
    elif isinstance(data, list):
        # [{ (name, year): {...} }, ...] — compound events
        for block in data:
            if isinstance(block, dict):
                matches.extend(parse_py_file_content(block))
    return matches

def parse_py_file_content(data: dict) -> list[dict]:
    matches = []
    for (athlete_name, year), info in data.items():
        matches.append({
            "athlete": athlete_name,
            "year": year,
            "opponent": info.get("opponent", ""),
            "event": info.get("event", ""),
            "winner": info.get("winner", ""),
            "method": info.get("method", ""),
            "start": info.get("start", ""),
            "seconds": parse_start(info.get("start", "")),
        })
    return matches

# Build mapping
output = {}
for stem, event_title in sorted(FILE_EVENTS.items()):
    py_path = ROOT / f"{stem}.py"
    if not py_path.exists():
        print(f"SKIP {stem}.py — not found")
        continue

    url = EVENT_URLS.get(event_title)
    entry = {
        "event_title": event_title,
        "video_url": url,
        "file": f"{stem}.py",
        "matches": [],
    }

    if stem == "matches":
        # Special case: matches.py is a complex list-of-dicts
        content = py_path.read_text(encoding="utf-8")
        try:
            data = ast.literal_eval(content)
        except:
            data = []
        if isinstance(data, list):
            for block in data:
                if isinstance(block, dict):
                    for (name, year), info in block.items():
                        entry["matches"].append({
                            "athlete": name,
                            "year": year,
                            "opponent": info.get("opponent", ""),
                            "winner": info.get("winner", ""),
                            "method": info.get("method", ""),
                            "start": info.get("start", ""),
                            "seconds": parse_start(info.get("start", "")),
                        })
    else:
        entry["matches"] = parse_py_file(py_path)

    output[stem] = entry

OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False))
print(f"Wrote {OUT} — {len(output)} events, {sum(len(e['matches']) for e in output.values())} total matches")
