from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import web_report_v84 as web


class WebReportV84Tests(unittest.TestCase):
    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _ohlcv() -> pd.DataFrame:
        index = pd.date_range("2026-04-01", "2026-08-21", freq="B")
        close = pd.Series(range(len(index)), index=index, dtype=float) * 0.03 + 10.0
        frame = pd.DataFrame(index=index)
        frame["Open"] = close - 0.03
        frame["High"] = close + 0.10
        frame["Low"] = close - 0.12
        frame["Close"] = close
        frame["Volume"] = 1_000_000 + pd.Series(range(len(index)), index=index) * 1000
        return frame

    def test_terminal_is_chinese_interactive_and_future_safe(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            site = Path(temp_dir) / "site"
            output.mkdir()
            rows = [
                {
                    "Ticker": "000001.SZ",
                    "Name": "平安银行",
                    "AssetType": "STOCK",
                    "Industry": "银行",
                    "Close": "12.34",
                    "ResearchRank": "3",
                    "TradeRank": "1",
                    "AlphaScore": "78.6",
                    "ExecutionState": "READY",
                    "EntrySignal": "BREAKOUT_CONFIRM",
                    "EntryZone": "12.10-12.20",
                    "BreakoutBuyPrice": "12.40",
                    "StopLoss": "11.70",
                    "ProjectedTarget": "14.20",
                    "SmoothTriggerScore": "72.4",
                    "QualityLayerStatus": "PASS",
                    "DataAsOf": "2026-08-20",
                    "SignalStatus": "NEW",
                    "SignalDays": "1",
                    "UserAccountSecret": "MUST_NOT_LEAK",
                }
            ]
            self._write_csv(output / "AllResults.csv", rows)
            self._write_csv(output / "Top50Mixed.csv", rows)
            (output / "DailyRunSummary.json").write_text(
                json.dumps({"effective_trading_date": "2026-08-20"}),
                encoding="utf-8",
            )

            with patch.object(web, "_load_cache", return_value=self._ohlcv()):
                result = web.build_web_report(output_dir=output, site_dir=site)
            page = result.index_path.read_text(encoding="utf-8")

        self.assertIn("机构交易研究终端", page)
        self.assertIn("今日研究榜", page)
        self.assertIn("研究#", page)
        self.assertIn("执行状态", page)
        self.assertIn('id="日K图"', page)
        self.assertIn("EMA20", page)
        self.assertIn("000001.SZ", page)
        self.assertIn("可执行", page)
        self.assertNotIn("MUST_NOT_LEAK", page)
        # 报告日为 8/20；即使缓存已有 8/21，也绝不能写入历史报告图表。
        self.assertNotIn("2026-08-21", page)

    def test_chart_frame_is_cut_off_at_report_date(self) -> None:
        with patch.object(web, "_load_cache", return_value=self._ohlcv()):
            frame = web._chart_frame("000001.SZ", "2026-08-20")
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertLessEqual(frame.index.max(), pd.Timestamp("2026-08-20"))
        self.assertIn("EMA20", frame.columns)
        self.assertIn("EMA50", frame.columns)
        self.assertIn("EMA200", frame.columns)


if __name__ == "__main__":
    unittest.main()
