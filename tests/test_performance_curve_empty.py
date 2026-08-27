import pandas as pd

from performance_curve import build_performance_curve


def test_empty_history_returns_empty_frame() -> None:
    assert build_performance_curve(pd.DataFrame()).empty
