from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import analytics
import backtest_command_v76 as command_guard
import main
import main_core
import report


class BacktestCommandIntegrityTests(unittest.TestCase):
    def test_public_cli_uses_whole_command_backtest_wrapper(self) -> None:
        self.assertIs(main_core.cmd_backtest, command_guard.cmd_backtest)
        self.assertIs(main.cmd_backtest, command_guard.cmd_backtest)
        self.assertEqual(
            getattr(main, "BACKTEST_COMMAND_INTEGRITY_VERSION", ""),
            "2026-08-19-v76-whole-command-transaction-v1",
        )

    def test_success_publishes_results_calibration_and_summary_together(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            (output / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (output / "AllResults.parquet").write_text("old-parquet", encoding="utf-8")

            original_main = main_core.OUTPUT_DIR
            original_analytics = analytics.OUTPUT_DIR
            original_report = report.OUTPUT_DIR

            def fake_legacy(args) -> int:
                del args
                stage = Path(main_core.OUTPUT_DIR)
                self.assertEqual(stage, Path(analytics.OUTPUT_DIR))
                self.assertEqual(stage, Path(report.OUTPUT_DIR))
                self.assertNotEqual(stage, output)
                (stage / "AllResults.csv").write_text("new-all", encoding="utf-8")
                (stage / "AllResults.parquet").write_text(
                    "new-parquet", encoding="utf-8"
                )
                (stage / "Top50.csv").write_text("new-top", encoding="utf-8")
                (stage / "ScoreCalibration.json").write_text(
                    '{"new":true}', encoding="utf-8"
                )
                (stage / "BacktestSummary.json").write_text(
                    '{"run":"new"}', encoding="utf-8"
                )
                return 0

            with patch.object(main_core, "OUTPUT_DIR", output), patch.object(
                command_guard, "_LEGACY_CMD_BACKTEST", side_effect=fake_legacy
            ):
                code = command_guard.cmd_backtest(SimpleNamespace())
                self.assertEqual(main_core.OUTPUT_DIR, output)
                self.assertEqual(analytics.OUTPUT_DIR, original_analytics)
                self.assertEqual(report.OUTPUT_DIR, original_report)

            self.assertEqual(code, 0)
            self.assertEqual(
                (output / "AllResults.csv").read_text(encoding="utf-8"), "new-all"
            )
            self.assertEqual(
                (output / "AllResults.parquet").read_text(encoding="utf-8"),
                "new-parquet",
            )
            self.assertEqual(
                (output / "Top50.csv").read_text(encoding="utf-8"), "new-top"
            )
            self.assertTrue((output / "ScoreCalibration.json").is_file())
            self.assertTrue((output / "BacktestSummary.json").is_file())
            self.assertEqual(main_core.OUTPUT_DIR, original_main)

    def test_nonzero_backtest_keeps_previous_publication_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            all_path = output / "AllResults.csv"
            all_path.write_text("old-all", encoding="utf-8")

            def fake_legacy(args) -> int:
                del args
                stage = Path(main_core.OUTPUT_DIR)
                (stage / "BacktestSummary.json").write_text(
                    '{"failed":true}', encoding="utf-8"
                )
                (stage / "AllResults.csv").write_text("partial-new", encoding="utf-8")
                return 2

            with patch.object(main_core, "OUTPUT_DIR", output), patch.object(
                command_guard, "_LEGACY_CMD_BACKTEST", side_effect=fake_legacy
            ):
                code = command_guard.cmd_backtest(SimpleNamespace())

            self.assertEqual(code, 2)
            self.assertEqual(all_path.read_text(encoding="utf-8"), "old-all")
            self.assertFalse((output / "BacktestSummary.json").exists())

    def test_exception_keeps_previous_publication_and_restores_output_roots(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            all_path = output / "AllResults.csv"
            all_path.write_text("old-all", encoding="utf-8")
            original_main = main_core.OUTPUT_DIR
            original_analytics = analytics.OUTPUT_DIR
            original_report = report.OUTPUT_DIR

            def fake_legacy(args) -> int:
                del args
                stage = Path(main_core.OUTPUT_DIR)
                (stage / "AllResults.csv").write_text("partial-new", encoding="utf-8")
                raise OSError("simulated backtest failure")

            with patch.object(main_core, "OUTPUT_DIR", output), patch.object(
                command_guard, "_LEGACY_CMD_BACKTEST", side_effect=fake_legacy
            ):
                with self.assertRaisesRegex(OSError, "simulated backtest failure"):
                    command_guard.cmd_backtest(SimpleNamespace())
                self.assertEqual(main_core.OUTPUT_DIR, output)
                self.assertEqual(analytics.OUTPUT_DIR, original_analytics)
                self.assertEqual(report.OUTPUT_DIR, original_report)

            self.assertEqual(all_path.read_text(encoding="utf-8"), "old-all")
            self.assertEqual(main_core.OUTPUT_DIR, original_main)


if __name__ == "__main__":
    unittest.main()
