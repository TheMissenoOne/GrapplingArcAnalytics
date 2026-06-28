"""Chess-style Elo engine for UFC fight histories — extracted for future use.

Adapted from **NBAtrev/UFC-Elo-Engine** (github.com/NBAtrev/UFC-Elo-Engine,
``UPDATEDufceloengine.py``): the standard Elo update with a +15% K bump on finishes
(KO / SUB). Stripped of the original's pandas/CSV driver so it is pure and dependency-free
— feed it any chronologically ordered list of fights.

Distinct from the graph-Elo replay in ``analysis.athlete_elo`` (per-athlete, ADCC-target
anchored). This is the flat head-to-head ladder over a whole fighter pool; the precomputed
UFC rankings live in ``elo_rankings/``.
"""

from __future__ import annotations

from dataclasses import dataclass

INITIAL_ELO = 1000.0
BASE_K = 40.0
FINISH_K_MULT = 1.15  # KO / SUB move Elo 15% harder than a decision


def expected_score(elo_a: float, elo_b: float) -> float:
    """Probability A beats B given their Elos."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def k_factor(method: str, base_k: float = BASE_K) -> float:
    """K for a result — bumped for a finish (method containing 'KO' or 'SUB')."""
    m = (method or "").upper()
    return base_k * FINISH_K_MULT if ("KO" in m or "SUB" in m) else base_k


def update_elo(
    winner_elo: float, loser_elo: float, k: float, result: str = "win"
) -> tuple[float, float]:
    """New (a, b) Elos. ``result``: 'win' (a beats b), 'draw', else no-contest (unchanged)."""
    expected_win = expected_score(winner_elo, loser_elo)
    if result == "win":
        return (round(winner_elo + k * (1 - expected_win), 2),
                round(loser_elo + k * (0 - (1 - expected_win)), 2))
    if result == "draw":
        return (round(winner_elo + k * (0.5 - expected_win), 2),
                round(loser_elo + k * (0.5 - (1 - expected_win)), 2))
    return round(winner_elo, 2), round(loser_elo, 2)


@dataclass(frozen=True)
class Fight:
    """One bout in chronological order. ``result``: 'win' = ``fighter_1`` won."""

    fighter_1: str
    fighter_2: str
    method: str = ""
    result: str = "win"


def compute_ratings(fights: list[Fight]) -> tuple[dict[str, float], dict[str, float]]:
    """Replay ordered fights → ``(current_elo, peak_elo)`` per fighter."""
    elo: dict[str, float] = {}
    peak: dict[str, float] = {}
    for f in fights:
        a = elo.setdefault(f.fighter_1, INITIAL_ELO)
        b = elo.setdefault(f.fighter_2, INITIAL_ELO)
        res = f.result if f.result in ("win", "draw") else "nc"
        na, nb = update_elo(a, b, k_factor(f.method), result=res)
        elo[f.fighter_1], elo[f.fighter_2] = na, nb
        peak[f.fighter_1] = max(peak.get(f.fighter_1, na), na)
        peak[f.fighter_2] = max(peak.get(f.fighter_2, nb), nb)
    return elo, peak
