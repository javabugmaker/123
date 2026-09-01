import glob
import logging
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings("ignore")
logging.getLogger("institution_scanner.score").setLevel(50)
sys.path.insert(0, r"d:\python1\1\InstitutionScanner-main")
import indicators as ind
import score_core as sc
from institution_scanner.backtest_score_vectorized import final_score_series
from score_core import _model_component_weights

ind.ENABLE_VOLUME_PROFILE = False
BASE = r"d:\python1\1\InstitutionScanner-main\cache\v4-tickflow-forward-volume-shares"

targets = ["517090.SH.parquet", "600661.SH.parquet"]
fs = sorted(glob.glob(os.path.join(BASE, "*.parquet")))
_by = {os.path.basename(x): x for x in fs}

ws, wt, we = _model_component_weights()
print("weights", ws, wt, we)

for name in targets:
    f = _by[name]
    df = pd.read_parquet(f).loc[lambda x: ~x.index.duplicated(keep="last")].sort_index()
    if len(df) < 260:
        continue
    ind.compute_all_indicators(df)
    comp = final_score_series(df, return_components=True)
    n = len(df)
    print("=" * 60)
    print(name, "len", n)
    cnt = 0
    for p in list(range(251, n, 30)) + [n - 1]:
        if p >= n:
            continue
        sub = df.iloc[: p + 1]
        bd = sc.score_ticker(sub)
        got = float(comp["final"][p])
        want = float(bd.final_score)
        if abs(got - want) > 1e-6:
            print(f"pos={p} final got={got:.3f} want={want:.3f} diff={got-want:+.3f}")
            print(f"   base   vec={comp['base'][p]:.3f} sc={bd.base_score:.3f}")
            print(f"   trig   vec={comp['trigger'][p]:.3f} sc={bd.trigger_score:.3f}")
            print(f"   exec   vec={comp['execution'][p]:.3f} sc={bd.execution_score:.3f}")
            print(f"   brk    vec={comp['breakout'][p]:.3f} sc={bd.breakout_score:.3f}")
            print(f"   execraw vec={comp['exec_raw'][p]:.3f}")
            print(f"   trap   vec={comp['trap'][p]:.3f} sc={bd.value_trap_risk:.3f}")
            cnt += 1
            if cnt >= 6:
                break