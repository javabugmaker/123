from __future__ import annotations

import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import daily_pipeline
import daily_pipeline_core
import daily_recovery_v74 as recovery


class DailyRecoveryIntegrityTests(unittest.TestCase):
    def test_public_daily_facade_installs_recovery_contract(self) -> None:
        self.assertIs(daily_pipeline_core.run_daily_pipeline, recovery.run_daily_pipeline)
        self.assertIs(daily_pipeline_core._begin_transaction, recovery._begin_transaction)
        self.assertEqual(
            getattr(daily_pipeline, "DAILY_RECOVERY_INTEGRITY_VERSION", ""),
            "2026-08-19-v74-pid-aware-outer-transaction-recovery-v1",
        )

    def test_dead_producer_without_activation_rolls_back_previous_result_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            run_id = "run-dead"
            tx_dir = output / ".daily_transactions" / run_id
            tx_dir.mkdir(parents=True)
            staging = output / ".staging" / run_id
            staging.mkdir(parents=True)
            run_dir = output / "runs" / run_id
            run_dir.mkdir(parents=True)

            # Canonical directory reflects a hard crash after partial publish.
            (output / "AllResults.csv").write_text("new-all", encoding="utf-8")
            (output / "Top50Mixed.csv").write_text("new-only", encoding="utf-8")
            (tx_dir / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (staging / "partial.tmp").write_text("stage", encoding="utf-8")
            (run_dir / "RunManifest.json").write_text("{}", encoding="utf-8")
            daily_pipeline_core._atomic_write_json(
                tx_dir / "state.json",
                {
                    "existing": ["AllResults.csv"],
                    "run_id": run_id,
                    "producer_pid": 424242,
                    "producer_host": socket.gethostname(),
                },
            )
            daily_pipeline_core._atomic_write_json(
                output / "LatestRun.json",
                {"run_id": "previous-run"},
            )

            with patch.object(daily_pipeline_core, "OUTPUT_DIR", output), patch.object(
                recovery, "_pid_alive", return_value=False
            ):
                result = recovery.recover_daily_transactions()

            self.assertEqual(result["rolled_back"], 1)
            self.assertEqual(result["committed"], 0)
            self.assertEqual(
                (output / "AllResults.csv").read_text(encoding="utf-8"),
                "old-all",
            )
            self.assertFalse((output / "Top50Mixed.csv").exists())
            self.assertFalse(tx_dir.exists())
            self.assertFalse(staging.exists())
            self.assertFalse(run_dir.exists())

    def test_dead_producer_after_activation_keeps_new_results_and_archive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            run_id = "run-activated"
            tx_dir = output / ".daily_transactions" / run_id
            tx_dir.mkdir(parents=True)
            staging = output / ".staging" / run_id
            staging.mkdir(parents=True)
            run_dir = output / "runs" / run_id
            run_dir.mkdir(parents=True)

            (output / "AllResults.csv").write_text("new-all", encoding="utf-8")
            (tx_dir / "AllResults.csv").write_text("old-all", encoding="utf-8")
            (run_dir / "RunManifest.json").write_text("{}", encoding="utf-8")
            daily_pipeline_core._atomic_write_json(
                tx_dir / "state.json",
                {
                    "existing": ["AllResults.csv"],
                    "run_id": run_id,
                    "producer_pid": 424242,
                    "producer_host": socket.gethostname(),
                },
            )
            daily_pipeline_core._atomic_write_json(
                output / "LatestRun.json",
                {"run_id": run_id},
            )

            with patch.object(daily_pipeline_core, "OUTPUT_DIR", output), patch.object(
                recovery, "_pid_alive", return_value=False
            ):
                result = recovery.recover_daily_transactions()

            self.assertEqual(result["rolled_back"], 0)
            self.assertEqual(result["committed"], 1)
            self.assertEqual(
                (output / "AllResults.csv").read_text(encoding="utf-8"),
                "new-all",
            )
            self.assertFalse(tx_dir.exists())
            self.assertFalse(staging.exists())
            self.assertTrue(run_dir.exists())

    def test_live_producer_fails_closed_without_touching_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            run_id = "run-live"
            tx_dir = output / ".daily_transactions" / run_id
            tx_dir.mkdir(parents=True)
            target = output / "AllResults.csv"
            target.write_text("current", encoding="utf-8")
            (tx_dir / "AllResults.csv").write_text("backup", encoding="utf-8")
            daily_pipeline_core._atomic_write_json(
                tx_dir / "state.json",
                {
                    "existing": ["AllResults.csv"],
                    "run_id": run_id,
                    "producer_pid": 12345,
                    "producer_host": socket.gethostname(),
                },
            )

            with patch.object(daily_pipeline_core, "OUTPUT_DIR", output), patch.object(
                recovery, "_pid_alive", return_value=True
            ):
                with self.assertRaisesRegex(RuntimeError, "DAILY_ALREADY_RUNNING"):
                    recovery.recover_daily_transactions()

            self.assertEqual(target.read_text(encoding="utf-8"), "current")
            self.assertTrue(tx_dir.exists())

    def test_corrupt_journal_fails_closed_without_guessing_previous_file_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            tx_dir = output / ".daily_transactions" / "run-corrupt"
            tx_dir.mkdir(parents=True)
            target = output / "AllResults.csv"
            target.write_text("must-survive", encoding="utf-8")
            daily_pipeline_core._atomic_write_json(
                tx_dir / "state.json",
                {"producer_pid": 0},
            )

            with patch.object(daily_pipeline_core, "OUTPUT_DIR", output):
                with self.assertRaisesRegex(RuntimeError, "DAILY_RECOVERY_FAILED"):
                    recovery.recover_daily_transactions()

            self.assertEqual(target.read_text(encoding="utf-8"), "must-survive")
            self.assertTrue(tx_dir.exists())

    def test_begin_transaction_preserves_manifest_and_adds_producer_identity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            transaction = output / ".daily_transactions" / "run-new"
            transaction.mkdir(parents=True)
            state = transaction / "state.json"
            daily_pipeline_core._atomic_write_json(
                state,
                {"existing": ["AllResults.csv"]},
            )

            with patch.object(
                recovery,
                "_LEGACY_BEGIN_TRANSACTION",
                return_value=(transaction, {"AllResults.csv"}),
            ):
                tx_dir, existing = recovery._begin_transaction("run-new")

            payload = daily_pipeline_core._read_json(state)
            self.assertEqual(tx_dir, transaction)
            self.assertEqual(existing, {"AllResults.csv"})
            self.assertEqual(payload["run_id"], "run-new")
            self.assertEqual(int(payload["producer_pid"]), recovery.os.getpid())
            self.assertEqual(payload["producer_host"], socket.gethostname())
            self.assertIn("created_at", payload)


if __name__ == "__main__":
    unittest.main()
