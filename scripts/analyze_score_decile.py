#!/usr/bin/env python3
"""
Score-decile calibration check for spx-algo Core SSR.

Independent of the live 55/44 bull/bear thresholds: buckets EVERY bar's raw group-average
score (not just threshold-crossing ones) into deciles and reports the actual forward-up rate
per bucket. Tests whether the score has any monotonic relationship to next-day direction at
all, on a given historical window -- the question the OOS-REVIEW-01 signal/gate ablation
couldn't answer (ablation only tests removing one signal at a time from an already-thresholded
call; it says nothing about whether 55/44 are the right cut points, or whether any cut point
would work).

Usage:
    python3 scripts/analyze_score_decile.py --start 2017-01-01 --end 2023-12-31
    python3 scripts/analyze_score_decile.py --period 10y
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_ablation as ra  # noqa: E402
import pandas as pd  # noqa: E402


def analyze(period: str = "2y", start: str | None = None, end: str | None = None) -> None:
    _fetch_kw = {"start": start, "end": end} if start else {"period": period}
    label = f"{start}→{end or 'now'}" if start else period
    print(f"[decile] Fetching {label} …")
    spx = ra.yf.download("^GSPC", interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
    vix = ra.yf.download("^VIX",  interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
    try:
        vvix_close = ra._squeeze(ra.yf.download("^VVIX", interval="1d", progress=False,
                                                 auto_adjust=True, **_fetch_kw), "Close")
    except Exception:
        vvix_close = pd.Series(dtype=float)
    try:
        vix9d_close = ra._squeeze(ra.yf.download("^VIX9D", interval="1d", progress=False,
                                                   auto_adjust=True, **_fetch_kw), "Close")
    except Exception:
        vix9d_close = pd.Series(dtype=float)
    sec = {}
    for t in ra.SECTOR_TICKERS:
        try:
            sec[t] = ra.yf.download(t, interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
        except Exception:
            sec[t] = pd.DataFrame()

    close = ra._squeeze(spx, "Close")
    openp = ra._squeeze(spx, "Open")
    n = len(spx)
    atr_v = ra._atr_series(spx)

    def label_threshold(idx: int) -> float:
        try:
            a = float(atr_v.iloc[idx])
            if a == a:
                return max(ra.LABEL_THRESHOLD_FLOOR, a * ra.LABEL_ATR_FRACTION)
        except Exception:
            pass
        return ra.LABEL_THRESHOLD_FLOOR

    rows = []
    for i in range(200, n - 1):
        try:
            spx_sl = spx.iloc[:i + 1]
            cutoff = pd.Timestamp(spx.index[i].date())
            vix_sl = vix[vix.index <= cutoff]
            sec_sl = {k: v[v.index <= cutoff] for k, v in sec.items() if not v.empty}
            vvix_sl = vvix_close[vvix_close.index <= cutoff] if not vvix_close.empty else pd.Series(dtype=float)
            vix9d_sl = vix9d_close[vix9d_close.index <= cutoff] if not vix9d_close.empty else pd.Series(dtype=float)
            sigs = ra._compute_signals(spx_sl, vix_sl, sec_sl, vvix_sl, vix9d_sl, as_of_date=cutoff)
            score = ra._grp_score(sigs)
            nxt = float(close.iloc[i + 1])
            cur = float(close.iloc[i])
            lt = label_threshold(i)
            if nxt > cur + lt:
                direction = 1
            elif nxt < cur - lt:
                direction = 0
            else:
                continue  # flat day -- no directional label, excluded same as ablation
            rows.append((score, direction))
        except Exception:
            continue

    df = pd.DataFrame(rows, columns=["score", "up"])
    print(f"[decile] {len(df)} labeled bars, window {label}")
    print(f"[decile] Overall up-rate: {df['up'].mean():.1%}  (baseline if score carried no information)")
    print()

    df["decile"] = pd.qcut(df["score"], 10, duplicates="drop")
    grp = df.groupby("decile", observed=True)["up"].agg(["mean", "count"])
    print(f"{'Score band':<20} {'Up rate':>10} {'n':>6}")
    for band, row in grp.iterrows():
        print(f"{str(band):<20} {row['mean']:>9.1%} {int(row['count']):>6}")

    corr = df["score"].corr(df["up"])
    print(f"\n[decile] Pearson correlation(score, up): {corr:.4f}")

    # Simple monotonicity check: is up-rate increasing across deciles?
    up_rates = grp["mean"].tolist()
    increases = sum(1 for a, b in zip(up_rates, up_rates[1:]) if b >= a)
    print(f"[decile] Monotonic increases across {len(up_rates)-1} decile transitions: "
          f"{increases}/{len(up_rates)-1}")

    # Raw 55/44 threshold accuracy (no abstain gates) -- isolates whether the base-rate
    # mismatch + near-zero correlation combination explains the poor ablation-measured accuracy.
    base_up = df["up"].mean()
    bull = df[df["score"] >= 55]
    bear = df[df["score"] <= 44]
    neutral_n = len(df) - len(bull) - len(bear)
    bull_acc = bull["up"].mean() if len(bull) else float("nan")
    bear_acc = (1 - bear["up"].mean()) if len(bear) else float("nan")
    combined_hits = bull["up"].sum() + (len(bear) - bear["up"].sum())
    combined_n = len(bull) + len(bear)
    print(f"\n[decile] Base up-rate (whole window): {base_up:.1%}")
    print(f"[decile] Bull calls (score>=55): n={len(bull)}  accuracy={bull_acc:.1%}")
    print(f"[decile] Bear calls (score<=44): n={len(bear)}  accuracy={bear_acc:.1%}")
    print(f"[decile] Neutral (45-54, no call): n={neutral_n}")
    print(f"[decile] Combined raw-threshold accuracy (no abstain gates): "
          f"{combined_hits/combined_n:.1%} ({int(combined_hits)}/{combined_n})")
    print(f"[decile] Call-rate split: {len(bull)/combined_n:.1%} bull / {len(bear)/combined_n:.1%} bear")


def main() -> None:
    ap = argparse.ArgumentParser(description="Score-decile calibration check")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    analyze(period=args.period, start=args.start, end=args.end)


if __name__ == "__main__":
    main()
