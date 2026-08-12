from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def save(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, label: str) -> None:
    text = load(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    save(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# v39 provenance: model semantics change because the final decision layer now
# preserves Fundamental Gate 2.0 and reconciles lifecycle state with strict
# breakout overrides.
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-12-v38-fundamental-gate2"\n'
    'PIPELINE_VERSION: str = "2026-08-12-v38-fundamental-gate2-v37-integrity"\n'
    'FUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"\n',
    'SCORING_VERSION: str = "2026-08-12-v39-decision-integrity2"\n'
    'PIPELINE_VERSION: str = "2026-08-12-v39-decision-integrity2-v38-fundamental"\n'
    'FUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"\n'
    'DECISION_INTEGRITY_VERSION: str = "2026-08-12-v39-gate-lifecycle-research"\n',
    "v39 version",
)

replace_once(
    "signal_lifecycle.py",
    '''"""v35 lifecycle/ranking policy facade.\n\n``signal_lifecycle_core`` preserves the stable v34 lifecycle implementation.\nThis facade applies three model-integrity corrections after the stable pass:\na bounded cross-asset percentile adjustment, terminal/rapidly-weakening signal\nconsistency, and tier/decision reconciliation using the corrected score.\n"""''',
    '''"""Current lifecycle/ranking policy facade.\n\n``signal_lifecycle_core`` provides the stable lifecycle engine.  The facade\nkeeps cross-asset normalization bounded and reconciles lifecycle, tier and\ndecision state after Fundamental Gate 2.0.  v39 additionally requires the\ncore pass to preserve v38 fundamental-gate authority end to end.\n"""''',
    "lifecycle facade provenance",
)

# ---------------------------------------------------------------------------
# Fundamental Gate 2.0 must remain authoritative after entering lifecycle.
# Legacy rows without QualityProfile keep the old reconstruction behavior.
# ---------------------------------------------------------------------------
old_quality = '''    supplied_quality_fail = (\n        _bool_series(result, "QualityDataAvailable")\n        & ~_bool_series(result, "QualityGate", True)\n        if "QualityGate" in result\n        else pd.Series(False, index=result.index)\n    )\n    known_fail = quality_applicable & (\n        (roe_available & ~_bool_series(result, "QualityROE", True))\n        | (margin_available & ~_bool_series(result, "QualityGrossMargin", True))\n        | (profit_available & ~_bool_series(result, "QualityNetProfit", True))\n        | status.eq("FAIL")\n        | supplied_quality_fail\n    )\n    any_unknown = quality_applicable & (\n        status.eq("UNKNOWN") | ~(roe_available & margin_available & profit_available)\n    )\n    result["QualityGate"] = ~known_fail\n    result["QualityMultiplier"] = np.select(\n        [~quality_applicable, known_fail, any_unknown],\n        [1.0, QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],\n        default=QUALITY_MULTIPLIER_PASS,\n    )\n    quality_reason = _text_series(result, "QualityGateReason", "")\n    quality_reason = quality_reason.where(~known_fail, "存在已确认质量未通过项")\n    quality_reason = quality_reason.where(\n        ~(status.eq("UNKNOWN") & ~known_fail & quality_applicable),\n        "机构覆盖家数历史不足，按中性处理",\n    )\n    quality_reason = quality_reason.where(\n        quality_applicable,\n        "ETF基本面门槛不适用",\n    )\n    quality_reason = quality_reason.where(\n        ~(quality_reason.eq("") & ~known_fail & ~any_unknown),\n        "全部可用质量项通过",\n    )\n    result["QualityGateReason"] = quality_reason\n'''
new_quality = '''    supplied_quality_gate = _bool_series(result, "QualityGate", True)\n    supplied_quality_fail = (\n        _bool_series(result, "QualityDataAvailable") & ~supplied_quality_gate\n        if "QualityGate" in result\n        else pd.Series(False, index=result.index)\n    )\n    quality_profile = _text_series(result, "QualityProfile", "").str.upper()\n    gate2_profile = quality_profile.isin({"GENERAL", "FINANCIAL", "CYCLICAL", "DEFENSIVE"})\n\n    legacy_known_fail = quality_applicable & (\n        (roe_available & ~_bool_series(result, "QualityROE", True))\n        | (margin_available & ~_bool_series(result, "QualityGrossMargin", True))\n        | (profit_available & ~_bool_series(result, "QualityNetProfit", True))\n        | status.eq("FAIL")\n        | supplied_quality_fail\n    )\n    # v38 Fundamental Gate 2.0 has already evaluated the profile-specific hard\n    # factors.  Institution coverage is supporting evidence only and therefore\n    # must never be reintroduced here as a standalone veto.\n    known_fail = legacy_known_fail.where(~gate2_profile, quality_applicable & ~supplied_quality_gate)\n    legacy_unknown = quality_applicable & (\n        status.eq("UNKNOWN") | ~(roe_available & margin_available & profit_available)\n    )\n    gate2_uncertain = quality_applicable & gate2_profile & (\n        status.ne("PASS") | result["QualityDataCompleteness"].lt(1.0)\n    )\n    any_unknown = legacy_unknown.where(~gate2_profile, gate2_uncertain)\n    result["QualityGate"] = ~known_fail\n\n    computed_multiplier = pd.Series(\n        np.select(\n            [~quality_applicable, known_fail, any_unknown],\n            [1.0, QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],\n            default=QUALITY_MULTIPLIER_PASS,\n        ),\n        index=result.index,\n        dtype=float,\n    )\n    supplied_multiplier = _number(\n        result.get("QualityMultiplier", pd.Series(np.nan, index=result.index)), np.nan\n    )\n    result["QualityMultiplier"] = supplied_multiplier.where(\n        gate2_profile & supplied_multiplier.notna(), computed_multiplier\n    )\n\n    supplied_reason = _text_series(result, "QualityGateReason", "")\n    legacy_reason = supplied_reason.copy()\n    legacy_reason = legacy_reason.where(~known_fail, "存在已确认质量未通过项")\n    legacy_reason = legacy_reason.where(\n        ~(status.eq("UNKNOWN") & ~known_fail & quality_applicable),\n        "机构覆盖家数历史不足，按中性处理",\n    )\n    legacy_reason = legacy_reason.where(quality_applicable, "ETF基本面门槛不适用")\n    legacy_reason = legacy_reason.where(\n        ~(legacy_reason.eq("") & ~known_fail & ~any_unknown),\n        "全部可用质量项通过",\n    )\n    result["QualityGateReason"] = supplied_reason.where(\n        gate2_profile & supplied_reason.ne(""), legacy_reason\n    )\n'''
replace_once(
    "signal_lifecycle_core.py",
    old_quality,
    new_quality,
    "gate2 authority in lifecycle core",
)

old_cyclical = '''    style_text = _text_series(result, "Style", "").str.lower()\n    cyclical_style = style_text.str.contains("周期", regex=False) | style_text.str.contains(\n        "cyc", regex=False\n    )\n    quality_score_value = _number(\n        result.get("QualityScore", pd.Series(np.nan, index=result.index)), np.nan\n    )\n    cyclical_quality_override = (\n        cyclical_style\n        & result["QualityDataCompleteness"].ge(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n        & quality_score_value.ge(45.0)\n    )\n    result["CyclicalQualityOverride"] = cyclical_quality_override\n'''
new_cyclical = '''    style_text = _text_series(result, "Style", "").str.lower()\n    cyclical_style = style_text.str.contains("周期", regex=False) | style_text.str.contains(\n        "cyc", regex=False\n    )\n    quality_score_value = _number(\n        result.get("QualityScore", pd.Series(np.nan, index=result.index)), np.nan\n    )\n    legacy_cyclical_override = (\n        cyclical_style\n        & result["QualityDataCompleteness"].ge(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n        & quality_score_value.ge(45.0)\n    )\n    supplied_cyclical_override = _bool_series(result, "CyclicalQualityOverride")\n    gate2_profile = _text_series(result, "QualityProfile", "").str.upper().isin(\n        {"GENERAL", "FINANCIAL", "CYCLICAL", "DEFENSIVE"}\n    )\n    cyclical_quality_override = supplied_cyclical_override.where(\n        gate2_profile, legacy_cyclical_override\n    )\n    result["CyclicalQualityOverride"] = cyclical_quality_override\n'''
replace_once(
    "signal_lifecycle_core.py",
    old_cyclical,
    new_cyclical,
    "preserve supplied cyclical override",
)

# ---------------------------------------------------------------------------
# Lifecycle activity must recognize the same strict breakout override used by
# the final decision layer.  Validate the breakout evidence before tracking it.
# ---------------------------------------------------------------------------
old_active = '''def _is_active(frame: pd.DataFrame) -> pd.Series:\n    score = _number(frame.get("Score", pd.Series(index=frame.index)))\n    signals = _number(frame.get("SignalCount", pd.Series(index=frame.index)))\n    passed = frame.get("PassedFilters", pd.Series(False, index=frame.index)).map(_bool)\n    return passed | ((score >= 35) & (signals >= 3))\n'''
new_active = '''def _is_active(frame: pd.DataFrame) -> pd.Series:\n    score = _number(frame.get("Score", pd.Series(index=frame.index)))\n    signals = _number(frame.get("SignalCount", pd.Series(index=frame.index)))\n    passed = _bool_series(frame, "PassedFilters")\n    universe_eligible = _bool_series(frame, "UniverseEligible", True)\n    entry_signal = _text_series(frame, "EntrySignal", "AVOID").str.upper()\n    strict_breakout_override = (\n        ~passed\n        & universe_eligible\n        & entry_signal.eq("BREAKOUT_CONFIRM")\n        & _bool_series(frame, "BreakoutVolumeConfirmed")\n        & _bool_series(frame, "BreakoutFlowConfirmed")\n    )\n    return passed | ((score >= 35) & (signals >= 3)) | strict_breakout_override\n'''
replace_once(
    "signal_lifecycle_core.py",
    old_active,
    new_active,
    "strict breakout lifecycle activity",
)

replace_once(
    "signal_lifecycle_core.py",
    '''    active = _is_active(result)\n    history = _load_history()\n''',
    '''    # Lifecycle activity and the later decision override must consume the\n    # same validated breakout evidence; otherwise a cautious candidate can\n    # incorrectly display as “无信号 / 0天”.\n    result = validate_signal_consistency(result)\n    active = _is_active(result)\n    history = _load_history()\n''',
    "validate before lifecycle activity",
)

# ---------------------------------------------------------------------------
# Research eligibility = directional-product policy AND hard universe gate.
# Diagnostics remain non-fatal, but minimum price/volume/cap/history cannot
# occupy a research Top50 after being labeled a hard gate.
# ---------------------------------------------------------------------------
old_policy_loop = '''        eligible, reason = etf_research_eligibility(\n            is_etf=is_etf,\n            name=row.get("Name", ""),\n            industry=row.get("Industry", ""),\n            sector=row.get("Sector", ""),\n            classification=row.get("ModelClassification", row.get("ETFTheme", "")),\n            ticker=row.get("Ticker", ""),\n        )\n        eligibility.append(bool(eligible))\n        reasons.append(str(reason or ""))\n'''
new_policy_loop = '''        eligible, reason = etf_research_eligibility(\n            is_etf=is_etf,\n            name=row.get("Name", ""),\n            industry=row.get("Industry", ""),\n            sector=row.get("Sector", ""),\n            classification=row.get("ModelClassification", row.get("ETFTheme", "")),\n            ticker=row.get("Ticker", ""),\n        )\n        hard_value = row.get("HardGatePassed", None)\n        try:\n            hard_missing = hard_value is None or pd.isna(hard_value)\n        except (TypeError, ValueError):\n            hard_missing = hard_value is None\n        if hard_missing or str(hard_value).strip() == "":\n            hard_value = row.get("UniverseEligible", True)\n        hard_ok = _truthy(hard_value)\n        if not hard_ok:\n            failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))\n            hard_reason = (\n                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"\n            )\n            reason = f"{reason}；{hard_reason}" if reason else hard_reason\n        eligibility.append(bool(eligible) and hard_ok)\n        reasons.append(str(reason or ""))\n'''
replace_once(
    "report.py",
    old_policy_loop,
    new_policy_loop,
    "hard gate in research policy",
)

# ---------------------------------------------------------------------------
# Publication invariant gate.  Current rich frames must not silently publish
# cross-version contradictions; legacy/minimal test frames are checked only for
# the columns they actually provide.
# ---------------------------------------------------------------------------
marker = '''def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:\n    """Return research-eligible valid results in canonical candidate order."""\n'''
invariant = '''def validate_decision_integrity(frame: pd.DataFrame) -> None:\n    """Fail closed on contradictions between eligibility, gate and lifecycle."""\n    if frame.empty:\n        return\n\n    violations: list[str] = []\n    ticker = frame.get("Ticker", pd.Series("", index=frame.index)).fillna("").astype(str)\n\n    if {"ResearchEligible", "HardGatePassed"}.issubset(frame.columns):\n        bad = _bool_series_for_integrity(frame, "ResearchEligible") & ~_bool_series_for_integrity(\n            frame, "HardGatePassed"\n        )\n        if bad.any():\n            violations.append(\n                "hard-gate rows marked research-eligible: " + ",".join(ticker.loc[bad].head(5))\n            )\n\n    if {"QualityReason", "QualityGate"}.issubset(frame.columns):\n        reason = frame["QualityReason"].fillna("").astype(str)\n        bad = reason.str.contains("行业自适应硬门槛通过", regex=False) & ~_bool_series_for_integrity(\n            frame, "QualityGate"\n        )\n        if bad.any():\n            violations.append(\n                "Fundamental Gate 2.0 pass rewritten to fail: " + ",".join(ticker.loc[bad].head(5))\n            )\n\n    lifecycle_columns = {"RankingEligibility", "SignalStatus", "SignalDays"}\n    if lifecycle_columns.issubset(frame.columns):\n        actionable = frame["RankingEligibility"].fillna("").astype(str).isin({"推荐", "谨慎候选"})\n        status = frame["SignalStatus"].fillna("").astype(str).str.upper().str.strip()\n        days = pd.to_numeric(frame["SignalDays"], errors="coerce").fillna(0.0)\n        bad = actionable & (status.eq("") | status.isin({"FAILED", "EXPIRED", "INACTIVE"}) | days.lt(1.0))\n        if bad.any():\n            violations.append(\n                "actionable rows without active lifecycle: " + ",".join(ticker.loc[bad].head(5))\n            )\n\n    if violations:\n        raise ValueError("Decision integrity violation: " + " | ".join(violations))\n\n\ndef _bool_series_for_integrity(frame: pd.DataFrame, column: str) -> pd.Series:\n    return frame.get(column, pd.Series(False, index=frame.index)).map(_truthy)\n\n\n'''
replace_once(
    "report.py",
    marker,
    invariant + marker,
    "decision integrity invariant helper",
)

replace_once(
    "report.py",
    '''    prepared = enrich_evidence_fields(_apply_research_policy(frame))\n    valid = prepared.loc[\n''',
    '''    prepared = enrich_evidence_fields(_apply_research_policy(frame))\n    validate_decision_integrity(prepared)\n    valid = prepared.loc[\n''',
    "validate prepared candidate frame",
)

# ---------------------------------------------------------------------------
# Focused v39 regression suite.
# ---------------------------------------------------------------------------
(ROOT / "test_v39_decision_integrity.py").write_text(
    '''from __future__ import annotations\n\nimport unittest\n\nimport pandas as pd\n\nimport config\nimport report\nimport signal_lifecycle_core as lifecycle_core\nfrom fundamental_quality import calculate_quality\n\n\nclass V39DecisionIntegrityTests(unittest.TestCase):\n    def test_gate2_holding_decline_is_supporting_evidence_not_veto_end_to_end(self):\n        quality = calculate_quality(\n            {\n                "Ticker": "600377.SH",\n                "Industry": "铁路公路",\n                "ROE": 11.48,\n                "GrossMargin": 20.0,\n                "IndustryGrossMarginPercentile": 0.50,\n                "InstitutionHoldingTrend": "decreasing",\n                "InstitutionHoldingPeriods": 3,\n                "NetProfitY1": 100.0,\n                "NetProfitY2": 95.0,\n                "NetProfitY3": 90.0,\n            }\n        )\n        self.assertTrue(quality.quality_gate)\n        self.assertEqual(quality.institution_holding_status, "FAIL")\n\n        frame = pd.DataFrame(\n            [\n                {\n                    "Ticker": "600377.SH",\n                    "AssetType": "stock",\n                    "IsETF": False,\n                    "Score": 60.0,\n                    "FinalScore": 60.0,\n                    "InstitutionalScore": 60.0,\n                    "EntrySignal": "WAIT_PULLBACK",\n                    "QualityApplicable": True,\n                    "QualityGate": quality.quality_gate,\n                    "QualityDataAvailable": quality.data_available,\n                    "QualityDataCompleteness": quality.quality_data_completeness,\n                    "QualityGateReason": quality.quality_gate_reason,\n                    "QualityMultiplier": quality.quality_multiplier,\n                    "QualityProfile": quality.quality_profile,\n                    "ProfitTrendStatus": quality.profit_trend_status,\n                    "CyclicalQualityOverride": quality.cyclical_quality_override,\n                    "QualityROE": quality.roe_factor,\n                    "QualityGrossMargin": quality.gross_margin_factor,\n                    "QualityNetProfit": quality.net_profit_factor,\n                    "ROE": quality.roe,\n                    "IndustryGrossMarginPercentile": quality.industry_gross_margin_percentile,\n                    "NetProfitY1": quality.net_profit_y1,\n                    "NetProfitY2": quality.net_profit_y2,\n                    "NetProfitY3": quality.net_profit_y3,\n                    "InstitutionHoldingTrend": quality.institution_holding_trend,\n                    "InstitutionHoldingPeriods": quality.institution_holding_periods,\n                    "InstitutionHoldingStatus": quality.institution_holding_status,\n                    "PassedFilters": True,\n                    "UniverseEligible": True,\n                    "ScoreCoverage": 1.0,\n                    "SignalRecencyDays": 0,\n                }\n            ]\n        )\n        out = lifecycle_core.finalize_signal_ranking(frame).iloc[0]\n        self.assertTrue(bool(out["QualityGate"]))\n        self.assertAlmostEqual(float(out["QualityMultiplier"]), float(quality.quality_multiplier), places=6)\n        self.assertIn("不单独否决", str(out["QualityGateReason"]))\n\n    def test_hard_gate_failure_cannot_enter_research_topn(self):\n        frame = pd.DataFrame(\n            [\n                {\n                    "Ticker": "000001.SZ",\n                    "AssetType": "stock",\n                    "IsETF": False,\n                    "RankingScore": 99.0,\n                    "HardGatePassed": False,\n                    "HardGateFailedNames": "min_price",\n                    "UniverseEligible": False,\n                    "Error": "",\n                },\n                {\n                    "Ticker": "000002.SZ",\n                    "AssetType": "stock",\n                    "IsETF": False,\n                    "RankingScore": 90.0,\n                    "HardGatePassed": True,\n                    "UniverseEligible": True,\n                    "Error": "",\n                },\n            ]\n        )\n        prepared = report._apply_research_policy(frame)\n        self.assertFalse(bool(prepared.loc[0, "ResearchEligible"]))\n        self.assertIn("min_price", str(prepared.loc[0, "ResearchExclusionReason"]))\n        ranked = report._rank_valid_candidates(frame)\n        self.assertEqual(ranked["Ticker"].tolist(), ["000002.SZ"])\n\n    def test_strict_breakout_override_is_lifecycle_active(self):\n        frame = pd.DataFrame(\n            [\n                {\n                    "Score": 30.0,\n                    "SignalCount": 1,\n                    "PassedFilters": False,\n                    "UniverseEligible": True,\n                    "EntrySignal": "BREAKOUT_CONFIRM",\n                    "BreakoutVolumeConfirmed": True,\n                    "BreakoutFlowConfirmed": True,\n                }\n            ]\n        )\n        self.assertTrue(bool(lifecycle_core._is_active(frame).iloc[0]))\n        frame.loc[0, "UniverseEligible"] = False\n        self.assertFalse(bool(lifecycle_core._is_active(frame).iloc[0]))\n\n    def test_publication_invariant_rejects_actionable_without_lifecycle(self):\n        frame = pd.DataFrame(\n            [\n                {\n                    "Ticker": "000001.SZ",\n                    "ResearchEligible": True,\n                    "HardGatePassed": True,\n                    "RankingEligibility": "谨慎候选",\n                    "SignalStatus": "",\n                    "SignalDays": 0,\n                    "QualityReason": "通用严格模型；行业自适应硬门槛通过",\n                    "QualityGate": True,\n                }\n            ]\n        )\n        with self.assertRaisesRegex(ValueError, "active lifecycle"):\n            report.validate_decision_integrity(frame)\n\n    def test_versions_advance_without_replacing_v38_gate_policy(self):\n        self.assertIn("v39", config.SCORING_VERSION)\n        self.assertIn("v39", config.PIPELINE_VERSION)\n        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)\n        self.assertIn("v39", config.DECISION_INTEGRITY_VERSION)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)
