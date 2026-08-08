from __future__ import annotations

import unittest

import pandas as pd

from signal_lifecycle import finalize_signal_ranking


class RankingIntegrityV19Tests(unittest.TestCase):
    @staticmethod
    def _row(ticker: str, score: float, *, failed: bool = False, quality_gate: bool = True, signal: str = "BUY_NOW") -> dict:
        return {
            "Ticker": ticker,
            "IsETF": False,
            "AssetType": "stock",
            "InstitutionalScore": score,
            "FinalScore": score,
            "Score": score,
            "EntrySignal": signal,
            "PassedFilters": True,
            "SignalStatus": "FAILED" if failed else "NEW",
            "QualityApplicable": True,
            "QualityDataCompleteness": 1.0,
            "QualityGate": quality_gate,
            "QualityDataAvailable": True,
            "QualityROE": True,
            "QualityGrossMargin": True,
            "QualityNetProfit": True,
            "InstitutionHoldingStatus": "PASS",
            "ROE": 12.0,
            "IndustryGrossMarginPercentile": 70.0,
            "NetProfitY1": 1.0,
            "NetProfitY2": 1.0,
            "NetProfitY3": 1.0,
            "ScoreCoverage": 1.0,
            "DataAgeDays": 0,
            "DataTradingAgeDays": 0,
            "ValueTrapRisk": 0.0,
        }

    def test_failed_quality_block_cannot_outrank_clean_ready_candidate(self):
        rows = [
            self._row("FAILED_TOP", 50.0, failed=True, quality_gate=False),
            self._row("READY", 48.0),
            self._row("S3", 46.0, signal="WAIT_PULLBACK"),
            self._row("S4", 44.0, signal="WAIT_PULLBACK"),
            self._row("S5", 42.0, signal="WAIT_PULLBACK"),
            self._row("S6", 40.0, signal="WAIT_PULLBACK"),
        ]
        result = finalize_signal_ranking(pd.DataFrame(rows)).set_index("Ticker")

        self.assertEqual(result.loc["READY", "DecisionState"], "READY")
        self.assertEqual(result.loc["FAILED_TOP", "DecisionState"], "OBSERVE")
        self.assertLess(
            result.loc["FAILED_TOP", "RankingScore"],
            result.loc["READY", "RankingScore"],
        )
        self.assertLess(result.loc["FAILED_TOP", "ReadinessPenaltyFactor"], 0.60)

    def test_cross_asset_percentile_uplift_is_capped(self):
        rows = [
            self._row("S1", 50.0),
            self._row("S2", 45.0),
            self._row("S3", 40.0),
            self._row("S4", 35.0),
            self._row("S5", 30.0),
            self._row("S6", 25.0),
        ]
        result = finalize_signal_ranking(pd.DataFrame(rows)).set_index("Ticker")
        uplift = result["CrossAssetScore"] - result["InstitutionalScore"]
        self.assertLessEqual(float(uplift.max()), 15.0001)

    def test_clean_wait_pullback_keeps_full_integrity_factor(self):
        rows = [self._row(f"S{i}", 55.0 - i, signal="WAIT_PULLBACK") for i in range(6)]
        result = finalize_signal_ranking(pd.DataFrame(rows)).set_index("Ticker")
        self.assertTrue((result["DecisionState"] == "OBSERVE").all())
        self.assertTrue((result["ReadinessPenaltyFactor"] == 1.0).all())


if __name__ == "__main__":
    unittest.main()
