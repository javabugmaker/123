from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import report


def _reference_diversity(
    frame: pd.DataFrame,
    limit: int,
    max_per_theme: int,
    max_per_stock_industry: int,
) -> pd.DataFrame:
    """Small row-wise oracle for the optimized selector."""
    working = frame.reset_index(drop=True)
    theme_counts: dict[str, int] = {}
    tracking_counts: dict[str, int] = {}
    industry_counts: dict[str, int] = {}
    cluster_counts: dict[str, int] = {}
    remaining = list(range(len(working)))
    selected: list[int] = []
    penalties: list[float] = []

    while remaining and len(selected) < limit:
        best_position: int | None = None
        best_value = -np.inf
        best_penalty = 1.0
        for position in remaining:
            row = working.iloc[position]
            is_etf = report._truthy(row.get("IsETF", False)) or str(
                row.get("AssetType", "")
            ).strip().lower() == "etf"
            theme = report._clean_group_key(row.get("ETFTheme", "")) if is_etf else ""
            tracking = (
                report._clean_group_key(row.get("ETFTrackingKey", ""))
                if is_etf
                else ""
            )
            classification = (
                report._clean_group_key(row.get("ModelClassification", ""))
                or report._clean_group_key(row.get("Industry", ""))
                or report._clean_group_key(row.get("Sector", ""))
            )
            cluster = report._clean_group_key(row.get("ThemeCluster", ""))
            if (
                is_etf
                and tracking
                and tracking_counts.get(tracking, 0)
                >= max(1, int(report.ETF_TRACKING_MAX_PER_TOP_LIST))
            ):
                continue
            if (
                is_etf
                and theme
                and theme_counts.get(theme, 0) >= max(1, int(max_per_theme))
            ):
                continue
            if (
                not is_etf
                and classification
                and industry_counts.get(classification, 0)
                >= max(1, int(max_per_stock_industry))
            ):
                continue
            penalty = (
                max(
                    0.70,
                    1.0
                    - float(report.THEME_CLUSTER_SOFT_PENALTY)
                    * cluster_counts.get(cluster, 0),
                )
                if cluster
                else 1.0
            )
            score = float(pd.to_numeric(row.get("RankingScore", 0.0), errors="coerce"))
            if not np.isfinite(score):
                score = 0.0
            risk_penalty = (
                1_000_000_000.0
                if str(row.get("RankingEligibility", "观察")) == "风险过滤"
                else 0.0
            )
            value = score * penalty - risk_penalty
            if value > best_value:
                best_position = position
                best_value = value
                best_penalty = penalty
        if best_position is None:
            break

        row = working.iloc[best_position]
        is_etf = report._truthy(row.get("IsETF", False)) or str(
            row.get("AssetType", "")
        ).strip().lower() == "etf"
        theme = report._clean_group_key(row.get("ETFTheme", "")) if is_etf else ""
        tracking = (
            report._clean_group_key(row.get("ETFTrackingKey", ""))
            if is_etf
            else ""
        )
        classification = (
            report._clean_group_key(row.get("ModelClassification", ""))
            or report._clean_group_key(row.get("Industry", ""))
            or report._clean_group_key(row.get("Sector", ""))
        )
        cluster = report._clean_group_key(row.get("ThemeCluster", ""))
        if is_etf and theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if is_etf and tracking:
            tracking_counts[tracking] = tracking_counts.get(tracking, 0) + 1
        if not is_etf and classification:
            industry_counts[classification] = industry_counts.get(classification, 0) + 1
        if cluster:
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        selected.append(best_position)
        penalties.append(round(best_penalty, 4))
        remaining.remove(best_position)

    result = working.iloc[selected].copy().reset_index(drop=True)
    result["ResearchDiversityPenalty"] = penalties
    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)
    return result


class V42DiversityPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.DataFrame(
            {
                "Ticker": [f"T{index:02d}" for index in range(14)],
                "RankingScore": [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 200],
                "RankingEligibility": ["观察"] * 13 + ["风险过滤"],
                "IsETF": [True, True, True, True, False, False, False, False, False, False, True, True, False, False],
                "AssetType": ["etf"] * 4 + ["stock"] * 6 + ["etf", "etf", "stock", "stock"],
                "ETFTheme": ["科技", "科技", "科技", "红利", np.nan, "", "", "", "", "", "医药", "医药", "", ""],
                "ETFTrackingKey": ["A", "A", "B", "C", np.nan, "", "", "", "", "", "D", "E", "", ""],
                "ModelClassification": ["ETF"] * 4 + ["银行", "银行", "银行", "汽车", "汽车", "消费"] + ["ETF", "ETF", "", "银行"],
                "Industry": [""] * 12 + ["机械", "银行"],
                "Sector": [""] * 14,
                "ThemeCluster": ["成长", "成长", "成长", "红利", "金融", "金融", "金融", "制造", "制造", "消费", "医药", "医药", "制造", "金融"],
            },
            index=[7] * 14,
        )

    def test_vectorized_selector_matches_rowwise_policy(self) -> None:
        with patch.object(report, "ETF_TRACKING_MAX_PER_TOP_LIST", 1):
            expected = _reference_diversity(self.frame, 10, 2, 2)
            actual = report._diversify_ranked_candidates(
                self.frame,
                10,
                max_per_theme=2,
                max_per_stock_industry=2,
                diversity_prepared=True,
            )
        self.assertEqual(list(actual["Ticker"]), list(expected["Ticker"]))
        self.assertEqual(
            list(actual["ResearchDiversityPenalty"]),
            list(expected["ResearchDiversityPenalty"]),
        )
        self.assertEqual(list(actual["ResearchPoolRank"]), list(range(1, 11)))

    def test_selector_normalizes_group_keys_only_once_per_row(self) -> None:
        original = report._clean_group_key
        calls = {"count": 0}

        def counted(value: object) -> str:
            calls["count"] += 1
            return original(value)

        frame = pd.concat([self.frame.reset_index(drop=True)] * 20, ignore_index=True)
        frame["Ticker"] = [f"X{index:04d}" for index in range(len(frame))]
        with patch.object(report, "_clean_group_key", side_effect=counted):
            report._diversify_ranked_candidates(
                frame, 50, diversity_prepared=True
            )
        self.assertLessEqual(calls["count"], len(frame) * 6)

    def test_selector_does_not_mutate_input(self) -> None:
        before = self.frame.copy(deep=True)
        report._diversify_ranked_candidates(
            self.frame, 8, diversity_prepared=True
        )
        pd.testing.assert_frame_equal(self.frame, before)


if __name__ == "__main__":
    unittest.main()
