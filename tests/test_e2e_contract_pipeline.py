from __future__ import annotations

from pathlib import Path

import pandas as pd

from institution_scanner.reliability import annotate_reliability
from institution_scanner.verify_output import verify_directory


def test_offline_reliability_to_publication_contract_golden(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "Ticker": ["600001.SH", "510300.SH", "600002.SH"],
            "RunId": ["golden-run"] * 3,
            "ModelVersion": ["golden-model"] * 3,
            "PipelineVersion": ["golden-pipeline"] * 3,
            "OutputContractVersion": ["golden-output"] * 3,
            "DecisionIntegrityVersion": ["golden-decision"] * 3,
            "DecisionPolicySignature": ["golden-policy"] * 3,
            "ModelWeightSignature": ["0.6000:0.2500:0.1500"] * 3,
            "SetupScore": [70.0, 60.0, 50.0],
            "TriggerScore": [50.0, 80.0, 40.0],
            "ExecutionScore": [60.0, 70.0, 50.0],
            "ResearchAssetClass": ["STOCK", "ETF", "STOCK"],
            "IndustryTopic": ["A", "ETF", "A"],
            "EntrySignal": ["BREAKOUT_CONFIRM", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"],
            "BacktestEffectiveSamples": [0.0, 0.0, 0.0],
            "BacktestScore": [50.0, 50.0, 50.0],
        }
    )
    result = annotate_reliability(base)
    result["GlobalCalibrationGovernanceStatus"] = "DIAGNOSTIC_ONLY"
    result["BacktestPeerEvidenceWeight"] = 0.0
    result["BacktestLocalEvidenceWeight"] = 0.0
    result["BacktestEligibleForRanking"] = False
    result["RankingScope"] = "FULL_UNIVERSE"
    result["RankingUniverseSize"] = len(result)
    result["RankingRunId"] = "golden-run"
    result["CandidateViewRank"] = [1, 2, 3]
    result["RankingScore"] = [70.0, 65.0, 40.0]
    result["ExecutionState"] = ["READY", "OBSERVE", "OBSERVE"]
    result["DataFreshnessStatus"] = "新鲜"
    result["QualityLayerStatus"] = ["PASS", "NOT_APPLICABLE", "PASS"]
    result["Close"] = [10.0, 4.0, 20.0]
    result["StopLoss"] = [9.0, 3.8, 18.0]
    result["TargetPrice"] = [12.0, 4.5, 24.0]

    result.to_csv(tmp_path / "AllResults.csv", index=False, encoding="utf-8-sig")
    result.iloc[:3].to_csv(
        tmp_path / "Top50Mixed.csv", index=False, encoding="utf-8-sig"
    )
    result.iloc[[0]].to_csv(
        tmp_path / "Top50TradeReady.csv", index=False, encoding="utf-8-sig"
    )

    payload = verify_directory(tmp_path)
    assert payload["status"] == "PASS"
    assert payload["errors"] == 0

    golden = result.loc[
        :, [
            "Ticker",
            "ProductionModelRole",
            "ProductionModelWeightSignatureLocked",
            "ChallengerModelRole",
            "ChallengerProductionApplied",
            "HierarchicalEvidenceProductionApplied",
            "CandidateViewRank",
        ]
    ].to_dict(orient="records")
    assert golden == [
        {
            "Ticker": "600001.SH",
            "ProductionModelRole": "PRODUCTION_CHAMPION",
            "ProductionModelWeightSignatureLocked": "0.6000:0.2500:0.1500",
            "ChallengerModelRole": "SHADOW_CHALLENGER",
            "ChallengerProductionApplied": False,
            "HierarchicalEvidenceProductionApplied": False,
            "CandidateViewRank": 1,
        },
        {
            "Ticker": "510300.SH",
            "ProductionModelRole": "PRODUCTION_CHAMPION",
            "ProductionModelWeightSignatureLocked": "0.6000:0.2500:0.1500",
            "ChallengerModelRole": "SHADOW_CHALLENGER",
            "ChallengerProductionApplied": False,
            "HierarchicalEvidenceProductionApplied": False,
            "CandidateViewRank": 2,
        },
        {
            "Ticker": "600002.SH",
            "ProductionModelRole": "PRODUCTION_CHAMPION",
            "ProductionModelWeightSignatureLocked": "0.6000:0.2500:0.1500",
            "ChallengerModelRole": "SHADOW_CHALLENGER",
            "ChallengerProductionApplied": False,
            "HierarchicalEvidenceProductionApplied": False,
            "CandidateViewRank": 3,
        },
    ]
