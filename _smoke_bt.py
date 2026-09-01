import numpy as np
import pandas as pd

import historical_backtest as hb

bench = hb._load_benchmark()
td = hb._trading_days(bench, hb.START_DATE)
day_index = pd.DatetimeIndex(td)
n_days = len(day_index)
rebalance_indices = list(range(0, n_days, hb.REBALANCE_DAYS))
dates_np = day_index[rebalance_indices].values
pos_np = np.asarray(rebalance_indices, dtype=np.int64)
print("universe size:", len(hb.list_universe()))
print("trading days:", n_days, "rebalances:", len(rebalance_indices))

sample = ["000001.SZ", "000002.SZ", "600519.SH", "300750.SZ", "000858.SZ"]
scores, rets = hb._worker((sample, day_index, dates_np, pos_np))
print("total scores:", len(scores))
from collections import Counter

print("scores per date (first 8):", Counter(d for d,_,_ in scores).most_common(8))
for d, t, v in scores[:10]:
    print(" ", d, t, round(v,2))
print("returns tickers:", list(rets.keys()))
for t in sample:
    if t in rets:
        r = rets[t]
        print(t, "ret len", len(r), "finite", int(np.isfinite(r).sum()), "mean", round(float(np.nanmean(r)),5))