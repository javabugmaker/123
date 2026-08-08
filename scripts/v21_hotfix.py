from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected exactly one match, got {count}: {old[:100]!r}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Pandas: parse the wide result file in one pass and consolidate after the
# backtest metrics merge. This removes the mixed-type DtypeWarning and the
# cascade of highly-fragmented PerformanceWarnings without changing values.
replace_once(
    "analytics.py",
    '    frame = pd.read_csv(path, encoding="utf-8-sig")\n',
    '    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False).copy()\n',
)
replace_once(
    "analytics.py",
    '    frame = frame.merge(\n'
    '        metrics,\n'
    '        on=["Ticker", "EntrySignal"],\n'
    '        how="left",\n'
    '        validate="one_to_one",\n'
    '    )\n',
    '    frame = frame.merge(\n'
    '        metrics,\n'
    '        on=["Ticker", "EntrySignal"],\n'
    '        how="left",\n'
    '        validate="one_to_one",\n'
    '    ).copy()\n',
)

# ATRExpansion: the threaded scanner handed enrichment a reduced frame that
# omitted ATR14/ATR50, so enrichment overwrote a valid scanner value with NaN.
replace_once(
    "scanner.py",
    '        "MA20",\n'
    '        "MA50",\n'
    '        "RSI14",\n',
    '        "MA20",\n'
    '        "MA50",\n'
    '        "ATR14",\n'
    '        "ATR50",\n'
    '        "RSI14",\n',
)
replace_once(
    "analytics.py",
    '    atr14 = _finite_float(enriched["ATR14"].iloc[-1]) if "ATR14" in enriched else np.nan\n'
    '    atr50 = _finite_float(enriched["ATR50"].iloc[-1]) if "ATR50" in enriched else np.nan\n'
    '    result.atr_expansion = (\n'
    '        atr14 / atr50\n'
    '        if np.isfinite(atr14) and np.isfinite(atr50) and atr50 > 0\n'
    '        else np.nan\n'
    '    )\n',
    '    atr14 = _finite_float(enriched["ATR14"].iloc[-1]) if "ATR14" in enriched else np.nan\n'
    '    atr50 = _finite_float(enriched["ATR50"].iloc[-1]) if "ATR50" in enriched else np.nan\n'
    '    if not np.isfinite(atr14):\n'
    '        atr14 = _finite_float(getattr(result, "atr14", np.nan))\n'
    '    if not np.isfinite(atr50):\n'
    '        atr50 = _finite_float(getattr(result, "atr50", np.nan))\n'
    '    if np.isfinite(atr14):\n'
    '        result.atr14 = atr14\n'
    '    if np.isfinite(atr50):\n'
    '        result.atr50 = atr50\n'
    '    result.atr_expansion = (\n'
    '        atr14 / atr50\n'
    '        if np.isfinite(atr14) and np.isfinite(atr50) and atr50 > 0\n'
    '        else np.nan\n'
    '    )\n',
)

# ETF eligibility: stock price/market-cap floors do not apply to ETFs, while
# liquidity and sufficient history remain mandatory universe conditions.
replace_once(
    "scanner.py",
    '        base_filter_states = {\n'
    '            "min_price": filter_results.min_price.passed,\n'
    '            "min_volume": filter_results.min_volume.passed,\n'
    '            "min_market_cap": filter_results.min_market_cap.passed,\n'
    '            "sufficient_history": filter_results.sufficient_history.passed,\n'
    '        }\n',
    '        base_filter_states = {\n'
    '            "min_price": True if ticker_info.is_etf else filter_results.min_price.passed,\n'
    '            "min_volume": filter_results.min_volume.passed,\n'
    '            "min_market_cap": True if ticker_info.is_etf else filter_results.min_market_cap.passed,\n'
    '            "sufficient_history": filter_results.sufficient_history.passed,\n'
    '        }\n',
)
replace_once(
    "signal_lifecycle.py",
    '    filter_override = (\n'
    '        ~passed_filters\n'
    '        & signal.eq("BREAKOUT_CONFIRM")\n',
    '    universe_eligible = _bool_series(result, "UniverseEligible", True)\n'
    '    filter_override = (\n'
    '        ~passed_filters\n'
    '        & universe_eligible\n'
    '        & signal.eq("BREAKOUT_CONFIRM")\n',
)

# GUI: a user-filtered view must not overwrite the canonical model-generated
# diversified Top50.csv. Preserve it as a separate Top50Filtered.csv instead.
replace_once(
    "gui_core.py",
    '    def _write_top50_csv(self, tickers: list[str]) -> Path:\n'
    '        path = OUTPUT_DIR / "Top50.csv"\n',
    '    def _write_top50_csv(self, tickers: list[str]) -> Path:\n'
    '        path = OUTPUT_DIR / "Top50Filtered.csv"\n',
)
replace_once(
    "gui_core.py",
    '            if not self.load_csv("Top50.csv"):\n'
    '                raise ValueError("Top50.csv 已生成，但未包含有效结果")\n',
    '            if not self.load_csv("Top50Filtered.csv"):\n'
    '                raise ValueError("Top50Filtered.csv 已生成，但未包含有效结果")\n',
)
replace_once(
    "gui_core.py",
    '        self.append_log(f"已从当前筛选结果生成 Top50.csv：{len(tickers)} 只\\n")\n',
    '        self.append_log(f"已从当前筛选结果生成 Top50Filtered.csv：{len(tickers)} 只\\n")\n',
)

# Bump output/execution semantics so old v20 checkpoints and indicator caches
# cannot be mistaken for a clean v21 run.
replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-09-v20-ranking-integrity"',
    'SCORING_VERSION: str = "2026-08-09-v21-output-integrity"',
)
replace_once(
    "test_model_v19_regressions.py",
    'self.assertEqual(SCORING_VERSION, "2026-08-09-v20-ranking-integrity")',
    'self.assertEqual(SCORING_VERSION, "2026-08-09-v21-output-integrity")',
)

Path("test_output_integrity_v21.py").write_text(
    '''from __future__ import annotations

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
                "Ticker,RankingScore\\nCANON,99\\n", encoding="utf-8"
            )
            path = gui._write_top50_csv(["A"])
            self.assertEqual(path.name, "Top50Filtered.csv")
            self.assertIn("CANON", canonical.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

# The staging transport is intentionally self-cleaning.
Path("scripts/v21_hotfix.py").unlink(missing_ok=True)
Path(".github/workflows/v21-output-integrity-hotfix.yml").unlink(missing_ok=True)
