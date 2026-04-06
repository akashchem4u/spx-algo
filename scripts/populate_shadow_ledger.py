#!/usr/bin/env python3
"""
Populate (or update) the shadow performance ledger.

Backfills core_ssr and actual outcome for recent sessions using the same
_compute_signals_fast / _grp_score pipeline as backtest_export.py.  Live-only
signals (live_adj_ssr, orb_status) are recorded as "n/a" for historical rows
since they cannot be reconstructed without intraday feeds.

Usage:
    python3 scripts/populate_shadow_ledger.py              # last 60 sessions
    python3 scripts/populate_shadow_ledger.py --days 90   # last 90 sessions
    python3 scripts/populate_shadow_ledger.py --append     # only new rows

Output: Codex/shadow-ledger.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import yfinance as yf
    import pandas as pd
except ImportError as exc:
    print(f"import error: {exc}")
    sys.exit(2)

from scripts.backtest_export import (
    _compute_signals_fast,
    _grp_score,
    _squeeze,
    _safe_float,
    SECTOR_TICKERS,
    GAP_THRESHOLD,
    VIX_FEAR_THRESHOLD,
    VIX_CALM_THRESHOLD,
    _history_period_for_days,
)

LEDGER_PATH = ROOT / "Codex" / "shadow-ledger.csv"
FIELDNAMES = [
    "date", "core_ssr", "live_adj_ssr",
    "vix", "vix_regime", "gap_pts", "gap_regime",
    "dow", "event_flags", "opex", "orb_status",
    "actual_dir", "actual_pts",
]

# Known FOMC, CPI, NFP dates (extend as needed).
# Format: "YYYY-MM-DD"
_EVENT_DATES: dict[str, list[str]] = {
    "FOMC": [
        "2025-03-19","2025-05-07","2025-06-18","2025-07-30",
        "2025-09-17","2025-11-05","2025-12-17",
        "2026-01-29","2026-03-19","2026-05-06",
    ],
    "CPI": [
        "2025-03-12","2025-04-10","2025-05-13","2025-06-11",
        "2025-07-11","2025-08-12","2025-09-10","2025-10-10",
        "2025-11-13","2025-12-11",
        "2026-01-15","2026-02-12","2026-03-12","2026-04-10",
    ],
    "NFP": [
        "2025-03-07","2025-04-04","2025-05-02","2025-06-06",
        "2025-07-03","2025-08-01","2025-09-05","2025-10-03",
        "2025-11-07","2025-12-05",
        "2026-01-10","2026-02-07","2026-03-07","2026-04-03",
    ],
}

# Build lookup: date string → comma-joined event types
_EVENT_LOOKUP: dict[str, str] = {}
for _etype, _dates in _EVENT_DATES.items():
    for _d in _dates:
        _EVENT_LOOKUP[_d] = ",".join(filter(None, [_EVENT_LOOKUP.get(_d, ""), _etype]))


def _is_opex_week(d: date) -> bool:
    """True if d falls in the third-Friday options expiration week."""
    # Find the third Friday of d's month
    first_day = d.replace(day=1)
    first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
    third_friday = first_friday + timedelta(weeks=2)
    week_start = third_friday - timedelta(days=third_friday.weekday())
    week_end   = week_start + timedelta(days=4)
    return week_start <= d <= week_end


def _load_data(days: int):
    period = _history_period_for_days(days + 5)  # extra buffer
    spx = yf.download("^GSPC", period=period, interval="1d", progress=False, auto_adjust=True)
    vix = yf.download("^VIX",  period=period, interval="1d", progress=False, auto_adjust=True)
    sectors = {}
    for ticker in SECTOR_TICKERS:
        try:
            sectors[ticker] = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[ticker] = pd.DataFrame()
    return spx, vix, sectors


def _read_existing() -> set[str]:
    if not LEDGER_PATH.exists():
        return set()
    with open(LEDGER_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["date"] for r in rows}


def _build_rows(spx, vix, sectors, days: int) -> list[dict]:
    close  = _squeeze(spx, "Close")
    opens  = _squeeze(spx, "Open")
    n      = len(spx)
    eval_start = max(252, n - days - 1)
    rows = []

    _DOW = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

    for i in range(eval_start, n - 1):
        cutoff_ts = spx.index[i]
        spx_slice = spx.iloc[: i + 1]
        vix_slice = vix[vix.index <= cutoff_ts]
        sec_slice = {t: df[df.index <= cutoff_ts] for t, df in sectors.items()}

        sigs = _compute_signals_fast(spx_slice, vix_slice, sec_slice)
        if not sigs:
            continue

        score = _grp_score(sigs)

        vix_val   = _safe_float(_squeeze(vix_slice, "Close"), default=20.0) if not vix_slice.empty else 20.0
        vix_reg   = "high" if vix_val > VIX_FEAR_THRESHOLD else ("low" if vix_val < VIX_CALM_THRESHOLD else "mid")
        gap_pts   = round(_safe_float(opens, i) - _safe_float(close, i - 1), 1) if i > 0 else 0.0
        gap_reg   = "up" if gap_pts > GAP_THRESHOLD else ("down" if gap_pts < -GAP_THRESHOLD else "flat")

        nxt = float(close.iloc[i + 1])
        cur = float(close.iloc[i])
        move = round(nxt - cur, 1)
        actual_dir = "up" if move > 5 else ("down" if move < -5 else "flat")

        day_date  = spx.index[i].date()
        date_str  = day_date.strftime("%Y-%m-%d")
        dow_name  = _DOW.get(spx.index[i].weekday(), "?")
        events    = _EVENT_LOOKUP.get(date_str, "")
        opex      = "yes" if _is_opex_week(day_date) else "no"

        rows.append({
            "date":         date_str,
            "core_ssr":     score,
            "live_adj_ssr": "n/a",   # requires real-time feeds; not reconstructable
            "vix":          round(vix_val, 1),
            "vix_regime":   vix_reg,
            "gap_pts":      gap_pts,
            "gap_regime":   gap_reg,
            "dow":          dow_name,
            "event_flags":  events,
            "opex":         opex,
            "orb_status":   "n/a",   # intraday; not reconstructable
            "actual_dir":   actual_dir,
            "actual_pts":   move,
        })

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--append", action="store_true",
                        help="only write rows not already in the ledger")
    args = parser.parse_args()

    print(f"Fetching data for last {args.days} sessions...")
    spx, vix, sectors = _load_data(args.days)

    existing_dates = _read_existing() if args.append else set()
    rows = _build_rows(spx, vix, sectors, args.days)

    new_rows = [r for r in rows if r["date"] not in existing_dates]
    if not new_rows:
        print("No new rows to write.")
        return

    write_header = not LEDGER_PATH.exists() or LEDGER_PATH.stat().st_size == 0 or not args.append
    mode = "a" if (args.append and LEDGER_PATH.exists()) else "w"

    with open(LEDGER_PATH, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"Wrote {len(new_rows)} rows to {LEDGER_PATH}")

    # Quick accuracy summary — matches backtest methodology:
    # only count calls where actual_dir is directional (not flat) so the
    # denominator matches what the validation gate measures.
    scored_dir = [
        r for r in new_rows
        if (r["core_ssr"] >= 55 or r["core_ssr"] <= 44) and r["actual_dir"] != "flat"
    ]
    correct = sum(
        1 for r in scored_dir
        if (r["core_ssr"] >= 55 and r["actual_dir"] == "up")
        or (r["core_ssr"] <= 44 and r["actual_dir"] == "down")
    )
    if scored_dir:
        pct = round(correct / len(scored_dir) * 100, 1)
        print(f"Core SSR directional accuracy (new rows): {correct}/{len(scored_dir)} = {pct}%")


if __name__ == "__main__":
    main()
