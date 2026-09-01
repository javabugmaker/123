import glob
import logging
import os
import random
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.getLogger("institution_scanner.score").setLevel(50)
sys.path.insert(0, r"d:\python1\1\InstitutionScanner-main")

import indicators as ind  # noqa: E402
import score_core as sc  # noqa: E402
from institution_scanner.backtest_score_vectorized import final_score_series  # noqa: E402

ind.ENABLE_VOLUME_PROFILE = False

BASE = r"d:\python1\1\InstitutionScanner-main\cache\v4-tickflow-forward-volume-shares"
fs = sorted(glob.glob(os.path.join(BASE, "*.parquet")))
fs = [f for f in fs if os.path.splitext(os.path.basename(f))[0].endswith((".SH", ".SZ"))]
random.seed(2024)
picks = random.sample(fs, 25)

maxdiff = 0.0
nmism = 0
nchecked = 0
for f in picks:
    df = pd.read_parquet(f)
    df = df.loc[~df.index.duplicated(keep="last")].sort_index()
    if len(df) < 260:
        continue
    ind.compute_all_indicators(df)
    ser = final_score_series(df)
    n = len(df)
    positions = set(range(251, n, 30))
    positions.update([252, 253, 254, 259, 260, 300, n - 1])
    for p in sorted(positions):
        if p >= n:
            continue
        sub = df.iloc[: p + 1]
        try:
            want = float(sc.score_ticker(sub).final_score)
        except Exception:
            continue
        got = float(ser[p])
        nchecked += 1
        if np.isnan(want) or np.isnan(got):
            if not (np.isnan(want) and np.isnan(got)):
                nmism += 1
                print(f"NAN-MISMATCH {os.path.basename(f)} pos={p} got={got} want={want}")
                continue
            continue
        d = abs(want - got)
        if d > maxdiff:
            maxdiff = d
        if d > 1e-6:
            nmism += 1
            if nmism <= 30:
                print(
                    f"MISMATCH {os.path.basename(f)} pos={p} "
                    f"got={got:.6f} want={want:.6f} diff={d:.6f}"
                )

print(f"checked={nchecked} mismatches={nmism} maxdiff={maxdiff:.3e}")