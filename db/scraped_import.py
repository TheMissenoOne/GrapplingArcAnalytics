"""Import LLM-processed fight transcripts as DRAFT global matches.

The ``harvest/`` package compiles each fight transcript + a processing prompt into
``data/harvest/inbox/``; a human runs that through ChatGPT/Copilot/Deepseek and saves
the returned JSON — shaped ``{fighter, opponent, year, events}`` with events
``{label, type, actor, successful}`` — into ``data/harvest/processed/``. This module
reads those processed files and maps them into the global ``matches`` model as
``status='draft'`` (held out of the ELO replay/graph until an admin approves)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from analysis.technique_match import clean_label
from db.models import Athlete, Match
from db.repository import register_match, upsert_athlete

logger = logging.getLogger(__name__)


def _parse_timestamp(ts: Any) -> int | None:
    """Parse H:MM:SS or M:SS timestamp string to seconds, or return None."""
    if not isinstance(ts, str):
        return None
    ts = ts.strip()
    if not ts:
        return None
    try:
        parts = ts.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return m * 60 + s
    except (ValueError, IndexError):
        pass
    return None


def _default_outputs_dir() -> Path:
    """``$GRAPPLINGARC_HARVEST_DIR`` or the harvest processed folder."""
    env = os.environ.get("GRAPPLINGARC_HARVEST_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "harvest" / "processed"


def _resolve(
    name: str, by_norm: dict[str, Athlete], session: Session
) -> Athlete:
    """Get-or-create an athlete by normalized name (both participants are athletes)."""
    key = _normalize_name(name)
    ath = by_norm.get(key)
    if ath is None:
        aid = upsert_athlete(name=name, belt="black", source="scraped", session=session)
        ath = session.get(Athlete, aid)
        assert ath is not None
        by_norm[key] = ath
    return ath


def analyzer_to_match_kwargs(
    d: dict[str, Any], fighter: Athlete, opponent: Athlete
) -> dict[str, Any]:
    """Map processed-transcript JSON + resolved athletes → register_match keyword args.

    Sequence actors (fighter names) become ``actor_id``. Winner/win_type/submission use
    the LLM's explicit top-level fields when present (the harvest prompt asks for them),
    else fall back to inference from the last successful submission event."""
    fnorm = _normalize_name(fighter.name)
    onorm = _normalize_name(opponent.name)
    events = d.get("events") or []

    seq: list[dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        by_fighter = _normalize_name(str(e.get("actor", ""))) == fnorm
        item: dict[str, Any] = {
            "label": clean_label(str(e.get("label", "")), str(e.get("type", ""))),
            "type": e.get("type", ""),
            "actor_id": fighter.id if by_fighter else opponent.id,
        }
        if "successful" in e:
            item["successful"] = bool(e["successful"])
        raw_ts = e.get("ts", e.get("timestamp"))  # analyzer says "ts"; dumps say "timestamp"
        ts = _parse_timestamp(raw_ts) if isinstance(raw_ts, str) else raw_ts
        if isinstance(ts, int):
            item["ts"] = ts
        seq.append(item)

    # Prefer the LLM's explicit result; fall back to inferring it from the events.
    winner_id: str | None = None
    wnorm = _normalize_name(str(d.get("winner") or ""))
    if wnorm == fnorm:
        winner_id = fighter.id
    elif wnorm == onorm:
        winner_id = opponent.id

    win_type = (str(d.get("win_type")).upper() if d.get("win_type") else None)
    submission = d.get("submission") or None

    if winner_id is None and not win_type:
        subs = [
            e for e in events
            if isinstance(e, dict) and e.get("type") == "submission" and e.get("successful")
        ]
        if subs:
            last = subs[-1]
            by_fighter = _normalize_name(str(last.get("actor", ""))) == fnorm
            winner_id = fighter.id if by_fighter else opponent.id
            win_type = "SUBMISSION"
            submission = submission or (str(last.get("label", "")) if by_fighter else None)

    return {
        "winner_id": winner_id,
        "win_type": win_type,
        "submission": str(submission) if submission else None,
        "event": d.get("event") or d.get("title") or None,
        "year": _norm_year(d.get("year")),
        "weight_class": d.get("weight_class") or None,
        "stage": d.get("stage") or None,
        "sequence": seq,
    }


def _norm_year(year: Any) -> int | None:
    """Coerce a JSON year (int OR string like \"2024\") to int, else None — so the stored
    value and the dedup key agree regardless of source type."""
    if isinstance(year, int):
        return year
    if isinstance(year, str) and year.strip().isdigit():
        return int(year.strip())
    return None


def import_scraped_dir(
    session: Session,
    outputs_dir: str | Path | None = None,
    created_by: str | None = None,
) -> list[str]:
    """Import every analyzer match JSON in ``outputs_dir`` as a draft match.

    Skips ``*_user_bundle.json`` (the UserBundle variant) and dedups against ALL existing
    matches (draft OR final) by (participants, year) — re-running after a draft is approved
    must not create a duplicate. Returns the created match ids."""
    out_dir = Path(outputs_dir) if outputs_dir else _default_outputs_dir()
    if not out_dir.is_dir():
        logger.warning("Scraped outputs dir not found: %s", out_dir)
        return []

    files = sorted(
        p for p in out_dir.glob("*.json") if not p.name.endswith("_user_bundle.json")
    )
    by_norm = {
        _normalize_name(a.name): a for a in session.execute(select(Athlete)).scalars()
    }
    existing: set[tuple[frozenset[str], int | None]] = {
        (frozenset((m.athlete_a_id, m.athlete_b_id)), m.year)
        for m in session.execute(select(Match)).scalars()
    }

    created: list[str] = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping %s: %s", f.name, exc)
            continue
        fighter_name = str(d.get("fighter", "")).strip()
        opponent_name = str(d.get("opponent", "")).strip()
        if not fighter_name or not opponent_name:
            continue
        fa = _resolve(fighter_name, by_norm, session)
        oa = _resolve(opponent_name, by_norm, session)
        key = (frozenset((fa.id, oa.id)), _norm_year(d.get("year")))
        if key in existing:
            continue
        kwargs = analyzer_to_match_kwargs(d, fa, oa)
        mid = register_match(
            fa.id, oa.id, created_by=created_by, session=session, status="draft", **kwargs
        )
        existing.add(key)
        created.append(mid)

    logger.info("Imported %d scraped draft matches from %s", len(created), out_dir)
    return created
