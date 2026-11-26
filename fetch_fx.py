"""
fetch_fx.py — daily USD/INR and USD/CNY, cached to fx_rates.csv.

Needed because the three price series we compare live in three currencies:
CBOT in USD, DCE in CNY, mandi in INR. Any correlation computed without
converting is partly measuring the exchange rate, not the commodity.
"""

from __future__ import annotations

import os
import pandas as pd
import yfinance as yf

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_DIR, "fx_rates.csv")
PAIRS = {"USDINR=X": "USDINR", "USDCNY=X": "USDCNY"}


def fetch(start="2019-12-01", end=None) -> pd.DataFrame:
    out = None
    for tk, name in PAIRS.items():
        d = yf.download(tk, start=start, end=end, progress=False, auto_adjust=False)
        if d is None or d.empty:
            raise RuntimeError(f"{tk}: no data")
        s = d["Close"]
        if isinstance(s, pd.DataFrame):          # yfinance returns a 1-col frame per ticker
            s = s.iloc[:, 0]
        s = s.rename(name)
        out = s.to_frame() if out is None else out.join(s, how="outer")
    out.index.name = "date"
    # FX doesn't trade on every calendar day; forward-fill so any price date can be converted
    out = out.sort_index().ffill()
    return out.reset_index()


if __name__ == "__main__":
    fx = fetch()
    fx.to_csv(OUT, index=False)
    print(f"wrote {OUT}  {len(fx):,} rows  {fx['date'].min().date()} -> {fx['date'].max().date()}")
    print(fx.tail(3).to_string(index=False))
