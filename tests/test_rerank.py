"""Tests for the re-ranking distribution math (aggregation + blend)."""

from __future__ import annotations

import pytest

from analysis.rerank import aggregate_class_probs, blend, normalize
from cv.vocab_map import NodeRef


@pytest.fixture
def index() -> dict[str, NodeRef]:
    """Minimal vocab index mirroring the app's position nodes."""
    return {
        "mount": NodeRef(name="Montada", type="control"),
        "closed guard": NodeRef(name="Guarda Fechada", type="guard"),
        "side control": NodeRef(name="Controle Lateral", type="control"),
    }


class TestAggregateClassProbs:
    def test_same_node_summed(self, index: dict[str, NodeRef]) -> None:
        probs = {"mount_top": 0.6, "mount_bottom": 0.3, "guard_top": 0.1}
        agg = aggregate_class_probs(probs, index)
        assert agg["Montada"] == pytest.approx(0.9)
        assert agg["Guarda Fechada"] == pytest.approx(0.1)

    def test_unmapped_class_kept_under_position(self, index: dict[str, NodeRef]) -> None:
        probs = {"flying_armbar_top": 0.7, "mount_top": 0.3}
        agg = aggregate_class_probs(probs, index)
        assert agg["flying_armbar"] == pytest.approx(0.7)
        assert agg["Montada"] == pytest.approx(0.3)

    def test_empty_input(self, index: dict[str, NodeRef]) -> None:
        assert aggregate_class_probs({}, index) == {}


class TestNormalize:
    def test_sums_to_one(self) -> None:
        d = normalize({"a": 2.0, "b": 3.0, "c": 5.0})
        assert sum(d.values()) == pytest.approx(1.0)

    def test_empty(self) -> None:
        assert normalize({}) == {}

    def test_all_zero(self) -> None:
        assert normalize({"a": 0.0, "b": 0.0}) == {}

    def test_single_key(self) -> None:
        assert normalize({"x": 42.0}) == {"x": 1.0}


class TestBlend:
    def test_alpha_one_equals_agg(self) -> None:
        agg = {"a": 0.3, "b": 0.7}
        prior = {"b": 1.0}
        result = blend(agg, prior, alpha=1.0)
        assert result == pytest.approx({"a": 0.3, "b": 0.7})

    def test_alpha_zero_equals_prior(self) -> None:
        agg = {"a": 1.0}
        prior = {"b": 0.4, "c": 0.6}
        result = blend(agg, prior, alpha=0.0)
        assert result == pytest.approx({"b": 0.4, "c": 0.6})

    def test_empty_prior_returns_normalized_agg(self) -> None:
        agg = {"a": 2.0, "b": 3.0}
        result = blend(agg, {}, alpha=0.7)
        assert result == pytest.approx({"a": 0.4, "b": 0.6})

    def test_favors_key_strong_in_both(self) -> None:
        agg = {"a": 0.9, "b": 0.1}
        prior = {"a": 0.1, "b": 0.9}
        result = blend(agg, prior, alpha=0.5)
        # geometric mean: a = sqrt(0.9*0.1)=0.3, b = sqrt(0.1*0.9)=0.3
        # after normalize: both 0.5
        assert result["a"] == pytest.approx(0.5)
        assert result["b"] == pytest.approx(0.5)

    def test_sums_to_one(self) -> None:
        agg = {"a": 0.8, "b": 0.2}
        prior = {"b": 0.6, "c": 0.4}
        result = blend(agg, prior, alpha=0.7)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_alpha_clamp(self) -> None:
        agg = {"x": 1.0}
        prior = {"y": 1.0}
        assert blend(agg, prior, alpha=1.5) == blend(agg, prior, alpha=1.0)
        assert blend(agg, prior, alpha=-0.5) == blend(agg, prior, alpha=0.0)
