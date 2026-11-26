"""
fetch_agmarknet_trend.py -- real Agmarknet prices for the 2025-11 -> now gap. No login needed.

How this was found
------------------
Agmarknet 2.0's headline report (`/daily-price-arrival/report`) is CAPTCHA-gated, and its
"Official Login" has no public registration -- accounts are issued to market officials. Dead end.

But the Price Trend reports run on a *different*, completely open API family:

    GET /v1/price-trend/wholesale-prices-weekly
        ?report_mode=Statewise&commodity=13&year=2026&month=3&week=1

No token, no captcha, no account. Two gotchas cost an hour, so they are documented here:

  1. `report_mode` is CASE-SENSITIVE: `Statewise` / `Districtwise` / `Marketwise`.
     Lower-case `statewise` returns 400 "Invalid report_mode".
  2. The MONTHLY sibling (`wholesale-prices-monthly`) is broken server-side -- 500 for every
     input we tried, across all years. Only the WEEKLY endpoint returns data.
  3. Pass `state` only for Districtwise/Marketwise. Sending it with Statewise flips the
     server to "Invalid report_mode".

Each call returns four price columns -- the target week, the previous week, the same week
last month, and the same week last year -- so one request yields several dated observations.
We harvest all of them and de-duplicate, which cuts the request count sharply.

    python fetch_agmarknet_trend.py                          # 2025-11-01 -> today
    python fetch_agmarknet_trend.py --from 2021-01 --to 2026-09
    python fetch_agmarknet_trend.py --mode Districtwise --state 19    # MP, finer grain
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import time
from datetime import date, datetime

import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://api.agmarknet.gov.in/v1/price-trend/wholesale-prices-weekly"
RAW_DIR = os.path.join(_DIR, "agmarknet_trend_raw")
OUT_WEEKLY = os.path.join(_DIR, "soybean_agmarknet_weekly.csv")
OUT_STATES = os.path.join(_DIR, "soybean_agmarknet_weekly_states.csv")

SOYBEAN = 13
VALID_MODES = ("Statewise", "Districtwise", "Marketwise")   # capital first letter -- required
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/",
    "Accept": "application/json",
}

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
# column keys look like  prices_1_8_mar_2026  /  prices_24_31_oct_2025
_COL_RE = re.compile(r"^prices_(\d{1,2})_(\d{1,2})_([a-z]{3})_(\d{4})$")


def parse_col(key: str) -> tuple | None:
    """'prices_1_8_mar_2026' -> (start_date, end_date, midpoint_date). None if not a price col."""
    m = _COL_RE.match(key)
    if not m:
        return None
    d1, d2, mon, yr = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
    mi = _MONTHS.get(mon)
    if not mi:
        return None
    last = calendar.monthrange(yr, mi)[1]
    d1, d2 = min(d1, last), min(d2, last)
    a, b = date(yr, mi, d1), date(yr, mi, d2)
    mid = a + (b - a) / 2
    return a, b, mid


def fetch_week(year: int, month: int, week: int, *, mode="Statewise",
               state: int | None = None, retries: int = 3) -> dict | None:
    params = {"report_mode": mode, "commodity": SOYBEAN,
              "year": year, "month": month, "week": week}
    if state is not None and mode != "Statewise":
        params["state"] = state
    for k in range(retries):
        try:
            r = requests.get(API, headers=HEADERS, params=params, timeout=(8, 70))
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                return None                      # week genuinely doesn't exist
            if k == retries - 1:
                print(f"      HTTP {r.status_code} {r.text[:90]}", file=sys.stderr)
        except Exception as e:
            if k == retries - 1:
                print(f"      {type(e).__name__}", file=sys.stderr)
        time.sleep(2.0 * (k + 1))
    return None


def harvest(payload: dict) -> tuple:
    """
    Pull every dated price out of one response.

    Returns (national_rows, state_rows). `average` is Agmarknet's cross-region average --
    the closest thing to a national figure this endpoint offers.
    """
    nat, per = [], []
    avg = payload.get("average") or {}
    for k, v in avg.items():
        parsed = parse_col(k)
        if parsed and v is not None:
            a, b, mid = parsed
            nat.append({"date": pd.Timestamp(mid), "period_start": a, "period_end": b,
                        "price": float(v), "column": k})
    for row in payload.get("rows") or []:
        region = row.get("state") or row.get("district") or row.get("market")
        for k, v in row.items():
            parsed = parse_col(k)
            if parsed and v is not None:
                a, b, mid = parsed
                per.append({"date": pd.Timestamp(mid), "period_start": a, "period_end": b,
                            "region": region, "price": float(v), "column": k})
    return nat, per


def _month_span(frm: str, to: str):
    y0, m0 = (int(x) for x in frm.split("-")[:2])
    y1, m1 = (int(x) for x in to.split("-")[:2])
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def run(frm: str, to: str, mode: str, state: int | None) -> tuple:
    os.makedirs(RAW_DIR, exist_ok=True)
    nat_all, per_all = [], []
    for y, m in _month_span(frm, to):
        got = 0
        for wk in (1, 2, 3, 4):
            tag = f"{mode}_{state or 'all'}_{y}-{m:02d}_w{wk}"
            cache = os.path.join(RAW_DIR, f"{tag}.json")
            if os.path.exists(cache):
                payload = json.load(open(cache, encoding="utf-8"))
            else:
                payload = fetch_week(y, m, wk, mode=mode, state=state)
                if payload:
                    json.dump(payload, open(cache, "w", encoding="utf-8"))
                time.sleep(0.7)
            if not payload:
                continue
            n, p = harvest(payload)
            nat_all += n
            per_all += p
            got += len(n)
        print(f"  {y}-{m:02d}  {got:>2} dated points", flush=True)

    nat = pd.DataFrame(nat_all)
    if not nat.empty:
        nat = (nat.sort_values("date").drop_duplicates(subset=["date"], keep="last")
               .reset_index(drop=True))
    per = pd.DataFrame(per_all)
    if not per.empty:
        per = (per.sort_values("date")
               .drop_duplicates(subset=["date", "region"], keep="last").reset_index(drop=True))
    return nat, per


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2025-11")
    ap.add_argument("--to", dest="to", default=date.today().strftime("%Y-%m"))
    ap.add_argument("--mode", default="Statewise", choices=VALID_MODES)
    ap.add_argument("--state", type=int, default=None)
    a = ap.parse_args()

    print(f"Agmarknet price-trend weekly | soybean | {a.mode} | {a.frm} -> {a.to}")
    nat, per = run(a.frm, a.to, a.mode, a.state)
    if nat.empty:
        raise SystemExit("nothing fetched")

    nat.to_csv(OUT_WEEKLY, index=False)
    print(f"\nwrote {OUT_WEEKLY}  {len(nat)} dated points  "
          f"{nat['date'].min().date()} -> {nat['date'].max().date()}")
    if not per.empty:
        per.to_csv(OUT_STATES, index=False)
        print(f"wrote {OUT_STATES}  {len(per)} region-points, "
              f"{per['region'].nunique()} regions")

    print("\nprice path (Rs./quintal):")
    for _, r in nat.iterrows():
        print(f"   {r['period_start']} .. {r['period_end']}   {r['price']:>8,.0f}")

    from data_integrity import audit_price_series
    print()
    print(audit_price_series(nat[["date", "price"]], max_frozen_run=3).summary())
