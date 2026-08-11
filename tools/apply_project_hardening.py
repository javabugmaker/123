from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def save(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, label: str) -> None:
    text = load(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    save(path, text.replace(old, new, 1))


def insert_before_once(path: str, marker: str, insertion: str, label: str) -> None:
    replace_once(path, marker, insertion + marker, label)


# ---------------------------------------------------------------------------
# v37: version/provenance only. Scoring semantics remain v35.
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    'PIPELINE_VERSION: str = "2026-08-12-v36-volume-shares-v35-model"\n',
    'PIPELINE_VERSION: str = "2026-08-12-v37-project-integrity-evidence"\n'
    'GUI_VERSION: str = "2026-08-12-v37-evidence-ux"\n'
    'EVIDENCE_POLICY_VERSION: str = "2026-08-12-v37-peer-plus-ticker"\n',
    "v37 config version",
)


# ---------------------------------------------------------------------------
# ETF research eligibility policy: exclude cash-management products by
# classification, without excluding equity cash-flow factor ETFs.
# ---------------------------------------------------------------------------
insert_before_once(
    "classification.py",
    "ETF_TRACKING_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (\n",
    '''ETF_RESEARCH_EXCLUDED_LABELS = frozenset(\n    {\n        "货币现金管理",\n        "货币ETF",\n        "现金管理",\n        "同业存单",\n        "短债",\n    }\n)\nETF_RESEARCH_EXCLUDED_KEYWORDS: tuple[str, ...] = (\n    "货币ETF",\n    "货币基金",\n    "现金管理",\n    "同业存单",\n    "短债",\n    "快钱",\n    "天天金",\n    "添益",\n)\n\n\n''',
    "ETF research exclusions",
)

insert_before_once(
    "classification.py",
    "def theme_cluster(\n",
    '''def etf_research_eligibility(\n    *,\n    is_etf: bool,\n    name: Any = "",\n    industry: Any = "",\n    sector: Any = "",\n    classification: Any = "",\n    ticker: Any = "",\n) -> tuple[bool, str]:\n    """Return whether an asset belongs in directional equity/ETF research lists.\n\n    Cash-management and cash-equivalent ETFs are intentionally excluded from\n    signal Top50 surfaces. Equity-factor products such as ``现金流因子`` remain\n    eligible because the exclusion uses exact labels/specific product keywords,\n    not a broad ``现金`` substring.\n    """\n    if not is_etf:\n        return True, ""\n    resolved = safe_text(classification) or etf_theme_key(\n        name=name, industry=industry, sector=sector, ticker=ticker\n    )\n    text = _classification_text(name, industry, sector, resolved)\n    if resolved in ETF_RESEARCH_EXCLUDED_LABELS:\n        return False, f"ETF分类排除：{resolved}"\n    for keyword in ETF_RESEARCH_EXCLUDED_KEYWORDS:\n        if keyword.upper() in text:\n            return False, f"ETF现金管理产品排除：{keyword}"\n    return True, ""\n\n\n''',
    "ETF research eligibility helper",
)


# ---------------------------------------------------------------------------
# Evidence-strength presentation model. This is deliberately NOT imported by
# the scoring engine and never changes RankingScore.
# ---------------------------------------------------------------------------
save(
    "evidence.py",
    '''"""Non-alpha evidence-strength fields for research/UI presentation.\n\nThe scanner has two different historical evidence sources:\n1. per-ticker backtest samples;\n2. peer/global calibration cohorts.\n\nThis module summarizes *confidence/coverage*, not expected return, and must not\nfeed back into RankingScore or trade eligibility.\n"""\n\nfrom __future__ import annotations\n\nimport numpy as np\nimport pandas as pd\n\n\ndef _number(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:\n    return (\n        pd.to_numeric(frame.get(column, pd.Series(default, index=frame.index)), errors="coerce")\n        .replace([np.inf, -np.inf], np.nan)\n        .fillna(default)\n        .astype(float)\n    )\n\n\ndef _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:\n    return frame.get(column, pd.Series(default, index=frame.index)).fillna(default).astype(str).str.strip()\n\n\ndef enrich_evidence_fields(frame: pd.DataFrame) -> pd.DataFrame:\n    """Add explainable evidence-strength fields without changing ranking."""\n    result = frame.copy()\n    if result.empty:\n        for column, dtype in (\n            ("TickerEvidence", "object"),\n            ("PeerCalibrationEvidence", "object"),\n            ("EvidenceStrengthScore", "float64"),\n            ("EvidenceTier", "object"),\n            ("EvidenceReason", "object"),\n        ):\n            result[column] = pd.Series(dtype=dtype)\n        return result\n\n    samples = _number(result, "BacktestSamples", 0.0).clip(lower=0.0)\n    effective = _number(result, "BacktestEffectiveSamples", 0.0).clip(lower=0.0)\n    mode = _text(result, "BacktestMode", "NONE").str.upper().replace("", "NONE")\n    ticker_tier = _text(result, "BacktestConfidenceTier", "未评估").replace("", "未评估")\n    ticker_strength = (effective / 20.0).clip(0.0, 1.0)\n\n    peer_samples = _number(result, "GlobalCalibrationSamples", 0.0).clip(lower=0.0)\n    peer_effective = _number(result, "GlobalCalibrationEffectiveSamples", 0.0).clip(lower=0.0)\n    peer_count = peer_effective.where(peer_effective.gt(0.0), peer_samples)\n    peer_confidence = _number(result, "GlobalCalibrationConfidence", 0.0).clip(0.0, 1.0)\n    peer_level = _text(result, "GlobalCalibrationLevel", "none").replace("", "none")\n    # Confidence already contains calibration sample-quality logic. The smooth\n    # sample term prevents a tiny cohort with a high numerical confidence from\n    # looking equivalent to a mature peer cohort.\n    peer_sample_strength = np.log1p(peer_count).div(np.log1p(100.0)).clip(0.0, 1.0)\n    peer_strength = (peer_confidence * peer_sample_strength).clip(0.0, 1.0)\n\n    evidence = (0.35 * ticker_strength + 0.65 * peer_strength) * 100.0\n    has_any = samples.gt(0.0) | peer_count.gt(0.0)\n    evidence = evidence.where(has_any, 0.0).clip(0.0, 100.0)\n    tier = pd.Series("不足", index=result.index, dtype="object")\n    tier.loc[has_any & evidence.lt(30.0)] = "低"\n    tier.loc[evidence.ge(30.0)] = "中"\n    tier.loc[evidence.ge(55.0)] = "中高"\n    tier.loc[evidence.ge(75.0)] = "高"\n\n    result["TickerEvidence"] = [\n        f"{m} · {int(s)}样本 · {t}"\n        for m, s, t in zip(mode, samples, ticker_tier)\n    ]\n    result["PeerCalibrationEvidence"] = [\n        f"{level} · {count:.0f}有效样本 · {confidence:.0%}"\n        if count > 0\n        else "无同类校准样本"\n        for level, count, confidence in zip(peer_level, peer_count, peer_confidence)\n    ]\n    result["EvidenceStrengthScore"] = evidence.round(2)\n    result["EvidenceTier"] = tier\n    result["EvidenceReason"] = [\n        f"本票有效样本 {ticker_eff:.1f}；同类有效样本 {peer_eff:.1f}，同类置信度 {peer_conf:.0%}。证据等级仅描述历史覆盖，不参与排序。"\n        for ticker_eff, peer_eff, peer_conf in zip(effective, peer_count, peer_confidence)\n    ]\n    return result\n''',
)


# ---------------------------------------------------------------------------
# Report/export semantics: research eligibility, hard-gate vs diagnostics,
# evidence columns and compatibility fields.
# ---------------------------------------------------------------------------
replace_once(
    "report.py",
    "from classification import etf_theme_key, etf_tracking_key, theme_cluster\n",
    "from classification import (\n"
    "    etf_research_eligibility,\n"
    "    etf_theme_key,\n"
    "    etf_tracking_key,\n"
    "    theme_cluster,\n"
    ")\n"
    "from evidence import enrich_evidence_fields\n",
    "report imports",
)

insert_before_once(
    "report.py",
    "def _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:\n",
    '''_HARD_GATE_FILTER_KEYS = ("min_price", "min_volume", "min_market_cap", "sufficient_history")\n_DIAGNOSTIC_FILTER_KEYS = (\n    "volume_accumulation",\n    "obv_divergence",\n    "cmf_positive",\n    "ad_slope",\n    "consolidation",\n    "volatility_contraction",\n)\n\n\ndef _failed_filter_names(result: ScanResult, keys: tuple[str, ...]) -> list[str]:\n    names: list[str] = []\n    for key in keys:\n        if result.is_etf and key in {"min_price", "min_market_cap"}:\n            continue\n        if not bool(result.filter_details.get(key, False)):\n            names.append(key)\n    return names\n\n\n''',
    "report filter semantics helpers",
)

replace_once(
    "report.py",
    '''                "PassedFilters": r.passed_filters,\n                "UniverseEligible": r.universe_eligible,\n                "SignalConfirmed": r.signal_confirmed,\n                "FailedFilterCount": r.failed_filter_count,\n                "FailedFilterNames": r.failed_filter_names,\n                "MinPricePassed": r.filter_details.get("min_price", False),\n''',
    '''                # Compatibility field: historically this meant the combined\n                # hard-gate + accumulation/structure recipe, not "every filter".\n                "PassedFilters": r.passed_filters,\n                "UniverseEligible": r.universe_eligible,\n                "HardGatePassed": r.universe_eligible,\n                "HardGateFailedCount": len(_failed_filter_names(r, _HARD_GATE_FILTER_KEYS)),\n                "HardGateFailedNames": ",".join(_failed_filter_names(r, _HARD_GATE_FILTER_KEYS)),\n                "SignalConfirmed": r.signal_confirmed,\n                "DiagnosticFailedCount": len(_failed_filter_names(r, _DIAGNOSTIC_FILTER_KEYS)),\n                "DiagnosticFailedNames": ",".join(_failed_filter_names(r, _DIAGNOSTIC_FILTER_KEYS)),\n                "FailedFilterCount": r.failed_filter_count,\n                "FailedFilterNames": r.failed_filter_names,\n                "MinPricePassed": r.filter_details.get("min_price", False),\n''',
    "report hard gate diagnostics",
)

insert_before_once(
    "report.py",
    "def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:\n",
    '''def _apply_research_policy(frame: pd.DataFrame) -> pd.DataFrame:\n    """Mark non-directional ETF products before any TopN candidate ranking."""\n    working = frame.copy()\n    if working.empty:\n        working["ResearchEligible"] = pd.Series(dtype=bool)\n        working["ResearchExclusionReason"] = pd.Series(dtype="object")\n        return working\n    eligibility: list[bool] = []\n    reasons: list[str] = []\n    for _, row in working.iterrows():\n        asset = str(row.get("AssetType", "") or "").strip().lower()\n        is_etf = _truthy(row.get("IsETF", False)) or asset == "etf"\n        eligible, reason = etf_research_eligibility(\n            is_etf=is_etf,\n            name=row.get("Name", ""),\n            industry=row.get("Industry", ""),\n            sector=row.get("Sector", ""),\n            classification=row.get("ModelClassification", row.get("ETFTheme", "")),\n            ticker=row.get("Ticker", ""),\n        )\n        eligibility.append(bool(eligible))\n        reasons.append(str(reason or ""))\n    working["ResearchEligible"] = eligibility\n    working["ResearchExclusionReason"] = reasons\n    return working\n\n\n''',
    "report research policy helper",
)

replace_once(
    "report.py",
    '''def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:\n    """Return valid results in the same order used by every candidate export."""\n    if frame.empty:\n        return frame.copy()\n    valid = frame.loc[\n        frame.get("Error", pd.Series("", index=frame.index))\n        .fillna("")\n        .astype(str)\n        .str.strip()\n        .eq("")\n    ].copy()\n''',
    '''def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:\n    """Return research-eligible valid results in canonical candidate order."""\n    if frame.empty:\n        return enrich_evidence_fields(_apply_research_policy(frame))\n    prepared = enrich_evidence_fields(_apply_research_policy(frame))\n    valid = prepared.loc[\n        prepared.get("Error", pd.Series("", index=prepared.index))\n        .fillna("")\n        .astype(str)\n        .str.strip()\n        .eq("")\n        & prepared["ResearchEligible"].fillna(False).astype(bool)\n    ].copy()\n''',
    "report candidate eligibility",
)

replace_once(
    "report.py",
    '''DECISION_RESULT_COLUMNS: tuple[str, ...] = (\n    "Ticker", "Name", "Sector", "Industry", "ETFTheme", "IsETF", "AssetType",\n    "ModelClassification", "ThemeCluster", "Close", "EntrySignal", "SignalStatus",\n    "SignalDays", "EntryZone", "BreakoutBuyPrice", "StopLoss", "RankingEligibility",\n    "RankingScore", "ResearchPoolRank", "OverallRank", "InstitutionalTier",\n    "InstitutionalScore", "TradeReadinessReason", "RankingReason", "DecisionState",\n    "BacktestRunMode", "BacktestMode", "BacktestStage", "BacktestSamples",\n    "BacktestStatus", "BacktestConfidenceTier", "BacktestRequested",\n    "BacktestEligibleForRanking", "BacktestSkipReason", "BacktestCacheHit",\n    "DataAsOf", "DataTradingAgeDays", "RunId", "ModelVersion", "PipelineVersion",\n)\n''',
    '''DECISION_RESULT_COLUMNS: tuple[str, ...] = (\n    "Ticker", "Name", "Sector", "Industry", "ETFTheme", "IsETF", "AssetType",\n    "ModelClassification", "ThemeCluster", "ResearchEligible", "ResearchExclusionReason",\n    "Close", "EntrySignal", "SignalStatus", "SignalDays", "EntryZone",\n    "BreakoutBuyPrice", "StopLoss", "RankingEligibility", "RankingScore",\n    "ResearchPoolRank", "OverallRank", "InstitutionalTier", "InstitutionalScore",\n    "HardGatePassed", "DiagnosticFailedCount", "DiagnosticFailedNames",\n    "TradeReadinessReason", "RankingReason", "DecisionState", "BacktestRunMode",\n    "BacktestMode", "BacktestStage", "BacktestSamples", "BacktestEffectiveSamples",\n    "BacktestStatus", "BacktestConfidenceTier", "BacktestRequested",\n    "BacktestEligibleForRanking", "BacktestSkipReason", "BacktestCacheHit",\n    "GlobalCalibrationSamples", "GlobalCalibrationEffectiveSamples",\n    "GlobalCalibrationConfidence", "GlobalCalibrationLevel", "TickerEvidence",\n    "PeerCalibrationEvidence", "EvidenceStrengthScore", "EvidenceTier", "EvidenceReason",\n    "DataAsOf", "DataTradingAgeDays", "RunId", "ModelVersion", "PipelineVersion",\n)\n''',
    "decision projection evidence columns",
)

replace_once(
    "report.py",
    '    df = enrich_signal_lifecycle(_results_to_dataframe(results))\n',
    '    df = enrich_evidence_fields(\n        _apply_research_policy(enrich_signal_lifecycle(_results_to_dataframe(results)))\n    )\n',
    "all results research/evidence enrichment",
)


# ---------------------------------------------------------------------------
# GUI: three separate evidence rows + clearer filter semantics + cache health.
# ---------------------------------------------------------------------------
replace_once(
    "gui.py",
    '''        "BacktestSkipReason": "回测说明",\n    }\n)\n''',
    '''        "BacktestSkipReason": "回测说明",\n        "HardGatePassed": "基础硬准入",\n        "DiagnosticFailedCount": "诊断未通过数",\n        "DiagnosticFailedNames": "诊断未通过项",\n        "ResearchEligible": "研究榜资格",\n        "ResearchExclusionReason": "研究榜排除原因",\n        "TickerEvidence": "本票回测证据",\n        "PeerCalibrationEvidence": "同类校准证据",\n        "EvidenceStrengthScore": "证据强度",\n        "EvidenceTier": "证据等级",\n        "EvidenceReason": "证据说明",\n    }\n)\n''',
    "GUI evidence column names",
)

replace_once(
    "gui.py",
    '        self.detail_backtest = tk.StringVar(master=root, value="-")\n',
    '        self.detail_backtest = tk.StringVar(master=root, value="-")\n'
    '        self.detail_peer_calibration = tk.StringVar(master=root, value="-")\n'
    '        self.detail_evidence = tk.StringVar(master=root, value="-")\n',
    "GUI evidence variables",
)

replace_once(
    "gui.py",
    '''            ("排序 / 机构", self.detail_score),\n            ("回测证据", self.detail_backtest),\n        ):\n''',
    '''            ("排序 / 机构", self.detail_score),\n            ("本票回测", self.detail_backtest),\n            ("同类校准", self.detail_peer_calibration),\n            ("证据等级", self.detail_evidence),\n        ):\n''',
    "GUI evidence rows",
)

replace_once(
    "gui.py",
    '''        self.detail_score.set("-")\n        self.detail_backtest.set("-")\n        self.detail_reason.set("双击可查看完整研究字段。")\n''',
    '''        self.detail_score.set("-")\n        self.detail_backtest.set("-")\n        self.detail_peer_calibration.set("-")\n        self.detail_evidence.set("-")\n        self.detail_reason.set("双击可查看完整研究字段。")\n''',
    "GUI evidence reset",
)

replace_once(
    "gui.py",
    '''        self.detail_backtest.set(" · ".join(value for value in backtest_parts if value) or "-")\n        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"\n''',
    '''        ticker_evidence = str(data.get("TickerEvidence", "") or "").strip()\n        self.detail_backtest.set(ticker_evidence or " · ".join(value for value in backtest_parts if value) or "-")\n        peer_evidence = str(data.get("PeerCalibrationEvidence", "") or "").strip()\n        self.detail_peer_calibration.set(peer_evidence or "-")\n        evidence_tier = str(data.get("EvidenceTier", "") or "").strip()\n        evidence_score = self._format_table_value(\n            "EvidenceStrengthScore", data.get("EvidenceStrengthScore", "")\n        )\n        self.detail_evidence.set(\n            " · ".join(value for value in (evidence_tier, evidence_score) if value) or "-"\n        )\n        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"\n        evidence_reason = str(data.get("EvidenceReason", "") or "").strip()\n        if evidence_reason:\n            reason = f"{reason}\\n\\n证据：{evidence_reason}"\n''',
    "GUI evidence content",
)

replace_once(
    "gui.py",
    '''        cache = float(backtest.get("cache_hit_rate", 0.0) or 0.0)\n        self.run_quality.set(\n            f"✓ {expected} · 总{_duration_label(elapsed)} · 扫描{_duration_label(scan_seconds)} · "\n            f"引擎{_duration_label(engine_seconds)} · 后处理{_duration_label(postprocess_seconds)} · Cache {cache:.0%}"\n        )\n''',
    '''        cache = float(backtest.get("cache_hit_rate", 0.0) or 0.0)\n        cache_health = str(backtest.get("cache_health", "") or "").strip()\n        cache_label = f"Cache {cache:.0%}" + (f"·{cache_health}" if cache_health else "")\n        self.run_quality.set(\n            f"✓ {expected} · 总{_duration_label(elapsed)} · 扫描{_duration_label(scan_seconds)} · "\n            f"引擎{_duration_label(engine_seconds)} · 后处理{_duration_label(postprocess_seconds)} · {cache_label}"\n        )\n''',
    "GUI cache-health footer",
)

replace_once(
    "gui.py",
    '''            f"  Cache：{float(backtest.get('cache_hit_rate', 0.0) or 0.0):.2%}",\n        ]\n''',
    '''            f"  Cache：{float(backtest.get('cache_hit_rate', 0.0) or 0.0):.2%}",\n            f"  Cache健康：{backtest.get('cache_health', '-')}",\n            f"  较上轮：{float(backtest.get('cache_hit_rate_delta', 0.0) or 0.0):+.2%}",\n        ]\n''',
    "GUI cache-health details",
)


# ---------------------------------------------------------------------------
# Daily pipeline: immutable run archives with SHA-256 provenance and cache
# health monitoring. Low cache is observable, not a publication failure.
# ---------------------------------------------------------------------------
replace_once(
    "daily_pipeline.py",
    "import csv\nimport json\n",
    "import csv\nimport hashlib\nimport json\n",
    "daily hashlib import",
)

archive_start = load("daily_pipeline.py")
old_archive = '''def _archive_run(pipeline_run_id: str, payload: dict[str, object]) -> Path:\n    run_dir = OUTPUT_DIR / "runs" / pipeline_run_id\n    if run_dir.exists():\n        shutil.rmtree(run_dir)\n    run_dir.mkdir(parents=True, exist_ok=True)\n    try:\n        for name in _ARCHIVE_FILES:\n            source = OUTPUT_DIR / name\n            if source.exists() and source.is_file():\n                shutil.copy2(source, run_dir / name)\n        _atomic_write_json(run_dir / "RunManifest.json", payload)\n        _atomic_write_json(\n            OUTPUT_DIR / "LatestRun.json",\n            {\n                "run_id": pipeline_run_id,\n                "data_run_id": payload.get("data_run_id", ""),\n                "expected_trading_date": payload.get("expected_trading_date", ""),\n                "run_dir": str(run_dir.relative_to(OUTPUT_DIR)),\n                "pipeline_version": PIPELINE_VERSION,\n            },\n        )\n    except Exception:\n        shutil.rmtree(run_dir, ignore_errors=True)\n        raise\n    return run_dir\n\n\n'''
new_archive = '''def _sha256_file(path: Path) -> str:\n    digest = hashlib.sha256()\n    with path.open("rb") as file:\n        for chunk in iter(lambda: file.read(1024 * 1024), b""):\n            digest.update(chunk)\n    return digest.hexdigest()\n\n\ndef _archive_run(pipeline_run_id: str, payload: dict[str, object]) -> Path:\n    """Create an immutable per-run snapshot; never overwrite an existing run id."""\n    run_dir = OUTPUT_DIR / "runs" / pipeline_run_id\n    if run_dir.exists():\n        raise FileExistsError(f"run archive already exists: {pipeline_run_id}")\n    run_dir.mkdir(parents=True, exist_ok=False)\n    try:\n        archive_hashes: dict[str, str] = {}\n        for name in _ARCHIVE_FILES:\n            source = OUTPUT_DIR / name\n            if source.exists() and source.is_file():\n                destination = run_dir / name\n                shutil.copy2(source, destination)\n                archive_hashes[name] = _sha256_file(destination)\n        manifest_payload = dict(payload)\n        manifest_payload["archive_hashes_sha256"] = archive_hashes\n        manifest_payload["archive_immutable"] = True\n        _atomic_write_json(run_dir / "RunManifest.json", manifest_payload)\n        _atomic_write_json(\n            OUTPUT_DIR / "LatestRun.json",\n            {\n                "run_id": pipeline_run_id,\n                "data_run_id": payload.get("data_run_id", ""),\n                "expected_trading_date": payload.get("expected_trading_date", ""),\n                "run_dir": str(run_dir.relative_to(OUTPUT_DIR)),\n                "pipeline_version": PIPELINE_VERSION,\n            },\n        )\n    except Exception:\n        shutil.rmtree(run_dir, ignore_errors=True)\n        raise\n    return run_dir\n\n\ndef _cache_health(\n    previous_summary: dict[str, object],\n    current_rate: float,\n    evaluations: int,\n) -> dict[str, object]:\n    previous_backtest = previous_summary.get("backtest", {})\n    if not isinstance(previous_backtest, dict):\n        previous_backtest = {}\n    previous_rate = float(previous_backtest.get("cache_hit_rate", 0.0) or 0.0)\n    previous_version = str(previous_summary.get("pipeline_version", "") or "")\n    cold_start = bool(previous_version and previous_version != PIPELINE_VERSION) or not previous_version\n    rate = float(max(0.0, min(1.0, current_rate)))\n    if evaluations <= 0:\n        status = "未知"\n    elif cold_start:\n        status = "冷启动"\n    elif rate >= 0.70:\n        status = "健康"\n    elif rate >= 0.35:\n        status = "偏低"\n    else:\n        status = "异常偏低"\n    return {\n        "status": status,\n        "cold_start": cold_start,\n        "current_rate": round(rate, 4),\n        "previous_rate": round(previous_rate, 4),\n        "delta": round(rate - previous_rate, 4),\n        "warning": bool(status == "异常偏低" and evaluations >= 100),\n    }\n\n\n'''
if old_archive not in archive_start:
    raise RuntimeError("daily immutable archive: expected function body not found")
save("daily_pipeline.py", archive_start.replace(old_archive, new_archive, 1))

replace_once(
    "daily_pipeline.py",
    '''    final_profiles: dict[str, dict[str, object]],\n    stage_seconds: dict[str, float],\n) -> dict[str, object]:\n''',
    '''    final_profiles: dict[str, dict[str, object]],\n    stage_seconds: dict[str, float],\n    previous_summary: dict[str, object],\n) -> dict[str, object]:\n''',
    "manifest previous-summary parameter",
)

replace_once(
    "daily_pipeline.py",
    '''    if cache_hit_rate <= 0 and evaluations > 0:\n        cache_hit_rate = cache_hits / evaluations\n    exact_elapsed = float(backtest.get("exact_refinement_elapsed_seconds", 0.0) or 0.0)\n''',
    '''    if cache_hit_rate <= 0 and evaluations > 0:\n        cache_hit_rate = cache_hits / evaluations\n    cache_health = _cache_health(previous_summary, cache_hit_rate, evaluations)\n    exact_elapsed = float(backtest.get("exact_refinement_elapsed_seconds", 0.0) or 0.0)\n''',
    "manifest cache health calculation",
)

replace_once(
    "daily_pipeline.py",
    '''            "cache_hits": cache_hits,\n            "cache_hit_rate": round(cache_hit_rate, 4),\n            "elapsed_seconds": round(backtest_elapsed, 3),\n''',
    '''            "cache_hits": cache_hits,\n            "cache_hit_rate": round(cache_hit_rate, 4),\n            "cache_health": str(cache_health.get("status", "未知")),\n            "cache_cold_start": bool(cache_health.get("cold_start", False)),\n            "cache_hit_rate_delta": float(cache_health.get("delta", 0.0) or 0.0),\n            "cache_warning": bool(cache_health.get("warning", False)),\n            "elapsed_seconds": round(backtest_elapsed, 3),\n''',
    "manifest cache health fields",
)

replace_once(
    "daily_pipeline.py",
    '''            final_profiles=final_profiles,\n            stage_seconds=stage_seconds,\n        )\n''',
    '''            final_profiles=final_profiles,\n            stage_seconds=stage_seconds,\n            previous_summary=previous_summary,\n        )\n''',
    "manifest cache baseline call",
)


# ---------------------------------------------------------------------------
# v37 regression suite.
# ---------------------------------------------------------------------------
save(
    "test_v37_project_integrity.py",
    '''from __future__ import annotations\n\nimport json\nimport tempfile\nimport unittest\nfrom pathlib import Path\nfrom unittest.mock import patch\n\nimport pandas as pd\n\nimport config\nimport daily_pipeline\nfrom classification import etf_research_eligibility\nfrom evidence import enrich_evidence_fields\nfrom report import _rank_valid_candidates\n\n\nclass V37ProjectIntegrityTests(unittest.TestCase):\n    def test_cash_management_etf_is_excluded_but_cashflow_factor_remains(self) -> None:\n        excluded, reason = etf_research_eligibility(\n            is_etf=True,\n            name="快钱ETF汇添富",\n            classification="货币现金管理",\n            ticker="159005.SZ",\n        )\n        self.assertFalse(excluded)\n        self.assertIn("排除", reason)\n        eligible, _ = etf_research_eligibility(\n            is_etf=True,\n            name="现金流ETF",\n            classification="现金流因子",\n            ticker="560000.SH",\n        )\n        self.assertTrue(eligible)\n\n    def test_candidate_rank_omits_cash_management_etf(self) -> None:\n        frame = pd.DataFrame(\n            {\n                "Ticker": ["159005.SZ", "560000.SH", "000001.SZ"],\n                "Name": ["快钱ETF汇添富", "现金流ETF", "平安银行"],\n                "IsETF": [True, True, False],\n                "AssetType": ["etf", "etf", "stock"],\n                "ModelClassification": ["货币现金管理", "现金流因子", "银行"],\n                "RankingScore": [99.0, 60.0, 55.0],\n                "InstitutionalScore": [50.0, 40.0, 38.0],\n                "Error": ["", "", ""],\n            }\n        )\n        ranked = _rank_valid_candidates(frame)\n        self.assertNotIn("159005.SZ", set(ranked["Ticker"]))\n        self.assertIn("560000.SH", set(ranked["Ticker"]))\n        self.assertIn("000001.SZ", set(ranked["Ticker"]))\n        self.assertTrue(ranked["ResearchEligible"].all())\n\n    def test_evidence_strength_uses_ticker_and_peer_coverage_without_ranking(self) -> None:\n        frame = pd.DataFrame(\n            {\n                "Ticker": ["A", "B"],\n                "RankingScore": [77.0, 66.0],\n                "BacktestMode": ["FAST", "FAST"],\n                "BacktestSamples": [0, 20],\n                "BacktestEffectiveSamples": [0.0, 20.0],\n                "BacktestConfidenceTier": ["样本不足", "中可信度"],\n                "GlobalCalibrationSamples": [10000, 10000],\n                "GlobalCalibrationEffectiveSamples": [1000.0, 1000.0],\n                "GlobalCalibrationConfidence": [1.0, 1.0],\n                "GlobalCalibrationLevel": ["asset_signal", "asset_signal"],\n            }\n        )\n        enriched = enrich_evidence_fields(frame)\n        self.assertEqual(list(enriched["RankingScore"]), [77.0, 66.0])\n        self.assertGreater(float(enriched.loc[0, "EvidenceStrengthScore"]), 50.0)\n        self.assertGreater(\n            float(enriched.loc[1, "EvidenceStrengthScore"]),\n            float(enriched.loc[0, "EvidenceStrengthScore"]),\n        )\n        self.assertIn(enriched.loc[0, "EvidenceTier"], {"中高", "高"})\n\n    def test_cache_health_distinguishes_cold_start_and_persistent_miss(self) -> None:\n        cold = daily_pipeline._cache_health(\n            {"pipeline_version": "old", "backtest": {"cache_hit_rate": 0.9}},\n            0.0,\n            6000,\n        )\n        self.assertEqual(cold["status"], "冷启动")\n        warm = daily_pipeline._cache_health(\n            {"pipeline_version": config.PIPELINE_VERSION, "backtest": {"cache_hit_rate": 0.8}},\n            0.1,\n            6000,\n        )\n        self.assertEqual(warm["status"], "异常偏低")\n        self.assertTrue(warm["warning"])\n\n    def test_run_archive_is_immutable_and_hashed(self) -> None:\n        with tempfile.TemporaryDirectory() as tmp:\n            output = Path(tmp)\n            (output / "Top50Mixed.csv").write_text("Ticker\\n000001.SZ\\n", encoding="utf-8")\n            with patch.object(daily_pipeline, "OUTPUT_DIR", output):\n                run_dir = daily_pipeline._archive_run(\n                    "run-1",\n                    {"data_run_id": "data-1", "expected_trading_date": "2026-08-11"},\n                )\n                manifest = json.loads((run_dir / "RunManifest.json").read_text(encoding="utf-8"))\n                self.assertTrue(manifest["archive_immutable"])\n                self.assertIn("Top50Mixed.csv", manifest["archive_hashes_sha256"])\n                with self.assertRaises(FileExistsError):\n                    daily_pipeline._archive_run("run-1", {})\n\n    def test_v37_does_not_change_scoring_model_version(self) -> None:\n        self.assertIn("v35", config.SCORING_VERSION)\n        self.assertIn("v37", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.GUI_VERSION)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
)

# Stage every generated product change. The legacy workflow's later explicit
# `git add` calls do not clear this index, so new modules/report/daily files are
# included in the validated commit without broadening the workflow itself.
subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
