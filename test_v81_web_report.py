from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import web_report_v81 as web


class WebReportV81Tests(unittest.TestCase):
    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def test_build_report_uses_public_allowlist_and_escapes_html(self) -> None:
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
                    "EntrySignal": "BUY_NOW",
                    "RankingEligibility": "推荐",
                    "RankingScore": "88.5",
                    "InstitutionalTier": "A级机构启动",
                    "InstitutionalScore": "91.2",
                    "DataAsOf": "2026-08-20",
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
                    "RankingScore": "61.0",
                    "InstitutionalTier": "B级观察",
                    "InstitutionalScore": "70.0",
                    "DataAsOf": "2026-08-20",
                    "SignalStatus": "WATCH",
                    "UserAccountSecret": "MUST_NOT_LEAK",
                },
            ]
            self._write_csv(output / "AllResults.csv", rows)
            self._write_csv(output / "Top50Mixed.csv", rows)
            (output / "DailyRunSummary.json").write_text(
                json.dumps(
                    {
                        "effective_trading_date": "2026-08-20",
                        "elapsed_seconds": 125,
                        "stage_seconds": {"scan": 60, "backtest": 50},
                        "backtest": {"cache_hit_rate": 0.75},
                    }
                ),
                encoding="utf-8",
            )

            result = web.build_web_report(output_dir=output, site_dir=site)
            page = result.index_path.read_text(encoding="utf-8")

        self.assertEqual(result.report_date, "2026-08-20")
        self.assertIn("交易快报 2026-08-20", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("MUST_NOT_LEAK", page)
        self.assertIn("000001.SZ", page)
        self.assertIn("510300.SH", page)

    def test_published_source_prefers_latest_activated_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            run_dir = output / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (output / "LatestRun.json").write_text(
                json.dumps({"run_dir": "runs/run-1"}), encoding="utf-8"
            )
            (run_dir / "AllResults.csv").write_text(
                "Ticker,DataAsOf\n000001.SZ,2026-08-20\n",
                encoding="utf-8-sig",
            )

            resolved = web._published_source_dir(output)

        self.assertEqual(resolved, run_dir)

    def test_noncanonical_staging_never_attempts_publication(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(
            web, "build_and_publish_web_report"
        ) as publish:
            result = web.maybe_publish_canonical_report(
                Path(temp_dir), reason="staging-test"
            )

        self.assertIsNone(result)
        publish.assert_not_called()

    def test_github_pages_url_supports_https_and_ssh(self) -> None:
        expected = "https://javabugmaker.github.io/123/"
        self.assertEqual(
            web.github_pages_url_from_remote(
                "https://github.com/javabugmaker/123.git"
            ),
            expected,
        )
        self.assertEqual(
            web.github_pages_url_from_remote("git@github.com:javabugmaker/123.git"),
            expected,
        )
        self.assertEqual(
            web.github_pages_url_from_remote(
                "ssh://git@github.com/javabugmaker/123.git"
            ),
            expected,
        )
        self.assertEqual(web.github_pages_url_from_remote("https://example.com/a/b"), "")

    def test_archive_page_lists_dated_reports_newest_first(self) -> None:
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
