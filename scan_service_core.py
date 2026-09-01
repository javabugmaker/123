"""Shared application service for CLI and GUI scan execution.

The service owns universe preparation, optional fundamental refresh, scan
execution and report export. UI/CLI layers only build a request and present the
result, which prevents their execution paths from drifting apart.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config import FUNDAMENTAL_REFRESH_FORCE, TOP_N_PARQUET, TOP_N_REPORT
from downloader import TickerInfo, build_ticker_universe, is_etf_ticker, normalize_ticker
from fundamental_data import (
    FundamentalRefreshCancelled,
    fundamental_data_path,
    refresh_fundamental_data,
)
from report import export_all
from scanner import ScanCancelled, ScanProgressCallback, ScanReport, run_scan


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
    prepare_seconds: float = 0.0
    fundamentals_seconds: float = 0.0
    scan_seconds: float = 0.0
    export_seconds: float = 0.0
    elapsed_seconds: float = 0.0


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
    cancel_event: threading.Event | None = None,
) -> None:
    if not stock_universe:
        return
    existing_path = fundamental_path_fn()
    explicit_refresh = bool(force or FUNDAMENTAL_REFRESH_FORCE)
    if existing_path is None and not explicit_refresh:
        logger.info(
            "AKShare 财报缓存尚未初始化；普通扫描不主动联网。"
            "需要基本面时请勾选/使用 --refresh-fundamentals。"
        )
        return
    try:
        refresh_kwargs = {
            "force": explicit_refresh,
            "industry_by_ticker": _fundamental_industry_map(stock_universe),
        }
        if cancel_event is not None:
            refresh_kwargs["cancel_event"] = cancel_event
        fundamental_path = refresh_fundamentals_fn(
            [ticker.ticker for ticker in stock_universe],
            **refresh_kwargs,
        )
    except FundamentalRefreshCancelled as exc:
        raise ScanCancelled("扫描已取消") from exc
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("基本面刷新失败，继续使用现有数据：%s", exc)
    else:
        logger.info("财报数据路径: %s", fundamental_path_fn() or fundamental_path)


def _emit_progress(
    callback: ScanProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    try:
        callback(stage, int(current), int(total), str(message))
    except Exception:
        logging.getLogger("institution_scanner").debug(
            "Scan service progress callback failed.", exc_info=True
        )


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
    execution_started = time.perf_counter()
    prepare_started = time.perf_counter()
    _emit_progress(progress_callback, "prepare", 0, 0, "正在准备股票池")
    stocks, etfs = prepare_universe(
        request,
        build_universe_fn=build_universe_fn,
        logger=log,
    )
    _emit_progress(
        progress_callback,
        "prepare",
        0,
        0,
        f"股票池准备完成：股票 {len(stocks)} · ETF {len(etfs)}；正在检查基本面",
    )
    prepare_seconds = time.perf_counter() - prepare_started
    fundamentals_started = time.perf_counter()
    if refresh_policy_fn is not None:
        refresh_policy_fn(stocks, request.refresh_fundamentals, log)
    else:
        refresh_fundamentals_if_needed(
            stocks,
            request.refresh_fundamentals,
            log,
            fundamental_path_fn=fundamental_path_fn,
            refresh_fundamentals_fn=refresh_fundamentals_fn,
            cancel_event=cancel_event,
        )
    fundamentals_seconds = time.perf_counter() - fundamentals_started
    scan_started = time.perf_counter()
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
    scan_seconds = time.perf_counter() - scan_started
    export_started = time.perf_counter()
    _emit_progress(
        progress_callback, "export", 0, len(report.results), "正在写入 CSV / Parquet 结果"
    )
    top_csv, top_parquet, full_csv, full_parquet = export_all_fn(
        report.results,
        top_n_csv=request.top_n_csv,
        top_n_parquet=request.top_n_parquet,
        data_source=request.data_source,
    )
    _emit_progress(
        progress_callback,
        "export",
        len(report.results),
        len(report.results),
        "结果文件写入完成",
    )
    export_seconds = time.perf_counter() - export_started
    elapsed_seconds = time.perf_counter() - execution_started
    return ScanExecutionResult(
        report=report,
        top_csv=top_csv,
        top_parquet=top_parquet,
        full_csv=full_csv,
        full_parquet=full_parquet,
        stock_count=len(stocks),
        etf_count=len(etfs),
        prepare_seconds=prepare_seconds,
        fundamentals_seconds=fundamentals_seconds,
        scan_seconds=scan_seconds,
        export_seconds=export_seconds,
        elapsed_seconds=elapsed_seconds,
    )
