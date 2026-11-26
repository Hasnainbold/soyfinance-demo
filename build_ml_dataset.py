"""
build_ml_dataset.py -- one modelling table: India soybean target + every exogenous driver.

Output: data_real/processed/soybean_ml_dataset.csv, one row per date, 2020-01-01 -> today.
Every input is read from data_real/ -- see data_real/README.md for source and audit of each.

    target          IN_price_inr_qtl        India soybean, Rs./quintal   <- what you predict
                    is_observed             True = a real observation. TRAIN ON THESE ONLY.
    india           IN_price_usd_t          same series in USD/tonne (FX-neutral)
                    IN_msp_inr_qtl          MSP floor for the crop year
                    IN_price_to_msp         price / MSP -- floor proximity
    china (DCE)     CN_*_usd_t              meal(cake)/oil/bean no1+no2/palm/rapeseed meal/corn
                    CN_meal_to_bean         crush-demand indicator
    global (CBOT)   CBOT_*                  soybean / meal / oil / corn
                    crush_margin_usd        oil+meal value minus bean cost (board crush)
    brazil          BR_export_price_usd_t  official govt export price (monthly, ffilled)
                    BR_price_is_reliable   False for thin-volume months (basis-estimated instead)
    fx              USDINR, USDCNY

Why `is_observed` matters
-------------------------
After 2025-10-30 only weekly data exists (Agmarknet killed the open daily feed). Those weekly
values are carried across their days in the master series. Training on the carried rows creates
8-day flat runs and teaches the model that price is constant -- exactly the pathology that made
the previous model's R2~0.95 meaningless. Filter to is_observed before fitting.

Every column here is fetched from a live source or derived from one. Nothing is hardcoded
except the MSP schedule, which is a published government constant.

Coverage note: CBOT columns only start 2021-08-27 (where the CBOT pull begins).
2020-01 -> 2021-08 has India + China + FX only.

    python build_ml_dataset.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(_DIR, "data_real")
OUT = os.path.join(REAL, "processed", "soybean_ml_dataset.csv")
QTL_PER_TONNE = 10.0

MSP_BY_YEAR = {2020: 3880, 2021: 3950, 2022: 4300, 2023: 4600,
               2024: 4892, 2025: 4892, 2026: 5328}

# CBOT soybean is quoted in cents/bushel, meal in USD/short ton, oil in cents/lb.
BU_PER_TONNE = 36.7437          # soybeans, 60 lb/bu
SHORT_TON_PER_TONNE = 1.10231
LB_PER_TONNE = 2204.62
# crush yields per bushel: 44 lb meal, 11 lb oil (US industry standard)
MEAL_LB_PER_BU, OIL_LB_PER_BU = 44.0, 11.0


def _read(*parts, **kw):
    """Read from data_real/ first, falling back to the project root."""
    for base in (REAL, _DIR):
        p = os.path.join(base, *parts)
        if os.path.exists(p):
            return pd.read_csv(p, **kw)
    return None


CN_SERIES = ["soybean_meal", "soybean_oil", "soybean_no1", "soybean_no2",
             "palm_oil", "rapeseed_meal", "corn"]


def _china_adjusted() -> pd.DataFrame | None:
    """
    Back-adjusted DCE closes, one column per product.

    Uses close_adj, not close_raw: the raw continuous series carries un-adjusted contract
    rollovers that read as -15% single-day moves. Adjusting cuts soybean-meal |ret|>5% days
    from 13 to 5 and annualised vol from 21.2% to 18.2%.
    """
    out = None
    for name in CN_SERIES:
        d = _read("china_dce", f"{name}_continuous_adjusted.csv", parse_dates=["date"])
        if d is None:
            continue
        col = d[["date", "close_adj"]].rename(columns={"close_adj": f"CN_{name}_CNY"})
        out = col if out is None else out.merge(col, on="date", how="outer")
    return out.sort_values("date").reset_index(drop=True) if out is not None else None


def build() -> pd.DataFrame:
    m = _read("processed", "soybean_price_master.csv", parse_dates=["date"])
    if m is None:
        raise SystemExit("run build_price_master.py first")
    df = m.rename(columns={"price": "IN_price_inr_qtl"})[
        ["date", "IN_price_inr_qtl", "source", "is_interpolated"]].copy()
    df["is_observed"] = ~df["is_interpolated"].astype(bool)
    df = df.drop(columns=["is_interpolated"])

    fx = _read("fx", "fx_rates.csv", parse_dates=["date"])
    df = df.merge(fx, on="date", how="left")
    df[["USDINR", "USDCNY"]] = df[["USDINR", "USDCNY"]].ffill()

    cn = _china_adjusted()
    if cn is not None:
        df = df.merge(cn, on="date", how="left")
        for c in [c for c in cn.columns if c.endswith("_CNY")]:
            df[c.replace("CN_", "CN_").replace("_CNY", "_usd_t")] = df[c] / df["USDCNY"]
        df = df.drop(columns=[c for c in cn.columns if c.endswith("_CNY")])
        # DCE trades on its own calendar; carry the last close into Indian non-trading days
        for c in [c for c in df.columns if c.startswith("CN_") and c.endswith("_usd_t")]:
            df[c] = df[c].ffill()
        if {"CN_soybean_meal_usd_t", "CN_soybean_no2_usd_t"} <= set(df.columns):
            df["CN_meal_to_bean"] = (df["CN_soybean_meal_usd_t"]
                                     / df["CN_soybean_no2_usd_t"])

    # global_commodities_5yr.csv holds ONLY the genuinely-fetched CBOT tickers
    # (ZS=F / ZM=F / ZL=F / ZC=F). We deliberately do NOT read final_ml_dataset.csv:
    # its *_Production_MMT columns were hardcoded into a Python dict (never fetched from
    # USDA, and its 2026 figures forecast an unfinished crop year) and its
    # Brazil_Export_Price_USD_MT was CBOT x USD/BRL -- a transform of columns already
    # here, not an observed Brazilian quote. Both were removed 2026-09-05.
    g = _read("cbot", "cbot_soy_complex_2020_2026.csv", parse_dates=["Date"])
    if g is not None:
        g = g.rename(columns={"Date": "date"})
        df = df.merge(g, on="date", how="left")
        for c in g.columns:
            if c != "date":
                df[c] = df[c].ffill()
        # board crush margin, USD/tonne of beans
        if {"Soybean_CBOT_USD", "Soy_Meal_DOC_CBOT_USD", "Soy_Oil_CBOT_USD"} <= set(df.columns):
            bean = df["Soybean_CBOT_USD"] / 100.0 * BU_PER_TONNE            # cents/bu -> USD/t
            meal = (df["Soy_Meal_DOC_CBOT_USD"] * SHORT_TON_PER_TONNE
                    * (MEAL_LB_PER_BU * BU_PER_TONNE / LB_PER_TONNE))
            oil = (df["Soy_Oil_CBOT_USD"] / 100.0
                   * (OIL_LB_PER_BU * BU_PER_TONNE))
            df["crush_margin_usd_t"] = (meal + oil - bean).round(2)

    br = _read("brazil", "brazil_soybean_export_price_2020_2026.csv", parse_dates=["date"])
    if br is not None:
        br["month"] = br["date"].dt.to_period("M").dt.to_timestamp()
        br_m = br[["month", "price_usd_per_mt", "is_reliable", "price_source"]].rename(
            columns={"price_usd_per_mt": "BR_export_price_usd_t",
                    "is_reliable": "BR_price_is_reliable",
                    "price_source": "BR_price_source"})
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
        df = df.merge(br_m, on="month", how="left").drop(columns=["month"])
        df["BR_export_price_usd_t"] = df["BR_export_price_usd_t"].ffill()
        df["BR_price_is_reliable"] = df["BR_price_is_reliable"].ffill()
        df["BR_price_source"] = df["BR_price_source"].ffill()

    # India in FX-neutral terms + MSP context
    df["IN_price_usd_t"] = (df["IN_price_inr_qtl"] * QTL_PER_TONNE / df["USDINR"]).round(2)
    df["IN_msp_inr_qtl"] = df["date"].dt.year.map(MSP_BY_YEAR).ffill()
    df["IN_price_to_msp"] = (df["IN_price_inr_qtl"] / df["IN_msp_inr_qtl"]).round(4)

    front = ["date", "IN_price_inr_qtl", "is_observed", "source",
             "IN_price_usd_t", "IN_msp_inr_qtl", "IN_price_to_msp", "USDINR", "USDCNY"]
    rest = [c for c in df.columns if c not in front]
    return df[front + rest].sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    d = build()
    d.to_csv(OUT, index=False)
    print(f"wrote {OUT}")
    print(f"  {len(d):,} rows  {d['date'].min().date()} -> {d['date'].max().date()}  "
          f"{len(d.columns)} columns")
    print(f"  observed (trainable): {int(d['is_observed'].sum()):,}   "
          f"carried-forward: {int((~d['is_observed']).sum()):,}")
    print("\n  column coverage (non-null %):")
    cov = (d.notna().mean() * 100).round(1)
    for c in d.columns:
        first = d.loc[d[c].notna(), "date"].min()
        print(f"    {c:<28} {cov[c]:>5.1f}%   from {first.date() if pd.notna(first) else '-'}")
    print("\n  tail:")
    print(d[["date", "IN_price_inr_qtl", "is_observed", "IN_price_usd_t",
             "CN_soybean_meal_usd_t"]].tail(5).to_string(index=False))
