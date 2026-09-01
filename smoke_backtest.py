import glob, os, sys, time, warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("institution_scanner.score").setLevel(50)
sys.path.insert(0, r"d:\python1\1\InstitutionScanner-main")
import numpy as np
import pandas as pd
import indicators as ind
from institution_scanner.backtest_score_vectorized import final_score_series

ind.ENABLE_VOLUME_PROFILE = False
BASE = r"d:\python1\1\InstitutionScanner-main\cache\v4-tickflow-forward-volume-shares"
fs = sorted(glob.glob(os.path.join(BASE, "*.parquet")))
fs = [f for f in fs if os.path.splitext(os.path.basename(f))[0].endswith((".SH", ".SZ"))]
print("universe files:", len(fs))

sample = fs[:8]
times = []
for f in sample:
    t0 = time.time()
    df = pd.read_parquet(f)
    df = df.loc[~df.index.duplicated(keep="last")].sort_index()
    ind.compute_all_indicators(df)
    ser = final_score_series(df).astype(np.float64)
    n_fin = int(np.isfinite(ser).sum())
    dt = time.time() - t0
    times.append(dt)
    print(f"{os.path.basename(f):20s} bars={len(df):5d} finite_scores={n_fin:5d} time={dt:.3f}s")

avg = float(np.mean(times))
print(f"\navg per-ticker: {avg:.3f}s  -> 6505 tickers ~ {avg*6505/60:.1f} min single-core, ~ {avg*6505/60/12:.1f} min @12 workers")