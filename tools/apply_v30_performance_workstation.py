from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing marker for {label} in {path.name}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"missing start marker for {label} in {path.name}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"missing end marker for {label} in {path.name}")
    path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


def update_config() -> None:
    path = ROOT / "config.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'PIPELINE_VERSION: str = "2026-08-09-v29-reliable-daily"',
        'PIPELINE_VERSION: str = "2026-08-09-v30-fast-workstation"',
    )
    text = text.replace(
        'GUI_VERSION: str = "2026-08-09-v29-workstation"',
        'GUI_VERSION: str = "2026-08-09-v30-workstation"',
    )
    text = text.replace(
        'BACKTEST_PROVENANCE_VERSION: str = "2026-08-09-v29"',
        'BACKTEST_PROVENANCE_VERSION: str = "2026-08-09-v30"',
    )
    path.write_text(text, encoding="utf-8")


def update_model_calibration() -> None:
    path = ROOT / "model_calibration.py"
    replacement = r'''def calibration_details_for_frame(
    frame: pd.DataFrame,
    rows: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """Resolve hierarchical calibration for a result frame in near-linear time.

    v29 resolved every ticker by repeatedly scanning the complete calibration
    row list, then scanned it a second time to recover detail fields.  A full
    market run therefore paid an O(result_rows * calibration_rows) Python cost.
    v30 builds one immutable lookup keyed by the same hierarchy and computes
    score/setup buckets in vectorized form.  Resolution semantics and priority
    order are unchanged, but each candidate now needs at most nine dictionary
    lookups.
    """
    columns = {
        "score": 50.0,
        "confidence": 0.0,
        "level": "none",
        "samples": 0,
        "effective_samples": 0.0,
        "mean_net_excess20": np.nan,
        "win_rate_net_excess20": np.nan,
        "start_date": "",
        "end_date": "",
    }
    if frame.empty:
        return pd.DataFrame(
            {
                key: pd.Series(
                    dtype=float if isinstance(value, (int, float)) else str
                )
                for key, value in columns.items()
            },
            index=frame.index,
        )
    if not rows:
        return pd.DataFrame(
            {key: pd.Series(value, index=frame.index) for key, value in columns.items()}
        )

    level_fields: dict[str, tuple[str, ...]] = {
        "asset_signal_regime_score_setup": (
            "asset_type",
            "entry_signal",
            "market_regime",
            "score_bucket",
            "setup_bucket",
        ),
        "asset_signal_regime_score": (
            "asset_type",
            "entry_signal",
            "market_regime",
            "score_bucket",
        ),
        "asset_signal_regime": ("asset_type", "entry_signal", "market_regime"),
        "asset_signal_bucket": ("asset_type", "entry_signal", "score_bucket"),
        "asset_signal": ("asset_type", "entry_signal"),
        "signal_bucket": ("entry_signal", "score_bucket"),
        "signal": ("entry_signal",),
        "asset": ("asset_type",),
        "global": tuple(),
    }
    lookup: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        level = str(row.get("level", ""))
        fields = level_fields.get(level)
        if fields is None:
            continue
        key = (level, *(str(row.get(field, "")) for field in fields))
        # build_global_calibration emits unique hierarchy keys.  setdefault also
        # preserves legacy "first row wins" behaviour for hand-written inputs.
        lookup.setdefault(key, row)

    asset_values = frame.get(
        "AssetType", frame.get("asset_type", pd.Series("stock", index=frame.index))
    ).fillna("stock").astype(str).str.lower()
    signal_values = frame.get(
        "EntrySignal", frame.get("entry_signal", pd.Series("UNKNOWN", index=frame.index))
    ).fillna("UNKNOWN").astype(str).str.upper()
    regime_values = frame.get(
        "MarketRegime", frame.get("market_regime", pd.Series("UNKNOWN", index=frame.index))
    ).map(_normalize_regime)
    model_scores = pd.to_numeric(
        frame.get("FinalScore", frame.get("score", pd.Series(np.nan, index=frame.index))),
        errors="coerce",
    )
    setup_values = pd.to_numeric(
        frame.get("BaseScore", frame.get("setup_score", pd.Series(np.nan, index=frame.index))),
        errors="coerce",
    )
    score_buckets = pd.cut(
        model_scores,
        bins=SCORE_BUCKET_EDGES,
        labels=SCORE_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")
    setup_buckets = pd.cut(
        setup_values,
        bins=SETUP_BUCKET_EDGES,
        labels=SETUP_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")

    records: list[dict[str, Any]] = []
    for asset, signal, regime, bucket_value, setup_bucket_value in zip(
        asset_values, signal_values, regime_values, score_buckets, setup_buckets
    ):
        asset = str(asset)
        signal = str(signal)
        regime = str(regime)
        bucket = str(bucket_value) if pd.notna(bucket_value) else ""
        setup_bucket = str(setup_bucket_value) if pd.notna(setup_bucket_value) else ""
        keys = (
            (
                "asset_signal_regime_score_setup",
                asset,
                signal,
                regime,
                bucket,
                setup_bucket,
            ),
            ("asset_signal_regime_score", asset, signal, regime, bucket),
            ("asset_signal_regime", asset, signal, regime),
            ("asset_signal_bucket", asset, signal, bucket),
            ("asset_signal", asset, signal),
            ("signal_bucket", signal, bucket),
            ("signal", signal),
            ("asset", asset),
            ("global",),
        )
        matched: dict[str, Any] | None = None
        score = 50.0
        confidence = 0.0
        level = "none"
        for key in keys:
            candidate = lookup.get(tuple(str(value) for value in key))
            if candidate is None:
                continue
            try:
                candidate_score = float(candidate.get("calibration_score", 50.0))
                candidate_confidence = float(candidate.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(candidate_score) or not np.isfinite(candidate_confidence):
                continue
            matched = candidate
            score = float(np.clip(candidate_score, 0.0, 100.0))
            confidence = float(np.clip(candidate_confidence, 0.0, 1.0))
            level = str(candidate.get("level", "none"))
            break

        matched = matched or {}
        mean_excess = pd.to_numeric(
            pd.Series([matched.get("mean_net_excess20", np.nan)]), errors="coerce"
        ).iloc[0]
        win_rate = pd.to_numeric(
            pd.Series([matched.get("win_rate_net_excess20", np.nan)]), errors="coerce"
        ).iloc[0]
        records.append(
            {
                "score": score,
                "confidence": confidence,
                "level": level,
                "samples": int(matched.get("samples", 0) or 0),
                "effective_samples": float(matched.get("effective_samples", 0.0) or 0.0),
                "mean_net_excess20": mean_excess,
                "win_rate_net_excess20": win_rate,
                "start_date": str(matched.get("start_date", "") or ""),
                "end_date": str(matched.get("end_date", "") or ""),
            }
        )
    return pd.DataFrame.from_records(records, index=frame.index)
'''
    replace_between(
        path,
        "def calibration_details_for_frame(\n",
        "\ndef calibration_scores_for_frame(\n",
        replacement,
        "indexed calibration details",
    )


def update_report() -> None:
    path = ROOT / "report.py"
    text = path.read_text(encoding="utf-8")

    start = text.find("def _diversify_ranked_candidates(\n")
    end = text.find("\ndef _sort_export_rows(\n", start)
    if start < 0 or end < 0:
        raise RuntimeError("missing diversify function")
    old = text[start:end]
    helper = r'''def _ensure_diversity_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Populate expensive ETF/theme provenance once and preserve existing values."""
    working = frame.copy()
    asset_type = working.get(
        "AssetType", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf = working.get(
        "IsETF", pd.Series(False, index=working.index)
    ).map(_truthy) | asset_type.eq("etf")

    if "ETFTheme" not in working:
        working["ETFTheme"] = ""
    missing_theme = is_etf & working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        working.loc[missing_theme, "ETFTheme"] = working.loc[missing_theme].apply(
            _etf_theme_key, axis=1
        )

    if "ETFTrackingKey" not in working:
        working["ETFTrackingKey"] = ""
    missing_tracking = is_etf & working["ETFTrackingKey"].fillna("").astype(str).str.strip().eq("")
    if missing_tracking.any():
        working.loc[missing_tracking, "ETFTrackingKey"] = working.loc[missing_tracking].apply(
            lambda row: etf_tracking_key(
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector="",
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )

    if "ThemeCluster" not in working:
        working["ThemeCluster"] = ""
    missing_cluster = working["ThemeCluster"].fillna("").astype(str).str.strip().eq("")
    if missing_cluster.any():
        working.loc[missing_cluster, "ThemeCluster"] = working.loc[missing_cluster].apply(
            lambda row: theme_cluster(
                is_etf=_truthy(row.get("IsETF", False))
                or str(row.get("AssetType", "")).strip().lower() == "etf",
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector=row.get("Sector", ""),
                classification=row.get("ModelClassification", ""),
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )
    return working


''' + old.replace(
        '    working = frame.copy()\n    working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)\n    working["ETFTrackingKey"] = working.apply(\n        lambda row: etf_tracking_key(\n            name=row.get("Name", ""), industry=row.get("Industry", ""),\n            sector="", ticker=row.get("Ticker", "")\n        ) if _truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf" else "",\n        axis=1,\n    )\n    working["ThemeCluster"] = working.apply(\n        lambda row: theme_cluster(\n            is_etf=_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf",\n            name=row.get("Name", ""), industry=row.get("Industry", ""), sector=row.get("Sector", ""),\n            classification=row.get("ModelClassification", ""), ticker=row.get("Ticker", ""),\n        ), axis=1,\n    )\n',
        '    working = _ensure_diversity_columns(frame)\n',
        1,
    )
    if helper == old:
        raise RuntimeError("failed to rewrite diversify preparation")
    text = text[:start] + helper + text[end:]

    old_projection_start = text.find("def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:\n")
    old_projection_end = text.find("\n\ndef write_decision_results(\n", old_projection_start)
    if old_projection_start < 0 or old_projection_end < 0:
        raise RuntimeError("missing decision projection")
    projection = r'''def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:
    # Project first so the GUI path never copies the 200+ column research frame.
    working = frame.reindex(columns=DECISION_RESULT_COLUMNS).copy()
    missing_theme = working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        inferred = working.loc[missing_theme].apply(_etf_theme_key, axis=1)
        ticker = working.loc[missing_theme, "Ticker"].fillna("").astype(str).str.strip()
        classification = (
            working.loc[missing_theme, "ModelClassification"]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        inferred_text = inferred.fillna("").astype(str).str.strip()
        generic = inferred_text.eq("") | inferred_text.eq(ticker)
        inferred_text = inferred_text.where(
            ~generic | classification.eq(""), classification
        )
        working.loc[missing_theme, "ETFTheme"] = inferred_text
    return working
'''
    text = text[:old_projection_start] + projection + text[old_projection_end:]

    marker = '    ranked = _rank_valid_candidates(frame)\n'
    if marker not in text:
        raise RuntimeError("missing ranked candidate marker")
    text = text.replace(
        marker,
        marker + '    ranked = _ensure_diversity_columns(ranked)\n',
        1,
    )
    path.write_text(text, encoding="utf-8")


def update_analytics() -> None:
    path = ROOT / "analytics.py"
    text = path.read_text(encoding="utf-8")
    old = '    cache_hit_rate: float = 0.0\n\n    def to_dict(self) -> dict[str, Any]:\n'
    new = '''    cache_hit_rate: float = 0.0
    calibration_lookup_elapsed_seconds: float = 0.0
    ranking_compute_elapsed_seconds: float = 0.0
    persistence_elapsed_seconds: float = 0.0
    postprocess_elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
'''
    if old not in text:
        raise RuntimeError("missing BacktestSummary timing marker")
    text = text.replace(old, new, 1)

    marker = '            # FAST/EXACT candidates now estimate a bounded bridge correction so\n            # the global prior is closer to the exact execution distribution.\n\n    prior_institutional_score = pd.to_numeric(\n'
    if marker not in text:
        raise RuntimeError("missing apply postprocess marker")
    text = text.replace(
        marker,
        marker.replace(
            '\n    prior_institutional_score',
            '\n    postprocess_started = time.perf_counter()\n\n    prior_institutional_score',
        ),
        1,
    )

    old = '''    calibration_details = calibration_details_for_frame(
        frame, getattr(summary, "global_calibration", None)
    )
'''
    new = '''    calibration_started = time.perf_counter()
    calibration_details = calibration_details_for_frame(
        frame, getattr(summary, "global_calibration", None)
    )
    summary.calibration_lookup_elapsed_seconds = float(
        time.perf_counter() - calibration_started
    )
'''
    if old not in text:
        raise RuntimeError("missing calibration call")
    text = text.replace(old, new, 1)

    old_end = '''    frame = finalize_signal_ranking(frame)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    from report import refresh_candidate_exports

    refresh_candidate_exports(frame, top_n_csv=top_n, output_dir=OUTPUT_DIR)
    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)
'''
    new_end = '''    frame = finalize_signal_ranking(frame)
    summary.ranking_compute_elapsed_seconds = float(
        time.perf_counter() - postprocess_started
    )
    persistence_started = time.perf_counter()
    from report import _atomic_write_csv, _atomic_write_parquet, refresh_candidate_exports

    _atomic_write_csv(frame, path)
    refresh_candidate_exports(frame, top_n_csv=top_n, output_dir=OUTPUT_DIR)
    _atomic_write_parquet(frame, OUTPUT_DIR / "AllResults.parquet")
    summary.persistence_elapsed_seconds = float(
        time.perf_counter() - persistence_started
    )
    summary.postprocess_elapsed_seconds = float(
        summary.ranking_compute_elapsed_seconds + summary.persistence_elapsed_seconds
    )
    logger.info(
        "Backtest postprocess: calibration=%.2fs, ranking=%.2fs, persistence=%.2fs, total=%.2fs.",
        summary.calibration_lookup_elapsed_seconds,
        summary.ranking_compute_elapsed_seconds,
        summary.persistence_elapsed_seconds,
        summary.postprocess_elapsed_seconds,
    )
'''
    if old_end not in text:
        raise RuntimeError("missing ranking persistence block")
    text = text.replace(old_end, new_end, 1)
    path.write_text(text, encoding="utf-8")


def update_scanner() -> None:
    path = ROOT / "scanner.py"
    text = path.read_text(encoding="utf-8")
    old = '''    elapsed_seconds: float = 0.0
    timestamp: str = field(
'''
    new = '''    elapsed_seconds: float = 0.0
    download_seconds: float = 0.0
    analysis_seconds: float = 0.0
    enrichment_seconds: float = 0.0
    timestamp: str = field(
'''
    if old not in text:
        raise RuntimeError("missing ScanReport timing marker")
    text = text.replace(old, new, 1)
    old = '''        passed_filters=passed,
        elapsed_seconds=elapsed,
    )
'''
    new = '''        passed_filters=passed,
        elapsed_seconds=elapsed,
        download_seconds=download_elapsed,
        analysis_seconds=analysis_elapsed,
        enrichment_seconds=enrichment_elapsed,
    )
'''
    if old not in text:
        raise RuntimeError("missing ScanReport construction marker")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def update_scan_service() -> None:
    path = ROOT / "scan_service.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import threading\n", "import threading\nimport time\n", 1)
    old = '''    stock_count: int
    etf_count: int
'''
    new = '''    stock_count: int
    etf_count: int
    prepare_seconds: float = 0.0
    fundamentals_seconds: float = 0.0
    scan_seconds: float = 0.0
    export_seconds: float = 0.0
    elapsed_seconds: float = 0.0
'''
    if old not in text:
        raise RuntimeError("missing ScanExecutionResult marker")
    text = text.replace(old, new, 1)

    old = '''    log = logger or logging.getLogger("institution_scanner")
    _emit_progress(progress_callback, "prepare", 0, 0, "正在准备股票池")
    stocks, etfs = prepare_universe(
'''
    new = '''    log = logger or logging.getLogger("institution_scanner")
    execution_started = time.perf_counter()
    prepare_started = time.perf_counter()
    _emit_progress(progress_callback, "prepare", 0, 0, "正在准备股票池")
    stocks, etfs = prepare_universe(
'''
    if old not in text:
        raise RuntimeError("missing scan service prepare marker")
    text = text.replace(old, new, 1)
    marker = '''    _emit_progress(
        progress_callback,
        "prepare",
        0,
        0,
        f"股票池准备完成：股票 {len(stocks)} · ETF {len(etfs)}；正在检查基本面",
    )
'''
    if marker not in text:
        raise RuntimeError("missing prepare completion marker")
    text = text.replace(marker, marker + '    prepare_seconds = time.perf_counter() - prepare_started\n    fundamentals_started = time.perf_counter()\n', 1)
    old = '''        refresh_fundamentals_if_needed(
            stocks,
            request.refresh_fundamentals,
            log,
            fundamental_path_fn=fundamental_path_fn,
            refresh_fundamentals_fn=refresh_fundamentals_fn,
        )
    report = run_scan_fn(
'''
    new = '''        refresh_fundamentals_if_needed(
            stocks,
            request.refresh_fundamentals,
            log,
            fundamental_path_fn=fundamental_path_fn,
            refresh_fundamentals_fn=refresh_fundamentals_fn,
        )
    fundamentals_seconds = time.perf_counter() - fundamentals_started
    scan_started = time.perf_counter()
    report = run_scan_fn(
'''
    if old not in text:
        raise RuntimeError("missing fundamentals completion marker")
    text = text.replace(old, new, 1)
    old = '''        cancel_event=cancel_event,
    )
    _emit_progress(
'''
    new = '''        cancel_event=cancel_event,
    )
    scan_seconds = time.perf_counter() - scan_started
    export_started = time.perf_counter()
    _emit_progress(
'''
    if old not in text:
        raise RuntimeError("missing scan completion marker")
    text = text.replace(old, new, 1)
    old = '''    _emit_progress(
        progress_callback,
        "export",
        len(report.results),
        len(report.results),
        "结果文件写入完成",
    )
    return ScanExecutionResult(
'''
    new = '''    _emit_progress(
        progress_callback,
        "export",
        len(report.results),
        len(report.results),
        "结果文件写入完成",
    )
    export_seconds = time.perf_counter() - export_started
    elapsed_seconds = time.perf_counter() - execution_started
    return ScanExecutionResult(
'''
    if old not in text:
        raise RuntimeError("missing export completion marker")
    text = text.replace(old, new, 1)
    old = '''        stock_count=len(stocks),
        etf_count=len(etfs),
    )
'''
    new = '''        stock_count=len(stocks),
        etf_count=len(etfs),
        prepare_seconds=prepare_seconds,
        fundamentals_seconds=fundamentals_seconds,
        scan_seconds=scan_seconds,
        export_seconds=export_seconds,
        elapsed_seconds=elapsed_seconds,
    )
'''
    if old not in text:
        raise RuntimeError("missing scan result timings")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def update_main() -> None:
    path = ROOT / "main.py"
    text = path.read_text(encoding="utf-8")
    marker = '    report = execution.report\n\n    if report.successful == 0:\n'
    if marker not in text:
        raise RuntimeError("missing cmd_scan report marker")
    addition = r'''    report = execution.report

    scan_performance = {
        "total_seconds": round(float(getattr(execution, "elapsed_seconds", 0.0) or 0.0), 4),
        "universe_seconds": round(float(getattr(execution, "prepare_seconds", 0.0) or 0.0), 4),
        "fundamentals_seconds": round(float(getattr(execution, "fundamentals_seconds", 0.0) or 0.0), 4),
        "scan_core_seconds": round(float(getattr(execution, "scan_seconds", 0.0) or 0.0), 4),
        "download_seconds": round(float(getattr(report, "download_seconds", 0.0) or 0.0), 4),
        "analysis_seconds": round(float(getattr(report, "analysis_seconds", 0.0) or 0.0), 4),
        "enrichment_seconds": round(float(getattr(report, "enrichment_seconds", 0.0) or 0.0), 4),
        "export_seconds": round(float(getattr(execution, "export_seconds", 0.0) or 0.0), 4),
        "successful": int(getattr(report, "successful", 0) or 0),
        "failed": int(getattr(report, "failed", 0) or 0),
        "stocks": int(getattr(execution, "stock_count", 0) or 0),
        "etfs": int(getattr(execution, "etf_count", 0) or 0),
    }
    scan_performance_path = OUTPUT_DIR / "ScanPerformance.json"
    temporary_scan_performance = scan_performance_path.with_name(".ScanPerformance.json.tmp")
    temporary_scan_performance.write_text(
        json.dumps(scan_performance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary_scan_performance, scan_performance_path)

    if report.successful == 0:
'''
    text = text.replace(marker, addition, 1)
    path.write_text(text, encoding="utf-8")


def update_daily_pipeline() -> None:
    path = ROOT / "daily_pipeline.py"
    text = path.read_text(encoding="utf-8")
    old = '''    "BacktestSummary.json",
    "DailyRunSummary.json",
)
'''
    new = '''    "BacktestSummary.json",
    "ScanPerformance.json",
    "DailyRunSummary.json",
)
'''
    if old not in text:
        raise RuntimeError("missing archive files marker")
    text = text.replace(old, new, 1)
    old = '    backtest = _read_json(OUTPUT_DIR / "BacktestSummary.json")\n'
    new = '    backtest = _read_json(OUTPUT_DIR / "BacktestSummary.json")\n    scan_performance = _read_json(OUTPUT_DIR / "ScanPerformance.json")\n'
    if old not in text:
        raise RuntimeError("missing manifest backtest marker")
    text = text.replace(old, new, 1)
    old = '''        "stage_seconds": {
            key: round(float(value), 3) for key, value in stage_seconds.items()
        },
        "backtest": {
'''
    new = '''        "stage_seconds": {
            key: round(float(value), 3) for key, value in stage_seconds.items()
        },
        "scan_breakdown": {
            "total_seconds": round(float(scan_performance.get("total_seconds", stage_seconds.get("scan", 0.0)) or 0.0), 3),
            "universe_seconds": round(float(scan_performance.get("universe_seconds", 0.0) or 0.0), 3),
            "fundamentals_seconds": round(float(scan_performance.get("fundamentals_seconds", 0.0) or 0.0), 3),
            "download_seconds": round(float(scan_performance.get("download_seconds", 0.0) or 0.0), 3),
            "analysis_seconds": round(float(scan_performance.get("analysis_seconds", 0.0) or 0.0), 3),
            "enrichment_seconds": round(float(scan_performance.get("enrichment_seconds", 0.0) or 0.0), 3),
            "export_seconds": round(float(scan_performance.get("export_seconds", 0.0) or 0.0), 3),
        },
        "backtest": {
'''
    if old not in text:
        raise RuntimeError("missing manifest stage marker")
    text = text.replace(old, new, 1)
    old = '''            "exact_elapsed_seconds": round(exact_elapsed, 3),
        },
'''
    new = '''            "exact_elapsed_seconds": round(exact_elapsed, 3),
            "calibration_lookup_seconds": round(float(backtest.get("calibration_lookup_elapsed_seconds", 0.0) or 0.0), 3),
            "ranking_compute_seconds": round(float(backtest.get("ranking_compute_elapsed_seconds", 0.0) or 0.0), 3),
            "persistence_seconds": round(float(backtest.get("persistence_elapsed_seconds", 0.0) or 0.0), 3),
            "postprocess_seconds": round(
                max(
                    float(backtest.get("postprocess_elapsed_seconds", 0.0) or 0.0),
                    float(stage_seconds.get("backtest", 0.0) or 0.0) - backtest_elapsed,
                    0.0,
                ),
                3,
            ),
        },
'''
    if old not in text:
        raise RuntimeError("missing manifest backtest detail marker")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def update_gui() -> None:
    path = ROOT / "gui.py"
    text = path.read_text(encoding="utf-8")
    insert_after = 'DAILY_PIPELINE_FILE = Path(__file__).resolve().with_name("daily_pipeline.py")\n\n'
    if insert_after not in text:
        raise RuntimeError("missing gui helper insertion marker")
    helper = r'''DAILY_PIPELINE_FILE = Path(__file__).resolve().with_name("daily_pipeline.py")


def _duration_label(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"

'''
    text = text.replace(insert_after, helper, 1)
    text = text.replace(
        '    body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)\n',
        '    body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)\n    self.body_paned = body\n',
        1,
    )
    result_marker = '''    ctk.CTkLabel(
        result_bar,
        textvariable=self.result_summary,
        font=("Microsoft YaHei UI", 9),
        text_color="#64748b",
    ).grid(row=0, column=1, padx=(12, 0), sticky="w")
'''
    if result_marker not in text:
        raise RuntimeError("missing result summary marker")
    text = text.replace(
        result_marker,
        result_marker + '''    self.detail_toggle_button = ctk.CTkButton(
        result_bar,
        text="详情 ‹",
        width=72,
        height=28,
        fg_color="transparent",
        hover_color="#eef4fb",
        text_color="#334e68",
        border_width=1,
        border_color="#d7e2ee",
        command=self._toggle_detail_panel,
    )
    self.detail_toggle_button.grid(row=0, column=2, padx=(8, 0), sticky="e")
''',
        1,
    )
    text = text.replace(
        '    detail = ctk.CTkFrame(body, width=310, corner_radius=10, fg_color="#ffffff")\n',
        '    detail = ctk.CTkFrame(body, width=280, corner_radius=10, fg_color="#ffffff")\n    self.detail_panel = detail\n',
        1,
    )
    text = text.replace('        wraplength=270,\n', '        wraplength=240,\n', 1)
    text = text.replace(
        '    body.add(detail, weight=1)\n\n    # Footer / backtest scope',
        '    body.add(detail, weight=1)\n\n    # Footer / backtest scope',
        1,
    )
    footer_marker = '''    ctk.CTkLabel(
        footer,
        textvariable=self.run_quality,
        text_color="#52677d",
        font=("Microsoft YaHei UI", 9),
    ).pack(side=tk.LEFT, padx=(2, 8), pady=9)
'''
    if footer_marker not in text:
        raise RuntimeError("missing footer run quality marker")
    text = text.replace(
        footer_marker,
        footer_marker + '''    ctk.CTkButton(
        footer,
        text="性能详情",
        width=76,
        height=28,
        fg_color="transparent",
        hover_color="#eef4fb",
        text_color="#334e68",
        border_width=1,
        border_color="#d7e2ee",
        command=self._show_run_performance,
    ).pack(side=tk.LEFT, padx=(0, 8), pady=7)
''',
        1,
    )
    text = text.replace(
        '        self._log_visible = False\n        super().__init__(root)\n',
        '        self._log_visible = False\n        self._detail_visible = True\n        self._run_performance_payload: dict[str, object] = {}\n        super().__init__(root)\n',
        1,
    )
    text = text.replace(
        '        self.root.bind("<Control-Shift-R>", lambda _event: self.start_daily_pipeline())\n',
        '        self.root.bind("<Control-Shift-R>", lambda _event: self.start_daily_pipeline())\n        self.root.bind("<Control-d>", lambda _event: self._toggle_detail_panel())\n',
        1,
    )
    toggle_marker = '''    def _show_log_for_error(self) -> None:
        if not self._log_visible:
            self._toggle_log()

    # Scan modes'''
    if toggle_marker not in text:
        raise RuntimeError("missing gui toggle marker")
    toggle_code = '''    def _show_log_for_error(self) -> None:
        if not self._log_visible:
            self._toggle_log()

    def _toggle_detail_panel(self) -> None:
        pane = getattr(self, "body_paned", None)
        detail = getattr(self, "detail_panel", None)
        button = getattr(self, "detail_toggle_button", None)
        if pane is None or detail is None:
            return
        visible = bool(getattr(self, "_detail_visible", True))
        if visible:
            try:
                pane.forget(detail)
            except Exception:
                return
            self._detail_visible = False
            if button is not None:
                button.configure(text="详情 ›")
        else:
            try:
                pane.add(detail, weight=1)
            except Exception:
                return
            self._detail_visible = True
            if button is not None:
                button.configure(text="详情 ‹")

    # Scan modes'''
    text = text.replace(toggle_marker, toggle_code, 1)

    start = text.find("    def _update_run_quality_summary(self) -> None:\n")
    end = text.find("\n    # Dashboard cards / decision card", start)
    if start < 0 or end < 0:
        raise RuntimeError("missing run quality method")
    methods = r'''    def _update_run_quality_summary(self) -> None:
        path = OUTPUT_DIR / "DailyRunSummary.json"
        if not path.exists() or not hasattr(self, "run_quality"):
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self._run_performance_payload = payload
        expected = str(payload.get("expected_trading_date", "") or "-")
        stages = payload.get("stage_seconds", {}) if isinstance(payload.get("stage_seconds", {}), dict) else {}
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        elapsed = float(payload.get("elapsed_seconds", 0.0) or 0.0)
        scan_seconds = float(stages.get("scan", 0.0) or 0.0)
        engine_seconds = float(backtest.get("elapsed_seconds", 0.0) or 0.0)
        postprocess_seconds = float(backtest.get("postprocess_seconds", 0.0) or 0.0)
        if postprocess_seconds <= 0:
            postprocess_seconds = max(
                0.0,
                float(stages.get("backtest", 0.0) or 0.0) - engine_seconds,
            )
        cache = float(backtest.get("cache_hit_rate", 0.0) or 0.0)
        self.run_quality.set(
            f"✓ {expected} · 总{_duration_label(elapsed)} · 扫描{_duration_label(scan_seconds)} · "
            f"引擎{_duration_label(engine_seconds)} · 后处理{_duration_label(postprocess_seconds)} · Cache {cache:.0%}"
        )

    def _show_run_performance(self) -> None:
        payload = dict(getattr(self, "_run_performance_payload", {}) or {})
        if not payload:
            self._update_run_quality_summary()
            payload = dict(getattr(self, "_run_performance_payload", {}) or {})
        if not payload:
            _core.messagebox.showinfo("运行性能", "还没有可用的 DailyRunSummary.json。")
            return
        universe = payload.get("universe", {}) if isinstance(payload.get("universe", {}), dict) else {}
        freshness = payload.get("freshness", {}) if isinstance(payload.get("freshness", {}), dict) else {}
        scan = payload.get("scan_breakdown", {}) if isinstance(payload.get("scan_breakdown", {}), dict) else {}
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        lines = [
            f"状态：{payload.get('publish_status', '-')}",
            f"交易日：{payload.get('expected_trading_date', '-')}",
            f"总耗时：{_duration_label(float(payload.get('elapsed_seconds', 0.0) or 0.0))}",
            f"标的：{int(universe.get('rows', 0) or 0)} · 股票 {int(universe.get('stocks', 0) or 0)} · ETF {int(universe.get('etfs', 0) or 0)}",
            f"最新覆盖：{float(freshness.get('all_results_ratio', 0.0) or 0.0):.2%}",
            "",
            "扫描阶段",
            f"  股票池：{_duration_label(float(scan.get('universe_seconds', 0.0) or 0.0))}",
            f"  基本面：{_duration_label(float(scan.get('fundamentals_seconds', 0.0) or 0.0))}",
            f"  行情更新：{_duration_label(float(scan.get('download_seconds', 0.0) or 0.0))}",
            f"  指标分析：{_duration_label(float(scan.get('analysis_seconds', 0.0) or 0.0))}",
            f"  评分增强：{_duration_label(float(scan.get('enrichment_seconds', 0.0) or 0.0))}",
            f"  扫描导出：{_duration_label(float(scan.get('export_seconds', 0.0) or 0.0))}",
            "",
            "回测阶段",
            f"  FAST：{int(backtest.get('fast_screen_tickers', 0) or 0)}",
            f"  EXACT：{int(backtest.get('exact_refinement_tickers', 0) or 0)}",
            f"  回测引擎：{_duration_label(float(backtest.get('elapsed_seconds', 0.0) or 0.0))}",
            f"  校准查表：{_duration_label(float(backtest.get('calibration_lookup_seconds', 0.0) or 0.0))}",
            f"  排名计算：{_duration_label(float(backtest.get('ranking_compute_seconds', 0.0) or 0.0))}",
            f"  文件落盘：{_duration_label(float(backtest.get('persistence_seconds', 0.0) or 0.0))}",
            f"  后处理：{_duration_label(float(backtest.get('postprocess_seconds', 0.0) or 0.0))}",
            f"  Cache：{float(backtest.get('cache_hit_rate', 0.0) or 0.0):.2%}",
        ]
        _core.messagebox.showinfo("本轮运行性能", "\n".join(lines))
'''
    text = text[:start] + methods + text[end:]
    path.write_text(text, encoding="utf-8")


def write_tests() -> None:
    path = ROOT / "test_v30_performance_workstation.py"
    path.write_text(r'''from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import config
import gui
import model_calibration
import report
import scan_service
import scanner


class V30PerformanceWorkstationTests(unittest.TestCase):
    def test_scoring_model_stays_v24_while_engineering_moves_to_v30(self):
        self.assertEqual(config.SCORING_VERSION, "2026-08-09-v24-decision-integrity")
        self.assertIn("v30", config.PIPELINE_VERSION)
        self.assertIn("v30", config.GUI_VERSION)

    def test_calibration_details_uses_indexed_lookup_not_rowwise_resolver(self):
        rows = [
            {
                "level": "asset_signal",
                "asset_type": "etf",
                "entry_signal": "WAIT_PULLBACK",
                "calibration_score": 63.0,
                "confidence": 0.5,
                "samples": 40,
                "effective_samples": 20.0,
                "mean_net_excess20": 1.2,
                "win_rate_net_excess20": 0.6,
                "start_date": "2025-01-01",
                "end_date": "2026-01-01",
            },
            {
                "level": "global",
                "calibration_score": 48.0,
                "confidence": 0.2,
                "samples": 100,
                "effective_samples": 70.0,
            },
        ]
        frame = pd.DataFrame(
            {
                "AssetType": ["etf", "stock"],
                "EntrySignal": ["WAIT_PULLBACK", "BUY_NOW"],
                "FinalScore": [60.0, 55.0],
                "BaseScore": [58.0, 52.0],
                "MarketRegime": ["震荡", "震荡"],
            }
        )
        with patch.object(
            model_calibration,
            "resolve_global_calibration",
            side_effect=AssertionError("rowwise resolver should not be called"),
        ):
            details = model_calibration.calibration_details_for_frame(frame, rows)
        self.assertEqual(details.loc[0, "level"], "asset_signal")
        self.assertEqual(details.loc[0, "score"], 63.0)
        self.assertEqual(details.loc[0, "samples"], 40)
        self.assertEqual(details.loc[1, "level"], "global")
        self.assertEqual(details.loc[1, "score"], 48.0)

    def test_diversity_preparation_preserves_complete_cached_classification(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "159915.SZ",
                    "Name": "消费ETF",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ETFTheme": "消费",
                    "ETFTrackingKey": "消费ETF",
                    "ThemeCluster": "消费",
                    "RankingScore": 50.0,
                }
            ]
        )
        with patch.object(report, "_etf_theme_key", side_effect=AssertionError("should reuse theme")):
            prepared = report._ensure_diversity_columns(frame)
        self.assertEqual(prepared.loc[0, "ETFTheme"], "消费")
        self.assertEqual(prepared.loc[0, "ETFTrackingKey"], "消费ETF")
        self.assertEqual(prepared.loc[0, "ThemeCluster"], "消费")

    def test_decision_projection_discards_wide_research_columns_first(self):
        row = {
            "Ticker": "000001.SZ",
            "Name": "测试",
            "AssetType": "stock",
            "Industry": "银行",
            "RankingScore": 42.0,
        }
        row.update({f"ResearchJunk{i}": i for i in range(300)})
        projected = report._decision_projection(pd.DataFrame([row]))
        self.assertEqual(tuple(projected.columns), report.DECISION_RESULT_COLUMNS)
        self.assertNotIn("ResearchJunk1", projected.columns)

    def test_scan_reports_expose_stage_timings(self):
        scan_report = scanner.ScanReport(
            download_seconds=1.0,
            analysis_seconds=2.0,
            enrichment_seconds=3.0,
        )
        result = scan_service.ScanExecutionResult(
            report=scan_report,
            top_csv=report.OUTPUT_DIR / "Top50.csv",
            top_parquet=report.OUTPUT_DIR / "Top200.parquet",
            full_csv=report.OUTPUT_DIR / "AllResults.csv",
            full_parquet=report.OUTPUT_DIR / "AllResults.parquet",
            stock_count=1,
            etf_count=1,
            prepare_seconds=0.1,
            fundamentals_seconds=0.2,
            scan_seconds=6.0,
            export_seconds=0.3,
            elapsed_seconds=6.6,
        )
        self.assertEqual(result.scan_seconds, 6.0)
        self.assertEqual(result.report.analysis_seconds, 2.0)

    def test_backtest_summary_has_postprocess_observability(self):
        summary = analytics.BacktestSummary()
        self.assertEqual(summary.calibration_lookup_elapsed_seconds, 0.0)
        self.assertEqual(summary.ranking_compute_elapsed_seconds, 0.0)
        self.assertEqual(summary.persistence_elapsed_seconds, 0.0)
        self.assertEqual(summary.postprocess_elapsed_seconds, 0.0)

    def test_gui_has_collapsible_detail_and_performance_dialog(self):
        self.assertTrue(hasattr(gui.DecisionScannerGUI, "_toggle_detail_panel"))
        self.assertTrue(hasattr(gui.DecisionScannerGUI, "_show_run_performance"))
        self.assertEqual(gui._duration_label(95), "1m35s")


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")


def main() -> None:
    update_config()
    update_model_calibration()
    update_report()
    update_analytics()
    update_scanner()
    update_scan_service()
    update_main()
    update_daily_pipeline()
    update_gui()
    write_tests()
    print("v30 performance workstation patch applied")


if __name__ == "__main__":
    main()
