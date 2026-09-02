from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

import indicators
from institution_scanner.backtest_score_vectorized import final_score_series


def _load_unpatched_exact_scorer() -> ModuleType:
    """Load the scalar file without process-global runtime overlays.

    Other test modules intentionally install compatibility overlays into the
    shared ``score_core`` module during collection.  The historical vector
    mirror targets the stable scalar file used by its standalone validator, so
    this test gives that reference an isolated module namespace.
    """
    name = "_test_unpatched_score_core"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parents[1] / "score_core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scalar scorer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EXACT_SCORE = _load_unpatched_exact_scorer()


def _enriched_frame(*, seed: int, rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.015, rows)))
    open_price = close * np.exp(rng.normal(0.0, 0.004, rows))
    spread = np.abs(rng.normal(0.01, 0.004, rows))
    high = np.maximum(open_price, close) * (1.0 + spread)
    low = np.minimum(open_price, close) * (1.0 - spread)
    volume = rng.lognormal(15.0, 0.8, rows)

    # Suspended/zero-turnover stretches produce legitimate gaps in CMF/MFI.
    # EXACT compacts those series with dropna(); FAST must use the same
    # observation basis instead of treating a row offset as an observation lag.
    for start, stop in ((300, 330), (700, 720)):
        close[start:stop] = close[start - 1]
        open_price[start:stop] = close[start:stop]
        high[start:stop] = close[start:stop]
        low[start:stop] = close[start:stop]
        volume[start:stop] = 0.0

    frame = pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=pd.bdate_range("2020-01-02", periods=rows),
    )
    previous = indicators.ENABLE_VOLUME_PROFILE
    try:
        indicators.ENABLE_VOLUME_PROFILE = False
        return indicators.compute_all_indicators(frame)
    finally:
        indicators.ENABLE_VOLUME_PROFILE = previous


def _assert_scalar_parity(frame: pd.DataFrame, *, is_etf: bool) -> None:
    vectorized = final_score_series(
        frame,
        is_etf=is_etf,
        return_components=True,
    )
    positions = sorted(
        set(range(251, len(frame), 10))
        | {329, 330, 331, 339, 340, 341, 719, 720, 721}
    )
    fields = {
        "final": "final_score",
        "base": "base_score",
        "trigger": "trigger_score",
        "execution": "execution_score",
        "breakout": "breakout_score",
        "trap": "value_trap_risk",
    }
    for position in positions:
        scalar = EXACT_SCORE.score_ticker(
            frame.iloc[: position + 1],
            is_etf=is_etf,
        )
        for vector_name, scalar_name in fields.items():
            np.testing.assert_allclose(
                float(vectorized[vector_name][position]),
                float(getattr(scalar, scalar_name)),
                rtol=0.0,
                atol=1e-10,
                err_msg=(
                    f"position={position} field={vector_name} "
                    f"is_etf={is_etf}"
                ),
            )


def test_fast_score_matches_exact_after_zero_turnover_indicator_gaps() -> None:
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    _assert_scalar_parity(_enriched_frame(seed=1), is_etf=False)


def test_fast_score_applies_exact_etf_style_and_price_precision() -> None:
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    _assert_scalar_parity(_enriched_frame(seed=2), is_etf=True)
