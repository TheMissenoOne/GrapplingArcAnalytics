#!/usr/bin/env python
"""Build `data/rating/rrb_dominance_calibration.json` — the E4 artefact of prereg §E.

    uv run python -m scripts.build_rrb_dominance_calibration            # write
    uv run python -m scripts.build_rrb_dominance_calibration --check    # rebuild and diff, write nothing
    uv run python -m scripts.build_rrb_dominance_calibration --stdout   # print, write nothing

Only runs (and should only be re-run) when the study's E1 death rule PASSES —
`docs/research/rrb_round_rating_study.md` §E states the verdict; this script does not re-derive it,
it packages the calibration §E1 already selected (temperature scaling on `actions_states`) into a
self-contained artefact a future App port can consume WITHOUT re-deriving Z from the corpus.

**Self-contained on purpose.** The artefact snapshots the 12-code `actions_states` value table
(`markov_action_weights.json`'s `global` block under `terminal=marginal`) rather than pointing at
that file — a consumer that only has this JSON must still be able to reproduce `Z`.

Privacy class A, public competition data: everything here is fit on `matches` rows only. The
owner's rounds inform the STUDY's death rule (docs/research), never this artefact's numbers
(root CLAUDE.md, "Public vs Private Data" — public → private is the only permitted direction, and
this artefact only ever flows outward from the public corpus).

READ-ONLY against prod `matches` (`scripts.research.rrb_round_rating.load_corpus_bouts`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.markov_weights import block_for_family, load_markov_weights  # noqa: E402
from analysis.rating_v2.rrb_dominance import (  # noqa: E402
    CALIBRATION_METHODS,
    CALIBRATION_TIE_NATS,
    action_values,
    calibration_fit,
    marginal_submission_share,
)
from scripts.research.rrb_round_rating import (  # noqa: E402
    _corpus_t2_dominance,
    load_corpus_bouts,
)

OUT = REPO / "data" / "rating" / "rrb_dominance_calibration.json"

#: Bump when the artefact's SHAPE changes, never for a corpus refresh — a consumer pins the
#: shape, and `fitted_on`/the corpus digest already say the numbers moved.
VERSION = 1

#: prereg §E4's fallback when the LOO clamp sweep is inconclusive or not run — the `hier4`
#: precedent already shipped in this study's recommendation, and the same order of magnitude as
#: the `difficulty` slider's own ±280 band.
CLAMP_ELO_DEFAULT = 400.0

CAVEATS: tuple[str, ...] = (
    "Esta calibração reescala apenas a ESCALA de `Z` (`actions_states`, terminal=marginal, γ=1) — "
    "ela não re-estima `v(code)`. A tabela de valores por código é um SNAPSHOT do bloco `global` "
    "de `markov_action_weights.json` sob esse terminal, não um ponteiro vivo: um consumidor que só "
    "tiver este arquivo ainda consegue reproduzir Z.",
    "O clamp de Elo NÃO vem de uma varredura leave-one-out dedicada — é o precedente do `hier4` "
    "(±400) citado em docs/research/rrb_round_rating_study.md, aplicado aqui porque a varredura "
    "LOO completa (prereg §E2) não foi executada nesta passada. Ver `docs/research/"
    "rrb_round_rating_study.md` §E para o que foi de fato medido.",
    "Esta calibração é fit UMA VEZ no corpus público inteiro (`fitted_on`), nunca por usuário — "
    "decisão vinculante do dono, prereg §E: sem camada pessoal, sem re-fit on-device.",
)


def corpus_digest(rows: list[tuple[int, float, int]]) -> str:
    """Fingerprint of the (year, Z, label) rows the calibration is fit on."""
    h = hashlib.sha256()
    for yr, z, y in sorted(rows, key=lambda r: (r[0], round(r[1], 10), r[2])):
        h.update(f"{yr}|{z:.10f}|{y}\n".encode())
    return h.hexdigest()


def build(generated: str) -> dict[str, Any]:
    weights_doc = load_markov_weights()
    if weights_doc is None:
        raise SystemExit("data/rating/markov_action_weights.json ausente — nada para calibrar")

    block = block_for_family("other", weights_doc) or {}
    marginal = marginal_submission_share(weights_doc)
    vals = action_values(block, terminal="marginal", marginal=marginal)

    bouts = load_corpus_bouts()
    from sqlalchemy import text

    from db.base import get_engine

    with get_engine().connect() as conn:
        win_types = {
            str(r["id"]): str(r["win_type"] or "")
            for r in conn.execute(text("SELECT id, win_type FROM matches WHERE status='final'")).mappings()
        }

    rows = _corpus_t2_dominance(bouts, win_types, vals)
    if not rows:
        raise SystemExit("nenhuma unidade T2 no corpus — recusando escrever uma calibração vazia")

    # The method itself (temperature/platt/isotonic) is NOT re-selected here — the study
    # (`docs/research/rrb_round_rating_study.md` §E1) already picked one by pooled chronological
    # validation log-loss with the simplest-wins tie-break; this artefact re-fits that SAME method
    # on the whole corpus, once, for deployment. If the study's choice changes, this constant does.
    method = "temperature"
    if method not in CALIBRATION_METHODS:
        raise SystemExit(f"método desconhecido: {method!r}")  # pragma: no cover - config guard

    all_z = [z for _, z, _ in rows]
    all_y = [float(y) for _, _, y in rows]
    params = calibration_fit(all_z, all_y, method)

    years = sorted({yr for yr, _, _ in rows})

    doc: dict[str, Any] = {
        "version": VERSION,
        "generated": generated,
        "signal": "actions_states",
        "definition": {
            "clean_label": "analysis.technique_match.clean_label — pt-BR/variant label resolver, "
            "applied BEFORE the Lamas mapper (prereg addendum §A1)",
            "node_key_to_lamas_code": "analysis.lamas_chain.lamas_state",
            "gamma": 1.0,
            "terminal": "marginal",
            "formula": "Z = mean(actions_Z, states_Z); actions_Z = sum(z_i)/n; "
            "states_Z = z of the LAST mapped step; z_i = +/-logit(value_table[code_i]) "
            "(own +, partner -)",
            "value_table": {k: round(v, 4) for k, v in sorted(vals.items())},
        },
        "calibration": {
            "method": method,
            "parameters": params,
            "tie_break_nats": CALIBRATION_TIE_NATS,
            "candidates_considered": list(CALIBRATION_METHODS),
        },
        "clamp_elo": {
            "value": CLAMP_ELO_DEFAULT,
            "source": "hier4 precedent (docs/research/rrb_round_rating_study.md); "
            "LOO clamp sweep not run in this pass (prereg §E2, stated as a gap, not assumed)",
        },
        "fitted_on": {
            "years": {"min": years[0], "max": years[-1]},
            "n_units": len(rows),
            "source": "prod `matches` where status='final' (public competition footage), "
            "T2 = finish-side from the prefix of SUBMISSION-decided bouts",
            "privacy_class": "A — public competition data",
        },
        "provenance": {
            "corpus_digest": corpus_digest(rows),
            "generator": "scripts/build_rrb_dominance_calibration.py",
            "study": "docs/research/rrb_round_rating_prereg.md §E, "
            "docs/research/rrb_round_rating_study.md §E",
            "runner": "scripts/research/rrb_round_rating.py:run_section_e1",
        },
        "caveats": list(CAVEATS),
    }
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true",
                    help="rebuild and diff against the committed file (ignoring `generated`)")
    ap.add_argument("--stdout", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    doc = build(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    text = json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    if args.stdout:
        print(text, end="")
        return 0
    if args.check:
        if not args.out.exists():
            print(f"MISSING {args.out}")
            return 1
        old = json.loads(args.out.read_text(encoding="utf-8"))
        new = json.loads(text)
        old.pop("generated", None)
        new.pop("generated", None)
        if old == new:
            print(f"ok — {args.out.relative_to(REPO)} matches the corpus and the code")
            return 0
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                print(f"DIFF {key}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO)} — {doc['fitted_on']['n_units']} units, "
          f"method={doc['calibration']['method']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
