"""Splice refined events (sidecar JSON) into a preliminary dump; drop the pbp scratch.

The refiner emits either the legacy ``{"<bout key>": [events]}`` or an enriched
``{"<bout key>": {events, scouting_observations, timing, adjudication}}`` sidecar.
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
SIDECAR_FIELDS = {"events", "scouting_observations", "timing", "adjudication"}


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


def _participant_name(value: Any, participants: tuple[str, str]) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned[: cleaned.rfind("(")].rstrip() if cleaned.endswith(")") and "(" in cleaned else cleaned
    return next((name for name in participants if name.casefold() == cleaned.casefold()), str(value))


def _norm_actor(item: Any, participants: tuple[str, str]) -> Any:
    if not isinstance(item, dict):
        return item
    value = _norm_event(item)
    if "actor" in value:
        value["actor"] = _participant_name(value["actor"], participants)
    return value


def _norm_result(value: Any, participants: tuple[str, str]) -> Any:
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"positive", "negative", "advantages", "penalties"} and isinstance(item, dict):
            normalized[key] = {_participant_name(actor, participants): score
                               for actor, score in item.items()}
        elif key == "rounds" and isinstance(item, list):
            normalized[key] = [
                {_participant_name(actor, participants): card for actor, card in round_.items()}
                if isinstance(round_, dict) else round_
                for round_ in item
            ]
        else:
            normalized[key] = item
    return normalized


def _scores(value: Any, participants: tuple[str, str], *, maximum: int | None = None) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(participants)
        and all(
            isinstance(score, int) and not isinstance(score, bool)
            and score >= 0 and (maximum is None or score <= maximum)
            for score in value.values()
        )
    )


def _validate_adjudication(value: dict[str, Any], participants: tuple[str, str]) -> None:
    allowed = {"status", "kind", "result"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"campo de adjudication desconhecido: {sorted(unknown)}")
    status, kind = value.get("status"), value.get("kind")
    if status not in {"verified", "partial", "unknown"}:
        raise ValueError("status de adjudication inválido")
    if kind not in {"point_total", "round_cards", "none"}:
        raise ValueError("kind de adjudication inválido")
    if status == "unknown" or kind == "none":
        if status != "unknown" or kind != "none" or set(value) != {"status", "kind"}:
            raise ValueError("status/kind de adjudication incoerentes")
        return
    if set(value) != allowed or not isinstance(value.get("result"), dict):
        raise ValueError("adjudication verificada/parcial exige result")
    result = value["result"]
    if kind == "point_total":
        fields = {"positive", "negative", "advantages", "penalties"}
        supplied = set(result)
        valid_fields = (
            supplied == fields if status == "verified" else bool(supplied) and supplied <= fields
        )
        if not valid_fields or not all(_scores(result[field], participants) for field in supplied):
            raise ValueError("result point_total inválido")
    elif (
        set(result) != {"rounds"}
        or not isinstance(result["rounds"], list)
        or not result["rounds"]
        or not all(_scores(round_, participants, maximum=10) for round_ in result["rounds"])
    ):
        raise ValueError("result round_cards inválido")


def _splice_value(bt: dict[str, Any], value: Any, participants: tuple[str, str]) -> None:
    if not isinstance(value, dict):
        if not isinstance(value, list):
            raise ValueError("sidecar legado deve conter lista de events")
        bt["events"] = [_norm_actor(event, participants) for event in value]
        return
    unknown = set(value) - SIDECAR_FIELDS
    if unknown:
        raise ValueError(f"campo de sidecar desconhecido: {sorted(unknown)}")
    if set(value) != SIDECAR_FIELDS:
        raise ValueError(f"campos obrigatórios de sidecar ausentes: {sorted(SIDECAR_FIELDS - set(value))}")
    events = value["events"]
    observations = value["scouting_observations"]
    timing = value["timing"]
    adjudication = value["adjudication"]
    if not isinstance(events, list) or not isinstance(observations, list):
        raise ValueError("events e scouting_observations devem ser listas")
    if not isinstance(timing, dict) or not isinstance(adjudication, dict):
        raise ValueError("timing e adjudication devem ser objetos")
    unknown_timing = set(timing) - {"end_ts", "overtime_start_ts"}
    if unknown_timing:
        raise ValueError(f"campo de timing desconhecido: {sorted(unknown_timing)}")
    normalized_events = [_norm_actor(event, participants) for event in events]
    normalized_observations = [_norm_actor(item, participants) for item in observations]
    normalized_timing = {
        key: _ts_to_sec(item) for key, item in timing.items()
        if _ts_to_sec(item) is not None
    }
    normalized_adjudication = dict(adjudication)
    if "result" in normalized_adjudication:
        normalized_adjudication["result"] = _norm_result(
            normalized_adjudication["result"], participants
        )
    _validate_adjudication(normalized_adjudication, participants)
    bt["events"] = normalized_events
    bt["scouting_observations"] = normalized_observations
    bt["timing"] = normalized_timing
    bt["adjudication"] = normalized_adjudication
    bt["timing_basis"] = "video_absolute"
    start = _ts_to_sec(bt.get("start"))
    if start is not None:
        bt["bout_start_s"] = start
        end = normalized_timing.get("end_ts")
        overtime = normalized_timing.get("overtime_start_ts")
        if end is not None and end >= start:
            bt["duration_s"] = end - start
        if overtime is not None and overtime >= start:
            bt["overtime_start_s"] = overtime - start


def splice(raw: Dump, events_by_key: dict[str, Any]) -> tuple[int, list[str]]:
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
                    _splice_value(bt, events_by_key[key], (a_name, str(opp)))
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
