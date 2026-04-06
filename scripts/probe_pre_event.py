#!/usr/bin/env python3
"""
Probe: Pre-event day abstain gate.

Hypothesis: On days immediately preceding a HIGH-impact economic event
(FOMC, CPI, NFP, PCE), traders square positions and reduce directional exposure.
This creates choppy, mean-reverting behavior that makes the SSR model less reliable.

Test: Does suppressing bear calls (and optionally bull calls) on pre-event days
improve accuracy? This is analogous to the gap-down abstain gate logic.

Strategy variants:
  A — abstain bear calls on pre-event days only
  B — abstain ALL calls (bull + bear) on pre-event days
  C — abstain only when pre-event AND Thursday (targeting the known Thursday weakness)
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

GAP_THRESHOLD = 25.0

# Same event set as run_ablation.py (including PCE)
_ECON_DATES: set[str] = {
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18","2025-07-30","2025-09-17","2025-11-07","2025-12-17",
    "2025-01-15","2025-02-12","2025-03-12","2025-04-10","2025-05-13","2025-06-11","2025-07-15","2025-08-12",
    "2025-09-10","2025-10-15","2025-11-13","2025-12-10",
    "2025-01-10","2025-02-07","2025-03-07","2025-04-04","2025-05-02","2025-06-06","2025-07-03","2025-08-01",
    "2025-09-05","2025-10-03","2025-11-07","2025-12-05",
    "2025-01-31","2025-02-28","2025-03-28","2025-04-25","2025-05-30","2025-06-27","2025-07-25","2025-08-29",
    "2025-09-26","2025-10-31","2025-11-26","2025-12-19",
    "2026-01-28","2026-03-18","2026-04-29","2026-06-10","2026-07-29","2026-09-16","2026-11-04","2026-12-16",
    "2026-01-14","2026-02-11","2026-03-11","2026-04-10","2026-05-13","2026-06-10","2026-07-15","2026-08-12",
    "2026-09-10","2026-10-14","2026-11-12","2026-12-10",
    "2026-01-09","2026-02-06","2026-03-06","2026-04-03","2026-05-01","2026-06-05","2026-07-10","2026-08-07",
    "2026-09-04","2026-10-02","2026-11-06","2026-12-04",
    "2026-01-30","2026-02-27","2026-03-27","2026-04-24","2026-05-29","2026-06-26",
}

def _next_trading_day_is_event(dt: pd.Timestamp) -> bool:
    """Check if the next calendar day (or first non-weekend day) is an event."""
    d = dt.date()
    # Check next 3 calendar days (handles weekends)
    for offset in range(1, 4):
        nd = d + timedelta(days=offset)
        if nd.weekday() < 5:  # weekday (Mon-Fri)
            return nd.strftime("%Y-%m-%d") in _ECON_DATES
    return False

SIGNAL_GROUPS = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA"],
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "MACD Bullish", "RSI Strong Trend"],
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting", "VIX Below 15", "VIX 1d Down", "VVIX Below 100"],
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
    ln=e12-e26; return ln,ln.ewm(span=9,adjust=False).mean()
def _atr(df, p=14):
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close"); pc=c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1).rolling(p).mean()

def _grp_score(sigs):
    ws = []
    for _, gs in SIGNAL_GROUPS.items():
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
        try: sigs["VVIX Below 100"]=int(float(vvix_sl.iloc[-1])<100)
        except: pass
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
            except: continue
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
    return sigs


def run_probe():
    print("[pre_event] Fetching 2y data …")
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

    rows_base,rows_a,rows_b,rows_c=[],[],[],[]
    pre_event_count=0
    thu_pre_event=0

    for i in range(eval_start,n-1):
        cutoff=spx.index[i]
        spx_sl=spx.iloc[:i+1]; vix_sl=vix[vix.index<=cutoff]
        vvix_sl=vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        sec_sl={t:df[df.index<=cutoff] for t,df in sectors.items()}

        sigs=_compute_core(spx_sl,vix_sl,sec_sl,vvix_sl)
        if not sigs: continue

        nxt=float(close.iloc[i+1]); cur=float(close.iloc[i])
        up=nxt>cur+5; dn=nxt<cur-5
        if not up and not dn: continue
        day_gap=_sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        score=_grp_score(sigs)
        bull=score>=55; bear=score<=44
        if not bull and not bear: continue
        if day_gap<-GAP_THRESHOLD and bear: continue  # existing gap-down gate

        is_pre_event = _next_trading_day_is_event(cutoff)
        is_thu = cutoff.weekday() == 3  # Thursday
        if is_pre_event: pre_event_count += 1
        if is_pre_event and is_thu: thu_pre_event += 1

        correct=(bull and up)or(bear and dn)

        # Baseline: no pre-event gate
        rows_base.append({"correct":correct,"pre":is_pre_event,"thu":is_thu})

        # Variant A: abstain bear calls on pre-event days
        if is_pre_event and bear:
            pass  # abstain
        else:
            rows_a.append({"correct":correct})

        # Variant B: abstain ALL calls on pre-event days
        if is_pre_event:
            pass  # abstain
        else:
            rows_b.append({"correct":correct})

        # Variant C: abstain ALL calls on Thursday+pre-event only
        if is_pre_event and is_thu:
            pass  # abstain
        else:
            rows_c.append({"correct":correct})

    def acc(rows):
        if not rows: return 0,0,0.0
        h=sum(r["correct"] for r in rows); t=len(rows)
        return h,t,round(h/t*100,1)

    bh,bt,ba=acc(rows_base)
    ah,at,aa=acc(rows_a); bxh,bxt,bxa=acc(rows_b); ch,ct,ca=acc(rows_c)

    # Accuracy on pre-event days specifically
    pre_rows=[r for r in rows_base if r["pre"]]
    non_pre=[r for r in rows_base if not r["pre"]]
    thu_rows=[r for r in rows_base if r["thu"]]
    thu_pre=[r for r in rows_base if r["thu"] and r["pre"]]
    ph,pt,pa=acc(pre_rows); nh,nt,na=acc(non_pre)
    th,tt,ta=acc(thu_rows); tph,tpt,tpa=acc(thu_pre)

    print(f"\n{'─'*70}")
    print(f"  Pre-event days (tomorrow is HIGH event): {pre_event_count} directional calls")
    print(f"  Thu+pre-event:                           {thu_pre_event} calls")
    print(f"{'─'*70}")
    print(f"  Baseline (no gate):                   {bh}/{bt} = {ba}%")
    print(f"  Pre-event accuracy:                   {ph}/{pt} = {pa}%   (vs {na}% on normal days)")
    print(f"  Thursday accuracy:                    {th}/{tt} = {ta}%")
    print(f"  Thursday+pre-event accuracy:          {tph}/{tpt} = {tpa}%")
    print(f"{'─'*70}")
    print(f"  Variant A (abstain bear on pre-event):{ah}/{at} = {aa}%  Δ={aa-ba:+.1f}pp  coverage: {round(at/bt*100,1)}%")
    print(f"  Variant B (abstain ALL on pre-event): {bxh}/{bxt} = {bxa}%  Δ={bxa-ba:+.1f}pp  coverage: {round(bxt/bt*100,1)}%")
    print(f"  Variant C (abstain ALL Thu+pre-event):{ch}/{ct} = {ca}%  Δ={ca-ba:+.1f}pp  coverage: {round(ct/bt*100,1)}%")
    print(f"{'─'*70}")


if __name__ == "__main__":
    run_probe()
    print("\n[pre_event] done.")
