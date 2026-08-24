from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from institution_scanner.contracts import (
    CHALLENGER_CONTRACT,
    PRODUCTION_CONTRACT,
)
from institution_scanner.reliability import (
    HIERARCHICAL_MAX_INFORMATION_PER_EFFECTIVE_PEER,
    annotate_reliability,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_production_contract_is_locked() -> None:
    assert (
        PRODUCTION_CONTRACT.weights.signature()
        == "0.6000:0.1500:0.2500"
    )
    assert PRODUCTION_CONTRACT.production is True
    assert (
        CHALLENGER_CONTRACT.weights.signature()
        == "0.5500:0.2000:0.2500"
    )
    assert CHALLENGER_CONTRACT.production is False


def test_reliability_golden_snapshot() -> None:
    frame = pd.read_csv(
        FIXTURES / "reliability_input.csv"
    )
    expected = json.loads(
        (
            FIXTURES / "reliability_expected.json"
        ).read_text(encoding="utf-8")
    )

    result = annotate_reliability(frame).set_index("Ticker")

    assert result["ChallengerProductionApplied"].eq(False).all()
    assert (
        result["HierarchicalEvidenceProductionApplied"]
        .eq(False)
        .all()
    )
    assert (
        result["HierarchicalEvidenceSelfExcluded"]
        .eq(True)
        .all()
    )

    for ticker, values in expected.items():
        row = result.loc[ticker]
        assert (
            float(row["ChampionAxisScoreDiagnostic"])
            == values["champion"]
        )
        assert (
            float(row["ChallengerAxisScoreDiagnostic"])
            == values["challenger"]
        )
        assert (
            int(row["ChampionAxisRankWithinAsset"])
            == values["champion_rank"]
        )
        assert (
            int(row["ChallengerAxisRankWithinAsset"])
            == values["challenger_rank"]
        )
        assert (
            int(row["ChallengerAxisRankDelta"])
            == values["delta"]
        )
        assert (
            row["HierarchicalEvidenceLevel"]
            == values["hierarchy"]
        )
        assert (
            row["HierarchicalEvidenceStatus"]
            == values["hierarchy_status"]
        )


def test_shadow_model_never_rewrites_production_columns() -> None:
    frame = pd.read_csv(
        FIXTURES / "reliability_input.csv"
    )
    frame["FinalScore"] = [
        61.0,
        62.0,
        63.0,
        64.0,
        65.0,
        66.0,
    ]
    frame["CandidateViewRank"] = [1, 2, 3, 4, 5, 6]
    before = frame[
        ["FinalScore", "CandidateViewRank"]
    ].copy()

    result = annotate_reliability(frame)

    pd.testing.assert_frame_equal(
        result[
            ["FinalScore", "CandidateViewRank"]
        ].reset_index(drop=True),
        before.reset_index(drop=True),
    )


def test_hierarchical_evidence_is_leave_one_out_and_breadth_capped() -> None:
    tickers = [f"T{index:02d}.ST" for index in range(12)]
    frame = pd.DataFrame(
        {
            "Ticker": tickers,
            "ResearchAssetClass": ["STOCK"] * 12,
            "IndustryTopic": ["POWER"] * 12,
            "EntrySignal": ["BREAKOUT_CONFIRM"] * 12,
            "SetupScore": [60.0] * 12,
            "TriggerScore": [60.0] * 12,
            "ExecutionScore": [60.0] * 12,
            "BacktestEvidenceScoreRaw": (
                [100.0] + [50.0] * 11
            ),
            "BacktestEffectiveSamples": [10.0] * 12,
        }
    )

    result = annotate_reliability(frame).set_index("Ticker")
    focal = result.loc[tickers[0]]
    ordinary = result.loc[tickers[1]]

    # The focal ticker's own 100 score is excluded: its 11 peers are all 50.
    assert np.isclose(
        float(focal["HierarchicalEvidenceScore"]),
        50.0,
    )
    # An ordinary ticker sees the extreme focal peer plus ten 50 peers.
    assert np.isclose(
        float(ordinary["HierarchicalEvidenceScore"]),
        (100.0 + 10.0 * 50.0) / 11.0,
        atol=1e-4,
    )
    assert int(focal["HierarchicalEvidencePeerTickers"]) == 11
    assert np.isclose(
        float(focal["HierarchicalEvidenceNominalN"]),
        110.0,
    )
    assert np.isclose(
        float(focal["HierarchicalEvidenceKishPeers"]),
        11.0,
    )
    assert np.isclose(
        float(focal["HierarchicalEvidenceEffectiveN"]),
        11.0
        * HIERARCHICAL_MAX_INFORMATION_PER_EFFECTIVE_PEER,
    )
    assert (
        float(focal["HierarchicalEvidenceEffectiveN"])
        < float(focal["HierarchicalEvidenceNominalN"])
    )
    assert focal["HierarchicalEvidenceLevel"] == "INDUSTRY_SIGNAL"
    assert (
        focal["HierarchicalEvidenceStatus"]
        == "DIAGNOSTIC_ONLY"
    )
    assert bool(focal["HierarchicalEvidenceSelfExcluded"]) is True
