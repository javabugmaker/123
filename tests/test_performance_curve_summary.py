import pandas as pd
from performance_curve import curve_summary


def test_summary_empty_contract() -> None:
    assert curve_summary(pd.DataFrame())["rows"] == 0
