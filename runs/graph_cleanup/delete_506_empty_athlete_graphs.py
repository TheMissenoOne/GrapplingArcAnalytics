"""Q3 (2026-08-24): delete the 506 empty athlete graphs whose owner has no
matches and no ELO. Backup at runs/graph_cleanup/backup_20260824T183016Z.json.
Keeps: 499 non-empty leaderboard rows + 309 empty graphs of athletes with matches.
Dependents (athlete_dossiers, graph_nodes) verified zero for the target set.

Run (from repo root, .env loaded):
    uv run python runs/graph_cleanup/delete_506_empty_athlete_graphs.py
"""
from sqlalchemy import text

from db.base import get_engine

PRED = """
  g.owner_kind='athlete'
  and not exists (select 1 from graph_edges ge where ge.graph_id=g.id)
  and g.user_elo is null
  and not exists (select 1 from matches m
      where m.athlete_a_id = g.owner_id or m.athlete_b_id = g.owner_id)
"""

eng = get_engine()
with eng.begin() as c:
    n = c.execute(text(f"select count(*) from graphs g where {PRED}")).scalar()
    assert n == 506, f"pre-check: expected 506 target rows, found {n} — aborting"
    deleted = c.execute(text(f"delete from graphs g where {PRED}")).rowcount
    assert deleted == 506, f"deleted {deleted}, expected 506 — rolled back"
    print("deleted:", deleted)

with eng.connect() as c:
    print("athlete graphs remaining:", c.execute(text(
        "select count(*) from graphs where owner_kind='athlete'")).scalar())
    print("user graphs untouched:", c.execute(text(
        "select count(*) from graphs where owner_kind='user'")).scalar())
