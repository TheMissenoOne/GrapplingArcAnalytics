"""CLI: harvest fight transcripts into data/harvest/inbox/ (transcript + prompt).

    uv run python -m harvest --url "https://youtu.be/XXXXXXXXXXX"
    uv run python -m harvest --url URL --fighter "Gordon Ryan" --opponent "Felipe Pena" --year 2022
    uv run python -m harvest --playlist "https://www.youtube.com/playlist?list=PL..."
    uv run python -m harvest --urls-file matches.txt        # one URL per line

Then run each file through ChatGPT/Copilot/Deepseek, save the returned JSON into
data/harvest/processed/, and use the admin "Import scraped drafts" button.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from harvest.harvester import (
    HARVEST_INBOX,
    HarvestResult,
    harvest_playlist,
    harvest_url,
    harvest_urls,
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Harvest YouTube fight transcripts → prompt files")
    ap.add_argument("--url", action="append", default=[],
                    help="Video URL, search URL, or 'Name vs Name' query (repeatable)")
    ap.add_argument("--playlist", help="Public playlist URL (harvests every video)")
    ap.add_argument("--urls-file", help="Text file with one URL/query per line")
    ap.add_argument("--fighter", help="Fighter A name (single --url only)")
    ap.add_argument("--opponent", help="Fighter B name (single --url only)")
    ap.add_argument("--year", type=int, help="Match year (single --url only)")
    ap.add_argument("--lang", default="en", help="Transcript language (default: en)")
    args = ap.parse_args()

    langs = (args.lang,)
    results: list[HarvestResult] = []

    if args.playlist:
        results += harvest_playlist(args.playlist, languages=langs)

    urls = list(args.url)
    if args.urls_file:
        urls += [
            ln.strip()
            for ln in Path(args.urls_file).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

    # A single input may carry explicit fighter/opponent/year; batches parse per-title.
    if len(urls) == 1 and (args.fighter or args.opponent or args.year):
        results.append(harvest_url(urls[0], fighter=args.fighter, opponent=args.opponent,
                                   year=args.year, languages=langs))
    elif urls:
        results += harvest_urls(urls, languages=langs)

    if not results:
        ap.print_help()
        return 1

    ok = [r for r in results if r.status == "ok"]
    print(f"\n✅ Harvested {len(ok)}/{len(results)} → {HARVEST_INBOX}")
    for r in results:
        if r.status == "ok" and r.path:
            print(f"   ✓ {r.path.name}")
        else:
            print(f"   ✗ [{r.status}] {r.input}  ({r.detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
