from __future__ import annotations

import gui_core


def test_v111_fundamental_progress_is_gui_parseable() -> None:
    line = (
        "FUNDAMENTAL progress: 120/5337 (119 checked, 1 failed). "
        "phase=LATEST | workers=6 | rate=2.45 stocks/s | success=99.2% | ETA=35m29s"
    )

    assert gui_core.FUNDAMENTAL_PROGRESS_RE.search(line).groups() == (
        "120",
        "5337",
        "119",
        "1",
    )
    assert gui_core.FUNDAMENTAL_PHASE_RE.search(line).group(1) == "LATEST"
    assert gui_core.FUNDAMENTAL_RATE_RE.search(line).group(1) == "2.45"
    assert gui_core.FUNDAMENTAL_SUCCESS_RE.search(line).group(1) == "99.2"
    assert gui_core.FUNDAMENTAL_ETA_RE.search(line).group(1) == "35m29s"


def test_fundamental_progress_keeps_legacy_english_compatibility() -> None:
    line = "FUNDAMENTAL progress: 9/5337 (9 updated, 0 unavailable)."

    assert gui_core.FUNDAMENTAL_PROGRESS_RE.search(line).groups() == (
        "9",
        "5337",
        "9",
        "0",
    )
