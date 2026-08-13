from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from analytics import _decision_quality_multiplier
from config import (
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_UNKNOWN,
    SCORING_VERSION,
)
from fundamental_quality import calculate_quality
from indicators import compute_atr, wilder_average
from report import validate_decision_integrity
from scanner import TickerInfo, _latest_atr_from_ohlc, scan_single_from_df
from score import entry_point, execution_quality_score
from signal_lifecycle import finalize_signal_ranking


def _quality_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Ticker": "000001.SZ",
        "Industry": "通用设备",
        "ROE": 12.0,
        "GrossMargin": 30.0,
        "IndustryGrossMarginPercentile": 0.20,
        "InstitutionHoldingTrend": "increasing",
        "InstitutionHoldingPeriods": 3,
        "NetProfitY1": 130.0,
        "NetProfitY2": 120.0,
        "NetProfitY3": 110.0,
    }
    row.update(overrides)
    return row


def _lifecycle_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Ticker": "000001.SZ",
        "EntrySignal": "BUY_NOW",
        "RawEntrySignal": "BUY_NOW",
        "Score": 65.0,
        "FinalScore": 65.0,
        "InstitutionalScore": 65.0,
        "TechnicalInstitutionalScore": 65.0,
        "IsETF": False,
        "AssetType": "stock",
        "PassedFilters": True,
        "UniverseEligible": True,
        "SignalStatus": "ACTIVE",
        "SignalTrend": "STRENGTHENING",
        "LifecycleStage": "趋势确认",
        "SignalRecencyDays": 0,
        "ScoreCoverage": 1.0,
        "DataAgeDays": 0,
        "DataTradingAgeDays": 0,
        "ValueTrapRisk": 0.0,
        "ChaseRiskScore": 0.0,
        "QualityApplicable": True,
        "QualityDataAvailable": True,
        "QualityDataCompleteness": 1.0,
        "QualityHardDataComplete": True,
        "QualityGate": True,
        "QualityProfile": "GENERAL",
        "InstitutionHoldingStatus": "PASS",
        "SignalRecencyFactor": 1.0,
        "FailureSignalFactor": 1.0,
        "SectorConfirmationFactor": 1.0,
        "BreakoutQualityFactor": 1.0,
        "StopDistancePct": 5.0,
        "RewardRiskRatio": 2.0,
    }
    row.update(overrides)
    return row


class _FilterCheck:
    def __init__(self, passed: bool, **details: object) -> None:
        self.passed = passed
        self.details = details


class _ETFExemptionFilters:
    min_price = _FilterCheck(False)
    min_volume = _FilterCheck(True)
    min_market_cap = _FilterCheck(False)
    sufficient_history = _FilterCheck(True)
    bear_market = _FilterCheck(True)
    consolidation = _FilterCheck(True)
    volume_accumulation = _FilterCheck(True, consecutive_days=4)
    obv_divergence = _FilterCheck(True)
    cmf_positive = _FilterCheck(False, cmf_improving=True)
    ad_slope = _FilterCheck(False)
    volatility_contraction = _FilterCheck(False)

    @staticmethod
    def signal_count() -> int:
        return 4

    @staticmethod
    def passed_count() -> int:
        return 6


class V43CoreLogicIntegrityTests(unittest.TestCase):
    def test_wilder_atr_matches_seed_and_recursive_definition(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        actual = wilder_average(values, 3)
        expected = [np.nan, np.nan, 2.0, 8.0 / 3.0, 31.0 / 9.0]
        np.testing.assert_allclose(actual, expected, equal_nan=True)

        index = pd.date_range("2026-01-01", periods=80, freq="B")
        close = pd.Series(np.linspace(10.0, 15.0, len(index)), index=index)
        frame = pd.DataFrame(
            {"High": close + 0.5, "Low": close - 0.4, "Close": close},
            index=index,
        )
        compute_atr(frame)
        self.assertEqual(frame["ATR14"].first_valid_index(), index[14])
        self.assertAlmostEqual(
            float(frame["ATR14"].iloc[-1]),
            _latest_atr_from_ohlc(frame, 14),
            places=12,
        )

    def test_breakout_confirmation_requires_current_event_volume(self) -> None:
        frame = self._breakout_frame(current_volume=110.0)
        weak = entry_point(frame, breakout=80.0, volume_score=25.0)
        self.assertAlmostEqual(float(weak["volume_ratio"]), 1.10, places=8)
        self.assertFalse(weak["volume_confirmed"])
        self.assertEqual(weak["signal"], "PRICE_BREAKOUT")

        frame.loc[frame.index[-1], "Volume"] = 121.0
        strong = entry_point(frame, breakout=80.0, volume_score=0.0)
        self.assertAlmostEqual(float(strong["volume_ratio"]), 1.21, places=8)
        self.assertTrue(strong["volume_confirmed"])
        self.assertEqual(strong["signal"], "BREAKOUT_CONFIRM")

    def test_breakout_stop_and_reward_use_broken_level(self) -> None:
        frame = self._breakout_frame(current_volume=130.0)
        entry = entry_point(frame, breakout=80.0)
        resistance = float(frame["High"].iloc[-21:-1].max())
        self.assertAlmostEqual(entry["stop"], resistance - 0.30, places=8)
        self.assertGreater(entry["projected_target"], float(frame["Close"].iloc[-1]))
        self.assertGreater(entry["reward_risk_ratio"], 0.0)
        self.assertGreater(execution_quality_score(frame, entry), 0.0)

    def test_execution_risk_geometry_blocks_trade_ready(self) -> None:
        safe = finalize_signal_ranking(pd.DataFrame([_lifecycle_row()]))
        self.assertEqual(safe.loc[0, "DecisionState"], "READY")

        wide = finalize_signal_ranking(
            pd.DataFrame([_lifecycle_row(StopDistancePct=13.0)])
        )
        self.assertEqual(wide.loc[0, "DecisionState"], "OBSERVE")
        self.assertIn("止损距离", wide.loc[0, "TradeReadinessReason"])

        for missing_field in ("StopDistancePct", "RewardRiskRatio"):
            missing = finalize_signal_ranking(
                pd.DataFrame([_lifecycle_row(**{missing_field: np.nan})])
            )
            self.assertEqual(missing.loc[0, "DecisionState"], "OBSERVE")
            self.assertIn("止损距离", missing.loc[0, "TradeReadinessReason"])

    def test_breakout_ratio_is_mandatory_when_current_schema_is_present(self) -> None:
        common = {
            "EntrySignal": "BREAKOUT_CONFIRM",
            "RawEntrySignal": "BREAKOUT_CONFIRM",
            "BreakoutVolumeConfirmed": True,
            "BreakoutFlowConfirmed": True,
        }
        for ratio in (np.nan, 1.19):
            result = finalize_signal_ranking(
                pd.DataFrame([_lifecycle_row(**common, BreakoutVolumeRatio=ratio)])
            )
            self.assertEqual(result.loc[0, "DecisionState"], "OBSERVE")
            self.assertIn("量能或资金确认不足", result.loc[0, "TradeReadinessReason"])

        confirmed = finalize_signal_ranking(
            pd.DataFrame([_lifecycle_row(**common, BreakoutVolumeRatio=1.20)])
        )
        self.assertEqual(confirmed.loc[0, "DecisionState"], "READY")

    def test_etf_price_and_market_cap_exemptions_reach_passed_filters(self) -> None:
        index = pd.date_range("2025-01-01", periods=300, freq="B")
        close = pd.Series(np.linspace(1.8, 2.2, len(index)), index=index)
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close + 0.03,
                "Low": close - 0.03,
                "Close": close,
                "Volume": 2_000_000.0,
            },
            index=index,
        )
        with patch("scanner.run_all_filters", return_value=_ETFExemptionFilters()):
            result = scan_single_from_df(
                TickerInfo(ticker="510050.SH", name="上证50ETF", is_etf=True),
                frame,
            )
        self.assertTrue(result.universe_eligible)
        self.assertTrue(result.signal_confirmed)
        self.assertTrue(result.passed_filters)
        self.assertTrue(result.filter_details["min_price"])
        self.assertTrue(result.filter_details["min_market_cap"])

    def test_legacy_etf_filter_flag_is_repaired_from_split_gates(self) -> None:
        row = _lifecycle_row(
            IsETF=True,
            AssetType="etf",
            QualityApplicable=False,
            QualityDataAvailable=False,
            PassedFilters=False,
            UniverseEligible=True,
            SignalConfirmed=True,
            EntrySignal="WAIT_PULLBACK",
            RawEntrySignal="WAIT_PULLBACK",
        )
        result = finalize_signal_ranking(pd.DataFrame([row]))
        self.assertTrue(bool(result.loc[0, "PassedFilters"]))

    def test_missing_required_hard_factor_cannot_be_actionable(self) -> None:
        missing_roe = calculate_quality(_quality_row(ROE=np.nan))
        self.assertTrue(missing_roe.quality_gate)
        self.assertFalse(missing_roe.quality_hard_data_complete)
        row = _lifecycle_row(
            ROE=np.nan,
            QualityGate=missing_roe.quality_gate,
            QualityDataCompleteness=missing_roe.quality_data_completeness,
            QualityHardDataComplete=missing_roe.quality_hard_data_complete,
            QualityProfile=missing_roe.quality_profile,
        )
        result = finalize_signal_ranking(pd.DataFrame([row]))
        self.assertNotEqual(result.loc[0, "DecisionState"], "READY")

        financial = calculate_quality(
            _quality_row(Industry="银行Ⅱ", IndustryGrossMarginPercentile=np.nan)
        )
        self.assertTrue(financial.quality_gate)
        self.assertTrue(financial.quality_hard_data_complete)

    def test_backtest_quality_multiplier_matches_gate_semantics(self) -> None:
        frame = pd.DataFrame(
            {
                "QualityApplicable": [True, True, False],
                "QualityGate": [True, False, True],
                "QualityHardDataComplete": [True, True, True],
                "InstitutionHoldingStatus": ["FAIL", "PASS", "UNKNOWN"],
            }
        )
        multiplier = _decision_quality_multiplier(
            frame,
            is_etf=pd.Series([False, False, True]),
            quality_available=pd.Series([True, True, False]),
        )
        self.assertEqual(multiplier.tolist(), [QUALITY_MULTIPLIER_UNKNOWN, QUALITY_MULTIPLIER_FAIL, 1.0])

    def test_core_and_facade_cross_asset_results_are_identical(self) -> None:
        rows = []
        for index in range(12):
            is_etf = index >= 6
            rows.append(
                _lifecycle_row(
                    Ticker=f"T{index:02d}",
                    EntrySignal="WAIT_PULLBACK",
                    RawEntrySignal="WAIT_PULLBACK",
                    InstitutionalScore=30.0 + index * 2.0,
                    TechnicalInstitutionalScore=30.0 + index * 2.0,
                    IsETF=is_etf,
                    AssetType="etf" if is_etf else "stock",
                    QualityApplicable=not is_etf,
                    QualityDataAvailable=not is_etf,
                    QualityHardDataComplete=True,
                )
            )
        frame = pd.DataFrame(rows)
        core_finalize = finalize_signal_ranking.__globals__["_legacy_finalize_signal_ranking"]
        core = core_finalize(frame)
        facade = finalize_signal_ranking(frame)
        for column in (
            "CrossAssetScore",
            "ResearchTier",
            "DecisionState",
            "RankingScore",
        ):
            pd.testing.assert_series_equal(
                core[column], facade[column], check_names=False, check_exact=False, rtol=1e-8
            )

    def test_scoring_version_marks_v43_logic_boundary(self) -> None:
        self.assertIn("v43", SCORING_VERSION)

    def test_output_integrity_rejects_actionable_risk_contradictions(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "BAD.SZ",
                    "RankingEligibility": "推荐",
                    "DecisionState": "READY",
                    "TradeReadiness": "推荐",
                    "EntrySignal": "BREAKOUT_CONFIRM",
                    "BreakoutVolumeRatio": 1.10,
                    "QualityApplicable": True,
                    "QualityGate": True,
                    "QualityHardDataComplete": False,
                    "IsETF": False,
                    "StopDistancePct": 13.0,
                    "RewardRiskRatio": 0.5,
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "Decision integrity violation"):
            validate_decision_integrity(frame)

    @staticmethod
    def _breakout_frame(current_volume: float) -> pd.DataFrame:
        index = pd.date_range("2026-01-01", periods=21, freq="B")
        close = np.full(21, 9.80)
        close[-1] = 10.50
        high = np.full(21, 10.00)
        high[-1] = 10.60
        low = np.full(21, 9.50)
        volume = np.full(21, 100.0)
        volume[-1] = current_volume
        return pd.DataFrame(
            {
                "Close": close,
                "High": high,
                "Low": low,
                "Volume": volume,
                "ATR14": 0.30,
                "MA20": 9.70,
                "MA50": 9.50,
                "RSI14": 60.0,
                "CMF": 0.10,
                "AD_Slope": 1.0,
                "OBV": np.arange(21, dtype=float),
            },
            index=index,
        )


if __name__ == "__main__":
    unittest.main()
