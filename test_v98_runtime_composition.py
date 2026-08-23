from __future__ import annotations

import unittest

import analytics  # noqa: F401 - installs the canonical runtime
import analytics_core as core
import backtest_acceleration_v77 as backtest_bundle
import backtest_profile_alignment_v95 as profile_alignment
import backtest_worker_tuning_v80 as worker_tuning


class V98RuntimeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        profile_alignment.install()

    def tearDown(self) -> None:
        profile_alignment.install()

    def test_worker_tuning_reinstall_cannot_downgrade_504_profile(self) -> None:
        before = core._resolve_backtest_profile("fast", 6000)
        self.assertEqual(int(before.score_window), 504)

        worker_tuning.install()
        after = core._resolve_backtest_profile("fast", 6000)

        self.assertEqual(int(after.score_window), 504)
        self.assertIs(
            core._resolve_backtest_profile,
            profile_alignment._resolve_backtest_profile,
        )
        self.assertGreaterEqual(int(after.chunk_size), 1)

    def test_full_worker_bundle_reinstall_preserves_504_profile(self) -> None:
        before = core._resolve_backtest_profile("fast", 6000)
        self.assertEqual(int(before.score_window), 504)

        backtest_bundle.install()
        after = core._resolve_backtest_profile("fast", 6000)

        self.assertEqual(int(after.score_window), 504)
        self.assertIs(
            core._resolve_backtest_profile,
            profile_alignment._resolve_backtest_profile,
        )


if __name__ == "__main__":
    unittest.main()
