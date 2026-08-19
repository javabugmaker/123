from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import report


def _transaction_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    destination = root / "output"
    transaction = destination / ".publication_txn" / "test-run"
    stage = transaction / "stage"
    backup = transaction / "backup"
    destination.mkdir()
    stage.mkdir(parents=True)
    return destination, transaction, stage, backup


class ReportTransactionIntegrityTests(unittest.TestCase):
    def test_successful_stage_commit_replaces_complete_file_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination, _transaction, stage, backup = _transaction_paths(Path(temp_dir))
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
            destination, _transaction, stage, backup = _transaction_paths(Path(temp_dir))
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

    def test_next_run_recovers_hard_crash_during_commit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            transaction = destination / ".publication_txn" / "dead-run"
            backup = transaction / "backup"
            backup.mkdir(parents=True)
            destination.mkdir(exist_ok=True)

            (destination / "AllResults.csv").write_text("new-all", encoding="utf-8")
            (destination / "NewCandidate.csv").write_text("new-only", encoding="utf-8")
            (backup / "AllResults.csv").write_text("old-all", encoding="utf-8")
            report._write_transaction_state(
                transaction,
                {
                    "version": 2,
                    "status": "COMMITTING",
                    "entries": [
                        {"path": "AllResults.csv", "existed": True},
                        {"path": "NewCandidate.csv", "existed": False},
                    ],
                },
            )

            recovered = report.recover_publication_transactions(destination)

            self.assertEqual(recovered, 1)
            self.assertEqual(
                (destination / "AllResults.csv").read_text(encoding="utf-8"),
                "old-all",
            )
            self.assertFalse((destination / "NewCandidate.csv").exists())
            self.assertFalse(transaction.exists())

    def test_recovery_failure_keeps_backups_for_a_second_retry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            transaction = destination / ".publication_txn" / "retry-run"
            backup = transaction / "backup"
            backup.mkdir(parents=True)
            destination.mkdir(exist_ok=True)

            all_target = destination / "AllResults.csv"
            top_target = destination / "Top50.csv"
            all_target.write_text("new-all", encoding="utf-8")
            top_target.write_text("new-top", encoding="utf-8")
            (backup / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (backup / "Top50.csv").write_text("old-top", encoding="utf-8")
            report._write_transaction_state(
                transaction,
                {
                    "version": 2,
                    "status": "COMMITTING",
                    "entries": [
                        {"path": "AllResults.csv", "existed": True},
                        {"path": "Top50.csv", "existed": True},
                    ],
                },
            )
            failed = False

            def flaky_replace(source, target):
                nonlocal failed
                target_path = Path(target)
                if not failed and target_path == all_target:
                    failed = True
                    raise OSError("recovery disk failure")
                os.replace(source, target)

            with self.assertRaisesRegex(RuntimeError, "REPORT_PUBLICATION_RECOVERY_FAILED"):
                report._recover_publication_transaction(
                    transaction,
                    destination,
                    replace_fn=flaky_replace,
                )

            # Recovery may have restored another target already, but the durable
            # source backups must still exist so the journal is retryable.
            self.assertEqual(
                (backup / "AllResults.csv").read_text(encoding="utf-8"),
                "old-all",
            )
            self.assertEqual(
                (backup / "Top50.csv").read_text(encoding="utf-8"),
                "old-top",
            )
            self.assertTrue(transaction.exists())

            recovered = report.recover_publication_transactions(destination)

            self.assertEqual(recovered, 1)
            self.assertEqual(all_target.read_text(encoding="utf-8"), "old-all")
            self.assertEqual(top_target.read_text(encoding="utf-8"), "old-top")
            self.assertFalse(transaction.exists())

    def test_committed_journal_is_cleaned_without_rolling_back_new_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "output"
            transaction = destination / ".backtest_publication_txn" / "done-run"
            transaction.mkdir(parents=True)
            destination.mkdir(exist_ok=True)
            target = destination / "AllResults.csv"
            target.write_text("new-all", encoding="utf-8")
            report._write_transaction_state(
                transaction,
                {
                    "version": 2,
                    "status": "COMMITTED",
                    "entries": [{"path": "AllResults.csv", "existed": True}],
                },
            )

            recovered = report.recover_publication_transactions(destination)

            self.assertEqual(recovered, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "new-all")
            self.assertFalse(transaction.exists())

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
            destination, _transaction, stage, backup = _transaction_paths(Path(temp_dir))
            target = destination / "AllResults.csv"
            target.write_text("published", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "staging directory is empty"):
                report._publish_stage(stage, destination, backup)

            self.assertEqual(target.read_text(), "published")


if __name__ == "__main__":
    unittest.main()
