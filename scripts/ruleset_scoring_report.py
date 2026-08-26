#!/usr/bin/env python
"""Run `analysis/ruleset_scoring.family_report` over the corpus and print the doc's tables.

    uv run python scripts/ruleset_scoring_report.py            # markdown, to stdout
    uv run python scripts/ruleset_scoring_report.py --json     # the whole block, unformatted

READ-ONLY against prod `matches`. Nothing here writes, to the database or to the repo — the
markdown is pasted into `docs/research/ruleset_scoring.md` by hand precisely so that re-running
this and diffing the output is how a reader checks the doc has not drifted.

Deliberately NOT `db_session()`, which commits on clean exit. This has no business writing.

Privacy class A, public competition data: `matches` rows from published footage only, never a
user graph or a session (root CLAUDE.md, "Public vs Private Data").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.ruleset_scoring import CENSUS_BUCKETS, SYMBOLS, family_report  # noqa: E402


def fetch_bouts() -> list[dict[str, Any]]:
    """Every FINAL bout, whatever its event tag — the census is a statement about the whole
    corpus and a WHERE clause on `event` would make it a statement about the rows that already
    agreed with it."""
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from sqlalchemy import text

    from db.base import get_engine

    with get_engine().connect() as c:
        rows = c.execute(text("""
            select m.id::text, m.event, m.year, m.win_type, m.winner_id::text,
                   m.athlete_a_id::text, m.athlete_b_id::text, m.stage, m.ts_origin,
                   m.video_url, m.video_start_seconds, m.sequence, m.timeline
              from matches m
             where m.status = 'final'
        """)).fetchall()
    return [{"id": r[0], "event": r[1], "year": r[2], "win_type": r[3], "winner": r[4],
             "a_id": r[5], "b_id": r[6], "stage": r[7], "ts_origin": r[8],
             "video_url": r[9], "video_start_seconds": r[10],
             "seq": list(r[11] or []), "timeline": list(r[12] or [])} for r in rows]


def _n(x: Any, places: int = 3) -> str:
    return "—" if x is None else f"{x:.{places}f}"


def markdown(rep: dict[str, Any]) -> str:
    out: list[str] = []
    fams = sorted(rep["families"], key=lambda f: -rep["families"][f]["bouts"])

    out.append("### Annotation coverage (measured FIRST)\n")
    out.append("| family | bouts | w/ sequence | events | `successful` present | `true` |")
    out.append("|---|---|---|---|---|---|")
    for f in fams:
        b = rep["families"][f]
        a = b["annotation"]
        out.append(f"| `{f}` | {b['bouts']} | {b['with_sequence']} | {a['events']} | "
                   f"{a['present_pct']}% | {a['landed_pct']}% |")
    c = rep["comparability"]
    out.append(f"\nspread {c['present_pct_spread']} pts · "
               f"landing_rate_cross_family_comparable="
               f"{c['landing_rate_cross_family_comparable']} · "
               f"envelope_cross_family_comparable={c['envelope_cross_family_comparable']}\n")

    out.append("### Census — score information per family\n")
    hdr = " | ".join(f"`{k}`" for k in CENSUS_BUCKETS)
    out.append(f"| family | point table | bouts | {hdr} | footage | + start |")
    out.append("|---|---|---|" + "---|" * (len(CENSUS_BUCKETS) + 2))
    for f in fams:
        r = rep["census"]["families"][f]
        cells = " | ".join(str(r[k]) for k in CENSUS_BUCKETS)
        out.append(f"| `{f}` | {'yes' if r['has_point_table'] else 'no'} | {r['bouts']} | "
                   f"{cells} | {r['footage']} | {r['footage_with_start']} |")

    out.append("\n### Footage by bucket (transcript / frame-read feasibility)\n")
    out.append("| family | " + hdr + " |")
    out.append("|---|" + "---|" * len(CENSUS_BUCKETS))
    for f in fams:
        r = rep["census"]["families"][f]
        out.append(f"| `{f}` | "
                   + " | ".join(str(r["footage_by_bucket"][k]) for k in CENSUS_BUCKETS) + " |")

    ac = rep["adcc_clock"]
    out.append(f"\n### ADCC scoring windows — feasibility\n\n"
               f"{ac['bouts']} adcc-family bouts · with `stage` {ac['with_stage']} · "
               f"with a usable clock {ac['with_usable_clock']} · **both {ac['both']}** · "
               f"`ts_origin` {ac['ts_origin']} · window_applicable={ac['window_applicable']} "
               f"(`{ac['reason_code']}`)\n")

    out.append("### Coverage on the two units no row gate can see\n")
    out.append("| family | athletes | eff. athletes | top athlete | bouts w/ events "
               "| eff. bouts | top bout |")
    out.append("|---|---|---|---|---|---|---|")
    for f in fams:
        a = rep["families"][f]["athlete_coverage"]
        c = rep["families"][f]["bout_concentration"]
        out.append(f"| `{f}` | {a['athletes']} | {a['effective_n']:.2f} | "
                   f"{a['top_share']:.0%} | {c['bouts_with_events']} | "
                   f"{c['effective_n']:.2f} | {c['top_share']:.0%} |")

    for f in fams:
        b = rep["families"][f]
        t = b["truncation"]
        out.append(f"\n### `{f}` — chance of scoring per action\n")
        out.append(f"truncation channel: {t['won_by_submission']} submission wins, "
                   f"{t['bouts_truncated']} truncated, {t['unflagged_finishes']} unflagged, "
                   f"{t['events_after_finish']} events after a finish "
                   f"({t['events_mapped']} mapped, {t['events_skipped']} skipped)\n")
        # `chance` and the landing envelope are DIFFERENT columns and printing one under the
        # other's heading is how a zero-point action reads as "never lands". `land *` is
        # P(the action lands); `chance` is P(it puts points on the board), which is the landing
        # rate only where the rule book pays for it.
        out.append("| symbol | pts | n | bouts | landed | annot. | land lo | land cc | land hi "
                   "| width | gate | chance | E[pts] lo | E[pts] hi |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        exp = b["expected_points"]
        for s in SYMBOLS:
            row = b["scoring_chance"][s]
            e = row["envelope"]
            ch = row["chance"]
            xp = (exp or {}).get(s) or {}
            chance = ("—" if ch is None else
                      "0 (det.)" if ch["deterministic"] else "= landing")
            out.append(
                f"| `{s}` | {'—' if row['points'] is None else row['points']} | {e['n']} | "
                f"{row['clusters']} | {e['landed']} | {e['annotated']} | "
                f"{_n(e['lo'])} | {_n(e['cc'])} | {_n(e['hi'])} | "
                f"{_n(e['n'] and row['width'], 3)} | {'yes' if row['estimable'] else 'no'} | "
                f"{chance} | {_n(xp.get('lo'), 2)} | {_n(xp.get('hi'), 2)} |")
        w = b["winner_agreement"]
        if w["applicable"]:
            for mode in ("strict", "lenient"):
                m = w[mode]
                out.append(f"\nwinner agreement, {mode} — {m['k']}/{m['n']} = {_n(m['p'])} "
                           f"[{_n(m['lo'])}, {_n(m['hi'])}] · ties/scoreless "
                           f"{m['ties_or_scoreless']}")
            out.append(f"\nskipped {w['bouts_skipped']}\n")
        else:
            out.append(f"\nwinner agreement — n/a (`{w['reason_code']}`)\n")

    con = rep["contrast"]
    if con:
        a, bfam = con["families"]
        arms = rep["contrast_arms"]
        out.append(f"\n### Contrast — `{a}` vs `{bfam}` "
                   f"({con['appearances'][a]} vs {con['appearances'][bfam]} appearances)\n")
        out.append(f"primary: {con['primary']} · tables differ on "
                   f"{con['points_table_differs_on']}\n")
        out.append(f"arms: {arms['athletes_a']} vs {arms['athletes_b']} athletes, "
                   f"{arms['shared']} shared "
                   f"({_n(arms['shared_share_of_a'], 2)} of the `{a}` arm)\n")
        m = con["multiplicity"]
        out.append(f"multiplicity: {m['tests']} tests, {m['method']}, alpha {m['alpha']}, "
                   f"family = {m['family']}\n")
        out.append(f"| symbol | pts {a} | pts {bfam} | occ. {a} | occ. {bfam} | diff | "
                   "diff 95% | p | q | BH | gate | landing verdict |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in con["rows"]:
            o = r["occupancy"]
            out.append(
                f"| `{r['symbol']}` | {r['points_a']} | {r['points_b']} | "
                f"{o['a']['k']}/{o['a']['n']} ({_n(o['a']['p'])}) | "
                f"{o['b']['k']}/{o['b']['n']} ({_n(o['b']['p'])}) | {_n(o['diff'])} | "
                f"[{_n(o['diff_lo'])}, {_n(o['diff_hi'])}] | {_n(o['p_value'], 4)} | "
                f"{_n(r['occupancy_q'], 4)} | {'yes' if r['occupancy_survives_bh'] else 'no'} | "
                f"{'yes' if r['occupancy_gated'] else 'no'} | {r['landing_verdict']} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--json", action="store_true", help="dump the whole report block")
    args = ap.parse_args()
    rep = family_report(fetch_bouts())
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json else markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
