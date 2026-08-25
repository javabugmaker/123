from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scan_worker_consumes_raw_frame_ownership() -> None:
    source = (ROOT / "scanner.py").read_text(encoding="utf-8")

    assert "del downloaded" in source
    assert "frame = downloaded_frames.pop(ticker)" in source
    assert "downloaded_frames[ticker]," not in source
