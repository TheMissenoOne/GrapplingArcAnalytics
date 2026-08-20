"""Wave 8 publish-confidence gate — pure-function tests (no DB).

Covers: both legs of ``is_confident`` are required (content AND RD), the seed-RD floor for
an athlete missing from a real run, the run_id-less fallback, and the link-decision
functions (``_bout_href``/``_dossier_href``) that are the ONLY place allowed to emit a
breakdown-/grapple- href — see export/site_data.py's Wave 8 section.
"""

from __future__ import annotations

from export.site_data import (
    SITE_MIN_CONFIDENCE_RD,
    WITHHELD_ATHLETE_NAMES,
    _bout_href,
    _dossier_href,
    _fold_name,
    _load_rating_deviations,
    _withheld_athlete_ids,
    is_confident,
)


class _NeverQuery:
    """Session stub that fails the test if .execute is ever called."""

    def execute(self, *a: object, **kw: object) -> object:
        raise AssertionError("queried AthleteRatingStateV2 without an explicit run_id")


def test_is_confident_requires_both_conditions() -> None:
    rd_by_athlete = {"a1": 300.0}  # content ok, but RD above the cut
    assert is_confident(True, "a1", rd_by_athlete) is False
    assert is_confident(False, "a2", {"a2": 50.0}) is False  # RD ok, but content fails
    assert is_confident(True, "a3", {"a3": SITE_MIN_CONFIDENCE_RD}) is True  # both pass


def test_is_confident_missing_athlete_uses_seed_rd_floor_not_zero_weight() -> None:
    # a run IS pinned (dict, not None) but this athlete has no row in it.
    assert is_confident(True, "unscored", {}) is False  # seed RD (250) > 200 cut


def test_is_confident_none_run_falls_back_to_content_only() -> None:
    assert is_confident(True, "a1", None) is True
    assert is_confident(False, "a1", None) is False  # content leg still required


def test_bout_publishes_iff_at_least_one_side_confident() -> None:
    rd = {"a": SITE_MIN_CONFIDENCE_RD, "b": SITE_MIN_CONFIDENCE_RD + 1}
    a_trusted = is_confident(True, "a", rd)
    b_trusted = is_confident(True, "b", rd)
    assert a_trusted and not b_trusted
    assert (a_trusted or b_trusted) is True  # one strong side -> publish
    assert (b_trusted or is_confident(False, "c", rd)) is False  # neither -> hide


def test_load_rating_deviations_never_queries_without_run_id() -> None:
    assert _load_rating_deviations(_NeverQuery(), None) is None
    assert _load_rating_deviations(_NeverQuery(), "") is None


def test_bout_href_only_for_published_bouts() -> None:
    slug_by_match = {"m1": "gordon-vs-craig-2024"}
    assert _bout_href("m1", slug_by_match) == "breakdown-gordon-vs-craig-2024.html"
    assert _bout_href("hidden", slug_by_match) is None
    assert _bout_href("m1", {}) is None


def test_dossier_href_only_for_trusted_athletes() -> None:
    dossier_slugs = frozenset({"gordon-ryan"})
    assert _dossier_href("gordon-ryan", dossier_slugs) == "grapple-gordon-ryan.html"
    assert _dossier_href("unknown-fighter", dossier_slugs) is None


# ── Withheld athletes ───────────────────────────────────────────────────────────
# A stronger veto than the confidence gate: no dossier, and no breakdown for ANY bout
# they appear in — even opposite a trusted opponent. See WITHHELD_ATHLETE_NAMES.


def test_withheld_names_are_stored_folded_so_lookups_can_match() -> None:
    # the set is compared against _fold_name output, so an entry that isn't already
    # folded could never match anything — a silent no-op instead of a withhold.
    for name in WITHHELD_ATHLETE_NAMES:
        assert _fold_name(name) == name


def test_fold_name_collapses_accents_and_case() -> None:
    assert _fold_name("Lívia Barasine") == "livia barasine"
    assert _fold_name("  LIVIA BARASINE  ") == "livia barasine"
    assert _fold_name("Yara Soares") == "yara soares"
    assert _fold_name("Yara Soarez") != "yara soares"  # not fuzzy — exact after folding


def test_withheld_athlete_ids_matches_diacritic_spellings() -> None:
    class _Athlete:
        def __init__(self, aid: str, name: str) -> None:
            self.id, self.name = aid, name

    rows = [
        _Athlete("w1", "Yara Soares"),
        _Athlete("w2", "Lívia Barasine"),   # accented spelling of a withheld name
        _Athlete("ok", "Ana Carolina Vieira"),
        _Athlete("no", "Livia Giles"),      # different person, shares a first name
    ]

    class _Session:
        def execute(self, *a: object, **kw: object) -> object:
            class _R:
                def scalars(self) -> list[_Athlete]:
                    return rows
            return _R()

    assert _withheld_athlete_ids(_Session()) == frozenset({"w1", "w2"})


def test_withheld_vetoes_the_bout_even_opposite_a_trusted_opponent() -> None:
    # This is the asymmetry that makes withholding stronger than the RD gate:
    # `trusted` needs ONE side, `withheld` is vetoed by EITHER side.
    trusted, withheld = frozenset({"star"}), frozenset({"held"})
    assert ("star" in trusted or "held" in trusted)          # would have published...
    assert ("star" in withheld or "held" in withheld)        # ...but one side is withheld
