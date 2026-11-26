"""
market.py — soybean price + forecast, wrapped for the platform.

One place every page pulls from:
    get_price_history()        real daily national-average mandi price series
    get_current_price()        today's live price (fast-fail) → snapshot → last CSV
    get_msp(dt)                MSP for the crop year
    get_forecast(days=90)      ML forecast (trained once, cached for the session)
    get_market_context()       volatility regime / VaR(tenure) / CDVI  (reuses volatility_model)

The forecaster is a single GradientBoosting model on lag / rolling / seasonal
features with recursive multi-step prediction — deliberately compact so the
whole thing trains in seconds and is cached. app.py's heavier 3-model
ensemble can be swapped in later behind the same get_forecast() signature.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    import streamlit as st
    cache_data = st.cache_data
    cache_resource = st.cache_resource
except Exception:                                   # usable headless / in scripts
    def _identity(*a, **k):
        def wrap(f):
            return f
        return wrap
    cache_data = cache_resource = _identity

from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

_DIR = os.path.dirname(os.path.abspath(__file__))
_HIST_DIR = os.path.join(_DIR, "price_history")

MSP_BY_YEAR = {2020: 3880, 2021: 3950, 2022: 4300, 2023: 4600, 2024: 4892, 2025: 4892, 2026: 5328}
DEFAULT_API_KEY = "579b464db66ec23bdd0000014aa051e453434dcf7fb7c2222b5491bc"
_RES_ID = "9ef84268-d588-465a-a308-a864a43d0070"


def get_msp(dt=None) -> int:
    y = (dt or datetime.now()).year
    return MSP_BY_YEAR.get(y, max(MSP_BY_YEAR.values()))


# ── price history ───────────────────────────────────────────────────────
@cache_data(show_spinner=False)
def get_price_history() -> pd.DataFrame:
    """Real daily national-average modal price, 2021 → latest. Columns: date, price."""
    frames = []
    complete = os.path.join(_DIR, "soybean_mandi_COMPLETE.csv")
    if os.path.exists(complete):
        d = pd.read_csv(complete, usecols=["date", "modal_price"])
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d["modal_price"] = pd.to_numeric(d["modal_price"], errors="coerce")
        d = d.dropna()
        daily = d.groupby("date", as_index=False)["modal_price"].mean().rename(
            columns={"modal_price": "price"})
        frames.append(daily)
    mh = os.path.join(_DIR, "soybean_maharashtra_daily.csv")
    if not frames and os.path.exists(mh):
        d = pd.read_csv(mh, usecols=["date", "avg_price"])
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d = d.dropna().rename(columns={"avg_price": "price"})
        frames.append(d[["date", "price"]])

    hist = frames[0] if frames else pd.DataFrame(columns=["date", "price"])

    # top up with recent daily snapshots (JSON) so the series reaches ~today
    rows = []
    if os.path.isdir(_HIST_DIR):
        for fn in sorted(os.listdir(_HIST_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                recs = json.load(open(os.path.join(_HIST_DIR, fn)))
                vals = pd.to_numeric(pd.Series([r.get("modal_price") for r in recs]),
                                     errors="coerce").dropna()
                if len(vals):
                    rows.append({"date": pd.to_datetime(fn[:-5]), "price": float(vals.mean())})
            except Exception:
                pass
    if rows:
        hist = pd.concat([hist, pd.DataFrame(rows)], ignore_index=True)

    hist = (hist.dropna().groupby("date", as_index=False)["price"].mean()
            .sort_values("date").reset_index(drop=True))
    hist["price"] = hist["price"].round(1)
    return hist


# ── current price ───────────────────────────────────────────────────────
@cache_data(ttl=900, show_spinner=False)
def get_current_price(api_key: str = DEFAULT_API_KEY) -> dict:
    """{'price', 'source', 'as_of', 'n_mandis'} — live API (fast-fail) → snapshot → last history point."""
    try:
        import requests
        url = (f"https://api.data.gov.in/resource/{_RES_ID}?api-key={api_key}"
               f"&format=json&filters[commodity]=Soyabean&limit=100&offset=0")
        r = requests.get(url, timeout=(4, 8), headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            recs = r.json().get("records", [])
            vals = pd.to_numeric(pd.Series([x.get("modal_price") for x in recs]),
                                 errors="coerce").dropna()
            if len(vals):
                return {"price": round(float(vals.mean()), 1), "source": "live mandi API",
                        "as_of": datetime.now().date().isoformat(), "n_mandis": int(len(vals))}
    except Exception:
        pass
    # snapshot fallback
    if os.path.isdir(_HIST_DIR):
        for fn in sorted(os.listdir(_HIST_DIR), reverse=True):
            if fn.endswith(".json"):
                try:
                    recs = json.load(open(os.path.join(_HIST_DIR, fn)))
                    vals = pd.to_numeric(pd.Series([r.get("modal_price") for r in recs]),
                                         errors="coerce").dropna()
                    if len(vals):
                        return {"price": round(float(vals.mean()), 1), "source": f"snapshot {fn[:-5]}",
                                "as_of": fn[:-5], "n_mandis": int(len(vals))}
                except Exception:
                    pass
    h = get_price_history()
    if len(h):
        return {"price": float(h["price"].iloc[-1]), "source": "last historical point",
                "as_of": h["date"].iloc[-1].date().isoformat(), "n_mandis": 0}
    return {"price": 6000.0, "source": "default", "as_of": None, "n_mandis": 0}


# ── features ────────────────────────────────────────────────────────────
_FEATS = ["lag1", "lag7", "lag14", "lag30", "roll7", "roll30", "std7", "std30",
          "roc7", "roc30", "doy_sin", "doy_cos", "month", "is_harvest", "is_sowing",
          "days_since_harvest"]


def _days_since_harvest(dt: pd.Timestamp) -> int:
    start = datetime(dt.year, 10, 1) if dt.month >= 10 else datetime(dt.year - 1, 10, 1)
    return (dt.to_pydatetime() - start).days


def _feature_frame(dates: pd.Series, prices: pd.Series) -> pd.DataFrame:
    p = prices.astype(float).reset_index(drop=True)
    f = pd.DataFrame(index=p.index)
    for k in (1, 7, 14, 30):
        f[f"lag{k}"] = p.shift(k)
    f["roll7"] = p.shift(1).rolling(7).mean()
    f["roll30"] = p.shift(1).rolling(30).mean()
    f["std7"] = p.shift(1).rolling(7).std()
    f["std30"] = p.shift(1).rolling(30).std()
    f["roc7"] = p.shift(1) / p.shift(8) - 1.0
    f["roc30"] = p.shift(1) / p.shift(31) - 1.0
    d = pd.to_datetime(dates).reset_index(drop=True)
    doy = d.dt.dayofyear
    f["doy_sin"] = np.sin(2 * np.pi * doy / 365.0)
    f["doy_cos"] = np.cos(2 * np.pi * doy / 365.0)
    f["month"] = d.dt.month
    f["is_harvest"] = d.dt.month.isin([10, 11, 12]).astype(int)
    f["is_sowing"] = d.dt.month.isin([6, 7, 8]).astype(int)
    f["days_since_harvest"] = d.apply(_days_since_harvest)
    return f


@cache_resource(show_spinner="Training the soybean price model…")
def _model_and_forecast(days_ahead: int = 90) -> dict:
    hist = get_price_history()
    if len(hist) < 120:
        cur = get_current_price()["price"]
        idx = pd.date_range(datetime.now().date() + timedelta(days=1), periods=days_ahead)
        fc = pd.DataFrame({"date": idx, "price": cur, "lo": cur * .95, "hi": cur * 1.05})
        return {"history": hist, "forecast": fc, "points": {}, "delta_pct": 0.0,
                "min_forecast": cur, "metrics": {}, "trained": "insufficient history"}

    dates, prices = hist["date"], hist["price"]
    X = _feature_frame(dates, prices)
    y = prices.astype(float).reset_index(drop=True)
    mask = X.notna().all(axis=1)
    X, y, dts = X[mask], y[mask], dates[mask].reset_index(drop=True)
    split = int(len(X) * 0.85)

    model = GradientBoostingRegressor(n_estimators=250, max_depth=4, learning_rate=0.05,
                                      subsample=0.85, random_state=42)
    model.fit(X.iloc[:split][_FEATS], y.iloc[:split])
    yhat = model.predict(X.iloc[split:][_FEATS])
    yt = y.iloc[split:].values
    metrics = {"mae": round(float(mean_absolute_error(yt, yhat)), 1),
               "mape": round(float(np.mean(np.abs((yt - yhat) / yt)) * 100), 2),
               "r2": round(float(r2_score(yt, yhat)), 3)}
    resid_std = float(np.std(yt - yhat))

    # recursive forecast
    model.fit(X[_FEATS], y)                                  # refit on all data
    series = list(y.values)
    all_dates = list(pd.to_datetime(dts))
    last = all_dates[-1]
    fc_dates, fc_vals = [], []
    for i in range(days_ahead):
        nd = last + timedelta(days=i + 1)
        sp = pd.Series(series)
        row = {
            "lag1": sp.iloc[-1], "lag7": sp.iloc[-7], "lag14": sp.iloc[-14], "lag30": sp.iloc[-30],
            "roll7": sp.iloc[-7:].mean(), "roll30": sp.iloc[-30:].mean(),
            "std7": sp.iloc[-7:].std(), "std30": sp.iloc[-30:].std(),
            "roc7": sp.iloc[-1] / sp.iloc[-8] - 1.0, "roc30": sp.iloc[-1] / sp.iloc[-31] - 1.0,
            "doy_sin": np.sin(2 * np.pi * nd.dayofyear / 365.0),
            "doy_cos": np.cos(2 * np.pi * nd.dayofyear / 365.0),
            "month": nd.month, "is_harvest": int(nd.month in (10, 11, 12)),
            "is_sowing": int(nd.month in (6, 7, 8)),
            "days_since_harvest": _days_since_harvest(pd.Timestamp(nd)),
        }
        pred = float(model.predict(pd.DataFrame([row])[_FEATS])[0])
        series.append(pred)
        fc_dates.append(nd)
        fc_vals.append(pred)

    band = resid_std * np.sqrt(np.arange(1, days_ahead + 1) / 7.0)   # widen with horizon
    fc = pd.DataFrame({"date": fc_dates, "price": np.round(fc_vals, 1),
                       "lo": np.round(np.array(fc_vals) - 1.64 * band, 1),
                       "hi": np.round(np.array(fc_vals) + 1.64 * band, 1)})
    cur = float(series[len(y) - 1])
    pts = {h: round(float(fc_vals[h - 1]), 1) for h in (7, 15, 30, 60, 90) if h <= days_ahead}
    return {
        "history": hist, "forecast": fc, "points": pts,
        "delta_pct": round((fc_vals[-1] - cur) / cur * 100, 2),
        "min_forecast": round(float(np.min(fc_vals)), 1),
        "current_anchor": round(cur, 1),
        "metrics": metrics,
        "trained": f"{len(X)} days, {dts.iloc[0].date()} → {dts.iloc[-1].date()}",
    }


def get_forecast(days: int = 90) -> dict:
    return _model_and_forecast(days)


# ── market context (reuse volatility_model) ─────────────────────────────
@cache_data(show_spinner=False)
def get_market_context(tenure_days: int = 90) -> dict:
    hist = get_price_history()
    s = hist.set_index("date")["price"] if len(hist) else pd.Series(dtype=float)
    try:
        from volatility_model import compute_volatility_risk_score
        v = compute_volatility_risk_score(s, loan_tenure_days=tenure_days)
        var = v.get("var_95", {})
        return {"regime": v.get("regime", "medium"),
                "annualised_vol_pct": v.get("current_volatility_pct"),
                "var_pct": var.get("var_pct") if isinstance(var, dict) else None,
                "cdvi": v.get("cdvi"), "vol_risk_score": v.get("score")}
    except Exception as e:
        return {"regime": "medium", "annualised_vol_pct": None, "var_pct": None,
                "cdvi": None, "vol_risk_score": None, "note": str(e)}


if __name__ == "__main__":
    h = get_price_history()
    print(f"history: {len(h)} rows  {h['date'].min().date()} → {h['date'].max().date()}  "
          f"last ₹{h['price'].iloc[-1]:.0f}")
    print("current:", get_current_price())
    f = get_forecast(90)
    print("forecast points:", f["points"], "| delta%:", f["delta_pct"], "| metrics:", f["metrics"])
    print("context:", get_market_context())
