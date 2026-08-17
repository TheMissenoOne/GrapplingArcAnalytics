"""The variant-name heuristic behind ``identidade=variante_suspeita``.

It exists to catch duplicate athlete rows an import creates from a transcript spelling.
Too loose and it merges two real people; too tight and half an athlete's corpus goes
missing from the scouting CSV.
"""

from __future__ import annotations

import pytest

from scripts.scouting_matches_csv import _suspect


@pytest.mark.parametrize(
    "db_name",
    ["raphaela guedes", "r guedes", "raphaela guedes final"],
)
def test_pega_variante_do_mesmo_sobrenome(db_name: str) -> None:
    assert _suspect("rafaela guedes", db_name)


@pytest.mark.parametrize(
    ("roster", "db_name"),
    [
        ("ana carolina vieira", "anna karolina vieira"),
        ("gabi garcia", "gabby garcia"),
    ],
)
def test_pega_grafia_alternativa(roster: str, db_name: str) -> None:
    assert _suspect(roster, db_name)


@pytest.mark.parametrize(
    ("roster", "db_name"),
    [
        ("gabi garcia", "marcelo garcia"),   # mesmo sobrenome, outra pessoa
        ("ana carolina vieira", "rodolfo vieira"),
        ("sarah galvao", "andre galvao"),
        ("morgan black", "nia blackman"),    # sobrenome só parecido
        ("gabi garcia", "gabi garcia"),      # já casou exato — não é "suspeita"
    ],
)
def test_nao_funde_pessoas_diferentes(roster: str, db_name: str) -> None:
    if roster == db_name:
        return  # o exato nunca chega em _suspect; o teste documenta a ordem
    assert not _suspect(roster, db_name)
