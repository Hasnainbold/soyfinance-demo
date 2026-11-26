"""
build_price_master.py -- assemble ONE trustworthy India soybean price series, 2020 -> today.

Rebuilds everything from the raw JSON caches, so it is deterministic and re-runnable and does
not depend on which fetcher ran last. (An earlier pass lost two assembled CSVs because two
background fetches wrote to the same output paths; the caches survived, and this script is the
fix -- outputs are named for what they contain.)

Sources, in priority order per date:
  1. CEDA daily            2020-01-01 .. 2025-10-30   real, independently validated
  2. our mandi CSVs daily  2021-01-01 .. 2025-10-30   real (used to cross-check CEDA)
  3. Agmarknet weekly      2020-01-01 .. today        real, composition-weighted

Everything after 2025-10-30 is weekly, because that is genuinely all that exists -- Agmarknet
2.0 killed the open daily feed. Weekly points are carried forward to daily with an explicit
`is_interpolated` flag so nothing downstream mistakes them for observations.

    python build_price_master.py
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
CEDA_RAW = os.path.join(_DIR, "ceda_raw")
AGMK_RAW = os.path.join(_DIR, "agmarknet_trend_raw")

OUT_MASTER = os.path.join(_DIR, "soybean_price_master.csv")
OUT_CEDA = os.path.join(_DIR, "soybean_ceda_daily_2020_2025.csv")
OUT_WEEKLY = os.path.join(_DIR, "soybean_agmarknet_weekly_2020_2026.csv")
OUT_STATES = os.path.join(_DIR, "soybean_agmarknet_weekly_states_2020_2026.csv")

CEDA_LAST_GOOD = pd.Timestamp("2025-10-30")


def build_ceda() -> pd.DataFrame:
    rows = []
    for fn in sorted(os.listdir(CEDA_RAW)):
        if fn.endswith(".json"):
            rows += json.load(open(os.path.join(CEDA_RAW, fn), encoding="utf-8")) or []
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows).rename(columns={"t": "date", "p_modal": "modal_price",
                                           "p_min": "min_price", "p_max": "max_price"})
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for c in ("modal_price", "min_price", "max_price"):
        d[c] = pd.to_numeric(d[c], errors="coerce").round(1)
    return (d.dropna(subset=["date", "modal_price"]).drop_duplicates("date")
            .sort_values("date").reset_index(drop=True))


def build_agmarknet():
    from fetch_agmarknet_trend import harvest
    nat, per = [], []
    for fn in sorted(os.listdir(AGMK_RAW)):
        if not fn.endswith(".json"):
            continue
        try:
            p = json.load(open(os.path.join(AGMK_RAW, fn), encoding="utf-8"))
        except Exception:
            continue
        n, q = harvest(p)
        nat += n
        per += q
    nat = (pd.DataFrame(nat).sort_values("date")
           .drop_duplicates(subset=["date"], keep="last").reset_index(drop=True))
    per = (pd.DataFrame(per).sort_values("date")
           .drop_duplicates(subset=["date", "region"], keep="last").reset_index(drop=True))
    return nat, per


def state_weights() -> pd.Series:
    """
    Weight each state by its share of mandi-rows in our own daily series.

    Without this, Agmarknet's cross-state mean sits ~7.7% above our mandi mean, because it
    equal-weights 13 states while ours is 43% MP + 29% Maharashtra by row count.
    """
    d = pd.read_csv(os.path.join(_DIR, "soybean_mandi_COMPLETE.csv"),
                    usecols=["date", "state_name", "modal_price"])
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna()
    d = d[d["date"] <= CEDA_LAST_GOOD]                 # real portion only
    return d["state_name"].map(_norm).value_counts(normalize=True)


def _norm(x) -> str:
    return str(x).strip().lower().replace("chattisgarh", "chhattisgarh")


def weekly_weighted(per: pd.DataFrame, wts: pd.Series) -> pd.DataFrame:
    p = per.copy()
    p["w"] = p["region"].map(_norm).map(wts).fillna(0.0)
    out = []
    for (a, b), g in p.groupby(["period_start", "period_end"]):
        gg = g[g["w"] > 0]
        if len(gg) and gg["w"].sum() > 0:
            out.append({"period_start": pd.Timestamp(a), "period_end": pd.Timestamp(b),
                        "price": float(np.average(gg["price"], weights=gg["w"])),
                        "n_states": int(len(gg))})
    return pd.DataFrame(out).sort_values("period_start").reset_index(drop=True)


def main() -> None:
    print("rebuilding from raw caches\n")

    ceda = build_ceda()
    ceda.to_csv(OUT_CEDA, index=False)
    print(f"  CEDA daily        {len(ceda):>5,} days   "
          f"{ceda['date'].min().date()} -> {ceda['date'].max().date()}")

    nat, per = build_agmarknet()
    nat.to_csv(OUT_WEEKLY, index=False)
    per.to_csv(OUT_STATES, index=False)
    print(f"  Agmarknet weekly  {len(nat):>5,} weeks  "
          f"{nat['date'].min().date()} -> {nat['date'].max().date()}")
    print(f"  Agmarknet states  {len(per):>5,} rows   {per['region'].nunique()} regions")

    for c in ("period_start", "period_end"):
        per[c] = pd.to_datetime(per[c])
    wk = weekly_weighted(per, state_weights())
    print(f"  weighted weekly   {len(wk):>5,} weeks  "
          f"{wk['period_start'].min().date()} -> {wk['period_end'].max().date()}")

    # -- master daily series -------------------------------------------------
    daily = ceda[["date", "modal_price"]].rename(columns={"modal_price": "price"})
    daily = daily[daily["date"] <= CEDA_LAST_GOOD].copy()
    daily["source"] = "ceda_daily"
    daily["is_interpolated"] = False

    # after CEDA stops, only weekly exists -- expand each week across its own days
    tail = wk[wk["period_end"] > CEDA_LAST_GOOD]
    rows = []
    for _, r in tail.iterrows():
        for dt in pd.date_range(max(r["period_start"], CEDA_LAST_GOOD + pd.Timedelta(days=1)),
                                r["period_end"]):
            rows.append({"date": dt, "price": round(r["price"], 1),
                         "source": "agmarknet_weekly_weighted",
                         "is_interpolated": dt != r["period_end"]})
    master = (pd.concat([daily, pd.DataFrame(rows)], ignore_index=True)
              .drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True))
    master.to_csv(OUT_MASTER, index=False)

    print(f"\n  MASTER            {len(master):>5,} days   "
          f"{master['date'].min().date()} -> {master['date'].max().date()}")
    print(master.groupby("source").agg(days=("date", "size"),
                                       first=("date", "min"), last=("date", "max")).to_string())

    from data_integrity import audit_price_series
    print()
    # weekly-carried days repeat by construction, so only the observed points are audited
    obs = master[~master["is_interpolated"]]
    print(audit_price_series(obs[["date", "price"]], max_frozen_run=3).summary())


if __name__ == "__main__":
    main()
