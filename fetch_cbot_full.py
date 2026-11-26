"""
fetch_cbot_full.py -- CBOT soybean complex from 2019-12, matching China/India/FX coverage.

The earlier global_commodities_5yr.csv only started 2021-08-27. yfinance actually holds
CBOT futures back to 2000, so there was no real reason for that gap -- just how far the
original fetch happened to go. This re-pulls the same four tickers over the full window so
all four markets (India, China, US, and FX) line up from 2019-12/2020-01 onward.

Tickers, all continuous front-month futures:
    ZS=F  Soybean        cents/bushel
    ZM=F  Soybean Meal   USD/short ton   (US "cake"/DOC equivalent)
    ZL=F  Soybean Oil    cents/lb
    ZC=F  Corn           cents/bushel

    python fetch_cbot_full.py
"""

from __future__ import annotations

import os

import pandas as pd
import yfinance as yf

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_DIR, "data_real", "cbot", "cbot_soy_complex_2020_2026.csv")
RAW_DIR = os.path.join(_DIR, "data_real", "cbot", "raw")

TICKERS = {
    "ZS=F": "Soybean_CBOT_USD",
    "ZM=F": "Soy_Meal_DOC_CBOT_USD",
    "ZL=F": "Soy_Oil_CBOT_USD",
    "ZC=F": "Corn_CBOT_USD",
}
START = "2019-12-01"


def fetch() -> pd.DataFrame:
    os.makedirs(RAW_DIR, exist_ok=True)
    out = None
    for tk, name in TICKERS.items():
        d = yf.download(tk, start=START, progress=False, auto_adjust=False)
        if d is None or d.empty:
            raise RuntimeError(f"{tk}: no data returned")
        d.to_csv(os.path.join(RAW_DIR, f"{tk.replace('=F','')}.csv"))
        s = d["Close"]
        if isinstance(s, pd.DataFrame):          # yfinance sometimes returns a 1-col frame
            s = s.iloc[:, 0]
        s = s.rename(name)
        print(f"  {tk:<6} {name:<24} {len(s):>5} rows  "
              f"{s.index.min().date()} -> {s.index.max().date()}  last {float(s.iloc[-1]):.2f}")
        out = s.to_frame() if out is None else out.join(s, how="outer")
    out.index.name = "Date"
    return out.sort_index().reset_index()


if __name__ == "__main__":
    print(f"Fetching CBOT soy complex from {START}...")
    df = fetch()
    df.to_csv(OUT, index=False)
    print(f"\nwrote {OUT}")
    print(f"  {len(df):,} rows  {df['Date'].min().date()} -> {df['Date'].max().date()}")
    print(f"  columns: {list(df.columns)}")
