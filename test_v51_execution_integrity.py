from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import config
import downloader
import report
from backtest_alignment import align_benchmark_returns
from filters import filter_min_volume, filter_volatility_contraction
from pipeline_contracts import (
    EnrichedResultView,
    PipelineStage,
    enforce_enrichment_contract,
)
from scanner import ScanResult
from score import score_volatility
from tradeability import daily_limit_pct, is_entry_tradeable


class V51ExecutionIntegrityTests(unittest.TestCase):
    def test_v51_versions_preserve_prior_boundaries(self) -> None:
        self.assertIn("v51", config.SCORING_VERSION)
        self.assertIn("v48", config.SCORING_VERSION)
        self.assertNotIn("v49", config.SCORING_VERSION)
        self.assertNotIn("v50", config.SCORING_VERSION)
        self.assertIn("v51", config.PIPELINE_VERSION)
        self.assertIn("v50", config.PIPELINE_VERSION)
        self.assertIn("v51", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v51", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v51", config.BACKTEST_PROVENANCE_VERSION)

    def test_public_analytics_import_installs_benchmark_open_alignment(self) -> None:
        self.assertTrue(
            getattr(analytics, "_V51_BENCHMARK_ALIGNMENT_INSTALLED", False)
        )
        self.assertEqual(
            getattr(analytics, "BACKTEST_BENCHMARK_ENTRY_BASIS", ""), "OPEN"
        )

    def test_benchmark_return_uses_same_session_open_as_stock_entry(self) -> None:
        benchmark = pd.DataFrame(
            {
                "Open": [100.0, 105.0, 110.0],
                "Close": [104.0, 115.0, 121.0],
            },
            index=pd.to_datetime(["2026-01-05", "2026-01-26", "2026-03-30"]),
        )
        samples = [
            {
                "entry_date": "2026-01-05",
                "exit20_date": "2026-01-26",
                "exit60_date": "2026-03-30",
                "benchmark_return20": 999.0,
                "benchmark_return60": 999.0,
            }
        ]

        aligned = align_benchmark_returns(samples, benchmark)[0]

        self.assertEqual(aligned["benchmark_entry_basis"], "OPEN")
        self.assertAlmostEqual(aligned["benchmark_entry_price"], 100.0)
        self.assertAlmostEqual(aligned["benchmark_return20"], 15.0)
        self.assertAlmostEqual(aligned["benchmark_return60"], 21.0)
        self.assertEqual(aligned["benchmark_alignment_status"], "ALIGNED")

    def test_benchmark_alignment_never_asof_falls_back_to_previous_session(self) -> None:
        benchmark = pd.DataFrame(
            {"Open": [100.0, 110.0], "Close": [105.0, 120.0]},
            index=pd.to_datetime(["2026-01-05", "2026-01-07"]),
        )
        samples = [
            {
                "entry_date": "2026-01-06",
                "exit20_date": "2026-01-07",
                "exit60_date": "2026-01-07",
            }
        ]

        aligned = align_benchmark_returns(samples, benchmark)[0]

        self.assertTrue(np.isnan(aligned["benchmark_return20"]))
        self.assertTrue(np.isnan(aligned["benchmark_return60"]))
        self.assertEqual(aligned["benchmark_alignment_status"], "INCOMPLETE")

    def test_liquidity_gate_prefers_cny_turnover_over_share_count(self) -> None:
        frame = pd.DataFrame(
            {
                "Volume": [100_000.0] * 60,
                "Amount": [10_000_000.0] * 60,
            }
        )

        result = filter_min_volume(frame)

        self.assertTrue(result.passed)
        self.assertEqual(result.details["liquidity_basis"], "turnover_cny")
        self.assertEqual(result.details["median_turnover_60"], 10_000_000.0)

    def test_liquidity_gate_keeps_share_fallback_for_legacy_cache(self) -> None:
        frame = pd.DataFrame({"Volume": [300_000.0] * 60})

        result = filter_min_volume(frame)

        self.assertTrue(result.passed)
        self.assertEqual(result.details["liquidity_basis"], "shares_fallback")

    def test_filter_and_score_share_same_volatility_contraction_state(self) -> None:
        bb_width = [10.0] * 70 + list(np.linspace(9.0, 6.0, 10))
        frame = pd.DataFrame(
            {
                "ATR14": [9.5] * 80,
                "ATR50": [10.0] * 80,
                "HV20": [95.0] * 80,
                "HV60": [100.0] * 80,
                "BB_Width": bb_width,
            }
        )

        gate = filter_volatility_contraction(frame)
        score = score_volatility(frame)

        self.assertTrue(gate.passed)
        self.assertTrue(gate.details["bb_contracting"])
        self.assertFalse(gate.details["atr_contracting"])
        self.assertFalse(gate.details["hv_contracting"])
        self.assertGreater(score, 0.0)

    def test_tickflow_share_unit_inference_is_explicit_and_sane(self) -> None:
        symbol = "000001.SZ"
        previous = downloader._INSTRUMENT_META.get(symbol)
        downloader._INSTRUMENT_META[symbol] = {
            "symbol": symbol,
            "ext": {"total_shares": 500_000.0},
        }
        try:
            evidence = downloader.get_market_cap_evidence(
                symbol,
                frame=pd.DataFrame({"Close": [10.0]}),
                fetch=False,
            )
        finally:
            if previous is None:
                downloader._INSTRUMENT_META.pop(symbol, None)
            else:
                downloader._INSTRUMENT_META[symbol] = previous

        self.assertTrue(evidence["unit_inference_used"])
        self.assertEqual(evidence["unit_assumption"], "10k_shares_inferred")
        self.assertEqual(evidence["normalized_total_shares"], 5_000_000_000.0)
        self.assertEqual(evidence["market_cap"], 50_000_000_000.0)
        self.assertTrue(evidence["market_cap_sanity_passed"])

    def test_etf_limit_rule_uses_cached_security_metadata(self) -> None:
        symbol = "588000.SH"
        previous = downloader._INSTRUMENT_META.get(symbol)
        downloader._INSTRUMENT_META[symbol] = {
            "symbol": symbol,
            "ext": {"limit_up": 0.20, "limit_down": 0.20},
        }
        downloader.get_price_limit_pct.cache_clear()
        try:
            self.assertAlmostEqual(daily_limit_pct(symbol, is_etf=True), 0.20)
            frame = pd.DataFrame(
                {
                    "Open": [10.0, 11.5],
                    "High": [10.1, 11.6],
                    "Low": [9.9, 11.5],
                    "Close": [10.0, 11.55],
                    "Volume": [1_000_000.0, 1_000_000.0],
                }
            )
            tradeable, reason = is_entry_tradeable(
                symbol, frame, 1, is_etf=True
            )
        finally:
            downloader.get_price_limit_pct.cache_clear()
            if previous is None:
                downloader._INSTRUMENT_META.pop(symbol, None)
            else:
                downloader._INSTRUMENT_META[symbol] = previous

        self.assertTrue(tradeable)
        self.assertEqual(reason, "tradeable")

    @staticmethod
    def _enriched(ticker: str) -> SimpleNamespace:
        return SimpleNamespace(
            ticker=ticker,
            error="",
            technical_institutional_score=50.0,
            institutional_score=50.0,
            data_source="tickflow",
            data_asof="2026-08-17",
        )

    def test_enriched_stage_view_is_complete_and_immutable(self) -> None:
        view = EnrichedResultView.from_result(self._enriched("000001.SZ"))
        self.assertTrue(view.complete)
        self.assertEqual(view.ticker, "000001.SZ")
        with self.assertRaises(FrozenInstanceError):
            view.ticker = "000002.SZ"  # type: ignore[misc]

    def test_enrichment_contract_quarantines_small_isolated_miss(self) -> None:
        rows = [self._enriched(f"{index:06d}.SZ") for index in range(100)]
        incomplete = SimpleNamespace(
            ticker="999999.SZ",
            error="",
            technical_institutional_score=np.nan,
            institutional_score=np.nan,
            data_source="",
            data_asof="",
        )
        rows.append(incomplete)

        health = enforce_enrichment_contract(rows)

        self.assertEqual(health.status, "DEGRADED")
        self.assertGreaterEqual(health.complete_ratio, 0.98)
        self.assertIn("ENRICHMENT_INCOMPLETE", incomplete.error)

    def test_enrichment_contract_fails_closed_on_material_loss(self) -> None:
        rows = [self._enriched("000001.SZ")]
        rows.append(
            SimpleNamespace(
                ticker="000002.SZ",
                error="",
                technical_institutional_score=np.nan,
                institutional_score=np.nan,
                data_source="",
                data_asof="",
            )
        )

        with self.assertRaisesRegex(ValueError, "ENRICHMENT_CONTRACT_FAILED"):
            enforce_enrichment_contract(rows)

    def test_public_result_exposes_new_data_provenance(self) -> None:
        result = ScanResult(
            ticker="000001.SZ",
            filter_details={
                "market_cap": 10_000_000_000.0,
                "market_cap_available": True,
                "market_cap_unit_inferred": True,
                "market_cap_unit_assumption": "10k_shares_inferred",
                "market_cap_raw_total_shares": 100_000.0,
                "market_cap_normalized_total_shares": 1_000_000_000.0,
                "market_cap_sanity_passed": True,
                "liquidity_basis": "turnover_cny",
                "median_turnover_60": 20_000_000.0,
                "turnover_observations": 60,
                "price_limit_pct": 0.10,
            },
        )
        with patch.object(
            report, "finalize_signal_ranking", side_effect=lambda frame: frame
        ):
            row = report._results_to_dataframe([result]).iloc[0]

        self.assertTrue(row["MarketCapUnitInferred"])
        self.assertEqual(row["LiquidityBasis"], "turnover_cny")
        self.assertEqual(row["MedianTurnover60"], 20_000_000.0)
        self.assertAlmostEqual(row["PriceLimitPct"], 0.10)

    def test_pipeline_stage_contract_is_explicit(self) -> None:
        self.assertEqual(PipelineStage.RAW_SCAN.value, "RAW_SCAN")
        self.assertEqual(PipelineStage.ENRICHED.value, "ENRICHED")
        self.assertEqual(PipelineStage.DECISION.value, "DECISION")
        self.assertEqual(PipelineStage.PUBLISHED.value, "PUBLISHED")


if __name__ == "__main__":
    unittest.main()
