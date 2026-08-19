from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import report


class ReportTransactionIntegrityTests(unittest.TestCase):
    def test_successful_stage_commit_replaces_complete_file_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            stage = root / "stage"
            backup = root / "backup"
            destination.mkdir()
            stage.mkdir()
            (destination / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (destination / "Top50.csv").write_text("old-top", encoding="utf-8")
            (stage / "AllResults.csv").write_text("new-all", encoding="utf-8")
            (stage / "Top50.csv").write_text("new-top", encoding="utf-8")

            published = report._publish_stage(stage, destination, backup)

            self.assertEqual((destination / "AllResults.csv").read_text(), "new-all")
            self.assertEqual((destination / "Top50.csv").read_text(), "new-top")
            self.assertEqual(
                {path.name for path in published},
                {"AllResults.csv", "Top50.csv"},
            )

    def test_mid_commit_failure_restores_every_previous_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            stage = root / "stage"
            backup = root / "backup"
            destination.mkdir()
            stage.mkdir()
            (destination / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (destination / "Top50.csv").write_text("old-top", encoding="utf-8")
            staged_all = stage / "AllResults.csv"
            staged_top = stage / "Top50.csv"
            staged_all.write_text("new-all", encoding="utf-8")
            staged_top.write_text("new-top", encoding="utf-8")
            failed = False

            def flaky_replace(source, target):
                nonlocal failed
                source_path = Path(source)
                target_path = Path(target)
                if (
                    not failed
                    and source_path == staged_top
                    and target_path == destination / "Top50.csv"
                ):
                    failed = True
                    raise OSError("simulated disk failure")
                os.replace(source_path, target_path)

            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                report._publish_stage(
                    stage,
                    destination,
                    backup,
                    replace_fn=flaky_replace,
                )

            self.assertEqual((destination / "AllResults.csv").read_text(), "old-all")
            self.assertEqual((destination / "Top50.csv").read_text(), "old-top")

    def test_lifecycle_state_is_seeded_into_staging(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            stage = root / "stage"
            destination.mkdir()
            history = destination / "SignalHistory.csv"
            tracking = destination / "SignalTracking.csv"
            history.write_bytes(b"history-state")
            tracking.write_bytes(b"tracking-state")
            lifecycle = SimpleNamespace(HISTORY_FILE=history, TRACKING_FILE=tracking)

            staged_history, staged_tracking = report._seed_lifecycle_state(
                destination,
                stage,
                lifecycle,
            )

            self.assertEqual(staged_history.read_bytes(), b"history-state")
            self.assertEqual(staged_tracking.read_bytes(), b"tracking-state")
            self.assertEqual(staged_history.parent, stage)
            self.assertEqual(staged_tracking.parent, stage)

    def test_empty_stage_never_touches_published_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            stage = root / "stage"
            backup = root / "backup"
            destination.mkdir()
            stage.mkdir()
            target = destination / "AllResults.csv"
            target.write_text("published", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "staging directory is empty"):
                report._publish_stage(stage, destination, backup)

            self.assertEqual(target.read_text(), "published")


if __name__ == "__main__":
    unittest.main()
