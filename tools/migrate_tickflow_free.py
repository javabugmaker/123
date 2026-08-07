from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOWNLOADER = '"""\ndownloader.py — TickFlow Free market-data layer for InstitutionScanner.\n\nTickFlow is the only OHLCV/universe provider.  AkShare is intentionally kept\nout of this module and is used only by fundamental_data.py for low-frequency\nfundamental refreshes.\n"""\n\nfrom __future__ import annotations\n\nimport json\nimport logging\nimport os\nimport re\nimport sys\nimport tempfile\nfrom dataclasses import dataclass\nfrom datetime import date, datetime, timedelta\nfrom pathlib import Path\nfrom typing import Any, cast\nfrom zoneinfo import ZoneInfo\n\nimport numpy as np\nimport pandas as pd\n\ntry:\n    from tickflow import TickFlow\nexcept ImportError:  # pragma: no cover - handled with a clear runtime error\n    TickFlow = None  # type: ignore[assignment]\n\nfrom config import (\n    CACHE_DIR,\n    EXCLUDED_SECURITY_KEYWORDS,\n    HISTORY_YEARS,\n    LOG_DIR,\n    TICKFLOW_ADJUST,\n    TICKFLOW_BATCH_SIZE,\n    TICKFLOW_MAX_WORKERS,\n    TICKFLOW_UNIVERSE_CACHE_TTL_HOURS,\n    setup_logging,\n)\n\nlogger = setup_logging(\n    "institution_scanner.downloader",\n    level=logging.DEBUG,\n    log_to_file=True,\n    log_dir=LOG_DIR,\n)\n\n_DATA_SOURCE = "tickflow"\n_DATA_SOURCE_LABEL = "TickFlow Free"\n_PRICE_CACHE_SCHEMA_VERSION = "v3-tickflow-forward"\n_PRICE_CACHE_DIR = CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION\n_UNIVERSE_CACHE_PATH = CACHE_DIR / "_tickflow_universe.json"\n_TICKFLOW_CLIENT: Any | None = None\n_INSTRUMENT_META: dict[str, dict[str, Any]] = {}\n\n\n\n_AKSHARE_MANAGED_PROXY_ENV: dict[str, str] = {}\n\n\ndef _proxy_url(value: str) -> str:\n    value = str(value or "").strip()\n    if not value:\n        return ""\n    return value if "://" in value else f"http://{value}"\n\n\ndef _windows_system_proxy() -> dict[str, str]:\n    """Read WinINET proxy (used by Clash system-proxy mode) for AkShare only."""\n    if sys.platform != "win32":\n        return {}\n    try:\n        import winreg\n\n        with winreg.OpenKey(\n            winreg.HKEY_CURRENT_USER,\n            r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings",\n        ) as key:\n            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)\n            if not enabled:\n                return {}\n            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()\n    except (OSError, ValueError, TypeError):\n        return {}\n    if not raw:\n        return {}\n    if ";" not in raw and "=" not in raw:\n        proxy = _proxy_url(raw)\n        return {"http": proxy, "https": proxy} if proxy else {}\n    result: dict[str, str] = {}\n    for item in raw.split(";"):\n        if "=" not in item:\n            continue\n        protocol, server = item.split("=", 1)\n        protocol = protocol.strip().lower()\n        if protocol in {"http", "https"}:\n            proxy = _proxy_url(server)\n            if proxy:\n                result[protocol] = proxy\n    if "http" in result and "https" not in result:\n        result["https"] = result["http"]\n    if "https" in result and "http" not in result:\n        result["http"] = result["https"]\n    return result\n\n\ndef configure_akshare_proxy_from_system() -> dict[str, str]:\n    """Mirror Clash/Windows system proxy into Requests env for AkShare fundamentals."""\n    system_proxy = _windows_system_proxy()\n    explicit = {\n        "http": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "",\n        "https": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "",\n    }\n    if not system_proxy:\n        for key, managed in list(_AKSHARE_MANAGED_PROXY_ENV.items()):\n            if os.environ.get(key) == managed:\n                os.environ.pop(key, None)\n            _AKSHARE_MANAGED_PROXY_ENV.pop(key, None)\n        return {k: v for k, v in explicit.items() if v}\n\n    resolved = {\n        "http": _proxy_url(explicit["http"] or system_proxy.get("http", "")),\n        "https": _proxy_url(\n            explicit["https"] or system_proxy.get("https", system_proxy.get("http", ""))\n        ),\n    }\n    for protocol, value in resolved.items():\n        if not value:\n            continue\n        upper = f"{protocol.upper()}_PROXY"\n        lower = f"{protocol}_proxy"\n        if not os.environ.get(upper) and not os.environ.get(lower):\n            os.environ[upper] = value\n            os.environ[lower] = value\n            _AKSHARE_MANAGED_PROXY_ENV[upper] = value\n            _AKSHARE_MANAGED_PROXY_ENV[lower] = value\n    return {k: v for k, v in resolved.items() if v}\n\n\nclass DownloadError(RuntimeError):\n    pass\n\n\n@dataclass\nclass TickerInfo:\n    ticker: str\n    name: str = ""\n    exchange: str = ""\n    sector: str = ""\n    industry: str = ""\n    is_etf: bool = False\n    asset_type: str = "stock"\n    market_cap: float | None = None\n    total_shares: float | None = None\n    float_shares: float | None = None\n\n\ndef normalize_data_source(source: str | None = None) -> str:\n    value = str(source or _DATA_SOURCE).strip().lower()\n    if value in {"", "tickflow", "tickflow-free", "free"}:\n        return _DATA_SOURCE\n    raise ValueError(f"已移除行情数据源 {source!r}；当前仅支持 TickFlow Free")\n\n\ndef get_data_source_label(source: str | None = None) -> str:\n    normalize_data_source(source)\n    return _DATA_SOURCE_LABEL\n\n\ndef normalize_ticker(ticker: str) -> str:\n    normalized = str(ticker).strip().upper()\n    if "." in normalized:\n        return normalized\n    if len(normalized) != 6 or not normalized.isdigit():\n        return normalized\n    if normalized.startswith(("4", "8", "92")):\n        suffix = "BJ"\n    elif normalized.startswith(("5", "6")):\n        suffix = "SH"\n    else:\n        suffix = "SZ"\n    return f"{normalized}.{suffix}"\n\n\ndef is_etf_ticker(ticker: str) -> bool:\n    code = normalize_ticker(ticker).split(".", 1)[0]\n    return code.startswith(("15", "16", "50", "51", "56", "58"))\n\n\ndef _is_excluded_security_name(name: str) -> bool:\n    normalized = re.sub(r"\\s+", "", str(name or "")).upper()\n    if "ST" in normalized or "退" in normalized:\n        return True\n    return any(keyword.upper() in normalized for keyword in EXCLUDED_SECURITY_KEYWORDS)\n\n\ndef _tickflow() -> Any:\n    global _TICKFLOW_CLIENT\n    if TickFlow is None:\n        raise DownloadError(\n            \'未安装 TickFlow SDK；请运行 pip install "tickflow[all]==0.1.24"\'\n        )\n    if _TICKFLOW_CLIENT is None:\n        try:\n            _TICKFLOW_CLIENT = TickFlow.free()\n        except Exception as exc:\n            raise DownloadError(f"TickFlow Free 初始化失败: {exc}") from exc\n    return _TICKFLOW_CLIENT\n\n\ndef close_tickflow_client() -> None:\n    global _TICKFLOW_CLIENT\n    client = _TICKFLOW_CLIENT\n    _TICKFLOW_CLIENT = None\n    if client is not None and hasattr(client, "close"):\n        try:\n            client.close()\n        except Exception:\n            logger.debug("TickFlow client close failed", exc_info=True)\n\n\ndef _safe_cache_stem(ticker: str) -> str:\n    value = normalize_ticker(ticker)\n    safe = re.sub(r\'[<>:"/\\\\|?*\\x00-\\x1f]\', "_", value).rstrip(" .")\n    return safe or "ticker"\n\n\ndef _cache_path(ticker: str, source: str | None = None) -> Path:\n    normalize_data_source(source)\n    return _PRICE_CACHE_DIR / f"{_safe_cache_stem(ticker)}.parquet"\n\n\ndef _legacy_cache_path(ticker: str, source: str | None = None) -> Path:\n    normalize_data_source(source)\n    return _PRICE_CACHE_DIR / f"{_safe_cache_stem(ticker)}.csv"\n\n\ndef _validate_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:\n    required = ["Open", "High", "Low", "Close", "Volume"]\n    if df is None or df.empty or any(column not in df.columns for column in required):\n        return None\n    cleaned = df.copy()\n    cleaned.index = pd.to_datetime(cleaned.index, errors="coerce")\n    cleaned = cleaned.loc[cleaned.index.notna()]\n    for column in required:\n        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")\n    if "Amount" in cleaned.columns:\n        cleaned["Amount"] = pd.to_numeric(cleaned["Amount"], errors="coerce")\n    cleaned = cleaned.sort_index()\n    cleaned = cleaned.loc[~cleaned.index.duplicated(keep="last")]\n    valid = (\n        cleaned[required].notna().all(axis=1)\n        & np.isfinite(cleaned[required]).all(axis=1)\n        & (cleaned["Open"] > 0)\n        & (cleaned["High"] > 0)\n        & (cleaned["Low"] > 0)\n        & (cleaned["Close"] > 0)\n        & (cleaned["Volume"] >= 0)\n        & (cleaned["High"] >= cleaned[["Open", "Close"]].max(axis=1))\n        & (cleaned["Low"] <= cleaned[["Open", "Close"]].min(axis=1))\n    )\n    cleaned = cleaned.loc[valid]\n    if cleaned.empty:\n        return None\n    keep = required + (["Amount"] if "Amount" in cleaned.columns else [])\n    return cast(pd.DataFrame, cleaned.loc[:, keep])\n\n\ndef _normalize_tickflow_frame(frame: Any) -> pd.DataFrame | None:\n    if not isinstance(frame, pd.DataFrame) or frame.empty:\n        return None\n    renamed = frame.rename(\n        columns={\n            "trade_date": "Date",\n            "date": "Date",\n            "open": "Open",\n            "high": "High",\n            "low": "Low",\n            "close": "Close",\n            "volume": "Volume",\n            "amount": "Amount",\n        }\n    ).copy()\n    if "Date" in renamed.columns:\n        renamed["Date"] = pd.to_datetime(renamed["Date"], errors="coerce")\n        renamed = renamed.set_index("Date")\n    elif "trade_time" in frame.columns:\n        renamed.index = pd.to_datetime(frame["trade_time"], errors="coerce")\n    return _validate_ohlcv(renamed)\n\n\ndef _load_cache(ticker: str, source: str | None = None) -> pd.DataFrame | None:\n    for path, reader in (\n        (_cache_path(ticker, source), pd.read_parquet),\n        (\n            _legacy_cache_path(ticker, source),\n            lambda p: pd.read_csv(p, index_col=0, parse_dates=True),\n        ),\n    ):\n        if not path.exists():\n            continue\n        try:\n            return _validate_ohlcv(reader(path))\n        except (OSError, ValueError, ImportError, pd.errors.ParserError):\n            logger.warning("行情缓存损坏，忽略: %s", path)\n    return None\n\n\ndef _save_cache(ticker: str, df: pd.DataFrame, source: str | None = None) -> None:\n    validated = _validate_ohlcv(df)\n    if validated is None:\n        return\n    path = _cache_path(ticker, source)\n    path.parent.mkdir(parents=True, exist_ok=True)\n    with tempfile.NamedTemporaryFile(suffix=".parquet", dir=path.parent, delete=False) as fh:\n        temporary = Path(fh.name)\n    try:\n        validated.to_parquet(temporary)\n        temporary.replace(path)\n    finally:\n        temporary.unlink(missing_ok=True)\n\n\ndef _latest_completed_trading_day(now: datetime | None = None) -> date:\n    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))\n    candidate = current.date()\n    if current.weekday() < 5 and current.hour * 60 + current.minute >= 15 * 60:\n        return candidate\n    candidate -= timedelta(days=1)\n    while candidate.weekday() >= 5:\n        candidate -= timedelta(days=1)\n    return candidate\n\n\ndef _cache_has_completed_daily_bar(\n    df: pd.DataFrame | None, now: datetime | None = None\n) -> bool:\n    if df is None or df.empty:\n        return False\n    index = pd.DatetimeIndex(df.index).dropna()\n    if index.empty:\n        return False\n    latest = pd.Timestamp(index.max())\n    if latest.tzinfo is not None:\n        latest = latest.tz_localize(None)\n    return latest.date() >= _latest_completed_trading_day(now)\n\n\ndef _is_a_share_market_closed(now: datetime | None = None) -> bool:\n    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))\n    return current.weekday() >= 5 or current.hour * 60 + current.minute >= 15 * 60\n\n\ndef _load_universe_cache() -> dict[str, Any] | None:\n    try:\n        if not _UNIVERSE_CACHE_PATH.exists():\n            return None\n        age = datetime.now().timestamp() - _UNIVERSE_CACHE_PATH.stat().st_mtime\n        if age > TICKFLOW_UNIVERSE_CACHE_TTL_HOURS * 3600:\n            return None\n        payload = json.loads(_UNIVERSE_CACHE_PATH.read_text(encoding="utf-8"))\n        return payload if isinstance(payload, dict) else None\n    except (OSError, UnicodeDecodeError, json.JSONDecodeError):\n        return None\n\n\ndef _save_universe_cache(payload: dict[str, Any]) -> None:\n    _UNIVERSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)\n    temporary = _UNIVERSE_CACHE_PATH.with_name(f".{_UNIVERSE_CACHE_PATH.name}.tmp")\n    try:\n        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")\n        temporary.replace(_UNIVERSE_CACHE_PATH)\n    finally:\n        temporary.unlink(missing_ok=True)\n\n\ndef _instrument_batches(symbols: list[str]) -> list[dict[str, Any]]:\n    client = _tickflow()\n    result: list[dict[str, Any]] = []\n    # TickFlow HTTP docs cap one instrument metadata batch at 1000 symbols.\n    for start in range(0, len(symbols), 1000):\n        chunk = symbols[start : start + 1000]\n        try:\n            rows = client.instruments.batch(symbols=chunk)\n        except TypeError:\n            rows = client.instruments.batch(chunk)\n        except Exception as exc:\n            logger.warning(\n                "TickFlow 标的元数据获取失败 (%d-%d): %s",\n                start + 1,\n                min(start + len(chunk), len(symbols)),\n                exc,\n            )\n            continue\n        if isinstance(rows, list):\n            result.extend(row for row in rows if isinstance(row, dict))\n    return result\n\n\ndef _ticker_info_from_meta(symbol: str, meta: dict[str, Any], is_etf: bool) -> TickerInfo:\n    ext = meta.get("ext") if isinstance(meta.get("ext"), dict) else {}\n    total_shares = _number_or_none(ext.get("total_shares"))\n    float_shares = _number_or_none(ext.get("float_shares"))\n    name = str(meta.get("name") or "")\n    exchange = str(meta.get("exchange") or symbol.rsplit(".", 1)[-1])\n    return TickerInfo(\n        ticker=symbol,\n        name=name,\n        exchange=exchange,\n        is_etf=is_etf,\n        asset_type="etf" if is_etf else "stock",\n        total_shares=total_shares,\n        float_shares=float_shares,\n    )\n\n\ndef _number_or_none(value: Any) -> float | None:\n    try:\n        number = float(value)\n    except (TypeError, ValueError):\n        return None\n    return number if np.isfinite(number) and number > 0 else None\n\n\ndef build_ticker_universe(\n    include_stocks: bool = True,\n    include_etfs: bool = True,\n) -> tuple[list[TickerInfo], list[TickerInfo]]:\n    cached = _load_universe_cache()\n    if cached is None:\n        client = _tickflow()\n        stock_symbols: list[str] = []\n        etf_symbols: list[str] = []\n        if include_stocks:\n            universe = client.universes.get("CN_Equity_A")\n            stock_symbols = [\n                normalize_ticker(symbol)\n                for symbol in (universe.get("symbols") or [])\n                if symbol\n            ]\n        if include_etfs:\n            universe = client.universes.get("CN_ETF")\n            etf_symbols = [\n                normalize_ticker(symbol)\n                for symbol in (universe.get("symbols") or [])\n                if symbol\n            ]\n        all_symbols = list(dict.fromkeys(stock_symbols + etf_symbols))\n        metadata = _instrument_batches(all_symbols)\n        meta_by_symbol = {\n            normalize_ticker(row.get("symbol", "")): row\n            for row in metadata\n            if row.get("symbol")\n        }\n        cached = {\n            "stocks": stock_symbols,\n            "etfs": etf_symbols,\n            "metadata": meta_by_symbol,\n        }\n        _save_universe_cache(cached)\n\n    stock_symbols = [normalize_ticker(s) for s in cached.get("stocks", [])] if include_stocks else []\n    etf_symbols = [normalize_ticker(s) for s in cached.get("etfs", [])] if include_etfs else []\n    metadata = cached.get("metadata", {})\n    if not isinstance(metadata, dict):\n        metadata = {}\n\n    stocks: list[TickerInfo] = []\n    etfs: list[TickerInfo] = []\n    for symbol in stock_symbols:\n        meta = metadata.get(symbol, {}) if isinstance(metadata.get(symbol, {}), dict) else {}\n        _INSTRUMENT_META[symbol] = meta\n        item = _ticker_info_from_meta(symbol, meta, False)\n        if not _is_excluded_security_name(item.name):\n            stocks.append(item)\n    for symbol in etf_symbols:\n        meta = metadata.get(symbol, {}) if isinstance(metadata.get(symbol, {}), dict) else {}\n        _INSTRUMENT_META[symbol] = meta\n        item = _ticker_info_from_meta(symbol, meta, True)\n        if not _is_excluded_security_name(item.name):\n            etfs.append(item)\n\n    stocks.sort(key=lambda item: item.ticker)\n    etfs.sort(key=lambda item: item.ticker)\n    logger.info(\n        "TickFlow universe built: %d stocks, %d ETFs", len(stocks), len(etfs)\n    )\n    return stocks, etfs\n\n\ndef _history_count() -> int:\n    return min(10000, max(320, int(HISTORY_YEARS * 260 + 80)))\n\n\ndef _batch_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:\n    if not symbols:\n        return {}\n    client = _tickflow()\n    try:\n        raw = client.klines.batch(\n            symbols,\n            period="1d",\n            count=_history_count(),\n            adjust=TICKFLOW_ADJUST,\n            as_dataframe=True,\n            show_progress=False,\n            max_workers=TICKFLOW_MAX_WORKERS,\n            batch_size=TICKFLOW_BATCH_SIZE,\n        )\n    except TypeError:\n        # Keep compatibility with older SDKs that may not expose batch_size.\n        raw = client.klines.batch(\n            symbols,\n            period="1d",\n            count=_history_count(),\n            adjust=TICKFLOW_ADJUST,\n            as_dataframe=True,\n            show_progress=False,\n            max_workers=TICKFLOW_MAX_WORKERS,\n        )\n    except Exception as exc:\n        raise DownloadError(f"TickFlow 批量 K 线请求失败: {exc}") from exc\n\n    results: dict[str, pd.DataFrame] = {}\n    if not isinstance(raw, dict):\n        return results\n    for ticker, frame in raw.items():\n        symbol = normalize_ticker(ticker)\n        normalized = _normalize_tickflow_frame(frame)\n        if normalized is not None and not normalized.empty:\n            results[symbol] = normalized\n    return results\n\n\ndef download_ticker(\n    ticker: str,\n    force: bool = False,\n    source: str | None = None,\n    cache_first: bool = False,\n) -> pd.DataFrame | None:\n    normalize_data_source(source)\n    ticker = normalize_ticker(ticker)\n    cached = None if force else _load_cache(ticker)\n    if cached is not None and (cache_first or _cache_has_completed_daily_bar(cached)):\n        return cached\n    try:\n        client = _tickflow()\n        frame = client.klines.get(\n            ticker,\n            period="1d",\n            count=_history_count(),\n            adjust=TICKFLOW_ADJUST,\n            as_dataframe=True,\n        )\n    except Exception as exc:\n        if cached is not None:\n            logger.warning("TickFlow 更新 %s 失败，继续使用缓存: %s", ticker, exc)\n            return cached\n        logger.warning("TickFlow 获取 %s 失败: %s", ticker, exc)\n        return None\n    normalized = _normalize_tickflow_frame(frame)\n    if normalized is not None:\n        _save_cache(ticker, normalized)\n        return normalized\n    return cached\n\n\ndef download_batch(\n    tickers: list[TickerInfo],\n    desc: str = "Downloading",\n    force: bool = False,\n    source: str | None = None,\n    cache_first: bool = False,\n    skip_tickers: set[str] | None = None,\n) -> dict[str, pd.DataFrame]:\n    del desc  # TickFlow SDK handles batching; GUI progress is logged below.\n    normalize_data_source(source)\n    skip = {normalize_ticker(t) for t in (skip_tickers or set())}\n    symbols = list(\n        dict.fromkeys(\n            normalize_ticker(item.ticker)\n            for item in tickers\n            if item.ticker and normalize_ticker(item.ticker) not in skip\n        )\n    )\n    total = len(symbols)\n    results: dict[str, pd.DataFrame] = {}\n    pending: list[str] = []\n\n    for symbol in symbols:\n        cached = None if force else _load_cache(symbol)\n        if cached is not None and (\n            cache_first or _cache_has_completed_daily_bar(cached)\n        ):\n            results[symbol] = cached\n        else:\n            pending.append(symbol)\n\n    logger.info(\n        "DOWNLOAD start: %d tickers via TickFlow Free; %d cache hits, %d need refresh.",\n        total,\n        len(results),\n        len(pending),\n    )\n    logger.info(\n        "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",\n        len(results),\n        total,\n        len(results),\n        0,\n    )\n\n    failed = 0\n    if pending:\n        try:\n            fetched = _batch_fetch(pending)\n        except DownloadError as exc:\n            logger.error("%s", exc)\n            fetched = {}\n        for symbol in pending:\n            frame = fetched.get(symbol)\n            if frame is not None and not frame.empty:\n                _save_cache(symbol, frame)\n                results[symbol] = frame\n            else:\n                stale = _load_cache(symbol)\n                if stale is not None and not force:\n                    results[symbol] = stale\n                    logger.debug("TickFlow 无新数据，沿用 %s 本地缓存", symbol)\n                else:\n                    failed += 1\n\n    logger.info(\n        "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",\n        total,\n        total,\n        len(results),\n        failed,\n    )\n    logger.info(\n        "Download batch complete (TickFlow Free): %d/%d tickers available.",\n        len(results),\n        total,\n    )\n    return results\n\n\ndef get_market_cap(ticker: str) -> float | None:\n    symbol = normalize_ticker(ticker)\n    meta = _INSTRUMENT_META.get(symbol, {})\n    ext = meta.get("ext") if isinstance(meta.get("ext"), dict) else {}\n    shares = _number_or_none(ext.get("total_shares"))\n    if shares is None:\n        return None\n    frame = _load_cache(symbol)\n    if frame is None or frame.empty:\n        return None\n    close = _number_or_none(frame["Close"].iloc[-1])\n    return shares * close if close is not None else None\n\n\ndef _fetch_eastmoney_realtime_price(ticker: str) -> float | None:\n    """Legacy compatibility: TickFlow Free has no realtime quote endpoint."""\n    frame = _load_cache(ticker)\n    return float(frame["Close"].iloc[-1]) if frame is not None and not frame.empty else None\n\n\ndef _fetch_eastmoney_realtime_prices(\n    tickers: list[str] | set[str],\n) -> dict[str, float]:\n    """Legacy compatibility: return latest cached TickFlow daily closes."""\n    result: dict[str, float] = {}\n    for ticker in tickers:\n        value = _fetch_eastmoney_realtime_price(ticker)\n        if value is not None and np.isfinite(value):\n            result[normalize_ticker(ticker)] = float(value)\n    return result\n\n\ndef get_etf_fund_flows(ticker: str) -> float | None:\n    del ticker\n    return None\n'
TICKFLOW_TEST = '\nfrom __future__ import annotations\n\nfrom unittest import TestCase\nfrom unittest.mock import Mock, patch\n\nimport pandas as pd\n\nimport downloader\n\n\nclass TickFlowProviderTests(TestCase):\n    def test_legacy_market_source_names_normalize_to_tickflow(self):\n        for source in ("tickflow", "auto", "akshare", "eastmoney", "sina", "tencent"):\n            self.assertEqual(downloader.normalize_data_source(source), "tickflow")\n\n    def test_tickflow_frame_normalizes_to_project_ohlcv(self):\n        frame = pd.DataFrame({\n            "trade_date": ["2026-08-06", "2026-08-07"],\n            "open": [10.0, 10.2],\n            "high": [10.5, 10.4],\n            "low": [9.9, 10.0],\n            "close": [10.2, 10.3],\n            "volume": [1000, 1200],\n            "amount": [10200, 12360],\n        })\n        result = downloader._normalize_tickflow_frame(frame)\n        self.assertEqual(\n            result.columns.tolist(),\n            ["Open", "High", "Low", "Close", "Volume", "Amount"],\n        )\n        self.assertEqual(str(result.index[-1].date()), "2026-08-07")\n\n    def test_batch_fetch_uses_forward_adjusted_daily_klines(self):\n        client = Mock()\n        client.klines.batch.return_value = {\n            "600000.SH": pd.DataFrame({\n                "trade_date": ["2026-08-07"],\n                "open": [10.0],\n                "high": [10.2],\n                "low": [9.9],\n                "close": [10.1],\n                "volume": [1000],\n                "amount": [10100],\n            })\n        }\n        with patch.object(downloader, "_tickflow", return_value=client):\n            result = downloader._batch_fetch(["600000.SH"])\n        self.assertIn("600000.SH", result)\n        kwargs = client.klines.batch.call_args.kwargs\n        self.assertEqual(kwargs["period"], "1d")\n        self.assertEqual(kwargs["adjust"], "forward")\n        self.assertLessEqual(kwargs["max_workers"], 10)\n\n    def test_download_batch_reuses_fresh_cache_and_batches_missing_symbols(self):\n        fresh = pd.DataFrame(\n            {\n                "Open": [10.0],\n                "High": [10.2],\n                "Low": [9.9],\n                "Close": [10.1],\n                "Volume": [1000],\n            },\n            index=pd.to_datetime(["2026-08-07"]),\n        )\n        fetched = pd.DataFrame(\n            {\n                "Open": [20.0],\n                "High": [20.2],\n                "Low": [19.9],\n                "Close": [20.1],\n                "Volume": [2000],\n            },\n            index=pd.to_datetime(["2026-08-07"]),\n        )\n        tickers = [\n            downloader.TickerInfo("600000.SH"),\n            downloader.TickerInfo("000001.SZ"),\n        ]\n        with (\n            patch.object(\n                downloader,\n                "_load_cache",\n                side_effect=lambda t, source=None: fresh if t == "600000.SH" else None,\n            ),\n            patch.object(\n                downloader, "_cache_has_completed_daily_bar", return_value=True\n            ),\n            patch.object(\n                downloader,\n                "_batch_fetch",\n                return_value={"000001.SZ": fetched},\n            ) as batch,\n            patch.object(downloader, "_save_cache"),\n        ):\n            result = downloader.download_batch(tickers)\n\n        self.assertEqual(set(result), {"600000.SH", "000001.SZ"})\n        batch.assert_called_once_with(["000001.SZ"])\n'


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing replacement target: {label}")
    return text.replace(old, new)


write("downloader.py", DOWNLOADER)

req = read("requirements.txt")
req = re.sub(
    r"# Data download\n.*?(?=\n# Technical indicators)",
    "# Market data / fundamentals\n"
    "# TickFlow Free is the sole OHLCV + universe provider.\n"
    "tickflow[all]==0.1.24\n"
    "# AkShare is retained only for low-frequency fundamental refreshes.\n"
    "akshare==1.16.53\n",
    req,
    flags=re.S,
)
write("requirements.txt", req)

cfg = read("config.py")
anchor = 'HISTORY_YEARS: int = 10  # Years of daily data to pull\n'
if "TICKFLOW_BATCH_SIZE" not in cfg:
    cfg = replace_required(
        cfg,
        anchor,
        anchor
        + '\n# TickFlow Free market-data settings\n'
        + 'TICKFLOW_ADJUST: str = "forward"  # ratio forward-adjusted; suited to returns/backtests\n'
        + 'TICKFLOW_BATCH_SIZE: int = 100\n'
        + 'TICKFLOW_MAX_WORKERS: int = 5\n'
        + 'TICKFLOW_UNIVERSE_CACHE_TTL_HOURS: int = 12\n',
        "config tickflow settings",
    )
cfg = re.sub(
    r'SCORING_VERSION: str = "[^"]+"',
    'SCORING_VERSION: str = "2026-08-08-v12-tickflow-free-akshare-fundamentals"',
    cfg,
)
write("config.py", cfg)

main = read("main.py")
main = re.sub(
    r'DATA_SOURCE_CHOICES = \([^\n]+\)',
    'DATA_SOURCE_CHOICES = ("tickflow",)',
    main,
)
main = main.replace('default="auto"', 'default="tickflow"')
main = main.replace(
    '没有可用行情数据，扫描失败；请检查网络或数据源后重试。',
    '没有可用 TickFlow 行情数据，扫描失败；请检查网络或 TickFlow Free 服务后重试。',
)
write("main.py", main)

scanner = read("scanner.py")
scanner = scanner.replace('data_source: str = "eastmoney"', 'data_source: str = "tickflow"')
scanner = scanner.replace('source: str = "eastmoney"', 'source: str = "tickflow"')
write("scanner.py", scanner)

analytics = read("analytics.py")
analytics = analytics.replace(
    '    _fetch_eastmoney_realtime_price,\n    _fetch_eastmoney_realtime_prices,\n',
    '',
)
pattern = re.compile(
    r'    reported_date = latest_date\n'
    r'    result\.close = float\(enriched\["Close"\]\.iloc\[-1\]\)\n'
    r'    last_business_day = .*?'
    r'\n\n    if reported_date is None:',
    re.S,
)
replacement = (
    '    reported_date = latest_date\n'
    '    # TickFlow Free exposes historical daily bars only; the last daily close\n'
    '    # remains tied to its actual trade_date and is never promoted to today.\n'
    '    result.close = float(enriched["Close"].iloc[-1])\n\n'
    '    if reported_date is None:'
)
analytics, count = pattern.subn(replacement, analytics, count=1)
if count != 1:
    raise RuntimeError("failed to remove realtime-price promotion block")
pattern2 = re.compile(
    r'    realtime_prices: dict\[str, float\] \| None = None\n'
    r'    if _is_a_share_market_closed\(\):\n'
    r'        try:\n'
    r'            realtime_prices = _fetch_eastmoney_realtime_prices\(.*?'
    r'            realtime_prices = \{\}\n',
    re.S,
)
analytics, count = pattern2.subn(
    '    # TickFlow Free has no realtime quote service.\n'
    '    realtime_prices: dict[str, float] | None = None\n',
    analytics,
    count=1,
)
if count != 1:
    raise RuntimeError("failed to remove batch realtime-price block")
write("analytics.py", analytics)

gui_core = read("gui_core.py")
gui_core = re.sub(
    r'DATA_SOURCE_CODES = \{.*?\}\n\nDATA_SOURCE_HINTS = \{.*?\}',
    'DATA_SOURCE_CODES = {"TickFlow Free": "tickflow"}\n\n'
    'DATA_SOURCE_HINTS = {"TickFlow Free": "日K/标的池：TickFlow Free；基本面：AkShare 低频缓存"}',
    gui_core,
    flags=re.S,
)
gui_core = gui_core.replace(
    '    "auto": "自动优选",\n    "akshare": "AkShare",\n',
    '    "tickflow": "TickFlow Free",\n',
)
for line in (
    '    "eastmoney": "东方财富",\n',
    '    "sina": "新浪财经",\n',
    '    "tencent": "腾讯财经",\n',
):
    gui_core = gui_core.replace(line, '')
gui_core = gui_core.replace(
    'self.data_source = tk.StringVar(value="自动优选")',
    'self.data_source = tk.StringVar(value="TickFlow Free")',
)
gui_core = re.sub(
    r'self\.data_source_label = tk\.StringVar\(\n\s*value="[^"]*"\n\s*\)',
    'self.data_source_label = tk.StringVar(\n'
    '            value="行情：TickFlow Free（日K/前复权） · 基本面：AkShare（低频缓存）"\n'
    '        )',
    gui_core,
    count=1,
)
gui_core = gui_core.replace(
    'return DATA_SOURCE_CODES.get(self.data_source.get(), "auto")',
    'return DATA_SOURCE_CODES.get(self.data_source.get(), "tickflow")',
)
write("gui_core.py", gui_core)

gui = read("gui.py")
gui = re.sub(
    r'# Strict source semantics after provider-consistent cache hardening\.\n'
    r'_core\.DATA_SOURCE_HINTS\.update\(\n.*?\n\)\n\n',
    '# Market data is fixed to TickFlow Free; AkShare is fundamentals-only.\n'
    '_core.DATA_SOURCE_HINTS.clear()\n'
    '_core.DATA_SOURCE_HINTS["TickFlow Free"] = "日K/标的池：TickFlow Free；基本面：AkShare 低频缓存"\n\n',
    gui,
    flags=re.S,
)
write("gui.py", gui)

fund = read("fundamental_data.py")
fund = fund.replace(
    "_FUNDAMENTAL_CACHE_MAX_AGE_DAYS = 7",
    "_FUNDAMENTAL_CACHE_MAX_AGE_DAYS = 14",
)
write("fundamental_data.py", fund)

tests = read("test_regressions.py")
remove_names = {
    "test_cache_path_isolated_by_source",
    "test_gui_build_command_supports_akshare",
    "test_gui_build_command_supports_auto_data_source",
    "test_parser_defaults_to_auto_data_source",
}
lines = tests.splitlines(keepends=True)
out: list[str] = []
i = 0
while i < len(lines):
    match = re.match(r'    def (test_[A-Za-z0-9_]+)\(', lines[i])
    if match and match.group(1) in remove_names:
        i += 1
        while i < len(lines) and not re.match(r'    def test_[A-Za-z0-9_]+\(', lines[i]):
            i += 1
        continue
    out.append(lines[i])
    i += 1
write("test_regressions.py", ''.join(out))
write("test_tickflow_provider.py", TICKFLOW_TEST)
