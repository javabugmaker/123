"""Deterministic input scenarios for the ``filters_core`` golden test.

The point of a golden test is to freeze current behaviour without having to
understand or modify the code under test.  Everything here is therefore built
from closed-form formulas — no random number generator, no wall-clock time, no
filesystem — so the same inputs come back byte-identical on every run and on
every machine.

Run this module directly to recapture the fixture::

    python tests/golden_filters_core.py

Recapturing is a deliberate, reviewable act: the golden file is a contract, and
regenerating it silently would destroy the very protection it provides.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Running this file directly puts ``tests/`` on sys.path, not the repo root, so
# the adapters under test would not be importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Importing the facade is what makes this test measure *production* semantics.
#
# ``filters.py`` replaces ``filters_core.filter_min_volume`` and
# ``filter_volatility_contraction`` as an import side effect, then swaps itself
# out for the patched module.  Without this import the golden file freezes the
# bare core implementation, which is not what ``scanner.py`` executes — and the
# result silently depends on whether some other test happened to import the
# facade first.  Importing it explicitly makes the captured state deterministic.
import filters  # noqa: E402,F401
import filters_core  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "filters_core_golden.json"

# Columns ``filters_core`` actually reads.  Several filters guard with
# ``if "<col>" in df.columns``, so a frame missing these is a legitimate input
# rather than a broken one — that is why one scenario omits them on purpose.
INDICATOR_COLUMNS = (
    "MA200",
    "VolMA20",
    "VolMA120",
    "ATR14",
    "ATR50",
    "CMF",
    "AD_Slope",
    "BB_Width",
)

FILTER_FIELDS = (
    "min_price",
    "min_volume",
    "min_market_cap",
    "sufficient_history",
    "bear_market",
    "consolidation",
    "volume_accumulation",
    "obv_divergence",
    "cmf_positive",
    "ad_slope",
    "volatility_contraction",
)


def _frame(
    closes: list[float],
    volumes: list[float],
    *,
    cmf: float = 0.10,
    ad_slope: float = 0.50,
    bb_width: float = 0.15,
    atr14: float = 0.45,
    atr50: float = 0.60,
    amount: float | None = None,
    drop: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Build one OHLCV+indicator frame with self-consistent derived columns."""
    index = pd.RangeIndex(len(closes))
    close_series = pd.Series(closes, dtype="float64")
    volume_series = pd.Series(volumes, dtype="float64")
    # OBV as a running signed sum of volume: rising closes accumulate, falling
    # ones distribute.  The OBV filter compares the first and second half of the
    # window, so a flat OBV would make that comparison vacuous.
    direction = np.sign(close_series.diff().fillna(0.0)).to_numpy()
    obv = np.cumsum(direction * volume_series.to_numpy())
    frame = pd.DataFrame(
        {
            "Close": close_series,
            "High": close_series * 1.01,
            "Low": close_series * 0.99,
            "Volume": volume_series,
            "OBV": pd.Series(obv, dtype="float64"),
        },
        index=index,
    )
    derived = {
        # Default min_periods (== window) so short histories yield NaN, exactly
        # like real indicator output before the warm-up completes.
        "MA200": frame["Close"].rolling(200).mean(),
        "VolMA20": frame["Volume"].rolling(20).mean(),
        "VolMA120": frame["Volume"].rolling(120).mean(),
        "ATR14": pd.Series(atr14, index=index, dtype="float64"),
        "ATR50": pd.Series(atr50, index=index, dtype="float64"),
        "CMF": pd.Series(cmf, index=index, dtype="float64"),
        "AD_Slope": pd.Series(ad_slope, index=index, dtype="float64"),
        "BB_Width": pd.Series(bb_width, index=index, dtype="float64"),
    }
    for name, series in derived.items():
        frame[name] = series
    # ``Amount`` switches the liquidity gate onto the CNY-turnover rule in the
    # v79 facade; without it, the filter falls back to the legacy share-volume
    # rule.  Both branches are production paths, so both need golden coverage.
    if amount is not None:
        frame["Amount"] = pd.Series(amount, index=index, dtype="float64")
    if drop:
        frame = frame.drop(columns=[c for c in drop if c in frame.columns])
    return frame


def _series(length: int, base: float, drift: float, wave: float = 0.02) -> list[float]:
    """Closed-form price path: exponential drift plus a small deterministic wave."""
    steps = np.arange(length, dtype="float64")
    return list(base * np.power(1.0 + drift, steps) * (1.0 + wave * np.sin(steps / 7.0)))


def _volumes(length: int, base: float = 4.0e6) -> list[float]:
    steps = np.arange(length, dtype="float64")
    return list(base * (1.0 + 0.25 * np.sin(steps / 5.0)))


def build_scenarios() -> dict[str, pd.DataFrame]:
    """Return every named input scenario, in a stable order."""
    empty = _frame([], [])
    short = _frame(_series(50, 20.0, 0.001), _volumes(50))
    one_year = _frame(_series(252, 20.0, 0.0015), _volumes(252))
    bull = _frame(_series(300, 12.0, 0.004), _volumes(300), cmf=0.22, ad_slope=1.4, bb_width=0.09)
    bear = _frame(_series(600, 60.0, -0.0018), _volumes(600), cmf=-0.18, ad_slope=-1.1, bb_width=0.31)
    flat = _frame(_series(300, 18.0, 0.0, wave=0.05), _volumes(300), cmf=0.02, ad_slope=0.01, bb_width=0.12)
    missing = _frame(_series(300, 15.0, 0.001), _volumes(300), drop=INDICATOR_COLUMNS)

    # Deliberately hostile values: NaN, infinities, zeros and a negative close.
    dirty_closes = _series(300, 25.0, 0.0008)
    dirty_closes[10] = float("nan")
    dirty_closes[140] = float("inf")
    dirty_closes[200] = 0.0
    dirty_closes[-1] = -3.0
    dirty_volumes = _volumes(300)
    dirty_volumes[7] = float("nan")
    dirty_volumes[150] = float("inf")
    dirty = _frame(dirty_closes, dirty_volumes, cmf=0.05, ad_slope=0.2, bb_width=0.2)

    # Price keeps making new lows while volume expands.  Unusual on its own,
    # but it is the one shape that flips both the volume-accumulation and the
    # OBV-divergence filters — without a case like this they are pinned to a
    # single verdict across every scenario and the golden file cannot detect a
    # change in their logic.
    accumulation_closes = [30.0 + (18.0 - 30.0) * i / 319 for i in range(320)]
    accumulation_volumes = [1.0e6] * 250 + [9.0e6] * 70
    accumulation = _frame(
        accumulation_closes, accumulation_volumes, cmf=0.15, ad_slope=0.8, bb_width=0.10
    )

    # OBV bullish divergence needs all four of: price sits near the recent low,
    # the second half makes a lower price low, the second half's *OBV* low sits
    # above the first half's, and OBV is not still falling.  Shape that with a
    # 60-bar tail — 15 bars down on middling volume (OBV bottoms), 15 bars up on
    # heavy volume (OBV lifted well clear of that bottom), then 30 bars drifting
    # to a new price low on thin volume (OBV gives back only a fraction).
    tail_closes = (
        [20.0 - 1.0 * i / 14 for i in range(15)]
        + [19.0 + 5.0 * (i + 1) / 15 for i in range(15)]
        + [24.0 - 5.2 * (i + 1) / 30 for i in range(30)]
    )
    tail_volumes = [2.0e6] * 15 + [8.0e6] * 15 + [1.0e6] * 30
    obv_closes = [30.0 + (20.0 - 30.0) * i / 259 for i in range(260)] + tail_closes
    obv_volumes = [2.0e6] * 260 + tail_volumes
    divergence = _frame(obv_closes, obv_volumes, cmf=0.05, ad_slope=0.3, bb_width=0.18)

    # Healthy trend, but turnover far below MIN_VOLUME — the only shape that
    # makes the min-volume filter reject.
    thin_volume = _frame(_series(300, 15.0, 0.002), [1.0e3] * 300, cmf=0.12, ad_slope=0.6)

    # Last close sitting exactly on MIN_PRICE.  This is the only input that
    # distinguishes `MIN_PRICE <= x` from `MIN_PRICE < x`, so without it a
    # silent off-by-one at the boundary would pass the golden test unnoticed.
    import config

    boundary_closes = _series(300, 15.0, -0.004)
    boundary_closes[-1] = float(config.MIN_PRICE)
    at_min_price = _frame(boundary_closes, _volumes(300), cmf=0.05, ad_slope=0.2, bb_width=0.2)

    # Same healthy frame, split only by whether 60-day CNY turnover clears the
    # facade's liquidity floor (2.5e6) — the v79 primary path and its rejection.
    turnover_healthy = _frame(
        _series(300, 15.0, 0.002), _volumes(300), cmf=0.12, ad_slope=0.6, amount=8.0e7
    )
    turnover_thin = _frame(
        _series(300, 15.0, 0.002), _volumes(300), cmf=0.12, ad_slope=0.6, amount=1.0e6
    )

    return {
        "empty": empty,
        "short_history_50": short,
        "one_year_boundary_252": one_year,
        "bull_trend_300": bull,
        "bear_trend_600": bear,
        "flat_range_300": flat,
        "accumulation_new_lows_320": accumulation,
        "obv_divergence_320": divergence,
        "thin_volume_300": thin_volume,
        "close_at_min_price_boundary_300": at_min_price,
        "turnover_healthy_300": turnover_healthy,
        "turnover_thin_300": turnover_thin,
        "missing_indicator_columns": missing,
        "dirty_values_300": dirty,
    }


def plain(value: Any) -> Any:
    """Convert numpy/pandas scalars into plain JSON-representable Python types."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        # NaN/inf are not valid JSON; encode them explicitly so the fixture
        # records that the filter really did produce a non-finite number.
        if np.isnan(value):
            return {"__nonfinite__": "nan"}
        if np.isinf(value):
            return {"__nonfinite__": "inf" if value > 0 else "-inf"}
        return value
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _encode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__nonfinite__"}:
        token = value["__nonfinite__"]
        return float("nan") if token == "nan" else float(token)
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def config_snapshot() -> dict[str, Any]:
    import config

    names = [
        "MIN_PRICE",
        "MAX_PRICE",
        "MIN_VOLUME",
        "MIN_MARKET_CAP",
        "BEAR_DECLINE_PCT",
        "BEAR_LOOKBACK_YEARS",
        "BEAR_MA200_DECLINING_DAYS",
        "CMF_THRESHOLD",
        "CONSOLIDATION_DAYS",
        "CONSOLIDATION_MAX_RANGE_PCT",
        "OBV_DIVERGENCE_LOOKBACK",
        "VOLUME_ACCUM_MIN_DAYS",
        "VOLUME_ACCUM_RATIO",
        "AD_SLOPE_LOOKBACK",
        "ATR_COMPRESSION_LOOKBACK",
        "BB_WIDTH_COMPRESSION_LOOKBACK",
    ]
    return {name: plain(getattr(config, name)) for name in names if hasattr(config, name)}


def capture() -> dict[str, Any]:
    """Run every filter against every scenario and return the golden payload."""
    cases: dict[str, Any] = {}
    for name, frame in build_scenarios().items():
        for label, market_cap, required in (
            ("no_market_cap", None, False),
            ("market_cap_ok", 8.0e10, True),
            ("market_cap_missing", None, True),
        ):
            key = f"{name}::{label}"
            # A raised exception is part of the contract too: ``filter_min_volume``
            # indexes ``.iloc[-1]`` on a rolling mean, which is out of bounds for
            # an empty frame.  Recording it means the day someone hardens that
            # path, the golden file goes red and the change gets reviewed —
            # instead of a silent behaviour change nobody notices.
            try:
                result = filters_core.run_all_filters(frame, market_cap, required)
            except Exception as exc:  # noqa: BLE001 - freezing whatever comes out
                cases[key] = {"raised": type(exc).__name__}
                continue
            cases[key] = {
                "passed_count": result.passed_count(),
                "all_passed": result.all_passed(),
                "filters": {
                    field: {
                        "passed": plain(item.passed),
                        "reason": plain(item.reason),
                        "details": plain(item.details),
                    }
                    for field in FILTER_FIELDS
                    for item in [getattr(result, field)]
                },
            }
    # ``plain`` encodes non-finite floats into a JSON-safe marker; undo it so
    # ``capture()`` and ``load()`` return the same shape and can be compared
    # directly.
    return _encode(
        {
            "schema": 1,
            "note": (
                "Golden output of filters_core.run_all_filters after the v79 "
                "facade has been imported. Regenerate with `python "
                "tests/golden_filters_core.py` ONLY after reviewing the diff: "
                "this file is the regression contract for a zero-coverage module."
            ),
            "captured_with": {
                "python": sys.version.split()[0],
                "pandas": pd.__version__,
                "numpy": np.__version__,
            },
            "config_snapshot": config_snapshot(),
            "cases": cases,
        }
    )


def load() -> dict[str, Any]:
    return _encode(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def main() -> None:
    payload = capture()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE_PATH}  cases={len(payload['cases'])}")


if __name__ == "__main__":
    main()
