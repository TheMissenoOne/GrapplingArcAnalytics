#!/usr/bin/env python
"""Append missing techniques to analysis/data/technique_library.json.

    uv run python scripts/extend_technique_library.py --check   # report only
    uv run python scripts/extend_technique_library.py           # write

The library is a cross-module contract (it is exported to the app as
``@grapplingarch:nodes_library``), so this script never edits an existing entry — it
only appends — and it refuses to write if any label that already resolved would start
resolving somewhere else.

Two things deliberately stay OUT:
  * outcome markers ("Finish", "Submission", "Tap", "Submit" — 257 occurrences) and
    referee/meta events ("Round End", "Stalling Warning", "Riding Time"). They are not
    techniques; putting them in the library would make them nodes in the shared graph.
  * labels the library already covers under a different type ("Guillotine Attempt"
    typed transition, "Trip" typed takedown). Those are dump errors — the type guard in
    ``clean_label`` is doing its job, and a library entry would paper over it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.names import _normalize_name  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "analysis/data/technique_library.json"

# (en, pt, type, variants)
ADDITIONS: list[tuple[str, str, str, list[str]]] = [
    # ── generic locks, by family. A transcript often says only "chave de pé" or
    # "shoulder lock"; without these the whole family falls out of the graph. Specific
    # entries (Toe Hold, Heel Hook, Armbar…) still win because they match exactly.
    ("Foot Lock", "Chave de Pé", "submission",
     ["footlock", "foot lock", "straight foot lock", "chave de pe", "chave no pe"]),
    ("Leg Lock", "Chave de Perna", "submission",
     ["leglock", "leg lock", "chave de perna", "chave na perna"]),
    ("Arm Lock", "Chave de Braço", "submission",
     ["armlock", "arm lock", "bent armlock", "chave no braco"]),
    ("Shoulder Lock", "Chave de Ombro", "submission",
     ["shoulder lock", "shoulder crunch", "chave de ombro", "chave no ombro"]),
    ("Lung Lock", "Chave de Pulmão", "submission",
     ["lung lock", "chave de pulmao", "chave no pulmao", "rib squeeze"]),
    ("Neck Crank", "Chave de Pescoço", "submission",
     ["neck crank", "face crank", "chave de pescoco", "chave cervical", "cervical"]),
    ("Choke", "Estrangulamento", "submission",
     ["choke", "strangle", "estrangulamento", "smother"]),

    # ── leg entanglements (the ashi family). Outside Ashi + Saddle already exist.
    ("Cross Ashi", "Ashi Garami Cruzado", "control",
     ["cross ashi", "cross ashi garami", "crossed ashi", "ashi cruzado",
      "ashi garami cruzado"]),
    ("Inside Ashi", "Ashi Garami Interno", "control",
     ["inside ashi", "inside ashi garami", "ashi interno", "ashi garami interno"]),
    ("Leg Entanglement", "Emaranhado de Pernas", "guard",
     ["leg entanglement", "leg entry", "leg lock entanglement", "emaranhado de pernas",
      "entrada nas pernas"]),
    ("False Reap", "False Reap", "transition",
     ["false reap", "false reap entry", "reap falso"]),
    ("Leg Lace", "Leg Lace", "control", ["leg lace", "lace"]),

    # ── back attacks / control
    ("Gift Wrap", "Gift Wrap", "control",
     ["gift wrap", "giftwrap", "embrulho de presente"]),
    ("Seatbelt Control", "Cinto de Segurança", "control",
     ["seatbelt", "seatbelt control", "cinto de seguranca"]),
    ("Body Triangle", "Triângulo de Corpo", "control",
     ["body triangle", "triangulo de corpo", "triangulo no corpo"]),
    ("Hooks In", "Ganchos Encaixados", "control",
     ["hooks in", "ganchos", "ganchos encaixados"]),
    ("Rear Body Lock", "Abraço por Trás", "control",
     ["rear body lock", "body lock por tras", "abraco por tras"]),
    ("Mounted Crucifix", "Crucifixo Montado", "control",
     ["mounted crucifix", "crucifix", "crucifixo"]),

    # ── head / arm control
    ("Front Headlock", "Frontal", "control",
     ["front headlock", "front headlock control", "frontal", "gravata frontal",
      "snap down to front headlock"]),
    ("Collar Tie", "Pegada na Nuca", "control",
     ["collar tie", "pegada na nuca", "pegada de nuca"]),
    ("Russian Tie", "Russian Tie", "control",
     ["russian tie", "two on one", "2 on 1", "dois contra um"]),
    ("Two-on-One Wrist Control", "Controle de Punho Dois-contra-Um", "control",
     ["two on one wrist control", "two-on-one wrist control"]),
    ("Kimura Grip", "Pegada Kimura", "control", ["kimura grip", "pegada kimura"]),

    # ── positions the corpus uses but the library lacked
    ("Top Half Guard", "Meia-Guarda por Cima", "control",
     ["top half guard", "half guard control", "meia guarda por cima"]),
    ("Smash Half Guard", "Meia-Guarda Esmagada", "control",
     ["smash half guard", "meia guarda esmagada"]),
    ("Three-Quarter Mount", "Montada Três-Quartos", "control",
     ["three quarter mount", "three-quarter mount", "montada tres quartos"]),
    ("North South Control", "Controle Norte-Sul", "control",
     ["north south control", "controle norte sul"]),
    ("Turtle Control", "Controle da Tartaruga", "control",
     ["turtle control", "controle da tartaruga"]),
    ("Top Control", "Controle por Cima", "control",
     ["top control", "controle por cima"]),

    # ── guards
    ("Seated Guard", "Guarda Sentada", "guard",
     ["seated guard", "sit guard", "guarda sentada"]),
    ("Octopus Guard", "Guarda Polvo", "guard", ["octopus guard", "guarda polvo"]),
    ("Inverted De La Riva Guard", "De La Riva Invertida", "guard",
     ["inverted de la riva", "inverted de la riva guard", "reverse de la riva",
      "de la riva invertida", "rdlr"]),
    ("Inverted Half Guard", "Meia-Guarda Invertida", "guard",
     ["inverted half guard", "meia guarda invertida"]),
    ("Knee Shield Half Guard", "Meia-Guarda com Escudo", "guard",
     ["knee shield half guard", "knee shield", "escudo de joelho",
      "meia guarda com escudo"]),
    ("Double Guard Pull", "Puxada Dupla", "guard",
     ["double guard pull", "puxada dupla", "dupla puxada"]),
    ("Guard Recovery", "Recomposição de Guarda", "guard",
     ["guard recovery", "guard retention", "recomposicao de guarda",
      "recomposicao", "retencao de guarda"]),

    # ── generic actions the transcripts fall back on
    ("Sweep", "Raspagem", "sweep", ["sweep", "reversal", "raspagem", "raspada"]),
    ("Guard Pass", "Passagem de Guarda", "pass",
     ["guard pass", "pass", "pass the guard", "passagem", "passagem de guarda"]),
    ("Half Guard Pass", "Passagem de Meia-Guarda", "pass",
     ["half guard pass", "passagem de meia guarda"]),
    ("North-South Pass", "Passagem Norte-Sul", "pass",
     ["north south pass", "north-south pass", "passagem norte sul"]),
    ("Outside Pass", "Passagem por Fora", "pass",
     ["outside pass", "passagem por fora"]),
    ("Takedown", "Queda", "takedown", ["takedown", "take down", "queda", "derrubada"]),
    ("Throw", "Projeção", "takedown", ["throw", "projecao", "arremesso"]),
    ("Trip", "Rasteira", "takedown", ["trip", "trip takedown", "rasteira", "varrida"]),

    # ── named takedowns the corpus uses
    ("Body Lock Takedown", "Queda de Abraço", "takedown",
     ["body lock takedown", "queda de abraco"]),
    ("Low Single Leg", "Single Leg Baixo", "takedown",
     ["low single leg", "low single leg takedown", "low single takedown",
      "single baixo"]),
    ("Snatch Single Leg", "Snatch Single", "takedown",
     ["snatch single leg", "snatch single leg takedown", "snatch single"]),
    ("High Crotch", "High Crotch", "takedown",
     ["high crotch", "high crotch takedown"]),
    ("Ankle Pick", "Ankle Pick", "takedown",
     ["ankle pick", "ankle pick takedown", "pegada no tornozelo"]),
    ("Arm Drag Takedown", "Queda de Arm Drag", "takedown",
     ["arm drag takedown", "queda de arm drag"]),
    ("Counter Takedown", "Contra-Queda", "takedown",
     ["counter takedown", "contra queda"]),
    ("Cradle", "Cradle", "takedown", ["cradle", "cradle takedown"]),
    ("Takedown Defense", "Defesa de Queda", "transition",
     ["takedown defense", "defesa de queda", "sprawl"]),
    ("Shoot", "Entrada", "transition", ["shoot", "entrada", "entrada de queda"]),

    # ── escapes / scrambles
    ("Escape to Standing", "Levantada", "escape",
     ["escape to standing", "stand up", "stand up escape", "stand-up escape",
      "quick stand-up escape", "levantada", "escapar em pe"]),
    ("Escape to Turtle", "Fuga para Tartaruga", "escape",
     ["escape to turtle", "fuga para tartaruga"]),
    ("Guard Recovery Escape", "Fuga para Guarda", "escape",
     ["escape to guard", "escape to half guard", "fuga para guarda"]),
    ("Switch Escape", "Fuga de Troca", "escape",
     ["switch escape", "sit-out escape", "sit out escape", "fuga de troca"]),
    ("Back Escape", "Fuga das Costas", "escape",
     ["back escape", "fuga das costas"]),
    ("Leg Lock Escape", "Fuga de Chave de Perna", "escape",
     ["leg lock escape", "fuga de chave de perna"]),
    ("Mount Escape", "Fuga da Montada", "escape",
     ["mount escape", "fuga da montada"]),
    ("Scramble", "Scramble", "transition",
     ["scramble", "escaped to neutral", "reset to neutral"]),
    ("Wrestle-Up", "Wrestle Up", "transition",
     ["wrestle up", "wrestle-up", "wrestle up to top"]),
    ("Roll-Through", "Rolamento", "transition",
     ["roll through", "roll-through", "roll", "rolamento"]),
    ("Imanari Roll", "Rolamento Imanari", "transition",
     ["imanari roll", "rolamento imanari"]),
    ("Snapdown to Front Headlock", "Snap Down para Frontal", "transition",
     ["snap down / front headlock", "snapdown to front headlock",
      "snap-down go-behind takedown", "snap down go behind"]),
    ("Clinch", "Clinch", "transition", ["clinch", "agarre"]),
    ("Guard Break", "Abertura de Guarda", "transition",
     ["guard break", "abertura de guarda", "abrir a guarda"]),
    ("Leg Drag to Straddle", "Leg Drag para Straddle", "transition",
     ["leg drag to straddle"]),

    # ── submissions the corpus names but the library lacked
    ("Choi Bar", "Choi Bar", "submission", ["choi bar", "choibar"]),
    ("Kimura Trap", "Kimura Trap", "submission",
     ["kimura trap", "kimura counter", "armadilha kimura"]),
    ("Back Triangle", "Triângulo pelas Costas", "submission",
     ["back triangle", "triangulo pelas costas"]),
    ("Guillotine Sweep", "Raspagem de Guilhotina", "sweep",
     ["guillotine sweep", "raspagem de guilhotina"]),
]


class ResolutionConflictError(ValueError):
    """Adding these entries would change what an already-resolvable label resolves to."""


def build(
    existing: list[dict[str, Any]],
    entries: list[tuple[str, str, str, list[str]]] = ADDITIONS,
) -> list[dict[str, Any]]:
    have = {e["en"] for e in existing}
    return [
        {"en": en, "pt": pt, "type": typ, "variants": variants}
        for en, pt, typ, variants in entries
        if en not in have
    ]


def _resolution_index(entries: list[dict[str, Any]]) -> dict[str, str]:
    """normalized term (en/pt/variant) → canonical en, first-in-list wins.

    Mirrors ``analysis.technique_match._index``'s collision rule (``setdefault`` over an
    alphabetically-sorted file), without its ``lru_cache`` — this needs a fresh read of a
    file that may have just changed.
    """
    idx: dict[str, str] = {}
    for e in entries:
        en = str(e.get("en", ""))
        if not en:
            continue
        for term in [en, e.get("pt", ""), *e.get("variants", [])]:
            key = _normalize_name(str(term))
            if key:
                idx.setdefault(key, en)
    return idx


def append_entries(entries: list[tuple[str, str, str, list[str]]]) -> list[dict[str, Any]]:
    """Append new (en, pt, type, variants) entries to ``technique_library.json``.

    Append-only — never edits an existing entry — and refuses (raises
    ``ResolutionConflictError``, writes nothing) if the merge would change what any
    already-resolvable label resolves to: the merged file is re-sorted alphabetically by
    ``en``, and ``_index``/``clean_label`` pick the alphabetically-first match on a
    collision, so a new entry sorted ahead of an existing one could silently steal one of
    its terms. Returns the entries actually appended (``[]`` if every ``en`` already existed).
    """
    existing = json.loads(LIB.read_text(encoding="utf-8"))
    new = build(existing, entries)
    if not new:
        return []

    before = _resolution_index(existing)
    merged = sorted(existing + new, key=lambda e: e["en"].lower())
    after = _resolution_index(merged)
    for key, en in before.items():
        if after.get(key) != en:
            raise ResolutionConflictError(
                f"{key!r} resolved to {en!r} before this change, would resolve to "
                f"{after.get(key)!r} after — refusing to write"
            )

    LIB.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    existing = json.loads(LIB.read_text(encoding="utf-8"))
    new = build(existing)
    skipped = len(ADDITIONS) - len(new)
    print(f"library {len(existing)} entries; adding {len(new)}"
          + (f" ({skipped} already present)" if skipped else ""))
    if args.check:
        for e in new:
            print(f"  + {e['en']:32} {e['type']:11} {e['pt']}")
        return 0

    added = append_entries(ADDITIONS)
    total = len(existing) + len(added)
    print(f"wrote {LIB} — {total} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
