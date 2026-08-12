import json
from pathlib import Path

REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "poc"
    / "decision_vision"
    / "external_datasets.json"
)


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_external_dataset_registry() -> None:
    payload = _registry()
    datasets = payload["datasets"]
    assert len(datasets) >= 7

    for dataset in datasets:
        if dataset["priority"] == "quarantine":
            continue
        assert dataset.get("url"), dataset["key"]
        assert dataset.get("license_verified") is True, dataset["key"]
        if dataset["priority"] != "research":
            assert dataset.get("commercial_use") is True, dataset["key"]
        assert dataset.get("poc_role"), dataset["key"]


def test_vicos_research_tier() -> None:
    by_key = {d["key"]: d for d in _registry()["datasets"]}
    vicos = by_key["vicos_bjj"]
    assert vicos["priority"] == "research"
    assert vicos.get("license_verified") is True
    assert vicos.get("commercial_use") is False
    assert vicos.get("poc_role") == "state_research_benchmark"


def test_provenance_flags_flagged() -> None:
    by_key = {d["key"]: d for d in _registry()["datasets"]}
    assert by_key["bjj3"]["provenance_review_required"] is True
    assert by_key["grappling_set"]["provenance_review_required"] is True


def test_bjj_techniques_quarantined() -> None:
    by_key = {d["key"]: d for d in _registry()["datasets"]}
    disabled = by_key["bjj_techniques"]
    assert disabled["priority"] == "quarantine"
    assert disabled.get("license_verified") is False
