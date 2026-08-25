from __future__ import annotations

from institution_scanner.contracts import PRODUCTION_CONTRACT
from institution_scanner.version_manifest import build_version_manifest


def test_version_manifest_exposes_canonical_production_contract() -> None:
    manifest = build_version_manifest()
    model = manifest["production_model"]
    assert isinstance(model, dict)
    assert model["role"] == "PRODUCTION_CHAMPION"
    assert model["weight_signature"] == PRODUCTION_CONTRACT.weights.signature()


def test_version_manifest_keeps_runtime_provenance_structured() -> None:
    manifest = build_version_manifest()
    runtime = manifest["runtime"]
    assert isinstance(runtime, dict)
    for field in (
        "scoring",
        "pipeline",
        "output_contract",
        "decision_integrity",
        "market_data",
        "backtest_provenance",
        "performance_engine",
    ):
        assert isinstance(runtime[field], str)
        assert runtime[field]
