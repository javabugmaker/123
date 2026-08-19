from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import analytics
import backtest_alignment
import report


class BacktestPublicationIntegrityTests(unittest.TestCase):
    def test_spawn_initializer_is_public_facade_safe(self) -> None:
        self.assertIs(
            analytics._init_backtest_worker,
            backtest_alignment.aligned_backtest_worker_initializer,
        )
        self.assertEqual(analytics._backtest_one_ticker_cached.__module__, "analytics")
        self.assertEqual(analytics.apply_backtest_ranking.__module__, "analytics")

    def test_successful_backtest_postprocess_commits_complete_staged_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output"
            destination.mkdir()
            (destination / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (destination / "Top50.csv").write_text("old-top", encoding="utf-8")
            frame = pd.DataFrame({"Ticker": ["000001.SZ"], "Value": [2]})

            def fake_csv(data: pd.DataFrame, path: Path) -> None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(
                    f"csv:{int(data['Value'].iloc[0])}", encoding="utf-8"
                )

            def fake_parquet(data: pd.DataFrame, path: Path) -> None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(
                    f"parquet:{int(data['Value'].iloc[0])}", encoding="utf-8"
                )

            def fake_refresh(
                data: pd.DataFrame,
                top_n_csv: int = 50,
                top_n_parquet: int = 200,
                output_dir: Path | None = None,
                **kwargs: object,
            ):
                del top_n_parquet, kwargs
                assert output_dir is not None
                top = Path(output_dir) / f"Top{top_n_csv}.csv"
                fake_csv(data, top)
                return top, Path(output_dir) / "Top200.parquet", data

            def fake_legacy(summary, top_n: int = 50) -> None:
                del summary
                import report as runtime_report

                runtime_report._atomic_write_csv(
                    frame, destination / "AllResults.csv"
                )
                runtime_report.refresh_candidate_exports(
                    frame,
                    top_n_csv=top_n,
                    output_dir=destination,
                )
                runtime_report._atomic_write_parquet(
                    frame, destination / "AllResults.parquet"
                )

            with patch.object(analytics, "OUTPUT_DIR", destination), patch.object(
                report, "_atomic_write_csv", side_effect=fake_csv
            ), patch.object(
                report, "_atomic_write_parquet", side_effect=fake_parquet
            ), patch.object(
                report, "refresh_candidate_exports", side_effect=fake_refresh
            ), patch.object(
                analytics, "_legacy_apply_backtest_ranking", side_effect=fake_legacy
            ):
                analytics.apply_backtest_ranking(object(), top_n=50)

            self.assertEqual(
                (destination / "AllResults.csv").read_text(encoding="utf-8"),
                "csv:2",
            )
            self.assertEqual(
                (destination / "Top50.csv").read_text(encoding="utf-8"),
                "csv:2",
            )
            self.assertEqual(
                (destination / "AllResults.parquet").read_text(encoding="utf-8"),
                "parquet:2",
            )

    def test_postprocess_failure_leaves_previous_published_set_untouched(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output"
            destination.mkdir()
            all_path = destination / "AllResults.csv"
            top_path = destination / "Top50.csv"
            all_path.write_text("old-all", encoding="utf-8")
            top_path.write_text("old-top", encoding="utf-8")
            frame = pd.DataFrame({"Ticker": ["000001.SZ"], "Value": [3]})

            def fake_csv(data: pd.DataFrame, path: Path) -> None:
                del data
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("new-staged", encoding="utf-8")

            def fake_legacy(summary, top_n: int = 50) -> None:
                del summary, top_n
                import report as runtime_report

                runtime_report._atomic_write_csv(
                    frame, destination / "AllResults.csv"
                )
                raise OSError("simulated backtest postprocess failure")

            with patch.object(analytics, "OUTPUT_DIR", destination), patch.object(
                report, "_atomic_write_csv", side_effect=fake_csv
            ), patch.object(
                analytics, "_legacy_apply_backtest_ranking", side_effect=fake_legacy
            ):
                with self.assertRaisesRegex(
                    OSError, "simulated backtest postprocess failure"
                ):
                    analytics.apply_backtest_ranking(object(), top_n=50)

            self.assertEqual(all_path.read_text(encoding="utf-8"), "old-all")
            self.assertEqual(top_path.read_text(encoding="utf-8"), "old-top")


if __name__ == "__main__":
    unittest.main()
