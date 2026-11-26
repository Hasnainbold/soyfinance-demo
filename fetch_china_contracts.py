"""
fetch_china_contracts.py -- individual DCE/CZCE contract months -> a back-adjusted continuous series.

Why not just use the M0 continuous series
-----------------------------------------
Sina's "M0" continuous contract splices successive front months WITHOUT back-adjusting. The
splice shows up as a fake price gap: 2023-12-11 reads -15.2%, 2022-12-19 -14.7%, 2023-08-14
-14.5%. Those are carry differences between two contracts, not market moves. 13 such days
inflate realised volatility from 17.7% to 21.8% and would poison any return-based model.

This module fetches each contract month separately and builds the series properly:

  1. every trading day, the dominant contract = the one with the highest OPEN INTEREST
  2. dominance is forced monotonic -- we never roll backwards to an earlier expiry
  3. on each roll date both contracts trade, so the ratio new/old is observable
  4. all history before a roll is multiplied by that ratio (cumulative, working backwards)

The result has continuous returns and no artificial jumps. Levels are expressed in
"today's contract" terms, which is the standard convention for a back-adjusted series.

Output lands in data_real/china_dce/ -- raw JSON per contract plus the built series.

    python fetch_china_contracts.py                 # soybean meal (the cake)
    python fetch_china_contracts.py --product all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_DIR, "data_real", "china_dce")
RAW_DIR = os.path.join(DATA_DIR, "raw_contracts")

_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        "var%20_{s}=/InnerFuturesNewService.getDailyKLine?symbol={s}")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

# product -> (english name, chinese, liquid delivery months)
# DCE's soy complex concentrates liquidity in Jan/May/Sep; the beans also trade Mar/Jul/Nov.
PRODUCTS = {
    "M":  ("soybean_meal", "豆粕",   (1, 5, 9)),            # <- soybean cake / DOC
    "Y":  ("soybean_oil",  "豆油",   (1, 5, 9)),
    "A":  ("soybean_no1",  "黄大豆一号", (1, 3, 5, 7, 9, 11)),
    "B":  ("soybean_no2",  "黄大豆二号", (1, 3, 5, 7, 9, 11)),
    "P":  ("palm_oil",     "棕榈油", (1, 5, 9)),
    "C":  ("corn",         "corn",   (1, 5, 9)),
    "RM": ("rapeseed_meal", "菜粕",  (1, 5, 9)),
}


def contract_codes(prod: str, y0=2019, y1=2027) -> list:
    """M2601 style codes: product + 2-digit year + 2-digit month."""
    months = PRODUCTS[prod][2]
    return [f"{prod}{str(y)[2:]}{m:02d}" for y in range(y0, y1 + 1) for m in months]


def fetch_contract(sym: str, *, retries: int = 2) -> pd.DataFrame | None:
    cache = os.path.join(RAW_DIR, f"{sym}.json")
    if os.path.exists(cache):
        recs = json.load(open(cache, encoding="utf-8"))
    else:
        recs = None
        for k in range(retries):
            try:
                r = requests.get(_URL.format(s=sym), headers=_HEADERS, timeout=(6, 45))
                m = re.search(r"=\(?(\[.*\])\)?;?\s*$", r.text.strip())
                if m:
                    recs = json.loads(m.group(1))
                break
            except Exception:
                time.sleep(2 * (k + 1))
        os.makedirs(RAW_DIR, exist_ok=True)
        json.dump(recs or [], open(cache, "w", encoding="utf-8"))
        time.sleep(0.4)
    if not recs:
        return None
    d = pd.DataFrame(recs).rename(columns={"d": "date", "o": "open", "h": "high", "l": "low",
                                           "c": "close", "v": "volume", "p": "open_interest",
                                           "s": "settle"})
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for c in ("open", "high", "low", "close", "volume", "open_interest", "settle"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["date", "close"])
    d = d[d["close"] > 0]
    if d.empty:
        return None
    d["contract"] = sym
    return d.sort_values("date").reset_index(drop=True)


def build_continuous(frames: list) -> pd.DataFrame:
    """
    Dominant-contract series, ratio back-adjusted at every roll.

    Returns columns: date, contract, close_raw, close_adj, volume, open_interest, rolled
    """
    allc = pd.concat(frames, ignore_index=True)
    allc["expiry_key"] = allc["contract"].str.extract(r"(\d{4})$")[0].astype(int)
    # 2-digit year wraps: treat 19xx-style keys as 20xx and order by (yy, mm)
    allc["sort_key"] = allc["expiry_key"]

    # per day, the contract carrying the most open interest
    idx = allc.groupby("date")["open_interest"].idxmax()
    dom = allc.loc[idx, ["date", "contract", "close", "volume",
                         "open_interest", "sort_key"]].sort_values("date")
    dom = dom.reset_index(drop=True)

    # forbid rolling backwards to an earlier expiry
    keep, cur = [], -1
    for i, r in dom.iterrows():
        if r["sort_key"] >= cur:
            cur = r["sort_key"]
            keep.append(i)
    dom = dom.loc[keep].reset_index(drop=True)

    # ratio adjust: on each roll day both contracts trade, so the gap is measurable
    dom["rolled"] = dom["contract"] != dom["contract"].shift()
    dom.loc[0, "rolled"] = False
    lookup = allc.set_index(["date", "contract"])["close"]
    factors = np.ones(len(dom))
    for i in range(1, len(dom)):
        if not dom.loc[i, "rolled"]:
            continue
        dt, new_c, old_c = dom.loc[i, "date"], dom.loc[i, "contract"], dom.loc[i - 1, "contract"]
        try:
            new_p, old_p = lookup.loc[(dt, new_c)], lookup.loc[(dt, old_c)]
            if old_p and old_p > 0:
                factors[i] = float(new_p) / float(old_p)
        except KeyError:
            pass                                   # old contract not quoted that day
    # cumulative factor applied BACKWARDS so the latest price stays unadjusted
    cum = np.cumprod(factors[::-1])[::-1] / factors[0] if len(factors) else factors
    cum = np.ones(len(dom))
    running = 1.0
    for i in range(len(dom) - 1, -1, -1):
        cum[i] = running
        if i > 0 and dom.loc[i, "rolled"]:
            running *= factors[i]
    dom["close_raw"] = dom["close"]
    dom["close_adj"] = (dom["close"] * cum).round(2)
    return dom[["date", "contract", "close_raw", "close_adj", "volume",
                "open_interest", "rolled"]]


def run(prod: str) -> pd.DataFrame | None:
    name = PRODUCTS[prod][0]
    print(f"\n{prod}  {name}")
    frames = []
    for sym in contract_codes(prod):
        d = fetch_contract(sym)
        if d is not None and len(d) > 20:
            frames.append(d)
    if not frames:
        print("   no contracts returned data")
        return None
    print(f"   {len(frames)} contracts with data")
    cont = build_continuous(frames)
    cont = cont[cont["date"] >= "2019-12-01"].reset_index(drop=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, f"{name}_continuous_adjusted.csv")
    cont.to_csv(out, index=False)

    r_raw = np.log(cont["close_raw"]).diff()
    r_adj = np.log(cont["close_adj"]).diff()
    print(f"   {len(cont):,} days  {cont['date'].min().date()} -> {cont['date'].max().date()}"
          f"   rolls: {int(cont['rolled'].sum())}")
    print(f"   |ret|>5% days   raw {int((r_raw.abs()>.05).sum()):>3}"
          f"   adjusted {int((r_adj.abs()>.05).sum()):>3}")
    print(f"   ann. vol        raw {r_raw.std()*np.sqrt(243)*100:>5.1f}%"
          f"  adjusted {r_adj.std()*np.sqrt(243)*100:>5.1f}%")
    print(f"   wrote {os.path.relpath(out, _DIR)}")
    return cont


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="M",
                    help="M (soybean meal/cake), Y, A, B, P, C, RM, or 'all'")
    a = ap.parse_args()
    prods = list(PRODUCTS) if a.product == "all" else [a.product.upper()]
    for p in prods:
        if p not in PRODUCTS:
            sys.exit(f"unknown product {p}; choose from {list(PRODUCTS)} or 'all'")
        run(p)
