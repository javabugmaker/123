from __future__ import annotations

"""Shared application service for CLI and GUI scan execution.

The service owns universe preparation, optional fundamental refresh, scan
execution and report export. UI/CLI layers only build a request and present the
result, which prevents their execution paths from drifting apart.
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import FUNDAMENTAL_REFRESH_FORCE, TOP_N_PARQUET, TOP_N_REPORT
from downloader import TickerInfo, build_ticker_universe, is_etf_ticker, normalize_ticker
from fundamental_data import fundamental_data_path, refresh_fundamental_data
from report import export_all
from scanner import ScanProgressCallback, ScanReport, run_scan


@dataclass(frozen=True)
class ScanRequest:
    include_stocks: bool = True
    include_etfs: bool = True
    tickers: tuple[str, ...] = ()
    force_download: bool = False
    resume: bool = True
    data_source: str = "tickflow"
    cache_first: bool = False
    refresh_fundamentals: bool = False
    top_n_csv: int = TOP_N_REPORT
    top_n_parquet: int = TOP_N_PARQUET


@dataclass(frozen=True)
class ScanExecutionResult:
    report: ScanReport
    top_csv: Path
    top_parquet: Path
    full_csv: Path
    full_parquet: Path
    stock_count: int
    etf_count: int


BuildUniverseFn = Callable[..., tuple[list[TickerInfo], list[TickerInfo]]]
RunScanFn = Callable[..., ScanReport]
ExportAllFn = Callable[..., tuple[Path, Path, Path, Path]]
FundamentalPathFn = Callable[[], Path | None]
RefreshFundamentalsFn = Callable[..., Path]
RefreshPolicyFn = Callable[[list[TickerInfo], bool, logging.Logger], None]


def _normalize_symbols(values: tuple[str, ...] | list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            normalize_ticker(value)
            for value in values
            if str(value).strip()
        )
    )


def prepare_universe(
    request: ScanRequest,
    *,
    build_universe_fn: BuildUniverseFn = build_ticker_universe,
    logger: logging.Logger | None = None,
) -> tuple[list[TickerInfo], list[TickerInfo]]:
    log = logger or logging.getLogger("institution_scanner")
    symbols = _normalize_symbols(request.tickers)
    if symbols:
        stocks = [
            TickerInfo(ticker=symbol)
            for symbol in symbols
            if request.include_stocks and not is_etf_ticker(symbol)
        ]
        etfs = [
            TickerInfo(ticker=symbol, is_etf=True, asset_type="etf")
            for symbol in symbols
            if request.include_etfs and is_etf_ticker(symbol)
        ]
        log.info(
            "Scanning %d specified tickers: %s",
            len(stocks) + len(etfs),
            ", ".join(item.ticker for item in [*stocks, *etfs]),
        )
        return stocks, etfs

    log.info(
        "Building ticker universe (stocks=%s, ETFs=%s)...",
        request.include_stocks,
        request.include_etfs,
    )
    stocks, etfs = build_universe_fn(
        include_stocks=request.include_stocks,
        include_etfs=request.include_etfs,
    )
    log.info(
        "Universe: %d stocks, %d ETFs — %d total.",
        len(stocks),
        len(etfs),
        len(stocks) + len(etfs),
    )
    return list(stocks), list(etfs)


def _fundamental_industry_map(tickers: list[TickerInfo]) -> dict[str, str]:
    return {
        normalize_ticker(item.ticker): str(item.industry).strip()
        for item in tickers
        if item.ticker and str(item.industry).strip()
    }


def refresh_fundamentals_if_needed(
    stock_universe: list[TickerInfo],
    force: bool,
    logger: logging.Logger,
    *,
    fundamental_path_fn: FundamentalPathFn = fundamental_data_path,
    refresh_fundamentals_fn: RefreshFundamentalsFn = refresh_fundamental_data,
) -> None:
    if not stock_universe:
        return
    existing_path = fundamental_path_fn()
    explicit_refresh = bool(force or FUNDAMENTAL_REFRESH_FORCE)
    if existing_path is None and not explicit_refresh:
        logger.info(
            "AkShare 基本面缓存尚未初始化；普通扫描不主动联网。"
            "需要基本面时请勾选/使用 --refresh-fundamentals。"
        )
        return
    try:
        fundamental_path = refresh_fundamentals_fn(
            [ticker.ticker for ticker in stock_universe],
            force=explicit_refresh,
            industry_by_ticker=_fundamental_industry_map(stock_universe),
        )
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("基本面刷新失败，继续使用现有数据：%s", exc)
    else:
        logger.info("基本面数据路径: %s", fundamental_path_fn() or fundamental_path)


def execute_scan(
    request: ScanRequest,
    *,
    progress_callback: ScanProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    logger: logging.Logger | None = None,
    build_universe_fn: BuildUniverseFn = build_ticker_universe,
    run_scan_fn: RunScanFn = run_scan,
    export_all_fn: ExportAllFn = export_all,
    fundamental_path_fn: FundamentalPathFn = fundamental_data_path,
    refresh_fundamentals_fn: RefreshFundamentalsFn = refresh_fundamental_data,
    refresh_policy_fn: RefreshPolicyFn | None = None,
) -> ScanExecutionResult:
    """Execute one complete scan through the shared application path."""
    log = logger or logging.getLogger("institution_scanner")
    stocks, etfs = prepare_universe(
        request,
        build_universe_fn=build_universe_fn,
        logger=log,
    )
    if refresh_policy_fn is not None:
        refresh_policy_fn(stocks, request.refresh_fundamentals, log)
    else:
        refresh_fundamentals_if_needed(
            stocks,
            request.refresh_fundamentals,
            log,
            fundamental_path_fn=fundamental_path_fn,
            refresh_fundamentals_fn=refresh_fundamentals_fn,
        )
    report = run_scan_fn(
        stock_universe=stocks,
        etf_universe=etfs,
        force_download=request.force_download,
        resume=bool(request.resume and not request.force_download),
        data_source=request.data_source,
        cache_first=bool(request.cache_first and not request.force_download),
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )
    top_csv, top_parquet, full_csv, full_parquet = export_all_fn(
        report.results,
        top_n_csv=request.top_n_csv,
        top_n_parquet=request.top_n_parquet,
        data_source=request.data_source,
    )
    return ScanExecutionResult(
        report=report,
        top_csv=top_csv,
        top_parquet=top_parquet,
        full_csv=full_csv,
        full_parquet=full_parquet,
        stock_count=len(stocks),
        etf_count=len(etfs),
    )
