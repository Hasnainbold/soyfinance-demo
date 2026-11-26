"""
fetch_china_soy.py — Chinese soy-complex futures (DCE/CZCE) via Sina, cached to CSV.

Why this exists
---------------
China crushes ~90 MMT of soybeans a year and is the marginal buyer for the world.
Its soybean-meal (豆粕 / soybean cake, "DOC") price is the demand signal that pulls
beans out of Brazil and the US — and India's mandi price sits downstream of the same
complex. Tracking DCE meal alongside the Indian mandi price is how we test whether
Chinese crush demand leads Indian soybean prices, and by how many days.

Source: Sina Finance's inner futures service (the same endpoint akshare wraps).
Continuous ("main") contracts, daily bars back to ~2005.

    python fetch_china_soy.py            # fetch all, write china_soy_futures.csv
    python fetch_china_soy.py --since 2020-01-01
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(_DIR, "china_soy_futures.csv")
RAW_DIR = os.path.join(_DIR, "china_cache")

_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        "var%20_{sym}=/InnerFuturesNewService.getDailyKLine?symbol={sym}")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

# symbol -> (exchange, english name, chinese name, unit)
CONTRACTS = {
    "M0":  ("DCE",   "soybean_meal",   "豆粕",       "CNY/t"),   # ← soybean cake / DOC
    "Y0":  ("DCE",   "soybean_oil",    "豆油",       "CNY/t"),
    "A0":  ("DCE",   "soybean_no1",    "黄大豆一号", "CNY/t"),   # domestic food-grade bean
    "B0":  ("DCE",   "soybean_no2",    "黄大豆二号", "CNY/t"),   # imported crush-grade bean
    "P0":  ("DCE",   "palm_oil",       "棕榈油",     "CNY/t"),   # substitute veg oil
    "RM0": ("CZCE",  "rapeseed_meal",  "菜粕",       "CNY/t"),   # substitute protein meal
    "C0":  ("DCE",   "corn",           "玉米",       "CNY/t"),   # competing feed grain
}


def fetch_symbol(sym: str, *, retries: int = 3) -> pd.DataFrame:
    """Daily OHLCV + open interest for one continuous contract."""
    url = _URL.format(sym=sym)
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=(5, 30))
            r.raise_for_status()
            m = re.search(r"=\(?(\[.*\])\)?;?\s*$", r.text.strip())
            if not m:
                raise ValueError("unexpected payload shape")
            recs = json.loads(m.group(1))
            os.makedirs(RAW_DIR, exist_ok=True)
            with open(os.path.join(RAW_DIR, f"{sym}.json"), "w") as fh:
                json.dump(recs, fh)
            d = pd.DataFrame(recs).rename(columns={
                "d": "date", "o": "open", "h": "high", "l": "low",
                "c": "close", "v": "volume", "p": "open_interest", "s": "settle"})
            d["date"] = pd.to_datetime(d["date"], errors="coerce")
            for c in ("open", "high", "low", "close", "volume", "open_interest", "settle"):
                if c in d:
                    d[c] = pd.to_numeric(d[c], errors="coerce")
            return d.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        except Exception as e:                                  # transient network / shape
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{sym}: {last}")


def fetch_all(since: str | None = None) -> pd.DataFrame:
    """Long-format frame: date, symbol, name, exchange, OHLCV, open_interest."""
    out = []
    for sym, (exch, name, cn, unit) in CONTRACTS.items():
        try:
            d = fetch_symbol(sym)
        except Exception as e:
            print(f"  {sym:4s} {name:15s} FAILED  {e}", file=sys.stderr)
            continue
        d.insert(1, "symbol", sym)
        d.insert(2, "name", name)
        d.insert(3, "exchange", exch)
        d.insert(4, "cn_name", cn)
        d.insert(5, "unit", unit)
        if since:
            d = d[d["date"] >= pd.Timestamp(since)]
        print(f"  {sym:4s} {name:15s} {len(d):>6,} rows  "
              f"{d['date'].min().date()} -> {d['date'].max().date()}  "
              f"last {d['close'].iloc[-1]:,.0f} {unit}")
        out.append(d)
    if not out:
        raise RuntimeError("every symbol failed — check the network")
    return pd.concat(out, ignore_index=True)


def wide(df: pd.DataFrame) -> pd.DataFrame:
    """One row per date, one close-price column per contract."""
    w = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    w.columns = [f"CN_{c}_CNY" for c in w.columns]
    return w.sort_index().reset_index()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2020-01-01")
    a = ap.parse_args()

    print(f"Fetching Chinese soy complex from Sina (since {a.since})...")
    long_df = fetch_all(a.since)
    long_df.to_csv(OUT_CSV, index=False)
    w = wide(long_df)
    w.to_csv(os.path.join(_DIR, "china_soy_wide.csv"), index=False)
    print(f"\nwrote {OUT_CSV}  ({len(long_df):,} rows)")
    print(f"wrote china_soy_wide.csv  ({len(w):,} dates × {len(w.columns)-1} series)")
