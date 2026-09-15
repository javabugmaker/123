"""The OOS weight override must stay observable and must have an off-ramp.

``score_core._model_component_weights`` reads ``OUTPUT_DIR/ScoreCalibration.json``
at runtime, so a backtest can in principle move the weights the next live scan
uses.  That override is deliberate -- ``config_core`` documents it and the
loader already defends it three ways (``accepted`` flag, guard-railed ranges,
sum-to-one check, and silent fallback to the shipped constants) -- and
``model_weight_signature`` already publishes the effective weights into every
report, so a live override is never invisible.

What was missing was a *master switch*: the only way to disable the override
was to delete the file or edit its ``accepted`` flag.  This module pins the
``MODEL_CALIBRATION_ENABLED`` switch that closes that gap.

The tests deliberately assert the **as-shipped default** rather than
monkeypatching the switch to ``True`` first: a test that sets up the value it
asserts would stay green forever, which is the defect class this file exists
to avoid.
"""

from __future__ import annotations

import json

import pytest

import score_core

_DEFAULTS = (
    score_core.MODEL_SETUP_WEIGHT,
    score_core.MODEL_TRIGGER_WEIGHT,
    score_core.MODEL_EXECUTION_WEIGHT,
)

_ACCEPTED_PAYLOAD = {
    "accepted": True,
    "setup_weight": 0.55,
    "trigger_weight": 0.30,
    "execution_weight": 0.15,
}


@pytest.fixture()
def calibration_dir(tmp_path, monkeypatch):
    """Point the loader at a scratch directory holding an accepted override."""
    monkeypatch.setattr(score_core, "OUTPUT_DIR", tmp_path)
    (tmp_path / "ScoreCalibration.json").write_text(
        json.dumps(_ACCEPTED_PAYLOAD), encoding="utf-8"
    )
    score_core.invalidate_model_weight_cache()
    yield tmp_path
    score_core.invalidate_model_weight_cache()


def test_master_switch_ships_enabled() -> None:
    """Pin the default: calibration stays on until someone turns it off."""
    import config

    assert config.MODEL_CALIBRATION_ENABLED is True, (
        "MODEL_CALIBRATION_ENABLED must default to on; "
        "set MODEL_CALIBRATION_ENABLED=0 only to pin the shipped constants"
    )


def test_switch_enters_decision_policy_signature() -> None:
    """A weight override is a policy change, so the switch must be in provenance."""
    from result_contract import decision_policy_payload

    assert "MODEL_CALIBRATION_ENABLED" in decision_policy_payload()


def test_accepted_calibration_is_applied_when_enabled(calibration_dir) -> None:
    """Baseline: with the switch on, an accepted file really does move the weights."""
    assert score_core._model_component_weights() == (
        _ACCEPTED_PAYLOAD["setup_weight"],
        _ACCEPTED_PAYLOAD["trigger_weight"],
        _ACCEPTED_PAYLOAD["execution_weight"],
    )


def test_disabling_the_switch_pins_the_shipped_constants(
    calibration_dir, monkeypatch
) -> None:
    """The off-ramp: an accepted file on disk must be ignored when disabled."""
    import config

    monkeypatch.setattr(config, "MODEL_CALIBRATION_ENABLED", False)
    assert score_core._model_component_weights() == _DEFAULTS


def test_disabling_does_not_poison_the_cache(calibration_dir, monkeypatch) -> None:
    """Turning the switch back on must still see the file, not a stale default."""
    import config

    monkeypatch.setattr(config, "MODEL_CALIBRATION_ENABLED", False)
    assert score_core._model_component_weights() == _DEFAULTS
    monkeypatch.setattr(config, "MODEL_CALIBRATION_ENABLED", True)
    assert score_core._model_component_weights() == (
        _ACCEPTED_PAYLOAD["setup_weight"],
        _ACCEPTED_PAYLOAD["trigger_weight"],
        _ACCEPTED_PAYLOAD["execution_weight"],
    )
