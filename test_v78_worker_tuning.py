from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import backtest_worker_tuning_v78 as tuning


class BacktestWorkerTuningTests(unittest.TestCase):
    def runtime(self):
        return SimpleNamespace(
            logical_cpus=12,
            estimated_physical_cores=6,
            backtest_processes=6,
        )

    def test_fast_uses_eight_workers_on_6c12t(self) -> None:
        with patch.object(tuning, "runtime_profile", side_effect=self.runtime), patch.dict(
            tuning.os.environ,
            {"INSTITUTION_SCANNER_BACKTEST_PROCESSES": ""},
        ):
            workers = tuning.adaptive_worker_count(
                6800, None, SimpleNamespace(name="fast")
            )
        self.assertEqual(workers, 8)

    def test_exact_stays_on_six_physical_cores(self) -> None:
        with patch.object(tuning, "runtime_profile", side_effect=self.runtime), patch.dict(
            tuning.os.environ,
            {"INSTITUTION_SCANNER_BACKTEST_PROCESSES": ""},
        ):
            workers = tuning.adaptive_worker_count(
                200, None, SimpleNamespace(name="exact")
            )
        self.assertEqual(workers, 6)

    def test_explicit_requested_worker_count_wins(self) -> None:
        with patch.object(tuning, "runtime_profile", side_effect=self.runtime):
            workers = tuning.adaptive_worker_count(
                6800, 4, SimpleNamespace(name="fast")
            )
        self.assertEqual(workers, 4)

    def test_environment_override_wins(self) -> None:
        with patch.object(tuning, "runtime_profile", side_effect=self.runtime), patch.dict(
            tuning.os.environ,
            {"INSTITUTION_SCANNER_BACKTEST_PROCESSES": "5"},
        ):
            workers = tuning.adaptive_worker_count(
                6800, None, SimpleNamespace(name="fast")
            )
        self.assertEqual(workers, 5)


if __name__ == "__main__":
    unittest.main()
