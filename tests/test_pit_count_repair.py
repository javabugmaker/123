from __future__ import annotations

from types import SimpleNamespace

from institution_scanner.pit_counts import (
    normalize_runtime_counts,
    repair_summary_payload,
)


def test_runtime_counts_fall_back_to_durable_raw_split_sizes() -> None:
    summary = SimpleNamespace(
        rolling_oos={
            "train": 800,
            "validation": 300,
            "test": 100,
        },
        rolling_oos_stats={},
    )

    counts = normalize_runtime_counts(summary, {})

    assert counts["test"] == {
        "raw": 100,
        "verified": 0,
        "unverified": 100,
    }
    assert counts["train"]["raw"] == 800
    assert counts["validation"]["raw"] == 300


def test_runtime_counts_never_infer_verified_from_raw() -> None:
    summary = SimpleNamespace(
        rolling_oos={"test": 100},
        rolling_oos_stats={},
    )

    counts = normalize_runtime_counts(
        summary,
        {"test": {"raw": 0, "verified": 0, "unverified": 0}},
    )

    assert counts["test"]["raw"] == 100
    assert counts["test"]["verified"] == 0
    assert counts["test"]["unverified"] == 100


def test_page_payload_repairs_false_zero_zero_into_pit_warmup() -> None:
    payload = {
        "heldout_raw_test_samples": 0,
        "heldout_verified_test_samples": 0,
        "heldout_unverified_test_samples": 0,
        "heldout_point_in_time_status": "INSUFFICIENT_VERIFIED_TEST",
        "heldout_metric_available": False,
        "rolling_oos": {"test": 1234},
        "rolling_oos_stats": {"test": {"samples": 1234}},
    }

    repaired = repair_summary_payload(payload)

    assert repaired["heldout_raw_test_samples"] == 1234
    assert repaired["heldout_verified_test_samples"] == 0
    assert repaired["heldout_unverified_test_samples"] == 1234
    assert repaired["heldout_point_in_time_status"] == "PIT_WARMUP"
    assert repaired["heldout_metric_available"] is False
    assert "0/1234" in repaired["heldout_metric_warning"]
