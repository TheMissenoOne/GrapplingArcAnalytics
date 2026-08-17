"""scripts.apply_decision_results — pure resolution logic, no DB / no real xlsx.

Covers: variant-spelling winner via similarity, ambiguous winner NOT resolved, Empate ->
DRAW, non-actionable statuses touch nothing, year correction proposed only when it differs,
conflict detection when the DB already holds a different value.
"""

from __future__ import annotations

from scripts.apply_decision_results import STATUS_NO_ACTION, resolve_row

MATCH_ID = "m-1"


def _row(status: str, vencedor: str = "", metodo: str = "", ano_corrigido: str = "",
         evento_corrigido: str = "", **extra: str) -> dict[str, str]:
    row = {
        "prioridade_promocao": "", "evento": "Event X", "ano": "2020", "fase": "",
        "atleta_a": "A Fighter", "atleta_b": "B Fighter", "ambos_invisiveis": "nao",
        "tem_video": "nao", "match_id": MATCH_ID, "status_resultado": status,
        "vencedor": vencedor, "metodo": metodo, "ano_corrigido": ano_corrigido,
        "evento_corrigido": evento_corrigido, "confianca": "Alta", "observacao": "",
        "fonte_1": "https://example.com/1", "fonte_2": "",
    }
    row.update(extra)
    return row


def _match(**over: object) -> dict[str, object]:
    match = {
        "match_id": MATCH_ID,
        "athlete_a_name": "George Pearce", "athlete_a_id": "a-id",
        "athlete_b_name": "Cormac Anderson", "athlete_b_id": "b-id",
        "year": 2020, "event": "Event X", "win_type": "DECISION",
        "submission": None, "winner_id": None,
    }
    match.update(over)
    return match


def test_similarity_resolves_variant_spelling():
    row = _row("Vitória", vencedor="George Pearse", metodo="Armbar")
    res = resolve_row(row, _match())

    assert res.winner_id == "a-id"
    assert res.winner_name == "George Pearce"
    assert res.method == "similaridade"
    assert res.score is not None and res.score >= 0.82
    assert res.win_type_proposed == "SUBMISSION"
    assert res.submission_proposed == "Armbar"
    assert not res.needs_review


def test_ambiguous_winner_not_resolved():
    # Both candidates score >=0.82 but within 0.10 of each other -> refuse, don't guess.
    match = _match(athlete_a_name="John Smith", athlete_b_name="Jan Smith")
    row = _row("Vitória", vencedor="Jon Smith", metodo="Decisão unânime")
    res = resolve_row(row, match)

    assert res.winner_id is None
    assert res.method == "nenhum"
    assert res.needs_review
    assert "vencedor" in res.review_reason


def test_empate_becomes_draw_no_winner():
    row = _row("Empate")
    res = resolve_row(row, _match(win_type="DECISION"))

    assert res.winner_id is None
    assert res.win_type_proposed == "DRAW"
    assert not res.needs_review
    assert not res.conflicts


def test_no_action_statuses_touch_nothing():
    for status in STATUS_NO_ACTION:
        row = _row(status, vencedor="George Pearse", ano_corrigido="1999",
                    evento_corrigido="Other Event")
        res = resolve_row(row, _match())

        assert res.winner_id is None
        assert res.win_type_proposed is None
        assert res.year_proposed is None
        assert res.event_proposed is None
        assert res.needs_review, status


def test_year_correction_proposed_only_when_it_differs():
    changed = resolve_row(_row("Vitória", vencedor="George Pearce", metodo="Decisão unânime",
                                ano_corrigido="2021"), _match(year=2020))
    assert changed.year_proposed == 2021

    unchanged = resolve_row(_row("Vitória", vencedor="George Pearce", metodo="Decisão unânime",
                                  ano_corrigido="2020"), _match(year=2020))
    assert unchanged.year_proposed is None


def test_points_method_maps_to_points_not_decision():
    row = _row("Vitória", vencedor="George Pearce", metodo="Pontos 2-0")
    res = resolve_row(row, _match(win_type="DECISION"))

    assert res.win_type_proposed == "POINTS"
    assert res.submission_proposed is None
    assert not res.needs_review


def test_named_finish_without_generic_keyword_maps_to_submission():
    row = _row("Vitória", vencedor="George Pearce", metodo="Dead Orchard")
    res = resolve_row(row, _match(win_type="DECISION"))

    assert res.win_type_proposed == "SUBMISSION"
    assert res.submission_proposed == "Dead Orchard"
    assert not res.needs_review


def test_unmapped_method_flagged_for_review_but_winner_still_resolves():
    # DQ/injury/bare "Overtime" don't fit the 4-value win_type vocabulary — winner still
    # resolves normally, win_type stays untouched, but the row is surfaced for a human.
    row = _row("Vitória", vencedor="George Pearce", metodo="DQ")
    res = resolve_row(row, _match(win_type="DECISION"))

    assert res.winner_id == "a-id"
    assert res.win_type_proposed is None
    assert not res.conflicts
    assert res.needs_review
    assert "DQ" in res.review_reason


def test_conflict_detected_when_db_already_has_a_different_value():
    row = _row("Vitória", vencedor="George Pearce", metodo="Armbar")
    res = resolve_row(row, _match(submission="Kimura"))

    assert res.win_type_proposed == "SUBMISSION"
    # submission field already populated with something ELSE -> don't overwrite silently.
    assert res.submission_proposed is None
    assert "submission" in res.conflicts
    assert res.needs_review
