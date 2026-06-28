"""Fight-transcript harvesting (analytics-native; replaces the bjj-match-analyzer repo).

Fetch a YouTube match transcript, compile it into a single file with an appended
processing prompt, and drop it in ``data/harvest/inbox/``. A human runs that file
through ChatGPT / Copilot / Deepseek and saves the returned JSON into
``data/harvest/processed/``, which ``db.scraped_import.import_scraped_dir`` reads
into the admin draft queue. No LLM API calls happen here — harvesting only.
"""

from harvest.harvester import (
    HARVEST_INBOX,
    HARVEST_PROCESSED,
    HarvestResult,
    harvest_playlist,
    harvest_url,
    harvest_urls,
    write_harvest_file,
)
from harvest.transcripts import (
    clean_transcript,
    extract_video_id,
    get_transcript,
    get_video_title,
    guess_year,
    parse_fighters,
    playlist_video_urls,
    resolve_to_video_id,
    search_video_id,
)

__all__ = [
    "HARVEST_INBOX",
    "HARVEST_PROCESSED",
    "HarvestResult",
    "harvest_playlist",
    "harvest_url",
    "harvest_urls",
    "write_harvest_file",
    "clean_transcript",
    "extract_video_id",
    "get_transcript",
    "get_video_title",
    "guess_year",
    "parse_fighters",
    "playlist_video_urls",
    "resolve_to_video_id",
    "search_video_id",
]
