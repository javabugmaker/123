"""Stable exact-tie ordering for report candidate generation."""
from __future__ import annotations

from typing import Any, Final

import numpy as np

REPORT_DETERMINISM_VERSION: Final = (
    "2026-08-25-v107-ticker-stable-candidate-order-v1"
)


def install(core: Any) -> None:
    """Keep report priorities unchanged and use ticker only for exact ties."""
    if getattr(core, "_REPORT_DETERMINISM_V107_INSTALLED", False):
        return

    def rankable_results(results: list[Any]) -> list[Any]:
        valid = [result for result in results if not getattr(result, "error", "")]

        def rank_score(result: Any) -> float:
            score = getattr(result, "score", None)
            fallback = getattr(score, "total", 0.0)
            for value in (
                getattr(result, "ranking_score", np.nan),
                getattr(result, "institutional_score", np.nan),
                getattr(result, "final_score", np.nan),
                fallback,
            ):
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(numeric):
                    return numeric
            return 0.0

        def signal_count(result: Any) -> int:
            details = getattr(result, "filter_details", {})
            if not isinstance(details, dict):
                return 0
            try:
                return int(details.get("signal_count", 0) or 0)
            except (TypeError, ValueError):
                return 0

        def key(result: Any) -> tuple[int, float, int, str]:
            risk_filtered = int(
                str(getattr(result, "ranking_eligibility", "") or "")
                == "风险过滤"
            )
            ticker = str(getattr(result, "ticker", "") or "").strip().upper()
            return (
                risk_filtered,
                -rank_score(result),
                -signal_count(result),
                ticker,
            )

        return sorted(valid, key=key)

    core._rankable_results = rankable_results
    core.REPORT_DETERMINISM_VERSION = REPORT_DETERMINISM_VERSION
    core._REPORT_DETERMINISM_V107_INSTALLED = True
