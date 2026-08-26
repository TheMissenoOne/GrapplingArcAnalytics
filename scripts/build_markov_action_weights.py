#!/usr/bin/env python
"""Build `data/rating/markov_action_weights.json` — the canonical action-weight artifact.

    uv run python -m scripts.build_markov_action_weights            # write
    uv run python -m scripts.build_markov_action_weights --check    # rebuild and diff, write nothing
    uv run python -m scripts.build_markov_action_weights --stdout   # print, write nothing

One weight per Lamas action code, derived from this corpus's RRB. The definition, the transform
and the fallback chain all live in ``analysis/rrb_progression.py`` and are pre-registered there;
this script only assembles the corpora, calls those pure functions and writes the file. Nothing
about the number is decided here.

**What ships and what does not.** ``global`` is the whole gated corpus. A ruleset family
(``adcc``, ``ibjjf``) ships a block **only when its own RRB clears its gates** — the corpus must
have absorbing bouts and the absorbing-bout coverage must be estimable
(``docs/research/lamas_chain_divisions.md`` §8.4). A refused family is OMITTED with its reason in
``provenance.families_omitted``, so a consumer falls back to ``global`` instead of reading twelve
zeroes as a measurement. Measured 2026-08-26: ``adcc`` ships (8 absorbing bouts of 53 usable),
``ibjjf`` does not (0 absorbing bouts of 11 usable).

**Determinism.** No bootstrap is run (``n_boot=0``), so no RNG is touched; every weight is
rounded to ``WEIGHT_PLACES``, which is what makes the file byte-identical across machines
regardless of the BLAS behind ``numpy.linalg.inv``. ``--check`` rebuilds and diffs everything
except ``generated``, and is the gate that says whether the committed artifact still matches the
corpus and the code.

READ-ONLY against prod ``matches``. Deliberately not ``db_session()``, which commits on clean
exit — this has no business writing to the database.

Privacy class A, public competition data: ``matches`` rows from published footage only, never a
user graph or a session (root CLAUDE.md, "Public vs Private Data").
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

from analysis.lamas_chain import STATES, chain_of, reward_risk, rrb  # noqa: E402
from analysis.rrb_progression import (  # noqa: E402
    NEUTRAL_SHARE,
    VALUE_SOURCES,
    WEIGHT_FLOOR,
    WEIGHT_PLACES,
    value_table,
    weights_from_value_table,
)
from analysis.ruleset_scoring import family_of  # noqa: E402

OUT = REPO / "data" / "rating" / "markov_action_weights.json"

#: Artifact schema version. Bump when a KEY or the transform changes, never for a corpus refresh
#: — a consumer pins the shape, and the corpus digest already says the numbers moved.
VERSION = 1

#: Families the artifact tries to publish, in file order. ``global`` is not a family: it is every
#: final bout, whatever its event tag, and it is the fallback a consumer uses when a family is
#: omitted.
FAMILIES: tuple[str, ...] = ("adcc", "ibjjf")

CAVEATS: tuple[str, ...] = (
    "Os pesos vêm da SHARE de absorção (`sub_share` do `lamas_chain.rrb`), não do `balance`. "
    "O `balance` deslocado foi REJEITADO: fora de SUB ele cabe todo em 0,09 de amplitude, o que "
    "daria 0,5 ± 0,04 para toda ação e uma redistribuição uniforme com outro nome.",
    "A AMPLITUDE É PEQUENA DE PROPÓSITO. O §8.6 do docs/research/lamas_chain_divisions.md mede "
    "por quê: a cadeia mistura mais rápido do que absorve, então a ação em que se está diz "
    "pouco sobre quem finaliza. Este artefato publica a medição, não uma amplificação dela. "
    "Consumidor que precisar de mais contraste está aplicando uma transformação própria e "
    "precisa justificá-la no PR dele.",
    "O peso de SUB é parcialmente CIRCULAR: boa parte das aparições de SUB é o passo terminal "
    "que absorve. `n_terminal` viaja em cada linha da procedência exatamente para que isso seja "
    "visível em vez de ser lido como achado.",
    "Os pesos NÃO estão normalizados. O consumidor renormaliza sobre as ações presentes na "
    "sequência que está pontuando; um vetor que soma 1 sobre doze estados estaria errado para "
    "todo subconjunto. `WEIGHT_FLOOR` é o que torna essa renormalização segura por invariante.",
    "Tudo aqui passa pela recusa por atribuição do `lamas_chain` (`one_sided` + `single_actor`) "
    "antes de entrar, porque o lado de cada transição é lido do `actor_id`.",
    "Comparável DENTRO de um bloco, não ENTRE blocos: a partição attempt/success segue o LOTE "
    "DE ANOTAÇÃO (`successful` ausente é lido como tentativa), e as famílias foram anotadas de "
    "formas diferentes (analysis/ruleset_scoring.comparability).",
)


def fetch_bouts() -> list[dict[str, Any]]:
    """Every FINAL bout, whatever its event tag. Ordered by id so the digest is stable."""
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from sqlalchemy import text

    from db.base import get_engine

    with get_engine().connect() as c:
        rows = c.execute(text("""
            select m.id::text, m.event, m.win_type, m.winner_id::text,
                   m.athlete_a_id::text, m.athlete_b_id::text, m.sequence
              from matches m
             where m.status = 'final'
             order by m.id
        """)).fetchall()
    return [{"id": r[0], "event": r[1], "win_type": r[2], "winner": r[3],
             "a_id": r[4], "b_id": r[5], "seq": list(r[6] or [])} for r in rows]


def corpus_digest(bouts: list[dict[str, Any]]) -> str:
    """A fingerprint of the INPUT, so a diff in the file can be traced to the corpus or the code.

    Bout id, win type, winner and the mapped chain — not the raw sequence: two annotation edits
    that do not change any Lamas action cannot change any weight, and a digest that moved anyway
    would send a reader looking for a number that did not.
    """
    h = hashlib.sha256()
    for b in sorted(bouts, key=lambda x: str(x["id"])):
        ch = chain_of(b)
        h.update(f"{b['id']}|{b.get('win_type')}|{b.get('winner')}|"
                 f"{','.join(s.state for s in ch.steps)}\n".encode())
    return h.hexdigest()


def block(bouts: list[dict[str, Any]]) -> dict[str, Any]:
    """One corpus → its value table, its weights and everything a reader needs to refuse them."""
    chains = [chain_of(b) for b in bouts]
    r = rrb(chains, n_boot=0)
    rr = reward_risk(chains, n_boot=0)
    values = value_table(r, rr)
    absorption = r["absorption"]
    return {
        "weights": weights_from_value_table(values),
        "estimable": bool(values["corpus_estimable"]),
        "reason_code": values["corpus_reason_code"],
        "bouts": len(bouts),
        "bouts_with_sequence": sum(1 for b in bouts if b.get("seq")),
        "usable_bouts": absorption["usable_bouts"],
        "bouts_refused": absorption["bouts_refused"],
        "absorbing_bouts": absorption["absorbing_bouts"],
        "absorbed_self": absorption["absorbed_self"],
        "absorbed_other": absorption["absorbed_other"],
        "absorbing_coverage": absorption["coverage"],
        "pooled_retention": values["pooled_retention"],
        "n_by_source": values["n_by_source"],
        "mixed_source": values["mixed_source"],
    }


def build(bouts: list[dict[str, Any]], generated: str) -> dict[str, Any]:
    """The whole artifact. Deterministic given ``bouts`` — nothing random, nothing from a clock
    except ``generated``, which ``--check`` ignores for exactly that reason."""
    by_family: dict[str, list[dict[str, Any]]] = {}
    for b in bouts:
        by_family.setdefault(family_of(b.get("event")), []).append(b)

    blocks = {"global": block(bouts)}
    omitted: dict[str, dict[str, Any]] = {}
    for fam in FAMILIES:
        blk = block(by_family.get(fam, []))
        if blk["estimable"]:
            blocks[fam] = blk
        else:
            omitted[fam] = {
                "reason_code": blk["reason_code"] or "no_absorbing_bouts",
                "reason": ("a família não tem lutas absorventes suficientes para o RRB; o "
                           "consumidor deve usar o bloco `global`"),
                "bouts": blk["bouts"], "usable_bouts": blk["usable_bouts"],
                "absorbing_bouts": blk["absorbing_bouts"],
                "fallback": "global",
            }

    if not blocks["global"]["estimable"]:      # pragma: no cover - corpus regression guard
        raise SystemExit("global block is not estimable — refusing to write a weights artifact "
                         f"({blocks['global']['reason_code']})")

    doc: dict[str, Any] = {
        "version": VERSION,
        "generated": generated,
        "action_space": "lamas12",
        "action_codes": list(STATES),
        "method": (
            "weight(action) = sub_share(action) = p_sub_own / (p_sub_own + p_sub_opp), the "
            "absorbing-chain share of reachable submissions that are the acting athlete's, from "
            "analysis/lamas_chain.rrb over analysis/lamas_chain.chain_of chains. Bounded in "
            "[0, 1] by construction, floored at WEIGHT_FLOOR so that any non-empty subset "
            "renormalises safely. NOT pre-normalised: the consumer renormalises over the actions "
            "present in the sequence it is scoring. Fallback chain per action, in order: "
            "rrb_sub_share -> reward_risk centred on the corpus's pooled retention, mapped to "
            "share space -> NEUTRAL_SHARE (no evidence). Definitions are pre-registered in "
            "analysis/rrb_progression.py and docs/research/rrb_progression.md; the chain, the "
            "state space and the gates are analysis/lamas_chain.py's."),
        "transform": {
            "value_function": "V(action) = 2 * sub_share - 1, in [-1, 1]",
            "weight": "w = max((V + 1) / 2, WEIGHT_FLOOR) = max(sub_share, WEIGHT_FLOOR)",
            "floor": WEIGHT_FLOOR,
            "neutral": NEUTRAL_SHARE,
            "places": WEIGHT_PLACES,
            "normalized": False,
            "rejected": ("shifted balance ((balance + 1) / 2) — the measured balances span 0.09 "
                         "of range outside SUB, so every action would weigh 0.5 +/- 0.04; and "
                         "the plain odds ratio — unbounded and undefined where p_sub_opp is 0 "
                         "(lamas_chain_divisions.md section 8.1)"),
            "fallback_order": list(VALUE_SOURCES),
        },
        **{name: {code: blk["weights"][code]["weight"] for code in STATES}
           for name, blk in blocks.items()},
        "provenance": {
            "corpus_digest": corpus_digest(bouts),
            "corpus_bouts": len(bouts),
            "source": "prod `matches` where status='final' (public competition footage)",
            "privacy_class": "A — public competition data",
            "generator": "scripts/build_markov_action_weights.py",
            "runner": "analysis/lamas_chain.rrb + analysis/rrb_progression.value_table",
            "deterministic": "n_boot=0 (no RNG); weights rounded to `transform.places`",
            "families": {name: {k: v for k, v in blk.items() if k != "weights"}
                         for name, blk in blocks.items()},
            "families_omitted": omitted,
            "actions": {name: blk["weights"] for name, blk in blocks.items()},
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

    bouts = fetch_bouts()
    doc = build(bouts, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
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
    print(f"wrote {args.out.relative_to(REPO)} — "
          f"{len(bouts)} bouts, blocks: "
          f"{', '.join(k for k in ('global', *FAMILIES) if k in doc)}")
    for fam, why in doc["provenance"]["families_omitted"].items():
        print(f"  omitted {fam}: {why['reason_code']} "
              f"({why['absorbing_bouts']} absorbing of {why['usable_bouts']} usable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
