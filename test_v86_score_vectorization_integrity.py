from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtest_alignment as stable_alignment
import backtest_alignment_acceleration_v80 as alignment_fast
import config
import report
import score
import score_acceleration_v79 as score_fast
import score_core
from classification import etf_research_eligibility
from indicators import compute_all_indicators


def _enriched_frame(rows: int = 700) -> pd.DataFrame:
    rng = np.random.default_rng(20260821)
    close = 24.0 * np.cumprod(1.0 + rng.normal(0.0003, 0.014, rows))
    open_price = close * (1.0 + rng.normal(0.0, 0.004, rows))
    high = np.maximum(open_price, close) * (
        1.0 + rng.uniform(0.002, 0.018, rows)
    )
    low = np.minimum(open_price, close) * (
        1.0 - rng.uniform(0.002, 0.018, rows)
    )
    volume = rng.integers(700_000, 12_000_000, rows).astype(float)
    raw = pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Amount": volume * close,
        },
        index=pd.bdate_range("2023-10-02", periods=rows),
    )
    return compute_all_indicators(raw)


def _scalar_research_policy(frame: pd.DataFrame) -> pd.DataFrame:
    """Frozen pre-v86 reference used only for exact-equivalence testing."""
    working = frame.copy()
    eligibility: list[bool] = []
    reasons: list[str] = []
    for row in working.to_dict(orient="records"):
        asset = str(row.get("AssetType", "") or "").strip().lower()
        is_etf = report._truthy(row.get("IsETF", False)) or asset == "etf"
        eligible, reason = etf_research_eligibility(
            is_etf=is_etf,
            name=row.get("Name", ""),
            industry=row.get("Industry", ""),
            sector=row.get("Sector", ""),
            classification=row.get(
                "ModelClassification", row.get("ETFTheme", "")
            ),
            ticker=row.get("Ticker", ""),
        )
        hard_value = row.get("HardGatePassed")
        try:
            hard_missing = hard_value is None or pd.isna(hard_value)
        except (TypeError, ValueError):
            hard_missing = hard_value is None
        if hard_missing or str(hard_value).strip() == "":
            hard_value = row.get("UniverseEligible", True)
        hard_ok = report._truthy(hard_value)
        failed_names = report._clean_group_key(row.get("HardGateFailedNames", ""))
        if (
            not hard_ok
            and not failed_names
            and report._truthy(row.get("PassedFilters", False))
        ):
            hard_ok = True
        if not hard_ok:
            hard_reason = (
                f"硬准入失败：{failed_names}"
                if failed_names
                else "硬准入条件未通过"
            )
            reason = f"{reason}；{hard_reason}" if reason else hard_reason
        eligibility.append(bool(eligible) and hard_ok)
        reasons.append(str(reason or ""))
    working["ResearchEligible"] = eligibility
    working["ResearchExclusionReason"] = reasons
    return working


def _scalar_alignment(
    samples: list[dict[str, object]], benchmark: pd.DataFrame
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for source in samples:
        item = dict(source)
        entry = stable_alignment._LEGACY_PRICE_ON_DATE(
            benchmark, item.get("entry_date"), "Open"
        )
        item["benchmark_entry_basis"] = "OPEN"
        item["benchmark_entry_price"] = entry
        complete = True
        for horizon in (20, 60):
            exit_price = stable_alignment._LEGACY_PRICE_ON_DATE(
                benchmark, item.get(f"exit{horizon}_date"), "Close"
            )
            if (
                np.isfinite(entry)
                and entry > 0.0
                and np.isfinite(exit_price)
                and exit_price > 0.0
            ):
                item[f"benchmark_return{horizon}"] = (
                    float(exit_price / entry - 1.0) * 100.0
                )
            else:
                item[f"benchmark_return{horizon}"] = np.nan
                complete = False
        item["benchmark_alignment_status"] = (
            "ALIGNED" if complete else "INCOMPLETE"
        )
        result.append(item)
    return result


class ScoreTransactionIntegrityTests(unittest.TestCase):
    def tearDown(self) -> None:
        score_fast.clear_thread_score_cache()

    def test_in_place_indicator_update_cannot_reuse_previous_score_arrays(self) -> None:
        frame = _enriched_frame()
        before = score.score_ticker(frame)

        frame.loc[frame.index[-20:], "CMF"] = np.linspace(-0.4, 0.4, 20)
        after = score.score_ticker(frame)
        score_fast.clear_thread_score_cache()
        independently_fresh = score.score_ticker(frame)

        self.assertGreater(abs(after.final_score - before.final_score), 0.1)
        for field in (
            "accumulation",
            "trigger_score",
            "base_score",
            "execution_score",
            "final_score",
        ):
            self.assertAlmostEqual(
                float(getattr(after, field)),
                float(getattr(independently_fresh, field)),
                places=12,
                msg=field,
            )

    def test_score_bounds_and_weight_reconstruction_remain_exact(self) -> None:
        result = score.score_ticker(_enriched_frame())
        component_limits = {
            "trend": 20.0,
            "volume": 25.0,
            "accumulation": 25.0,
            "volatility": 15.0,
            "structure": 15.0,
        }
        for field, upper in component_limits.items():
            self.assertGreaterEqual(float(getattr(result, field)), 0.0)
            self.assertLessEqual(float(getattr(result, field)), upper)
        setup, trigger, execution = score_core._model_component_weights()
        reconstructed = (
            result.base_score * setup
            + result.trigger_score * trigger
            + result.execution_score * execution
        )
        coverage_cap = 40.0 + 60.0 * result.indicator_coverage
        self.assertAlmostEqual(
            result.final_score,
            min(max(reconstructed, 0.0), 100.0, coverage_cap),
            places=12,
        )

    def test_v86_is_runtime_only_and_does_not_claim_a_formula_change(self) -> None:
        self.assertIn("v86", config.PIPELINE_VERSION)
        self.assertIn("v86", config.PERFORMANCE_ENGINE_VERSION)
        self.assertNotIn("v86", config.SCORING_VERSION)


class ResearchPolicyVectorizationTests(unittest.TestCase):
    def test_bulk_policy_matches_scalar_rules_with_duplicate_indexes(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "511990.SH",
                    "Name": "华宝添益ETF",
                    "Industry": "货币基金",
                    "Sector": "现金管理",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ModelClassification": "",
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "HardGateFailedNames": "",
                    "PassedFilters": False,
                },
                {
                    "Ticker": "511850.SH",
                    "Name": "招商财富宝ETF",
                    "Industry": "",
                    "Sector": "",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ModelClassification": "财富宝",
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "HardGateFailedNames": "",
                    "PassedFilters": False,
                },
                {
                    "Ticker": "159001.SZ",
                    "Name": "现金流ETF",
                    "Industry": "因子",
                    "Sector": "股票",
                    "AssetType": "ETF",
                    "IsETF": "yes",
                    "ModelClassification": "现金流因子",
                    "HardGatePassed": "",
                    "UniverseEligible": "1",
                    "HardGateFailedNames": np.nan,
                    "PassedFilters": False,
                },
                {
                    "Ticker": "000001.SZ",
                    "Name": "平安银行",
                    "Industry": "银行",
                    "Sector": "金融",
                    "AssetType": "stock",
                    "IsETF": False,
                    "ModelClassification": "银行",
                    "HardGatePassed": False,
                    "UniverseEligible": False,
                    "HardGateFailedNames": "min_price",
                    "PassedFilters": True,
                },
                {
                    "Ticker": "000002.SZ",
                    "Name": "万科",
                    "Industry": "地产",
                    "Sector": "房地产",
                    "AssetType": "stock",
                    "IsETF": False,
                    "ModelClassification": "地产",
                    "HardGatePassed": False,
                    "UniverseEligible": False,
                    "HardGateFailedNames": "",
                    "PassedFilters": "是",
                },
                {
                    "Ticker": "511360.SH",
                    "Name": "短债ETF",
                    "Industry": "",
                    "Sector": "",
                    "AssetType": "etf",
                    "IsETF": False,
                    "ModelClassification": pd.NA,
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "HardGateFailedNames": "",
                    "PassedFilters": False,
                },
            ],
            index=[4, 4, 4, 9, 12, 12],
        )
        with pd.option_context("mode.copy_on_write", True):
            expected = _scalar_research_policy(frame)
            with patch.object(
                pd.DataFrame,
                "iterrows",
                side_effect=AssertionError(
                    "research policy must stay on the bulk path"
                ),
            ):
                actual = report._apply_research_policy(frame)
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False)


class BenchmarkAlignmentVectorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        alignment_fast.clear_benchmark_alignment_cache()

    def test_batch_date_normalization_matches_scalar_contract(self) -> None:
        values: list[object] = [
            "2026-01-06T15:00:00",
            pd.Timestamp("2026-01-07 13:45:00"),
            pd.Timestamp("2026-01-08 23:00:00", tz="Asia/Shanghai"),
            date(2026, 1, 9),
            "January 10, 2026",
            None,
            "invalid",
        ]
        expected = np.asarray(
            [alignment_fast._date_key(value) for value in values], dtype=object
        )
        np.testing.assert_array_equal(alignment_fast._date_keys(values), expected)

    def test_alignment_matches_scalar_for_mixed_and_missing_dates(self) -> None:
        benchmark = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0, np.nan],
                "Close": [100.5, 101.5, 102.5, 103.5, 104.5],
            },
            index=pd.to_datetime(
                [
                    "2026-01-05",
                    "2026-01-06",
                    "2026-01-06",
                    "2026-01-07",
                    "2026-01-08",
                ]
            ),
        )
        samples: list[dict[str, object]] = [
            {
                "entry_date": pd.Timestamp("2026-01-06"),
                "exit20_date": "2026-01-07T15:00:00",
                "exit60_date": date(2026, 1, 8),
            },
            {
                "entry_date": "2026-01-08",
                "exit20_date": "2026-01-09",
                "exit60_date": None,
            },
        ]
        expected = _scalar_alignment(samples, benchmark)
        actual = alignment_fast.align_benchmark_returns(samples, benchmark)
        pd.testing.assert_frame_equal(
            pd.DataFrame(actual).sort_index(axis=1),
            pd.DataFrame(expected).sort_index(axis=1),
            check_dtype=False,
        )

    def test_homogeneous_datetime_batch_never_calls_scalar_parser(self) -> None:
        values = list(pd.bdate_range("2026-01-05", periods=128))
        with patch.object(
            alignment_fast,
            "_date_key",
            side_effect=AssertionError("homogeneous dates must be vectorized"),
        ):
            keys = alignment_fast._date_keys(values)
        self.assertEqual(keys[0], "2026-01-05")
        self.assertEqual(keys[-1], "2026-07-01")


if __name__ == "__main__":
    unittest.main()
