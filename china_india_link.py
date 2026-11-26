"""
china_india_link.py -- does Chinese soybean-cake (DCE meal) price move Indian soybean price?

China is the marginal buyer of the world's soybeans and crushes them for meal
("soybean cake" / DOC). If Chinese crush demand is the global demand signal, its
meal price should lead the Indian mandi price. This module tests that rather than
assuming it.

Every series is converted to USD per tonne first, otherwise a "correlation"
between CNY/t and INR/qtl is partly just the USD/CNY vs USD/INR cross rate.

    python china_india_link.py                 # full report
    python china_india_link.py --start 2020-01-01 --save
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
QTL_PER_TONNE = 10.0          # 1 tonne = 10 quintals


# -- loaders --------------------------------------------------------------
def load_fx() -> pd.DataFrame:
    fx = pd.read_csv(os.path.join(_DIR, "fx_rates.csv"), parse_dates=["date"])
    return fx.sort_values("date")


def load_china() -> pd.DataFrame:
    """DCE continuous contracts, close in CNY/tonne."""
    w = pd.read_csv(os.path.join(_DIR, "china_soy_wide.csv"), parse_dates=["date"])
    return w.sort_values("date")


def load_india_daily() -> pd.DataFrame:
    """National average modal price, INR/quintal, 2021-01-01 onward."""
    d = pd.read_csv(os.path.join(_DIR, "soybean_mandi_COMPLETE.csv"),
                    usecols=["date", "modal_price"])
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["modal_price"] = pd.to_numeric(d["modal_price"], errors="coerce")
    d = d.dropna()
    return (d.groupby("date", as_index=False)["modal_price"].mean()
            .rename(columns={"modal_price": "IN_soybean_INR_qtl"}).sort_values("date"))


def load_india_monthly() -> pd.DataFrame:
    """Monthly average, INR/quintal, back to 2001 -- the only India source covering 2020."""
    d = pd.read_csv(os.path.join(_DIR, "soybean_monthly_avg.csv"))
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"]).sort_values("date")
    return d[["date", "avg_price"]].rename(columns={"avg_price": "IN_soybean_INR_qtl"})


# -- the aligned panel ----------------------------------------------------
def _month_end() -> str:
    """'ME' on pandas >= 2.2, 'M' before it -- both mean month-end."""
    from pandas import __version__ as _v
    major, minor = (int(x) for x in _v.split(".")[:2])
    return "ME" if (major, minor) >= (2, 2) else "M"


def build_panel(start="2020-01-01", end=None, freq="D") -> pd.DataFrame:
    """
    One row per date with every series in USD/tonne.

    freq='D' -> daily (India coverage starts 2021-01-01)
    freq='M' -> month-end (India coverage starts 2020-01-01, via the monthly file)
    """
    fx, cn = load_fx(), load_china()
    ind = load_india_daily() if freq == "D" else load_india_monthly()

    if freq == "M":
        _ME = _month_end()
        cn = cn.set_index("date").resample(_ME).mean().reset_index()
        fx = fx.set_index("date").resample(_ME).mean().reset_index()
        ind = ind.set_index("date").resample(_ME).mean().reset_index()

    p = cn.merge(fx, on="date", how="left").merge(ind, on="date", how="outer").sort_values("date")
    p[["USDINR", "USDCNY"]] = p[["USDINR", "USDCNY"]].ffill()

    # CNY/tonne -> USD/tonne
    for c in [c for c in p.columns if c.startswith("CN_") and c.endswith("_CNY")]:
        p[c.replace("_CNY", "_USD")] = p[c] / p["USDCNY"]
    # INR/quintal -> USD/tonne
    p["IN_soybean_USD_t"] = p["IN_soybean_INR_qtl"] * QTL_PER_TONNE / p["USDINR"]

    p = p[p["date"] >= pd.Timestamp(start)]
    if end:
        p = p[p["date"] <= pd.Timestamp(end)]
    return p.reset_index(drop=True)


# -- analysis -------------------------------------------------------------
def lead_lag(a: pd.Series, b: pd.Series, max_lag: int = 40) -> pd.DataFrame:
    """
    Cross-correlation of log-returns. lag k>0 means b leads a by k periods
    (b's move today shows up in a k periods later).
    """
    ra = np.log(a).diff()
    rb = np.log(b).diff()
    rows = []
    for k in range(-max_lag, max_lag + 1):
        s = ra.corr(rb.shift(k))
        rows.append({"lag": k, "corr": np.nan if pd.isna(s) else round(float(s), 4)})
    return pd.DataFrame(rows)


def summarise(p: pd.DataFrame, cols: list) -> pd.DataFrame:
    rows = []
    for c in cols:
        if c not in p.columns:
            continue
        s = p.set_index("date")[c].dropna()
        if len(s) < 10:
            continue
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        rows.append({
            "series": c, "n": len(s),
            "from": s.index[0].date(), "to": s.index[-1].date(),
            "first": round(float(s.iloc[0]), 1), "last": round(float(s.iloc[-1]), 1),
            "change_%": round((s.iloc[-1] / s.iloc[0] - 1) * 100, 1),
            "cagr_%": round(((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None,
            "peak": round(float(s.max()), 1), "peak_on": s.idxmax().date(),
            "trough": round(float(s.min()), 1), "trough_on": s.idxmin().date(),
        })
    return pd.DataFrame(rows)


def report(start="2020-01-01") -> dict:
    out = {}
    print("=" * 84)
    print("  CHINA SOYBEAN CAKE (DCE meal) vs INDIA SOYBEAN  --  all in USD/tonne")
    print("=" * 84)

    # -- monthly panel: the only one reaching back to 2020-01 on the India side
    pm = build_panel(start, freq="M")
    dm = pm.dropna(subset=["CN_soybean_meal_USD", "IN_soybean_USD_t"])
    print("\n[MONTHLY PANEL] %d overlapping months  %s -> %s"
          % (len(dm), dm["date"].min().date(), dm["date"].max().date()))
    print(summarise(dm, ["CN_soybean_meal_USD", "CN_soybean_no2_USD",
                         "IN_soybean_USD_t"]).to_string(index=False))

    lv = dm["CN_soybean_meal_USD"].corr(dm["IN_soybean_USD_t"])
    rt = (np.log(dm["CN_soybean_meal_USD"]).diff()
          .corr(np.log(dm["IN_soybean_USD_t"]).diff()))
    print("\n  correlation, price LEVELS   : %+.3f   (inflated by shared trend)" % lv)
    print("  correlation, monthly RETURNS: %+.3f   <- the honest number" % rt)
    out["monthly_level_corr"], out["monthly_return_corr"] = lv, rt
    out["monthly"] = dm

    # -- daily panel: 2021-01-01 onward, enough resolution for lead-lag
    pdd = build_panel("2021-01-01", freq="D")
    dd = pdd.dropna(subset=["CN_soybean_meal_USD", "IN_soybean_USD_t"]).reset_index(drop=True)
    print("\n[DAILY PANEL]   %d overlapping days   %s -> %s"
          % (len(dd), dd["date"].min().date(), dd["date"].max().date()))

    pairs = [("CN_soybean_meal_USD", "China soybean CAKE (meal)"),
             ("CN_soybean_no2_USD", "China imported SOYBEAN"),
             ("CN_soybean_no1_USD", "China domestic SOYBEAN"),
             ("CN_soybean_oil_USD", "China soybean OIL"),
             ("CN_rapeseed_meal_USD", "China rapeseed meal (subst.)"),
             ("CN_palm_oil_USD", "China palm oil (subst.)"),
             ("CN_corn_USD", "China corn (feed competitor)")]
    print("\n  %-32s %9s %9s %14s %9s" % ("driver", "lvl corr", "ret corr", "best lag", "lag corr"))
    print("  " + "-" * 78)
    lags = {}
    for c, name in pairs:
        if c not in dd.columns:
            continue
        sub = dd.dropna(subset=[c, "IN_soybean_USD_t"])
        lvc = sub[c].corr(sub["IN_soybean_USD_t"])
        rtc = np.log(sub[c]).diff().corr(np.log(sub["IN_soybean_USD_t"]).diff())
        cc = lead_lag(sub["IN_soybean_USD_t"], sub[c], 40).dropna()
        best = cc.loc[cc["corr"].abs().idxmax()]
        lags[c] = cc
        k = int(best["lag"])
        arrow = ("CN leads %dd" % k) if k > 0 else (("IN leads %dd" % abs(k)) if k < 0 else "same day")
        print("  %-32s %+9.3f %+9.3f %14s %+9.3f" % (name, lvc, rtc, arrow, best["corr"]))
    out["daily"], out["lags"] = dd, lags

    # -- crush economics: meal is what makes a bean worth crushing
    dd["CN_meal_to_bean"] = dd["CN_soybean_meal_USD"] / dd["CN_soybean_no2_USD"]
    dd["IN_to_CN_meal"] = dd["IN_soybean_USD_t"] / dd["CN_soybean_meal_USD"]
    print("\n[RATIOS]  China meal/bean         mean %.3f  now %.3f"
          % (dd["CN_meal_to_bean"].mean(), dd["CN_meal_to_bean"].iloc[-1]))
    print("          India bean / China meal  mean %.3f  now %.3f"
          % (dd["IN_to_CN_meal"].mean(), dd["IN_to_CN_meal"].iloc[-1]))

    # -- rolling correlation: is the link stable or regime-dependent?
    rr = (np.log(dd["CN_soybean_meal_USD"]).diff()
          .rolling(90).corr(np.log(dd["IN_soybean_USD_t"]).diff()))
    dd["roll90_corr"] = rr
    v = rr.dropna()
    print("\n[ROLLING 90d RETURN CORR]  mean %+.3f  min %+.3f  max %+.3f  now %+.3f"
          % (v.mean(), v.min(), v.max(), v.iloc[-1]))
    print("  share of the window with corr > 0 : %.0f%%" % ((v > 0).mean() * 100))

    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--save", action="store_true", help="write the aligned panel to CSV")
    a = ap.parse_args()
    res = report(a.start)
    if a.save:
        res["daily"].to_csv(os.path.join(_DIR, "china_india_panel.csv"), index=False)
        res["monthly"].to_csv(os.path.join(_DIR, "china_india_panel_monthly.csv"), index=False)
        print("\nwrote china_india_panel.csv + china_india_panel_monthly.csv")
