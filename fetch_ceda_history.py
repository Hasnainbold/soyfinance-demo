"""
fetch_ceda_history.py -- daily all-India soybean prices from CEDA Ashoka, 2020 -> 2025-10.

CEDA (Centre for Economic Data & Analysis, Ashoka University) republishes cleaned
Agmarknet data. Verified coverage as of 2026-09-05:

    2020-01-01 .. 2025-10-30   available
    2025-11-01 onward          empty -- CEDA's upstream died when Agmarknet
                               migrated to 2.0 (~2025-11-07), same as ours

So this fills the one gap we can fill without anyone's credentials: **2020**, which no
file in this project covered at daily resolution. It also re-derives 2021-2025 from an
independent source, so we can cross-check the mandi CSVs we already trust.

    python fetch_ceda_history.py                       # 2020-01-01 -> 2025-10-30
    python fetch_ceda_history.py --from 2020-01-01 --to 2020-12-31
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://agmarknet.ceda.ashoka.edu.in/api/prices"
RAW_DIR = os.path.join(_DIR, "ceda_raw")
OUT_CSV = os.path.join(_DIR, "soybean_ceda_daily_2020_2025.csv")

SOYBEAN_COMMODITY_ID = 13
HEADERS = {"User-Agent": "Mozilla/5.0",
           "Referer": "https://agmarknet.ceda.ashoka.edu.in/",
           "Content-Type": "application/json"}

CEDA_LAST_GOOD = "2025-10-30"          # measured, not assumed


def fetch_month(y: int, m: int, *, retries: int = 3) -> list:
    a = f"{y}-{m:02d}-01"
    b = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]}"
    payload = {"state_id": 0, "commodity_id": SOYBEAN_COMMODITY_ID, "district_id": 0,
               "calculation_type": "d", "start_date": a, "end_date": b}
    for k in range(retries):
        try:
            r = requests.post(API, json=payload, headers=HEADERS, timeout=(8, 90))
            if r.status_code == 200:
                return r.json().get("data", []) or []
            if k == retries - 1:
                print(f"    HTTP {r.status_code}")
        except Exception as e:
            if k == retries - 1:
                print(f"    {type(e).__name__}")
        time.sleep(2.5 * (k + 1))
    return []


def _months(a: date, b: date):
    cur = date(a.year, a.month, 1)
    while cur <= b:
        yield cur.year, cur.month
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        cur = last + timedelta(days=1)


def run(frm: str, to: str) -> pd.DataFrame:
    os.makedirs(RAW_DIR, exist_ok=True)
    d0, d1 = date.fromisoformat(frm), date.fromisoformat(to)
    frames = []
    for y, m in _months(d0, d1):
        cache = os.path.join(RAW_DIR, f"soybean_{y}-{m:02d}.json")
        if os.path.exists(cache):
            rows = json.load(open(cache, encoding="utf-8"))
            src = "cached"
        else:
            rows = fetch_month(y, m)
            json.dump(rows, open(cache, "w", encoding="utf-8"))
            src = "fetched"
            time.sleep(1.0)
        print(f"  {y}-{m:02d}  {len(rows):>3} rows ({src})", flush=True)
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    d = d.rename(columns={"t": "date", "p_modal": "modal_price",
                          "p_min": "min_price", "p_max": "max_price", "cmdty": "commodity"})
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for c in ("modal_price", "min_price", "max_price"):
        d[c] = pd.to_numeric(d[c], errors="coerce").round(1)
    d["source"] = "ceda"
    return (d.dropna(subset=["date", "modal_price"])
            .drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2020-01-01")
    ap.add_argument("--to", dest="to", default=CEDA_LAST_GOOD)
    a = ap.parse_args()

    print(f"CEDA soybean daily, {a.frm} -> {a.to}")
    df = run(a.frm, a.to)
    if df.empty:
        raise SystemExit("nothing downloaded")
    df.to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV}  {len(df):,} days  "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")

    from data_integrity import audit_price_series
    ser = df.rename(columns={"modal_price": "price"})[["date", "price"]]
    print()
    print(audit_price_series(ser, today=a.to).summary())
