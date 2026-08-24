from __future__ import annotations

from pathlib import Path

import pandas as pd

from institution_scanner.verify_output import verify_directory


def _write_fixture(root: Path, *, peer_weight: float = 0.0) -> None:
    all_results = pd.DataFrame(
        {
            "Ticker": ["A.ST", "B.ST"],
            "ModelWeightSignature": [
                "0.6000:0.1500:0.2500",
                "0.6000:0.1500:0.2500",
            ],
            "GlobalCalibrationGovernanceStatus": [
                "DIAGNOSTIC_ONLY",
                "DIAGNOSTIC_ONLY",
            ],
            "BacktestPeerEvidenceWeight": [peer_weight, 0.0],
            "BacktestEligibleForRanking": [False, False],
            "BacktestLocalEvidenceWeight": [0.0, 0.0],
            "ChallengerProductionApplied": [False, False],
            "HierarchicalEvidenceProductionApplied": [False, False],
            "AlphaFormulaReconstructionAbsError": [0.0, 0.0],
            "RankingFormulaReconstructionAbsError": [0.0, 0.0],
            "RankingDecisionInferenceAbsError": [0.0, 0.0],
        }
    )
    mixed = pd.DataFrame(
        {
            "Ticker": ["A.ST", "B.ST"],
            "CandidateViewRank": [1, 2],
        }
    )
    trade_ready = pd.DataFrame(
        {
            "Ticker": ["A.ST"],
            "ExecutionState": ["READY"],
            "QualityLayerStatus": ["PASS"],
            "EntrySignal": ["BREAKOUT_CONFIRM"],
            "DataFreshnessStatus": ["新鲜"],
            "Close": [10.0],
            "StopLoss": [9.0],
            "TargetPrice": [12.0],
        }
    )
    all_results.to_csv(root / "AllResults.csv", index=False, encoding="utf-8-sig")
    mixed.to_csv(root / "Top50Mixed.csv", index=False, encoding="utf-8-sig")
    trade_ready.to_csv(
        root / "Top50TradeReady.csv",
        index=False,
        encoding="utf-8-sig",
    )


def test_output_contract_passes_clean_fixture(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    payload = verify_directory(tmp_path)
    assert payload["status"] == "PASS"
    assert payload["errors"] == 0


def test_output_contract_rejects_diagnostic_peer_weight(tmp_path: Path) -> None:
    _write_fixture(tmp_path, peer_weight=0.05)
    payload = verify_directory(tmp_path)
    assert payload["status"] == "FAIL"
    codes = {item["code"] for item in payload["issues"]}
    assert "DIAGNOSTIC_PEER_WEIGHT_NONZERO" in codes
