from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "analytics.py"
text = PATH.read_text(encoding="utf-8")

old_signal_wrapper = '''def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    """Compatibility wrapper returning only historical signal indexes."""
    return [
        index
        for index, _score, _signal in _signal_evaluations(
            enriched, cooldown=cooldown, is_etf=is_etf
        )
    ]
'''
new_signal_wrapper = '''class _SignalPointList(list[int]):
    """List-compatible signal points carrying precomputed real-run evaluations."""

    def __init__(self, evaluations: list[tuple[int, float, str]]) -> None:
        super().__init__(index for index, _score, _signal in evaluations)
        self.evaluations = evaluations


def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    """Return signal indexes while retaining score/signal metadata for the hot path.

    Returning a normal list subtype preserves the public/test contract.  Real
    calls reuse the attached evaluations, while patched/legacy callers that
    return a plain list still follow the historical compatibility path.
    """
    evaluations = _signal_evaluations(
        enriched, cooldown=cooldown, is_etf=is_etf
    )
    return _SignalPointList(evaluations)
'''
if old_signal_wrapper not in text:
    raise RuntimeError("optimized _signal_points wrapper anchor not found")
text = text.replace(old_signal_wrapper, new_signal_wrapper, 1)

old_eval_start = '''    is_etf = is_etf_ticker(str(ticker))
    evaluations = _signal_evaluations(enriched, is_etf=is_etf)
    if not evaluations:
        return []
    evaluation_map = {
        index: (score, signal) for index, score, signal in evaluations
    }
'''
new_eval_start = '''    is_etf = is_etf_ticker(str(ticker))
    signal_points = _signal_points(enriched, is_etf=is_etf)
    if not signal_points:
        return []

    attached_evaluations = getattr(signal_points, "evaluations", None)
    if attached_evaluations is not None:
        evaluation_map = {
            index: (score, signal)
            for index, score, signal in attached_evaluations
        }
    else:
        # Compatibility path for callers/tests that explicitly provide a plain
        # list of historical points.  Each point is still evaluated only once.
        evaluation_map: dict[int, tuple[float, str]] = {}
        for index in signal_points:
            historical = _backtest_scoring_window(enriched, int(index))
            historical_score = score_ticker(historical, is_etf=is_etf)
            final_score = _finite_float(
                getattr(historical_score, "final_score", np.nan), np.nan
            )
            if not np.isfinite(final_score):
                final_score = _finite_float(
                    getattr(historical_score, "total", np.nan), 0.0
                )
            evaluation_map[int(index)] = (
                float(final_score),
                _historical_entry_signal(historical, historical_score),
            )
'''
if old_eval_start not in text:
    raise RuntimeError("optimized evaluation start anchor not found")
text = text.replace(old_eval_start, new_eval_start, 1)

old_loop = '''    for index, _score, _signal in evaluations:
        entry_index = index + 1
'''
new_loop = '''    for index in signal_points:
        entry_index = index + 1
'''
if old_loop not in text:
    raise RuntimeError("optimized valid-point loop anchor not found")
text = text.replace(old_loop, new_loop, 1)

PATH.write_text(text, encoding="utf-8")
print("Backtest compatibility hotfix applied.")
