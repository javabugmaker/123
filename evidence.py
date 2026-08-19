"""Non-alpha evidence-strength fields for research/UI presentation.

The scanner has two different historical evidence sources:
1. per-ticker backtest samples;
2. peer/global calibration cohorts.

This module summarizes *confidence/coverage*, not expected return.  The evidence
strength fields themselves never change RankingScore or trade eligibility;
peer/global calibration is a separate bounded model input and may already have
contributed to CompositeScore before these explanatory fields are generated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _number(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    return (
        pd.to_numeric(frame.get(column, pd.Series(default, index=frame.index)), errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
        .astype(float)
    )


def _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    return frame.get(column, pd.Series(default, index=frame.index)).fillna(default).astype(str).str.strip()


def enrich_evidence_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """Add explainable evidence-strength fields without changing ranking."""
    result = frame.copy()
    if result.empty:
        for column, dtype in (
            ("TickerEvidence", "object"),
            ("PeerCalibrationEvidence", "object"),
            ("EvidenceStrengthScore", "float64"),
            ("EvidenceTier", "object"),
            ("EvidenceReason", "object"),
        ):
            result[column] = pd.Series(dtype=dtype)
        return result

    samples = _number(result, "BacktestSamples", 0.0).clip(lower=0.0)
    effective = _number(result, "BacktestEffectiveSamples", 0.0).clip(lower=0.0)
    mode = _text(result, "BacktestMode", "NONE").str.upper().replace("", "NONE")
    ticker_tier = _text(result, "BacktestConfidenceTier", "未评估").replace("", "未评估")
    ticker_strength = (effective / 20.0).clip(0.0, 1.0)

    peer_samples = _number(result, "GlobalCalibrationSamples", 0.0).clip(lower=0.0)
    peer_effective = _number(result, "GlobalCalibrationEffectiveSamples", 0.0).clip(lower=0.0)
    peer_count = peer_effective.where(peer_effective.gt(0.0), peer_samples)
    peer_confidence = _number(result, "GlobalCalibrationConfidence", 0.0).clip(0.0, 1.0)
    peer_level = _text(result, "GlobalCalibrationLevel", "none").replace("", "none")
    # Confidence already contains calibration sample-quality logic. The smooth
    # sample term prevents a tiny cohort with a high numerical confidence from
    # looking equivalent to a mature peer cohort.
    peer_sample_strength = np.log1p(peer_count).div(np.log1p(100.0)).clip(0.0, 1.0)
    peer_strength = (peer_confidence * peer_sample_strength).clip(0.0, 1.0)

    evidence = (0.35 * ticker_strength + 0.65 * peer_strength) * 100.0
    has_any = samples.gt(0.0) | peer_count.gt(0.0)
    evidence = evidence.where(has_any, 0.0).clip(0.0, 100.0)
    tier = pd.Series("不足", index=result.index, dtype="object")
    tier.loc[has_any & evidence.lt(30.0)] = "低"
    tier.loc[evidence.ge(30.0)] = "中"
    tier.loc[evidence.ge(55.0)] = "中高"
    tier.loc[evidence.ge(75.0)] = "高"

    result["TickerEvidence"] = [
        f"{m} · {int(s)}样本 · {t}"
        for m, s, t in zip(mode, samples, ticker_tier)
    ]
    result["PeerCalibrationEvidence"] = [
        f"{level} · {count:.0f}有效样本 · {confidence:.0%}"
        if count > 0
        else "无同类校准样本"
        for level, count, confidence in zip(peer_level, peer_count, peer_confidence)
    ]
    result["EvidenceStrengthScore"] = evidence.round(2)
    result["EvidenceTier"] = tier
    result["EvidenceReason"] = [
        (
            f"本票有效样本 {ticker_eff:.1f}；同类有效样本 {peer_eff:.1f}，"
            f"同类置信度 {peer_conf:.0%}。证据等级字段本身不参与排序；"
            "同类全局校准若有有效置信度，可通过综合分的受限校准权重参与模型。"
        )
        for ticker_eff, peer_eff, peer_conf in zip(effective, peer_count, peer_confidence)
    ]
    return result
