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
import institution_scanner.backtest_score_vectorized as V
import score_core as sc

ind.ENABLE_VOLUME_PROFILE = False
BASE = r"d:\python1\1\InstitutionScanner-main\cache\v4-tickflow-forward-volume-shares"
fs = sorted(glob.glob(os.path.join(BASE, "*.parquet")))
fs = [f for f in fs if os.path.splitext(os.path.basename(f))[0].endswith((".SH", ".SZ"))]
_by_name = {os.path.basename(x): x for x in fs}
f = _by_name.get("517090.SH.parquet") or fs[1234]
df = pd.read_parquet(f).loc[lambda x: ~x.index.duplicated(keep="last")].sort_index()
ind.compute_all_indicators(df)
print("ticker", os.path.basename(f), "len", len(df))

cols = ["Close","High","Low","Volume","MA20","MA50","MA200","ATR14","ATR50","RSI14","OBV","AD","AD_Slope","CMF","MFI","VolMA20","VolMA120","VolZScore","BB_Width","HV20","HV60","Low52W","DistToLow52W","RegSlope","RegR2","Above_HVN","DistToHVN_Pct"]
print("present:", [c for c in cols if c in df.columns])
print("missing:", [c for c in cols if c not in df.columns])

def get(name):
    return V._col(df, name)

C,H,L,Vol = get("Close"),get("High"),get("Low"),get("Volume")
ma20,ma50,ma200 = get("MA20"),get("MA50"),get("MA200")
atr14,atr50,rsi = get("ATR14"),get("ATR50"),get("RSI14")
obv,ad,ad_slope,cmf,mfi = get("OBV"),get("AD"),get("AD_Slope"),get("CMF"),get("MFI")
vm20,vm120,vz = get("VolMA20"),get("VolMA120"),get("VolZScore")
bbw,hv20,hv60 = get("BB_Width"),get("HV20"),get("HV60")
l52,d52 = get("Low52W"),get("DistToLow52W")
rs,r2 = get("RegSlope"),get("RegR2")
ah,dh = get("Above_HVN"),get("DistToHVN_Pct")

# compute vector sub-scores once (full arrays)
tr = V._trend(C, ma200)
vo = V._volume(df, vm20, vm120, vz)
ac = V._accumulation(df, C, obv, ad, ad_slope, cmf, mfi)
vt = V._volatility(atr14, atr50, bbw, hv20, hv60)
st = V._structure(df, C, H, L, l52, d52, rs, r2, ah, dh)
vtr = V._value_trap(C, Vol, ma20, ma50, cmf, ad_slope, obv)
br = V._breakout(C, H, Vol, ma20, ma50, ma200)

for p in [260, 400, len(df)-1]:
    sub = df.iloc[:p+1]
    row = {
        "trend": (float(tr[p]), float(sc.score_trend(sub))),
        "volume": (float(vo[p]), float(sc.score_volume(sub))),
        "accum": (float(ac[p]), float(sc.score_accumulation(sub))),
        "volat": (float(vt[p]), float(sc.score_volatility(sub))),
        "struct": (float(st[p]), float(sc.score_structure(sub))),
        "trap": (float(vtr[p]), float(sc.value_trap_risk(sub))),
        "breakout": (float(br[p]), float(sc.breakout_score(sub))),
    }
    print(f"--- p={p} ---")
    for k, (g, w) in row.items():
        mark = "OK " if abs(g-w) < 1e-6 else "BAD"
        print(f"  {mark} {k:9s} vec={g:9.3f} scalar={w:9.3f} diff={g-w:8.3f}")
