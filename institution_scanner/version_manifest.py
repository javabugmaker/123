"""Structured runtime provenance alongside legacy version strings.

Legacy version strings remain compatibility surfaces. New code should consume
this manifest so provenance does not require parsing ever-growing concatenated
strings. The manifest also exposes the remaining compatibility-overlay debt so
canonicalization progress is measurable rather than implicit.
"""

from __future__ import annotations

from typing import Final

from .contracts import CONTRACT_VERSION, PRODUCTION_CONTRACT
from .policy_manifest import build_decision_policy_manifest
from .runtime_inventory import runtime_inventory

VERSION_MANIFEST_SCHEMA: Final = "2026-09-04-v113-structured-version-manifest-v6"


def build_version_manifest() -> dict[str, object]:
    import config

    policy = build_decision_policy_manifest(config)
    return {
        "schema": VERSION_MANIFEST_SCHEMA,
        "production_model": {
            "role": PRODUCTION_CONTRACT.role,
            "contract_version": PRODUCTION_CONTRACT.version,
            "contract_schema": CONTRACT_VERSION,
            "weight_signature": PRODUCTION_CONTRACT.weights.signature(),
        },
        "decision_policy": {
            "schema": policy.schema,
            "hash": policy.policy_hash(),
            "manifest": policy.as_dict(),
        },
        "runtime": {
            "scoring": str(getattr(config, "SCORING_VERSION", "")),
            "pipeline": str(getattr(config, "PIPELINE_VERSION", "")),
            "output_contract": str(getattr(config, "OUTPUT_CONTRACT_VERSION", "")),
            "decision_integrity": str(
                getattr(config, "DECISION_INTEGRITY_VERSION", "")
            ),
            "market_data": str(getattr(config, "MARKET_DATA_VERSION", "")),
            "fundamentals": str(
                getattr(config, "FUNDAMENTAL_REFRESH_INTEGRITY_VERSION", "")
            ),
            "backtest_provenance": str(
                getattr(config, "BACKTEST_PROVENANCE_VERSION", "")
            ),
            "performance_engine": str(
                getattr(config, "PERFORMANCE_ENGINE_VERSION", "")
            ),
        },
        "compatibility_debt": runtime_inventory(),
    }
