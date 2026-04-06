#!/usr/bin/env python3
"""
Probe batch 6:

  CMF-01  "CMF Positive" — Chaikin Money Flow (20d) > 0
          CMF = sum[(((close-low)-(high-close))/(high-low)) * volume] / sum[volume]  over 20 bars
          CMF > 0 = net accumulation (smart money buying) = bullish directional bias
          Fires ~50-60% of bars — more selective than pure price-based momentum

  WPIVOT-01  "Above Weekly Pivot" — close > weekly pivot (prev week H+L+C / 3)
             Weekly-scale support/resistance: if price holds above weekly pivot,
             bullish continuation bias. Different from daily pivot (already in Position group).
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

def _cmf(df, period=20) -> pd.Series:
    """Chaikin Money Flow."""
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close")
    vol=_sq(df,"Volume") if "Volume" in df.columns else pd.Series(np.ones(len(c)), index=c.index)
    hl_range = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / hl_range  # money flow multiplier
    mfv = mfm * vol  # money flow volume
    cmf = mfv.rolling(period).sum() / vol.rolling(period).sum()
    return cmf

def _weekly_pivot(df) -> pd.Series:
    """Previous-week pivot = (prev week H + L + C) / 3. Aligned to daily index."""
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close")
    daily = pd.DataFrame({"H":h,"L":l,"C":c}, index=df.index if hasattr(df,"index") else h.index)
    # Resample to weekly
    weekly = daily.resample("W").agg({"H":"max","L":"min","C":"last"})
    weekly["pivot"] = (weekly["H"] + weekly["L"] + weekly["C"]) / 3
    # Shift by 1 week so we use prev week's pivot on current week's days
    weekly["pivot"] = weekly["pivot"].shift(1)
    # Forward-fill to daily
    pivot_daily = weekly["pivot"].reindex(daily.index, method="ffill")
    return pivot_daily

def _grp_score(sigs, groups):
    ws = []
    for _, gs in groups.items():
        present = [sigs[n] for n in gs if n in sigs]
        if present: ws.append(sum(present)/len(present))
    return round(sum(ws)/len(ws)*100) if ws else 50

def _compute_core(spx_sl, vix_sl, sector_slices, vvix_sl=None):
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

    # CMF
    if len(spx_sl)>=22:
        try:
            cmf_v = _cmf(spx_sl, 20)
            sigs["CMF Positive"] = int(_sf(cmf_v.dropna(), default=0.0) > 0)
        except Exception:
            pass

    # Weekly pivot — uses full history to build weekly resample
    if len(spx_sl)>=10:
        try:
            wp = _weekly_pivot(spx_sl)
            if not wp.empty and not wp.isna().all():
                wp_val = float(wp.iloc[-1]) if not pd.isna(wp.iloc[-1]) else 0.0
                if wp_val > 0:
                    sigs["Above Weekly Pivot"] = int(c > wp_val)
        except Exception:
            pass

    return sigs


def run_probe():
    print("[cmf] Fetching 2y data …")
    spx    =yf.download("^GSPC",period="2y",interval="1d",progress=False,auto_adjust=True)
    vix    =yf.download("^VIX", period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix_df=yf.download("^VVIX",period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix   =_sq(vvix_df,"Close") if not vvix_df.empty else pd.Series(dtype=float)
    sectors={}
    for t in SECTOR_TICKERS:
        try: sectors[t]=yf.download(t,period="2y",interval="1d",progress=False,auto_adjust=True)
        except: sectors[t]=pd.DataFrame()

    close=_sq(spx,"Close"); opens=_sq(spx,"Open"); n=len(spx)
    eval_start=max(252,n-505)

    g_cmf   = dict(SIGNAL_GROUPS); g_cmf["Breadth"]=list(SIGNAL_GROUPS["Breadth"])+["CMF Positive"]
    g_wpivot= dict(SIGNAL_GROUPS); g_wpivot["Position"]=list(SIGNAL_GROUPS["Position"])+["Above Weekly Pivot"]

    rows_base,rows_cmf,rows_wp=[],[],[]
    cmf_fires=wp_fires=0

    for i in range(eval_start,n-1):
        cutoff=spx.index[i]
        spx_sl =spx.iloc[:i+1]; vix_sl=vix[vix.index<=cutoff]
        vvix_sl=vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        sec_sl ={t:df[df.index<=cutoff] for t,df in sectors.items()}

        sigs=_compute_core(spx_sl,vix_sl,sec_sl,vvix_sl)
        if not sigs: continue

        nxt=float(close.iloc[i+1]); cur=float(close.iloc[i])
        up=nxt>cur+5; dn=nxt<cur-5
        if not up and not dn: continue
        day_gap=_sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        if sigs.get("CMF Positive",0)==1: cmf_fires+=1
        if sigs.get("Above Weekly Pivot",0)==1: wp_fires+=1

        score_b =_grp_score(sigs,SIGNAL_GROUPS)
        score_c =_grp_score(sigs,g_cmf)
        score_wp=_grp_score(sigs,g_wpivot)

        for sc,bucket in [(score_b,rows_base),(score_c,rows_cmf),(score_wp,rows_wp)]:
            bull=sc>=55; bear=sc<=44
            if not bull and not bear: continue
            if day_gap<-GAP_THRESHOLD and bear: continue
            correct=(bull and up)or(bear and dn)
            bucket.append({"correct":correct})

    def acc(r):
        if not r: return 0,0,0.0
        h=sum(x["correct"] for x in r); t=len(r)
        return h,t,round(h/t*100,1)

    bh,bt,ba=acc(rows_base); ch,ct,ca=acc(rows_cmf); wh,wt,wa=acc(rows_wp)
    n_eval=bt
    print(f"\n{'─'*65}")
    print(f"  Baseline:                           {bh}/{bt} = {ba}%")
    print(f"  +CMF Positive (Breadth):            {ch}/{ct} = {ca}%  Δ={ca-ba:+.1f}pp")
    print(f"  +Above Weekly Pivot (Position):     {wh}/{wt} = {wa}%  Δ={wa-ba:+.1f}pp")
    print(f"{'─'*65}")
    print(f"  CMF fires: {cmf_fires}/{n_eval}  Weekly Pivot fires: {wp_fires}/{n_eval}  (directional call bars)")


if __name__ == "__main__":
    run_probe()
    print("\n[cmf] done.")
