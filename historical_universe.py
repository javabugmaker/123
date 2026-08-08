from __future__ import annotations

import re
from pathlib import Path

from config import CACHE_DIR
from downloader import normalize_ticker

_CACHE_DIR = CACHE_DIR / "v3-tickflow-forward"
_BENCHMARKS = {"000300.SH", "000905.SH", "399006.SZ"}
_SECURITY = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def cached_security_tickers(cache_dir: Path | None = None) -> list[str]:
    directory = cache_dir or _CACHE_DIR
    if not directory.exists():
        return []
    result: set[str] = set()
    for path in directory.glob("*.parquet"):
        ticker = normalize_ticker(path.stem)
        if ticker in _BENCHMARKS or not _SECURITY.fullmatch(ticker):
            continue
        result.add(ticker)
    return sorted(result)


def merge_with_cached_universe(
    current_tickers: list[str],
    cache_dir: Path | None = None,
) -> list[str]:
    combined = [normalize_ticker(ticker) for ticker in current_tickers if str(ticker).strip()]
    combined.extend(cached_security_tickers(cache_dir))
    return list(dict.fromkeys(combined))
