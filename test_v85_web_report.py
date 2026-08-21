from __future__ import annotations

import csv
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import web_report_v85 as web
from v85_terminal_config import HOME_SECTIONS, SECTION_TITLES


class WebReportV85Tests(unittest.TestCase):
    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _ohlcv() -> pd.DataFrame:
        index = pd.date_range("2026-03-02", "2026-08-21", freq="B")
        close = pd.Series(range(len(index)), index=index, dtype=float) * 0.02 + 10.0
        frame = pd.DataFrame(index=index)
        frame["Open"] = close - 0.03
        frame["High"] = close + 0.11
        frame["Low"] = close - 0.12
        frame["Close"] = close
        frame["Volume"] = 1_000_000 + pd.Series(range(len(index)), index=index) * 1000
        return frame

    def test_v85_sections_views_public_safety_and_historical_cutoff(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            site = Path(temp_dir) / "site"
            output.mkdir()
            rows = [
                {
                    "Ticker": "000001.SZ",
                    "Name": "<script>alert(1)</script>",
                    "AssetType": "STOCK",
                    "Industry": "银行",
                    "Close": "12.34",
                    "ResearchRank": "1",
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
                    "DirectionalResearchEligible": "True",
                    "DirectionalResearchReason": "",
                    "BreakoutPriceConfirmationScore": "82.5",
                    "BreakoutPriceGatePassed": "True",
                    "BreakoutPriceGateReason": "突破价格确认强度满足执行门槛",
                    "TradeEconomicsPassed": "True",
                    "TradeEstimatedRoundTripCostPct": "0.238829",
                    "TradeTargetCostMultiple": "8.20",
                    "TradeEconomicsReason": "预期目标足以覆盖估算往返交易成本",
                    "UserAccountSecret": "MUST_NOT_LEAK",
                },
                {
                    "Ticker": "510300.SH",
                    "Name": "沪深300ETF",
                    "AssetType": "ETF",
                    "ETFTheme": "宽基",
                    "Close": "4.321",
                    "ResearchRank": "2",
                    "TradeRank": "3",
                    "AlphaScore": "66.2",
                    "ExecutionState": "CAUTIOUS",
                    "EntrySignal": "WAIT_PULLBACK",
                    "EntryZone": "4.280-4.310",
                    "StopLoss": "4.100",
                    "ProjectedTarget": "4.800",
                    "QualityLayerStatus": "NOT_APPLICABLE",
                    "DataAsOf": "2026-08-20",
                    "SignalStatus": "ACTIVE",
                    "DirectionalResearchEligible": "False",
                    "DirectionalResearchReason": "ETF现金管理产品排除：财富宝</script><script>alert(2)</script>",
                    "BreakoutPriceConfirmationScore": "50.3",
                    "BreakoutPriceGatePassed": "False",
                    "BreakoutPriceGateReason": "突破幅度仍处于零附近过渡区，价格确认强度不足",
                    "TradeEconomicsPassed": "False",
                    "TradeEstimatedRoundTripCostPct": "0.238829",
                    "TradeTargetCostMultiple": "0.1465",
                    "TradeEconomicsReason": "预期目标不足以覆盖最低往返成本倍数",
                },
                {
                    "Ticker": "600000.SH",
                    "Name": "浦发银行",
                    "AssetType": "STOCK",
                    "Industry": "银行",
                    "Close": "9.12",
                    "ResearchRank": "3",
                    "TradeRank": "8",
                    "AlphaScore": "54.0",
                    "ExecutionState": "BLOCKED",
                    "EntrySignal": "HOLD_WAIT",
                    "QualityLayerStatus": "DATA_INCOMPLETE",
                    "DataAsOf": "2026-08-20",
                    "SignalStatus": "WATCH",
                },
            ]
            self._write_csv(output / "AllResults.csv", rows)
            self._write_csv(output / "Top50Mixed.csv", rows)
            (output / "DailyRunSummary.json").write_text(
                json.dumps(
                    {
                        "effective_trading_date": "2026-08-20",
                        "elapsed_seconds": 125,
                        "publish_status": "published",
                        "freshness": {"all_results_ratio": 0.99},
                        "quality_gate": {"pass_rate": 0.88},
                        "stage_seconds": {"scan": 60, "backtest": 50},
                        "backtest": {"cache_hit_rate": 0.75, "cache_health": "健康"},
                        "run_diff": {
                            "eligibility_upgraded": 1,
                            "eligibility_downgraded": 2,
                            "score_up_5_plus": 3,
                            "score_down_5_plus": 4,
                            "upgraded_examples": ["000001.SZ"],
                            "downgraded_examples": ["600000.SH"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(web._v84, "_load_cache", return_value=self._ohlcv()):
                result = web.build_web_report(output_dir=output, site_dir=site)
            page = result.index_path.read_text(encoding="utf-8")

        self.assertIn("A股研究简报", page)
        self.assertIn("RESEARCH UNIVERSE / 研究池", page)
        self.assertIn("SECTOR ROTATION / 行业轮动", page)
        self.assertIn("RISK RADAR / 风险雷达", page)
        self.assertIn("MODEL CHANGES / 本轮变化", page)
        self.assertIn('data-view="stocks"', page)
        self.assertIn('data-view="etf"', page)
        self.assertIn("000001.SZ", page)
        self.assertIn("510300.SH", page)
        self.assertNotIn("MUST_NOT_LEAK", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("方向性研究准入", page)
        self.assertIn("突破价格确认", page)
        self.assertIn("估算往返成本", page)
        self.assertIn("目标 / 成本", page)
        self.assertIn("执行经济性", page)
        self.assertIn("2026-08-21-v87-directional-execution-diagnostics-v1", page)
        self.assertNotIn("</script><script>alert(2)</script>", page)

        positions = [page.index(f'data-section="{section}"') for section in HOME_SECTIONS]
        self.assertEqual(positions, sorted(positions))
        for section in HOME_SECTIONS:
            self.assertIn(SECTION_TITLES[section], page)

        match = re.search(
            r'<script id="图表数据" type="application/json">(.*?)</script>',
            page,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        charts = json.loads(match.group(1))
        self.assertEqual(max(charts["000001.SZ"]["d"]), "2026-08-20")

        details_match = re.search(
            r'<script id="详情数据" type="application/json">(.*?)</script>',
            page,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(details_match)
        assert details_match is not None
        details = json.loads(details_match.group(1))
        stock = details["000001.SZ"]
        self.assertEqual(stock["directionalGate"], "通过")
        self.assertEqual(stock["breakoutGate"], "通过")
        self.assertEqual(stock["breakoutConfirmation"], 82.5)
        self.assertEqual(stock["economicsGate"], "通过")
        self.assertAlmostEqual(stock["roundTripCostPct"], 0.238829)
        self.assertEqual(stock["targetCostMultiple"], 8.2)

        excluded = details["510300.SH"]
        self.assertEqual(excluded["directionalGate"], "未通过")
        self.assertEqual(excluded["breakoutGate"], "未通过")
        self.assertEqual(excluded["economicsGate"], "未通过")
        self.assertEqual(excluded["targetCostMultiple"], 0.1465)
        self.assertIn("财富宝</script>", excluded["directionalReason"])

    def test_archive_is_newest_first(self) -> None:
        with TemporaryDirectory() as temp_dir:
            site = Path(temp_dir)
            reports = site / "reports"
            reports.mkdir()
            (reports / "2026-08-19.html").write_text("old", encoding="utf-8")
            (reports / "2026-08-20.html").write_text("new", encoding="utf-8")
            archive = web._archive_html(site)
        self.assertLess(archive.index("2026-08-20"), archive.index("2026-08-19"))


if __name__ == "__main__":
    unittest.main()
