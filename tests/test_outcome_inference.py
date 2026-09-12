"""D7's internal-action outcome inference — one small fixture per rule, plus the owner's own
pinned worked examples (docs/research/rrb_round_rating_prereg.md §D2) and the "no information"
refusals. See docs/taxonomy/05_INFERENCIA_DE_RESULTADO.md for the contract this pins."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from analysis.outcome_inference import (
    DEFAULT_RULES,
    ActionRef,
    StateRef,
    infer_outcome,
    r0_exit_table,
    r1_orientation_flip,
    r2_type_expectation,
    r3_actor_consistency,
)

ROOT = Path(__file__).resolve().parents[1]

# ── the owner's own worked examples, pinned ─────────────────────────────────────
def test_owner_example_sweep_lands_english() -> None:
    prev = StateRef("Half Guard", "guard", "you")
    nxt = StateRef("Mount", "control", "you")
    assert infer_outcome(prev, ActionRef("Sweep", "sweep"), nxt, "you") is True


def test_owner_example_submission_attempt_fails_english() -> None:
    back_control = StateRef("Back Control", "control", "you")
    assert infer_outcome(back_control, ActionRef("RNC", "submission"), back_control, "you") is False


def test_owner_examples_hold_in_pt_br() -> None:
    prev = StateRef("Meia Guarda", "guard", "you")
    nxt = StateRef("Montada", "control", "you")
    assert infer_outcome(prev, ActionRef("Raspagem", "sweep"), nxt, "you") is True

    costas = StateRef("Costas", "control", "you")
    assert infer_outcome(costas, ActionRef("Mata Leao", "submission"), costas, "you") is False


# ── R0 — the exit-orientation table ─────────────────────────────────────────────
def test_r0_no_next_state_is_unresolved_without_terminal() -> None:
    assert r0_exit_table(None, ActionRef("Armlock", "submission"), None, "you") is None


def test_r0_terminal_submission_with_no_next_state_lands() -> None:
    out = r0_exit_table(None, ActionRef("Armlock", "submission"), None, "you", terminal=True)
    assert out is True


def test_r0_neutral_declared_exit_never_guesses() -> None:
    # `escape`'s declared exit orientation is `neutral` — a declared no-claim, not a guess.
    guard = StateRef("Closed Guard", "guard", "you")
    assert r0_exit_table(None, ActionRef("Stand-up Escape", "escape"), guard, "you") is None


# ── R1 — orientation flip, sweep/reversal only ──────────────────────────────────
def test_r1_only_fires_for_sweep_and_reversal() -> None:
    top = StateRef("Mount", "control", "you")
    assert r1_orientation_flip(None, ActionRef("Guard Pass", "pass"), top, "you") is None


def test_r1_still_bottom_same_guard_family_fails() -> None:
    guard_before = StateRef("Half Guard", "guard", "you")
    guard_after = StateRef("Closed Guard", "guard", "you")
    out = r1_orientation_flip(guard_before, ActionRef("Sweep", "sweep"), guard_after, "you")
    assert out is False


def test_r1_lands_on_top() -> None:
    guard_before = StateRef("Half Guard", "guard", "you")
    top = StateRef("Mount", "control", "you")
    assert r1_orientation_flip(guard_before, ActionRef("Sweep", "sweep"), top, "you") is True


# ── R2 — type -> expected next-state family ─────────────────────────────────────
def test_r2_guard_pull_lands_in_a_guard() -> None:
    guard = StateRef("Closed Guard", "guard", "you")
    assert r2_type_expectation(None, ActionRef("Guard Pull", "guard"), guard, "you") is True


def test_r2_back_take_needs_a_controlling_state() -> None:
    back_control = StateRef("Back Control", "control", "you")
    back_take = ActionRef("Back Take", "transition")
    assert r2_type_expectation(None, back_take, back_control, "you") is True
    mount = StateRef("Mount", "control", "you")
    assert r2_type_expectation(None, back_take, mount, "you") is False


def test_r2_escape_to_neutral_lands() -> None:
    # A `transition`-typed state with no positional claim reads `neutral` — the same reading a
    # real "back on her feet" state gets.
    neutral = StateRef("Standing", "transition", "you")
    assert r2_type_expectation(None, ActionRef("Stand-up Escape", "escape"), neutral, "you") is True


def test_r2_no_next_state_is_unresolved() -> None:
    assert r2_type_expectation(None, ActionRef("Armlock", "submission"), None, "you") is None


# ── R3 — actor consistency, gated by reliability ────────────────────────────────
def test_r3_state_owned_by_other_actor_means_failed() -> None:
    other_owned = StateRef("Mount", "control", "partner")
    assert r3_actor_consistency(None, ActionRef("Sweep", "sweep"), other_owned, "you") is False


def test_r3_never_infers_success_from_agreement() -> None:
    same_owner = StateRef("Mount", "control", "you")
    assert r3_actor_consistency(None, ActionRef("Sweep", "sweep"), same_owner, "you") is None


def test_r3_gated_by_actor_readable() -> None:
    other_owned = StateRef("Mount", "control", "partner")
    out = r3_actor_consistency(None, ActionRef("Sweep", "sweep"), other_owned, "you",
                               actor_readable=False)
    assert out is None


# ── no-information refusal, and composition order ───────────────────────────────
def test_no_information_resolves_to_none() -> None:
    unrelated = StateRef("Standing", "transition", "you")
    unmapped = ActionRef("Something Unmapped", "transition")
    assert infer_outcome(None, unmapped, unrelated, "you") is None


def test_composition_stops_at_first_non_none_rule() -> None:
    # R0 alone already resolves a landed pass (exit orientation `top`); R1/R2 must never be
    # asked to answer differently for the same case.
    guard = StateRef("Closed Guard", "guard", "you")
    top = StateRef("Mount", "control", "you")
    action = ActionRef("Guard Pass", "pass")
    assert infer_outcome(guard, action, top, "you", rules=DEFAULT_RULES) is True
    assert infer_outcome(guard, action, top, "you", rules=(r0_exit_table,)) is True


def test_generator_check_flag_is_green() -> None:
    """`data/fixtures/outcomeInferenceGolden.json` on disk must match the generator's own
    output — a stale golden is a defect this test exists to catch."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.export_outcome_inference_fixtures", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
