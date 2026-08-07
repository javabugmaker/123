from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start_index] + replacement + text[end_index:]


def patch_config() -> None:
    path = ROOT / "config.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'SCORING_VERSION: str = "2026-08-08-v15-ranking-diversity-metadata"',
        'SCORING_VERSION: str = "2026-08-08-v16-model-v2-oos"',
        "config scoring version",
    )
    anchor = 'BACKTEST_NEUTRAL_SCORE: Final[float] = 50.0\n'
    addition = '''BACKTEST_NEUTRAL_SCORE: Final[float] = 50.0\n\n# Model v2 separates setup, trigger and execution so the same trend/volume\n# evidence is not rewarded repeatedly.  A validated OOS calibration file may\n# override these defaults within the bounded ranges enforced by score.py.\nMODEL_SETUP_WEIGHT: Final[float] = 0.60\nMODEL_TRIGGER_WEIGHT: Final[float] = 0.25\nMODEL_EXECUTION_WEIGHT: Final[float] = 0.15\nMODEL_QUALITY_WEIGHT: Final[float] = 0.30\nGLOBAL_CALIBRATION_MIN_SAMPLES: Final[int] = 30\nGLOBAL_CALIBRATION_MAX_WEIGHT: Final[float] = 0.15\n'''
    text = replace_once(text, anchor, addition, "config model constants")
    path.write_text(text, encoding="utf-8")


def patch_gui_inheritance() -> None:
    core_path = ROOT / "gui_core.py"
    core = core_path.read_text(encoding="utf-8")
    core = replace_once(
        core,
        '''def main() -> None:\n    root = tk.Tk()\n    ScannerGUI(root)\n    root.mainloop()\n''',
        '''def main(gui_class: type[ScannerGUI] = ScannerGUI) -> None:\n    root = tk.Tk()\n    gui_class(root)\n    root.mainloop()\n''',
        "gui_core main factory",
    )
    core_path.write_text(core, encoding="utf-8")

    path = ROOT / "gui.py"
    text = path.read_text(encoding="utf-8")
    first_patch = '''_core.ScannerGUI._build_ui = _build_ui_v16\n_core.ScannerGUI._update_filter_values = _update_filter_values_v16\n_core.ScannerGUI._row_matches_filters = _row_matches_filters_v16\n_core.ScannerGUI.clear_filters = _clear_filters_v16\n_core.ScannerGUI._format_table_value = _format_table_value_v16\n'''
    text = replace_once(text, first_patch, "", "remove gui monkey patch block")
    text = replace_once(
        text,
        '_core.ScannerGUI._update_market_overview = _update_market_overview_decision\n\n',
        '',
        "remove overview monkey patch",
    )
    text = replace_once(
        text,
        '_core.ScannerGUI._render_cached_rows = _render_cached_rows_decision\n\n# Preserve the historical import surface used by tests and external launchers.\nif __name__ == "__main__":\n    _core.main()\nelse:\n    sys.modules[__name__] = _core\n',
        '''class DecisionScannerGUI(_core.ScannerGUI):\n    """Decision-oriented GUI implemented through normal inheritance."""\n\n    def _build_ui(self) -> None:\n        _build_ui_v16(self)\n\n    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:\n        _update_filter_values_v16(self, headers, rows)\n\n    def _row_matches_filters(\n        self,\n        indexes: dict[str, int],\n        row: list[str],\n        query: str,\n        search_text: str | None = None,\n        filter_values: tuple[str, ...] | None = None,\n    ) -> bool:\n        return _row_matches_filters_v16(\n            self, indexes, row, query, search_text, filter_values\n        )\n\n    def clear_filters(self) -> None:\n        _clear_filters_v16(self)\n\n    def _format_table_value(self, column: str, value: str) -> str:\n        return _format_table_value_v16(self, column, value)\n\n    def _update_market_overview(self, rows, indexes) -> None:\n        _update_market_overview_decision(self, rows, indexes)\n\n    def _render_cached_rows(self) -> bool:\n        return _render_cached_rows_decision(self)\n\n\n# Preserve the historical import surface without mutating gui_core.ScannerGUI.\nScannerGUI = DecisionScannerGUI\n\ndef main() -> None:\n    _core.main(gui_class=DecisionScannerGUI)\n\ndef __getattr__(name: str):\n    return getattr(_core, name)\n\nif __name__ == "__main__":\n    main()\n''',
        "install gui subclass",
    )
    path.write_text(text, encoding="utf-8")


def patch_scanner_progress() -> None:
    path = ROOT / "scanner.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, 'import sys\nimport time\n', 'import sys\nimport threading\nimport time\n', "scanner threading import")
    text = replace_once(text, 'from typing import Any\n', 'from typing import Any, Callable\n', "scanner callable import")
    marker = '_SCAN_RECOVERABLE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError)\n\n\n'
    insert = '''_SCAN_RECOVERABLE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError)\n\nScanProgressCallback = Callable[[str, int, int, str], None]\n\n\nclass ScanCancelled(RuntimeError):\n    """Raised when an in-process caller requests cooperative cancellation."""\n\n\ndef _emit_progress(\n    callback: ScanProgressCallback | None,\n    stage: str,\n    current: int,\n    total: int,\n    message: str,\n) -> None:\n    if callback is None:\n        return\n    try:\n        callback(stage, int(current), int(total), str(message))\n    except Exception:\n        logger.debug("Scan progress callback failed.", exc_info=True)\n\n\ndef _raise_if_cancelled(cancel_event: threading.Event | None) -> None:\n    if cancel_event is not None and cancel_event.is_set():\n        raise ScanCancelled("扫描已取消")\n\n\n'''
    text = replace_once(text, marker, insert, "scanner progress helpers")
    old_sig = '''def run_scan(\n    stock_universe: list[TickerInfo] | None = None,\n    etf_universe: list[TickerInfo] | None = None,\n    force_download: bool = False,\n    resume: bool = True,\n    data_source: str = "tickflow",\n    cache_first: bool = False,\n) -> ScanReport:\n    start_time = time.perf_counter()\n'''
    new_sig = '''def run_scan(\n    stock_universe: list[TickerInfo] | None = None,\n    etf_universe: list[TickerInfo] | None = None,\n    force_download: bool = False,\n    resume: bool = True,\n    data_source: str = "tickflow",\n    cache_first: bool = False,\n    progress_callback: ScanProgressCallback | None = None,\n    cancel_event: threading.Event | None = None,\n) -> ScanReport:\n    start_time = time.perf_counter()\n    _raise_if_cancelled(cancel_event)\n    _emit_progress(progress_callback, "prepare", 0, 0, "准备扫描")\n'''
    text = replace_once(text, old_sig, new_sig, "run_scan signature")
    text = replace_once(
        text,
        '''    logger.info(\n        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",\n        len(all_tickers),\n        TICKFLOW_MAX_WORKERS,\n    )\n''',
        '''    logger.info(\n        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",\n        len(all_tickers),\n        TICKFLOW_MAX_WORKERS,\n    )\n    _raise_if_cancelled(cancel_event)\n    _emit_progress(\n        progress_callback, "download", 0, len(all_tickers),\n        f"准备 TickFlow 行情：{len(all_tickers)} 个标的",\n    )\n''',
        "download progress start",
    )
    text = replace_once(
        text,
        '    logger.info("Download phase complete in %.1f seconds.", download_elapsed)\n',
        '''    logger.info("Download phase complete in %.1f seconds.", download_elapsed)\n    _raise_if_cancelled(cancel_event)\n    _emit_progress(\n        progress_callback, "download", len(all_tickers), len(all_tickers),\n        f"行情准备完成，用时 {download_elapsed:.1f}s",\n    )\n''',
        "download progress complete",
    )
    text = replace_once(
        text,
        '''        def submit_next() -> bool:\n            try:\n                ti = next(ticker_iter)\n''',
        '''        def submit_next() -> bool:\n            if cancel_event is not None and cancel_event.is_set():\n                return False\n            try:\n                ti = next(ticker_iter)\n''',
        "analysis cooperative submit",
    )
    text = replace_once(
        text,
        '''            while futures:\n                future = next(as_completed(futures))\n''',
        '''            while futures:\n                _raise_if_cancelled(cancel_event)\n                future = next(as_completed(futures))\n''',
        "analysis cancellation check",
    )
    old_progress = '''                if len(analysed_this_run) % 100 == 0 or len(\n                    analysed_this_run\n                ) == len(analyse_queue):\n                    logger.info(\n                        "ANALYSE progress: %d/%d (%d successful, %d failed).",\n                        completed,\n                        len(analyse_queue),\n                        successful,\n                        failed,\n                    )\n'''
    new_progress = '''                if len(analysed_this_run) % 100 == 0 or len(\n                    analysed_this_run\n                ) == len(analyse_queue):\n                    logger.info(\n                        "ANALYSE progress: %d/%d (%d successful, %d failed).",\n                        completed,\n                        len(analyse_queue),\n                        successful,\n                        failed,\n                    )\n                if completed == len(analyse_queue) or completed % 25 == 0:\n                    _emit_progress(\n                        progress_callback, "analyse", completed, len(analyse_queue),\n                        f"指标分析 {completed}/{len(analyse_queue)} · 成功 {successful} · 失败 {failed}",\n                    )\n'''
    text = replace_once(text, old_progress, new_progress, "analysis progress callback")
    text = replace_once(
        text,
        '    logger.info("Enriching %d scan results...", len(results))\n',
        '''    _raise_if_cancelled(cancel_event)\n    logger.info("Enriching %d scan results...", len(results))\n    _emit_progress(progress_callback, "enrich", 0, len(results), "正在增强评分与排序")\n''',
        "enrichment progress start",
    )
    text = replace_once(
        text,
        '''    logger.info(\n        "Enrichment complete: %d scan results in %.1f seconds.",\n        len(results),\n        enrichment_elapsed,\n    )\n''',
        '''    logger.info(\n        "Enrichment complete: %d scan results in %.1f seconds.",\n        len(results),\n        enrichment_elapsed,\n    )\n    _raise_if_cancelled(cancel_event)\n    _emit_progress(\n        progress_callback, "enrich", len(results), len(results),\n        f"评分增强完成，用时 {enrichment_elapsed:.1f}s",\n    )\n''',
        "enrichment progress complete",
    )
    text = replace_once(
        text,
        '''    logger.info(\n        "Scan complete: %d successful, %d failed, %d passed filters, %.1f seconds.",\n        successful,\n        failed,\n        passed,\n        elapsed,\n    )\n    return report\n''',
        '''    logger.info(\n        "Scan complete: %d successful, %d failed, %d passed filters, %.1f seconds.",\n        successful,\n        failed,\n        passed,\n        elapsed,\n    )\n    _emit_progress(\n        progress_callback, "complete", len(all_tickers), len(all_tickers),\n        f"扫描完成：成功 {successful} · 失败 {failed} · 用时 {elapsed:.1f}s",\n    )\n    return report\n''',
        "scan complete callback",
    )
    path.write_text(text, encoding="utf-8")


def patch_score_model() -> None:
    path = ROOT / "score.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, 'import logging\n', 'import json\nimport logging\n', "score json import")
    text = replace_once(
        text,
        '    LOG_DIR,\n    SCORING_WEIGHTS,\n',
        '    LOG_DIR,\n    MODEL_EXECUTION_WEIGHT,\n    MODEL_SETUP_WEIGHT,\n    MODEL_TRIGGER_WEIGHT,\n    OUTPUT_DIR,\n    SCORING_WEIGHTS,\n',
        "score model config imports",
    )
    text = replace_once(
        text,
        '    entry_score: float = 0.0\n    value_trap_risk: float = 0.0\n',
        '    entry_score: float = 0.0\n    execution_score: float = 0.0\n    value_trap_risk: float = 0.0\n',
        "score breakdown execution field",
    )
    insertion_marker = 'def _is_finite(value: Any) -> bool:\n'
    weight_helpers = '''_MODEL_WEIGHT_CACHE: tuple[float, float, float] | None = None\n\n\ndef _model_component_weights() -> tuple[float, float, float]:\n    global _MODEL_WEIGHT_CACHE\n    if _MODEL_WEIGHT_CACHE is not None:\n        return _MODEL_WEIGHT_CACHE\n    defaults = (MODEL_SETUP_WEIGHT, MODEL_TRIGGER_WEIGHT, MODEL_EXECUTION_WEIGHT)\n    path = OUTPUT_DIR / "ScoreCalibration.json"\n    try:\n        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}\n        if not bool(payload.get("accepted", False)):\n            raise ValueError("calibration not accepted")\n        setup = float(payload.get("setup_weight"))\n        trigger = float(payload.get("trigger_weight"))\n        execution = float(payload.get("execution_weight"))\n        if not (0.45 <= setup <= 0.70 and 0.15 <= trigger <= 0.35 and 0.10 <= execution <= 0.25):\n            raise ValueError("calibration outside guard rails")\n        if abs(setup + trigger + execution - 1.0) > 1e-6:\n            raise ValueError("calibration weights must sum to one")\n        _MODEL_WEIGHT_CACHE = (setup, trigger, execution)\n    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):\n        _MODEL_WEIGHT_CACHE = defaults\n    return _MODEL_WEIGHT_CACHE\n\n\ndef model_weight_signature() -> str:\n    setup, trigger, execution = _model_component_weights()\n    return f"{setup:.4f}:{trigger:.4f}:{execution:.4f}"\n\n\n'''
    text = replace_once(text, insertion_marker, weight_helpers + insertion_marker, "score weight loader")

    new_value_trap = '''def value_trap_risk(df: pd.DataFrame, is_etf: bool = False) -> float:\n    """Estimate deterioration risk without treating a low price as a trap by itself.\n\n    Model v2 rewards genuine bottom recovery and penalises *continued* lower lows,\n    failed rebounds and absent money-flow confirmation.  This removes the old\n    contradiction where being below MA200 both helped the setup score and heavily\n    penalised the same ticker as a value trap.\n    """\n    close = _series(df, "Close")\n    volume = _series(df, "Volume")\n    clean_close = close.dropna()\n    if len(clean_close) < 121:\n        return 0.0\n\n    risk = 0.0\n    price = _latest(df, "Close")\n    ma20 = _latest(df, "MA20")\n    ma50 = _latest(df, "MA50")\n    ret20 = _safe_return(close, 20)\n    ret60 = _safe_return(close, 60)\n    ret120 = _safe_return(close, 120)\n\n    # Persistent deterioration rather than absolute low-price location.\n    if _is_finite(ret120) and ret120 < 0:\n        risk += _clamp(abs(ret120) / 45.0) * 15.0\n    if _is_finite(ma50) and len(df) >= 25 and "MA50" in df:\n        old_ma50 = _series(df, "MA50").iloc[-25] if len(_series(df, "MA50")) >= 25 else np.nan\n        if _is_finite(old_ma50) and old_ma50 > 0 and ma50 < old_ma50:\n            risk += _clamp((old_ma50 - ma50) / old_ma50 / 0.12) * 12.0\n        if _is_finite(price) and price < ma50 and _is_finite(ret20) and ret20 < 0:\n            risk += 8.0\n\n    recent_low = float(clean_close.iloc[-40:].min())\n    prior_low = float(clean_close.iloc[-80:-40].min()) if len(clean_close) >= 80 else recent_low\n    if prior_low > 0 and recent_low < prior_low * 0.98:\n        risk += _clamp((prior_low - recent_low) / prior_low / 0.12) * 15.0\n\n    if _is_finite(ret20) and _is_finite(ret60) and ret20 < 0 and ret60 < 0:\n        risk += 10.0\n\n    # Money-flow evidence is the key distinction between accumulation and a trap.\n    flow_positive = 0\n    flow_available = 0\n    cmf = _latest(df, "CMF")\n    ad_slope = _latest(df, "AD_Slope")\n    obv = _series(df, "OBV").dropna()\n    for value in (cmf, ad_slope):\n        if _is_finite(value):\n            flow_available += 1\n            flow_positive += int(value > 0)\n    if len(obv) >= 20:\n        flow_available += 1\n        flow_positive += int(float(obv.iloc[-1] - obv.iloc[-20]) > 0)\n    if flow_available:\n        if flow_positive == 0:\n            risk += 25.0\n        elif flow_positive == 1:\n            risk += 10.0\n        elif flow_positive >= 2:\n            risk -= 8.0\n\n    if len(volume.dropna()) >= 60:\n        vol20 = float(volume.dropna().iloc[-20:].mean())\n        vol60 = float(volume.dropna().iloc[-60:-20].mean())\n        if vol60 > 0 and vol20 < vol60 * 0.75:\n            if _is_finite(ret20) and ret20 < 0:\n                risk += 10.0\n            elif _is_finite(ret20) and ret20 >= 0:\n                risk -= 3.0\n\n    recovery_confirmed = (\n        _is_finite(price)\n        and _is_finite(ma20)\n        and _is_finite(ma50)\n        and price >= ma20 >= ma50\n        and _is_finite(ret20)\n        and ret20 > 0\n    )\n    if recovery_confirmed:\n        risk -= 15.0\n    elif _is_finite(ret20) and ret20 > 5.0 and flow_positive >= 2:\n        risk -= 8.0\n\n    # ETFs do not carry company-specific value-trap risk; retain only technical\n    # deterioration with a softer scale while keeping the public field name.\n    if is_etf:\n        risk *= 0.80\n    return _clamp(risk, 0.0, 100.0)\n\n\n'''
    text = replace_between(text, 'def value_trap_risk(df: pd.DataFrame) -> float:\n', 'def breakout_score(df: pd.DataFrame) -> float:\n', new_value_trap, "replace value trap")

    before_score_ticker = 'def score_ticker(df: pd.DataFrame, is_etf: bool = False) -> ScoreBreakdown:\n'
    execution_helper = '''def execution_quality_score(\n    df: pd.DataFrame, entry: dict[str, Any] | None = None\n) -> float:\n    """Score execution location only; trend and breakout evidence live elsewhere."""\n    if df is None or df.empty:\n        return 0.0\n    price = _latest(df, "Close")\n    atr = _latest(df, "ATR14")\n    rsi = _latest(df, "RSI14")\n    ma20 = _latest(df, "MA20")\n    high = _series(df, "High")\n    low = _series(df, "Low")\n    if not _is_finite(price) or price <= 0:\n        return 0.0\n    effective_atr = atr if _is_finite(atr) and atr > 0 else price * 0.03\n    support = float(low.dropna().iloc[-20:].min()) if len(low.dropna()) >= 20 else price - effective_atr\n    resistance = float(high.dropna().iloc[-21:-1].max()) if len(high.dropna()) >= 21 else price + effective_atr * 2.0\n    stop = float(entry.get("stop", np.nan)) if entry else np.nan\n    if not _is_finite(stop):\n        stop = max(support - effective_atr, 0.0)\n\n    score = 0.0\n    distance_support_atr = max(0.0, price - support) / max(effective_atr, 1e-9)\n    score += (1.0 - _clamp(distance_support_atr / 3.0)) * 35.0\n\n    if _is_finite(ma20):\n        ma_distance_atr = abs(price - ma20) / max(effective_atr, 1e-9)\n        score += (1.0 - _clamp(ma_distance_atr / 2.5)) * 20.0\n\n    risk_distance = (price - stop) / price if price > 0 and stop >= 0 else np.nan\n    if _is_finite(risk_distance):\n        # A 2%-8% stop distance is practical; extremely wide or zero stops are poor execution.\n        if 0.02 <= risk_distance <= 0.08:\n            score += 20.0\n        elif 0.01 <= risk_distance <= 0.12:\n            score += 10.0\n\n    reward = max(0.0, resistance - price)\n    risk_amount = max(price - stop, effective_atr * 0.25)\n    reward_risk = reward / risk_amount if risk_amount > 0 else 0.0\n    score += _clamp(reward_risk / 2.5) * 15.0\n\n    if _is_finite(rsi):\n        if 40.0 <= rsi <= 68.0:\n            score += 10.0\n        elif 30.0 <= rsi <= 75.0:\n            score += 5.0\n    return _clamp(score, 0.0, 100.0)\n\n\n'''
    text = replace_once(text, before_score_ticker, execution_helper + before_score_ticker, "insert execution score")
    start = text.find(before_score_ticker)
    if start < 0:
        raise RuntimeError("score_ticker start missing")
    new_score_ticker = '''def score_ticker(df: pd.DataFrame, is_etf: bool = False) -> ScoreBreakdown:\n    """Compute orthogonal setup, trigger and execution components."""\n    available = _score_dimensions_available(df)\n    missing_indicators = available.count(False)\n    indicator_coverage = sum(available) / len(available)\n\n    if missing_indicators >= 4:\n        logger.warning(\n            "数据不足：%d/5 个维度不可用，覆盖率 %.1f%%，跳过评分",\n            missing_indicators,\n            indicator_coverage * 100,\n        )\n        return ScoreBreakdown(\n            total=0.0,\n            missing_indicators=missing_indicators,\n            indicator_coverage=indicator_coverage,\n            confidence=0.0,\n        )\n\n    raw_scores = (\n        score_trend(df) if available[0] else 0.0,\n        score_volume(df) if available[1] else 0.0,\n        score_accumulation(df) if available[2] else 0.0,\n        score_volatility(df) if available[3] else 0.0,\n        score_structure(df) if available[4] else 0.0,\n    )\n    style = classify_style(df, is_etf=is_etf)\n    adjustments = _style_adjustment(df, style)\n    limits = tuple(\n        float(value)\n        for value in (\n            SCORING_WEIGHTS.trend,\n            SCORING_WEIGHTS.volume,\n            SCORING_WEIGHTS.accumulation,\n            SCORING_WEIGHTS.volatility,\n            SCORING_WEIGHTS.structure,\n        )\n    )\n    adjusted_scores = tuple(\n        _clamp(score * adjustment, 0.0, limit)\n        for score, adjustment, limit in zip(raw_scores, adjustments, limits)\n    )\n    trend, volume, accumulation, volatility, structure = adjusted_scores\n    available_weight = sum(\n        limit for is_available, limit in zip(available, limits) if is_available\n    )\n    total = (\n        sum(\n            score\n            for is_available, score in zip(available, adjusted_scores)\n            if is_available\n        )\n        / available_weight\n        * 100.0\n        if available_weight\n        else 0.0\n    )\n\n    trap = value_trap_risk(df, is_etf=is_etf)\n    breakout = breakout_score(df)\n    entry = entry_point(\n        df,\n        breakout,\n        volume_score=volume,\n        value_trap_risk_value=trap,\n    )\n    execution_raw = execution_quality_score(df, entry)\n\n    setup_coverage = 0.55 + 0.45 * indicator_coverage\n    trigger_coverage = 0.75 + 0.25 * indicator_coverage\n    execution_coverage = 0.70 + 0.30 * indicator_coverage\n    base_score = _clamp(total * setup_coverage, 0.0, 100.0)\n    trigger_score = _clamp(breakout * trigger_coverage, 0.0, 100.0)\n    execution_score = _clamp(execution_raw * execution_coverage, 0.0, 100.0)\n\n    setup_weight, trigger_weight, execution_weight = _model_component_weights()\n    final_score = _clamp(\n        base_score * setup_weight\n        + trigger_score * trigger_weight\n        + execution_score * execution_weight,\n        0.0,\n        100.0,\n    )\n    coverage_cap = 40.0 + 60.0 * indicator_coverage\n    final_score = min(final_score, coverage_cap)\n\n    contributions = ScoreContributions(\n        {\n            "trend": trend,\n            "volume": volume,\n            "accumulation": accumulation,\n            "compression": volatility,\n            "structure": structure,\n        }\n    )\n    contributions.update(\n        {\n            "base": base_score,\n            "breakout": breakout,\n            "entry": entry["score"],\n            "execution": execution_score,\n            "coverage_cap": coverage_cap,\n            "value_trap_risk": trap,\n        }\n    )\n\n    return ScoreBreakdown(\n        total=total,\n        trend=trend,\n        volume=volume,\n        accumulation=accumulation,\n        volatility=volatility,\n        structure=structure,\n        missing_indicators=missing_indicators,\n        indicator_coverage=indicator_coverage,\n        confidence=indicator_coverage,\n        base_score=base_score,\n        breakout_score=breakout,\n        entry_score=entry["score"],\n        execution_score=execution_score,\n        value_trap_risk=trap,\n        trigger_score=trigger_score,\n        final_score=final_score,\n        entry_zone_low=entry["low"],\n        entry_zone_high=entry["high"],\n        breakout_buy_price=entry["breakout"],\n        stop_loss=entry["stop"],\n        contributions=contributions,\n    )\n'''
    text = text[:start] + new_score_ticker
    path.write_text(text, encoding="utf-8")


def patch_analytics() -> None:
    path = ROOT / "analytics.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    BACKTEST_CACHE_ENABLED,\n    INDICATOR_CACHE_ENABLED,\n',
        '    BACKTEST_CACHE_ENABLED,\n    GLOBAL_CALIBRATION_MAX_WEIGHT,\n    GLOBAL_CALIBRATION_MIN_SAMPLES,\n    INDICATOR_CACHE_ENABLED,\n',
        "analytics calibration config",
    )
    text = replace_once(
        text,
        'from performance_cache import (\n',
        '''from model_calibration import (\n    build_global_calibration,\n    calibrate_component_weights,\n    calibration_scores_for_frame,\n    walk_forward_stats,\n)\nfrom performance_cache import (\n''',
        "analytics calibration imports",
    )
    text = replace_once(
        text,
        'from score import breakout_score, entry_point, score_ticker, value_trap_risk\n',
        'from score import breakout_score, entry_point, model_weight_signature, score_ticker, value_trap_risk\n',
        "analytics model signature import",
    )
    text = replace_once(
        text,
        '    by_ticker: list[dict[str, Any]] = field(default_factory=list)\n',
        '    by_ticker: list[dict[str, Any]] = field(default_factory=list)\n    global_calibration: list[dict[str, Any]] = field(default_factory=list)\n    walk_forward: list[dict[str, Any]] = field(default_factory=list)\n    component_calibration: dict[str, Any] = field(default_factory=dict)\n',
        "backtest summary calibration fields",
    )
    # Preserve public tuple shape while carrying orthogonal score components in a side sink.
    text = replace_once(
        text,
        '    start_index: int | None = None,\n) -> list[tuple[int, float, str]]:\n',
        '    start_index: int | None = None,\n    component_sink: dict[int, tuple[float, float, float]] | None = None,\n) -> list[tuple[int, float, str]]:\n',
        "signal evaluations component sink",
    )
    text = replace_once(
        text,
        '        evaluations.append((index, float(final_score), signal))\n        last_signal = index\n',
        '''        evaluations.append((index, float(final_score), signal))\n        if component_sink is not None:\n            component_sink[index] = (\n                _finite_float(getattr(historical_score, "base_score", np.nan), 0.0),\n                _finite_float(getattr(historical_score, "trigger_score", np.nan), 0.0),\n                _finite_float(getattr(historical_score, "execution_score", np.nan),\n                    _finite_float(getattr(historical_score, "entry_score", np.nan), 0.0)),\n            )\n        last_signal = index\n''',
        "capture score components",
    )
    text = replace_once(
        text,
        '''    def __init__(self, evaluations: list[tuple[int, float, str]]) -> None:\n        super().__init__(index for index, _score, _signal in evaluations)\n        self.evaluations = evaluations\n''',
        '''    def __init__(\n        self,\n        evaluations: list[tuple[int, float, str]],\n        components: dict[int, tuple[float, float, float]] | None = None,\n    ) -> None:\n        super().__init__(index for index, _score, _signal in evaluations)\n        self.evaluations = evaluations\n        self.components = components or {}\n''',
        "signal point components",
    )
    text = replace_once(
        text,
        '''    evaluations = _signal_evaluations(\n        enriched, cooldown=cooldown, is_etf=is_etf\n    )\n    return _SignalPointList(evaluations)\n''',
        '''    components: dict[int, tuple[float, float, float]] = {}\n    evaluations = _signal_evaluations(\n        enriched, cooldown=cooldown, is_etf=is_etf, component_sink=components\n    )\n    return _SignalPointList(evaluations, components)\n''',
        "signal points capture components",
    )
    old_profile_points = '''        signal_points = _SignalPointList(\n            _signal_evaluations(\n                enriched,\n                is_etf=is_etf,\n                profile=active_profile,\n                start_index=signal_start_index,\n            )\n        )\n'''
    new_profile_points = '''        profile_components: dict[int, tuple[float, float, float]] = {}\n        signal_points = _SignalPointList(\n            _signal_evaluations(\n                enriched,\n                is_etf=is_etf,\n                profile=active_profile,\n                start_index=signal_start_index,\n                component_sink=profile_components,\n            ),\n            profile_components,\n        )\n'''
    text = replace_once(text, old_profile_points, new_profile_points, "profile components")
    text = replace_once(
        text,
        '    attached_evaluations = getattr(signal_points, "evaluations", None)\n',
        '    attached_evaluations = getattr(signal_points, "evaluations", None)\n    component_map = dict(getattr(signal_points, "components", {}) or {})\n',
        "backtest component map",
    )
    text = replace_once(
        text,
        '''            evaluation_map[int(index)] = (\n                float(final_score),\n                _historical_entry_signal(historical, historical_score),\n            )\n''',
        '''            evaluation_map[int(index)] = (\n                float(final_score),\n                _historical_entry_signal(historical, historical_score),\n            )\n            component_map[int(index)] = (\n                _finite_float(getattr(historical_score, "base_score", np.nan), 0.0),\n                _finite_float(getattr(historical_score, "trigger_score", np.nan), 0.0),\n                _finite_float(getattr(historical_score, "execution_score", np.nan),\n                    _finite_float(getattr(historical_score, "entry_score", np.nan), 0.0)),\n            )\n''',
        "fallback component map",
    )
    text = replace_once(
        text,
        '        historical_score, historical_signal = evaluation_map[index]\n        samples.append(\n',
        '''        historical_score, historical_signal = evaluation_map[index]\n        setup_component, trigger_component, execution_component = component_map.get(\n            index, (historical_score, 0.0, 0.0)\n        )\n        samples.append(\n''',
        "sample components values",
    )
    text = replace_once(
        text,
        '                "ticker": ticker,\n                "entry_signal": historical_signal,\n',
        '                "ticker": ticker,\n                "asset_type": "etf" if is_etf else "stock",\n                "entry_signal": historical_signal,\n',
        "sample asset type",
    )
    text = replace_once(
        text,
        '                "score": historical_score,\n                "split": split,\n',
        '''                "score": historical_score,\n                "setup_score": float(setup_component),\n                "trigger_score": float(trigger_component),\n                "execution_score": float(execution_component),\n                "split": split,\n''',
        "sample component fields",
    )
    text = replace_once(
        text,
        '            "fast_prefilter": bool(active_profile.fast_prefilter),\n',
        '            "fast_prefilter": bool(active_profile.fast_prefilter),\n            "model_weight_signature": model_weight_signature(),\n',
        "backtest cache model signature",
    )

    # Keep the quality-adjusted InstitutionalScore produced during scan as the\n    # single quality application.  The backtest pass adjusts it by the ratio of\n    # calibrated technical evidence instead of blending fundamentals again.
    text = replace_once(
        text,
        '    frame = pd.read_csv(path, encoding="utf-8-sig")\n    metric_columns = {\n',
        '''    frame = pd.read_csv(path, encoding="utf-8-sig")\n    prior_institutional_score = pd.to_numeric(\n        frame.get("InstitutionalScore", pd.Series(np.nan, index=frame.index)),\n        errors="coerce",\n    )\n    metric_columns = {\n''',
        "capture prior institutional score",
    )
    text = replace_once(
        text,
        '        "InstitutionalScore",\n        "InstitutionalPercentile",\n',
        '        "InstitutionalPercentile",\n',
        "preserve institutional score column",
    )
    # Global calibration is used as prior evidence only when the backtest run\n    # contains a real peer table; old/synthetic summaries remain unchanged.
    old_adjusted = '''    frame["BacktestAdjustedScore"] = (\n        BACKTEST_NEUTRAL_SCORE\n        + (backtest_component - BACKTEST_NEUTRAL_SCORE) * reliability\n    ).round(4)\n'''
    new_adjusted = '''    peer_score, peer_confidence = calibration_scores_for_frame(\n        frame, getattr(summary, "global_calibration", None)\n    )\n    peer_available = peer_confidence.gt(0.0)\n    peer_anchor = peer_score.where(peer_available, BACKTEST_NEUTRAL_SCORE)\n    frame["BacktestAdjustedScore"] = (\n        peer_anchor + (backtest_component - peer_anchor) * reliability\n    ).round(4)\n'''
    text = replace_once(text, old_adjusted, new_adjusted, "global calibration prior")
    text = replace_once(
        text,
        '''    effective_weight = pd.to_numeric(\n        frame["BacktestEffectiveWeight"], errors="coerce"\n    ).fillna(0.0)\n''',
        '''    effective_weight = pd.to_numeric(\n        frame["BacktestEffectiveWeight"], errors="coerce"\n    ).fillna(0.0)\n    if peer_available.any():\n        peer_weight = (peer_confidence * float(GLOBAL_CALIBRATION_MAX_WEIGHT)).clip(0.0, GLOBAL_CALIBRATION_MAX_WEIGHT)\n        effective_weight = pd.Series(\n            np.maximum(effective_weight.to_numpy(dtype=float), peer_weight.to_numpy(dtype=float)),\n            index=frame.index,\n        )\n        frame["BacktestEffectiveWeight"] = effective_weight.round(4)\n''',
        "global calibration weight",
    )

    # Replace the second quality blend only for real rows that already carry a\n    # scan-time InstitutionalScore; legacy synthetic files keep old behavior.
    old_inst = '''    frame["InstitutionalScore"] = pd.Series(\n        np.where(\n            quality_eligible,\n            institutional_component * 0.7 + quality_score * 0.3,\n            institutional_component,\n        ),\n        index=frame.index,\n    ).mul(frame["QualityMultiplier"], axis=0).round(4)\n'''
    new_inst = '''    legacy_institutional = pd.Series(\n        np.where(\n            quality_eligible,\n            institutional_component * 0.7 + quality_score * 0.3,\n            institutional_component,\n        ),\n        index=frame.index,\n    ).mul(frame["QualityMultiplier"], axis=0)\n    raw_reference = raw_score.replace(0.0, np.nan)\n    calibration_ratio = (\n        pd.to_numeric(frame["FailureAdjustedScore"], errors="coerce") / raw_reference\n    ).replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0.70, 1.30)\n    single_quality_score = prior_institutional_score * calibration_ratio * recency_multiplier\n    frame["InstitutionalScore"] = single_quality_score.where(\n        prior_institutional_score.notna(), legacy_institutional\n    ).round(4)\n'''
    text = replace_once(text, old_inst, new_inst, "single quality application")

    # Build OOS calibration and component-weight audit before selecting test rows.
    anchor = '        all_frame = pd.concat(sample_batches, ignore_index=True)\n        summary.all_samples = len(all_frame)\n'
    addition = '''        all_frame = pd.concat(sample_batches, ignore_index=True)\n        summary.all_samples = len(all_frame)\n        calibration_frame = all_frame.loc[all_frame["split"].isin(["train", "validation"])].copy()\n        summary.global_calibration = build_global_calibration(\n            calibration_frame, min_samples=GLOBAL_CALIBRATION_MIN_SAMPLES\n        )\n        summary.walk_forward = walk_forward_stats(all_frame)\n        component_calibration = calibrate_component_weights(all_frame)\n        summary.component_calibration = component_calibration.to_dict()\n        calibration_path = OUTPUT_DIR / "ScoreCalibration.json"\n        try:\n            calibration_path.write_text(\n                json.dumps(summary.component_calibration, ensure_ascii=False, indent=2),\n                encoding="utf-8",\n            )\n        except OSError:\n            logger.warning("无法写入评分权重校准文件 %s", calibration_path)\n'''
    text = replace_once(text, anchor, addition, "build model calibration")
    text = replace_once(
        text,
        '''        summary.rolling_oos_stats = {\n            split: {"samples": len(all_frame[all_frame["split"] == split])}\n            for split in ("train", "validation", "test")\n        }\n''',
        '''        summary.rolling_oos_stats = {\n            split: {"samples": len(all_frame[all_frame["split"] == split])}\n            for split in ("train", "validation", "test")\n        }\n        summary.rolling_oos_stats["walk_forward"] = summary.walk_forward\n''',
        "walk forward summary",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_gui_inheritance()
    patch_scanner_progress()
    patch_score_model()
    patch_analytics()
    print("model v2 foundation migration applied")


if __name__ == "__main__":
    main()
