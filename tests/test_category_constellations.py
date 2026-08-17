"""Gate de publicação da seção 5 — o ramo que decide o que o relatório AFIRMA.

Uma constelação sustentada por uma única atleta não pode virar "tendência da categoria":
em +65 kg uma atleta concentra 86% dos eventos próprios, então sem este gate os maiores
lifts do relatório seriam todos o jogo de uma pessoa com cara de meta da divisão.
"""

from __future__ import annotations

from analysis.category_constellations import MIN_SUPPORT_ATHLETES, gate_text
from analysis.constellations import Stability


def test_athlete_driven_nao_passa_e_nomeia_a_atleta() -> None:
    passa, texto = gate_text("Foot Sweep", Stability.ATHLETE_DRIVEN, 1, "Ana Carolina Vieira")
    assert passa is False
    assert "Ana Carolina Vieira" in texto, "o texto tem que nomear quem sustenta o padrão"


def test_suporte_de_uma_atleta_nao_passa_mesmo_se_estavel() -> None:
    """Estabilidade alta com uma única contribuinte ainda é o jogo de uma pessoa."""
    passa, texto = gate_text("Inverted De La Riva Guard", Stability.STABLE, 1, None)
    assert passa is False
    assert "insuficiente" in texto


def test_varias_atletas_e_estavel_passa() -> None:
    passa, texto = gate_text("Back Control", Stability.PARTIALLY_STABLE, 4, None)
    assert passa is True
    assert "4 atletas" in texto
    assert "Back Control" in texto


def test_limiar_de_suporte_e_o_declarado() -> None:
    """O gate usa MIN_SUPPORT_ATHLETES, não um número mágico solto no meio do texto."""
    assert gate_text("X", Stability.STABLE, MIN_SUPPORT_ATHLETES, None)[0] is True
    assert gate_text("X", Stability.STABLE, MIN_SUPPORT_ATHLETES - 1, None)[0] is False
