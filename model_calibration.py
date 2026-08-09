from __future__ import annotations

"""Out-of-sample calibration helpers for InstitutionScanner.

This module is intentionally independent from the scanner/GUI layers.  It turns
historical backtest samples into model-level evidence that can be reused when a
single ticker has too few independent observations, and it evaluates the model
with expanding-window walk-forward tests.
"""

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

DEFAULT_COMPONENT_WEIGHTS: tuple[float, float, float] = (0.60, 0.25, 0.15)
SCORE_BUCKET_EDGES: tuple[float, ...] = (-np.inf, 40.0, 50.0, 60.0, 70.0, 80.0, np.inf)
SCORE_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-50", "50-60", "60-70", "70-80", ">=80")
SETUP_BUCKET_EDGES: tuple[float, ...] = (-np.inf, 40.0, 55.0, 70.0, np.inf)
SETUP_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-55", "55-70", ">=70")


@dataclass(frozen=True)
class ComponentCalibration:
    setup_weight: float = DEFAULT_COMPONENT_WEIGHTS[0]
    trigger_weight: float = DEFAULT_COMPONENT_WEIGHTS[1]
    execution_weight: float = DEFAULT_COMPONENT_WEIGHTS[2]
    accepted: bool = False
    validation_ic: float = 0.0
    validation_default_ic: float = 0.0
    test_ic: float = 0.0
    test_default_ic: float = 0.0
    validation_samples: int = 0
    test_samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_weight": round(float(self.setup_weight), 4),
            "trigger_weight": round(float(self.trigger_weight), 4),
            "execution_weight": round(float(self.execution_weight), 4),
            "accepted": bool(self.accepted),
            "validation_ic": round(float(self.validation_ic), 6),
            "validation_default_ic": round(float(self.validation_default_ic), 6),
            "test_ic": round(float(self.test_ic), 6),
            "test_default_ic": round(float(self.test_default_ic), 6),
            "validation_samples": int(self.validation_samples),
            "test_samples": int(self.test_samples),
        }


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _normalize_regime(value: Any) -> str:
    text = str(value or "UNKNOWN").strip().upper()
    if text in {"RISK_ON", "风险偏好", "风险偏好环境"}:
        return "RISK_ON"
    if text in {"RISK_OFF", "风险规避", "风险规避环境"}:
        return "RISK_OFF"
    if text in {"NEUTRAL", "震荡", "震荡修复", "震荡转弱"}:
        return "NEUTRAL"
    return "UNKNOWN"


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    valid = values.notna() & weights.notna() & weights.gt(0)
    if not valid.any():
        return float("nan")
    value_array = values.loc[valid].to_numpy(dtype=float)
    weight_array = weights.loc[valid].to_numpy(dtype=float)
    total = float(weight_array.sum())
    return float(np.dot(value_array, weight_array) / total) if total > 0 else float("nan")


def _weighted_rate(values: pd.Series, weights: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.notna() & weights.notna() & weights.gt(0)
    if not valid.any():
        return float("nan")
    return _weighted_mean(numeric.loc[valid].gt(0).astype(float), weights.loc[valid])


def _spearman(score: pd.Series, target: pd.Series) -> float:
    data = pd.concat(
        [pd.to_numeric(score, errors="coerce"), pd.to_numeric(target, errors="coerce")],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 3 or data.iloc[:, 0].nunique() < 2 or data.iloc[:, 1].nunique() < 2:
        return 0.0
    value = data.iloc[:, 0].rank().corr(data.iloc[:, 1].rank())
    return float(value) if pd.notna(value) and np.isfinite(value) else 0.0


def _prepare_samples(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "asset_type" not in result:
        result["asset_type"] = result.get("ticker", pd.Series("", index=result.index)).astype(str).map(
            lambda ticker: "etf" if ticker.startswith(("15", "16", "51", "52", "56", "58")) else "stock"
        )
    result["asset_type"] = result["asset_type"].fillna("stock").astype(str).str.lower()
    result["entry_signal"] = result.get("entry_signal", pd.Series("UNKNOWN", index=result.index)).fillna("UNKNOWN").astype(str).str.upper()
    result["market_regime"] = result.get("market_regime", pd.Series("UNKNOWN", index=result.index)).map(_normalize_regime)
    result["score"] = _numeric(result, "score")
    result["setup_score"] = _numeric(result, "setup_score")
    result["sample_weight"] = _numeric(result, "sample_weight").fillna(1.0).clip(0.0, 1.0)
    result["net_excess20"] = _numeric(result, "net_return20") - _numeric(result, "benchmark_return20")
    result["net_excess60"] = _numeric(result, "net_return60") - _numeric(result, "benchmark_return60")
    result["score_bucket"] = pd.cut(
        result["score"],
        bins=SCORE_BUCKET_EDGES,
        labels=SCORE_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")
    result["setup_bucket"] = pd.cut(
        result["setup_score"],
        bins=SETUP_BUCKET_EDGES,
        labels=SETUP_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")
    result["entry_date"] = pd.to_datetime(result.get("entry_date"), errors="coerce")
    return result


def _calibration_score(mean_excess: float, win_rate: float, effective_samples: float, min_samples: int) -> tuple[float, float]:
    if not np.isfinite(mean_excess) or not np.isfinite(win_rate) or effective_samples <= 0:
        return 50.0, 0.0
    confidence = float(np.clip(effective_samples / max(float(min_samples) * 2.0, 1.0), 0.0, 1.0))
    raw = 50.0 + float(np.clip(mean_excess, -10.0, 10.0)) * 3.0 + float(np.clip(win_rate - 0.5, -0.3, 0.3)) * 40.0
    score = 50.0 + (float(np.clip(raw, 0.0, 100.0)) - 50.0) * confidence
    return round(score, 4), round(confidence, 4)


def build_global_calibration(
    frame: pd.DataFrame,
    *,
    min_samples: int = 30,
) -> list[dict[str, Any]]:
    """Build hierarchical peer calibration without using the held-out test set.

    Levels are stored from most to least specific.  Resolution can therefore
    fall back from asset+signal+score-bucket to broader evidence instead of
    reverting immediately to a ticker's tiny sample history.
    """
    if frame is None or frame.empty:
        return []
    sample = _prepare_samples(frame)
    sample = sample.dropna(subset=["net_excess20", "score"])
    if sample.empty:
        return []

    levels: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("asset_signal_regime_score_setup", ("asset_type", "entry_signal", "market_regime", "score_bucket", "setup_bucket")),
        ("asset_signal_regime_score", ("asset_type", "entry_signal", "market_regime", "score_bucket")),
        ("asset_signal_regime", ("asset_type", "entry_signal", "market_regime")),
        ("asset_signal_bucket", ("asset_type", "entry_signal", "score_bucket")),
        ("asset_signal", ("asset_type", "entry_signal")),
        ("signal_bucket", ("entry_signal", "score_bucket")),
        ("signal", ("entry_signal",)),
        ("asset", ("asset_type",)),
        ("global", tuple()),
    )
    rows: list[dict[str, Any]] = []
    for level, keys in levels:
        groups: Iterable[tuple[Any, pd.DataFrame]]
        if keys:
            grouping_key: str | list[str] = keys[0] if len(keys) == 1 else list(keys)
            groups = sample.groupby(grouping_key, dropna=False, sort=False)
        else:
            groups = [("global", sample)]
        for key_values, group in groups:
            weights = group["sample_weight"]
            effective = float(weights.sum())
            if len(group) < max(3, min_samples // 3) or effective < max(3.0, min_samples / 4.0):
                continue
            mean20 = _weighted_mean(group["net_excess20"], weights)
            mean60 = _weighted_mean(group["net_excess60"], weights)
            win20 = _weighted_rate(group["net_excess20"], weights)
            score, confidence = _calibration_score(mean20, win20, effective, min_samples)
            if not isinstance(key_values, tuple):
                key_values = (key_values,)
            row: dict[str, Any] = {
                "level": level,
                "samples": int(len(group)),
                "effective_samples": round(effective, 4),
                "mean_net_excess20": round(float(mean20), 4) if np.isfinite(mean20) else np.nan,
                "mean_net_excess60": round(float(mean60), 4) if np.isfinite(mean60) else np.nan,
                "win_rate_net_excess20": round(float(win20), 4) if np.isfinite(win20) else np.nan,
                "calibration_score": score,
                "confidence": confidence,
                "start_date": str(group["entry_date"].min().date()) if group["entry_date"].notna().any() else "",
                "end_date": str(group["entry_date"].max().date()) if group["entry_date"].notna().any() else "",
            }
            for column, value in zip(keys, key_values):
                row[column] = "" if pd.isna(value) else str(value)
            rows.append(row)
    return rows


def resolve_global_calibration(
    asset_type: str,
    entry_signal: str,
    score: float,
    rows: list[dict[str, Any]] | None,
    market_regime: str = "UNKNOWN",
    setup_score: float = np.nan,
) -> tuple[float, float, str]:
    if not rows:
        return 50.0, 0.0, "none"
    asset = str(asset_type or "stock").lower()
    signal = str(entry_signal or "UNKNOWN").upper()
    regime = _normalize_regime(market_regime)
    bucket_series = pd.cut(
        pd.Series([score], dtype=float),
        bins=SCORE_BUCKET_EDGES,
        labels=SCORE_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    )
    bucket = str(bucket_series.iloc[0]) if pd.notna(bucket_series.iloc[0]) else ""
    setup_bucket_series = pd.cut(
        pd.Series([setup_score], dtype=float),
        bins=SETUP_BUCKET_EDGES,
        labels=SETUP_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    )
    setup_bucket = str(setup_bucket_series.iloc[0]) if pd.notna(setup_bucket_series.iloc[0]) else ""
    priorities = (
        ("asset_signal_regime_score_setup", {"asset_type": asset, "entry_signal": signal, "market_regime": regime, "score_bucket": bucket, "setup_bucket": setup_bucket}),
        ("asset_signal_regime_score", {"asset_type": asset, "entry_signal": signal, "market_regime": regime, "score_bucket": bucket}),
        ("asset_signal_regime", {"asset_type": asset, "entry_signal": signal, "market_regime": regime}),
        ("asset_signal_bucket", {"asset_type": asset, "entry_signal": signal, "score_bucket": bucket}),
        ("asset_signal", {"asset_type": asset, "entry_signal": signal}),
        ("signal_bucket", {"entry_signal": signal, "score_bucket": bucket}),
        ("signal", {"entry_signal": signal}),
        ("asset", {"asset_type": asset}),
        ("global", {}),
    )
    for level, expected in priorities:
        candidates = [row for row in rows if str(row.get("level", "")) == level]
        for row in candidates:
            if all(str(row.get(key, "")) == str(value) for key, value in expected.items()):
                try:
                    calibration_score = float(row.get("calibration_score", 50.0))
                    confidence = float(row.get("confidence", 0.0))
                except (TypeError, ValueError):
                    continue
                if np.isfinite(calibration_score) and np.isfinite(confidence):
                    return float(np.clip(calibration_score, 0.0, 100.0)), float(np.clip(confidence, 0.0, 1.0)), level
    return 50.0, 0.0, "none"


def calibration_details_for_frame(
    frame: pd.DataFrame,
    rows: list[dict[str, Any]] | None,
) -> pd.DataFrame:
    """Resolve hierarchical calibration for a result frame in near-linear time.

    v29 resolved every ticker by repeatedly scanning the complete calibration
    row list, then scanned it a second time to recover detail fields.  A full
    market run therefore paid an O(result_rows * calibration_rows) Python cost.
    v30 builds one immutable lookup keyed by the same hierarchy and computes
    score/setup buckets in vectorized form.  Resolution semantics and priority
    order are unchanged, but each candidate now needs at most nine dictionary
    lookups.
    """
    columns = {
        "score": 50.0,
        "confidence": 0.0,
        "level": "none",
        "samples": 0,
        "effective_samples": 0.0,
        "mean_net_excess20": np.nan,
        "win_rate_net_excess20": np.nan,
        "start_date": "",
        "end_date": "",
    }
    if frame.empty:
        return pd.DataFrame(
            {
                key: pd.Series(
                    dtype=float if isinstance(value, (int, float)) else str
                )
                for key, value in columns.items()
            },
            index=frame.index,
        )
    if not rows:
        return pd.DataFrame(
            {key: pd.Series(value, index=frame.index) for key, value in columns.items()}
        )

    level_fields: dict[str, tuple[str, ...]] = {
        "asset_signal_regime_score_setup": (
            "asset_type",
            "entry_signal",
            "market_regime",
            "score_bucket",
            "setup_bucket",
        ),
        "asset_signal_regime_score": (
            "asset_type",
            "entry_signal",
            "market_regime",
            "score_bucket",
        ),
        "asset_signal_regime": ("asset_type", "entry_signal", "market_regime"),
        "asset_signal_bucket": ("asset_type", "entry_signal", "score_bucket"),
        "asset_signal": ("asset_type", "entry_signal"),
        "signal_bucket": ("entry_signal", "score_bucket"),
        "signal": ("entry_signal",),
        "asset": ("asset_type",),
        "global": tuple(),
    }
    lookup: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        level = str(row.get("level", ""))
        fields = level_fields.get(level)
        if fields is None:
            continue
        key = (level, *(str(row.get(field, "")) for field in fields))
        # build_global_calibration emits unique hierarchy keys.  setdefault also
        # preserves legacy "first row wins" behaviour for hand-written inputs.
        lookup.setdefault(key, row)

    asset_values = frame.get(
        "AssetType", frame.get("asset_type", pd.Series("stock", index=frame.index))
    ).fillna("stock").astype(str).str.lower()
    signal_values = frame.get(
        "EntrySignal", frame.get("entry_signal", pd.Series("UNKNOWN", index=frame.index))
    ).fillna("UNKNOWN").astype(str).str.upper()
    regime_values = frame.get(
        "MarketRegime", frame.get("market_regime", pd.Series("UNKNOWN", index=frame.index))
    ).map(_normalize_regime)
    model_scores = pd.to_numeric(
        frame.get("FinalScore", frame.get("score", pd.Series(np.nan, index=frame.index))),
        errors="coerce",
    )
    setup_values = pd.to_numeric(
        frame.get("BaseScore", frame.get("setup_score", pd.Series(np.nan, index=frame.index))),
        errors="coerce",
    )
    score_buckets = pd.cut(
        model_scores,
        bins=SCORE_BUCKET_EDGES,
        labels=SCORE_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")
    setup_buckets = pd.cut(
        setup_values,
        bins=SETUP_BUCKET_EDGES,
        labels=SETUP_BUCKET_LABELS,
        right=False,
        include_lowest=True,
    ).astype("object")

    records: list[dict[str, Any]] = []
    for asset, signal, regime, bucket_value, setup_bucket_value in zip(
        asset_values, signal_values, regime_values, score_buckets, setup_buckets
    ):
        asset = str(asset)
        signal = str(signal)
        regime = str(regime)
        bucket = str(bucket_value) if pd.notna(bucket_value) else ""
        setup_bucket = str(setup_bucket_value) if pd.notna(setup_bucket_value) else ""
        keys = (
            (
                "asset_signal_regime_score_setup",
                asset,
                signal,
                regime,
                bucket,
                setup_bucket,
            ),
            ("asset_signal_regime_score", asset, signal, regime, bucket),
            ("asset_signal_regime", asset, signal, regime),
            ("asset_signal_bucket", asset, signal, bucket),
            ("asset_signal", asset, signal),
            ("signal_bucket", signal, bucket),
            ("signal", signal),
            ("asset", asset),
            ("global",),
        )
        matched: dict[str, Any] | None = None
        score = 50.0
        confidence = 0.0
        level = "none"
        for key in keys:
            candidate = lookup.get(tuple(str(value) for value in key))
            if candidate is None:
                continue
            try:
                candidate_score = float(candidate.get("calibration_score", 50.0))
                candidate_confidence = float(candidate.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(candidate_score) or not np.isfinite(candidate_confidence):
                continue
            matched = candidate
            score = float(np.clip(candidate_score, 0.0, 100.0))
            confidence = float(np.clip(candidate_confidence, 0.0, 1.0))
            level = str(candidate.get("level", "none"))
            break

        matched = matched or {}
        mean_excess = pd.to_numeric(
            pd.Series([matched.get("mean_net_excess20", np.nan)]), errors="coerce"
        ).iloc[0]
        win_rate = pd.to_numeric(
            pd.Series([matched.get("win_rate_net_excess20", np.nan)]), errors="coerce"
        ).iloc[0]
        records.append(
            {
                "score": score,
                "confidence": confidence,
                "level": level,
                "samples": int(matched.get("samples", 0) or 0),
                "effective_samples": float(matched.get("effective_samples", 0.0) or 0.0),
                "mean_net_excess20": mean_excess,
                "win_rate_net_excess20": win_rate,
                "start_date": str(matched.get("start_date", "") or ""),
                "end_date": str(matched.get("end_date", "") or ""),
            }
        )
    return pd.DataFrame.from_records(records, index=frame.index)

def calibration_scores_for_frame(
    frame: pd.DataFrame,
    rows: list[dict[str, Any]] | None,
) -> tuple[pd.Series, pd.Series]:
    details = calibration_details_for_frame(frame, rows)
    return details["score"], details["confidence"]


def _component_score(frame: pd.DataFrame, weights: tuple[float, float, float]) -> pd.Series:
    setup = _numeric(frame, "setup_score")
    trigger = _numeric(frame, "trigger_score")
    execution = _numeric(frame, "execution_score")
    return setup * weights[0] + trigger * weights[1] + execution * weights[2]


def calibrate_component_weights(frame: pd.DataFrame) -> ComponentCalibration:
    """Select bounded component weights on validation data only.

    The test split is never used to choose weights.  It is reported only as an
    audit metric so repeatedly running the tool cannot silently tune on test.
    """
    if frame is None or frame.empty:
        return ComponentCalibration()
    sample = _prepare_samples(frame)
    required = ("setup_score", "trigger_score", "execution_score")
    if any(column not in sample for column in required):
        return ComponentCalibration()
    for column in required:
        sample[column] = _numeric(sample, column)
    sample = sample.dropna(subset=[*required, "net_excess20"])
    validation = sample.loc[sample.get("split", "").astype(str).eq("validation")]
    test = sample.loc[sample.get("split", "").astype(str).eq("test")]
    if len(validation) < 30:
        return ComponentCalibration(validation_samples=len(validation), test_samples=len(test))

    default_validation = _spearman(_component_score(validation, DEFAULT_COMPONENT_WEIGHTS), validation["net_excess20"])
    best_weights = DEFAULT_COMPONENT_WEIGHTS
    best_ic = default_validation
    for setup in np.arange(0.45, 0.701, 0.05):
        for trigger in np.arange(0.15, 0.351, 0.05):
            execution = 1.0 - float(setup) - float(trigger)
            if execution < 0.10 - 1e-9 or execution > 0.25 + 1e-9:
                continue
            weights = (round(float(setup), 4), round(float(trigger), 4), round(float(execution), 4))
            ic = _spearman(_component_score(validation, weights), validation["net_excess20"])
            # Require a measurable validation improvement; ties keep defaults.
            if ic > best_ic + 1e-9:
                best_ic = ic
                best_weights = weights

    accepted = bool(best_weights != DEFAULT_COMPONENT_WEIGHTS and best_ic >= default_validation + 0.01)
    selected = best_weights if accepted else DEFAULT_COMPONENT_WEIGHTS
    test_selected = _spearman(_component_score(test, selected), test["net_excess20"]) if len(test) >= 3 else 0.0
    test_default = _spearman(_component_score(test, DEFAULT_COMPONENT_WEIGHTS), test["net_excess20"]) if len(test) >= 3 else 0.0
    return ComponentCalibration(
        setup_weight=selected[0],
        trigger_weight=selected[1],
        execution_weight=selected[2],
        accepted=accepted,
        validation_ic=best_ic if accepted else default_validation,
        validation_default_ic=default_validation,
        test_ic=test_selected,
        test_default_ic=test_default,
        validation_samples=len(validation),
        test_samples=len(test),
    )


def walk_forward_stats(
    frame: pd.DataFrame,
    *,
    min_train_samples: int = 80,
    min_test_samples: int = 15,
) -> list[dict[str, Any]]:
    """Expanding-window yearly OOS evaluation of global calibration evidence."""
    if frame is None or frame.empty:
        return []
    sample = _prepare_samples(frame).dropna(subset=["entry_date", "net_excess20", "score"])
    if sample.empty:
        return []
    years = sorted(int(year) for year in sample["entry_date"].dt.year.dropna().unique())
    rows: list[dict[str, Any]] = []
    for year in years:
        start = pd.Timestamp(year=year, month=1, day=1)
        end = pd.Timestamp(year=year + 1, month=1, day=1)
        train = sample.loc[sample["entry_date"] < start]
        test = sample.loc[(sample["entry_date"] >= start) & (sample["entry_date"] < end)]
        if len(train) < min_train_samples or len(test) < min_test_samples:
            continue
        calibration = build_global_calibration(train)
        predicted, confidence = calibration_scores_for_frame(
            test.rename(columns={"asset_type": "AssetType", "entry_signal": "EntrySignal", "score": "FinalScore"}),
            calibration,
        )
        test_target = test["net_excess20"].reset_index(drop=True)
        predicted = predicted.reset_index(drop=True)
        confidence = confidence.reset_index(drop=True)
        valid = predicted.notna() & test_target.notna() & confidence.gt(0)
        if valid.sum() < min_test_samples:
            continue
        rank_ic = _spearman(predicted.loc[valid], test_target.loc[valid])
        ranked = pd.DataFrame({"prediction": predicted.loc[valid], "target": test_target.loc[valid]})
        ranked["bucket"] = pd.qcut(ranked["prediction"].rank(method="first"), q=min(5, len(ranked)), labels=False, duplicates="drop")
        top = ranked.loc[ranked["bucket"].eq(ranked["bucket"].max()), "target"]
        bottom = ranked.loc[ranked["bucket"].eq(ranked["bucket"].min()), "target"]
        rows.append(
            {
                "year": year,
                "train_samples": int(len(train)),
                "test_samples": int(valid.sum()),
                "rank_ic": round(rank_ic, 6),
                "top_bucket_net_excess20": round(float(top.mean()), 4) if not top.empty else np.nan,
                "bottom_bucket_net_excess20": round(float(bottom.mean()), 4) if not bottom.empty else np.nan,
                "top_bottom_spread20": round(float(top.mean() - bottom.mean()), 4) if not top.empty and not bottom.empty else np.nan,
                "mean_net_excess20": round(float(test_target.loc[valid].mean()), 4),
            }
        )
    return rows
