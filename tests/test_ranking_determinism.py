from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from institution_scanner.ranking_determinism import _stable_ordinal_rank
from institution_scanner.report_determinism import install as install_report_determinism


def _rank_map(frame: pd.DataFrame) -> dict[str, int]:
    ranks = _stable_ordinal_rank(
        frame["score"],
        frame["asset"],
        frame["ticker"],
        bucket=frame["bucket"],
    )
    return {ticker: int(rank) for ticker, rank in zip(frame["ticker"], ranks)}


def test_exact_ties_are_invariant_to_input_order() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["B.ST", "A.ST", "C.ST", "D.ETF"],
            "asset": ["STOCK", "STOCK", "STOCK", "ETF"],
            "bucket": [0, 0, 0, 0],
            "score": [50.0, 50.0, 49.0, 50.0],
        }
    )
    shuffled = frame.sample(frac=1.0, random_state=7).reset_index(drop=True)

    assert _rank_map(frame) == _rank_map(shuffled)
    assert _rank_map(frame)["A.ST"] == 1
    assert _rank_map(frame)["B.ST"] == 2


def test_report_candidate_ties_use_ticker_not_arrival_order() -> None:
    core = SimpleNamespace()
    install_report_determinism(core)

    def row(ticker: str) -> SimpleNamespace:
        return SimpleNamespace(
            ticker=ticker,
            error="",
            ranking_eligibility="观察",
            ranking_score=42.0,
            institutional_score=42.0,
            final_score=42.0,
            score=SimpleNamespace(total=42.0),
            filter_details={"signal_count": 4},
        )

    first = [row("B.ST"), row("A.ST"), row("C.ST")]
    second = list(reversed(first))

    assert [item.ticker for item in core._rankable_results(first)] == [
        "A.ST",
        "B.ST",
        "C.ST",
    ]
    assert [item.ticker for item in core._rankable_results(second)] == [
        "A.ST",
        "B.ST",
        "C.ST",
    ]
