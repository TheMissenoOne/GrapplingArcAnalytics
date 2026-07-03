"""Find YouTube URLs for each event .py file."""
from dotenv import load_dotenv
load_dotenv()
from harvest.transcripts import search_video_id
from pathlib import Path
import re

ROOT = Path("/home/vetor/GrapplingArc")

SKIP = {"matches", "_run_user_insights"}
py_files = sorted(p for p in ROOT.glob("*.py") if p.stem not in SKIP and not p.stem.startswith("_"))

results = {}

for py in py_files:
    content = py.read_text(encoding="utf-8")
    events = set()
    for m in re.finditer(r"['\"]event['\"]\s*:\s*['\"]([^'\"]+)['\"]", content):
        events.add(m.group(1))
    txt_path = ROOT / f"{py.stem}.txt"
    ref = ""
    if txt_path.exists():
        first_line = txt_path.read_text(encoding="utf-8").split("\n")[0]
        if first_line.startswith("Ref:"):
            ref = first_line.replace("Ref:", "").strip().strip('"\' ')
    event_name = ref or (max(events, key=len) if events else py.stem)

    q = event_name
    print(f"[{py.stem}] searching: {q[:80]}...", flush=True)
    vid = search_video_id(q)
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}"
        print(f"  -> {url}", flush=True)
        results[py.stem] = url
    else:
        print(f"  -> NOT FOUND", flush=True)
        results[py.stem] = None

print("\n\n=== FINAL RESULTS ===")
for name, url in sorted(results.items()):
    print(f"{name}.py -> {url or 'NOT FOUND'}")
