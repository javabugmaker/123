from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    'TOP_N_REPORT: int = 50\nTOP_N_PARQUET: int = 200\n\nSCORING_VERSION: str = "2026-08-08-v14-fast-exact-incremental-backtest"',
    'TOP_N_REPORT: int = 50\nTOP_N_PARQUET: int = 200\nETF_THEME_MAX_PER_TOP_LIST: Final[int] = 2\n\nSCORING_VERSION: str = "2026-08-08-v15-ranking-diversity-metadata"',
)

# ---------------------------------------------------------------------------
# scanner.py: persist backtest provenance on ScanResult/export.
# ---------------------------------------------------------------------------
replace_once(
    "scanner.py",
    '    backtest_return_std_20d: float = np.nan\n    composite_score: float = np.nan',
    '    backtest_return_std_20d: float = np.nan\n    backtest_mode: str = ""\n    backtest_cache_hit: bool = False\n    backtest_last_evaluated_date: str = ""\n    backtest_engine: str = ""\n    composite_score: float = np.nan',
)

# ---------------------------------------------------------------------------
# analytics.py: neutral missing industry, per-ticker provenance, cache tracking.
# ---------------------------------------------------------------------------
replace_once(
    "analytics.py",
    '    cache_hits: int = 0\n    elapsed_seconds: float = 0.0',
    '    cache_hits: int = 0\n    cache_hit_tickers: list[str] = field(default_factory=list)\n    elapsed_seconds: float = 0.0',
)

replace_once(
    "analytics.py",
    '''            if enriched is not None:\n                cached_frames[result.ticker] = enriched\n                if np.isfinite(relative):\n                    industry = result.industry or result.sector or "未分类"\n                    industry_returns.setdefault(industry, {})[result.ticker] = relative''',
    '''            if enriched is not None:\n                cached_frames[result.ticker] = enriched\n                classification = str(result.industry or result.sector or "").strip()\n                if classification and np.isfinite(relative):\n                    industry_returns.setdefault(classification, {})[result.ticker] = relative''',
)

replace_once(
    "analytics.py",
    '''        value = _safe_return(frame["Close"], 60)\n        industry = result.industry or result.sector or "未分类"\n        total_return, count = industry_totals.get(industry, (0.0, 0))\n        peer = (\n            (total_return - value) / (count - 1)\n            if np.isfinite(value) and count >= 2\n            else np.nan\n        )\n        result.industry_relative_strength = (\n            round(value - peer, 2)\n            if np.isfinite(value) and np.isfinite(peer)\n            else np.nan\n        )\n        result.industry_momentum_60d = round(peer, 2) if np.isfinite(peer) else np.nan\n        if np.isfinite(peer):\n            result.sector_confirmation_factor = round(\n                float(\n                    np.clip(\n                        0.2 + _bounded_score(peer, -20.0, 20.0) * 0.8,\n                        0.2,\n                        1.0,\n                    )\n                ),\n                4,\n            )\n        else:\n            result.sector_confirmation_factor = 1.0''',
    '''        value = _safe_return(frame["Close"], 60)\n        classification = str(result.industry or result.sector or "").strip()\n        if not classification:\n            result.industry_relative_strength = np.nan\n            result.industry_momentum_60d = np.nan\n            result.sector_confirmation_factor = 1.0\n            continue\n        total_return, count = industry_totals.get(classification, (0.0, 0))\n        peer = (\n            (total_return - value) / (count - 1)\n            if np.isfinite(value) and count >= 2\n            else np.nan\n        )\n        result.industry_relative_strength = (\n            round(value - peer, 2)\n            if np.isfinite(value) and np.isfinite(peer)\n            else np.nan\n        )\n        result.industry_momentum_60d = round(peer, 2) if np.isfinite(peer) else np.nan\n        if np.isfinite(peer):\n            result.sector_confirmation_factor = round(\n                float(\n                    np.clip(\n                        0.2 + _bounded_score(peer, -20.0, 20.0) * 0.8,\n                        0.2,\n                        1.0,\n                    )\n                ),\n                4,\n            )\n        else:\n            result.sector_confirmation_factor = 1.0''',
)

replace_once(
    "analytics.py",
    '''def _backtest_chunk_worker(\n    tickers: list[str],\n) -> tuple[pd.DataFrame, int, list[tuple[str, str]], int]:\n    context = _BACKTEST_WORKER_CONTEXT\n    frames: list[pd.DataFrame] = []\n    cache_hits = 0\n    errors: list[tuple[str, str]] = []''',
    '''def _backtest_chunk_worker(\n    tickers: list[str],\n) -> tuple[pd.DataFrame, int, list[str], list[tuple[str, str]], int]:\n    context = _BACKTEST_WORKER_CONTEXT\n    frames: list[pd.DataFrame] = []\n    cache_hits = 0\n    cache_hit_tickers: list[str] = []\n    errors: list[tuple[str, str]] = []''',
)
replace_once(
    "analytics.py",
    '''            cache_hits += int(cache_hit)\n        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:\n            errors.append((ticker, str(exc)))\n    batch = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()\n    return batch, cache_hits, errors, len(tickers)''',
    '''            cache_hits += int(cache_hit)\n            if cache_hit:\n                cache_hit_tickers.append(str(ticker))\n        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:\n            errors.append((ticker, str(exc)))\n    batch = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()\n    return batch, cache_hits, cache_hit_tickers, errors, len(tickers)''',
)
replace_once(
    "analytics.py",
    '''    completed = 0\n    cache_hits = 0\n    next_progress = max(1, int(BACKTEST_PROGRESS_INTERVAL))''',
    '''    completed = 0\n    cache_hits = 0\n    cache_hit_tickers: set[str] = set()\n    next_progress = max(1, int(BACKTEST_PROGRESS_INTERVAL))''',
)
replace_once(
    "analytics.py",
    '''    def record_progress(\n        batch_frame: pd.DataFrame,\n        batch_completed: int,\n        batch_cache_hits: int,\n    ) -> None:\n        nonlocal completed, cache_hits, next_progress, sample_count''',
    '''    def record_progress(\n        batch_frame: pd.DataFrame,\n        batch_completed: int,\n        batch_cache_hits: int,\n        batch_cache_hit_tickers: list[str] | None = None,\n    ) -> None:\n        nonlocal completed, cache_hits, next_progress, sample_count''',
)
replace_once(
    "analytics.py",
    '''        completed += int(batch_completed)\n        cache_hits += int(batch_cache_hits)\n        if completed >= next_progress or completed >= total:''',
    '''        completed += int(batch_completed)\n        cache_hits += int(batch_cache_hits)\n        if batch_cache_hit_tickers:\n            cache_hit_tickers.update(str(ticker) for ticker in batch_cache_hit_tickers)\n        if completed >= next_progress or completed >= total:''',
)
replace_once(
    "analytics.py",
    '''                    batch_frame, batch_hits, errors, batch_count = future.result()''',
    '''                    batch_frame, batch_hits, batch_hit_tickers, errors, batch_count = future.result()''',
)
replace_once(
    "analytics.py",
    '''                    record_progress(pd.DataFrame(), len(chunk), 0)''',
    '''                    record_progress(pd.DataFrame(), len(chunk), 0, [])''',
)
replace_once(
    "analytics.py",
    '''                record_progress(batch_frame, batch_count, batch_hits)''',
    '''                record_progress(batch_frame, batch_count, batch_hits, batch_hit_tickers)''',
)
replace_once(
    "analytics.py",
    '''            record_progress(batch_frame, 1, int(cache_hit))''',
    '''            record_progress(\n                batch_frame,\n                1,\n                int(cache_hit),\n                [str(ticker)] if cache_hit else [],\n            )''',
)
replace_once(
    "analytics.py",
    '''    summary.cache_hits = int(cache_hits)\n    summary.elapsed_seconds = float(time.perf_counter() - backtest_started)''',
    '''    summary.cache_hits = int(cache_hits)\n    summary.cache_hit_tickers = sorted(cache_hit_tickers)\n    summary.elapsed_seconds = float(time.perf_counter() - backtest_started)''',
)
replace_once(
    "analytics.py",
    '''        summary.by_ticker = _ticker_backtest_rows(sample_frame, objective)\n        summary.by_score_bucket = _bucket_rows(sample_frame)''',
    '''        summary.by_ticker = _ticker_backtest_rows(sample_frame, objective)\n        last_evaluated = split_dates.get("global_end") or ""\n        for row in summary.by_ticker:\n            row["backtest_mode"] = profile.name.upper()\n            row["backtest_cache_hit"] = str(row.get("ticker", "")) in cache_hit_tickers\n            row["backtest_last_evaluated_date"] = last_evaluated\n            row["backtest_engine"] = engine\n        summary.by_score_bucket = _bucket_rows(sample_frame)''',
)

replace_once(
    "analytics.py",
    '''        "failure_signal_factor": "FailureSignalFactor",\n    }''',
    '''        "failure_signal_factor": "FailureSignalFactor",\n        "backtest_mode": "BacktestMode",\n        "backtest_cache_hit": "BacktestCacheHit",\n        "backtest_last_evaluated_date": "BacktestLastEvaluatedDate",\n        "backtest_engine": "BacktestEngine",\n    }''',
)
replace_once(
    "analytics.py",
    '''        "BacktestAdjustedScore",\n        "InstitutionalTier",''',
    '''        "BacktestAdjustedScore",\n        "BacktestMode",\n        "BacktestCacheHit",\n        "BacktestLastEvaluatedDate",\n        "BacktestEngine",\n        "InstitutionalTier",''',
)
replace_once(
    "analytics.py",
    '''    if "SectorConfirmationFactor" in frame:\n        sector_factor = pd.to_numeric(\n            frame["SectorConfirmationFactor"], errors="coerce"\n        ).fillna(1.0)\n    else:\n        sector_factor = pd.Series(1.0, index=frame.index)\n    sector_multiplier = 0.7 + 0.3 * sector_factor''',
    '''    if "SectorConfirmationFactor" in frame:\n        sector_factor = pd.to_numeric(\n            frame["SectorConfirmationFactor"], errors="coerce"\n        ).fillna(1.0)\n    else:\n        sector_factor = pd.Series(1.0, index=frame.index)\n    sector_text = frame.get("Sector", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()\n    industry_text = frame.get("Industry", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()\n    classified = sector_text.ne("") | industry_text.ne("")\n    sector_factor = sector_factor.where(classified, 1.0).clip(0.0, 1.0)\n    frame["SectorConfirmationFactor"] = sector_factor.round(4)\n    if "IndustryRelativeStrength" in frame:\n        frame.loc[~classified, "IndustryRelativeStrength"] = np.nan\n    if "IndustryMomentum60D" in frame:\n        frame.loc[~classified, "IndustryMomentum60D"] = np.nan\n    sector_multiplier = 0.7 + 0.3 * sector_factor''',
)

# ---------------------------------------------------------------------------
# signal_lifecycle.py: RankingScore is primary. Only hard risk is forced last.
# ---------------------------------------------------------------------------
replace_once(
    "signal_lifecycle.py",
    '''    eligibility_order = result["RankingEligibility"].map(\n        {"推荐": 2, "观察": 1, "风险过滤": 0}\n    ).fillna(0)\n    result = result.assign(_EligibilityOrder=eligibility_order).sort_values(\n        [\n            "_EligibilityOrder",\n            "RankingScore",\n            "InstitutionalScore",\n            "FinalScore",\n            "Score",\n        ],\n        ascending=[False, False, False, False, False],\n        kind="mergesort",\n    ).reset_index(drop=True)\n    result["OverallRank"] = np.arange(1, len(result) + 1)\n    return result.drop(columns="_EligibilityOrder")''',
    '''    risk_order = result["RankingEligibility"].eq("风险过滤").astype(int)\n    result = result.assign(_RiskOrder=risk_order).sort_values(\n        [\n            "_RiskOrder",\n            "RankingScore",\n            "InstitutionalScore",\n            "BacktestAdjustedScore",\n            "EntrySignalPriority",\n            "FinalScore",\n            "Score",\n        ],\n        ascending=[True, False, False, False, False, False, False],\n        kind="mergesort",\n    ).reset_index(drop=True)\n    result["OverallRank"] = np.arange(1, len(result) + 1)\n    return result.drop(columns="_RiskOrder")''',
)

# ---------------------------------------------------------------------------
# report.py: consistent ranking, ETF theme diversity, provenance columns.
# ---------------------------------------------------------------------------
replace_once("report.py", "import os\nimport tempfile", "import os\nimport re\nimport tempfile")
replace_once(
    "report.py",
    '''    INSTITUTIONAL_TIER_WAIT_LABEL,\n    OUTPUT_DIR,''',
    '''    INSTITUTIONAL_TIER_WAIT_LABEL,\n    ETF_THEME_MAX_PER_TOP_LIST,\n    OUTPUT_DIR,''',
)
replace_once(
    "report.py",
    '''    eligibility_order = {"推荐": 2, "观察": 1, "风险过滤": 0}\n    return sorted(\n        valid,\n        key=lambda r: (\n            eligibility_order.get(r.ranking_eligibility, 0),\n            rank_score(r),\n            int(r.filter_details.get("signal_count", 0)),\n        ),\n        reverse=True,\n    )''',
    '''    return sorted(\n        valid,\n        key=lambda r: (\n            r.ranking_eligibility != "风险过滤",\n            rank_score(r),\n            int(r.filter_details.get("signal_count", 0)),\n        ),\n        reverse=True,\n    )''',
)
replace_once(
    "report.py",
    '''                "BacktestObjectiveValue": round(r.backtest_objective_value, 4)\n                if np.isfinite(r.backtest_objective_value)\n                else None,\n                "UniverseType": r.universe_type,''',
    '''                "BacktestObjectiveValue": round(r.backtest_objective_value, 4)\n                if np.isfinite(r.backtest_objective_value)\n                else None,\n                "BacktestMode": r.backtest_mode,\n                "BacktestCacheHit": r.backtest_cache_hit,\n                "BacktestLastEvaluatedDate": r.backtest_last_evaluated_date,\n                "BacktestEngine": r.backtest_engine,\n                "UniverseType": r.universe_type,''',
)
replace_once(
    "report.py",
    '''    eligibility = valid.get(\n        "RankingEligibility", pd.Series("观察", index=valid.index)\n    ).map({"推荐": 2, "观察": 1, "风险过滤": 0}).fillna(0)\n    ranked = valid.assign(\n        _EligibilityOrder=eligibility,\n        _RankingScore=sort_metric("RankingScore"),\n        _InstitutionalScore=sort_metric("InstitutionalScore"),\n        _FinalScore=sort_metric("FinalScore"),\n        _Score=sort_metric("Score"),\n    ).sort_values(\n        [\n            "_EligibilityOrder",\n            "_RankingScore",\n            "_InstitutionalScore",\n            "_FinalScore",\n            "_Score",\n        ],\n        ascending=False,\n        kind="mergesort",\n    ).drop(\n        columns=[\n            "_EligibilityOrder",\n            "_RankingScore",\n            "_InstitutionalScore",\n            "_FinalScore",\n            "_Score",\n        ]\n    ).reset_index(drop=True)''',
    '''    risk_order = valid.get(\n        "RankingEligibility", pd.Series("观察", index=valid.index)\n    ).eq("风险过滤").astype(int)\n    ranked = valid.assign(\n        _RiskOrder=risk_order,\n        _RankingScore=sort_metric("RankingScore"),\n        _InstitutionalScore=sort_metric("InstitutionalScore"),\n        _BacktestAdjustedScore=sort_metric("BacktestAdjustedScore"),\n        _EntrySignalPriority=sort_metric("EntrySignalPriority"),\n        _FinalScore=sort_metric("FinalScore"),\n        _Score=sort_metric("Score"),\n    ).sort_values(\n        [\n            "_RiskOrder",\n            "_RankingScore",\n            "_InstitutionalScore",\n            "_BacktestAdjustedScore",\n            "_EntrySignalPriority",\n            "_FinalScore",\n            "_Score",\n        ],\n        ascending=[True, False, False, False, False, False, False],\n        kind="mergesort",\n    ).drop(\n        columns=[\n            "_RiskOrder",\n            "_RankingScore",\n            "_InstitutionalScore",\n            "_BacktestAdjustedScore",\n            "_EntrySignalPriority",\n            "_FinalScore",\n            "_Score",\n        ]\n    ).reset_index(drop=True)''',
)

insert_marker = '''def _sort_export_rows(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:\n'''
helpers = '''_ETF_THEME_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (\n    ("医药医疗", ("创新药", "医疗", "医药", "生物科技", "生物医药", "医疗器械", "医疗设备")),\n    ("半导体芯片", ("半导体", "芯片", "集成电路")),\n    ("人工智能", ("人工智能", "AI", "算力", "数据中心")),\n    ("机器人", ("机器人", "人形机器人")),\n    ("黄金", ("黄金", "金矿")),\n    ("有色金属", ("有色", "铜", "铝", "稀土", "锂")),\n    ("新能源", ("新能源", "光伏", "风电", "储能", "电池")),\n    ("券商", ("证券", "券商")),\n    ("军工", ("军工", "国防")),\n    ("消费", ("消费", "白酒", "食品饮料")),\n    ("传媒游戏", ("传媒", "游戏")),\n    ("港股科技", ("恒生科技", "港股科技", "互联网")),\n    ("红利", ("红利", "高股息")),\n)\n\n\ndef _truthy(value: object) -> bool:\n    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}\n\n\ndef _etf_theme_key(row: pd.Series) -> str:\n    if not (_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf"):\n        return ""\n    text = " ".join(\n        str(row.get(column, "") or "")\n        for column in ("Name", "Industry", "Sector")\n    ).upper()\n    for theme, keywords in _ETF_THEME_GROUPS:\n        if any(str(keyword).upper() in text for keyword in keywords):\n            return theme\n    fallback = re.sub(r"ETF|LOF|基金|指数|联接|交易型开放式", "", text, flags=re.IGNORECASE)\n    fallback = re.sub(r"[^0-9A-Z\\u4e00-\\u9fff]+", "", fallback).strip()\n    return fallback[:24] or str(row.get("Ticker", "")).strip().upper()\n\n\ndef _diversify_ranked_candidates(\n    frame: pd.DataFrame,\n    limit: int,\n    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,\n) -> pd.DataFrame:\n    if frame.empty or limit <= 0:\n        return frame.head(0).copy()\n    working = frame.copy()\n    working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)\n    theme_counts: dict[str, int] = {}\n    selected: list[int] = []\n    for index, row in working.iterrows():\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        if theme:\n            if theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        selected.append(index)\n        if len(selected) >= int(limit):\n            break\n    result = working.loc[selected].copy().reset_index(drop=True)\n    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)\n    return result\n\n\n'''
replace_once("report.py", insert_marker, helpers + insert_marker)

replace_once(
    "report.py",
    '''    csv_path = destination / f"Top{top_n_csv}.csv"\n    _atomic_write_csv(ranked.head(top_n_csv), csv_path)\n    logger.info(\n        "Exported Top %d (%d rows) to %s",\n        top_n_csv,\n        len(ranked.head(top_n_csv)),\n        csv_path,\n    )''',
    '''    csv_path = destination / f"Top{top_n_csv}.csv"\n    research_pool = _diversify_ranked_candidates(ranked, top_n_csv)\n    _atomic_write_csv(research_pool, csv_path)\n    logger.info(\n        "Exported diversified Top %d (%d rows) to %s",\n        top_n_csv,\n        len(research_pool),\n        csv_path,\n    )''',
)
replace_once(
    "report.py",
    '''    _atomic_write_csv(trade_ready.head(top_n_csv), trade_ready_path)''',
    '''    trade_ready = _diversify_ranked_candidates(trade_ready, top_n_csv)\n    _atomic_write_csv(trade_ready, trade_ready_path)''',
)
replace_once(
    "report.py",
    '''        len(trade_ready.head(top_n_csv)),''',
    '''        len(trade_ready),''',
)
replace_once(
    "report.py",
    '''    _atomic_write_csv(opportunity.head(top_n_csv), opportunity_path)''',
    '''    opportunity = _diversify_ranked_candidates(opportunity, top_n_csv)\n    _atomic_write_csv(opportunity, opportunity_path)''',
)
replace_once(
    "report.py",
    '''    _atomic_write_csv(trigger.head(top_n_csv), trigger_path)''',
    '''    trigger = _diversify_ranked_candidates(trigger, top_n_csv)\n    _atomic_write_csv(trigger, trigger_path)''',
)
replace_once(
    "report.py",
    '''    _atomic_write_csv(entry.head(top_n_csv), entry_path)''',
    '''    entry = _diversify_ranked_candidates(entry, top_n_csv)\n    _atomic_write_csv(entry, entry_path)''',
)

# ---------------------------------------------------------------------------
# Regression coverage for the v15 behavior.
# ---------------------------------------------------------------------------
Path("test_ranking_v15_regressions.py").write_text(r'''import unittest\n\nimport pandas as pd\n\nfrom report import _diversify_ranked_candidates, _rank_valid_candidates\nfrom scanner import ScanResult\n\n\nclass RankingV15RegressionTests(unittest.TestCase):\n    def test_ranking_score_beats_soft_eligibility_but_risk_stays_last(self):\n        frame = pd.DataFrame(\n            [\n                {"Ticker": "LOW", "Error": "", "RankingEligibility": "推荐", "RankingScore": 30.0, "InstitutionalScore": 31.0, "BacktestAdjustedScore": 50.0, "EntrySignalPriority": 4.0, "FinalScore": 31.0, "Score": 31.0},\n                {"Ticker": "HIGH", "Error": "", "RankingEligibility": "观察", "RankingScore": 43.0, "InstitutionalScore": 44.0, "BacktestAdjustedScore": 51.0, "EntrySignalPriority": 3.0, "FinalScore": 44.0, "Score": 44.0},\n                {"Ticker": "RISK", "Error": "", "RankingEligibility": "风险过滤", "RankingScore": 99.0, "InstitutionalScore": 99.0, "BacktestAdjustedScore": 80.0, "EntrySignalPriority": 5.0, "FinalScore": 99.0, "Score": 99.0},\n            ]\n        )\n        ranked = _rank_valid_candidates(frame)\n        self.assertEqual(ranked["Ticker"].tolist(), ["HIGH", "LOW", "RISK"])\n\n    def test_etf_theme_diversity_caps_repeated_medical_etfs(self):\n        rows = []\n        for i in range(5):\n            rows.append({"Ticker": f"ETF{i}", "Name": f"创新药ETF{i}", "IsETF": True, "AssetType": "etf"})\n        for i in range(5):\n            rows.append({"Ticker": f"STK{i}", "Name": f"股票{i}", "IsETF": False, "AssetType": "stock"})\n        diversified = _diversify_ranked_candidates(pd.DataFrame(rows), 6, max_per_theme=2)\n        self.assertEqual(len(diversified), 6)\n        self.assertLessEqual((diversified["ETFTheme"] == "医药医疗").sum(), 2)\n        self.assertEqual(diversified["ResearchPoolRank"].tolist(), list(range(1, 7)))\n\n    def test_scan_result_exposes_backtest_provenance_fields(self):\n        result = ScanResult("000001.SZ")\n        self.assertEqual(result.backtest_mode, "")\n        self.assertFalse(result.backtest_cache_hit)\n        self.assertEqual(result.backtest_last_evaluated_date, "")\n        self.assertEqual(result.backtest_engine, "")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

print("ranking v15 migration applied")
