from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-09-v24-decision-integrity"\n# Engineering versions are intentionally separate from the scoring model.\nPIPELINE_VERSION: str = "2026-08-10-v34-static-quality"',
    'SCORING_VERSION: str = "2026-08-11-v35-orthogonal-decision"\n# Engineering versions are intentionally separate from the scoring model.\nPIPELINE_VERSION: str = "2026-08-11-v35-model-integrity"',
)
replace_once(
    "config.py",
    'GLOBAL_CALIBRATION_MIN_SAMPLES: Final[int] = 30\nGLOBAL_CALIBRATION_MAX_WEIGHT: Final[float] = 0.15',
    'GLOBAL_CALIBRATION_MIN_SAMPLES: Final[int] = 30\nGLOBAL_CALIBRATION_MAX_WEIGHT: Final[float] = 0.15\n# Cross-asset percentile is a small comparability correction, not a second alpha score.\nCROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT: Final[float] = 5.0\n# Rapidly weakening signals remain visible for research but cannot stay trade-ready.\nLIFECYCLE_WEAKEN_RANKING_FACTOR: Final[float] = 0.82',
)

replace_once(
    "score.py",
    '''def _style_adjustment(\n    df: pd.DataFrame, style: str\n) -> tuple[float, float, float, float, float]:\n    if style == "高波动成长":\n        return (1.15, 1.05, 0.90, 0.85, 0.95)\n    if style == "趋势成长":\n        return (1.25, 1.00, 0.90, 0.85, 0.95)\n    if style == "资金吸筹":\n        return (0.90, 1.05, 1.25, 1.05, 1.00)\n    if style == "低波动防守":\n        return (0.90, 0.95, 1.05, 1.25, 1.20)\n    if style == "ETF趋势/资金":\n        return (1.00, 1.00, 1.10, 1.00, 0.90)\n    return (1.00, 1.00, 1.00, 1.00, 1.00)\n''',
    '''def _style_adjustment(\n    df: pd.DataFrame, style: str\n) -> tuple[float, float, float, float, float]:\n    """Keep style descriptive; do not reward the same features twice.\n\n    Style is inferred from volatility, momentum and volume.  Reweighting the\n    component scores with that same label created a self-reinforcing loop.\n    Cross-sectional/style calibration can still use the label downstream.\n    """\n    _ = (df, style)\n    return (1.00, 1.00, 1.00, 1.00, 1.00)\n''',
)
replace_once(
    "score.py",
    '''    return _clamp(points, 0.0, 100.0)\n\n\ndef smart_money_stage(''',
    '''    return _clamp(points, 0.0, 100.0)\n\n\ndef trigger_event_score(df: pd.DataFrame) -> float:\n    """Score only *new* launch evidence, avoiding setup/trend double counting.\n\n    The legacy breakout score intentionally remains available for signal-state\n    classification.  This trigger component instead measures event surprise:\n    resistance clearance, current-volume expansion and acceleration in money\n    flow.  It deliberately does not look at MA20/MA50/MA200.\n    """\n    close = _series(df, "Close")\n    high = _series(df, "High")\n    volume = _series(df, "Volume")\n    valid = pd.concat({"close": close, "high": high, "volume": volume}, axis=1).dropna()\n    if len(valid) < 21:\n        return 0.0\n\n    price = float(valid["close"].iloc[-1])\n    resistance = float(valid["high"].iloc[-21:-1].max())\n    volume_now = float(valid["volume"].iloc[-1])\n    volume_baseline = float(valid["volume"].iloc[-21:-1].mean())\n    points = 0.0\n\n    if resistance > 0:\n        clearance_pct = (price / resistance - 1.0) * 100.0\n        if clearance_pct > 0.0:\n            points += 35.0 + _clamp(clearance_pct / 3.0) * 15.0\n        elif clearance_pct >= -1.5:\n            points += _clamp((clearance_pct + 1.5) / 1.5) * 12.0\n\n    if volume_baseline > 0:\n        volume_ratio = volume_now / volume_baseline\n        points += _clamp((volume_ratio - 1.0) / 1.25) * 25.0\n\n    cmf = _series(df, "CMF").dropna()\n    if len(cmf) >= 6:\n        cmf_delta = float(cmf.iloc[-1] - cmf.iloc[-6])\n        points += _clamp(cmf_delta / 0.12) * 10.0\n\n    ad_slope = _series(df, "AD_Slope").dropna()\n    if len(ad_slope) >= 6:\n        current_ad = float(ad_slope.iloc[-1])\n        prior_ad = float(ad_slope.iloc[-6:-1].median())\n        if current_ad > 0.0 and prior_ad <= 0.0:\n            points += 8.0\n        elif current_ad > 0.0 and current_ad > prior_ad:\n            points += 4.0\n\n    obv = _series(df, "OBV").dropna()\n    if len(obv) >= 11:\n        recent_change = float(obv.iloc[-1] - obv.iloc[-6])\n        prior_change = float(obv.iloc[-6] - obv.iloc[-11])\n        if recent_change > 0.0 and recent_change > max(prior_change, 0.0):\n            points += 7.0\n\n    return _clamp(points, 0.0, 100.0)\n\n\ndef smart_money_stage(''',
)
replace_once(
    "score.py",
    '''    execution_raw = execution_quality_score(df, entry)\n\n    setup_coverage = 0.55 + 0.45 * indicator_coverage\n    trigger_coverage = 0.75 + 0.25 * indicator_coverage\n    execution_coverage = 0.70 + 0.30 * indicator_coverage\n    base_score = _clamp(total * setup_coverage, 0.0, 100.0)\n    trigger_score = _clamp(breakout * trigger_coverage, 0.0, 100.0)\n    execution_score = _clamp(execution_raw * execution_coverage, 0.0, 100.0)''',
    '''    execution_raw = execution_quality_score(df, entry)\n    trigger_raw = trigger_event_score(df)\n\n    setup_coverage = 0.55 + 0.45 * indicator_coverage\n    trigger_coverage = 0.75 + 0.25 * indicator_coverage\n    execution_coverage = 0.70 + 0.30 * indicator_coverage\n    base_score = _clamp(total * setup_coverage, 0.0, 100.0)\n    trigger_score = _clamp(trigger_raw * trigger_coverage, 0.0, 100.0)\n    execution_score = _clamp(execution_raw * execution_coverage, 0.0, 100.0)''',
)
replace_once(
    "score.py",
    '''            "breakout": breakout,\n            "entry": entry["score"],''',
    '''            "breakout": breakout,\n            "trigger_event": trigger_raw,\n            "entry": entry["score"],''',
)

replace_once(
    "signal_lifecycle.py",
    '''    CHASE_RISK_RSI_START,\n    DATA_FRESHNESS_DELAYED_FACTOR,''',
    '''    CHASE_RISK_RSI_START,\n    CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT,\n    DATA_FRESHNESS_DELAYED_FACTOR,''',
)
replace_once(
    "signal_lifecycle.py",
    '''    INSTITUTIONAL_TIER_WAIT_LABEL,\n    OUTPUT_DIR,''',
    '''    INSTITUTIONAL_TIER_WAIT_LABEL,\n    LIFECYCLE_WEAKEN_RANKING_FACTOR,\n    OUTPUT_DIR,''',
)
replace_once(
    "signal_lifecycle.py",
    '''    passed_filters = _bool_series(result, "PassedFilters", True)\n    signal_status = _text_series(result, "SignalStatus", "").str.upper()\n    lifecycle_failed = signal_status.eq("FAILED")''',
    '''    passed_filters = _bool_series(result, "PassedFilters", True)\n    signal_status = _text_series(result, "SignalStatus", "").str.upper()\n    signal_trend = _text_series(result, "SignalTrend", "").str.upper()\n    lifecycle_terminal = signal_status.isin({"FAILED", "EXPIRED", "INACTIVE"})\n    lifecycle_weakening = signal_status.eq("WEAKEN") & (\n        signal_trend.str.contains("快速", regex=False)\n        | signal_trend.str.contains("FAST", regex=False)\n        | signal_trend.str.contains("RAPID", regex=False)\n    )\n    # Compatibility alias for downstream conditions that historically named\n    # only FAILED.  Terminal lifecycle states now share the same semantics.\n    lifecycle_failed = lifecycle_terminal''',
)
replace_once(
    "signal_lifecycle.py",
    '''    hard_penalty.loc[stale_data] = np.minimum(\n        hard_penalty.loc[stale_data], DATA_FRESHNESS_STALE_FACTOR\n    )\n    hard_reason = _append_reason(hard_reason, avoid, "回避信号")''',
    '''    hard_penalty.loc[stale_data] = np.minimum(\n        hard_penalty.loc[stale_data], DATA_FRESHNESS_STALE_FACTOR\n    )\n    hard_penalty.loc[lifecycle_terminal] = np.minimum(\n        hard_penalty.loc[lifecycle_terminal], HARD_RISK_STAGE_PENALTY\n    )\n    hard_reason = _append_reason(hard_reason, avoid, "回避信号")''',
)
replace_once(
    "signal_lifecycle.py",
    '''    hard_reason = _append_reason(hard_reason, stale_data, "行情数据已过期")\n    result["HardRiskFlag"] = avoid | stage_risk | trap_risk | data_risk | stale_data''',
    '''    hard_reason = _append_reason(hard_reason, stale_data, "行情数据已过期")\n    hard_reason = _append_reason(hard_reason, lifecycle_terminal, "信号生命周期已结束")\n    result["HardRiskFlag"] = (\n        avoid | stage_risk | trap_risk | data_risk | stale_data | lifecycle_terminal\n    )''',
)
replace_once(
    "signal_lifecycle.py",
    '''    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, lifecycle_failed, "历史信号生命周期已失败"\n    )\n    institutional_raw = _number(''',
    '''    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, lifecycle_failed, "历史信号生命周期已结束"\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, lifecycle_weakening, "信号快速衰减，禁止进入推荐"\n    )\n    institutional_raw = _number(''',
)
replace_once(
    "signal_lifecycle.py",
    '''    # Relative percentile is useful for comparing stocks with ETFs, but it must\n    # never overwhelm the absolute institutional score.  Keep the absolute score\n    # as the dominant anchor and cap percentile uplift at +15 points.\n    normalized_score = (\n        asset_percentile * 0.45\n        + institutional_raw.clip(0.0, 100.0) * 0.55\n    ).clip(0.0, 100.0)\n    normalized_score = pd.Series(\n        np.minimum(\n            normalized_score.to_numpy(dtype=float),\n            (institutional_raw.clip(0.0, 100.0) + 15.0).to_numpy(dtype=float),\n        ),\n        index=result.index,\n    )\n    cross_asset_score = institutional_raw.clip(0.0, 100.0).where(\n        ~use_cross_asset_normalization, normalized_score\n    )\n    result["CrossAssetScore"] = cross_asset_score.round(4)''',
    '''    # Percentile is a bounded comparability correction.  The absolute\n    # institutional score remains the model anchor; relative rank can move it by\n    # at most +/-5 points instead of saturating many leaders at a +15 uplift.\n    max_adjustment = float(CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT)\n    relative_adjustment = (\n        (asset_percentile - 50.0) / 50.0 * max_adjustment\n    ).clip(-max_adjustment, max_adjustment)\n    relative_adjustment = relative_adjustment.where(use_cross_asset_normalization, 0.0)\n    normalized_score = (\n        institutional_raw.clip(0.0, 100.0) + relative_adjustment\n    ).clip(0.0, 100.0)\n    cross_asset_score = institutional_raw.clip(0.0, 100.0).where(\n        ~use_cross_asset_normalization, normalized_score\n    )\n    result["CrossAssetAdjustment"] = relative_adjustment.round(4)\n    result["CrossAssetScore"] = cross_asset_score.round(4)''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & _bool_series(result, "BreakoutFlowConfirmed", False)\n        & ~lifecycle_failed\n    )''',
    '''        & _bool_series(result, "BreakoutFlowConfirmed", False)\n        & ~lifecycle_failed\n        & ~lifecycle_weakening\n    )''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & (passed_filters | filter_override)\n        & ~lifecycle_failed\n        & ~stage_risk''',
    '''        & (passed_filters | filter_override)\n        & ~lifecycle_failed\n        & ~lifecycle_weakening\n        & ~stage_risk''',
)
replace_once(
    "signal_lifecycle.py",
    '''        avoid\n        | trap_risk\n        | lifecycle.isin({"派发", "DISTRIBUTION"})\n        | stale_data\n    )''',
    '''        avoid\n        | trap_risk\n        | lifecycle.isin({"派发", "DISTRIBUTION"})\n        | stale_data\n        | lifecycle_terminal\n    )''',
)
replace_once(
    "signal_lifecycle.py",
    '''    readiness_reason.loc[\n        chase.ge(CHASE_RISK_HIGH_THRESHOLD) & ~hard_filter\n    ] = "追高风险过高，转为观察"\n    result["TradeReadinessReason"] = readiness_reason''',
    '''    readiness_reason.loc[\n        chase.ge(CHASE_RISK_HIGH_THRESHOLD) & ~hard_filter\n    ] = "追高风险过高，转为观察"\n    readiness_reason.loc[lifecycle_weakening & ~hard_filter] = (\n        "信号处于WEAKEN且快速下降，等待重新增强后再进入推荐"\n    )\n    result["TradeReadinessReason"] = readiness_reason''',
)
replace_once(
    "signal_lifecycle.py",
    '''    readiness_penalty_factor *= np.where(lifecycle_failed, 0.70, 1.0)\n    readiness_penalty_factor *= np.where(~passed_filters & ~filter_override, 0.90, 1.0)''',
    '''    readiness_penalty_factor *= np.where(lifecycle_failed, 0.70, 1.0)\n    readiness_penalty_factor *= np.where(\n        lifecycle_weakening, LIFECYCLE_WEAKEN_RANKING_FACTOR, 1.0\n    )\n    readiness_penalty_factor *= np.where(~passed_filters & ~filter_override, 0.90, 1.0)''',
)

Path("test_v35_model_integrity.py").write_text(
    '''from __future__ import annotations\n\nimport unittest\n\nimport numpy as np\nimport pandas as pd\n\nimport config\nimport score\nfrom signal_lifecycle import finalize_signal_ranking\n\n\nclass ModelV35IntegrityTests(unittest.TestCase):\n    def _decision_row(self, status: str, trend: str) -> dict[str, object]:\n        return {\n            "Ticker": "159999.SZ",\n            "IsETF": True,\n            "AssetType": "etf",\n            "EntrySignal": "BUY_NOW",\n            "InstitutionalScore": 60.0,\n            "TechnicalInstitutionalScore": 60.0,\n            "Score": 60.0,\n            "FinalScore": 60.0,\n            "ScoreCoverage": 1.0,\n            "PassedFilters": True,\n            "UniverseEligible": True,\n            "ValueTrapRisk": 0.0,\n            "LifecycleStage": "趋势确认",\n            "SignalStatus": status,\n            "SignalTrend": trend,\n            "SignalRecencyDays": 0,\n            "DataTradingAgeDays": 0,\n            "DataAgeDays": 0,\n            "RSI14": 55.0,\n            "DistToLow52W": 10.0,\n            "DistToMA20": 1.0,\n            "RecentReturn20D": 3.0,\n            "ATRExpansion": 1.0,\n            "BacktestSamples": 0,\n        }\n\n    def test_fast_weaken_cannot_remain_trade_ready(self):\n        frame = finalize_signal_ranking(pd.DataFrame([self._decision_row("WEAKEN", "快速下降")]))\n        self.assertEqual(frame.iloc[0]["DecisionState"], "OBSERVE")\n        self.assertEqual(frame.iloc[0]["RankingEligibility"], "观察")\n        self.assertIn("重新增强", frame.iloc[0]["TradeReadinessReason"])\n        self.assertLess(frame.iloc[0]["ReadinessPenaltyFactor"], 1.0)\n\n    def test_strengthening_signal_can_still_be_ready(self):\n        frame = finalize_signal_ranking(pd.DataFrame([self._decision_row("STRENGTHEN", "持续增强")]))\n        self.assertEqual(frame.iloc[0]["DecisionState"], "READY")\n        self.assertEqual(frame.iloc[0]["RankingEligibility"], "推荐")\n\n    def test_terminal_lifecycle_is_blocked(self):\n        frame = finalize_signal_ranking(pd.DataFrame([self._decision_row("EXPIRED", "已过期")]))\n        self.assertEqual(frame.iloc[0]["DecisionState"], "BLOCKED")\n        self.assertEqual(frame.iloc[0]["RankingEligibility"], "风险过滤")\n        self.assertTrue(bool(frame.iloc[0]["HardRiskFlag"]))\n\n    def test_cross_asset_percentile_adjustment_is_bounded(self):\n        rows = []\n        for index, raw in enumerate((20.0, 25.0, 30.0, 35.0, 40.0)):\n            row = self._decision_row("WATCH", "横盘观察")\n            row.update(\n                {\n                    "Ticker": f"15999{index}.SZ",\n                    "EntrySignal": "WAIT_PULLBACK",\n                    "InstitutionalScore": raw,\n                    "TechnicalInstitutionalScore": raw,\n                    "Score": raw,\n                    "FinalScore": raw,\n                }\n            )\n            rows.append(row)\n        frame = finalize_signal_ranking(pd.DataFrame(rows))\n        adjustment = pd.to_numeric(frame["CrossAssetAdjustment"], errors="coerce")\n        self.assertLessEqual(float(adjustment.abs().max()), config.CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT)\n        top = frame.loc[frame["InstitutionalScore"].idxmax()]\n        self.assertAlmostEqual(float(top["CrossAssetScore"]), 45.0, places=4)\n\n    def test_style_label_no_longer_reweights_the_same_features(self):\n        dummy = pd.DataFrame({"Close": [1.0]})\n        for style in ("高波动成长", "趋势成长", "资金吸筹", "低波动防守", "ETF趋势/资金", "均衡"):\n            self.assertEqual(score._style_adjustment(dummy, style), (1.0, 1.0, 1.0, 1.0, 1.0))\n\n    def test_trigger_event_is_independent_of_moving_average_levels(self):\n        index = pd.date_range("2026-01-01", periods=40, freq="B")\n        close = np.linspace(10.0, 10.8, len(index))\n        high = close * 1.002\n        high[-1] = close[-1] * 1.001\n        volume = np.full(len(index), 1_000_000.0)\n        volume[-1] = 2_000_000.0\n        base = pd.DataFrame(\n            {\n                "Close": close,\n                "High": high,\n                "Volume": volume,\n                "CMF": np.linspace(0.0, 0.15, len(index)),\n                "AD_Slope": np.linspace(-1.0, 1.0, len(index)),\n                "OBV": np.linspace(1_000.0, 2_000.0, len(index)),\n            },\n            index=index,\n        )\n        bearish_ma = base.assign(MA20=20.0, MA50=21.0, MA200=22.0)\n        bullish_ma = base.assign(MA20=9.0, MA50=8.5, MA200=8.0)\n        self.assertAlmostEqual(\n            score.trigger_event_score(bearish_ma),\n            score.trigger_event_score(bullish_ma),\n            places=8,\n        )\n\n    def test_scoring_version_advances_for_changed_model_semantics(self):\n        self.assertIn("v35", config.SCORING_VERSION)\n        self.assertIn("v35", config.PIPELINE_VERSION)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)
