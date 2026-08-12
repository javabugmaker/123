from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# config.py: v40 is an output / integrity release, not a scoring-model change.
# Keeping SCORING_VERSION at v39 preserves valid backtest and indicator caches.
# ---------------------------------------------------------------------------
config = ROOT / "config.py"
replace_once(
    config,
    'current model/pipeline provenance across v36 market-data normalization, v37\nproject-integrity/evidence UX, and v38 industry-adaptive Fundamental Gate 2.0.\n',
    'current model/pipeline provenance across v36 market-data normalization, v37\nproject-integrity/evidence UX, v38 Fundamental Gate 2.0, v39 decision integrity,\nand v40 candidate-view / explanation integrity.\n',
)
replace_once(
    config,
    'SCORING_VERSION: str = "2026-08-12-v39-decision-integrity2"\nPIPELINE_VERSION: str = "2026-08-12-v39-decision-integrity2-v38-fundamental"\nFUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"\nDECISION_INTEGRITY_VERSION: str = "2026-08-12-v39-gate-lifecycle-research"\n',
    'SCORING_VERSION: str = "2026-08-12-v39-decision-integrity2"\nPIPELINE_VERSION: str = "2026-08-12-v40-output-semantics-v39-decision-integrity-v38-fundamental"\nFUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"\nDECISION_INTEGRITY_VERSION: str = "2026-08-12-v40-explanation-integrity-v39-gate-lifecycle-research"\nOUTPUT_CONTRACT_VERSION: str = "2026-08-12-v40-candidate-views"\n',
)


# ---------------------------------------------------------------------------
# signal_lifecycle.py: the v35+ facade can promote a legacy B-tier decision to
# final A-tier after the bounded cross-asset correction.  Reconcile explanatory
# fields after that final decision so stale B-tier wording cannot survive.
# ---------------------------------------------------------------------------
lifecycle = ROOT / "signal_lifecycle.py"
text = lifecycle.read_text(encoding="utf-8")
marker = '\ndef _recompute_tiers_and_decisions(\n'
if text.count(marker) != 1:
    raise RuntimeError("signal_lifecycle.py: recompute marker not unique")
helper = r'''

def _strip_reason_tokens(series: pd.Series, tokens: tuple[str, ...]) -> pd.Series:
    cleaned = series.fillna("").astype(str)
    for token in tokens:
        cleaned = cleaned.str.replace(token, "", regex=False)
    cleaned = cleaned.str.replace("；；", "；", regex=False)
    return cleaned.str.strip("；， ")


def _sync_final_explanations(
    result: pd.DataFrame,
    strong_ready: pd.Series,
    cautious_ready: pd.Series,
    filter_override: pd.Series,
) -> None:
    """Synchronize reason/penalty text with the final post-normalization decision."""
    actionable = strong_ready | cautious_ready
    readiness_reason = _core._text_series(
        result, "TradeReadinessReason", "等待趋势、量能或风险条件改善"
    )
    ranking_reason = _core._text_series(result, "RankingReason", "")
    ranking_reason.loc[actionable] = readiness_reason.loc[actionable]

    backtest_eligible = _core._bool_series(result, "BacktestEligibleForRanking")
    confidence = _core._text_series(result, "BacktestConfidenceTier", "")
    backtest_status = _core._text_series(result, "BacktestStatus", "").str.upper()
    insufficient_evidence = actionable & ~backtest_eligible & (
        confidence.str.contains("样本不足", regex=False)
        | backtest_status.isin({"SAMPLES", "NO_SIGNAL_SAMPLES"})
    )
    ranking_reason.loc[insufficient_evidence] = (
        ranking_reason.loc[insufficient_evidence].str.rstrip("；")
        + "；回测样本不足，不参与校准"
    )

    strict_override = actionable & filter_override
    override_text = "量价资金确认突破，严格覆盖基础筛选缺口"
    missing_override_text = strict_override & ~ranking_reason.str.contains(
        override_text, regex=False
    )
    ranking_reason.loc[missing_override_text] = (
        ranking_reason.loc[missing_override_text].str.rstrip("；")
        + "；"
        + override_text
    )
    result["RankingReason"] = ranking_reason

    penalty = _core._text_series(result, "RankingPenaltyReason", "")
    if strong_ready.any():
        cleaned = _strip_reason_tokens(
            penalty.loc[strong_ready],
            (
                "B级仅列谨慎候选",
                "B级量价资金突破确认，谨慎候选",
            ),
        )
        penalty.loc[strong_ready] = cleaned
    result["RankingPenaltyReason"] = penalty
'''
text = text.replace(marker, helper + marker, 1)
old_decision_reason = '''    result["TradeReadinessReason"] = reason\n    result["DecisionReason"] = reason\n\n    advice = _core._text_series(result, "OperationAdvice", "")\n'''
new_decision_reason = '''    result["TradeReadinessReason"] = reason\n    result["DecisionReason"] = reason\n    _sync_final_explanations(result, strong_ready, cautious_ready, filter_override)\n\n    advice = _core._text_series(result, "OperationAdvice", "")\n'''
if text.count(old_decision_reason) != 1:
    raise RuntimeError("signal_lifecycle.py: decision reason block not unique")
text = text.replace(old_decision_reason, new_decision_reason, 1)
old_tail = '''_core.finalize_signal_ranking = finalize_signal_ranking\nsys.modules[__name__] = _core\n'''
new_tail = '''_core.finalize_signal_ranking = finalize_signal_ranking\n_core._sync_final_explanations = _sync_final_explanations\nsys.modules[__name__] = _core\n'''
if text.count(old_tail) != 1:
    raise RuntimeError("signal_lifecycle.py: module patch tail not unique")
text = text.replace(old_tail, new_tail, 1)
lifecycle.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# report.py: preserve risk bucket during diversified selection; expose explicit
# candidate-view ranks; make specialized exports rank by their own metric.
# ---------------------------------------------------------------------------
report = ROOT / "report.py"
text = report.read_text(encoding="utf-8")
old_signature = '''def _diversify_ranked_candidates(\n    frame: pd.DataFrame,\n    limit: int,\n    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,\n    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n) -> pd.DataFrame:\n    if frame.empty or limit <= 0:\n        return frame.head(0).copy()\n    working = _ensure_diversity_columns(frame)\n'''
new_signature = '''def _diversify_ranked_candidates(\n    frame: pd.DataFrame,\n    limit: int,\n    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,\n    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n    diversity_prepared: bool = False,\n) -> pd.DataFrame:\n    if frame.empty or limit <= 0:\n        return frame.head(0).copy()\n    # refresh_candidate_exports() prepares these columns once for the full wide\n    # frame.  Reusing them avoids repeated 200+ column copies for each view.\n    working = frame if diversity_prepared else _ensure_diversity_columns(frame)\n'''
if text.count(old_signature) != 1:
    raise RuntimeError("report.py: diversify signature not unique")
text = text.replace(old_signature, new_signature, 1)

old_rank_score = '''    rank_score = pd.to_numeric(\n        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),\n        errors="coerce",\n    ).fillna(0.0)\n    penalties: dict[int, float] = {}\n'''
new_rank_score = '''    rank_score = pd.to_numeric(\n        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),\n        errors="coerce",\n    ).fillna(0.0)\n    risk_filtered = working.get(\n        "RankingEligibility", pd.Series("观察", index=working.index)\n    ).fillna("观察").astype(str).eq("风险过滤")\n    penalties: dict[int, float] = {}\n'''
if text.count(old_rank_score) != 1:
    raise RuntimeError("report.py: rank score block not unique")
text = text.replace(old_rank_score, new_rank_score, 1)

old_value = '''            value = float(rank_score.loc[index]) * penalty\n            if value > best_value:\n'''
new_value = '''            # _rank_valid_candidates() deliberately puts risk-filtered rows last.\n            # Preserve that bucket priority after diversity reranking: a blocked\n            # row may fill the list only when no feasible non-risk row remains.\n            risk_bucket_penalty = 1_000_000_000.0 if bool(risk_filtered.loc[index]) else 0.0\n            value = float(rank_score.loc[index]) * penalty - risk_bucket_penalty\n            if value > best_value:\n'''
if text.count(old_value) != 1:
    raise RuntimeError("report.py: diversity value block not unique")
text = text.replace(old_value, new_value, 1)

insert_marker = '\ndef refresh_candidate_exports(\n'
if text.count(insert_marker) != 1:
    raise RuntimeError("report.py: refresh marker not unique")
view_helper = r'''

def _annotate_candidate_view(frame: pd.DataFrame, view: str) -> pd.DataFrame:
    """Attach a stable view identity and sequential rank to every candidate export."""
    working = frame.copy().reset_index(drop=True)
    rank = np.arange(1, len(working) + 1)
    working["CandidateView"] = view
    working["CandidateViewRank"] = rank
    # ResearchPoolRank remains the compatibility rank used by older GUI/data
    # consumers.  Specialized views historically received it only when they
    # happened to pass through the diversity selector; v40 makes it explicit.
    working["ResearchPoolRank"] = rank
    if "ResearchDiversityPenalty" not in working.columns:
        working["ResearchDiversityPenalty"] = 1.0
    return working
'''
text = text.replace(insert_marker, view_helper + insert_marker, 1)

start_token = '    csv_path = destination / f"Top{top_n_csv}.csv"\n'
end_token = '    return csv_path, parquet_path, ranked\n'
start = text.find(start_token)
end = text.find(end_token, start)
if start < 0 or end < 0:
    raise RuntimeError("report.py: candidate export block bounds not found")
end += len(end_token)
new_block = r'''    csv_path = destination / f"Top{top_n_csv}.csv"
    research_pool = _diversify_ranked_candidates(
        ranked, top_n_csv, diversity_prepared=True
    )
    research_pool = _annotate_candidate_view(research_pool, "MIXED_RESEARCH")
    _atomic_write_csv(research_pool, csv_path)
    logger.info(
        "Exported diversified Top %d (%d rows) to %s",
        top_n_csv,
        len(research_pool),
        csv_path,
    )

    # TopN.csv is the compatibility alias of the explicit mixed research list.
    mixed_path = destination / f"Top{top_n_csv}Mixed.csv"
    _atomic_write_csv(research_pool, mixed_path)

    asset_type = ranked.get(
        "AssetType", pd.Series("", index=ranked.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf_mask = ranked.get(
        "IsETF", pd.Series(False, index=ranked.index)
    ).map(_truthy) | asset_type.eq("etf")

    # Dedicated asset lists are pure within-asset rankings.  They intentionally
    # do not inherit mixed-list diversity caps or trade-readiness thresholds.
    stock_path = destination / f"Top{top_n_csv}Stocks.csv"
    stock_pool = ranked.loc[~is_etf_mask].head(top_n_csv).copy()
    stock_pool = _annotate_candidate_view(stock_pool, "STOCK_RESEARCH")
    _atomic_write_csv(stock_pool, stock_path)

    etf_path = destination / f"Top{top_n_csv}ETF.csv"
    etf_pool = ranked.loc[is_etf_mask].head(top_n_csv).copy()
    etf_pool = _annotate_candidate_view(etf_pool, "ETF_RESEARCH")
    _atomic_write_csv(etf_pool, etf_path)
    logger.info(
        "Exported split research lists: mixed=%d, stocks=%d, ETF=%d.",
        len(research_pool),
        len(stock_pool),
        len(etf_pool),
    )

    trade_ready_path = destination / f"Top{top_n_csv}TradeReady.csv"
    trade_ready = ranked.loc[
        ranked.get(
            "RankingEligibility", pd.Series("观察", index=ranked.index)
        ).eq("推荐")
    ]
    trade_ready = _diversify_ranked_candidates(
        trade_ready, top_n_csv, diversity_prepared=True
    )
    trade_ready = _annotate_candidate_view(trade_ready, "TRADE_READY")
    _atomic_write_csv(trade_ready, trade_ready_path)
    logger.info(
        "Exported %d trade-ready candidates to %s",
        len(trade_ready),
        trade_ready_path,
    )

    parquet_path = destination / f"Top{top_n_parquet}.parquet"
    _atomic_write_parquet(ranked.head(top_n_parquet), parquet_path)
    logger.info("Exported Top %d to %s", top_n_parquet, parquet_path)

    # Specialized research surfaces rank by their own purpose.  v39 still sent
    # these through the mixed RankingScore diversity selector, which could make
    # Opportunity and EntryCandidates byte-for-byte clones of Top50Mixed.
    non_risk = ranked.get(
        "RankingEligibility", pd.Series("观察", index=ranked.index)
    ).fillna("观察").astype(str).ne("风险过滤")

    opportunity_path = destination / f"Top{top_n_csv}Opportunity.csv"
    opportunity = _sort_export_rows(
        ranked.loc[non_risk],
        ("OpportunityScore", "RankingScore", "FinalScore"),
    ).head(top_n_csv)
    opportunity = _annotate_candidate_view(opportunity, "OPPORTUNITY")
    _atomic_write_csv(opportunity, opportunity_path)

    trigger_path = destination / f"Top{top_n_csv}BreakoutCandidates.csv"
    entry_signal = ranked.get(
        "EntrySignal", pd.Series("AVOID", index=ranked.index)
    ).fillna("AVOID").astype(str).str.upper()
    confirmed_breakout = (
        non_risk
        & entry_signal.eq("BREAKOUT_CONFIRM")
        & ranked.get("PriceBreakout", pd.Series(False, index=ranked.index)).map(_truthy)
        & ranked.get(
            "BreakoutVolumeConfirmed", pd.Series(False, index=ranked.index)
        ).map(_truthy)
        & ranked.get(
            "BreakoutFlowConfirmed", pd.Series(False, index=ranked.index)
        ).map(_truthy)
    )
    trigger = _sort_export_rows(
        ranked.loc[confirmed_breakout], ("BreakoutScore", "RankingScore")
    ).head(top_n_csv)
    trigger = _annotate_candidate_view(trigger, "CONFIRMED_BREAKOUT")
    _atomic_write_csv(trigger, trigger_path)

    entry_path = destination / f"Top{top_n_csv}EntryCandidates.csv"
    entry = ranked.loc[
        non_risk
        & entry_signal.isin(["BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"])
    ]
    entry = _sort_export_rows(
        entry, ("EntrySignalPriority", "EntryScore", "RankingScore")
    ).head(top_n_csv)
    entry = _annotate_candidate_view(entry, "ENTRY_SETUP")
    _atomic_write_csv(entry, entry_path)

    trap_path = destination / f"Top{top_n_csv}ValueTrapRisk.csv"
    trap = ranked.loc[
        pd.to_numeric(
            ranked.get("ValueTrapRisk", pd.Series(0.0, index=ranked.index)),
            errors="coerce",
        ).fillna(0) >= 60
    ]
    trap = _sort_export_rows(trap, ("ValueTrapRisk", "RankingScore")).head(top_n_csv)
    trap = _annotate_candidate_view(trap, "VALUE_TRAP_RISK")
    _atomic_write_csv(trap, trap_path)

    sustained_path = destination / f"Top{top_n_csv}SustainedSignals.csv"
    signal_days = pd.to_numeric(
        ranked.get("SignalDays", pd.Series(0.0, index=ranked.index)),
        errors="coerce",
    ).fillna(0)
    sustained = ranked.loc[non_risk & signal_days.gt(0)]
    sustained = _sort_export_rows(
        sustained, ("SignalDays", "OpportunityScore", "RankingScore")
    ).head(top_n_csv)
    sustained = _annotate_candidate_view(sustained, "SUSTAINED_SIGNAL")
    _atomic_write_csv(sustained, sustained_path)
    return csv_path, parquet_path, ranked
'''
text = text[:start] + new_block + text[end:]

old_integrity_tail = '''    if violations:\n        raise ValueError("Decision integrity violation: " + " | ".join(violations))\n'''
new_integrity_tail = '''    if "RankingEligibility" in frame.columns:\n        recommended = frame["RankingEligibility"].fillna("").astype(str).eq("推荐")\n        stale = pd.Series(False, index=frame.index)\n        if "RankingReason" in frame.columns:\n            ranking_reason = frame["RankingReason"].fillna("").astype(str)\n            stale |= recommended & (\n                ranking_reason.str.contains("谨慎候选", regex=False)\n                | ranking_reason.str.contains("转为观察", regex=False)\n                | ranking_reason.str.contains("禁止进入推荐", regex=False)\n            )\n        if "RankingPenaltyReason" in frame.columns:\n            penalty_reason = frame["RankingPenaltyReason"].fillna("").astype(str)\n            stale |= recommended & (\n                penalty_reason.str.contains("B级仅列谨慎候选", regex=False)\n                | penalty_reason.str.contains("禁止进入推荐", regex=False)\n            )\n        if stale.any():\n            violations.append(\n                "recommended rows carry stale cautious explanation: "\n                + ",".join(ticker.loc[stale].head(5))\n            )\n\n    if violations:\n        raise ValueError("Decision integrity violation: " + " | ".join(violations))\n'''
if text.count(old_integrity_tail) != 1:
    raise RuntimeError("report.py: integrity tail not unique")
text = text.replace(old_integrity_tail, new_integrity_tail, 1)
report.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# README: document the now-distinct candidate surfaces.
# ---------------------------------------------------------------------------
readme = ROOT / "README.md"
replace_once(
    readme,
    '''- `AllResults.csv` / `AllResults.parquet`\n- `Top50.csv`\n- `Top50TradeReady.csv`\n- `Top50EntryCandidates.csv`\n- `Top50BreakoutCandidates.csv`\n- 信号生命周期与回测文件\n''',
    '''- `AllResults.csv` / `AllResults.parquet`：完整研究结果\n- `Top50.csv` / `Top50Mixed.csv`：统一 RankingScore + 多样性约束的综合研究榜\n- `Top50Stocks.csv` / `Top50ETF.csv`：股票、ETF 各自独立纯排名\n- `Top50TradeReady.csv`：仅最终 `推荐`\n- `Top50Opportunity.csv`：按 OpportunityScore 排序的非风险研究机会\n- `Top50EntryCandidates.csv`：按买点优先级 → EntryScore → RankingScore 排序\n- `Top50BreakoutCandidates.csv`：仅价格突破且量能、资金流同时确认的严格突破\n- `Top50SustainedSignals.csv`：仍有效且非风险过滤的持续信号\n- `Top50ValueTrapRisk.csv`：价值陷阱风险研究池\n- 信号生命周期与回测文件\n\n候选 CSV 均带 `CandidateView` / `CandidateViewRank`，避免把“研究榜排名”、\n“买点排序”和“推荐资格”混成同一个概念。\n''',
)


# ---------------------------------------------------------------------------
# Dedicated v40 regression contract.
# ---------------------------------------------------------------------------
test_path = ROOT / "test_v40_output_semantics.py"
test_path.write_text(r'''from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import config
import report
import signal_lifecycle_core as lifecycle_core


class V40OutputSemanticsTests(unittest.TestCase):
    @staticmethod
    def _ranked_fixture() -> pd.DataFrame:
        rows = [
            {
                "Ticker": "000001.SZ", "Name": "RankLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业A", "ModelClassification": "行业A", "RankingScore": 99.0,
                "OpportunityScore": 20.0, "EntrySignal": "WAIT_PULLBACK", "EntrySignalPriority": 3.0,
                "EntryScore": 60.0, "BreakoutScore": 80.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "观察", "SignalDays": 2, "Error": "",
            },
            {
                "Ticker": "000002.SZ", "Name": "OpportunityLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业B", "ModelClassification": "行业B", "RankingScore": 55.0,
                "OpportunityScore": 95.0, "EntrySignal": "BUY_NOW", "EntrySignalPriority": 5.0,
                "EntryScore": 95.0, "BreakoutScore": 20.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": False, "BreakoutFlowConfirmed": False,
                "RankingEligibility": "观察", "SignalDays": 1, "Error": "",
            },
            {
                "Ticker": "000003.SZ", "Name": "BreakoutLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业C", "ModelClassification": "行业C", "RankingScore": 65.0,
                "OpportunityScore": 50.0, "EntrySignal": "BREAKOUT_CONFIRM", "EntrySignalPriority": 4.0,
                "EntryScore": 75.0, "BreakoutScore": 100.0, "PriceBreakout": True,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "谨慎候选", "SignalDays": 3, "Error": "",
            },
            {
                "Ticker": "000004.SZ", "Name": "FakeBreakout", "AssetType": "stock", "IsETF": False,
                "Industry": "行业D", "ModelClassification": "行业D", "RankingScore": 70.0,
                "OpportunityScore": 40.0, "EntrySignal": "WAIT_PULLBACK", "EntrySignalPriority": 3.0,
                "EntryScore": 80.0, "BreakoutScore": 99.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "观察", "SignalDays": 4, "Error": "",
            },
            {
                "Ticker": "000005.SZ", "Name": "Sustained", "AssetType": "stock", "IsETF": False,
                "Industry": "行业E", "ModelClassification": "行业E", "RankingScore": 50.0,
                "OpportunityScore": 80.0, "EntrySignal": "HOLD_WAIT", "EntrySignalPriority": 2.0,
                "EntryScore": 30.0, "BreakoutScore": 30.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": False, "BreakoutFlowConfirmed": False,
                "RankingEligibility": "观察", "SignalDays": 12, "Error": "",
            },
            {
                "Ticker": "000006.SZ", "Name": "RiskHighScore", "AssetType": "stock", "IsETF": False,
                "Industry": "行业F", "ModelClassification": "行业F", "RankingScore": 120.0,
                "OpportunityScore": 120.0, "EntrySignal": "BUY_NOW", "EntrySignalPriority": 5.0,
                "EntryScore": 100.0, "BreakoutScore": 100.0, "PriceBreakout": True,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "风险过滤", "SignalDays": 20, "ValueTrapRisk": 95.0, "Error": "",
            },
        ]
        return pd.DataFrame(rows)

    def test_specialized_candidate_views_rank_by_purpose_and_keep_risk_out(self):
        frame = self._ranked_fixture()
        with TemporaryDirectory() as temp_dir, patch.object(
            report, "_rank_valid_candidates", return_value=frame.copy()
        ):
            output_dir = Path(temp_dir)
            report.refresh_candidate_exports(
                frame, top_n_csv=3, top_n_parquet=3, output_dir=output_dir
            )
            mixed = pd.read_csv(output_dir / "Top3Mixed.csv", encoding="utf-8-sig")
            opportunity = pd.read_csv(output_dir / "Top3Opportunity.csv", encoding="utf-8-sig")
            entry = pd.read_csv(output_dir / "Top3EntryCandidates.csv", encoding="utf-8-sig")
            breakout = pd.read_csv(output_dir / "Top3BreakoutCandidates.csv", encoding="utf-8-sig")
            sustained = pd.read_csv(output_dir / "Top3SustainedSignals.csv", encoding="utf-8-sig")

        self.assertEqual(mixed.loc[0, "Ticker"], "000001.SZ")
        self.assertNotIn("000006.SZ", mixed["Ticker"].tolist())
        self.assertEqual(opportunity.loc[0, "Ticker"], "000002.SZ")
        self.assertNotIn("000006.SZ", opportunity["Ticker"].tolist())
        self.assertEqual(entry.loc[0, "Ticker"], "000002.SZ")
        self.assertEqual(breakout["Ticker"].tolist(), ["000003.SZ"])
        self.assertEqual(sustained.loc[0, "Ticker"], "000005.SZ")
        self.assertNotIn("000006.SZ", sustained["Ticker"].tolist())
        self.assertEqual(opportunity["CandidateView"].unique().tolist(), ["OPPORTUNITY"])
        self.assertEqual(opportunity["CandidateViewRank"].tolist(), [1, 2, 3])
        self.assertEqual(opportunity["ResearchPoolRank"].tolist(), [1, 2, 3])

    def test_final_explanation_sync_removes_stale_b_tier_wording(self):
        frame = pd.DataFrame([
            {
                "RankingReason": "B级量价资金突破确认，谨慎候选；回测样本不足，不参与校准",
                "RankingPenaltyReason": "B级仅列谨慎候选",
                "TradeReadinessReason": "买点、质量、数据与综合评分均满足执行条件",
                "BacktestEligibleForRanking": False,
                "BacktestConfidenceTier": "样本不足",
                "BacktestStatus": "SAMPLES",
            }
        ])
        strong = pd.Series([True])
        cautious = pd.Series([False])
        override = pd.Series([False])
        lifecycle_core._sync_final_explanations(frame, strong, cautious, override)
        self.assertNotIn("谨慎候选", str(frame.loc[0, "RankingReason"]))
        self.assertIn("买点、质量、数据与综合评分均满足执行条件", str(frame.loc[0, "RankingReason"]))
        self.assertIn("回测样本不足", str(frame.loc[0, "RankingReason"]))
        self.assertNotIn("B级仅列谨慎候选", str(frame.loc[0, "RankingPenaltyReason"]))

    def test_integrity_gate_rejects_recommended_row_with_stale_cautious_reason(self):
        frame = pd.DataFrame([
            {
                "Ticker": "000001.SZ", "ResearchEligible": True, "HardGatePassed": True,
                "RankingEligibility": "推荐", "SignalStatus": "WATCH", "SignalDays": 2,
                "QualityReason": "行业自适应硬门槛通过", "QualityGate": True,
                "RankingReason": "B级量价资金突破确认，谨慎候选",
                "RankingPenaltyReason": "B级仅列谨慎候选",
            }
        ])
        with self.assertRaisesRegex(ValueError, "stale cautious explanation"):
            report.validate_decision_integrity(frame)

    def test_v40_is_pipeline_only_and_does_not_invalidate_v39_scoring_cache(self):
        self.assertIn("v39", config.SCORING_VERSION)
        self.assertNotIn("v40", config.SCORING_VERSION)
        self.assertIn("v40", config.PIPELINE_VERSION)
        self.assertIn("v40", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v40", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
