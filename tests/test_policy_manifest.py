from __future__ import annotations

from dataclasses import replace

import config
from institution_scanner.contracts import PRODUCTION_CONTRACT
from institution_scanner.policy_manifest import build_decision_policy_manifest


def test_policy_manifest_matches_production_champion_weights() -> None:
    policy = build_decision_policy_manifest(config)

    assert policy.model.setup_weight == PRODUCTION_CONTRACT.weights.setup
    assert policy.model.trigger_weight == PRODUCTION_CONTRACT.weights.trigger
    assert policy.model.execution_weight == PRODUCTION_CONTRACT.weights.execution
    assert policy.policy_hash().startswith("DP-")
    assert len(policy.policy_hash()) == 15


def test_policy_hash_is_stable_and_sensitive_to_policy_changes() -> None:
    policy = build_decision_policy_manifest(config)
    same = build_decision_policy_manifest(config)
    changed = replace(
        policy,
        execution=replace(
            policy.execution,
            assumed_order_notional_cny=policy.execution.assumed_order_notional_cny + 1.0,
        ),
    )

    assert policy.policy_hash() == same.policy_hash()
    assert policy.policy_hash() != changed.policy_hash()
