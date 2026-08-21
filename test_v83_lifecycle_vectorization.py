from __future__ import annotations

import unittest

import pandas as pd

from lifecycle_acceleration_v83 import vectorized_lifecycle_state


class LifecycleVectorizationV83Tests(unittest.TestCase):
    def test_vectorized_state_matches_stable_status_semantics(self) -> None:
        history = pd.DataFrame(
            {
                "TradeDate": ["2026-08-19"] * 5,
                "Ticker": [
                    "000001.SZ",
                    "000002.SZ",
                    "000003.SZ",
                    "000004.SZ",
                    "000005.SZ",
                ],
                "SignalActive": [True, True, False, False, True],
                "SignalDays": [4, 2, 0, 0, 3],
                "SignalStartDate": [
                    "2026-08-16",
                    "2026-08-18",
                    "",
                    "",
                    "2026-08-17",
                ],
                "OpportunityScore": [50.0, 50.0, 40.0, 40.0, 55.0],
            }
        )
        current = pd.DataFrame(
            {
                "Ticker": [
                    "000001.SZ",
                    "000002.SZ",
                    "000003.SZ",
                    "000004.SZ",
                    "000005.SZ",
                ],
                "OpportunityScore": [50.0, 53.0, 45.0, 45.0, 50.0],
            }
        )
        active = pd.Series([True, True, True, False, False])
        trade_dates = pd.Series(["2026-08-20"] * len(current))

        days, starts, statuses, strengths = vectorized_lifecycle_state(
            current, history, active, trade_dates
        )

        self.assertEqual(days.tolist(), [5, 3, 1, 0, 0])
        self.assertEqual(
            starts.tolist(),
            ["2026-08-16", "2026-08-18", "2026-08-20", "", ""],
        )
        self.assertEqual(
            statuses.tolist(),
            ["CONFIRMED", "STRENGTHEN", "NEW", "", "FAILED"],
        )
        self.assertEqual(
            strengths.tolist(),
            ["50|50", "50|53", "40|45", "40|45", "55|50"],
        )

    def test_previous_global_trade_date_semantics_are_preserved(self) -> None:
        # Stable code chooses the latest historical market date strictly before
        # each current DataAsOf, then looks for the same ticker on that date.
        history = pd.DataFrame(
            {
                "TradeDate": ["2026-08-18", "2026-08-19", "2026-08-19"],
                "Ticker": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "SignalActive": [True, True, True],
                "SignalDays": [7, 4, 4],
                "SignalStartDate": ["2026-08-10", "2026-08-16", "2026-08-16"],
                "OpportunityScore": [60.0, 50.0, 50.0],
            }
        )
        current = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "OpportunityScore": [61.0, 51.0],
            }
        )
        active = pd.Series([True, True])
        trade_dates = pd.Series(["2026-08-20", "2026-08-20"])

        days, starts, statuses, _ = vectorized_lifecycle_state(
            current, history, active, trade_dates
        )

        # 000001 has history, but not on the globally previous market date
        # 2026-08-19, so stable semantics treat it as NEW rather than carrying
        # forward 2026-08-18 state.
        self.assertEqual(days.tolist(), [1, 5])
        self.assertEqual(starts.tolist(), ["2026-08-20", "2026-08-16"])
        self.assertEqual(statuses.tolist(), ["NEW", "CONFIRMED"])

    def test_history_strength_uses_only_last_29_observations(self) -> None:
        history = pd.DataFrame(
            {
                "TradeDate": pd.date_range("2026-07-01", periods=35).astype(str),
                "Ticker": ["000001.SZ"] * 35,
                "SignalActive": [True] * 35,
                "SignalDays": list(range(1, 36)),
                "SignalStartDate": ["2026-07-01"] * 35,
                "OpportunityScore": list(range(1, 36)),
            }
        )
        current = pd.DataFrame(
            {"Ticker": ["000001.SZ"], "OpportunityScore": [36.0]}
        )
        active = pd.Series([True])
        trade_dates = pd.Series(["2026-08-20"])

        _, _, _, strengths = vectorized_lifecycle_state(
            current, history, active, trade_dates
        )

        values = strengths.iloc[0].split("|")
        self.assertEqual(len(values), 30)
        self.assertEqual(values[0], "7")
        self.assertEqual(values[-1], "36")


if __name__ == "__main__":
    unittest.main()
