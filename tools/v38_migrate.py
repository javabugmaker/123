from __future__ import annotations

import re
import subprocess
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
        raise RuntimeError(f"{label}: expected one match, got {count}")
    save(path, text.replace(old, new, 1))


def sub_once(path: str, pattern: str, replacement: str, label: str) -> None:
    text = load(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, got {count}")
    save(path, updated)


# ---------------------------------------------------------------------------
# v38 version and bounded profile thresholds.
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-11-v35-orthogonal-decision"\n'
    'PIPELINE_VERSION: str = "2026-08-12-v37-project-integrity-evidence"\n',
    'SCORING_VERSION: str = "2026-08-12-v38-fundamental-gate2"\n'
    'PIPELINE_VERSION: str = "2026-08-12-v38-fundamental-gate2-v37-integrity"\n'
    'FUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"\n',
    "v38 model version",
)
replace_once(
    "config.py",
    'LIFECYCLE_WEAKEN_RANKING_FACTOR: float = 0.82\n',
    'LIFECYCLE_WEAKEN_RANKING_FACTOR: float = 0.82\n\n'
    '# Fundamental Gate 2.0 keeps GENERAL strict and only adapts sectors whose\n'
    '# accounting/economic cycles make the universal v24 gate inappropriate.\n'
    'QUALITY_GENERAL_ROE_THRESHOLD: float = 10.0\n'
    'QUALITY_FINANCIAL_ROE_THRESHOLD: float = 6.0\n'
    'QUALITY_CYCLICAL_ROE_THRESHOLD: float = 5.0\n'
    'QUALITY_DEFENSIVE_ROE_THRESHOLD: float = 6.0\n'
    'QUALITY_GENERAL_MARGIN_MAX_PERCENTILE: float = 0.30\n'
    'QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE: float = 0.50\n'
    'QUALITY_RECOVERY_MIN_GROWTH: float = 0.15\n'
    'QUALITY_RESILIENT_MIN_LATEST_RATIO: float = 0.90\n',
    "v38 quality thresholds",
)


# ---------------------------------------------------------------------------
# Fundamental Gate 2.0.
# ---------------------------------------------------------------------------
replace_once(
    "fundamental_quality.py",
    '''from config import (\n    INSTITUTION_HOLDING_MIN_PERIODS,\n    QUALITY_MULTIPLIER_FAIL,\n    QUALITY_MULTIPLIER_PASS,\n    QUALITY_MULTIPLIER_UNKNOWN,\n)\n''',
    '''from config import (\n    INSTITUTION_HOLDING_MIN_PERIODS,\n    QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE,\n    QUALITY_CYCLICAL_ROE_THRESHOLD,\n    QUALITY_DEFENSIVE_ROE_THRESHOLD,\n    QUALITY_FINANCIAL_ROE_THRESHOLD,\n    QUALITY_GENERAL_MARGIN_MAX_PERCENTILE,\n    QUALITY_GENERAL_ROE_THRESHOLD,\n    QUALITY_MULTIPLIER_FAIL,\n    QUALITY_MULTIPLIER_PASS,\n    QUALITY_MULTIPLIER_UNKNOWN,\n    QUALITY_RECOVERY_MIN_GROWTH,\n    QUALITY_RESILIENT_MIN_LATEST_RATIO,\n)\n''',
    "fundamental config imports",
)
replace_once(
    "fundamental_quality.py",
    '    quality_multiplier: float = QUALITY_MULTIPLIER_UNKNOWN\n',
    '    quality_multiplier: float = QUALITY_MULTIPLIER_UNKNOWN\n'
    '    quality_profile: str = "GENERAL"\n'
    '    profit_trend_status: str = "UNKNOWN"\n'
    '    cyclical_quality_override: bool = False\n',
    "fundamental dataclass v38 fields",
)

helpers = '''\n\n_FINANCIAL_INDUSTRY_KEYWORDS = ("银行", "证券", "保险", "多元金融", "信托", "金融", "租赁")\n_CYCLICAL_INDUSTRY_KEYWORDS = (\n    "煤炭", "工业金属", "贵金属", "小金属", "钢铁", "水泥", "建材",\n    "化学原料", "化学制品", "化学纤维", "造纸", "航运", "港口", "养殖", "饲料",\n)\n_DEFENSIVE_INDUSTRY_KEYWORDS = (\n    "电力", "燃气", "水务", "环境治理", "铁路公路", "高速公路", "公用事业",\n)\n\n\ndef quality_profile(industry: str) -> str:\n    text = str(industry or "").strip()\n    if any(keyword in text for keyword in _FINANCIAL_INDUSTRY_KEYWORDS):\n        return "FINANCIAL"\n    if any(keyword in text for keyword in _CYCLICAL_INDUSTRY_KEYWORDS):\n        return "CYCLICAL"\n    if any(keyword in text for keyword in _DEFENSIVE_INDUSTRY_KEYWORDS):\n        return "DEFENSIVE"\n    return "GENERAL"\n\n\ndef profit_trend_status(y1: float, y2: float, y3: float) -> str:\n    """Classify newest-to-oldest three-year profit shape without look-ahead."""\n    if not all(np.isfinite(value) for value in (y1, y2, y3)):\n        return "UNKNOWN"\n    if y1 >= y2 >= y3:\n        return "STABLE_GROWTH"\n    recovery = (y1 - y2) / max(abs(y2), 1.0)\n    if (\n        y1 > 0\n        and y1 > y2\n        and y2 < y3\n        and (y2 <= 0 or recovery >= QUALITY_RECOVERY_MIN_GROWTH)\n    ):\n        return "RECOVERY"\n    if (\n        y1 > 0\n        and y2 > 0\n        and y1 >= QUALITY_RESILIENT_MIN_LATEST_RATIO * y2\n    ):\n        return "RESILIENT"\n    if (y1 <= 0 < y2) or (y1 < y2 <= y3):\n        return "DETERIORATING"\n    return "MIXED"\n\n\ndef _profit_strength(status: str, y1: float, y2: float, y3: float) -> float:\n    if status == "UNKNOWN":\n        return 0.5\n    if status == "RECOVERY":\n        if y2 <= 0 < y1:\n            return 0.85\n        recovery = (y1 - y2) / max(abs(y2), 1.0)\n        return float(np.clip(0.65 + 0.25 * recovery, 0.65, 0.90))\n    if status == "RESILIENT":\n        ratio = y1 / y2 if y2 > 0 else 0.0\n        return float(np.clip(0.55 + (ratio - 0.90), 0.55, 0.65))\n    if status == "DETERIORATING":\n        return 0.15\n    if status == "MIXED":\n        return 0.40\n    growth_values: list[float] = []\n    if abs(y2) > 1e-9:\n        growth_values.append((y1 - y2) / abs(y2))\n    if abs(y3) > 1e-9:\n        growth_values.append((y2 - y3) / abs(y3))\n    mean_growth = float(np.mean(growth_values)) if growth_values else 0.0\n    return float(np.clip(0.5 + mean_growth / 0.50, 0.0, 1.0))\n\n\ndef _profile_name(profile: str) -> str:\n    return {\n        "FINANCIAL": "金融",\n        "CYCLICAL": "周期",\n        "DEFENSIVE": "防守/公用事业",\n        "GENERAL": "通用严格",\n    }.get(profile, profile)\n'''
replace_once(
    "fundamental_quality.py",
    '\n\ndef calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:\n',
    helpers + '\n\ndef calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:\n',
    "v38 quality helpers",
)

new_calculate = '''def calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:\n    values = row.to_dict() if isinstance(row, pd.Series) else row\n    normalized_ticker = _ticker(values.get("Ticker", ticker))\n    industry = str(values.get("Industry", "") or "").strip()\n    profile = quality_profile(industry)\n    numeric = {\n        column: _number(values.get(column)) for column in FUNDAMENTAL_FACTOR_COLUMNS\n    }\n    trend = values.get("InstitutionHoldingTrend")\n    holding_status = _institution_holding_status(\n        trend, numeric["InstitutionHoldingPeriods"]\n    )\n    roe_available = np.isfinite(numeric["ROE"])\n    gross_margin_available = np.isfinite(numeric["IndustryGrossMarginPercentile"])\n    profit_available = all(\n        np.isfinite(numeric[column])\n        for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")\n    )\n    holding_available = holding_status in {"PASS", "FAIL"}\n    profit_status = profit_trend_status(\n        numeric["NetProfitY1"], numeric["NetProfitY2"], numeric["NetProfitY3"]\n    )\n\n    margin_applicable = profile in {"GENERAL", "CYCLICAL"}\n    if profile == "FINANCIAL":\n        roe_threshold = QUALITY_FINANCIAL_ROE_THRESHOLD\n        allowed_profit = {"STABLE_GROWTH", "RECOVERY", "RESILIENT"}\n        roe_label = f"ROE>={roe_threshold:g}%"\n        margin_label = ""\n        profit_label = "利润趋势稳定/恢复"\n    elif profile == "CYCLICAL":\n        roe_threshold = QUALITY_CYCLICAL_ROE_THRESHOLD\n        allowed_profit = {"STABLE_GROWTH", "RECOVERY"}\n        roe_label = f"ROE>={roe_threshold:g}%"\n        margin_label = f"毛利率行业前{int(QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE * 100)}%"\n        profit_label = "利润趋势稳定或触底回升"\n    elif profile == "DEFENSIVE":\n        roe_threshold = QUALITY_DEFENSIVE_ROE_THRESHOLD\n        allowed_profit = {"STABLE_GROWTH", "RESILIENT"}\n        roe_label = f"ROE>={roe_threshold:g}%"\n        margin_label = ""\n        profit_label = "利润保持稳定"\n    else:\n        roe_threshold = QUALITY_GENERAL_ROE_THRESHOLD\n        allowed_profit = {"STABLE_GROWTH"}\n        roe_label = f"ROE>{roe_threshold:g}%"\n        margin_label = f"毛利率行业前{int(QUALITY_GENERAL_MARGIN_MAX_PERCENTILE * 100)}%"\n        profit_label = "近3年净利润非下降"\n\n    roe_factor: bool | None\n    if not roe_available:\n        roe_factor = None\n    elif profile == "GENERAL":\n        roe_factor = numeric["ROE"] > roe_threshold\n    else:\n        roe_factor = numeric["ROE"] >= roe_threshold\n\n    margin_factor: bool | None = None\n    if margin_applicable:\n        if not gross_margin_available:\n            margin_factor = None\n        else:\n            threshold = (\n                QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE\n                if profile == "CYCLICAL"\n                else QUALITY_GENERAL_MARGIN_MAX_PERCENTILE\n            )\n            margin_factor = numeric["IndustryGrossMarginPercentile"] <= threshold\n\n    profit_factor = profit_status in allowed_profit if profit_available else None\n    holding_factor = (\n        True if holding_status == "PASS" else False if holding_status == "FAIL" else None\n    )\n\n    hard_factors: dict[str, bool | None] = {roe_label: roe_factor, profit_label: profit_factor}\n    if margin_applicable:\n        hard_factors[margin_label] = margin_factor\n    hard_failed = [name for name, value in hard_factors.items() if value is False]\n    hard_unknown = [name for name, value in hard_factors.items() if value is None]\n\n    evidence_available = [roe_available, profit_available, holding_available]\n    if margin_applicable:\n        evidence_available.append(gross_margin_available)\n    completeness = sum(float(value) for value in evidence_available) / len(evidence_available)\n    data_available = any(evidence_available)\n    quality_gate = not hard_failed\n    cyclical_override = bool(\n        profile == "CYCLICAL"\n        and profit_status == "RECOVERY"\n        and profit_factor is True\n        and roe_factor is not False\n        and margin_factor is not False\n    )\n\n    if hard_failed:\n        quality_multiplier = QUALITY_MULTIPLIER_FAIL\n    elif holding_status == "FAIL" or hard_unknown or holding_status == "UNKNOWN":\n        quality_multiplier = QUALITY_MULTIPLIER_UNKNOWN\n    else:\n        quality_multiplier = QUALITY_MULTIPLIER_PASS\n\n    reason_parts = [f"{_profile_name(profile)}模型"]\n    if hard_failed:\n        reason_parts.append("硬门槛未通过：" + "、".join(hard_failed))\n    else:\n        reason_parts.append("行业自适应硬门槛通过")\n    if cyclical_override:\n        reason_parts.append("周期利润触底回升已确认")\n    if holding_status == "FAIL":\n        reason_parts.append("辅助证据：机构覆盖家数未增加（不单独否决）")\n    elif holding_status == "UNKNOWN":\n        reason_parts.append("机构覆盖家数历史不足（中性）")\n    if hard_unknown:\n        reason_parts.append("数据不足：" + "、".join(hard_unknown))\n    reason = "；".join(reason_parts)\n\n    weighted_points = 0.0\n    available_weight = 0.0\n    if roe_available:\n        roe_scale = 20.0 if profile == "GENERAL" else 15.0\n        weighted_points += float(np.clip(numeric["ROE"] / roe_scale, 0.0, 1.0)) * 25.0\n        available_weight += 25.0\n    if margin_applicable and gross_margin_available:\n        weighted_points += float(\n            np.clip(1.0 - numeric["IndustryGrossMarginPercentile"], 0.0, 1.0)\n        ) * 20.0\n        available_weight += 20.0\n    if profit_available:\n        weighted_points += _profit_strength(\n            profit_status,\n            numeric["NetProfitY1"],\n            numeric["NetProfitY2"],\n            numeric["NetProfitY3"],\n        ) * 25.0\n        available_weight += 25.0\n    if holding_available:\n        weighted_points += 15.0 if holding_status == "PASS" else 0.0\n        available_weight += 15.0\n\n    if available_weight > 0:\n        observed_score = weighted_points / available_weight * 100.0\n        shrunk_factor_score = 50.0 + (observed_score - 50.0) * completeness\n        quality_score = round(float(np.clip(shrunk_factor_score, 0.0, 100.0)), 4)\n    else:\n        quality_score = np.nan\n\n    return FundamentalQuality(\n        ticker=normalized_ticker,\n        industry=industry,\n        roe=numeric["ROE"],\n        gross_margin=numeric["GrossMargin"],\n        institution_holding_trend=trend,\n        institution_holding_periods=numeric["InstitutionHoldingPeriods"],\n        net_profit_y1=numeric["NetProfitY1"],\n        net_profit_y2=numeric["NetProfitY2"],\n        net_profit_y3=numeric["NetProfitY3"],\n        industry_gross_margin_percentile=numeric["IndustryGrossMarginPercentile"],\n        roe_factor=bool(roe_factor),\n        gross_margin_factor=True if not margin_applicable else bool(margin_factor),\n        institution_holding_factor=bool(holding_factor),\n        net_profit_factor=bool(profit_factor),\n        quality_score=quality_score,\n        quality_gate=quality_gate,\n        quality_reason=reason,\n        data_available=data_available,\n        institution_holding_status=holding_status,\n        quality_data_completeness=round(completeness, 4),\n        quality_gate_reason=reason,\n        quality_multiplier=quality_multiplier,\n        quality_profile=profile,\n        profit_trend_status=profit_status,\n        cyclical_quality_override=cyclical_override,\n    )\n\n\n'''
sub_once(
    "fundamental_quality.py",
    r'def calculate_quality\(row: pd\.Series \| dict\[str, Any\], ticker: str = ""\) -> FundamentalQuality:\n.*?\n\ndef _path_value',
    new_calculate + 'def _path_value',
    "v38 calculate_quality",
)
replace_once(
    "fundamental_quality.py",
    '            quality_multiplier=QUALITY_MULTIPLIER_PASS,\n        )\n',
    '            quality_multiplier=QUALITY_MULTIPLIER_PASS,\n'
    '            quality_profile="ETF",\n'
    '            profit_trend_status="NOT_APPLICABLE",\n'
    '            cyclical_quality_override=False,\n'
    '        )\n',
    "ETF quality profile",
)


# ---------------------------------------------------------------------------
# Carry the new quality provenance through scanner/report/GUI.
# ---------------------------------------------------------------------------
replace_once(
    "scanner.py",
    '    quality_multiplier: float = 0.95\n',
    '    quality_multiplier: float = 0.95\n'
    '    quality_profile: str = "GENERAL"\n'
    '    quality_profit_trend_status: str = "UNKNOWN"\n'
    '    cyclical_quality_override: bool = False\n',
    "ScanResult v38 quality fields",
)
replace_once(
    "scanner.py",
    '''            quality_gate_reason=quality.quality_gate_reason,\n            quality_multiplier=quality.quality_multiplier,\n            model_classification=resolved_classification,\n''',
    '''            quality_gate_reason=quality.quality_gate_reason,\n            quality_multiplier=quality.quality_multiplier,\n            quality_profile=getattr(quality, "quality_profile", "GENERAL"),\n            quality_profit_trend_status=getattr(quality, "profit_trend_status", "UNKNOWN"),\n            cyclical_quality_override=bool(getattr(quality, "cyclical_quality_override", False)),\n            model_classification=resolved_classification,\n''',
    "scanner v38 quality assignment",
)
replace_once(
    "report.py",
    '''                "QualityGateReason": r.quality_gate_reason,\n                "QualityMultiplier": round(r.quality_multiplier, 4),\n                "BacktestSamples": r.backtest_samples,\n''',
    '''                "QualityGateReason": r.quality_gate_reason,\n                "QualityMultiplier": round(r.quality_multiplier, 4),\n                "QualityProfile": r.quality_profile,\n                "ProfitTrendStatus": r.quality_profit_trend_status,\n                "CyclicalQualityOverride": r.cyclical_quality_override,\n                "BacktestSamples": r.backtest_samples,\n''',
    "report v38 quality provenance",
)
replace_once(
    "report.py",
    '    "HardGatePassed", "DiagnosticFailedCount", "DiagnosticFailedNames",\n',
    '    "HardGatePassed", "DiagnosticFailedCount", "DiagnosticFailedNames",\n'
    '    "QualityProfile", "ProfitTrendStatus", "CyclicalQualityOverride",\n',
    "decision projection v38 quality fields",
)
replace_once(
    "gui.py",
    '        "EvidenceReason": "证据说明",\n',
    '        "EvidenceReason": "证据说明",\n'
    '        "QualityProfile": "基本面模型",\n'
    '        "ProfitTrendStatus": "利润趋势",\n'
    '        "CyclicalQualityOverride": "周期恢复放行",\n',
    "GUI v38 quality labels",
)


# ---------------------------------------------------------------------------
# Forward-compatible version contracts and focused v38 tests.
# ---------------------------------------------------------------------------
replace_once(
    "test_v35_model_integrity.py",
    '        self.assertIn("v35", config.SCORING_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))\n'
    '        )\n',
    "v35 scoring version forward compatibility",
)
replace_once(
    "test_v36_tickflow_volume_units.py",
    '        self.assertIn("v35", config.SCORING_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))\n'
    '        )\n',
    "v36 scoring version forward compatibility",
)
replace_once(
    "test_v37_project_integrity.py",
    '        self.assertIn("v35", config.SCORING_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))\n'
    '        )\n',
    "v37 scoring version forward compatibility",
)

save(
    "test_v38_fundamental_gate2.py",
    '''from __future__ import annotations\n\nimport unittest\n\nimport numpy as np\n\nimport config\nfrom fundamental_quality import calculate_quality\n\n\ndef row(\n    *,\n    industry: str,\n    roe: float,\n    margin_pct: float = np.nan,\n    y1: float,\n    y2: float,\n    y3: float,\n    holding: str | None = None,\n    holding_periods: int = 0,\n):\n    return {\n        "Ticker": "000001.SZ",\n        "Industry": industry,\n        "ROE": roe,\n        "GrossMargin": 30.0,\n        "IndustryGrossMarginPercentile": margin_pct,\n        "InstitutionHoldingTrend": holding,\n        "InstitutionHoldingPeriods": holding_periods,\n        "NetProfitY1": y1,\n        "NetProfitY2": y2,\n        "NetProfitY3": y3,\n    }\n\n\nclass FundamentalGateV38Tests(unittest.TestCase):\n    def test_cyclical_trough_recovery_can_pass_without_lowering_general_gate(self):\n        quality = calculate_quality(\n            row(industry="水泥", roe=7.0, margin_pct=0.45, y1=300.0, y2=100.0, y3=150.0)\n        )\n        self.assertEqual(quality.quality_profile, "CYCLICAL")\n        self.assertEqual(quality.profit_trend_status, "RECOVERY")\n        self.assertTrue(quality.cyclical_quality_override)\n        self.assertTrue(quality.quality_gate)\n\n        general = calculate_quality(\n            row(industry="通用设备", roe=7.0, margin_pct=0.20, y1=300.0, y2=200.0, y3=100.0)\n        )\n        self.assertEqual(general.quality_profile, "GENERAL")\n        self.assertFalse(general.quality_gate)\n\n    def test_cyclical_loss_or_bad_margin_still_fails(self):\n        loss = calculate_quality(\n            row(industry="造纸", roe=-10.0, margin_pct=0.40, y1=-20.0, y2=-30.0, y3=-40.0)\n        )\n        self.assertFalse(loss.quality_gate)\n        bad_margin = calculate_quality(\n            row(industry="饲料", roe=8.0, margin_pct=0.70, y1=120.0, y2=60.0, y3=90.0)\n        )\n        self.assertFalse(bad_margin.quality_gate)\n\n    def test_financial_profile_does_not_require_gross_margin(self):\n        bank = calculate_quality(\n            row(industry="银行Ⅱ", roe=12.0, y1=130.0, y2=120.0, y3=110.0)\n        )\n        self.assertEqual(bank.quality_profile, "FINANCIAL")\n        self.assertTrue(bank.quality_gate)\n        self.assertTrue(bank.gross_margin_factor)\n        self.assertAlmostEqual(bank.quality_data_completeness, 2 / 3, places=4)\n\n        weak_broker = calculate_quality(\n            row(industry="证券Ⅱ", roe=2.0, y1=130.0, y2=100.0, y3=-20.0)\n        )\n        self.assertFalse(weak_broker.quality_gate)\n\n    def test_defensive_profile_allows_small_profit_dip_not_structural_decline(self):\n        resilient = calculate_quality(\n            row(industry="环境治理", roe=8.0, margin_pct=0.90, y1=95.0, y2=100.0, y3=80.0)\n        )\n        self.assertEqual(resilient.quality_profile, "DEFENSIVE")\n        self.assertEqual(resilient.profit_trend_status, "RESILIENT")\n        self.assertTrue(resilient.quality_gate)\n\n        deteriorating = calculate_quality(\n            row(industry="燃气Ⅱ", roe=8.0, y1=50.0, y2=100.0, y3=120.0)\n        )\n        self.assertEqual(deteriorating.profit_trend_status, "DETERIORATING")\n        self.assertFalse(deteriorating.quality_gate)\n\n    def test_institution_coverage_is_supporting_not_a_standalone_fundamental_veto(self):\n        quality = calculate_quality(\n            row(\n                industry="铁路公路",\n                roe=11.0,\n                y1=95.0,\n                y2=100.0,\n                y3=80.0,\n                holding="decreasing",\n                holding_periods=3,\n            )\n        )\n        self.assertTrue(quality.quality_gate)\n        self.assertEqual(quality.institution_holding_status, "FAIL")\n        self.assertLess(quality.quality_multiplier, 1.0)\n        self.assertIn("不单独否决", quality.quality_reason)\n\n    def test_v38_advances_model_but_preserves_v37_and_v36_provenance(self):\n        self.assertIn("v38", config.SCORING_VERSION)\n        self.assertIn("v38", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.GUI_VERSION)\n        self.assertIn("v36", config.MARKET_DATA_VERSION)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
)

subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
