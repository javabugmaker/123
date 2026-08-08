from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import gui_core
from downloader import TickerInfo
from scanner import _analyse_one_ticker_from_df
from signal_lifecycle import finalize_signal_ranking


class OutputIntegrityV21Tests(unittest.TestCase):
    def test_enrichment_frame_keeps_atr_columns(self):
        index = pd.date_range("2024-01-01", periods=320, freq="B")
        close = pd.Series(np.linspace(1.0, 1.5, len(index)), index=index)
        raw = pd.DataFrame({
            "Open": close,
            "High": close + 0.02,
            "Low": close - 0.02,
            "Close": close,
            "Volume": 2_000_000.0,
        }, index=index)
        ticker = TickerInfo(
            ticker="510300.SH", name="测试ETF", is_etf=True, asset_type="etf"
        )
        result, enrichment = _analyse_one_ticker_from_df(ticker, raw, "tickflow")
        self.assertFalse(result.error)
        self.assertIsNotNone(enrichment)
        self.assertIn("ATR14", enrichment.columns)
        self.assertIn("ATR50", enrichment.columns)
        self.assertTrue(np.isfinite(result.atr_expansion))

    def test_breakout_override_cannot_bypass_universe_gate(self):
        base = {
            "Ticker": "ETF1",
            "IsETF": True,
            "AssetType": "etf",
            "InstitutionalScore": 50.0,
            "FinalScore": 50.0,
            "Score": 50.0,
            "EntrySignal": "BREAKOUT_CONFIRM",
            "PassedFilters": False,
            "BreakoutVolumeConfirmed": True,
            "BreakoutFlowConfirmed": True,
            "BreakoutVolumeRatio": 1.5,
            "VolumeScore": 10.0,
            "CMF_Pos": True,
            "SignalStatus": "NEW",
            "QualityApplicable": False,
            "QualityDataCompleteness": 0.0,
            "QualityGate": True,
            "ScoreCoverage": 1.0,
            "DataTradingAgeDays": 0,
            "ValueTrapRisk": 0.0,
            "LifecycleStage": "趋势确认",
            "SignalRecencyDays": 1,
        }
        blocked = finalize_signal_ranking(
            pd.DataFrame([{**base, "UniverseEligible": False}])
        )
        self.assertNotEqual(blocked.loc[0, "RankingEligibility"], "推荐")
        ready = finalize_signal_ranking(
            pd.DataFrame([{**base, "UniverseEligible": True}])
        )
        self.assertEqual(ready.loc[0, "RankingEligibility"], "推荐")

    def test_gui_filtered_export_does_not_overwrite_canonical_top50(self):
        gui = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)
        gui._csv_headers = ["Ticker", "RankingScore"]
        gui._csv_rows = [["A", "50"]]
        gui._csv_path = None
        gui._csv_mtime = None
        with tempfile.TemporaryDirectory() as directory, patch(
            "gui_core.OUTPUT_DIR", Path(directory)
        ):
            canonical = Path(directory) / "Top50.csv"
            canonical.write_text(
                "Ticker,RankingScore\nCANON,99\n", encoding="utf-8"
            )
            path = gui._write_top50_csv(["A"])
            self.assertEqual(path.name, "Top50Filtered.csv")
            self.assertIn("CANON", canonical.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
