from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scan_service
from publication_guard_v65 import enforce_cache_first_market_contract
from trading_calendar import is_trading_day, latest_completed_trading_day


def _result(asof: date, error: str = ""):
    return SimpleNamespace(data_asof=asof.isoformat(), error=error)


def _previous_trading_day(day: date, steps: int = 1) -> date:
    cursor = day
    remaining = steps
    while remaining:
        cursor -= timedelta(days=1)
        if is_trading_day(cursor):
            remaining -= 1
    return cursor


class CacheFirstPublicationIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = date(2026, 8, 19)
        self.lag1 = _previous_trading_day(self.target, 1)
        self.lag2 = _previous_trading_day(self.target, 2)

    def test_current_cache_passes(self) -> None:
        health = enforce_cache_first_market_contract(
            [_result(self.target) for _ in range(10)],
            expected_date=self.target,
        )
        self.assertEqual(health["status"], "CURRENT")
        self.assertEqual(health["lag_trading_days"], 0)

    def test_coherent_one_trading_day_provider_lag_passes(self) -> None:
        health = enforce_cache_first_market_contract(
            [_result(self.lag1) for _ in range(10)],
            expected_date=self.target,
        )
        self.assertEqual(health["status"], "PROVIDER_LAG")
        self.assertEqual(health["lag_trading_days"], 1)

    def test_coherent_two_day_stale_cache_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "too stale"):
            enforce_cache_first_market_contract(
                [_result(self.lag2) for _ in range(10)],
                expected_date=self.target,
            )

    def test_mixed_cache_dates_fail(self) -> None:
        rows = [_result(self.target) for _ in range(7)] + [
            _result(self.lag1) for _ in range(3)
        ]
        with self.assertRaisesRegex(ValueError, "mixed market dates"):
            enforce_cache_first_market_contract(rows, expected_date=self.target)

    def test_future_date_fails_even_when_minority(self) -> None:
        rows = [_result(self.target) for _ in range(99)] + [
            _result(self.target + timedelta(days=1))
        ]
        with self.assertRaisesRegex(ValueError, "future market date"):
            enforce_cache_first_market_contract(rows, expected_date=self.target)

    def test_scan_service_does_not_call_export_for_stale_cache_first_results(self) -> None:
        target = latest_completed_trading_day()
        stale = _previous_trading_day(target, 2)
        results = [_result(stale) for _ in range(10)]
        # Enrichment contract requires these fields on successful rows.
        for row in results:
            row.technical_institutional_score = 50.0
            row.institutional_score = 50.0
            row.data_source = "tickflow"
            row.ticker = "000001.SZ"

        export = Mock(side_effect=AssertionError("stale run must not publish"))

        def fake_legacy(request, **kwargs):
            kwargs["export_all_fn"](
                results,
                top_n_csv=50,
                top_n_parquet=200,
                data_source="tickflow",
            )
            raise AssertionError("guard should have raised before this point")

        request = scan_service.ScanRequest(cache_first=True)
        with patch.object(scan_service, "_legacy_execute_scan", side_effect=fake_legacy):
            with self.assertRaisesRegex(ValueError, "CACHE_FIRST_MARKET_CONTRACT_FAILED"):
                scan_service.execute_scan(request, export_all_fn=export)

        export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
