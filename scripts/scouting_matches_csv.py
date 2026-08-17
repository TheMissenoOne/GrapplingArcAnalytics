"""Bouts already registered in the DB for the athletes of a scouting roster → CSV.

    uv run python -m scripts.scouting_matches_csv                      # ADCC 2026 women
    uv run python -m scripts.scouting_matches_csv --roster <file.json> --out <file.csv>

Read-only. One row per (focus athlete, match): a bout between two focus athletes shows up
twice, once from each side, flagged in ``adversario_em_foco``.

Roster names are resolved through ``analysis.names.athlete_key`` plus the roster's own
``aliases``, so duplicate athlete rows in the DB ("Helena Cravar" next to "Helena Crevar")
are collected under one athlete and reported in ``nome_no_banco`` instead of vanishing.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from sqlalchemy import or_, select

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from analysis.names import athlete_key
from db.base import get_session_factory
from db.models import Athlete, Match

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = ROOT / "data" / "scouting" / "adcc_2026_women.json"
COLUMNS = [
    "divisao",
    "atleta",
    "nome_no_banco",
    "adversario",
    "evento",
    "ano",
    "fase",
    "resultado",
    "metodo",
    "finalizacao",
    "n_eventos",
    "n_timeline",
    "tem_video",
    "status",
    "adversario_em_foco",
    "identidade",
    "match_id",
]


def _roster_keys(roster: dict) -> dict[str, tuple[str, str]]:
    """athlete_key → (canonical roster name, division). Aliases map to the same pair."""
    out: dict[str, tuple[str, str]] = {}
    for div in roster["divisions"]:
        for a in div["athletes"]:
            for name in [a["name"], *a.get("aliases", [])]:
                out[athlete_key(name)] = (a["name"], div["name"])
    return out


def _suspect(roster_key: str, db_key: str) -> bool:
    """Same surname + a first name that plausibly abbreviates or misspells the roster one.

    Catches the duplicate athlete rows an import creates from a transcript spelling
    ("Raphaela Guedes", "R. Guedes", "Anna Karolina Vieira"). Deliberately loose — the
    output is flagged ``variante_suspeita``, never silently merged.
    """
    want, got = roster_key.split(), db_key.split()
    if len(want) < 2 or want[-1] not in got:
        return False
    # The surname is already accounted for — comparing it against the first name would
    # match every "<anybody> Garcia" to "Gabi Garcia".
    rest = [t for t in got if t != want[-1]]
    return any(t[:2] == want[0][:2] or want[0].startswith(t) for t in rest)


def build_rows(session, roster: dict) -> tuple[list[dict], list[str]]:
    keys = _roster_keys(roster)
    # id → (roster name, division); several DB rows can point at the same roster athlete.
    focus: dict[str, tuple[str, str]] = {}
    db_names: dict[str, str] = {}
    suspect_ids: set[str] = set()
    unmatched: list[Athlete] = []
    for ath in session.execute(select(Athlete)).scalars():
        hit = keys.get(athlete_key(ath.name))
        if hit:
            focus[ath.id] = hit
            db_names[ath.id] = ath.name
        else:
            unmatched.append(ath)
    for ath in unmatched:
        for rkey, hit in keys.items():
            if _suspect(rkey, athlete_key(ath.name)):
                focus[ath.id] = hit
                db_names[ath.id] = ath.name
                suspect_ids.add(ath.id)
                break
    missing = sorted({n for n, _ in keys.values()} - {n for n, _ in focus.values()})

    ids = list(focus)
    matches = session.execute(
        select(Match).where(or_(Match.athlete_a_id.in_(ids), Match.athlete_b_id.in_(ids)))
    ).scalars().all()
    names = dict(
        session.execute(select(Athlete.id, Athlete.name)).all()  # opponents may be off-roster
    )

    rows: list[dict] = []
    for m in matches:
        for side, other in ((m.athlete_a_id, m.athlete_b_id), (m.athlete_b_id, m.athlete_a_id)):
            if side not in focus:
                continue
            atleta, divisao = focus[side]
            rows.append(
                {
                    "divisao": divisao,
                    "atleta": atleta,
                    "nome_no_banco": db_names[side],
                    "adversario": names.get(other, "?"),
                    "evento": m.event or "",
                    "ano": m.year or "",
                    "fase": m.stage or "",
                    "resultado": (
                        "EMPATE/NULO"
                        if m.winner_id is None
                        else "V"
                        if m.winner_id == side
                        else "D"
                    ),
                    "metodo": m.win_type or "",
                    "finalizacao": m.submission or "",
                    "n_eventos": len(m.sequence or []),
                    "n_timeline": len(m.timeline or []),
                    "tem_video": "sim" if m.video_url else "nao",
                    "status": m.status,
                    "adversario_em_foco": "sim" if other in focus else "nao",
                    "identidade": "variante_suspeita" if side in suspect_ids else "confirmada",
                    "match_id": m.id,
                }
            )
    rows.sort(key=lambda r: (r["divisao"], r["atleta"], r["ano"] or 0, r["evento"]))
    return rows, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    out = args.out or args.roster.with_name(f"{args.roster.stem}_lutas.csv")

    with get_session_factory()() as session:
        rows, missing = build_rows(session, roster)

    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} linhas → {out}")
    if missing:
        print("sem NENHUMA luta no banco: " + ", ".join(missing))


if __name__ == "__main__":
    main()
