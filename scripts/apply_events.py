"""Splice refined events (sidecar JSON) into a preliminary dump; drop the pbp scratch.

The refiner (docs/deepseek/E-refine-events.md) emits
``transcripts/deepseek/<event>_events.json`` = ``{"<a_name>|<year>": [ {label,type,actor,…} ]}``.
This applies it to ``scripts/dumps/<module>.py``: sets each matched bout's ``events``, removes its
``pbp``, normalises event ``ts`` "M:SS" → seconds, and rewrites the dump in the greppable pprint
form ``batch_queue`` uses (so it still imports + stays greppable). Only matched bouts lose ``pbp``,
so a partial sidecar leaves the rest refinable.

    uv run python -m scripts.apply_events <module> transcripts/deepseek/<event>_events.json
    uv run python -m scripts.apply_events --check          # round-trip self-test, no files touched
"""
from __future__ import annotations

import ast
import importlib
import json
import pprint
import sys
from pathlib import Path
from typing import Any

DUMPS = Path(__file__).resolve().parent / "dumps"
HEADER = '''"""%s — refined from transcript."""
# ruff: noqa: E501
from __future__ import annotations
from typing import Any

RAW: list[dict[tuple[str, int], dict[str, Any]]] = %s
'''

Dump = list[dict[tuple[str, int], dict[str, Any]]]


def _ts_to_sec(ts: Any) -> int | None:
    """"M:SS"/"H:MM:SS"/int → seconds; None if unparseable (dropped, not crashed)."""
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str) and ":" in ts:
        try:
            p = [int(x) for x in ts.strip().split(":")]
        except ValueError:
            return None
        return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1]
    try:
        return int(ts)
    except (TypeError, ValueError):
        return None


def _norm_event(e: dict[str, Any]) -> dict[str, Any]:
    e = dict(e)
    if "ts" in e:
        s = _ts_to_sec(e["ts"])
        if s is None:
            e.pop("ts")       # drop unparseable ts rather than store a bad value
        else:
            e["ts"] = s
    return e


def splice(raw: Dump, events_by_key: dict[str, list[dict[str, Any]]]) -> tuple[int, list[str]]:
    """Mutate ``raw`` in place: for each matched bout key ``"<a_name>|<year>"`` set its events
    and drop its pbp. Returns (bouts patched, sidecar keys that matched nothing)."""
    unmatched = set(events_by_key)
    patched = 0
    for bout in raw:
        for (a_name, year), bt in bout.items():
            opp = bt.get("opponent", "")
            keys = [f"{a_name}|{opp}|{year}", f"{a_name}|{year}"] if opp else [f"{a_name}|{year}"]
            for key in keys:
                if key in events_by_key:
                    bt["events"] = [_norm_event(e) for e in events_by_key[key]]
                    bt.pop("pbp", None)
                    patched += 1
                    unmatched.discard(key)
                    break
    return patched, sorted(unmatched)


def apply(module: str, sidecar: Path) -> None:
    module = module.removesuffix(".py")
    path = DUMPS / f"{module}.py"
    if not path.exists():
        sys.exit(f"ERROR: dump {path} not found")
    if not sidecar.exists():
        sys.exit(f"ERROR: sidecar {sidecar} not found")
    mod = importlib.import_module(f"scripts.dumps.{module}")
    raw: Dump = mod.RAW
    patched, unmatched = splice(raw, json.loads(sidecar.read_text(encoding="utf-8")))
    title = (mod.__doc__ or module).split("—")[0].strip() or module
    body = pprint.pformat(raw, width=100, sort_dicts=False)
    path.write_text(HEADER % (title, body), encoding="utf-8")
    print(f"✓  {module}: patched {patched} bouts, dropped their pbp → {path}")
    if unmatched:
        print(f"⚠  {len(unmatched)} sidecar key(s) matched no bout: {unmatched[:5]}")


def _check() -> None:
    """Round-trip self-test on an in-memory dump — touches no files."""
    raw: Dump = [
        {("Gordon Ryan", 2025): {"opponent": "Felipe Pena", "events": [],
                                 "pbp": [{"ts": 12, "text": "pulls guard"}]}},
        {("Mica Galvao", 2025): {"opponent": "Kaynan Duarte", "events": [],
                                 "pbp": [{"ts": 5, "text": "grip fight"}]}},
    ]
    patched, unmatched = splice(raw, {
        "Gordon Ryan|2025": [{"label": "Armbar", "type": "submission",
                              "actor": "Gordon Ryan", "successful": True, "ts": "1:23"}],
        "Nobody|2025": [{"label": "x", "type": "guard", "actor": "y"}],
    })
    a = raw[0][("Gordon Ryan", 2025)]
    b = raw[1][("Mica Galvao", 2025)]
    assert patched == 1, patched
    assert "pbp" not in a, "matched bout kept pbp"
    assert a["events"][0]["ts"] == 83, a["events"][0]          # "1:23" → 83s
    assert "pbp" in b, "unmatched bout lost its pbp"           # partial splice preserves the rest
    assert unmatched == ["Nobody|2025"], unmatched
    reparsed = ast.literal_eval(pprint.pformat(raw, width=100, sort_dicts=False))
    assert reparsed[0][("Gordon Ryan", 2025)]["events"][0]["label"] == "Armbar"
    print("apply_events self-check OK")


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--check":
        _check()
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    apply(argv[0], Path(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
