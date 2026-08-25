"""Runtime verification for generated production artifacts.

The verifier is intentionally independent of the scanner runtime. It checks the
published contract after the DAILY pipeline has finished and before GitHub Pages
is allowed to publish.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from result_contract import (
    REQUIRED_MIXED_VIEW_COLUMNS,
    REQUIRED_PRODUCTION_COLUMNS,
    REQUIRED_TRADE_READY_COLUMNS,
)

from .contracts import PRODUCTION_CONTRACT

VERIFICATION_VERSION: Final = "2026-08-25-v108.5-required-production-schema-v1"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    source = frame.get(column, pd.Series(np.nan, index=frame.index, dtype=float))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return pd.to_numeric(source, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=object))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return source.fillna(default).astype(str).str.strip()


def _bool(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    raw = _text(frame, column, str(default))
    return raw.str.lower().isin({"true", "1", "yes", "y", "是"})


def _issue(
    issues: list[dict[str, str]], severity: str, code: str, detail: str
) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _require_columns(
    frame: pd.DataFrame,
    required: frozenset[str],
    issues: list[dict[str, str]],
    *,
    code: str,
    surface: str,
) -> bool:
    missing = sorted(required.difference(frame.columns))
    if not missing:
        return True
    _issue(
        issues,
        "ERROR",
        code,
        f"{surface} missing required contract columns: {missing}",
    )
    return False


def _verify_all_results(frame: pd.DataFrame, issues: list[dict[str, str]]) -> None:
    if frame.empty:
        _issue(issues, "ERROR", "ALL_RESULTS_EMPTY", "AllResults.csv is empty or missing")
        return

    _require_columns(
        frame,
        REQUIRED_PRODUCTION_COLUMNS,
        issues,
        code="PRODUCTION_SCHEMA_MISSING",
        surface="AllResults",
    )

    if "Ticker" in frame.columns and frame["Ticker"].astype(str).duplicated().any():
        duplicates = int(frame["Ticker"].astype(str).duplicated().sum())
        _issue(
            issues,
            "ERROR",
            "DUPLICATE_TICKERS",
            f"AllResults contains {duplicates} duplicate ticker rows",
        )

    expected_signature = PRODUCTION_CONTRACT.weights.signature()
    if "ModelWeightSignature" in frame.columns:
        observed = sorted(
            {
                str(value).strip()
                for value in frame["ModelWeightSignature"].dropna().tolist()
                if str(value).strip()
            }
        )
        unexpected = [value for value in observed if value != expected_signature]
        if unexpected:
            _issue(
                issues,
                "ERROR",
                "PRODUCTION_WEIGHT_DRIFT",
                f"expected {expected_signature}, observed {unexpected[:4]}",
            )

    if "ProductionModelWeightSignatureLocked" in frame.columns:
        bad = _text(frame, "ProductionModelWeightSignatureLocked").ne(expected_signature)
        if bad.any():
            _issue(
                issues,
                "ERROR",
                "LOCKED_PRODUCTION_SIGNATURE_DRIFT",
                f"{int(bad.sum())} rows do not match locked {expected_signature}",
            )

    if "ProductionModelRole" in frame.columns:
        bad = _text(frame, "ProductionModelRole").str.upper().ne("PRODUCTION_CHAMPION")
        if bad.any():
            _issue(
                issues,
                "ERROR",
                "PRODUCTION_ROLE_INVALID",
                f"{int(bad.sum())} rows do not identify the production champion",
            )

    if {"GlobalCalibrationGovernanceStatus", "BacktestPeerEvidenceWeight"}.issubset(
        frame.columns
    ):
        status = _text(frame, "GlobalCalibrationGovernanceStatus").str.upper()
        peer_weight = _numeric(frame, "BacktestPeerEvidenceWeight").fillna(0.0)
        bad = status.ne("ACTIVE") & peer_weight.abs().gt(1e-12)
        if bad.any():
            _issue(
                issues,
                "ERROR",
                "DIAGNOSTIC_PEER_WEIGHT_NONZERO",
                f"{int(bad.sum())} rows retain peer weight while calibration is diagnostic-only",
            )

    if "GlobalCalibrationApplied" in frame.columns:
        applied = _bool(frame, "GlobalCalibrationApplied")
        if applied.any():
            for column in (
                "GlobalCalibrationPointInTimeVerified",
                "GlobalCalibrationSurvivorshipComplete",
                "GlobalCalibrationLeaveOneOutVerified",
            ):
                bad = applied & ~_bool(frame, column, False)
                if bad.any():
                    _issue(
                        issues,
                        "ERROR",
                        "ACTIVE_PEER_INTEGRITY_UNVERIFIED",
                        f"{int(bad.sum())} active peer rows lack {column}",
                    )

    if {"BacktestEligibleForRanking", "BacktestLocalEvidenceWeight"}.issubset(
        frame.columns
    ):
        eligible = _bool(frame, "BacktestEligibleForRanking")
        local_weight = _numeric(frame, "BacktestLocalEvidenceWeight").fillna(0.0)
        bad = ~eligible & local_weight.abs().gt(1e-12)
        if bad.any():
            _issue(
                issues,
                "ERROR",
                "INELIGIBLE_LOCAL_WEIGHT_NONZERO",
                f"{int(bad.sum())} rows retain local weight while local ranking evidence is ineligible",
            )

    for column, limit in (
        ("AlphaFormulaReconstructionAbsError", 0.001),
        ("RankingFormulaReconstructionAbsError", 0.005),
        ("RankingDecisionInferenceAbsError", 0.001),
    ):
        if column not in frame.columns:
            continue
        maximum = _numeric(frame, column).max(skipna=True)
        if pd.notna(maximum) and float(maximum) > limit:
            _issue(
                issues,
                "ERROR",
                f"{column.upper()}_HIGH",
                f"max {float(maximum):.8f} exceeds {limit}",
            )

    if "ChallengerProductionApplied" in frame.columns:
        applied = _bool(frame, "ChallengerProductionApplied")
        if applied.any():
            _issue(
                issues,
                "ERROR",
                "CHALLENGER_LEAKED_TO_PRODUCTION",
                f"{int(applied.sum())} rows mark shadow challenger as production-applied",
            )

    if "HierarchicalEvidenceProductionApplied" in frame.columns:
        applied = _bool(frame, "HierarchicalEvidenceProductionApplied")
        if applied.any():
            _issue(
                issues,
                "ERROR",
                "HIERARCHICAL_EVIDENCE_LEAKED_TO_PRODUCTION",
                f"{int(applied.sum())} rows mark hierarchical evidence as production-applied",
            )

    if "HierarchicalEvidenceStatus" in frame.columns:
        diagnostic = _text(frame, "HierarchicalEvidenceStatus").str.upper().eq(
            "DIAGNOSTIC_ONLY"
        )
        if diagnostic.any():
            bad_self = diagnostic & ~_bool(
                frame, "HierarchicalEvidenceSelfExcluded", False
            )
            if bad_self.any():
                _issue(
                    issues,
                    "ERROR",
                    "HIERARCHICAL_SELF_INCLUDED",
                    f"{int(bad_self.sum())} diagnostic rows lack leave-one-out certification",
                )

            peers = _numeric(frame, "HierarchicalEvidencePeerTickers")
            bad_peers = diagnostic & (peers.isna() | peers.lt(2.0))
            if bad_peers.any():
                _issue(
                    issues,
                    "ERROR",
                    "HIERARCHICAL_PEER_BREADTH_INVALID",
                    f"{int(bad_peers.sum())} diagnostic rows have fewer than two peer tickers",
                )

            nominal = _numeric(frame, "HierarchicalEvidenceNominalN")
            effective = _numeric(frame, "HierarchicalEvidenceEffectiveN")
            kish = _numeric(frame, "HierarchicalEvidenceKishPeers")
            bad_n = diagnostic & (
                nominal.isna()
                | effective.isna()
                | kish.isna()
                | effective.gt(nominal + 1e-9)
                | effective.gt(kish * 3.0 + 1e-9)
            )
            if bad_n.any():
                _issue(
                    issues,
                    "ERROR",
                    "HIERARCHICAL_EFFECTIVE_N_INVALID",
                    f"{int(bad_n.sum())} diagnostic rows violate LOO/Kish effective-N bounds",
                )


def _verify_mixed(frame: pd.DataFrame, issues: list[dict[str, str]]) -> None:
    if frame.empty:
        _issue(issues, "ERROR", "MIXED_EMPTY", "Top50Mixed.csv is empty or missing")
        return
    if not _require_columns(
        frame,
        REQUIRED_MIXED_VIEW_COLUMNS,
        issues,
        code="MIXED_SCHEMA_MISSING",
        surface="Top50Mixed",
    ):
        return

    if frame["Ticker"].astype(str).duplicated().any():
        _issue(
            issues, "ERROR", "MIXED_DUPLICATE_TICKER", "Mixed view contains duplicate tickers"
        )

    ranks = _numeric(frame, "CandidateViewRank")
    if ranks.isna().any() or ranks.le(0).any():
        _issue(
            issues, "ERROR", "MIXED_RANK_INVALID", "Mixed ranks must be finite and positive"
        )
        return
    if ranks.duplicated().any():
        _issue(
            issues, "ERROR", "MIXED_RANK_DUPLICATE", "CandidateViewRank must be unique"
        )
    expected = list(range(1, len(frame) + 1))
    observed = [int(value) for value in ranks.tolist()]
    if observed != expected:
        _issue(
            issues,
            "ERROR",
            "MIXED_RANK_NOT_SEQUENTIAL",
            f"expected 1..{len(frame)}, first observed ranks={observed[:10]}",
        )


def _verify_trade_ready(
    frame: pd.DataFrame, all_results: pd.DataFrame, issues: list[dict[str, str]]
) -> None:
    if frame.empty:
        _issue(
            issues,
            "WARN",
            "TRADE_READY_EMPTY",
            "Top50TradeReady.csv is empty; valid if no candidate passes hard gates",
        )
        return
    if not _require_columns(
        frame,
        REQUIRED_TRADE_READY_COLUMNS,
        issues,
        code="TRADE_READY_SCHEMA_MISSING",
        surface="Top50TradeReady",
    ):
        return

    tickers = _text(frame, "Ticker")
    if tickers.duplicated().any():
        _issue(
            issues, "ERROR", "TRADE_READY_DUPLICATE", "TradeReady contains duplicate tickers"
        )

    if not all_results.empty and "Ticker" in all_results.columns:
        universe = set(_text(all_results, "Ticker"))
        missing = [ticker for ticker in tickers.tolist() if ticker not in universe]
        if missing:
            _issue(
                issues,
                "ERROR",
                "TRADE_READY_NOT_IN_UNIVERSE",
                f"TradeReady tickers missing from AllResults: {missing[:5]}",
            )

    invalid_state = ~_text(frame, "ExecutionState").str.upper().isin(
        {"READY", "CAUTIOUS", "推荐", "谨慎候选"}
    )
    if invalid_state.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_STATE_INVALID",
            f"{int(invalid_state.sum())} rows are outside READY/CAUTIOUS",
        )

    invalid_quality = _text(frame, "QualityLayerStatus").str.upper().isin(
        {"POLICY_FAIL", "DATA_INCOMPLETE"}
    )
    if invalid_quality.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_QUALITY_INVALID",
            f"{int(invalid_quality.sum())} rows fail the quality layer",
        )

    invalid_signal = _text(frame, "EntrySignal").str.upper().eq("AVOID")
    if invalid_signal.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_SIGNAL_AVOID",
            f"{int(invalid_signal.sum())} rows have EntrySignal=AVOID",
        )

    stale = _text(frame, "DataFreshnessStatus").str.upper().isin(
        {"STALE", "过期", "PROVIDER_LAG", "MISSING", "FUTURE"}
    )
    if stale.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_STALE",
            f"{int(stale.sum())} rows have stale/invalid market data",
        )

    close = _numeric(frame, "Close")
    stop = _numeric(frame, "StopLoss")
    bad_stop = stop.notna() & close.notna() & stop.ge(close)
    if bad_stop.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_STOP_INVALID",
            f"{int(bad_stop.sum())} rows have StopLoss >= Close",
        )

    target = _numeric(frame, "TargetPrice")
    bad_target = target.notna() & close.notna() & target.le(close)
    if bad_target.any():
        _issue(
            issues,
            "ERROR",
            "TRADE_READY_TARGET_INVALID",
            f"{int(bad_target.sum())} rows have TargetPrice <= Close",
        )


def verify_directory(output_dir: Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    issues: list[dict[str, str]] = []
    all_results = _read_csv(output_dir / "AllResults.csv")
    mixed = _read_csv(output_dir / "Top50Mixed.csv")
    if mixed.empty:
        mixed = _read_csv(output_dir / "Top50.csv")
    trade_ready = _read_csv(output_dir / "Top50TradeReady.csv")

    _verify_all_results(all_results, issues)
    _verify_mixed(mixed, issues)
    _verify_trade_ready(trade_ready, all_results, issues)

    errors = [issue for issue in issues if issue["severity"] == "ERROR"]
    warnings = [issue for issue in issues if issue["severity"] == "WARN"]
    payload: dict[str, Any] = {
        "version": VERIFICATION_VERSION,
        "status": "PASS" if not errors else "FAIL",
        "errors": len(errors),
        "warnings": len(warnings),
        "issues": issues,
        "counts": {
            "all_results": len(all_results),
            "mixed": len(mixed),
            "trade_ready": len(trade_ready),
        },
        "production_weight_signature_expected": PRODUCTION_CONTRACT.weights.signature(),
    }
    path = output_dir / "ReliabilityVerification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    output_dir = Path(args[0]) if args else Path("output")
    payload = verify_directory(output_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
