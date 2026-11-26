"""
agri_data_sources.py
=====================
Real government data fetchers for two risk-engine roadmap items:
  #5 Rainfall & Weather   (Ministry of Jal Shakti — "Daily District-wise Rainfall Data")
  #8 India Production     (Dept. of Agriculture & Farmers Welfare — "District-wise,
                            season-wise crop production statistics from 1997")

Both are data.gov.in "Data API" resources, fetched with the same
api.data.gov.in/resource/{RESOURCE_ID} pattern already used by app.py for
mandi prices, and cached to local JSON so repeated runs don't re-hit the API.
Resource IDs were located live on data.gov.in (via each dataset's "API" tab)
on 2026-08-29:

    RAINFALL_RESOURCE_ID   = "6c05cd1b-ed59-40c2-bc31-e314f39c6971"
    PRODUCTION_RESOURCE_ID = "35be999b-0208-4354-b557-f6ca9a5355de"

IMPORTANT — what has and hasn't been verified:
  - Both resource IDs were confirmed to exist and expose a "Data API" tab.
  - The actual JSON field names each API returns were NOT confirmed end to
    end, because this dev sandbox's network egress blocks api.data.gov.in
    (only the data.gov.in *website* was reachable, via the browser). So
    this module parses responses defensively — it scans each record's keys
    for what it needs by keyword (case-insensitive) instead of assuming an
    exact field name, the same defensive-parsing spirit as app.py's mandi
    data column renaming.
  - Run `python agri_data_sources.py` on a machine with real internet
    access (e.g. via the Streamlit app's own host) to self-test and print
    the actual field names / sample rows. If a fetch returns None, print
    `_debug_last_raw_sample` (set by both fetchers) to see the raw API
    response and adjust the keyword lists in `_find_key` calls below.

Both public fetchers return None on any failure (bad key, network error,
unrecognized response shape, zero records) so callers can fall back to the
existing synthetic data path — same fallback discipline used everywhere
else in this codebase (see build_real_price_history() in app.py).
"""

from __future__ import annotations

import os
import json
import time
from typing import Optional, List, Dict, Any

import requests
import pandas as pd

RAINFALL_RESOURCE_ID = "6c05cd1b-ed59-40c2-bc31-e314f39c6971"
PRODUCTION_RESOURCE_ID = "35be999b-0208-4354-b557-f6ca9a5355de"
BASE_URL = "https://api.data.gov.in/resource/{rid}"

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agri_data_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
_CACHE_TTL_SECONDS = 24 * 3600  # daily refresh is plenty for these two datasets

# Exposed for debugging when a fetch returns None — holds the last raw
# record dict seen, so a caller/dev can inspect `list(_debug_last_raw_sample)`.
_debug_last_raw_sample: Dict[str, Any] = {}


def _cache_path(name: str) -> str:
    return os.path.join(_CACHE_DIR, f"{name}.json")


def _read_cache(name: str) -> Optional[list]:
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > _CACHE_TTL_SECONDS:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(name: str, records: list) -> None:
    try:
        with open(_cache_path(name), "w") as f:
            json.dump(records, f)
    except Exception:
        pass


def _fetch_all_records(resource_id: str, api_key: str, extra_filters: Optional[dict] = None,
                        max_records: int = 5000, timeout: int = 8) -> Optional[List[Dict[str, Any]]]:
    """Paginate through a data.gov.in resource — same pattern as app.py's mandi fetcher."""
    url = BASE_URL.format(rid=resource_id)
    all_records: List[Dict[str, Any]] = []
    try:
        for offset in range(0, max_records, 100):
            params = {"api-key": api_key, "format": "json", "limit": "100", "offset": str(offset)}
            if extra_filters:
                params.update(extra_filters)
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.json()
            recs = data.get("records", [])
            if not recs:
                break
            all_records.extend(recs)
            if len(all_records) >= int(data.get("total", 0) or 0):
                break
        return all_records or None
    except Exception:
        return None


def _find_key(record: dict, keywords: List[str]) -> Optional[str]:
    """Find the first key in `record` whose lowercased name contains any of `keywords`."""
    for k in record.keys():
        kl = k.lower().replace(" ", "_")
        if any(kw in kl for kw in keywords):
            return k
    return None


# ─────────────────────────────────────────────────────────────────────────
# #5 Rainfall & Weather
# ─────────────────────────────────────────────────────────────────────────

def fetch_district_rainfall(api_key: str, state: Optional[str] = None,
                             use_cache: bool = True) -> Optional[pd.DataFrame]:
    """
    Fetch daily district-wise rainfall (IMD grid model, via the Ministry of
    Jal Shakti's data.gov.in resource). Returns a DataFrame with normalized
    columns [state, district, date, rainfall_mm], or None on failure.
    """
    cache_name = f"rainfall_{(state or 'all').lower().replace(' ', '_')}"
    if use_cache:
        cached = _read_cache(cache_name)
        if cached:
            return _normalize_rainfall_records(cached)

    filters = {}
    if state:
        filters["filters[state]"] = state

    records = _fetch_all_records(RAINFALL_RESOURCE_ID, api_key, extra_filters=filters)
    if not records:
        return None

    _write_cache(cache_name, records)
    return _normalize_rainfall_records(records)


def _normalize_rainfall_records(records: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
    if not records:
        return None
    global _debug_last_raw_sample
    _debug_last_raw_sample = records[0]

    state_key = _find_key(records[0], ["state"])
    district_key = _find_key(records[0], ["district"])
    date_key = _find_key(records[0], ["date"])
    rain_key = _find_key(records[0], ["rainfall", "rain_mm", "actual", "value"])
    if not (district_key and rain_key):
        return None  # response shape not recognized — see _debug_last_raw_sample

    rows = []
    for rec in records:
        try:
            rows.append({
                "state": rec.get(state_key, ""),
                "district": rec.get(district_key, ""),
                "date": rec.get(date_key, ""),
                "rainfall_mm": float(rec.get(rain_key) or 0),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def compute_district_rainfall_deviation(rainfall_df: pd.DataFrame, district: str,
                                         normal_mm: float, window_days: int = 90) -> Optional[float]:
    """
    Real-data replacement for yield_risk_model's synthetic
    `rainfall_deviation_pct`: % deviation of the last `window_days` of
    rainfall for `district` from the supplied normal/LPA (long-period
    average) figure (yield_risk_model.DISTRICT_PROFILES already has one
    per district — pass that in as `normal_mm`).
    """
    if rainfall_df is None or rainfall_df.empty:
        return None
    d = rainfall_df[rainfall_df["district"].astype(str).str.strip().str.lower() == district.strip().lower()]
    if d.empty:
        return None
    cutoff = d["date"].max() - pd.Timedelta(days=window_days)
    recent = d[d["date"] >= cutoff]
    if recent.empty or normal_mm <= 0:
        return None
    total_recent_mm = float(recent["rainfall_mm"].sum())
    scaled_normal = normal_mm * (window_days / 365.0)  # scale annual normal to the window
    if scaled_normal <= 0:
        return None
    return round((total_recent_mm - scaled_normal) / scaled_normal * 100.0, 2)


# ─────────────────────────────────────────────────────────────────────────
# #8 India Price & Production
# ─────────────────────────────────────────────────────────────────────────

def fetch_soybean_production(api_key: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
    """
    Fetch district-wise, season-wise soybean area/production from the
    Dept. of Agriculture & Farmers Welfare dataset. Returns a DataFrame with
    normalized columns [state, district, crop_year, area_hectares,
    production_tonnes], or None.

    Published mirrors of this dataset generally spell the crop "Soyabean";
    both spellings are tried since live field *values* were not confirmed
    from this environment.
    """
    cache_name = "soybean_production"
    if use_cache:
        cached = _read_cache(cache_name)
        if cached:
            return _normalize_production_records(cached)

    records = None
    for crop_name in ("Soyabean", "Soybean", "soyabean"):
        records = _fetch_all_records(
            PRODUCTION_RESOURCE_ID, api_key,
            extra_filters={"filters[Crop]": crop_name},
            max_records=20000,
        )
        if records:
            break

    if not records:
        return None

    _write_cache(cache_name, records)
    return _normalize_production_records(records)


def _normalize_production_records(records: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
    if not records:
        return None
    global _debug_last_raw_sample
    _debug_last_raw_sample = records[0]

    state_key = _find_key(records[0], ["state"])
    district_key = _find_key(records[0], ["district"])
    year_key = _find_key(records[0], ["crop_year", "year"])
    area_key = _find_key(records[0], ["area"])
    prod_key = _find_key(records[0], ["production"])
    if not (area_key and prod_key):
        return None  # response shape not recognized — see _debug_last_raw_sample

    rows = []
    for rec in records:
        try:
            rows.append({
                "state": rec.get(state_key, ""),
                "district": rec.get(district_key, ""),
                "crop_year": rec.get(year_key, ""),
                "area_hectares": float(rec.get(area_key) or 0),
                "production_tonnes": float(rec.get(prod_key) or 0),
            })
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows) if rows else None


def compute_production_yoy_change(production_df: pd.DataFrame, state: Optional[str] = None) -> Optional[dict]:
    """
    Year-over-year change in total soybean production (India, or a given
    state) — the #12 "Production-Price Link" signal: a larger crop
    typically pressures prices down, a smaller crop supports them. Feed
    `yoy_change_pct` into the price-level pillar or the ML feature set.
    """
    if production_df is None or production_df.empty:
        return None
    df = production_df.copy()
    if state:
        df = df[df["state"].astype(str).str.strip().str.lower() == state.strip().lower()]
    if df.empty:
        return None
    df["crop_year"] = pd.to_numeric(df["crop_year"].astype(str).str.slice(0, 4), errors="coerce")
    yearly = df.groupby("crop_year")["production_tonnes"].sum().dropna().sort_index()
    if len(yearly) < 2:
        return None
    latest_year, prev_year = yearly.index[-1], yearly.index[-2]
    latest_val, prev_val = float(yearly.iloc[-1]), float(yearly.iloc[-2])
    if prev_val <= 0:
        return None
    pct_change = round((latest_val - prev_val) / prev_val * 100.0, 2)
    return {
        "latest_year": int(latest_year), "prev_year": int(prev_year),
        "latest_production_tonnes": latest_val, "prev_production_tonnes": prev_val,
        "yoy_change_pct": pct_change,
    }


if __name__ == "__main__":
    DEFAULT_API_KEY = "579b464db66ec23bdd0000014aa051e453434dcf7fb7c2222b5491bc"

    print("Testing rainfall fetch (Maharashtra)...")
    rain_df = fetch_district_rainfall(DEFAULT_API_KEY, state="Maharashtra", use_cache=False)
    if rain_df is not None:
        print(rain_df.head())
    else:
        print("  -> No usable data. Raw sample record was:")
        print(" ", _debug_last_raw_sample)

    print("\nTesting production fetch (Soyabean)...")
    prod_df = fetch_soybean_production(DEFAULT_API_KEY, use_cache=False)
    if prod_df is not None:
        print(prod_df.head())
        print("\nYoY change (India):", compute_production_yoy_change(prod_df))
    else:
        print("  -> No usable data. Raw sample record was:")
        print(" ", _debug_last_raw_sample)
