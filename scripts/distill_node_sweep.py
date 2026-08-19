"""Destila o sweep do ADR-03 para o resumo versionado que o teste de sombra lê.

    uv run python -m analysis.rating_v2.node_replay      # gera reports/rating_v2/node-sweep.json
    uv run python -m scripts.distill_node_sweep          # destila para data/rating_v2/

``reports/`` está no ``.gitignore`` — é diretório de artefato local. Mas a decisão do ADR-03 se
apoia naquele arquivo, e uma decisão cuja evidência não está no repo é uma decisão que ninguém
consegue auditar (e um teste que não roda em CI). Então os campos em que a decisão se apoia — e
só eles — vivem versionados em ``data/rating_v2/node_sweep_summary.json``, ao lado do
``glicko2_golden.json``, que já existe pelo mesmo motivo.

Isto é destilação, não cópia: o sweep bruto tem por célula um bloco por critério; aqui fica um
número por critério. Quem quiser o detalhe roda o replay.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "rating_v2" / "node-sweep.json"
TARGET = ROOT / "data" / "rating_v2" / "node_sweep_summary.json"


def distill(raw: dict, measured_at: str) -> dict:
    return {
        "_comment": (
            "Destilado de reports/rating_v2/node-sweep.json, que NAO e versionado "
            "(reports/ esta no .gitignore). Guarda so os campos em que a decisao do ADR-03 se "
            "apoia, para que a evidencia viva junto da decisao e o teste rode em CI. Regenerar: "
            "uv run python -m analysis.rating_v2.node_replay, depois "
            "uv run python -m scripts.distill_node_sweep."
        ),
        "measured_at": measured_at,
        "train_cutoff": raw["train_cutoff"],
        "holdout_year": raw["holdout_year"],
        "coverage": raw["coverage"],
        "total_node_bouts": raw["total_node_bouts"],
        "cells": [
            {
                "node_weight": c["node_weight"],
                "node_initial_rd": c["node_initial_rd"],
                "node_tau": c["node_tau"],
                "improves_log_loss": c["criterion_1_log_loss"]["improves"],
                "baseline_log_loss": c["criterion_1_log_loss"]["baseline_log_loss"],
                "candidate_log_loss": c["criterion_1_log_loss"]["candidate_log_loss"],
                "low_rd_node_observations": c["criterion_2_calibration"][
                    "low_rd_node_observations"
                ],
                "mean_spearman": c["criterion_3_bootstrap_stability"]["mean_spearman"],
                "median_bouts_observed": c["criterion_4_fraction_at_prior"][
                    "median_bouts_observed"
                ],
                "fraction_at_prior": c["criterion_4_fraction_at_prior"]["fraction_at_prior"],
            }
            for c in raw["sweep"]
        ],
    }


def main() -> int:
    if not SOURCE.exists():
        print(f"faltando {SOURCE} — rode `uv run python -m analysis.rating_v2.node_replay` antes")
        return 1
    # O sweep bruto não carrega data própria; a do arquivo é o que existe, e é melhor do que
    # uma constante que envelhece em silêncio na próxima execução.
    measured_at = datetime.fromtimestamp(SOURCE.stat().st_mtime, UTC).date().isoformat()
    TARGET.write_text(
        json.dumps(distill(json.loads(SOURCE.read_text()), measured_at), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"escrito {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
