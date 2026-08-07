from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'tools' else Path.cwd()


def load(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def save(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, got {count}')
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, repl: str, label: str, flags: int = 0) -> str:
    new, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one regex match, got {count}')
    return new


path = 'downloader.py'
text = load(path)
text = replace_once(text, '_LAST_DOWNLOAD_AT = 0.0\n', '_LAST_DOWNLOAD_AT = 0.0\n_PRICE_CACHE_SCHEMA_VERSION = "v2-provider-consistent"\n', 'downloader cache schema')
text = sub_once(text, r'def normalize_ticker\(ticker: str\) -> str:\n(?:    .*\n)+?\n\ndef is_etf_ticker', '''def normalize_ticker(ticker: str) -> str:\n    normalized = str(ticker).strip().upper()\n    if "." in normalized:\n        return normalized\n    if len(normalized) != 6 or not normalized.isdigit():\n        return normalized\n    if normalized.startswith(("4", "8", "92")):\n        suffix = "BJ"\n    elif normalized.startswith(("5", "6")):\n        suffix = "SH"\n    else:\n        suffix = "SZ"\n    return f"{normalized}.{suffix}"\n\n\ndef is_etf_ticker''', 'normalize_ticker', flags=re.MULTILINE)
text = replace_once(text, '''    if source:\n        value = f"{value}__{normalize_data_source(source)}"\n''', '''    if source:\n        normalized_source = normalize_data_source(source)\n        value = f"{value}__{normalized_source}__{_PRICE_CACHE_SCHEMA_VERSION}"\n''', 'cache stem versioning')
text = sub_once(text, r'_DATA_SOURCE_CANDIDATES = \{.*?\n\}', '''_DATA_SOURCE_CANDIDATES = {\n    # auto only uses providers with an explicit forward-adjusted daily-history\n    # path. Sina remains available when selected explicitly, but is excluded\n    # from automatic fallback because this adapter does not expose a stable\n    # qfq contract compatible with the other providers.\n    "auto": ("tencent", "akshare", "eastmoney"),\n    "akshare": ("akshare",),\n    "eastmoney": ("eastmoney",),\n    "sina": ("sina",),\n    "tencent": ("tencent",),\n}''', 'data source candidate policy', flags=re.DOTALL)
text = sub_once(text, r'def _download_single\(\n    ticker: str,\n    source: str = "eastmoney",\n    start_date: datetime \| None = None,\n\) -> pd\.DataFrame \| None:\n.*?\n\ndef download_ticker\(', '''def _download_single_with_source(\n    ticker: str,\n    source: str = "eastmoney",\n    start_date: datetime | None = None,\n) -> tuple[pd.DataFrame | None, str | None]:\n    """Download one coherent history and report the provider that produced it."""\n    ticker = normalize_ticker(ticker)\n    selected = normalize_data_source(source)\n    loaders = {\n        "akshare": _download_from_akshare,\n        "eastmoney": _download_from_eastmoney,\n        "sina": _download_from_sina,\n        "tencent": _download_from_tencent,\n    }\n    for candidate in _DATA_SOURCE_CANDIDATES[selected]:\n        loader = loaders[candidate]\n        try:\n            _wait_for_download_slot()\n            frame = loader(ticker, start_date=start_date)\n        except _DOWNLOAD_ERRORS as exc:\n            logger.debug(\n                "数据源 %s 获取 %s 失败：%s",\n                get_data_source_label(candidate),\n                ticker,\n                exc,\n            )\n            continue\n        if frame is not None and not frame.empty:\n            if selected == "auto":\n                logger.debug(\n                    "自动优选已使用%s获取 %s 的数据。",\n                    get_data_source_label(candidate),\n                    ticker,\n                )\n            return frame, candidate\n    return None, None\n\n\ndef _download_single(\n    ticker: str,\n    source: str = "eastmoney",\n    start_date: datetime | None = None,\n) -> pd.DataFrame | None:\n    frame, _actual_source = _download_single_with_source(\n        ticker, source=source, start_date=start_date\n    )\n    return frame\n\n\ndef _price_source_meta_path(ticker: str, selected_source: str) -> Path:\n    return CACHE_DIR / f"{_safe_cache_stem(ticker, selected_source)}.source.json"\n\n\ndef _save_price_source_meta(\n    ticker: str, selected_source: str, actual_source: str\n) -> None:\n    path = _price_source_meta_path(ticker, selected_source)\n    payload = {\n        "selected_source": normalize_data_source(selected_source),\n        "actual_source": normalize_data_source(actual_source),\n        "cache_schema": _PRICE_CACHE_SCHEMA_VERSION,\n        "updated": datetime.now(timezone.utc).isoformat(),\n    }\n    temporary = path.with_name(f".{path.name}.tmp")\n    try:\n        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")\n        temporary.replace(path)\n    finally:\n        temporary.unlink(missing_ok=True)\n\n\ndef _load_price_source_meta(ticker: str, selected_source: str) -> dict[str, str]:\n    path = _price_source_meta_path(ticker, selected_source)\n    try:\n        payload = json.loads(path.read_text(encoding="utf-8"))\n    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):\n        return {}\n    if payload.get("cache_schema") != _PRICE_CACHE_SCHEMA_VERSION:\n        return {}\n    actual = str(payload.get("actual_source", "")).strip().lower()\n    if actual not in _DATA_SOURCE_LABELS or actual == "auto":\n        return {}\n    return {"actual_source": actual}\n\n\ndef download_ticker(''', 'download source provenance helpers', flags=re.DOTALL)
text = sub_once(text, r'def download_ticker\(\n    ticker: str,\n    force: bool = False,\n    source: str = "eastmoney",\n    cache_first: bool = False,\n\) -> pd\.DataFrame \| None:\n.*?\n\ndef download_batch\(', '''def download_ticker(\n    ticker: str,\n    force: bool = False,\n    source: str = "eastmoney",\n    cache_first: bool = False,\n) -> pd.DataFrame | None:\n    """Return provider-consistent OHLCV history for one ticker.\n\n    Explicit providers never silently fall back. ``auto`` records the actual\n    provider used for the cache and keeps incremental updates on that same\n    provider. If that provider becomes unavailable, the cache is rebuilt in\n    full from one alternative provider instead of concatenating incompatible\n    adjustment histories.\n    """\n    ticker = normalize_ticker(ticker)\n    selected = normalize_data_source(source)\n\n    def persist(frame: pd.DataFrame | None, actual_source: str | None) -> pd.DataFrame | None:\n        if frame is None or frame.empty or actual_source is None:\n            return frame\n        _save_cache(ticker, frame, selected)\n        _save_price_source_meta(ticker, selected, actual_source)\n        return frame\n\n    if force:\n        frame, actual_source = _download_single_with_source(ticker, selected)\n        return persist(frame, actual_source)\n\n    cached = _load_cache(ticker, selected)\n    if cached is None:\n        frame, actual_source = _download_single_with_source(ticker, selected)\n        return persist(frame, actual_source)\n\n    if cache_first or _cache_has_completed_daily_bar(cached):\n        return cached\n\n    cached_index = pd.DatetimeIndex(cached.index).dropna()\n    if cached_index.empty:\n        return cached\n    last_timestamp = pd.Timestamp(cast(Any, cached_index[-1]))\n    last_date = cast(datetime, last_timestamp.to_pydatetime())\n    if last_date.tzinfo is not None:\n        last_date = last_date.replace(tzinfo=None)\n\n    metadata = _load_price_source_meta(ticker, selected)\n    actual_source = metadata.get("actual_source")\n    if selected != "auto":\n        actual_source = selected\n\n    request_start = last_date - timedelta(days=7)\n    if actual_source:\n        try:\n            incremental, provider = _download_single_with_source(\n                ticker, actual_source, start_date=request_start\n            )\n        except _DOWNLOAD_ERRORS as exc:\n            logger.debug("Incremental update failed for %s: %s", ticker, exc)\n            incremental, provider = None, None\n        if incremental is not None and not incremental.empty and provider == actual_source:\n            new_df = incremental.loc[incremental.index >= pd.Timestamp(last_date)]\n            if not new_df.empty:\n                combined = cast(pd.DataFrame, pd.concat([cached, new_df]))\n                combined = combined.loc[~combined.index.duplicated(keep="last")].sort_index()\n                validated = _validate_ohlcv(combined)\n                if validated is not None and not validated.equals(cached):\n                    return persist(validated, actual_source)\n            return cached\n\n    if selected == "auto":\n        rebuilt, provider = _download_single_with_source(ticker, selected)\n        if rebuilt is not None and not rebuilt.empty and provider is not None:\n            return persist(rebuilt, provider)\n\n    return cached\n\n\ndef download_batch(''', 'download_ticker provider-consistent implementation', flags=re.DOTALL)
save(path, text)

path = 'score.py'
text = load(path)
old = '''    trap = value_trap_risk(df)\n    breakout = breakout_score(df)\n    entry = entry_point(df, breakout)\n    base_score = total * (1.0 - 0.55 * trap / 100.0)\n    trigger_score = _clamp(breakout * 0.55 + entry["score"] * 0.45, 0.0, 100.0)\n    if trap >= 70.0:\n        trigger_score *= 0.5\n    final_score = _clamp(base_score * 0.65 + trigger_score * 0.35, 0.0, 100.0)\n'''
new = '''    trap = value_trap_risk(df)\n    breakout = breakout_score(df)\n    entry = entry_point(\n        df,\n        breakout,\n        volume_score=volume,\n        value_trap_risk_value=trap,\n    )\n\n    # Three distinct layers: setup quality, trigger strength and execution\n    # readiness. Missing indicator dimensions reduce the score instead of\n    # allowing the remaining dimensions to be renormalized to an artificial 100.\n    setup_coverage = 0.55 + 0.45 * indicator_coverage\n    trigger_coverage = 0.75 + 0.25 * indicator_coverage\n    execution_coverage = 0.70 + 0.30 * indicator_coverage\n    base_score = total * (1.0 - 0.55 * trap / 100.0) * setup_coverage\n    trigger_score = _clamp(breakout * trigger_coverage, 0.0, 100.0)\n    execution_score = _clamp(float(entry["score"]) * execution_coverage, 0.0, 100.0)\n    if trap >= 70.0:\n        trigger_score *= 0.5\n        execution_score *= 0.5\n    final_score = _clamp(\n        base_score * 0.55 + trigger_score * 0.25 + execution_score * 0.20,\n        0.0,\n        100.0,\n    )\n    coverage_cap = 40.0 + 60.0 * indicator_coverage\n    final_score = min(final_score, coverage_cap)\n'''
text = replace_once(text, old, new, 'score composition')
text = replace_once(text, '        "entry": entry["score"],\n', '        "entry": entry["score"],\n        "execution": execution_score,\n        "coverage_cap": coverage_cap,\n', 'score contribution execution')
save(path, text)

path = 'analytics.py'
text = load(path)
text = replace_once(text, 'from score import score_ticker\n', 'from score import entry_point, score_ticker\n', 'analytics entry_point import')
text = sub_once(text, r'def _signal_points\(\n    enriched: pd\.DataFrame, cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS\n\) -> list\[int\]:\n.*?\n\ndef _backtest_one_ticker\(', '''_BACKTEST_ACTIONABLE_SIGNALS = frozenset({"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"})\n\n\ndef _signal_points(\n    enriched: pd.DataFrame, cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS\n) -> list[int]:\n    """Locate historical signals with the same entry engine used by live scans."""\n    if len(enriched) < 252:\n        return []\n    cooldown = max(1, int(cooldown))\n    close = pd.to_numeric(enriched.get("Close"), errors="coerce")\n    high = pd.to_numeric(enriched.get("High"), errors="coerce")\n    low = pd.to_numeric(enriched.get("Low"), errors="coerce")\n    ma20 = pd.to_numeric(enriched.get("MA20", pd.Series(np.nan, index=enriched.index)), errors="coerce")\n    ma50 = pd.to_numeric(enriched.get("MA50", pd.Series(np.nan, index=enriched.index)), errors="coerce")\n    atr = pd.to_numeric(enriched.get("ATR14", pd.Series(np.nan, index=enriched.index)), errors="coerce")\n    support = low.rolling(20, min_periods=20).min()\n    resistance = high.shift(1).rolling(20, min_periods=20).max()\n    near_support = close.le(support + atr.fillna(close * 0.03) * 1.5)\n    five_day_up = close.ge(close.shift(5))\n    base_entry = (\n        close.ge(ma20).astype(float) * 20.0\n        + ma20.ge(ma50).astype(float) * 20.0\n        + near_support.astype(float) * 20.0\n        + five_day_up.astype(float) * 15.0\n    )\n    broad_candidate = (\n        (close.gt(ma20) & base_entry.ge(25.0))\n        | (near_support & base_entry.ge(45.0))\n        | close.gt(resistance)\n    ).fillna(False)\n    candidates = np.flatnonzero(broad_candidate.to_numpy(dtype=bool))\n    last_signal = -cooldown\n    points: list[int] = []\n    is_etf = False\n    for index in candidates:\n        if index < 251 or index >= len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS:\n            continue\n        if index - last_signal < cooldown:\n            continue\n        historical = enriched.iloc[: index + 1].copy()\n        historical_score = score_ticker(historical, is_etf=is_etf)\n        entry = entry_point(\n            historical,\n            breakout=historical_score.breakout_score,\n            volume_score=historical_score.volume,\n            value_trap_risk_value=historical_score.value_trap_risk,\n        )\n        if str(entry.get("signal", "AVOID")).upper() not in _BACKTEST_ACTIONABLE_SIGNALS:\n            continue\n        points.append(int(index))\n        last_signal = int(index)\n    return points\n\n\ndef _backtest_one_ticker(''', 'historical signal engine', flags=re.DOTALL)
text = replace_once(text, '    score_cache: dict[int, float] = {}\n', '    score_cache: dict[int, float] = {}\n    signal_cache: dict[int, str] = {}\n', 'backtest signal cache')
text = replace_once(text, '''        score_cache[length] = (\n            final_score\n            if np.isfinite(final_score)\n            else _finite_float(getattr(historical_score, "total", np.nan), 0.0)\n        )\n''', '''        score_cache[length] = (\n            final_score\n            if np.isfinite(final_score)\n            else _finite_float(getattr(historical_score, "total", np.nan), 0.0)\n        )\n        historical_entry = entry_point(\n            historical,\n            breakout=historical_score.breakout_score,\n            volume_score=historical_score.volume,\n            value_trap_risk_value=historical_score.value_trap_risk,\n        )\n        signal_cache[length] = str(historical_entry.get("signal", "AVOID")).upper()\n''', 'historical entry signal cache')
text = replace_once(text, '                "score": score_cache[index + 1],\n                "split": split,\n', '                "score": score_cache[index + 1],\n                "entry_signal": signal_cache.get(index + 1, "AVOID"),\n                "split": split,\n', 'backtest sample entry signal')
text = replace_once(text, '    for ticker, group in sample_frame.groupby("ticker", sort=False):\n', '    if "entry_signal" not in sample_frame:\n        sample_frame["entry_signal"] = "UNKNOWN"\n    for (ticker, entry_signal), group in sample_frame.groupby(["ticker", "entry_signal"], sort=False):\n', 'signal-specific backtest grouping')
text = replace_once(text, '                "ticker": str(ticker),\n                "samples": len(group),\n', '                "ticker": str(ticker),\n                "entry_signal": str(entry_signal),\n                "samples": len(group),\n', 'signal-specific backtest row')
text = replace_once(text, '''    metrics = (\n        pd.DataFrame(summary.by_ticker)\n        .rename(columns={"ticker": "Ticker", **metric_columns})\n        .reindex(columns=["Ticker", *metric_columns.values()])\n    )\n    frame = frame.merge(metrics, on="Ticker", how="left", validate="one_to_one")\n''', '''    metrics = (\n        pd.DataFrame(summary.by_ticker)\n        .rename(columns={"ticker": "Ticker", "entry_signal": "EntrySignal", **metric_columns})\n        .reindex(columns=["Ticker", "EntrySignal", *metric_columns.values()])\n    )\n    frame["EntrySignal"] = (\n        frame.get("EntrySignal", pd.Series("AVOID", index=frame.index))\n        .fillna("AVOID")\n        .astype(str)\n        .str.upper()\n    )\n    metrics["EntrySignal"] = metrics["EntrySignal"].fillna("UNKNOWN").astype(str).str.upper()\n    frame = frame.merge(metrics, on=["Ticker", "EntrySignal"], how="left", validate="one_to_one")\n''', 'signal-specific backtest merge')
save(path, text)

path = 'fundamental_data.py'
text = load(path)
text = replace_once(text, '_CACHE_COMPLETENESS_THRESHOLD = 0.90\n', '_CACHE_COMPLETENESS_THRESHOLD = 0.80\n_FUNDAMENTAL_CACHE_MAX_AGE_DAYS = 7\n', 'fundamental cache thresholds')
text = sub_once(text, r'def _is_current_quarter\(\) -> bool:\n.*?\n\ndef _write_frame', '''def _is_current_quarter() -> bool:\n    """Return True only for a recent cache from the current disclosure quarter."""\n    try:\n        metadata = json.loads(_META_PATH.read_text(encoding="utf-8"))\n        quarter = f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}"\n        if metadata.get("quarter") != quarter:\n            return False\n        updated = date.fromisoformat(str(metadata.get("updated", "")))\n        return (date.today() - updated).days <= _FUNDAMENTAL_CACHE_MAX_AGE_DAYS\n    except (OSError, ValueError, TypeError):\n        return False\n\n\ndef _write_frame''', 'fundamental freshness function', flags=re.DOTALL)
text = sub_once(text, r'def _cache_completeness\(\n    frame: pd\.DataFrame,\n    symbols: list\[str\],\n    industries: Mapping\[str, str\],\n\) -> float:\n.*?\n\ndef _fetch_fundamental_row', '''def _cache_completeness(\n    frame: pd.DataFrame,\n    symbols: list[str],\n    industries: Mapping[str, str],\n) -> float:\n    if frame.empty or not symbols:\n        return 0.0\n    cached = (\n        frame.drop_duplicates("Ticker", keep="last")\n        .set_index("Ticker")\n        .reindex(symbols)\n    )\n    roe = pd.to_numeric(cached.get("ROE"), errors="coerce").notna()\n    margin = pd.to_numeric(cached.get("IndustryGrossMarginPercentile"), errors="coerce").notna()\n    profits = (\n        pd.to_numeric(cached.get("NetProfitY1"), errors="coerce").notna()\n        & pd.to_numeric(cached.get("NetProfitY2"), errors="coerce").notna()\n        & pd.to_numeric(cached.get("NetProfitY3"), errors="coerce").notna()\n    )\n    periods = pd.to_numeric(cached.get("InstitutionHoldingPeriods"), errors="coerce")\n    trend = cached.get("InstitutionHoldingTrend", pd.Series("", index=cached.index)).fillna("").astype(str).str.strip()\n    holder = periods.ge(2) & trend.ne("") & ~trend.str.lower().eq("unknown")\n    factor_count = roe.astype(int) + margin.astype(int) + profits.astype(int) + holder.astype(int)\n    complete = factor_count.ge(3)\n    if industries:\n        expected_industry = cached.index.to_series().isin(industries)\n        cache_industry = cached.get("Industry", pd.Series("", index=cached.index)).fillna("").astype(str).str.strip().ne("")\n        complete &= ~expected_industry | cache_industry\n    return float(complete.mean()) if len(complete) else 0.0\n\n\ndef _fetch_fundamental_row''', 'fundamental completeness', flags=re.DOTALL)
save(path, text)

path = 'fundamental_quality.py'
text = load(path)
text = text.replace('"机构持仓连续增加"', '"机构覆盖家数连续增加"')
text = replace_once(text, '''        quality_score=(\n            round(len(passed) / (len(passed) + len(failed)) * 100.0, 4)\n            if passed or failed\n            else np.nan\n        ),\n''', '''        quality_score=(\n            round(\n                50.0\n                + (len(passed) / (len(passed) + len(failed)) * 100.0 - 50.0)\n                * completeness,\n                4,\n            )\n            if passed or failed\n            else np.nan\n        ),\n''', 'quality score shrinkage')
save(path, text)

path = 'main.py'
text = load(path)
text = replace_once(text, '    if getattr(args, "refresh_fundamentals", False) and stock_universe:\n', '    if stock_universe:\n', 'scan automatic fundamental refresh')
text = replace_once(text, '''    fundamental_path = fundamental_data_path()\n    if getattr(args, "refresh_fundamentals", False):\n''', '''    fundamental_path = fundamental_data_path()\n    if stock_universe:\n''', 'report automatic fundamental refresh')
save(path, text)

path = 'signal_lifecycle.py'
text = load(path)
text = sub_once(text, r'    prior_recency_factor = _number\(\n        result\.get\("SignalRecencyFactor", pd\.Series\(np\.nan, index=result\.index\)\),\n        np\.nan,\n    \)\n', '', 'remove unused prior recency factor')
text = sub_once(text, r'    base_institutional = _number\(\n        result\.get\("InstitutionalScore", result\["Score"\]\), default=np\.nan\n    \)\n.*?    result\.loc\[\n        ~result\.get\("IsETF".*?\n    \] = INSTITUTIONAL_TIER_TRAP_LABEL\n', '''    result["BreakoutQualityFactor"] = _number(\n        result.get("BreakoutQualityFactor", pd.Series(1.0, index=result.index)), 1.0\n    ).clip(0.0, 1.0)\n''', 'remove duplicate lifecycle score/tier mutation', flags=re.DOTALL)
save(path, text)

path = 'scanner.py'
text = load(path)
text = replace_once(text, '    _load_cache,\n', '    _cache_path,\n    _load_cache,\n', 'scanner canonical cache import')
text = replace_once(text, '''    results.sort(\n        key=lambda result: (\n            _parse_float(result.institutional_score, np.nan)\n            if np.isfinite(_parse_float(result.institutional_score, np.nan))\n            else _parse_float(result.final_score, result.score.total)\n        ),\n        reverse=True,\n    )\n''', '''    results.sort(\n        key=lambda result: (\n            _parse_float(result.ranking_score, np.nan)\n            if np.isfinite(_parse_float(result.ranking_score, np.nan))\n            else _parse_float(result.institutional_score, np.nan)\n            if np.isfinite(_parse_float(result.institutional_score, np.nan))\n            else _parse_float(result.final_score, result.score.total)\n        ),\n        reverse=True,\n    )\n''', 'scanner canonical ranking sort')
text = sub_once(text, r'def _cache_path_for\(ticker: str, source: str\) -> Path:\n    .*?\n\n', '''def _cache_path_for(ticker: str, source: str) -> Path:\n    return _cache_path(_normalize_ticker(ticker), normalize_data_source(source))\n\n''', 'scanner canonical cache path', flags=re.DOTALL)
save(path, text)

path = 'gui_core.py'
text = load(path)
text = sub_once(text, r'DATA_SOURCE_HINTS = \{.*?\n\}', '''DATA_SOURCE_HINTS = {\n    "自动优选": "腾讯 / AKShare / 东方财富自动择优（统一前复权）",\n    "AkShare": "仅使用 AkShare，不静默混源",\n    "东方财富": "仅使用东方财富，不静默混源",\n    "新浪财经": "仅使用新浪财经（独立缓存）",\n    "腾讯财经": "仅使用腾讯财经，不静默混源",\n}''', 'gui data source hints', flags=re.DOTALL)
text = text.replace('"BreakoutScore": "启动概率",', '"BreakoutScore": "突破强度",')
text = text.replace('"InstitutionHoldingStatus": "机构持仓状态",', '"InstitutionHoldingStatus": "机构覆盖趋势",')
text = sub_once(text, r'DISPLAY_COLUMNS = \(.*?\n\)', '''DISPLAY_COLUMNS = (\n    "OverallRank",\n    "Ticker",\n    "Name",\n    "Close",\n    "EntrySignal",\n    "EntryZone",\n    "BreakoutBuyPrice",\n    "StopLoss",\n    "RankingEligibility",\n    "RankingScore",\n    "InstitutionalTier",\n    "InstitutionalScore",\n    "FinalScore",\n    "QualityGate",\n    "QualityDataCompleteness",\n    "BacktestSamples",\n    "BacktestConfidenceTier",\n    "ValueTrapRisk",\n    "ChaseRiskScore",\n    "PassedFilters",\n    "TradeReadinessReason",\n    "DataAsOf",\n    "RankingReason",\n)''', 'gui display columns', flags=re.DOTALL)
text = text.replace('"Close": "收盘价",', '"Close": "当日收盘价",')
text = text.replace('"EntryZone": "买入区间",', '"EntryZone": "回调买点",')
text = text.replace('"BreakoutBuyPrice": "突破买入价",', '"BreakoutBuyPrice": "突破买点",')
text = text.replace('"RankingEligibility": "排序资格",', '"RankingEligibility": "交易资格",')
text = text.replace('"TradeReadinessReason": "执行资格说明",', '"TradeReadinessReason": "执行说明",')
text = sub_once(text, r'    def _update_market_overview\(\n        self, rows: list\[list\[str\]\], indexes: dict\[str, int\]\n    \) -> None:\n.*?\n    def _market_regime_summary', '''    def _update_market_overview(\n        self, rows: list[list[str]], indexes: dict[str, int]\n    ) -> None:\n        if not hasattr(self, "market_overview"):\n            return\n        total, _active, _confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)\n        self.market_overview.set(\n            f"概览：{total} 只 · 启动 {breakout} · 可交易 {actionable} · 最终均分 {average:.1f}"\n        )\n\n    def _market_regime_summary''', 'gui compact market overview', flags=re.DOTALL)
text = replace_once(text, '''            stale = sum(\n                len(row) > freshness_index\n                and self._cell_text(row[freshness_index]) == "过期"\n                for row in filtered\n            ) if freshness_index is not None else 0\n            readiness = f" · 就绪 {recommended}" if eligibility_index is not None else ""\n            freshness = f" · 过期 {stale}" if freshness_index is not None and stale else ""\n            self.result_summary.set(\n                f"当前文件：{self.current_file} · 命中 {len(filtered):,} / {len(data_rows):,} 条{readiness}{freshness}"\n            )\n''', '''            readiness = f" · 就绪 {recommended}" if eligibility_index is not None else ""\n            self.result_summary.set(\n                f"当前文件：{self.current_file} · 命中 {len(filtered):,} / {len(data_rows):,} 条{readiness}"\n            )\n''', 'gui hide stale diagnostic counter')
save(path, text)

save('gui.py', '''from __future__ import annotations\n\n"""Tkinter GUI entrypoint. Presentation policy now lives in gui_core.py."""\n\nfrom gui_core import ScannerGUI, main\n\n__all__ = ["ScannerGUI", "main"]\n\n\nif __name__ == "__main__":\n    main()\n''')

path = 'config.py'
text = load(path)
text = sub_once(text, r'SCORING_VERSION: str = "[^"]+"', 'SCORING_VERSION: str = "2026-08-07-v11-provider-consistency-entry-backtest"', 'scoring version')
save(path, text)

path = 'requirements.txt'
text = load(path)
text = replace_once(text, 'akshare>=1.16.53\n', 'akshare==1.16.53\n', 'akshare pin')
text = text.replace('pandas>=2.0.0\n', 'pandas>=2.0.0,<3.0\n')
text = text.replace('numpy>=1.24.0\n', 'numpy>=1.24.0,<3.0\n')
text = text.replace('requests>=2.28.0\n', 'requests>=2.28.0,<3.0\n')
save(path, text)

new_tests = r'''from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import downloader
import gui_core
from fundamental_quality import calculate_quality
from score import score_ticker


class HardeningRegressionTests(TestCase):
    def test_beijing_ticker_normalization(self):
        self.assertEqual(downloader.normalize_ticker("430047"), "430047.BJ")
        self.assertEqual(downloader.normalize_ticker("832000"), "832000.BJ")
        self.assertEqual(downloader.normalize_ticker("920001"), "920001.BJ")

    def test_explicit_sources_never_silently_fallback(self):
        self.assertEqual(downloader._DATA_SOURCE_CANDIDATES["eastmoney"], ("eastmoney",))
        self.assertEqual(downloader._DATA_SOURCE_CANDIDATES["akshare"], ("akshare",))
        self.assertNotIn("sina", downloader._DATA_SOURCE_CANDIDATES["auto"])

    def test_price_cache_schema_invalidates_legacy_paths(self):
        path = downloader._cache_path("000001.SZ", "eastmoney")
        self.assertIn("v2-provider-consistent", path.name)

    def test_missing_score_dimensions_cap_final_score(self):
        index = pd.date_range("2025-01-01", periods=260, freq="B")
        close = pd.Series(np.linspace(10.0, 8.0, len(index)), index=index)
        frame = pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99, "MA200": close.rolling(200, min_periods=1).mean()}, index=index)
        result = score_ticker(frame)
        self.assertEqual(result.missing_indicators, 3)
        self.assertLessEqual(result.final_score, 64.0)

    def test_partial_fundamentals_shrink_quality_toward_neutral(self):
        quality = calculate_quality({"Ticker": "000001.SZ", "ROE": 15.0})
        self.assertAlmostEqual(quality.quality_data_completeness, 0.25)
        self.assertAlmostEqual(quality.quality_score, 62.5)

    def test_backtest_signal_points_use_live_entry_engine(self):
        index = pd.date_range("2024-01-01", periods=340, freq="B")
        close = pd.Series(np.linspace(10.0, 12.0, len(index)), index=index)
        frame = pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99, "Volume": 1_000_000.0, "MA20": close * 0.99, "MA50": close * 0.98, "ATR14": close * 0.02}, index=index)
        fake_score = SimpleNamespace(breakout_score=80.0, volume=20.0, value_trap_risk=10.0)
        with patch.object(analytics, "score_ticker", return_value=fake_score), patch.object(analytics, "entry_point", return_value={"signal": "BUY_NOW"}) as entry:
            points = analytics._signal_points(frame, cooldown=20)
        self.assertTrue(points)
        self.assertGreater(entry.call_count, 0)
        self.assertTrue(all(b - a >= 20 for a, b in zip(points, points[1:])))

    def test_backtest_rows_are_signal_specific(self):
        sample = pd.DataFrame({"ticker": ["000001.SZ"] * 4, "entry_signal": ["BUY_NOW", "BUY_NOW", "WAIT_PULLBACK", "WAIT_PULLBACK"], "return20": [2.0, 3.0, -1.0, 1.0], "return60": [4.0, 5.0, -2.0, 2.0], "benchmark_return20": [0.0] * 4, "benchmark_return60": [0.0] * 4, "net_return20": [1.5, 2.5, -1.5, 0.5], "net_return60": [3.5, 4.5, -2.5, 1.5], "drawdown20": [-2.0, -2.0, -4.0, -3.0], "drawdown60": [-3.0, -3.0, -6.0, -5.0], "sample_weight": [1.0] * 4, "signal_date": pd.date_range("2025-01-01", periods=4, freq="30D")})
        rows = analytics._ticker_backtest_rows(sample)
        self.assertEqual({row["entry_signal"] for row in rows}, {"BUY_NOW", "WAIT_PULLBACK"})
        self.assertEqual(len(rows), 2)

    def test_gui_first_screen_keeps_decision_fields_only(self):
        self.assertIn("Close", gui_core.DISPLAY_COLUMNS)
        self.assertIn("EntryZone", gui_core.DISPLAY_COLUMNS)
        self.assertNotIn("HardRiskFlag", gui_core.DISPLAY_COLUMNS)
        self.assertNotIn("DataFreshnessStatus", gui_core.DISPLAY_COLUMNS)
        self.assertNotIn("MarketRegime", gui_core.DISPLAY_COLUMNS)
        self.assertEqual(gui_core.COLUMN_NAMES["BreakoutScore"], "突破强度")
'''
save('test_hardening_regressions.py', new_tests)

print('project hardening patch applied')
