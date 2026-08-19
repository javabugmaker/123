from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import main
import publication_guard_v65


def _row(asof: str) -> SimpleNamespace:
    return SimpleNamespace(
        ticker="000001.SZ",
        error="",
        technical_institutional_score=50.0,
        institutional_score=50.0,
        data_source="tickflow",
        data_asof=asof,
    )


class CachedReportIntegrityTests(unittest.TestCase):
    def test_current_cached_report_is_allowed(self) -> None:
        rows = [_row("2026-08-19")]
        with patch.object(main, "_legacy_report_enrich") as legacy, patch.object(
            publication_guard_v65,
            "latest_completed_trading_day",
            return_value=date(2026, 8, 19),
        ):
            main.enrich_results(rows, "tickflow")
        legacy.assert_called_once()

    def test_two_trading_day_stale_report_fails_before_export_phase(self) -> None:
        rows = [_row("2026-08-17")]
        with patch.object(main, "_legacy_report_enrich") as legacy, patch.object(
            publication_guard_v65,
            "latest_completed_trading_day",
            return_value=date(2026, 8, 19),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "CACHE_FIRST_MARKET_CONTRACT_FAILED",
            ):
                main.enrich_results(rows, "tickflow")
        legacy.assert_called_once()

    def test_mixed_cached_report_dates_fail_closed(self) -> None:
        rows = [_row("2026-08-19") for _ in range(7)] + [
            _row("2026-08-18") for _ in range(3)
        ]
        with patch.object(main, "_legacy_report_enrich"), patch.object(
            publication_guard_v65,
            "latest_completed_trading_day",
            return_value=date(2026, 8, 19),
        ):
            with self.assertRaisesRegex(ValueError, "mixed market dates"):
                main.enrich_results(rows, "tickflow")


if __name__ == "__main__":
    unittest.main()
