from __future__ import annotations

from pathlib import Path

from institution_scanner.contracts import PRODUCTION_CONTRACT


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_document_matches_canonical_production_weights() -> None:
    text = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    weights = PRODUCTION_CONTRACT.weights

    assert f"- Setup: {weights.setup:.2f}" in text
    assert f"- Trigger: {weights.trigger:.2f}" in text
    assert f"- Execution: {weights.execution:.2f}" in text
    assert "Signature order: `Setup:Trigger:Execution`" in text
