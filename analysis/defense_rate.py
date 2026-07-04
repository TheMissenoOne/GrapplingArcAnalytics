"""ELO-adjusted Defense Rate — how well an athlete stops each category of attack.

For each defensive category (takedown, guard pass, sweep, positional control,
submission), the rate = share of the OPPONENT's attempts of that type that failed,
**weighted by opponent ELO** — defending a 2000-ELO opponent's takedown counts far
more than a 900-ELO one. An opponent event with ``successful is False`` = defended;
undefined ``successful`` defaults to landed (the app's convention), i.e. not defended.

    from analysis.defense_rate import defense_profile
    dp = defense_profile(athlete_id, matches, session)   # {category: {rate, attempts, elo_wt}}
"""

from __future__ import annotations

from typing import Any

# The five attack categories we score defense against (event ``type`` values).
CATEGORIES = ("takedown", "pass", "sweep", "control", "submission")


def _accumulate(athlete_id: str, matches: list[Any], session: Any) -> dict[str, dict[str, float]]:
    """Single pass: ELO-weighted defended/attempt sums per category (opp ELO once per match)."""
    from db.repository import opponent_input_elo

    acc = {c: {"defended_w": 0.0, "attempt_w": 0.0, "n": 0.0} for c in CATEGORIES}
    for m in matches:
        seq = getattr(m, "sequence", None)
        if not seq:
            continue
        opp_elo = opponent_input_elo(m, athlete_id, session)  # per MATCH, not per event
        for e in seq:
            # Only the OPPONENT's offensive attempts test THIS athlete's defense.
            if e.get("actor_id") == athlete_id:
                continue
            typ = str(e.get("type", ""))
            if typ not in acc:
                continue
            acc[typ]["attempt_w"] += opp_elo
            acc[typ]["n"] += 1
            if e.get("successful") is False:  # opponent's attempt failed → defended
                acc[typ]["defended_w"] += opp_elo
    return acc


def elo_adjusted_defense_rate(
    athlete_id: str, matches: list[Any], session: Any
) -> dict[str, dict[str, Any]]:
    """Per-category ELO-weighted defense rate. ``rate`` is None when never attempted."""
    return {
        c: {
            "rate": round(a["defended_w"] / a["attempt_w"], 3) if a["attempt_w"] else None,
            "attempts": int(a["n"]),
            "elo_wt": round(a["attempt_w"] / a["n"], 1) if a["n"] else 0.0,  # avg opp ELO faced
        }
        for c, a in _accumulate(athlete_id, matches, session).items()
    }


def defense_profile(athlete_id: str, matches: list[Any], session: Any) -> dict[str, Any]:
    """Per-category defense rates + an ELO-weighted overall rate — one pass, no re-fetch."""
    acc = _accumulate(athlete_id, matches, session)
    tot_def = sum(a["defended_w"] for a in acc.values())
    tot_att = sum(a["attempt_w"] for a in acc.values())
    return {
        "categories": {
            c: {
                "rate": round(a["defended_w"] / a["attempt_w"], 3) if a["attempt_w"] else None,
                "attempts": int(a["n"]),
                "elo_wt": round(a["attempt_w"] / a["n"], 1) if a["n"] else 0.0,
            }
            for c, a in acc.items()
        },
        "overall": round(tot_def / tot_att, 3) if tot_att else None,
    }


# ── self-check (ponytail: runnable, no DB) ────────────────────────────────────
def _demo() -> None:
    class _M:
        def __init__(self, seq: list[dict[str, Any]], a: str, b: str) -> None:
            self.sequence, self.athlete_a_id, self.athlete_b_id = seq, a, b

    class _Session:
        def get(self, _model: Any, oid: str) -> Any:
            return type("A", (), {"rank_elo": 2000.0 if oid == "strong" else 800.0})()

    # Monkeypatch opponent_input_elo indirectly via a fake session + repository import.
    import analysis.defense_rate as mod

    def fake_opp_elo(match: Any, athlete_id: str, session: Any) -> float:
        other = match.athlete_b_id if match.athlete_a_id == athlete_id else match.athlete_a_id
        return 2000.0 if other == "strong" else 800.0

    import db.repository as repo
    orig = repo.opponent_input_elo
    repo.opponent_input_elo = fake_opp_elo  # type: ignore[assignment]
    try:
        me = "me"
        # vs strong: two takedown attempts, one defended. vs weak: one takedown, defended.
        matches = [
            _M([{"actor_id": "strong", "type": "takedown", "successful": False},
                {"actor_id": "strong", "type": "takedown", "successful": True}], me, "strong"),
            _M([{"actor_id": "weak", "type": "takedown", "successful": False},
                {"actor_id": me, "type": "sweep"}], me, "weak"),
        ]
        dp = mod.defense_profile(me, matches, None)
        td = dp["categories"]["takedown"]
        assert td["attempts"] == 3, td
        # Weighted: defended = 2000 (strong) + 800 (weak) = 2800; attempts = 2000*2 + 800 = 4800.
        assert abs(td["rate"] - round(2800 / 4800, 3)) < 1e-6, td["rate"]
        # The athlete's own sweep is not counted as a defense attempt.
        assert dp["categories"]["sweep"]["attempts"] == 0
        assert dp["overall"] == round(2800 / 4800, 3)
        print("defense_rate demo OK")
    finally:
        repo.opponent_input_elo = orig  # type: ignore[assignment]


if __name__ == "__main__":
    _demo()
