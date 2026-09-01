from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import pytest

from downloader import TickerInfo
from fundamental_data import FundamentalRefreshCancelled
from scan_service_core import refresh_fundamentals_if_needed
from scanner import ScanCancelled


def test_scan_service_forwards_fundamental_cancel_event() -> None:
    cancel_event = threading.Event()
    received: dict[str, Any] = {}

    def cancel_refresh(tickers: list[str], **kwargs: Any) -> Path:
        received["tickers"] = tickers
        received.update(kwargs)
        raise FundamentalRefreshCancelled("cancelled")

    with pytest.raises(ScanCancelled):
        refresh_fundamentals_if_needed(
            [TickerInfo(ticker="600000.SH", industry="银行")],
            True,
            logging.getLogger("test.scan_service.cancel"),
            fundamental_path_fn=lambda: None,
            refresh_fundamentals_fn=cancel_refresh,
            cancel_event=cancel_event,
        )

    assert received["tickers"] == ["600000.SH"]
    assert received["cancel_event"] is cancel_event
    assert received["industry_by_ticker"] == {"600000.SH": "银行"}
