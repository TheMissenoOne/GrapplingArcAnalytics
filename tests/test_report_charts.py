"""analysis.report_charts — pure SVG string generation, no dependency, no I/O.

Every generator must return a string containing ``<svg`` and never the literal ``NaN`` or
``Infinity`` (a divide-by-zero or a missing key reaching a coordinate would print one of those
straight into the markup)."""

from __future__ import annotations

from analysis import report_charts as rc


def _assert_clean_svg(svg: str) -> None:
    assert "<svg" in svg
    assert "NaN" not in svg
    assert "Infinity" not in svg


def test_stacked_bar_single() -> None:
    _assert_clean_svg(rc.stacked_bar_single(0.95, "Helena Crevar"))


def test_stacked_bar_single_zero_share() -> None:
    _assert_clean_svg(rc.stacked_bar_single(0.0, "Ninguém"))


def test_profile_bars_com_baseline() -> None:
    category = {
        "peso_igual": {"submission": 0.5, "pass": 0.5},
        "ponderado_por_evento": {"submission": 0.9, "pass": 0.1},
        "leave_one_out": {"submission": 0.0, "pass": 1.0},
    }
    baseline = {
        "peso_igual": {"submission": 0.4, "pass": 0.6},
        "ponderado_por_evento": {"submission": 0.4, "pass": 0.6},
    }
    _assert_clean_svg(rc.profile_bars(["submission", "pass"], category, baseline))


def test_profile_bars_sem_baseline() -> None:
    category = {"peso_igual": {}, "ponderado_por_evento": {}, "leave_one_out": {}}
    _assert_clean_svg(rc.profile_bars([], category, None))


def test_ranked_bars() -> None:
    _assert_clean_svg(rc.ranked_bars([("Armbar", 12), ("Kimura", 4)]))


def test_ranked_bars_vazio() -> None:
    _assert_clean_svg(rc.ranked_bars([]))


def test_divergent_lollipop() -> None:
    rows = [("Armbar", 1.8, "alta"), ("Kimura", -2.4, "baixa"), ("Guilhotina", 0.0, None)]
    _assert_clean_svg(rc.divergent_lollipop(rows))


def test_divergent_lollipop_vazio() -> None:
    _assert_clean_svg(rc.divergent_lollipop([]))


def test_scatter_reward_risk() -> None:
    points = [("Back Control", 0.2, 0.6, 12), ("Half Guard", 0.5, 0.1, 3)]
    _assert_clean_svg(rc.scatter_reward_risk(points, baseline_point=(0.3, 0.4)))


def test_scatter_reward_risk_sem_baseline_nem_pontos() -> None:
    _assert_clean_svg(rc.scatter_reward_risk([], baseline_point=None))


def test_inline_bar() -> None:
    _assert_clean_svg(rc.inline_bar(4, 10))


def test_inline_bar_max_zero_nao_divide_por_zero() -> None:
    _assert_clean_svg(rc.inline_bar(0, 0))
