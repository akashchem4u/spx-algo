#!/usr/bin/env python3
"""
Probe batch 5:

  VRP-01   "Vol Risk Premium" — VIX > 20d SPX realized vol.
           The market is paying more for options protection than history warrants.
           A positive VRP (VIX > realized) suggests fear is overpriced → mean-revert → bull signal.
           Partially independent from VIX level: VIX=25 with realized=10 is a big premium
           (very bullish); VIX=20 with realized=19 is a small premium (neutral).

  QSPY-01  "QQQ > SPY 5d" — QQQ outperforming SPY over 5 sessions = risk-on tech leadership
           In risk-off/defensive rotations SPY > QQQ; risk-on = QQQ leads.

  QSPY-02  "QQQ > SPY 20d" — same but 20d horizon (more persistent trend filter)
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

GAP_THRESHOLD = 25.0

SIGNAL_GROUPS = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA"],
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "MACD Bullish", "RSI Strong Trend"],
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting", "VIX Below 15", "VIX 1d Down",
                   "VVIX Below 100"],
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%", "Sector Breadth ≥ 85%"],
    "Extremes":   ["Stoch Bullish"],
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
    "Context":    ["Gap/ATR Normal", "VIX No Spike", "Gap Up Day", "Gap Down Contrarian"],
    "Position":   ["52w Range Upper Half", "Above Prior Day High", "Above Pivot", "Above 5d High"],
}
SECTOR_TICKERS = ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]

def _sq(df, col):
    s = df[col] if col in df.columns else df.iloc[:,0]
    return s.squeeze() if isinstance(s, pd.DataFrame) else s

def _sf(s, idx=-1, default=0.0):
    try: return float(s.iloc[idx])
    except: return default

def _rsi(c, p=14):
    d=c.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    return 100-(100/(1+g/l.replace(0,np.nan)))

def _macd(c):
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
    ln=e12-e26; return ln, ln.ewm(span=9,adjust=False).mean()

def _atr(df, p=14):
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close"); pc=c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1).rolling(p).mean()

def _realized_vol(close, window=20):
    """Annualized realized volatility from daily log returns (in VIX-comparable units)."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(252) * 100  # same scale as VIX

def _grp_score(sigs, groups):
    ws = []
    for _, gs in groups.items():
        present = [sigs[n] for n in gs if n in sigs]
        if present: ws.append(sum(present)/len(present))
    return round(sum(ws)/len(ws)*100) if ws else 50

def _compute_core(spx_sl, vix_sl, sector_slices, vvix_sl=None, qqq_sl=None, spy_sl=None):
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl)<20: return sigs
    close=_sq(spx_sl,"Close"); high=_sq(spx_sl,"High"); low=_sq(spx_sl,"Low")
    volume=_sq(spx_sl,"Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close)<20: return sigs
    c=_sf(close); sma20=close.rolling(20).mean(); sma50=close.rolling(50).mean(); sma200=close.rolling(200).mean()
    rsi_v=_rsi(close).dropna(); ml,ms=_macd(close); atr_v=_atr(spx_sl)
    stk_r=high.rolling(14).max()-low.rolling(14).min()
    stk_k=(100*(close-low.rolling(14).min())/stk_r.where(stk_r>0)).fillna(50).clip(0,100)
    stk_d=stk_k.rolling(3).mean().fillna(50)

    sigs["Above 20 SMA"]=int(c>_sf(sma20)); sigs["Above 50 SMA"]=int(c>_sf(sma50)) if len(close)>=50 else 0
    sigs["Above 200 SMA"]=int(c>_sf(sma200)) if len(close)>=200 else 0
    rl=_sf(rsi_v,default=50.0)
    sigs["Higher Close (1d)"]=int(len(close)>=2 and c>_sf(close,-2))
    sigs["Higher Close (5d)"]=int(len(close)>=6 and c>_sf(close,-6))
    sigs["MACD Bullish"]=int(_sf(ml)>_sf(ms)); sigs["RSI Strong Trend"]=int(60<=rl<=75)

    if not vix_sl.empty:
        vc=_sq(vix_sl,"Close"); vv=_sf(vc,default=20.0)
        sigs["VIX Below 20"]=int(vv<20); sigs["VIX Below 15"]=int(vv<15)
        sigs["VIX Falling"]=int(len(vc)>=6 and vv<_sf(vc,-6))
        sigs["VIX 1d Down"]=int(len(vc)>=2 and vv<_sf(vc,-2))
        if len(vc)>=4:
            v3d=_sf(vc,-4,default=vv); sigs["VIX No Spike"]=int((vv-v3d)/max(v3d,1)<=0.08)
        else:
            sigs["VIX No Spike"]=1
        sigs["ATR Contracting"]=int(len(atr_v.dropna())>=20 and _sf(atr_v)<_sf(atr_v,-5))

        # VRP: VIX > 20d realized vol (over-priced fear = contrarian bull)
        if len(close)>=22:
            try:
                rvol = _realized_vol(close, 20)
                rv_last = _sf(rvol.dropna(), default=vv)
                if rv_last > 0:
                    sigs["Vol Risk Premium"] = int(vv > rv_last)
            except Exception:
                pass

    if vvix_sl is not None and len(vvix_sl)>=1:
        try:
            sigs["VVIX Below 100"]=int(float(vvix_sl.iloc[-1])<100)
        except Exception:
            pass

    if len(volume)>=20:
        sigs["Volume Above Average"]=int(_sf(volume)>_sf(volume.rolling(20).mean()) and len(close)>=2 and _sf(close)>_sf(close,-2))

    total_s=len(sector_slices)
    if total_s:
        above=0
        for df in sector_slices.values():
            if df.empty: continue
            try:
                sc=_sq(df,"Close")
                if len(sc)<50: continue
                above+=int(_sf(sc)>_sf(sc.rolling(50).mean()))
            except Exception: continue
        sigs["Sector Breadth ≥ 50%"]=int((above/total_s)>=0.5)
        sigs["Sector Breadth ≥ 85%"]=int((above/total_s)>=0.85)

    if "Open" in spx_sl.columns:
        op=_sq(spx_sl,"Open")
        if len(op)>=2 and len(close)>=2:
            gp=_sf(op)-_sf(close,-2)
            sigs["Gap Up Day"]=int(gp>GAP_THRESHOLD)
            if gp<-GAP_THRESHOLD: sigs["Gap Down Contrarian"]=1

    sigs["Stoch Bullish"]=int(_sf(stk_k)>_sf(stk_d))

    if len(close)>=252:
        h52=_sf(close.rolling(252).max()); l52=_sf(close.rolling(252).min())
        sigs["52w Range Upper Half"]=int((c-l52)/max(h52-l52,1.0)>0.5)
    elif len(close)>=20:
        h20=_sf(high.rolling(20).max()); l20=_sf(low.rolling(20).min())
        sigs["52w Range Upper Half"]=int((c-l20)/max(h20-l20,1.0)>0.5)

    if len(close)>=2:
        sigs["Above Prior Day High"]=int(c>_sf(high,-2))
        sigs["Above Pivot"]=int(c>(_sf(high,-2)+_sf(low,-2)+_sf(close,-2))/3)
    if len(high)>=6:
        sigs["Above 5d High"]=int(c>float(high.iloc[-6:-1].max()))

    # QQQ vs SPY relative momentum
    if qqq_sl is not None and spy_sl is not None and len(qqq_sl)>=6 and len(spy_sl)>=6:
        try:
            q = _sq(qqq_sl,"Close") if isinstance(qqq_sl, pd.DataFrame) else qqq_sl
            s = _sq(spy_sl,"Close") if isinstance(spy_sl, pd.DataFrame) else spy_sl
            # Normalize: compare % return QQQ vs SPY over 5d
            q_ret5 = (_sf(q) - _sf(q,-6)) / max(abs(_sf(q,-6)), 1.0)
            s_ret5 = (_sf(s) - _sf(s,-6)) / max(abs(_sf(s,-6)), 1.0)
            sigs["QQQ > SPY 5d"] = int(q_ret5 > s_ret5)
            if len(q)>=21 and len(s)>=21:
                q_ret20 = (_sf(q) - _sf(q,-21)) / max(abs(_sf(q,-21)), 1.0)
                s_ret20 = (_sf(s) - _sf(s,-21)) / max(abs(_sf(s,-21)), 1.0)
                sigs["QQQ > SPY 20d"] = int(q_ret20 > s_ret20)
        except Exception:
            pass

    return sigs


def run_probe():
    print("[vrp] Fetching 2y data …")
    spx    =yf.download("^GSPC",period="2y",interval="1d",progress=False,auto_adjust=True)
    vix    =yf.download("^VIX", period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix_df=yf.download("^VVIX",period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix   =_sq(vvix_df,"Close") if not vvix_df.empty else pd.Series(dtype=float)
    qqq_df =yf.download("QQQ",  period="2y",interval="1d",progress=False,auto_adjust=True)
    spy_df =yf.download("SPY",  period="2y",interval="1d",progress=False,auto_adjust=True)
    qqq = _sq(qqq_df,"Close") if not qqq_df.empty else pd.Series(dtype=float)
    spy = _sq(spy_df,"Close") if not spy_df.empty else pd.Series(dtype=float)
    sectors={}
    for t in SECTOR_TICKERS:
        try:
            sectors[t]=yf.download(t,period="2y",interval="1d",progress=False,auto_adjust=True)
        except Exception:
            sectors[t]=pd.DataFrame()

    close=_sq(spx,"Close"); opens=_sq(spx,"Open"); n=len(spx)
    eval_start=max(252,n-505)

    # Groups for each variant
    g_vrp  = dict(SIGNAL_GROUPS); g_vrp["Volatility"]=list(SIGNAL_GROUPS["Volatility"])+["Vol Risk Premium"]
    g_q5   = dict(SIGNAL_GROUPS); g_q5["Momentum"]=list(SIGNAL_GROUPS["Momentum"])+["QQQ > SPY 5d"]
    g_q20  = dict(SIGNAL_GROUPS); g_q20["Momentum"]=list(SIGNAL_GROUPS["Momentum"])+["QQQ > SPY 20d"]

    rows_base,rows_vrp,rows_q5,rows_q20=[],[],[],[]
    vrp_fires=q5_fires=q20_fires=0

    for i in range(eval_start,n-1):
        cutoff=spx.index[i]
        spx_sl =spx.iloc[:i+1]
        vix_sl =vix[vix.index<=cutoff]
        vvix_sl=vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        sec_sl ={t:df[df.index<=cutoff] for t,df in sectors.items()}
        qqq_sl =qqq[qqq.index<=cutoff] if not qqq.empty else None
        spy_sl =spy[spy.index<=cutoff] if not spy.empty else None

        sigs=_compute_core(spx_sl,vix_sl,sec_sl,vvix_sl,qqq_sl,spy_sl)
        if not sigs: continue

        nxt=float(close.iloc[i+1]); cur=float(close.iloc[i])
        up=nxt>cur+5; dn=nxt<cur-5
        if not up and not dn: continue
        day_gap=_sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        if sigs.get("Vol Risk Premium",0)==1: vrp_fires+=1
        if sigs.get("QQQ > SPY 5d",0)==1: q5_fires+=1
        if sigs.get("QQQ > SPY 20d",0)==1: q20_fires+=1

        score_b =_grp_score(sigs,SIGNAL_GROUPS)
        score_v =_grp_score(sigs,g_vrp)
        score_q5=_grp_score(sigs,g_q5)
        score_q20=_grp_score(sigs,g_q20)

        for sc,bucket in [(score_b,rows_base),(score_v,rows_vrp),(score_q5,rows_q5),(score_q20,rows_q20)]:
            bull=sc>=55; bear=sc<=44
            if not bull and not bear: continue
            if day_gap<-GAP_THRESHOLD and bear: continue
            correct=(bull and up)or(bear and dn)
            bucket.append({"correct":correct})

    def acc(rows):
        if not rows: return 0,0,0.0
        h=sum(r["correct"] for r in rows); t=len(rows)
        return h,t,round(h/t*100,1)

    bh,bt,ba=acc(rows_base); vh,vt,va=acc(rows_vrp)
    q5h,q5t,q5a=acc(rows_q5); q20h,q20t,q20a=acc(rows_q20)

    n_eval=bt  # approx
    print(f"\n{'─'*65}")
    print(f"  Baseline:                           {bh}/{bt} = {ba}%")
    print(f"  +Vol Risk Premium (Volatility):     {vh}/{vt} = {va}%  Δ={va-ba:+.1f}pp")
    print(f"  +QQQ > SPY 5d (Momentum):           {q5h}/{q5t} = {q5a}%  Δ={q5a-ba:+.1f}pp")
    print(f"  +QQQ > SPY 20d (Momentum):          {q20h}/{q20t} = {q20a}%  Δ={q20a-ba:+.1f}pp")
    print(f"{'─'*65}")
    print(f"  VRP fires: {vrp_fires}  QQQ>SPY5d: {q5_fires}  QQQ>SPY20d: {q20_fires}  (of ~{n_eval} directional calls)")
    if not qqq.empty and not spy.empty:
        qqq_beats_5d = (qqq.pct_change(5) > spy.pct_change(5)).mean()
        qqq_beats_20d = (qqq.pct_change(20) > spy.pct_change(20)).mean()
        print(f"  QQQ beats SPY 5d overall: {round(qqq_beats_5d*100,1)}%  20d: {round(qqq_beats_20d*100,1)}% of 2yr bars")


if __name__ == "__main__":
    run_probe()
    print("\n[vrp] done.")
