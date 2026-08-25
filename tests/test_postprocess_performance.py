from __future__ import annotations

import warnings

import pandas as pd

from institution_scanner.postprocess_performance import (
    WIDE_FRAME_COLUMN_THRESHOLD,
    defragment_wide_frame,
)


def test_wide_fragmented_frame_is_consolidated_without_value_changes() -> None:
    frame = pd.DataFrame({"base": range(4)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        for index in range(WIDE_FRAME_COLUMN_THRESHOLD + 24):
            frame[f"c{index}"] = index

    before_blocks = int(getattr(frame, "_mgr").nblocks)
    result = defragment_wide_frame(frame)

    pd.testing.assert_frame_equal(result, frame)
    assert result is not frame
    assert int(getattr(result, "_mgr").nblocks) < before_blocks


def test_narrow_frame_does_not_pay_copy_cost() -> None:
    frame = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

    result = defragment_wide_frame(frame)

    assert result is frame
