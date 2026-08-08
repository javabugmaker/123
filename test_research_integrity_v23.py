from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import score
from calibration_bridge import bridge_global_calibration
from config import MODEL_QUALITY_WEIGHT
from historical_universe import merge_with_cached_universe
from tradeability import daily_limit_pct, is_entry_tradeable
from trading_calendar import is_trading_day, trading_age_days


class ResearchIntegrityV23Tests(unittest.TestCase):
    def test_china_holiday_is_not_trading_day(self):
        self.assertFalse(is_trading_day(date(2026, 10, 1)))
        now = datetime(2026, 10, 1, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(trading_age_days(date(2026, 9, 30), now), 0)

    def test_calibration_weights_hot_reload(self):
        original_output = score.OUTPUT_DIR
        with tempfile.TemporaryDirectory() as tmp:
            score.OUTPUT_DIR = Path(tmp)
            score.invalidate_model_weight_cache()
            path = score.OUTPUT_DIR / "ScoreCalibration.json"
            path.write_text(json.dumps({
                "accepted": True,
                "setup_weight": 0.60,
                "trigger_weight": 0.25,
                "execution_weight": 0.15,
            }), encoding="utf-8")
            first = score._model_component_weights()
            path.write_text(json.dumps({
                "accepted": True,
                "setup_weight": 0.55,
                "trigger_weight": 0.30,
                "execution_weight": 0.15,
            }), encoding="utf-8")
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            second = score._model_component_weights()
            self.assertEqual(first, (0.60, 0.25, 0.15))
            self.assertEqual(second, (0.55, 0.30, 0.15))
        score.OUTPUT_DIR = original_output
        score.invalidate_model_weight_cache()

    def test_locked_limit_up_is_not_buyable(self):
        frame = pd.DataFrame({
            "Open": [9.9, 10.0, 11.0],
            "High": [10.1, 10.1, 11.0],
            "Low": [9.8, 9.9, 11.0],
            "Close": [10.0, 10.0, 11.0],
            "Volume": [1000, 1000, 1000],
        })
        ok, reason = is_entry_tradeable("600000.SH", frame, 2, is_etf=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "locked_limit_up")
        self.assertEqual(daily_limit_pct("300001.SZ"), 0.20)
        ok_growth, _ = is_entry_tradeable("300001.SZ", frame, 2, is_etf=False)
        self.assertTrue(ok_growth)

    def test_fast_exact_bridge_adjusts_global_prior(self):
        fast = []
        exact = []
        for index in range(6):
            ticker = f"60000{index}.SH"
            fast.append({"ticker": ticker, "entry_signal": "BUY_NOW", "samples": 20, "backtest_adjusted_score": 50 + index})
            exact.append({"ticker": ticker, "entry_signal": "BUY_NOW", "samples": 20, "backtest_adjusted_score": 55 + index})
        adjusted, metadata = bridge_global_calibration(
            [{"level": "global", "calibration_score": 50.0, "confidence": 1.0}],
            fast,
            exact,
            min_samples=10,
        )
        self.assertTrue(metadata["accepted"])
        self.assertGreater(adjusted[0]["calibration_score"], 50.0)

    def test_historical_cache_union_adds_archived_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "600000.SH.parquet").touch()
            (cache / "000300.SH.parquet").touch()
            merged = merge_with_cached_universe(["000001.SZ"], cache)
            self.assertEqual(merged, ["000001.SZ", "600000.SH"])

    def test_fundamentals_are_gate_not_alpha_weight(self):
        self.assertEqual(MODEL_QUALITY_WEIGHT, 0.0)


if __name__ == "__main__":
    unittest.main()
