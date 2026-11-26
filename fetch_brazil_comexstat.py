"""
fetch_brazil_comexstat.py -- real Brazilian soybean export price, official government data.

NOT MERGED into the ML dataset yet -- the user asked to verify this is real before it
touches the modelling table. Output goes to data_real/brazil/ standalone.

Source: Brazil's Ministry of Development, Industry, Trade and Services (MDIC), via the
official Comex Stat API. This is not a market quote -- it is actual customs declarations:
total export VALUE (FOB, USD) divided by total export WEIGHT (KG) for every shipment of
soybeans leaving Brazil that month. Price = value / quantity, both real, both government-recorded.

    POST https://api-comexstat.mdic.gov.br/general
    {"flow": "export", "monthDetail": true, "period": {"from": "2020-01", "to": "2026-08"},
     "filters": [{"filter": "ncm", "values": ["12019000"]}],
     "details": ["ncm"], "metrics": ["metricFOB", "metricKG"]}

NCM 12019000 = "Soja, mesmo triturada, exceto para semeadura"
              (Soybeans, whether or not broken, other than for sowing) -- the raw bean.
NCM 23040090 = "Bagaços e outros resíduos sólidos, da extração do óleo de soja"
              (oilcake and other solid residues from soybean-oil extraction) -- Brazil's
              version of the "cake"/meal, fetched as a bonus for completeness.

Verify it yourself: https://comexstat.mdic.gov.br (official portal, same numbers) or the API
docs at https://api-comexstat.mdic.gov.br/docs#/. The bare `requests` library gets a 403 from
Cloudflare on the human-facing site; the API itself has no such block.

Note: an earlier version of this script pointed at "api.comexstat.mdic.gov.br" (dot, not
hyphen) and used NCM "1201" (4-digit, not the real 8-digit code) -- that hostname doesn't
exist and the filter matched nothing. Fixed here.

    python fetch_brazil_comexstat.py
"""

from __future__ import annotations

import os

import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_DIR, "data_real", "brazil")
API = "https://api-comexstat.mdic.gov.br/general"
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

NCM_BEAN = "12019000"     # raw soybeans
NCM_CAKE = "23040090"     # soybean meal / oilcake residue


def fetch(ncm: str, frm="2020-01", to="2026-08", flow="export") -> pd.DataFrame:
    payload = {"flow": flow, "monthDetail": True, "period": {"from": frm, "to": to},
              "filters": [{"filter": "ncm", "values": [ncm]}],
              "details": ["ncm"], "metrics": ["metricFOB", "metricKG"]}
    r = requests.post(API, json=payload, headers=HEADERS, timeout=(10, 120))
    r.raise_for_status()
    j = r.json()
    if not j.get("success", True):
        raise RuntimeError(j.get("message"))
    rows = j.get("data", {}).get("list", [])
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["year"] = d["year"].astype(int)
    d["monthNumber"] = d["monthNumber"].astype(int)
    d["date"] = pd.to_datetime(dict(year=d["year"], month=d["monthNumber"], day=1))
    for c in ("metricFOB", "metricKG"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["export_value_usd"] = d["metricFOB"]
    d["export_qty_kg"] = d["metricKG"]
    d["export_qty_mt"] = d["export_qty_kg"] / 1000.0
    d["price_usd_per_mt"] = (d["export_value_usd"] / d["export_qty_mt"]).round(2)
    return (d[["date", "ncm", "coNcm", "export_value_usd", "export_qty_mt", "price_usd_per_mt"]]
            .sort_values("date").reset_index(drop=True))


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Fetching Brazil soybean EXPORT price (NCM 12019000, the raw bean)...")
    bean = fetch(NCM_BEAN)
    if bean.empty:
        raise SystemExit("no data returned for soybeans -- check the API is still live")
    out1 = os.path.join(OUT_DIR, "brazil_soybean_export_price_2020_2026.csv")
    bean.to_csv(out1, index=False)
    print(f"  {len(bean)} months  {bean['date'].min().date()} -> {bean['date'].max().date()}")
    print(f"  price range: ${bean['price_usd_per_mt'].min():.0f} - "
          f"${bean['price_usd_per_mt'].max():.0f} /tonne")
    print(f"  wrote {out1}")

    print("\nFetching Brazil soybean MEAL/CAKE export price (NCM 23040090, bonus)...")
    cake = fetch(NCM_CAKE)
    if not cake.empty:
        out2 = os.path.join(OUT_DIR, "brazil_soymeal_export_price_2020_2026.csv")
        cake.to_csv(out2, index=False)
        print(f"  {len(cake)} months  {cake['date'].min().date()} -> {cake['date'].max().date()}")
        print(f"  price range: ${cake['price_usd_per_mt'].min():.0f} - "
              f"${cake['price_usd_per_mt'].max():.0f} /tonne")
        print(f"  wrote {out2}")

    print("\nlast 6 months, soybean bean price:")
    print(bean[["date", "price_usd_per_mt", "export_qty_mt"]].tail(6).to_string(index=False))
