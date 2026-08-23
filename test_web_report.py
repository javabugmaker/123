from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import web_report as web


class CanonicalWebReportTests(unittest.TestCase):
    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _fixture(self, output: Path) -> None:
        rows = [
            {
                "Ticker": "000001.SZ",
                "Name": "平安银行",
                "AssetType": "STOCK",
                "Industry": "银行",
                "Close": "12.34",
                "EntrySignal": "BUY_NOW",
                "RankingEligibility": "推荐",
                "ExecutionState": "READY",
                "RankingScore": "88.5",
                "FinalScore": "80.0",
                "CompositeScore": "82.0",
                "AlphaScore": "72.5",
                "BacktestScore": "68.4",
                "BacktestAdjustedScore": "64.7",
                "BacktestEffectiveWeight": "0.12",
                "BacktestSamples": "37",
                "BacktestConfidenceTier": "中可信度",
                "ReferenceBuyPrice": "12.20-12.35",
                "StopLoss": "11.70",
                "ProjectedTarget": "13.80",
                "RiskRewardRatio": "2.2",
                "DataAsOf": "2026-08-21",
                "SignalStatus": "NEW",
                "UserAccountSecret": "MUST_NOT_LEAK",
            },
            {
                "Ticker": "510300.SH",
                "Name": "沪深300ETF",
                "AssetType": "ETF",
                "ETFTheme": "宽基",
                "Close": "4.21",
                "EntrySignal": "WAIT_PULLBACK",
                "RankingEligibility": "观察",
                "ExecutionState": "OBSERVE",
                "RankingScore": "61.0",
                "FinalScore": "61.0",
                "AlphaScore": "52.0",
                "DataAsOf": "2026-08-21",
                "SignalStatus": "WATCH",
                "UserAccountSecret": "MUST_NOT_LEAK",
            },
        ]
        self._write_csv(output / "AllResults.csv", rows)
        self._write_csv(output / "Top50Mixed.csv", rows)
        self._write_csv(output / "Top50TradeReady.csv", rows[:1])
        (output / "DailyRunSummary.json").write_text(
            json.dumps(
                {
                    "effective_trading_date": "2026-08-21",
                    "elapsed_seconds": 125,
                    "freshness": {"all_results_ratio": 1.0},
                    "stage_seconds": {"scan": 60, "backtest": 50},
                    "backtest": {"cache_hit_rate": 0.75},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (output / "BacktestSummary.json").write_text(
            json.dumps(
                {
                    "mode": "FAST",
                    "objective": "net_excess_return_20d",
                    "ranking_calibration_status": "ENABLED",
                    "ranking_calibration_samples": 37,
                    "resonance_analysis": {
                        "version": "v91",
                        "status": "EXPERIMENTAL_DIAGNOSTIC_ONLY",
                        "samples": 25,
                        "by_band": [
                            {
                                "group": "4-5/5",
                                "samples": 12,
                                "net_excess_win_rate_20d": 0.6667,
                                "average_net_excess_20d": 2.6,
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_page_is_decision_first_and_keeps_model_layers_separate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            site = Path(temp_dir) / "site"
            output.mkdir()
            self._fixture(output)
            result = web.build_web_report(output_dir=output, site_dir=site)
            page = result.index_path.read_text(encoding="utf-8")

        self.assertEqual(result.report_date, "2026-08-21")
        self.assertIn("v101-canonical-decision-briefing", web.WEB_REPORT_VERSION)
        for token in (
            "TODAY / 今日行动摘要",
            "ACTION BOARD / 当前行动池",
            "当前有 1 个 READY 候选",
            "生产回测校准",
            "参与当前排名",
            "五因子共振回测",
            "不进入排名",
            "4-5/5",
            "回测校准 Δ",
            "交易快报 2026-08-21",
        ):
            self.assertIn(token, page)
        self.assertNotIn("MUST_NOT_LEAK", page)

    def test_action_card_preserves_clickable_detail_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            site = Path(temp_dir) / "site"
            output.mkdir()
            self._fixture(output)
            page = web.build_web_report(output_dir=output, site_dir=site).index_path.read_text(
                encoding="utf-8"
            )

        self.assertIn('class="action-card" data-ticker="000001.SZ"', page)
        self.assertIn("12.20-12.35", page)
        self.assertIn("止损", page)
        self.assertIn("目标", page)
        self.assertIn("中可信度", page)

    def test_archive_and_publication_boundaries_remain_stable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            site = Path(temp_dir) / "site"
            output.mkdir()
            self._fixture(output)
            result = web.build_web_report(output_dir=output, site_dir=site)

            self.assertTrue(result.archive_path.is_file())
            self.assertTrue((site / "reports" / "index.html").is_file())
            self.assertIn("历史报告", result.index_path.read_text(encoding="utf-8"))

        expected = "https://javabugmaker.github.io/123/"
        self.assertEqual(
            web.github_pages_url_from_remote("git@github.com:javabugmaker/123.git"),
            expected,
        )

    def test_noncanonical_staging_never_attempts_publication(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(
            web, "build_and_publish_web_report"
        ) as publish:
            result = web.maybe_publish_canonical_report(
                Path(temp_dir), reason="staging-test"
            )
        self.assertIsNone(result)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
