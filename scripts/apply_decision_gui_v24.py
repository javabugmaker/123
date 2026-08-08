from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-09-v23-research-integrity"',
    'SCORING_VERSION: str = "2026-08-09-v24-decision-integrity"',
)

lifecycle_path = Path("signal_lifecycle.py")
lifecycle = lifecycle_path.read_text(encoding="utf-8")
marker = '    rank_reason = pd.Series("等待趋势与量能确认", index=result.index)\n'
if marker not in lifecycle:
    raise SystemExit("signal_lifecycle rank_reason marker not found")
decision_block = '''    # Final execution eligibility must agree with the research tier.  The
    # technical gate above answers "is the signal executable?"; the tier gate
    # answers "is the evidence strong enough to call it a recommendation?".
    initially_ready = result["DecisionState"].eq("READY")
    strong_ready = initially_ready & tier.eq("A级机构启动")
    cautious_ready = (
        initially_ready
        & tier.eq("B级观察")
        & signal.eq("BREAKOUT_CONFIRM")
        & breakout_confirmation_ok
    )
    tier_demoted = initially_ready & ~(strong_ready | cautious_ready)

    result.loc[cautious_ready, "DecisionState"] = "CAUTIOUS"
    result.loc[tier_demoted, "DecisionState"] = "OBSERVE"
    result["RankingEligibility"] = result["DecisionState"].map(
        {
            "READY": "推荐",
            "CAUTIOUS": "谨慎候选",
            "OBSERVE": "观察",
            "BLOCKED": "风险过滤",
        }
    ).fillna("观察")
    result["TradeReadiness"] = result["RankingEligibility"]
    result.loc[cautious_ready, "TradeReadinessReason"] = (
        "B级观察但量价资金突破确认，列为谨慎候选"
    )
    result.loc[tier_demoted, "TradeReadinessReason"] = (
        "研究等级未达到A级执行门槛，转为观察"
    )
    result["DecisionReason"] = result["TradeReadinessReason"]
    result.loc[cautious_ready, "OperationAdvice"] = (
        "B级突破确认，仅列谨慎候选；控制仓位并等待进一步确认。"
    )
    result.loc[tier_demoted, "OperationAdvice"] = (
        "技术买点存在，但研究等级不足以列为强推荐，继续观察。"
    )

    # RankingScore was calculated with READY=1.0 earlier. Reconcile the
    # score with the final tier-aware state without recomputing independent
    # technical components.
    ranking_score_now = _number(result["RankingScore"], 0.0)
    result.loc[cautious_ready, "RankingScore"] = (
        ranking_score_now.loc[cautious_ready] * 0.94
    ).round(4)
    result.loc[tier_demoted, "RankingScore"] = (
        ranking_score_now.loc[tier_demoted] * 0.88
    ).round(4)
    tier_penalty_reason = _text_series(result, "RankingPenaltyReason", "")
    tier_penalty_reason = _append_reason(
        tier_penalty_reason, cautious_ready, "B级仅列谨慎候选"
    )
    tier_penalty_reason = _append_reason(
        tier_penalty_reason, tier_demoted, "研究等级未达A级执行门槛"
    )
    result["RankingPenaltyReason"] = tier_penalty_reason

'''
lifecycle = lifecycle.replace(marker, decision_block + marker, 1)
old_reason = '    rank_reason.loc[signal.eq("BREAKOUT_CONFIRM")] = "量价与资金确认突破"\n'
if old_reason not in lifecycle:
    raise SystemExit("signal_lifecycle breakout rank reason marker not found")
lifecycle = lifecycle.replace(
    old_reason,
    old_reason
    + '    rank_reason.loc[cautious_ready] = "B级量价资金突破确认，谨慎候选"\n'
    + '    rank_reason.loc[tier_demoted & ~hard_filter] = (\n'
    + '        "研究等级未达到A级执行门槛，转为观察"\n'
    + '    )\n',
    1,
)
lifecycle_path.write_text(lifecycle, encoding="utf-8")

report_path = Path("report.py")
report = report_path.read_text(encoding="utf-8")
export_marker = '''    logger.info(
        "Exported diversified Top %d (%d rows) to %s",
        top_n_csv,
        len(research_pool),
        csv_path,
    )

'''
if export_marker not in report:
    raise SystemExit("report diversified export marker not found")
split_exports = '''    # Keep TopN.csv as the compatibility alias while publishing explicit
    # mixed / stock / ETF research lists so one asset class cannot hide the
    # other in the GUI.
    mixed_path = destination / f"Top{top_n_csv}Mixed.csv"
    _atomic_write_csv(research_pool, mixed_path)

    asset_type = ranked.get(
        "AssetType", pd.Series("", index=ranked.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf_mask = ranked.get(
        "IsETF", pd.Series(False, index=ranked.index)
    ).map(_truthy) | asset_type.eq("etf")

    stock_path = destination / f"Top{top_n_csv}Stocks.csv"
    stock_pool = _diversify_ranked_candidates(
        ranked.loc[~is_etf_mask], top_n_csv
    )
    _atomic_write_csv(stock_pool, stock_path)

    etf_path = destination / f"Top{top_n_csv}ETF.csv"
    etf_pool = _diversify_ranked_candidates(
        ranked.loc[is_etf_mask], top_n_csv
    )
    _atomic_write_csv(etf_pool, etf_path)
    logger.info(
        "Exported split research lists: mixed=%d, stocks=%d, ETF=%d.",
        len(research_pool),
        len(stock_pool),
        len(etf_pool),
    )

'''
report = report.replace(export_marker, export_marker + split_exports, 1)
empty_tuple = '''            f"Top{top_n_csv}Opportunity.csv",
            f"Top{top_n_csv}BreakoutCandidates.csv",
'''
if empty_tuple not in report:
    raise SystemExit("report empty export tuple marker not found")
report = report.replace(
    empty_tuple,
    '''            f"Top{top_n_csv}Mixed.csv",
            f"Top{top_n_csv}Stocks.csv",
            f"Top{top_n_csv}ETF.csv",
            f"Top{top_n_csv}Opportunity.csv",
            f"Top{top_n_csv}BreakoutCandidates.csv",
''',
    1,
)
report_path.write_text(report, encoding="utf-8")

gui_path = Path("gui.py")
gui = gui_path.read_text(encoding="utf-8")
display_pattern = re.compile(
    r'_core\.DISPLAY_COLUMNS = \(\n.*?\n\)\n\n_core\.COLUMN_NAMES', re.S
)
new_display = '''_core.DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "AssetType",
    "Sector",
    "Industry",
    "Close",
    "EntrySignal",
    "EntryZone",
    "BreakoutBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalTier",
    "InstitutionalScore",
    "FinalScore",
    "QualityGate",
    "BacktestConfidenceTier",
    "PassedFilters",
    "TradeReadinessReason",
    "DataAsOf",
    "RankingReason",
)

_core.COLUMN_NAMES'''
gui, count = display_pattern.subn(new_display, gui, count=1)
if count != 1:
    raise SystemExit("gui display columns block not replaced")

ui_marker = '''def _build_ui_v16(self) -> None:
    _original_build_ui(self)

'''
if ui_marker not in gui:
    raise SystemExit("gui build marker not found")
toolbar_block = '''def _build_ui_v16(self) -> None:
    _original_build_ui(self)

    # Keep the toolbar centered on actual decisions. Remove legacy side
    # reports that duplicate information already available in row details.
    actions = self.progress.master
    for child in list(actions.winfo_children()):
        try:
            label = str(child.cget("text"))
        except Exception:
            continue
        if label in {"风险榜", "市场概览", "连续信号"}:
            child.destroy()
        elif label == "生成前50名":
            child.configure(
                text="综合榜", command=lambda: self.load_csv("Top50Mixed.csv")
            )
        elif label == "交易就绪":
            child.configure(text="强推荐")

    _core.ttk.Button(
        actions,
        text="股票榜",
        style="Quiet.TButton",
        command=lambda: self.load_csv("Top50Stocks.csv"),
    ).pack(side=_core.tk.LEFT, padx=(0, 6))
    _core.ttk.Button(
        actions,
        text="ETF榜",
        style="Quiet.TButton",
        command=lambda: self.load_csv("Top50ETF.csv"),
    ).pack(side=_core.tk.LEFT, padx=(0, 6))

'''
gui = gui.replace(ui_marker, toolbar_block, 1)
gui = gui.replace(
    'values=("全部资格", "推荐", "观察", "风险过滤"),',
    'values=("全部资格", "推荐", "谨慎候选", "观察", "风险过滤"),',
    1,
)
overview_block = '''    ttk.Label(
        filters,
        textvariable=self.market_overview,
        style="Status.TLabel",
        padding=(0, 8, 0, 0),
    ).grid(row=2, column=0, columnspan=14, sticky=tk.W)

'''
if overview_block not in gui:
    raise SystemExit("gui persistent overview block not found")
gui = gui.replace(overview_block, "", 1)
gui_path.write_text(gui, encoding="utf-8")

tests_path = Path("test_regressions.py")
tests = tests_path.read_text(encoding="utf-8")
for old, new in (
    ('"Score": [60.0, 24.9, 30.0],', '"Score": [60.0, 24.9, 70.0],'),
    ('"FinalScore": [60.0, 24.9, 30.0],', '"FinalScore": [60.0, 24.9, 70.0],'),
    ('"InstitutionalScore": [60.0, 24.9, 30.0],', '"InstitutionalScore": [60.0, 24.9, 70.0],'),
):
    if old not in tests:
        raise SystemExit(f"regression marker missing: {old}")
    tests = tests.replace(old, new, 1)
tests_path.write_text(tests, encoding="utf-8")

Path("test_decision_gui_v24.py").write_text(r'''from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

import gui
import report
import signal_lifecycle
from config import SCORING_VERSION


class DecisionGuiV24Tests(TestCase):
    @staticmethod
    def _decision_frame() -> pd.DataFrame:
        scores = [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 35.0, 32.0, 28.0, 20.0]
        return pd.DataFrame(
            {
                "Ticker": [f"TEST{i:02d}.SZ" for i in range(len(scores))],
                "Score": scores,
                "FinalScore": scores,
                "InstitutionalScore": scores,
                "EntrySignal": ["BREAKOUT_CONFIRM"] * len(scores),
                "BreakoutVolumeConfirmed": [True] * len(scores),
                "BreakoutFlowConfirmed": [True] * len(scores),
                "PassedFilters": [True] * len(scores),
                "UniverseEligible": [True] * len(scores),
                "QualityGate": [True] * len(scores),
                "QualityDataCompleteness": [1.0] * len(scores),
                "ScoreCoverage": [1.0] * len(scores),
                "DataTradingAgeDays": [0] * len(scores),
                "SignalRecencyDays": [1] * len(scores),
                "LifecycleStage": ["趋势确认"] * len(scores),
                "AssetType": ["stock"] * len(scores),
                "IsETF": [False] * len(scores),
            }
        )

    def test_tier_and_execution_eligibility_are_consistent(self):
        result = signal_lifecycle.finalize_signal_ranking(self._decision_frame())
        by_score = result.set_index("InstitutionalScore")
        self.assertEqual(by_score.loc[90.0, "InstitutionalTier"], "A级机构启动")
        self.assertEqual(by_score.loc[90.0, "DecisionState"], "READY")
        self.assertEqual(by_score.loc[90.0, "RankingEligibility"], "推荐")
        self.assertEqual(by_score.loc[70.0, "InstitutionalTier"], "B级观察")
        self.assertEqual(by_score.loc[70.0, "DecisionState"], "CAUTIOUS")
        self.assertEqual(by_score.loc[70.0, "RankingEligibility"], "谨慎候选")
        self.assertEqual(by_score.loc[50.0, "InstitutionalTier"], "C级价值观察")
        self.assertEqual(by_score.loc[50.0, "DecisionState"], "OBSERVE")
        self.assertEqual(by_score.loc[50.0, "RankingEligibility"], "观察")

    def test_b_tier_buy_now_is_observation_not_cautious(self):
        frame = self._decision_frame()
        frame.loc[2, "EntrySignal"] = "BUY_NOW"
        result = signal_lifecycle.finalize_signal_ranking(frame).set_index("Ticker")
        row = result.loc["TEST02.SZ"]
        self.assertEqual(row["InstitutionalTier"], "B级观察")
        self.assertEqual(row["DecisionState"], "OBSERVE")
        self.assertEqual(row["RankingEligibility"], "观察")

    def test_split_exports_publish_mixed_stocks_and_etfs(self):
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ", "510300.SH", "159915.SZ"],
                "Name": ["股票一", "股票二", "沪深300ETF", "创业板ETF"],
                "AssetType": ["stock", "stock", "etf", "etf"],
                "IsETF": [False, False, True, True],
                "RankingScore": [90.0, 80.0, 85.0, 75.0],
                "InstitutionalScore": [90.0, 80.0, 85.0, 75.0],
                "FinalScore": [90.0, 80.0, 85.0, 75.0],
                "Score": [90.0, 80.0, 85.0, 75.0],
                "RankingEligibility": ["推荐", "观察", "推荐", "观察"],
                "EntrySignal": ["BUY_NOW", "WAIT_PULLBACK", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"],
                "Industry": ["银行", "软件", "宽基", "宽基"],
                "Sector": ["金融", "科技", "ETF", "ETF"],
            }
        )
        with TemporaryDirectory() as temp_dir, patch.object(report, "_atomic_write_parquet"):
            destination = Path(temp_dir)
            report.refresh_candidate_exports(frame, top_n_csv=3, top_n_parquet=3, output_dir=destination)
            mixed = pd.read_csv(destination / "Top3Mixed.csv", encoding="utf-8-sig")
            stocks = pd.read_csv(destination / "Top3Stocks.csv", encoding="utf-8-sig")
            etfs = pd.read_csv(destination / "Top3ETF.csv", encoding="utf-8-sig")
            legacy = pd.read_csv(destination / "Top3.csv", encoding="utf-8-sig")
        self.assertEqual(set(stocks["AssetType"].str.lower()), {"stock"})
        self.assertEqual(set(etfs["AssetType"].str.lower()), {"etf"})
        self.assertEqual(legacy["Ticker"].tolist(), mixed["Ticker"].tolist())

    def test_gui_main_table_is_decision_focused(self):
        self.assertIn("AssetType", gui.DISPLAY_COLUMNS)
        self.assertNotIn("ValueTrapRisk", gui.DISPLAY_COLUMNS)
        self.assertNotIn("ChaseRiskScore", gui.DISPLAY_COLUMNS)
        self.assertNotIn("BacktestSamples", gui.DISPLAY_COLUMNS)
        self.assertNotIn("QualityDataCompleteness", gui.DISPLAY_COLUMNS)

    def test_model_version_is_v24(self):
        self.assertEqual(SCORING_VERSION, "2026-08-09-v24-decision-integrity")


if __name__ == "__main__":
    import unittest
    unittest.main()
''', encoding="utf-8")

for path in (
    Path(".github/workflows/apply_decision_gui_v24.yml"),
    Path("scripts/apply_decision_gui_v24.py"),
):
    if path.exists():
        path.unlink()
