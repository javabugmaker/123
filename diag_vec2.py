import glob, os, sys, warnings, logging, numpy as np, pandas as pd
warnings.filterwarnings("ignore")
logging.getLogger("institution_scanner.score").setLevel(50)
sys.path.insert(0, r"d:\python1\1\InstitutionScanner-main")
import indicators as ind
import score_core as sc
import institution_scanner.backtest_score_vectorized as V

ind.ENABLE_VOLUME_PROFILE = False
BASE = r"d:\python1\1\InstitutionScanner-main\cache\v4-tickflow-forward-volume-shares"
_by = {os.path.basename(x): x for x in sorted(glob.glob(os.path.join(BASE, "*.parquet")))}

def sub_scores(df):
    C = V._col(df, "Close"); H = V._col(df, "High"); L = V._col(df, "Low"); Vol = V._col(df, "Volume")
    ma20 = V._col(df, "MA20"); ma50 = V._col(df, "MA50"); ma200 = V._col(df, "MA200")
    atr14 = V._col(df, "ATR14"); atr50 = V._col(df, "ATR50"); rsi = V._col(df, "RSI14")
    obv = V._col(df, "OBV"); ad = V._col(df, "AD"); ad_slope = V._col(df, "AD_Slope")
    cmf = V._col(df, "CMF"); mfi = V._col(df, "MFI")
    vm20 = V._col(df, "VolMA20"); vm120 = V._col(df, "VolMA120"); vz = V._col(df, "VolZScore")
    bbw = V._col(df, "BB_Width"); hv20 = V._col(df, "HV20"); hv60 = V._col(df, "HV60")
    l52 = V._col(df, "Low52W"); d52 = V._col(df, "DistToLow52W")
    rs = V._col(df, "RegSlope"); r2 = V._col(df, "RegR2")
    ah = V._col(df, "Above_HVN"); dh = V._col(df, "DistToHVN_Pct")
    tr = V._trend(C, ma200)
    vo = V._volume(df, vm20, vm120, vz)
    ac = V._accumulation(df, C, obv, ad, ad_slope, cmf, mfi)
    vt = V._volatility(atr14, atr50, bbw, hv20, hv60)
    st = V._structure(df, C, H, L, l52, d52, rs, r2, ah, dh)
    trap = V._value_trap(C, Vol, ma20, ma50, cmf, ad_slope, obv)
    brk = V._breakout(C, H, Vol, ma20, ma50, ma200)
    return tr, vo, ac, vt, st, trap, brk

checks = [("517090.SH.parquet", [581, 971]), ("600661.SH.parquet", [341, 431])]

for name, positions in checks:
    f = _by[name]
    df = pd.read_parquet(f).loc[lambda x: ~x.index.duplicated(keep="last")].sort_index()
    ind.compute_all_indicators(df)
    tr, vo, ac, vt, st, trap, brk = sub_scores(df)
    n = len(df)
    print("=" * 60)
    print(name, "len", n)
    for p in positions:
        sub = df.iloc[: p + 1]
        comps = [
            ("trend", tr[p], sc.score_trend(sub)),
            ("volume", vo[p], sc.score_volume(sub)),
            ("accum", ac[p], sc.score_accumulation(sub)),
            ("volat", vt[p], sc.score_volatility(sub)),
            ("struct", st[p], sc.score_structure(sub)),
            ("trap", trap[p], sc.value_trap_risk(sub)),
            ("breakout", brk[p], sc.breakout_score(sub)),
        ]
        bad = [(k, g, w, g - w) for k, g, w in comps if abs(g - w) > 1e-6]
        print(f"--- p={p} ---")
        for k, g, w, d in bad:
            print(f"  BAD {k:9s} vec={g:9.3f} scalar={w:9.3f} diff={d:+.3f}")
        if not bad:
            print("   all sub OK")