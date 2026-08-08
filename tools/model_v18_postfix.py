from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"postfix pattern not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# The v4 cache namespace already invalidates v3 production caches.  Do not
# reject simplified compute functions used by cache consumers/tests merely
# because they intentionally return a subset of production indicators.
replace_once(
    "performance_cache.py",
    '''    if cached is not None and not _REQUIRED_INDICATOR_COLUMNS.issubset(cached.columns):
        cached = None

    if cached is not None:
''',
    '''    if cached is not None:
''',
)

# ETF fundamentals are not applicable, so UNKNOWN holding history must not
# overwrite the explicit N/A reason.  Chase risk belongs in the decision gate,
# not in the immutable technical EntrySignal.
replace_once(
    "signal_lifecycle.py",
    '''    quality_reason = quality_reason.where(
        ~(status.eq("UNKNOWN") & ~known_fail),
        "机构覆盖家数历史不足，按中性处理",
    )
''',
    '''    quality_reason = quality_reason.where(
        ~(status.eq("UNKNOWN") & ~known_fail & quality_applicable),
        "机构覆盖家数历史不足，按中性处理",
    )
    quality_reason = quality_reason.where(
        quality_applicable,
        "ETF基本面门槛不适用",
    )
''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & ~stale_data
        & ~minimum_score_risk
    )
''',
    '''        & ~stale_data
        & ~minimum_score_risk
        & chase.lt(CHASE_RISK_HIGH_THRESHOLD)
    )
''',
)
replace_once(
    "signal_lifecycle.py",
    '''    result["TradeReadinessReason"] = readiness_reason
''',
    '''    readiness_reason.loc[quality_action_block & ~hard_filter] = (
        "质量门槛未通过或数据不足，转为观察"
    )
    readiness_reason.loc[
        chase.ge(CHASE_RISK_HIGH_THRESHOLD) & ~hard_filter
    ] = "追高风险过高，转为观察"
    result["TradeReadinessReason"] = readiness_reason
''',
)
replace_once(
    "signal_lifecycle.py",
    '''    operation_advice.loc[decision_state.eq("BLOCKED")] = "当前存在硬风险条件，暂不参与。"
''',
    '''    operation_advice.loc[
        decision_state.eq("BLOCKED") & ~stale_data
    ] = "当前存在硬风险条件，暂不参与。"
    operation_advice.loc[stale_data] = "行情数据已过期，请刷新后再判断。"
''',
)

# The old regression suite encoded the v17 contract where safety gates rewrote
# EntrySignal.  v18 deliberately keeps the technical state immutable and moves
# those outcomes to DecisionState/TradeReadiness.
replace_once(
    "test_regressions.py",
    '''        self.assertFalse(result.loc[0, "QualityGate"])
        self.assertEqual(result.loc[0, "EntrySignal"], "WAIT_PULLBACK")
        self.assertIn("质量门槛", result.loc[0, "SignalAdjustmentReason"])
''',
    '''        self.assertFalse(result.loc[0, "QualityGate"])
        self.assertEqual(result.loc[0, "EntrySignal"], "BUY_NOW")
        self.assertEqual(result.loc[0, "DecisionState"], "OBSERVE")
        self.assertIn("质量门槛", result.loc[0, "DecisionReason"])
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertGreaterEqual(result.loc[0, "ChaseRiskScore"], 60.0)
        self.assertEqual(result.loc[0, "EntrySignal"], "HOLD_WAIT")
        self.assertIn("追高风险", result.loc[0, "SignalAdjustmentReason"])
        self.assertEqual(result.loc[0, "OperationAdvice"], "暂缓操作，等待风险或趋势条件改善。")
''',
    '''        self.assertGreaterEqual(result.loc[0, "ChaseRiskScore"], 60.0)
        self.assertEqual(result.loc[0, "EntrySignal"], "BREAKOUT_CONFIRM")
        self.assertEqual(result.loc[0, "DecisionState"], "OBSERVE")
        self.assertIn("追高风险", result.loc[0, "DecisionReason"])
''',
)
replace_once(
    "test_regressions.py",
    '''                "Score": [80.0],
                "BreakoutQualityFactor": [0.2],
                "PassedFilters": [True],
''',
    '''                "Score": [80.0],
                "EntrySignal": ["BREAKOUT_CONFIRM"],
                "BreakoutQualityFactor": [0.2],
                "PassedFilters": [True],
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertEqual(stronger.sector_confirmation_factor, 0.6)
        self.assertEqual(weaker.industry_momentum_60d, 10.0)
        self.assertEqual(weaker.industry_relative_strength, -10.0)
        self.assertEqual(weaker.sector_confirmation_factor, 0.8)
''',
    '''        self.assertEqual(stronger.sector_confirmation_factor, 0.9113)
        self.assertEqual(weaker.industry_momentum_60d, 10.0)
        self.assertEqual(weaker.industry_relative_strength, -10.0)
        self.assertEqual(weaker.sector_confirmation_factor, 0.8402)
        self.assertGreater(stronger.sector_confirmation_factor, weaker.sector_confirmation_factor)
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertLess(result.loc["AVOID", "RankingScore"], result.loc["BUY", "RankingScore"])
        self.assertGreaterEqual(result.loc["CHASE", "ChaseRiskScore"], 60.0)
        self.assertNotEqual(result.loc["CHASE", "EntrySignal"], "BUY_NOW")
''',
    '''        self.assertLess(result.loc["AVOID", "RankingScore"], result.loc["BUY", "RankingScore"])
        self.assertGreaterEqual(result.loc["CHASE", "ChaseRiskScore"], 60.0)
        self.assertEqual(result.loc["CHASE", "EntrySignal"], "BUY_NOW")
        self.assertNotEqual(result.loc["CHASE", "DecisionState"], "READY")
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertEqual(result.loc["WEAK_BREAKOUT", "EntrySignal"], "PRICE_BREAKOUT")
        self.assertFalse(result.loc["WEAK_BREAKOUT", "BreakoutVolumeConfirmed"])
        self.assertFalse(result.loc["WEAK_BREAKOUT", "BreakoutFlowConfirmed"])
''',
    '''        self.assertEqual(result.loc["WEAK_BREAKOUT", "EntrySignal"], "BREAKOUT_CONFIRM")
        self.assertFalse(result.loc["WEAK_BREAKOUT", "BreakoutVolumeConfirmed"])
        self.assertFalse(result.loc["WEAK_BREAKOUT", "BreakoutFlowConfirmed"])
        self.assertEqual(result.loc["WEAK_BREAKOUT", "DecisionState"], "OBSERVE")
''',
)
replace_once(
    "test_regressions.py",
    '''        result = signal_lifecycle.finalize_signal_ranking(frame)
        self.assertEqual(result.loc[0, "EntrySignal"], "AVOID")
        self.assertIn("RankingScore", result)
''',
    '''        result = signal_lifecycle.finalize_signal_ranking(frame)
        self.assertEqual(result.loc[0, "EntrySignal"], "BUY_NOW")
        self.assertEqual(result.loc[0, "DecisionState"], "BLOCKED")
        self.assertIn("RankingScore", result)
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertEqual(result.loc["STALE", "DataFreshnessStatus"], "过期")
        self.assertEqual(result.loc["STALE", "EntrySignal"], "HOLD_WAIT")
        self.assertEqual(result.loc["STALE", "RankingEligibility"], "风险过滤")
''',
    '''        self.assertEqual(result.loc["STALE", "DataFreshnessStatus"], "过期")
        self.assertEqual(result.loc["STALE", "EntrySignal"], "BREAKOUT_CONFIRM")
        self.assertEqual(result.loc["STALE", "DecisionState"], "BLOCKED")
        self.assertEqual(result.loc["STALE", "RankingEligibility"], "风险过滤")
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertFalse(result.loc["QUALITY_FAIL", "QualityGate"])
        self.assertEqual(result.loc["QUALITY_FAIL", "EntrySignal"], "WAIT_PULLBACK")
        self.assertEqual(result.loc["QUALITY_FAIL", "RankingEligibility"], "观察")
        self.assertIn("质量门槛", result.loc["QUALITY_FAIL", "SignalAdjustmentReason"])
''',
    '''        self.assertFalse(result.loc["QUALITY_FAIL", "QualityGate"])
        self.assertEqual(result.loc["QUALITY_FAIL", "EntrySignal"], "BUY_NOW")
        self.assertEqual(result.loc["QUALITY_FAIL", "DecisionState"], "OBSERVE")
        self.assertEqual(result.loc["QUALITY_FAIL", "RankingEligibility"], "观察")
        self.assertIn("质量门槛", result.loc["QUALITY_FAIL", "DecisionReason"])
''',
)

print("model v18 postfix applied")
