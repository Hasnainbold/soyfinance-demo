import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor, VotingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import warnings
warnings.filterwarnings("ignore")

# ── AgriCred Risk Engine ──────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from risk_engine import (
        compute_composite_risk_score,
        generate_enhanced_receipt,
        generate_synthetic_borrower,
    )
    RISK_ENGINE_AVAILABLE = True
except ImportError:
    RISK_ENGINE_AVAILABLE = False

try:
    from agri_data_sources import (
        fetch_district_rainfall,
        compute_district_rainfall_deviation,
        fetch_soybean_production,
        compute_production_yoy_change,
    )
    AGRI_DATA_SOURCES_AVAILABLE = True
except ImportError:
    AGRI_DATA_SOURCES_AVAILABLE = False

try:
    import data_provenance as provenance
    PROVENANCE_AVAILABLE = True
except ImportError:
    PROVENANCE_AVAILABLE = False

try:
    import receipt_store
    RECEIPT_STORE_AVAILABLE = True
except ImportError:
    RECEIPT_STORE_AVAILABLE = False

try:
    import borrower_verification_demo as borrower_verify
    BORROWER_VERIFY_AVAILABLE = True
except Exception:
    BORROWER_VERIFY_AVAILABLE = False

try:
    import land_record_verify
    LAND_VERIFY_AVAILABLE = True
except Exception:
    LAND_VERIFY_AVAILABLE = False

try:
    import fpo_verification
    FPO_VERIFY_AVAILABLE = True
except Exception:
    FPO_VERIFY_AVAILABLE = False

# Set page config
st.set_page_config(layout="wide", page_title="SoyFinance - Soybean Warehouse Receipt Finance", page_icon="🌱")

# Custom CSS for Dark Theme & Glassmorphism
st.markdown("""
<style>
    /* Main background */
    .stApp {
        background-color: #0f172a;
        color: #f8fafc;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #1e293b;
    }
    
    /* Metric Cards */
    [data-testid="stMetricValue"] {
        color: #10b981;
    }
    .metric-card {
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 15px;
        backdrop-filter: blur(10px);
        margin-bottom: 1rem;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #f8fafc !important;
    }
    
    /* Dataframe tables */
    .stDataFrame {
        border-radius: 10px;
        overflow: hidden;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #1e293b;
        border-radius: 5px 5px 0 0;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #3b82f6;
        color: white;
    }
    
    /* Custom Colors */
    .text-green { color: #10b981; }
    .text-blue { color: #3b82f6; }
    .text-red { color: #ef4444; }
    .text-amber { color: #f59e0b; }

    /* ── Soybean Intelligence page styles ─────────────── */
    .si-hero {
        background: linear-gradient(135deg,rgba(16,185,129,0.12),rgba(59,130,246,0.08));
        border: 1px solid rgba(16,185,129,0.25); border-radius:16px;
        padding:22px 32px; text-align:center; margin-bottom:20px;
    }
    .si-hero-title {
        font-size:2rem; font-weight:800;
        background:linear-gradient(135deg,#10b981,#3b82f6);
        -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin:0;
    }
    .si-hero-sub { color:#94a3b8; font-size:.95rem; margin-top:6px; }
    .live-dot {
        display:inline-block; width:9px; height:9px; background:#10b981;
        border-radius:50%; animation:blink 1.4s infinite; margin-right:6px; vertical-align:middle;
    }
    @keyframes blink {
        0%{box-shadow:0 0 0 0 rgba(16,185,129,.7);}
        70%{box-shadow:0 0 0 8px rgba(16,185,129,0);}
        100%{box-shadow:0 0 0 0 rgba(16,185,129,0);}
    }
    .kpi {
        background:rgba(15,30,51,0.85); border:1px solid rgba(255,255,255,0.07);
        border-radius:14px; padding:20px 16px; text-align:center;
        transition:border-color .2s, transform .2s;
    }
    .kpi:hover { border-color:rgba(16,185,129,.35); transform:translateY(-3px); }
    .kpi-label { font-size:.72rem; font-weight:600; letter-spacing:.09em;
                 color:#94a3b8; text-transform:uppercase; margin-bottom:8px; }
    .kpi-val { font-size:1.9rem; font-weight:800; color:#f8fafc; line-height:1; }
    .kpi-sub { font-size:.8rem; margin-top:6px; }
    .g{color:#10b981;}.r{color:#ef4444;}.bl{color:#3b82f6;}.pu{color:#a855f7;}.am{color:#f59e0b;}
    .mrow {
        display:flex; align-items:center; justify-content:space-between;
        background:rgba(15,30,51,0.7); border:1px solid rgba(255,255,255,0.06);
        border-radius:12px; padding:14px 18px; margin-bottom:8px; transition:border-color .2s;
    }
    .mrow:hover { border-color:rgba(59,130,246,.3); }
    .prow {
        display:flex; justify-content:space-between; align-items:center;
        background:rgba(15,30,51,0.6); border:1px solid rgba(255,255,255,0.05);
        border-radius:11px; padding:14px 18px; margin-bottom:8px;
    }
    .infobox {
        background:rgba(15,30,51,0.7); border:1px solid rgba(255,255,255,0.06);
        border-radius:12px; padding:16px; font-size:.85rem; color:#94a3b8;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
CURRENT_DATE   = datetime.now()
DEFAULT_API_KEY = "579b464db66ec23bdd0000014aa051e453434dcf7fb7c2222b5491bc"
RES_ID         = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL       = f"https://api.data.gov.in/resource/{RES_ID}"
MSP            = 4892
DATA_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_history")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Real data file paths ──
_APP_DIR       = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR    = os.path.dirname(_APP_DIR)
MAHARASHTRA_DAILY_CSV  = os.path.join(_APP_DIR, "soybean_maharashtra_daily.csv")
MANDI_DETAIL_CSV       = os.path.join(_APP_DIR, "soybean_mandi_COMPLETE.csv")
EXCEL_MANDI_FILE       = os.path.join(_APP_DIR, "Mandi_price_data.xlsx")

def format_inr(number):
    try:
        s = str(int(round(number)))
        r = ",".join([s[x-2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
        return f"₹ {r}"
    except:
        return f"₹ {number}"


def _render_receipt_field(f, _date):
    """Render one Streamlit input for a receipt_store.FIELDS entry; return its raw value.

    Optional date fields get a 'recorded?' checkbox so they can stay blank
    (a blank date is then scored as unverified by the provenance layer).
    """
    t = f["type"]
    label = f["label"]
    help_txt = f.get("help") or None
    key = f"rf_{f['key']}"

    if t == "text":
        return st.text_input(label, value=str(f["default"] or ""), help=help_txt, key=key)
    if t == "int":
        return st.number_input(label, value=int(f["default"] or 0), step=1, min_value=0,
                               help=help_txt, key=key)
    if t == "float":
        return st.number_input(label, value=float(f["default"] or 0.0), step=1.0,
                               min_value=0.0, format="%.2f", help=help_txt, key=key)
    if t == "bool":
        return st.checkbox(label, value=bool(f["default"]), help=help_txt, key=key)
    if t == "date":
        if f["required"]:
            return st.date_input(label, value=_date.today(), help=help_txt, key=key)
        # Inside st.form, widgets don't rerun until submit, so render both
        # and resolve at submit time rather than conditionally.
        d = st.date_input(label, value=_date.today(), help=help_txt, key=key)
        na = st.checkbox(f"↳ {label}: not recorded / N/A", value=True, key=key + "_na")
        return None if na else d
    if t.startswith("choice:"):
        opts = t.split(":", 1)[1].split("|")
        default = f["default"] if f["default"] in opts else opts[0]
        return st.selectbox(label, opts, index=opts.index(default), help=help_txt, key=key)
    return st.text_input(label, value="", help=help_txt, key=key)

# ─────────────────────────────────────────────────────────────────────────────
# REAL DATA LOADERS  (Maharashtra focus)
# ─────────────────────────────────────────────────────────────────────────────
# Historical MSP values by crop year (Oct–Sep cycle)
_MSP_BY_YEAR = {
    2020: 3880, 2021: 3950, 2022: 4300, 2023: 4600, 2024: 4892, 2025: 4892,
}

def _msp_for_date(dt):
    """Return the MSP applicable for a given date (crop year runs Oct–Sep)."""
    yr = dt.year if dt.month >= 10 else dt.year
    return _MSP_BY_YEAR.get(yr, MSP)


@st.cache_data(show_spinner=False)
def load_maharashtra_daily():
    """Load real Maharashtra daily avg prices (2021-01-01 to 2025-10-30, ~1764 rows)."""
    if not os.path.exists(MAHARASHTRA_DAILY_CSV):
        return None
    df = pd.read_csv(MAHARASHTRA_DAILY_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # Add MSP column
    df["msp"] = df["date"].apply(_msp_for_date)
    return df


@st.cache_data(show_spinner=False)
def load_mandi_detail():
    """Load district-level detail from the 2022-2026 CSV (all states)."""
    if not os.path.exists(MANDI_DETAIL_CSV):
        return None
    df = pd.read_csv(MANDI_DETAIL_CSV)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["min_price", "max_price", "modal_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)

@st.cache_data(show_spinner=False)
def load_mandi_detail_maharashtra():
    """Load district-level detail for Maharashtra from the CSV."""
    df = load_mandi_detail()
    if df is None:
        return None
    mh = df[df["state_name"] == "Maharashtra"].copy()
    return mh.reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_excel_mandi():
    """Load the Mandi_price_data.xlsx (Latur market, 134 rows, Jan 2024 – Apr 2025)."""
    if not os.path.exists(EXCEL_MANDI_FILE):
        return None
    df = pd.read_excel(EXCEL_MANDI_FILE)
    df = df.rename(columns={
        "Arrival Date": "arrival_date", "State": "state", "District": "district",
        "Market": "market", "Min Price": "min_price", "Max Price": "max_price",
        "Avg price": "modal_price", "Variety": "variety", "Commodity": "commodity",
    })
    df["arrival_date"] = pd.to_datetime(df["arrival_date"], format="%d/%m/%Y", errors="coerce")
    for col in ["min_price", "max_price", "modal_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("arrival_date").reset_index(drop=True)


def build_real_price_history():
    """
    Build a real historical price series.
    Try Maharashtra daily CSV first, then fall back to mandi detail CSV (daily avg).
    Returns DataFrame with columns: Date, Price, MSP  (or None).
    """
    mh = load_maharashtra_daily()
    if mh is not None and len(mh) > 0:
        hist = pd.DataFrame({
            "Date": mh["date"],
            "Price": mh["avg_price"].round().astype(int),
            "MSP": mh["msp"].astype(int),
        })
        if "num_districts" in mh.columns:
            hist["Volume"] = (mh["num_districts"] * np.random.RandomState(42).randint(400, 900, len(mh))).astype(int)
        return hist.reset_index(drop=True)

    # Fallback: build daily avg from the mandi detail CSV
    detail = load_mandi_detail()
    if detail is None or len(detail) == 0:
        return None
    daily = detail.groupby("date").agg(
        avg_price=("modal_price", "mean"),
        num_mandis=("modal_price", "count"),
    ).reset_index()
    daily = daily.sort_values("date")
    daily["msp"] = daily["date"].apply(_msp_for_date)
    hist = pd.DataFrame({
        "Date": daily["date"],
        "Price": daily["avg_price"].round().astype(int),
        "MSP": daily["msp"].astype(int),
    })
    return hist.reset_index(drop=True)


def get_mandi_detail_for_date(selected_date):
    """
    Retrieve district-level mandi data for a specific date from CSV (all states).
    Returns DataFrame compatible with API format, or None.
    """
    detail = load_mandi_detail()
    if detail is None:
        return None
    date_str = pd.Timestamp(selected_date)
    day_data = detail[detail["date"] == date_str]
    if len(day_data) == 0:
        # Try closest date within ±2 days
        nearby = detail[(detail["date"] >= date_str - pd.Timedelta(days=2)) &
                     (detail["date"] <= date_str + pd.Timedelta(days=2))]
        if len(nearby) > 0:
            closest_date = nearby.iloc[(nearby["date"] - date_str).abs().argsort().iloc[0]]["date"]
            day_data = detail[detail["date"] == closest_date]
    if len(day_data) == 0:
        return None
    # Normalize columns to match API format
    result = day_data.copy()
    result = result.rename(columns={
        "state_name": "state",
        "district_name": "district",
    })
    result["market"] = result["district"]  # Use district as market name proxy
    return result[["state", "district", "market", "modal_price", "min_price", "max_price"]].reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# LIVE API + LOCAL SNAPSHOT LOGIC (ported from soy_price_predictor)
# ─────────────────────────────────────────────────────────────────────────────
# Fast-fail networking: a dead / rate-limited data.gov.in used to stall the
# whole app for minutes (60s timeout x 3 retries x up to 10 pages). Cap it
# hard — if live prices don't answer in a few seconds we fall back to the
# most recent saved snapshot, then to CSV history.
_API_CONNECT_TIMEOUT = 4      # seconds to establish the connection
_API_READ_TIMEOUT    = 8      # seconds to wait for the response body
_LIVE_FETCH_MAX_PAGES = 5     # 500 mandi rows is ample for a national average

def _api_call(api_key, extra_params=None, timeout=None):
    params = {"api-key": api_key, "format": "json", "filters[commodity]": "Soyabean"}
    if extra_params:
        params.update(extra_params)
    # Build full URL manually (requests URL-encodes brackets which some APIs reject)
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    full_url  = f"{BASE_URL}?{param_str}"
    to = timeout if timeout is not None else (_API_CONNECT_TIMEOUT, _API_READ_TIMEOUT)
    try:
        s = requests.Session()
        s.mount("https://", HTTPAdapter(max_retries=Retry(
            total=1, connect=1, read=1, backoff_factor=0.3,
            status_forcelist=(500, 502, 503, 504),
        )))
        r = s.get(full_url, timeout=to, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, f"API unreachable: {e}"

def _save_snapshot(date_str, records):
    path = os.path.join(DATA_DIR, f"{date_str}.json")
    with open(path, "w") as f:
        json.dump(records, f)

def _load_snapshot(date_str):
    path = os.path.join(DATA_DIR, f"{date_str}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def _get_saved_dates():
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    dates = []
    for f in files:
        try:
            d = datetime.strptime(f.replace(".json", ""), "%Y-%m-%d").date()
            dates.append(d)
        except:
            pass
    return sorted(dates, reverse=True)

@st.cache_data(ttl=900, show_spinner=False)
def fetch_live_mandi(api_key, selected_date):
    """
    Fetch mandi prices for a given date.
    - Today  → live API (no date filter) + save snapshot
    - Past   → local snapshot if available, else API with arrival_date filter
    Returns: (DataFrame | None, bool success, avg_price | None, error_str | None)
    """
    date_str   = selected_date.strftime("%Y-%m-%d")
    today_date = date.today()
    is_today   = (selected_date == today_date)

    # ── Past date: try CSV data first → snapshot → API
    if not is_today:
        # 1. Try real CSV district-level data (Maharashtra, 2021–2025)
        csv_data = get_mandi_detail_for_date(selected_date)
        if csv_data is not None and len(csv_data) > 0:
            return csv_data, True, float(csv_data["modal_price"].mean()), None

        # 2. Try saved JSON snapshot
        saved = _load_snapshot(date_str)
        if saved:
            df = pd.DataFrame(saved)
            for col in ["modal_price", "min_price", "max_price"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["modal_price"])
            if len(df):
                return df, True, float(df["modal_price"].mean()), None

        # No snapshot → query API with arrival_date filter
        api_date_str = selected_date.strftime("%d/%m/%Y")
        all_records = []
        for offset in range(0, 2000, 100):
            data, err = _api_call(api_key, {"limit": "100", "offset": str(offset),
                                            "filters[arrival_date]": api_date_str})
            if data and data.get("records"):
                all_records.extend(data["records"])
                if len(all_records) >= int(data.get("total", 0)):
                    break
            else:
                break

        if all_records:
            _save_snapshot(date_str, all_records)
            df = pd.DataFrame(all_records)
            for col in ["modal_price", "min_price", "max_price"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["modal_price"])
            return df, True, float(df["modal_price"].mean()), None

        return None, False, None, f"No API data for {selected_date.strftime('%d %b %Y')} (market may have been closed)."

    # ── Today: live fetch (no date filter)
    all_records = []
    err = None
    for offset in range(0, _LIVE_FETCH_MAX_PAGES * 100, 100):
        data, err = _api_call(api_key, {"limit": "100", "offset": str(offset)})
        if data and data.get("records"):
            all_records.extend(data["records"])
            if len(all_records) >= int(data.get("total", 0)):
                break
        else:
            break

    if all_records:
        _save_snapshot(date_str, all_records)
        df = pd.DataFrame(all_records)
        for col in ["modal_price", "min_price", "max_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["modal_price"])
        return df, True, float(df["modal_price"].mean()), None

    # ── Live API didn't answer — fall back to the most recent saved
    #    snapshot (still real mandi data, just not today's) before the
    #    caller drops to CSV history / hardcoded estimate.
    for d in _get_saved_dates():
        saved = _load_snapshot(d.strftime("%Y-%m-%d"))
        if not saved:
            continue
        df = pd.DataFrame(saved)
        for col in ["modal_price", "min_price", "max_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["modal_price"])
        if len(df):
            return df, True, float(df["modal_price"].mean()), f"stale:{d.strftime('%d %b %Y')}"

    return None, False, None, err or "No data returned from API"


def build_price_history_from_snapshots():
    """Build a real historical price series from all saved daily snapshots."""
    saved_dates = _get_saved_dates()
    rows = []
    for d in saved_dates:
        data = _load_snapshot(d.strftime("%Y-%m-%d"))
        if data:
            df = pd.DataFrame(data)
            df["modal_price"] = pd.to_numeric(df.get("modal_price", pd.Series(dtype=float)), errors="coerce")
            avg = df["modal_price"].dropna().mean()
            if not np.isnan(avg):
                rows.append({"Date": pd.Timestamp(d), "Price": round(avg)})
    if rows:
        return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return None


def synthetic_price_history(current_avg):
    """5-year synthetic history anchored to current live price."""
    np.random.seed(42)
    today_dt = datetime.today()
    n = 5 * 365
    dates = [today_dt - timedelta(days=n - 1 - i) for i in range(n)]
    start = current_avg * 0.83
    base  = np.linspace(start, current_avg, n)
    doy   = np.array([d.timetuple().tm_yday for d in dates])
    seas  = -np.sin((doy - 60) / 365 * 2 * np.pi) * current_avg * 0.05
    noise = np.cumsum(np.random.normal(0, current_avg * 0.002, n))
    prices = np.clip(base + seas + noise, current_avg * 0.75, current_avg * 1.15)
    prices[-1] = current_avg
    msp = np.linspace(4200, MSP, n).astype(int)
    volumes = np.random.randint(10000, 100000, n)
    harvest_mask = np.array([(d.month >= 10 and d.month <= 12) for d in dates])
    volumes[harvest_mask] = volumes[harvest_mask] * 2
    return pd.DataFrame({
        "Date": dates, "Price": prices.round().astype(int),
        "MSP": msp, "Volume": volumes
    })



def _build_ml_features(price_series):
    """
    Build ML features from a price series (pandas Series or array).
    Returns a DataFrame with all feature columns.
    Enhanced with 38 features for the ensemble model.
    """
    df = pd.DataFrame({"price": price_series.values if hasattr(price_series, 'values') else price_series})

    # Lag features (8)
    for lag in [1, 2, 3, 5, 7, 14, 21, 30]:
        df[f"lag_{lag}"] = df["price"].shift(lag)

    # Rolling statistics (12)
    for window in [7, 14, 30]:
        df[f"roll_mean_{window}"] = df["price"].rolling(window).mean()
        df[f"roll_std_{window}"] = df["price"].rolling(window).std()
        df[f"roll_min_{window}"] = df["price"].rolling(window).min()
        df[f"roll_max_{window}"] = df["price"].rolling(window).max()

    # Price momentum (3)
    df["momentum_7d"] = df["price"].pct_change(7) * 100
    df["momentum_14d"] = df["price"].pct_change(14) * 100
    df["momentum_30d"] = df["price"].pct_change(30) * 100

    # Volatility ratio (1)
    df["vol_ratio_7_30"] = df["roll_std_7"] / (df["roll_std_30"] + 1e-6)

    # Mean reversion signal (1)
    df["mean_revert_30"] = (df["price"] - df["roll_mean_30"]) / (df["roll_std_30"] + 1e-6)

    # ── NEW: Exponential Moving Averages (2)
    df["ema_7"] = df["price"].ewm(span=7, adjust=False).mean()
    df["ema_21"] = df["price"].ewm(span=21, adjust=False).mean()

    # ── NEW: Rate of Change (2)
    df["roc_7"] = (df["price"] - df["price"].shift(7)) / (df["price"].shift(7) + 1e-6) * 100
    df["roc_14"] = (df["price"] - df["price"].shift(14)) / (df["price"].shift(14) + 1e-6) * 100

    # ── NEW: Bollinger Band position (1)
    bb_mid = df["price"].rolling(20).mean()
    bb_std = df["price"].rolling(20).std()
    df["bb_position"] = (df["price"] - bb_mid) / (2 * bb_std + 1e-6)

    # ── NEW: Price spread ratio (1)
    df["spread_7_30"] = df["roll_mean_7"] / (df["roll_mean_30"] + 1e-6)

    return df


def _build_prediction_row(prices, next_date):
    """Build a single feature row for prediction from the price history + date."""
    row = {}

    # Lag features
    for lag in [1, 2, 3, 5, 7, 14, 21, 30]:
        idx = len(prices) - lag
        row[f"lag_{lag}"] = prices[idx] if idx >= 0 else prices[0]

    # Rolling stats
    for window in [7, 14, 30]:
        recent = prices[-window:] if len(prices) >= window else prices
        row[f"roll_mean_{window}"] = float(np.mean(recent))
        row[f"roll_std_{window}"] = float(np.std(recent)) if len(recent) > 1 else 0.0
        row[f"roll_min_{window}"] = float(np.min(recent))
        row[f"roll_max_{window}"] = float(np.max(recent))

    # Momentum
    for period in [7, 14, 30]:
        past = prices[-period] if len(prices) > period else prices[0]
        row[f"momentum_{period}d"] = (prices[-1] - past) / (past + 1e-6) * 100

    # Volatility ratio
    std7 = row["roll_std_7"]
    std30 = row["roll_std_30"]
    row["vol_ratio_7_30"] = std7 / (std30 + 1e-6)

    # Mean reversion
    row["mean_revert_30"] = (prices[-1] - row["roll_mean_30"]) / (row["roll_std_30"] + 1e-6)

    # ── NEW: EMA (approximation using recent prices)
    def _ema(data, span):
        alpha = 2 / (span + 1)
        ema = data[0]
        for p in data[1:]:
            ema = alpha * p + (1 - alpha) * ema
        return ema
    row["ema_7"] = _ema(prices[-min(30, len(prices)):], 7)
    row["ema_21"] = _ema(prices[-min(60, len(prices)):], 21)

    # ── NEW: Rate of Change
    p7 = prices[-7] if len(prices) > 7 else prices[0]
    p14 = prices[-14] if len(prices) > 14 else prices[0]
    row["roc_7"] = (prices[-1] - p7) / (p7 + 1e-6) * 100
    row["roc_14"] = (prices[-1] - p14) / (p14 + 1e-6) * 100

    # ── NEW: Bollinger Band position
    recent20 = prices[-20:] if len(prices) >= 20 else prices
    bb_mid = float(np.mean(recent20))
    bb_std = float(np.std(recent20)) if len(recent20) > 1 else 1.0
    row["bb_position"] = (prices[-1] - bb_mid) / (2 * bb_std + 1e-6)

    # ── NEW: Price spread ratio
    row["spread_7_30"] = row["roll_mean_7"] / (row["roll_mean_30"] + 1e-6)

    # Calendar features
    row["month"] = next_date.month
    row["day_of_week"] = next_date.weekday() if hasattr(next_date, 'weekday') else next_date.dayofweek
    row["day_of_year"] = next_date.timetuple().tm_yday if hasattr(next_date, 'timetuple') else next_date.dayofyear
    row["is_harvest"] = 1 if next_date.month in [10, 11, 12] else 0
    row["is_sowing"] = 1 if next_date.month in [6, 7, 8] else 0

    # ── NEW: Quarter & crop year features
    row["quarter"] = (next_date.month - 1) // 3 + 1
    crop_start = datetime(next_date.year, 10, 1) if next_date.month >= 10 else datetime(next_date.year - 1, 10, 1)
    row["days_since_harvest"] = (next_date - crop_start.date() if hasattr(next_date, 'date') == False else next_date - crop_start).days if hasattr(next_date, 'month') else 0
    try:
        row["days_since_harvest"] = (next_date - crop_start.date()).days
    except:
        row["days_since_harvest"] = (next_date - crop_start).days

    return row


FEATURE_COLS = [
    # Lags (8)
    "lag_1", "lag_2", "lag_3", "lag_5", "lag_7", "lag_14", "lag_21", "lag_30",
    # Rolling stats (12)
    "roll_mean_7", "roll_mean_14", "roll_mean_30",
    "roll_std_7", "roll_std_14", "roll_std_30",
    "roll_min_7", "roll_min_14", "roll_min_30",
    "roll_max_7", "roll_max_14", "roll_max_30",
    # Momentum (3)
    "momentum_7d", "momentum_14d", "momentum_30d",
    # Derived (2)
    "vol_ratio_7_30", "mean_revert_30",
    # NEW: EMA (2)
    "ema_7", "ema_21",
    # NEW: Rate of Change (2)
    "roc_7", "roc_14",
    # NEW: Bollinger & spread (2)
    "bb_position", "spread_7_30",
    # Calendar (5)
    "month", "day_of_week", "day_of_year",
    "is_harvest", "is_sowing",
    # NEW: Seasonal (2)
    "quarter", "days_since_harvest",
]


@st.cache_resource(show_spinner="🤖 Training Ensemble ML model on 2021–2026 mandi data...")
def train_price_model():
    """
    Train an Ensemble model (GBR + RandomForest + Ridge) on daily avg price
    from the complete mandi CSV (2021–2026).
    Returns (ensemble_model, rf_model, metrics_dict) or (None, None, None).
    """
    detail = load_mandi_detail()
    if detail is None or len(detail) == 0:
        return None, None, None

    # Aggregate to daily national average
    daily = detail.groupby("date")["modal_price"].mean().reset_index()
    daily = daily.sort_values("date").reset_index(drop=True)
    daily.columns = ["date", "price"]

    # Build features
    feat_df = _build_ml_features(daily["price"])

    # Add calendar features from dates
    feat_df["month"] = daily["date"].dt.month
    feat_df["day_of_week"] = daily["date"].dt.dayofweek
    feat_df["day_of_year"] = daily["date"].dt.dayofyear
    feat_df["is_harvest"] = feat_df["month"].isin([10, 11, 12]).astype(int)
    feat_df["is_sowing"] = feat_df["month"].isin([6, 7, 8]).astype(int)
    feat_df["quarter"] = ((feat_df["month"] - 1) // 3 + 1).astype(int)

    # Days since harvest start (Oct 1 of current crop year)
    def _days_since_harvest(dt):
        crop_start = datetime(dt.year, 10, 1) if dt.month >= 10 else datetime(dt.year - 1, 10, 1)
        return (dt - crop_start).days
    feat_df["days_since_harvest"] = daily["date"].apply(_days_since_harvest)

    # Drop rows with NaN (first ~30 days lack lag features)
    feat_df = feat_df.dropna().reset_index(drop=True)

    X = feat_df[FEATURE_COLS]
    y = feat_df["price"]

    # Train/test split: last 20% as test (time-series split)
    split_idx = int(len(feat_df) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    # ── Ensemble: 3 models with weighted voting ──
    # Tree counts are tuned for a fast first load on modest hardware. Raise
    # GBR_ESTIMATORS / RF_ESTIMATORS (e.g. 400 / 300) for a bit more accuracy
    # at the cost of a slower cold start; the model is cached for the session.
    GBR_ESTIMATORS = 120
    RF_ESTIMATORS = 100
    RF_NJOBS = 2  # -1 can oversubscribe BLAS threads and run slower on 2–4 core machines
    gbr = GradientBoostingRegressor(
        n_estimators=GBR_ESTIMATORS, max_depth=5, learning_rate=0.06,
        min_samples_split=10, min_samples_leaf=5,
        subsample=0.85, random_state=42,
    )
    rf = RandomForestRegressor(
        n_estimators=RF_ESTIMATORS, max_depth=12,
        min_samples_split=8, min_samples_leaf=4,
        random_state=42, n_jobs=RF_NJOBS,
    )
    ridge = Ridge(alpha=100.0)

    ensemble = VotingRegressor(
        estimators=[("gbr", gbr), ("rf", rf), ("ridge", ridge)],
        weights=[3, 2, 1],
    )
    ensemble.fit(X_train, y_train)

    # Also train RF separately for confidence intervals
    rf_standalone = RandomForestRegressor(
        n_estimators=RF_ESTIMATORS, max_depth=12,
        min_samples_split=8, min_samples_leaf=4,
        random_state=42, n_jobs=RF_NJOBS,
    )
    rf_standalone.fit(X_train, y_train)

    # Evaluate
    y_pred_test = ensemble.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred_test)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred_test)))
    mape = float(np.mean(np.abs((y_test.values - y_pred_test) / y_test.values)) * 100)
    r2 = float(r2_score(y_test, y_pred_test))

    # Feature importances (from GBR component)
    gbr_model = ensemble.named_estimators_["gbr"]
    importances = dict(zip(FEATURE_COLS, gbr_model.feature_importances_))
    top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:10]

    metrics = {
        "mae": round(mae, 1),
        "rmse": round(rmse, 1),
        "mape": round(mape, 2),
        "r2": round(r2, 4),
        "train_size": len(X_train),
        "test_size": len(X_test),
        "total_days": len(daily),
        "date_range": f"{daily['date'].min().strftime('%d %b %Y')} – {daily['date'].max().strftime('%d %b %Y')}",
        "top_features": top_features,
    }

    return ensemble, rf_standalone, metrics


def make_prediction(hist_df, horizons, days_ahead=90):
    """
    Ensemble ML prediction using GBR + RF + Ridge.
    Uses RF per-tree spread for confidence intervals.
    Falls back to simple trend if model is unavailable.
    Returns: (pts_dict, pred_df)  — same interface as before.
    """
    result = train_price_model()
    model, rf_model, ml_metrics = result if result and len(result) == 3 else (None, None, None)

    prices_arr = hist_df["Price"].values.astype(float).tolist()
    current = prices_arr[-1]
    last_date = pd.to_datetime(hist_df["Date"].iloc[-1])

    if model is not None:
        # ── ML recursive prediction ──
        predicted_prices = []
        predicted_upper = []
        predicted_lower = []
        working_prices = list(prices_arr)  # mutable copy

        for i in range(days_ahead):
            next_date = last_date + timedelta(days=i + 1)
            row = _build_prediction_row(working_prices, next_date)
            X_pred = pd.DataFrame([row])[FEATURE_COLS]

            # Ensemble prediction
            pred_price = float(model.predict(X_pred)[0])

            # RF-based confidence interval: use per-tree predictions
            if rf_model is not None:
                tree_preds = np.array([tree.predict(X_pred)[0] for tree in rf_model.estimators_])
                tree_std = float(np.std(tree_preds))
                # Widen confidence band over horizon (uncertainty grows)
                horizon_factor = 1 + 0.015 * i
                ci_width = max(tree_std * 1.96 * horizon_factor, ml_metrics["mae"] * 0.5)
            else:
                mae = ml_metrics["mae"] if ml_metrics else 100
                ci_width = mae * (1 + 0.03 * i)

            predicted_prices.append(pred_price)
            predicted_upper.append(pred_price + ci_width)
            predicted_lower.append(pred_price - ci_width)
            working_prices.append(pred_price)

        # Extract specific horizon points
        future_dates = [last_date + timedelta(days=i + 1) for i in range(days_ahead)]
        curve = [round(p) for p in predicted_prices]
        upper = [round(u) for u in predicted_upper]
        lower = [round(l) for l in predicted_lower]

        pts = {}
        for h in horizons:
            idx = min(h - 1, len(predicted_prices) - 1)
            pts[h] = round(predicted_prices[idx])

    else:
        # ── Fallback: simple trend (original logic) ──
        window = min(60, len(prices_arr))
        recent = np.array(prices_arr[-window:])
        x = np.arange(window)
        slope, _ = np.polyfit(x, recent, 1)
        slope = np.clip(slope, -15, 15)
        today_month = datetime.today().month
        months_to_harvest = (10 - today_month) % 12
        harvest_factor = -0.04 if months_to_harvest <= 3 else 0.01
        max_change_pct = 0.12

        pts = {}
        for h in horizons:
            raw = current + slope * h + current * harvest_factor * (h / 90)
            p = float(np.clip(raw, current * (1 - max_change_pct * h / 90),
                              current * (1 + max_change_pct * h / 90)))
            pts[h] = round(p)

        future_dates = [last_date + timedelta(days=i + 1) for i in range(days_ahead)]
        curve, upper, lower = [], [], []
        for i in range(days_ahead):
            h = i + 1
            raw = current + slope * h + current * harvest_factor * (h / 90)
            p = float(np.clip(raw, current * (1 - max_change_pct * h / 90),
                              current * (1 + max_change_pct * h / 90)))
            w = current * 0.005 * (h / 10)
            curve.append(round(p))
            upper.append(round(p + w))
            lower.append(round(p - w))

    pred_df = pd.DataFrame({
        "Date": future_dates,
        "Predicted Price": curve,
        "Upper Bound": upper,
        "Lower Bound": lower,
    })
    return pts, pred_df


# ─────────────────────────────────────────────────────────────────────────────
# DATA GENERATION (non-price data stays the same)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def generate_farmers():
    np.random.seed(42)
    first_names = ['Rajesh', 'Suresh', 'Amit', 'Vijay', 'Ramesh', 'Mukesh', 'Gopal', 'Hari', 'Shiv', 'Ram']
    last_names  = ['Patidar', 'Chouhan', 'Yadav', 'Sharma', 'Patel', 'Jain', 'Singh', 'Deshmukh', 'Kadam']
    return [f"{np.random.choice(first_names)} {np.random.choice(last_names)}" for _ in range(50)]

@st.cache_data
def generate_warehouse_receipts(current_price):
    np.random.seed(42)
    farmers   = generate_farmers()
    locations = ['Indore', 'Ujjain', 'Dewas', 'Khandwa', 'Latur', 'Nagpur', 'Akola']
    grades    = ['A', 'B', 'C']
    statuses  = ['Pending', 'Approved', 'Active', 'Repaid', 'Defaulted']
    wdra_opts = [True, True, True, False]          # 75% registered
    ins_opts  = ['full', 'full', 'partial', 'none'] # 50% full, 25% partial, 25% none
    data = []
    for i in range(1, 101):
        qty        = np.random.randint(50, 500)
        # Use actual live price ± small variance for market value
        price_var  = int(current_price * np.random.uniform(0.97, 1.03))
        market_val = qty * price_var
        loan_amt   = int(market_val * np.random.uniform(0.70, 0.80))
        deposit_date = CURRENT_DATE - timedelta(days=np.random.randint(1, 120))
        storage_days = (CURRENT_DATE - deposit_date).days
        location   = np.random.choice(locations)
        grade      = np.random.choice(grades, p=[0.6, 0.3, 0.1])

        # Synthetic borrower profile (deterministic per receipt)
        rng_i = np.random.RandomState(42 + i)
        txns  = rng_i.randint(1, 40)

        # ── New risk-roadmap fields (quick wins) ──
        wdra_reg = bool(np.random.choice(wdra_opts))
        insurance_val = np.random.choice(ins_opts)
        # Registration number only exists if actually registered
        wdra_reg_number = f"{location[:2].upper()}-WDRA-{rng_i.randint(1000, 9999)}" if wdra_reg else None
        # Last physical inspection: usually recent, occasionally stale/missing (models registry decay)
        inspection_gap_days = int(rng_i.choice([30, 60, 120, 200, 400, 999])) if wdra_reg else None
        last_inspection = (CURRENT_DATE - timedelta(days=inspection_gap_days)).strftime("%Y-%m-%d") if inspection_gap_days else None
        # Insurance policy: expiry can be past, near, or comfortably future
        expiry_gap_days = int(rng_i.choice([-20, 15, 90, 200, 300])) if insurance_val != 'none' else None
        insurance_expiry = (CURRENT_DATE + timedelta(days=expiry_gap_days)).strftime("%Y-%m-%d") if expiry_gap_days is not None else None
        sum_insured_val = int(market_val * rng_i.uniform(0.65, 1.05)) if insurance_val != 'none' else 0
        # Lab quality slip — correlated loosely with Grade
        moisture_val = round(float(rng_i.uniform(8.0, 14.0)), 1)
        oil_val = round(float(rng_i.uniform(16.5, 20.5)), 1)
        protein_val = round(float(rng_i.uniform(32.0, 40.0)), 1)
        foreign_matter_val = round(float(rng_i.uniform(0.3, 3.5)), 2)

        data.append({
            'Receipt ID':          f"WR-2026-{1000+i}",
            'Farmer Name':         np.random.choice(farmers),
            'Location':            location,
            'Quantity (qtl)':      qty,
            'Grade':               grade,
            'Deposit Date':        deposit_date.strftime("%Y-%m-%d"),
            'Market Value':        market_val,
            'Loan Amount':         loan_amt,
            'Status':              np.random.choice(statuses, p=[0.15, 0.1, 0.5, 0.2, 0.05]),
            'Risk Score':          0,  # placeholder — computed by risk engine
            'Tenure (days)':       90,
            # ── Enhanced risk metadata ──
            'WDRA Registered':     wdra_reg,
            'WDRA Reg Number':     wdra_reg_number,
            'Last Inspection':     last_inspection,
            'Insurance':           insurance_val,
            'Insurance Expiry':    insurance_expiry,
            'Sum Insured':         sum_insured_val,
            'Collateral Manager':  bool(rng_i.random() > 0.2),
            'Storage Days':        storage_days,
            'Deposit Price':       price_var,
            'Moisture %':          moisture_val,
            'Oil Content %':       oil_val,
            'Protein Content %':   protein_val,
            'Foreign Matter %':    foreign_matter_val,
            # Borrower profile
            'Borrower Txns':       txns,
            'Borrower Success':    int(txns * rng_i.uniform(0.75, 1.0)),
            'Years on Platform':   int(rng_i.randint(0, 8)),
            'KYC Verified':        bool(rng_i.random() > 0.1),
            'FPO Member':          bool(rng_i.random() > 0.4),
            'Past Defaults':       int(rng_i.choice([0, 0, 0, 0, 1, 1, 2])),
            'Source':              'Synthetic',
        })
    return pd.DataFrame(data)

@st.cache_data
def generate_investors():
    np.random.seed(42)
    first_names = ['Anand', 'Prakash', 'Sanjay', 'Rahul', 'Neha', 'Priya', 'Kiran', 'Nitin']
    last_names  = ['Rathi', 'Oswal', 'Motilal', 'Jhunjhunwala', 'Damani', 'Biyani', 'Kotak']
    return [f"{np.random.choice(first_names)} {np.random.choice(last_names)}" for _ in range(20)]

@st.cache_data
def generate_bids(receipts_df):
    np.random.seed(42)
    investors = generate_investors()
    bids = []
    active_receipts = receipts_df[receipts_df['Status'].isin(['Pending', 'Approved'])].head(20)
    for _, row in active_receipts.iterrows():
        for _ in range(np.random.randint(1, 5)):
            bids.append({
                'Receipt ID':       row['Receipt ID'],
                'Investor Name':    np.random.choice(investors),
                'Bid Amount':       row['Loan Amount'],
                'Interest Rate (%)': round(np.random.uniform(10.5, 16.0), 2),
                'Status':           np.random.choice(['Pending', 'Accepted', 'Rejected'], p=[0.6, 0.2, 0.2])
            })
    return pd.DataFrame(bids)

@st.cache_data
def get_buyers(current_price):
    base = int(current_price)
    return pd.DataFrame([
        {"Company": "Ruchi Soya (Patanjali)", "Location": "Indore",     "Product": "Soy Oil",          "Price Offered (₹/qtl)": base + 8,    "Demand (MT)": 500,  "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Adani Wilmar",           "Location": "Ahmedabad",  "Product": "Soy Oil",          "Price Offered (₹/qtl)": base - 42,   "Demand (MT)": 1000, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Cargill India",          "Location": "Gurgaon",    "Product": "Soy Meal Export",  "Price Offered (₹/qtl)": base + 28,   "Demand (MT)": 800,  "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "ITC Agri Business",      "Location": "Kolkata",    "Product": "Soybean",          "Price Offered (₹/qtl)": base - 12,   "Demand (MT)": 600,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "Sonic Biochem",          "Location": "Indore",     "Product": "Soy Lecithin",     "Price Offered (₹/qtl)": base - 22,   "Demand (MT)": 200,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "Prestige Industries",    "Location": "Indore",     "Product": "Soy Oil",          "Price Offered (₹/qtl)": base - 52,   "Demand (MT)": 300,  "Rating": "⭐⭐⭐"},
        {"Company": "Vippy Industries",       "Location": "Dewas",      "Product": "Soy Meal",         "Price Offered (₹/qtl)": base - 62,   "Demand (MT)": 400,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "NAFED",                  "Location": "Delhi",      "Product": "MSP Procurement",  "Price Offered (₹/qtl)": MSP,          "Demand (MT)": 5000, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Mahakosh (Ruchi)",       "Location": "Haldia",     "Product": "Soy Oil",          "Price Offered (₹/qtl)": base - 32,   "Demand (MT)": 450,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "Kirti Gold",             "Location": "Latur",      "Product": "Soy Oil",          "Price Offered (₹/qtl)": base + 18,   "Demand (MT)": 250,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "Maharashtra Solvents",   "Location": "Dhule",      "Product": "Soy Meal",         "Price Offered (₹/qtl)": base - 92,   "Demand (MT)": 150,  "Rating": "⭐⭐⭐"},
        {"Company": "Godrej Agrovet",         "Location": "Mumbai",     "Product": "Animal Feed",      "Price Offered (₹/qtl)": base - 102,  "Demand (MT)": 700,  "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Suguna Foods",           "Location": "Coimbatore", "Product": "Poultry Feed",     "Price Offered (₹/qtl)": base - 72,   "Demand (MT)": 900,  "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Aviagen",                "Location": "Pune",       "Product": "Poultry Feed",     "Price Offered (₹/qtl)": base - 82,   "Demand (MT)": 350,  "Rating": "⭐⭐⭐⭐"},
        {"Company": "Bunge India",            "Location": "Rajpura",    "Product": "Edible Oil",       "Price Offered (₹/qtl)": base - 17,   "Demand (MT)": 600,  "Rating": "⭐⭐⭐⭐⭐"},
    ])


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.markdown("<h1 style='text-align: center; color: #10b981;'>🌱 SoyFinance</h1>", unsafe_allow_html=True)
st.sidebar.markdown("<p style='text-align: center; color: #94a3b8;'>Warehouse Receipt Finance</p>", unsafe_allow_html=True)
st.sidebar.divider()

page = st.sidebar.radio("Navigation",
    ["📊 Dashboard", "📋 Warehouse Receipts & Loans", "📈 Price Trends & Prediction",
     "💰 Investor Bid Board", "🏪 Buyer Marketplace", "⚠️ Risk Assessment",
     "➕ Add Real Receipt", "🧾 Borrower Verification", "🏢 FPO Verification",
     "🏞️ Land Verification", "🌾 Soybean Intelligence"]
)

st.sidebar.divider()
st.sidebar.markdown("#### 🔑 API Key")
api_key = st.sidebar.text_input("data.gov.in", value=DEFAULT_API_KEY, type="password")

st.sidebar.markdown("#### 📅 Price Date")
selected_date = st.sidebar.date_input(
    "View prices for",
    value=date.today(),
    max_value=date.today(),
    min_value=date.today() - timedelta(days=730),
)
is_today = (selected_date == date.today())

saved_dates = _get_saved_dates()

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh Prices", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown(f"""
<div style='font-size: 0.8rem; color: #64748b; text-align: center;'>
    System Status: <span style='color: #10b981;'>Online</span><br>
    Date: {CURRENT_DATE.strftime('%d %b %Y')}<br>
    Price Source: {'🟢 LIVE' if is_today else '🔵 HISTORICAL'}<br>
    Version: 1.1.0
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# FETCH PRICES — re-runs whenever selected_date changes
# ─────────────────────────────────────────────────────────────────────────────
spinner_msg = (
    "⚡ Fetching live mandi prices..."
    if is_today else
    f"🔍 Fetching historical prices for {selected_date.strftime('%d %b %Y')} from API..."
)
with st.spinner(spinner_msg):
    mandi_df, price_ok, live_avg, price_err = fetch_live_mandi(api_key, selected_date)

# ── Handle API failure
FALLBACK_PRICE = 6650
if not price_ok or mandi_df is None or len(mandi_df) == 0:
    if not is_today:
        # For past dates: don't silently show fake data — show real error
        st.error(
            f"❌ **No data found for {selected_date.strftime('%d %b %Y')}** — "
            f"{price_err or 'API returned no records.'}"
        )
        st.info(
            "💡 **Tip:** Mandi data is usually not reported on **Sundays and public holidays**. "
            "Try the previous or next weekday.\n\n"
            f"📁 **Dates with saved data:** "
            + (', '.join(f"`{d.strftime('%d %b %Y')}`" for d in saved_dates) if saved_dates else "*None yet — view today's data first*")
        )
        st.stop()
    else:
        # For today: use fallback so the app still works
        live_avg = FALLBACK_PRICE
        mandi_df = pd.DataFrame([
            {"state": "Madhya Pradesh", "district": "Indore",  "market": "Indore APMC",  "modal_price": 6650, "min_price": 6400, "max_price": 6900},
            {"state": "Maharashtra",    "district": "Latur",   "market": "Latur APMC",   "modal_price": 6700, "min_price": 6500, "max_price": 6950},
            {"state": "Madhya Pradesh", "district": "Ujjain",  "market": "Ujjain APMC",  "modal_price": 6580, "min_price": 6350, "max_price": 6800},
            {"state": "Madhya Pradesh", "district": "Dewas",   "market": "Dewas APMC",   "modal_price": 6620, "min_price": 6400, "max_price": 6850},
            {"state": "Maharashtra",    "district": "Nagpur",  "market": "Nagpur APMC",  "modal_price": 6610, "min_price": 6380, "max_price": 6840},
            {"state": "Maharashtra",    "district": "Akola",   "market": "Akola APMC",   "modal_price": 6570, "min_price": 6330, "max_price": 6810},
            {"state": "Madhya Pradesh", "district": "Khandwa", "market": "Khandwa APMC", "modal_price": 6550, "min_price": 6310, "max_price": 6790},
        ])
        st.sidebar.warning(f"⚠️ Live API failed: {price_err}\nShowing estimate data.")
        price_ok = False

current_price  = float(live_avg)
price_vs_msp   = current_price - MSP
pct_vs_msp     = price_vs_msp / MSP * 100

# Build price history: Real CSV → snapshots → synthetic fallback
real_hist = build_real_price_history()
if real_hist is not None and len(real_hist) >= 30:
    hist_df     = real_hist
    hist_source = f"Real Mandi Data ({len(real_hist)} days, 2021–2026)"
else:
    # Fallback: try snapshots
    snapshot_hist = build_price_history_from_snapshots()
    if snapshot_hist is not None and len(snapshot_hist) >= 3:
        hist_df     = snapshot_hist
        hist_source = f"Real Snapshots ({len(snapshot_hist)} days saved)"
    else:
        hist_df     = synthetic_price_history(current_price)
        hist_source = "Synthetic (based on live price)"

# Add MSP column if missing (for synthetic/snapshot)
if "MSP" not in hist_df.columns:
    hist_df["MSP"] = MSP

# Anchor last point to live price
hist_df = hist_df.copy()
hist_df.loc[hist_df.index[-1], "Price"] = int(current_price)

# Predictions
pred_pts, pred_df = make_prediction(hist_df, [7, 15, 30, 60, 90], days_ahead=90)

# Price change vs previous saved day
if len(hist_df) >= 2:
    prev_price = float(hist_df["Price"].iloc[-2])
    day_chg    = (current_price - prev_price) / prev_price * 100
    day_chg_abs = current_price - prev_price
else:
    day_chg     = 0.0
    day_chg_abs = 0.0

# Source label
price_src_label = (
    f"🟢 LIVE — {selected_date.strftime('%d %b %Y')} · {len(mandi_df)} mandis"
    if price_ok else
    f"🟡 FALLBACK — API unavailable"
)

# Load all other data (uses live price for market values)
receipts_df = generate_warehouse_receipts(current_price)

# Merge in any operator-entered REAL receipts (Source="Manual"). These are
# real lots with genuine lab / WDRA / insurance inputs, so the provenance
# layer counts their fields as verified. They sort to the top.
real_receipts_count = 0
if RECEIPT_STORE_AVAILABLE:
    try:
        _real_df = receipt_store.to_display_rows(current_price)
        if _real_df is not None and len(_real_df) > 0:
            _real_df = _real_df.reindex(columns=receipts_df.columns)
            receipts_df = pd.concat([_real_df, receipts_df], ignore_index=True)
            real_receipts_count = len(_real_df)
    except Exception as _re:
        st.sidebar.warning(f"⚠️ Could not load real receipts: {_re}")

if "Source" not in receipts_df.columns:
    receipts_df["Source"] = "Synthetic"
receipts_df["Source"] = receipts_df["Source"].fillna("Synthetic")

bids_df     = generate_bids(receipts_df)
buyers_df   = get_buyers(current_price)


# ═════════════════════════════════════════════════════════════════════════════
# PAGES
# ═════════════════════════════════════════════════════════════════════════════

if page == "📊 Dashboard":
    st.title("Platform Dashboard")

    # Price source banner
    src_color = "#10b981" if price_ok else "#f59e0b"
    st.markdown(f"""
    <div style='background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);
                border-radius:10px;padding:10px 18px;margin-bottom:16px;font-size:.88rem;color:#94a3b8;'>
      <span style='color:{src_color};font-weight:700;'>{price_src_label}</span>
      &nbsp;·&nbsp; History: {hist_source}
    </div>""", unsafe_allow_html=True)

    # KPIs
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Receipts", len(receipts_df))
    with col2:
        total_loan = receipts_df[receipts_df['Status'] == 'Active']['Loan Amount'].sum()
        st.metric("Active Loans Value", format_inr(total_loan))
    with col3:
        chg_str = f"{day_chg:+.1f}%"
        st.metric("Avg Soybean Price", f"₹ {int(current_price):,}/qtl", chg_str)
    with col4:
        st.metric("Active Investors", len(bids_df['Investor Name'].unique()))
    with col5:
        st.metric("Registered Buyers", len(buyers_df))

    st.divider()

    col_chart, col_table = st.columns([2, 1])

    with col_chart:
        st.subheader("Recent Price Trend (30 Days)")
        recent_prices = hist_df.tail(30).copy()
        recent_prices["Date"] = pd.to_datetime(recent_prices["Date"])
        fig = px.area(recent_prices, x='Date', y='Price',
                      title="Mandi Average Price (₹/Quintal)",
                      color_discrete_sequence=['#10b981'],
                      template='plotly_dark')
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                          margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        st.subheader("Platform Status")
        status_counts = receipts_df['Status'].value_counts().reset_index()
        status_counts.columns = ['Status', 'Count']
        fig2 = px.pie(status_counts, values='Count', names='Status', hole=0.7,
                     color_discrete_sequence=['#3b82f6', '#10b981', '#f59e0b', '#64748b', '#ef4444'],
                     template='plotly_dark')
        fig2.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                           margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Recent Warehouse Receipts")
    st.dataframe(receipts_df.sort_values(by='Deposit Date', ascending=False).head(5),
                 use_container_width=True, hide_index=True)


elif page == "📋 Warehouse Receipts & Loans":
    st.title("Warehouse Receipts & Loans")

    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.multiselect("Filter by Status", receipts_df['Status'].unique(), default=receipts_df['Status'].unique())
    with col2:
        loc_filter = st.multiselect("Filter by Location", receipts_df['Location'].unique(), default=receipts_df['Location'].unique())
    with col3:
        risk_filter = st.slider("Max Risk Score", 0, 100, 100)

    filtered_df = receipts_df[
        (receipts_df['Status'].isin(status_filter)) &
        (receipts_df['Location'].isin(loc_filter)) &
        (receipts_df['Risk Score'] <= risk_filter)
    ]

    def color_status(val):
        color = 'white'
        if val == 'Active':    color = '#10b981'
        elif val == 'Pending': color = '#f59e0b'
        elif val == 'Defaulted': color = '#ef4444'
        elif val == 'Approved':  color = '#3b82f6'
        return f'color: {color}'

    st.dataframe(
        filtered_df.style.map(color_status, subset=['Status']),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Market Value": st.column_config.NumberColumn("Market Value", format="₹ %d"),
            "Loan Amount":  st.column_config.NumberColumn("Loan Amount",  format="₹ %d"),
            "Risk Score":   st.column_config.ProgressColumn("Risk Score", min_value=0, max_value=100)
        }
    )

    with st.expander("➕ Simulate New Receipt Creation"):
        st.markdown("### Farmer Soybean Deposit")
        f_col1, f_col2, f_col3 = st.columns(3)
        fname = f_col1.text_input("Farmer Name")
        floc  = f_col2.selectbox("Mandi Location", ['Indore', 'Ujjain', 'Dewas', 'Latur'])
        fqty  = f_col3.number_input("Quantity (Quintals)", min_value=10)
        if st.button("Generate Warehouse Receipt & Assess Loan"):
            est_val  = int(fqty * current_price)
            est_loan = int(est_val * 0.75)
            st.success(f"Receipt WR-2026-NEW created! Qty: {fqty} qtl @ ₹{int(current_price):,}/qtl  "
                       f"→ Market Value: ₹{est_val:,} → Loan (75%): ₹{est_loan:,}")


elif page == "📈 Price Trends & Prediction":
    st.title("Soybean Market Intelligence")

    tabs = st.tabs(["📊 Past → Current → Future", "🤖 Prediction Details (90 Days)", "Weather & Mandi Data"])

    with tabs[0]:
        st.subheader("Unified Price Timeline: 2021 → Today → +90 Days")
        st.caption(f"Source: {hist_source} · Ensemble ML forecast with confidence bands")

        fig = go.Figure()
        hist_plot = hist_df.copy()
        hist_plot["Date"] = pd.to_datetime(hist_plot["Date"])

        # Historical actual prices (solid blue)
        fig.add_trace(go.Scatter(x=hist_plot['Date'], y=hist_plot['Price'],
                                 name='Historical Price', line=dict(color='#3b82f6', width=2),
                                 hovertemplate="<b>%{x|%d %b %Y}</b><br>₹%{y:,.0f}/qtl<extra>Historical</extra>"))

        # MSP line
        if "MSP" in hist_plot.columns:
            fig.add_trace(go.Scatter(x=hist_plot['Date'], y=hist_plot['MSP'],
                                     name='Govt MSP', line=dict(color='#f59e0b', width=1.5, dash='dash'),
                                     hovertemplate="MSP: ₹%{y:,.0f}<extra></extra>"))

        # Prediction confidence band (shaded purple)
        pred_plot = pred_df.copy()
        pred_plot["Date"] = pd.to_datetime(pred_plot["Date"])
        fig.add_trace(go.Scatter(
            x=pd.concat([pred_plot['Date'], pred_plot['Date'][::-1]]),
            y=pd.concat([pred_plot['Upper Bound'], pred_plot['Lower Bound'][::-1]]),
            fill='toself', fillcolor='rgba(168,85,247,0.12)',
            line=dict(color='rgba(0,0,0,0)'), name='Confidence Band', hoverinfo='skip'))

        # Prediction line (dashed purple)
        fig.add_trace(go.Scatter(x=pred_plot['Date'], y=pred_plot['Predicted Price'],
                                 name='ML Forecast', line=dict(color='#a855f7', width=2.5, dash='dot'),
                                 hovertemplate="<b>%{x|%d %b %Y}</b><br>₹%{y:,.0f}/qtl<extra>Forecast</extra>"))

        # Today marker
        fig.add_vline(x=CURRENT_DATE, line_width=2, line_dash="dash", line_color="#10b981")
        fig.add_annotation(x=CURRENT_DATE, y=current_price,
                           text=f"Today ₹{int(current_price):,}",
                           showarrow=True, arrowhead=2, arrowcolor="#10b981",
                           font=dict(color="#10b981", size=12, family="Arial Black"),
                           bgcolor="rgba(16,185,129,0.15)", bordercolor="#10b981")

        # Forecast horizon annotations
        for h, color in [(30, "#a855f7"), (90, "#ec4899")]:
            fd = (CURRENT_DATE + timedelta(days=h))
            pp = pred_pts.get(h, current_price)
            fig.add_annotation(
                x=fd, y=pp, text=f"+{h}d: ₹{pp:,}", showarrow=True,
                arrowhead=2, arrowcolor=color, font=dict(color=color, size=10),
                bgcolor=f"rgba(168,85,247,0.15)", bordercolor=color, borderwidth=1)

        fig.update_layout(
            template='plotly_dark',
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(rangeslider=dict(visible=True), type="date", title=""),
            yaxis=dict(title="₹/Quintal", tickprefix="₹", tickformat=","),
            hovermode="x unified", height=520,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)

        # Summary metrics below chart
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Current Price", f"₹ {int(current_price):,}")
        p7 = pred_pts.get(7, current_price)
        m2.metric("7-Day", f"₹ {p7:,}", f"{p7 - int(current_price):+,}")
        p30 = pred_pts.get(30, current_price)
        m3.metric("30-Day", f"₹ {p30:,}", f"{p30 - int(current_price):+,}")
        p60 = pred_pts.get(60, current_price)
        m4.metric("60-Day", f"₹ {p60:,}", f"{p60 - int(current_price):+,}")
        p90 = pred_pts.get(90, current_price)
        m5.metric("90-Day", f"₹ {p90:,}", f"{p90 - int(current_price):+,}")

    with tabs[1]:
        result = train_price_model()
        _, _, ml_metrics = result if result and len(result) == 3 else (None, None, None)
        ml_active = ml_metrics is not None

        st.subheader("🤖 Ensemble ML Prediction Engine")
        if ml_active:
            st.caption(f"**Ensemble (GBR + RandomForest + Ridge)** trained on **{ml_metrics['total_days']:,} days** of real mandi data ({ml_metrics['date_range']})")
        else:
            st.caption(f"Based on {'real historical data' if 'Real' in hist_source else 'synthetic history'} anchored to live price ₹{int(current_price):,}")

        col1, col2 = st.columns([3, 1])
        with col1:
            pred_plot = pred_df.copy()
            pred_plot["Date"] = pd.to_datetime(pred_plot["Date"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=pred_plot['Date'], y=pred_plot['Upper Bound'],
                                     fill=None, mode='lines', line_color='rgba(255,255,255,0)', showlegend=False))
            fig.add_trace(go.Scatter(x=pred_plot['Date'], y=pred_plot['Lower Bound'],
                                     fill='tonexty', mode='lines', line_color='rgba(255,255,255,0)',
                                     fillcolor='rgba(168,85,247,0.15)', name='Confidence Interval'))
            fig.add_trace(go.Scatter(x=pred_plot['Date'], y=pred_plot['Predicted Price'],
                                     mode='lines', name='ML Predicted Price', line=dict(color='#a855f7', width=3)))
            fig.add_vline(x=CURRENT_DATE, line_width=2, line_dash="dash", line_color="#10b981")
            fig.add_annotation(x=CURRENT_DATE, y=current_price,
                               text=f"Today ₹{int(current_price):,}",
                               showarrow=True, arrowhead=2, arrowcolor="#10b981",
                               font=dict(color="#10b981", size=11))
            fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                              paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified",
                              yaxis=dict(tickprefix="₹", tickformat=","))
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            p30 = pred_pts.get(30, current_price)
            p60 = pred_pts.get(60, current_price)
            p90 = pred_pts.get(90, current_price)
            st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
            st.metric("30-Day Forecast", f"₹ {p30:,}", f"{p30 - int(current_price):+,}")
            st.metric("60-Day Forecast", f"₹ {p60:,}", f"{p60 - int(current_price):+,}")
            st.metric("90-Day Forecast", f"₹ {p90:,}", f"{p90 - int(current_price):+,}")
            st.markdown("</div>", unsafe_allow_html=True)

            # ML Model Accuracy Card
            if ml_active:
                acc_pct = 100 - ml_metrics["mape"]
                acc_color = "#10b981" if acc_pct >= 95 else ("#f59e0b" if acc_pct >= 90 else "#ef4444")
                st.markdown(f"""<div class='metric-card' style='margin-top:12px;border-left:3px solid {acc_color};'>
                  <div style='font-size:.75rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.08em;'>🤖 Ensemble Accuracy</div>
                  <div style='font-size:1.8rem;font-weight:800;color:{acc_color};margin:6px 0;'>{acc_pct:.1f}%</div>
                  <div style='font-size:.78rem;color:#94a3b8;'>
                    MAE: <b style="color:#f1f5f9;">₹{ml_metrics["mae"]:,.0f}</b><br>
                    RMSE: <b style="color:#f1f5f9;">₹{ml_metrics["rmse"]:,.0f}</b><br>
                    MAPE: <b style="color:#f1f5f9;">{ml_metrics["mape"]:.2f}%</b><br>
                    R²: <b style="color:#f1f5f9;">{ml_metrics["r2"]:.4f}</b>
                  </div>
                </div>""", unsafe_allow_html=True)

        # ML Model Details (expandable)
        if ml_active:
            with st.expander("📊 Ensemble Model Details & Feature Importance", expanded=False):
                mi1, mi2 = st.columns(2)
                with mi1:
                    st.markdown("#### 🔧 Ensemble Configuration")
                    st.markdown(f"""
                    - **Algorithm:** Voting Ensemble (3 models)
                      - GBR: 400 trees, depth 5, lr 0.06 (weight 3)
                      - RandomForest: 300 trees, depth 12 (weight 2)
                      - Ridge: α=100 (weight 1)
                    - **Training Data:** {ml_metrics['train_size']:,} days
                    - **Test Data:** {ml_metrics['test_size']:,} days
                    - **Date Range:** {ml_metrics['date_range']}
                    - **Features:** {len(FEATURE_COLS)} engineered features
                    - **R² Score:** {ml_metrics['r2']:.4f}
                    """)
                with mi2:
                    st.markdown("#### 🏆 Top Feature Importances")
                    feat_names = [f[0] for f in ml_metrics["top_features"]]
                    feat_vals = [round(f[1] * 100, 1) for f in ml_metrics["top_features"]]
                    fig_feat = go.Figure(go.Bar(
                        x=feat_vals, y=feat_names, orientation='h',
                        marker_color=['#10b981' if v > 10 else '#3b82f6' for v in feat_vals],
                        text=[f"{v}%" for v in feat_vals], textposition="outside"
                    ))
                    fig_feat.update_layout(
                        template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=0, r=40, t=10, b=0),
                        xaxis=dict(title="Importance %"), yaxis=dict(autorange="reversed"),
                        height=340, showlegend=False
                    )
                    st.plotly_chart(fig_feat, use_container_width=True)

        st.subheader("Market Drivers")
        d_col1, d_col2, d_col3, d_col4, d_col5 = st.columns(5)
        d_col1.info("🌧️ Monsoon Forecast\n\n**Normal (98% LPA)**")
        d_col2.warning("📉 Global CBOT\n\n**Bearish Trend**")
        d_col3.success("📈 Domestic Demand\n\n**High (Soy Meal)**")
        d_col4.info(f"🏛️ Govt MSP\n\n**₹ {MSP:,}/qtl**")
        d_col5.warning("🏭 Inventory Levels\n\n**Above Average**")

    with tabs[2]:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Weather Impact Analysis")
            st.markdown("""
            *Simulated forecast for MP/Maharashtra Soybean Belt*
            - **Rainfall Deviation:** +5% (Normal)
            - **Temperature:** 28°C - 32°C (Optimal)
            - **Pest Alert:** Low risk of Yellow Mosaic Virus
            """)
            st.success("Overall Weather Risk: NEUTRAL TO POSITIVE for crop yield.")

        with col2:
            st.subheader(f"Live Mandi Prices — {selected_date.strftime('%d %b %Y')}")
            if price_ok and "market" in mandi_df.columns:
                # Show top mandis sorted by modal price
                disp = mandi_df[["market", "state", "modal_price", "min_price", "max_price"]].copy()
                disp = disp.rename(columns={
                    "market": "Mandi", "state": "State",
                    "modal_price": "Modal (₹)", "min_price": "Min (₹)", "max_price": "Max (₹)"
                })
                disp["vs MSP"] = disp["Modal (₹)"].apply(
                    lambda p: f"{(p - MSP) / MSP * 100:+.1f}%"
                )
                disp = disp.sort_values("Modal (₹)", ascending=False).head(15)
                for col in ["Modal (₹)", "Min (₹)", "Max (₹)"]:
                    disp[col] = disp[col].apply(lambda x: f"₹ {int(x):,}")
                st.dataframe(disp.reset_index(drop=True), hide_index=True, use_container_width=True)
                st.caption(f"Showing top 15 of {len(mandi_df)} mandis · Avg: ₹{int(current_price):,}/qtl")
            else:
                # Fallback table
                st.warning("⚠️ Using fallback prices — API unavailable")
                mandi_disp = pd.DataFrame([
                    {"Mandi": r["market"], "Price (₹)": int(r["modal_price"]),
                     "vs MSP": f"{(r['modal_price'] - MSP) / MSP * 100:+.1f}%"}
                    for _, r in mandi_df.iterrows()
                ])
                st.dataframe(mandi_disp, hide_index=True, use_container_width=True)


elif page == "💰 Investor Bid Board":
    st.title("Investor Marketplace")
    st.markdown("Bid on high-quality warehouse receipts backed by physical soybean collateral.")

    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown("### Portfolio Summary")
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("Total Invested", format_inr(2500000))
        st.metric("Expected Annual Return", "14.5%")
        st.metric("Active Bids", len(bids_df[bids_df['Status'] == 'Pending']))
        st.markdown("</div>", unsafe_allow_html=True)

        risk_dist = pd.DataFrame({'Risk Tier': ['Low', 'Medium', 'High'], 'Allocation': [60, 30, 10]})
        fig = px.pie(risk_dist, values='Allocation', names='Risk Tier', hole=0.5,
                     color='Risk Tier', color_discrete_map={'Low': '#10b981', 'Medium': '#f59e0b', 'High': '#ef4444'},
                     template='plotly_dark')
        fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0),
                          height=200, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Active Bidding Opportunities")
        opportunities = receipts_df[receipts_df['Status'] == 'Pending'].copy()
        opportunities = opportunities[['Receipt ID', 'Location', 'Quantity (qtl)', 'Market Value', 'Loan Amount', 'Risk Score']]
        st.dataframe(opportunities.head(10), use_container_width=True, hide_index=True)

        st.subheader("Recent Bids on Platform")

        def color_bid_status(val):
            color = 'white'
            if val == 'Accepted': color = '#10b981'
            elif val == 'Pending': color = '#f59e0b'
            elif val == 'Rejected': color = '#ef4444'
            return f'color: {color}'

        st.dataframe(bids_df.style.map(color_bid_status, subset=['Status']),
                     use_container_width=True, hide_index=True)


elif page == "🏪 Buyer Marketplace":
    st.title("Buyer Offtake Marketplace")
    st.markdown(f"Verified institutional buyers · Live reference price: **₹ {int(current_price):,}/qtl**")

    col1, col2, col3 = st.columns(3)
    with col1:
        prod_filter = st.selectbox("Product Requirement", ["All"] + list(buyers_df['Product'].unique()))
    with col2:
        min_price = st.number_input("Minimum Price (₹/qtl)", value=int(current_price) - 200)
    with col3:
        loc_filter = st.selectbox("Buyer Location", ["All"] + list(buyers_df['Location'].unique()))

    filtered_buyers = buyers_df.copy()
    if prod_filter != "All": filtered_buyers = filtered_buyers[filtered_buyers['Product'] == prod_filter]
    if loc_filter  != "All": filtered_buyers = filtered_buyers[filtered_buyers['Location'] == loc_filter]
    filtered_buyers = filtered_buyers[filtered_buyers['Price Offered (₹/qtl)'] >= min_price]

    st.dataframe(filtered_buyers, use_container_width=True, hide_index=True)

    st.markdown("### 🤝 Smart Matching Engine")
    sample_receipt = receipts_df.iloc[0]
    st.info(f"Looking for matches for **{sample_receipt['Receipt ID']}** "
            f"({sample_receipt['Quantity (qtl)']} qtl at {sample_receipt['Location']})")

    best_buyer = buyers_df.sort_values("Price Offered (₹/qtl)", ascending=False).iloc[0]
    alt_buyer  = buyers_df[buyers_df['Location'] == 'Indore'].sort_values("Price Offered (₹/qtl)", ascending=False).iloc[0]
    st.success(f"**Top Match:** {best_buyer['Company']} ({best_buyer['Location']}) — "
               f"Offers ₹{best_buyer['Price Offered (₹/qtl)']:,}/qtl. "
               f"Net realization ≈ ₹{best_buyer['Price Offered (₹/qtl)'] - 150:,}/qtl (after logistics)")
    st.success(f"**Alternative:** {alt_buyer['Company']} (Indore) — "
               f"Offers ₹{alt_buyer['Price Offered (₹/qtl)']:,}/qtl. "
               f"Net realization ≈ ₹{alt_buyer['Price Offered (₹/qtl)'] - 250:,}/qtl")


elif page == "🌾 Soybean Intelligence":
    # ══════════════════════════════════════════════════════════════
    # SOYBEAN INTELLIGENCE PAGE — full mirror of SoyMandi predictor
    # ══════════════════════════════════════════════════════════════

    def _fmt(n):
        try:   return f"₹{int(round(n)):,}"
        except: return f"₹{n}"

    # State filter (only on this page)
    si_state = st.sidebar.selectbox(
        "🗺️ Filter State (Intelligence)",
        ["All States", "Madhya Pradesh", "Maharashtra", "Rajasthan",
         "Karnataka", "Telangana", "Gujarat", "Andhra Pradesh"],
        key="si_state"
    )
    si_horizon = st.sidebar.slider("🔭 Forecast Days", 30, 120, 90, step=15, key="si_horizon")

    # Apply state filter to mandi_df
    si_mandi = mandi_df.copy()
    if si_state != "All States" and "state" in si_mandi.columns:
        si_mandi = si_mandi[si_mandi["state"] == si_state]
    if len(si_mandi) == 0:
        si_mandi = mandi_df.copy()   # fallback to all if filter gives nothing

    si_avg   = float(si_mandi["modal_price"].mean())
    si_max   = float(si_mandi["modal_price"].max())
    si_min   = float(si_mandi["modal_price"].min())
    si_top   = si_mandi.loc[si_mandi["modal_price"].idxmax(), "market"] if "market" in si_mandi.columns else "—"

    # History & predictions
    si_hist  = hist_df.copy()
    si_hist["Price"] = si_hist["Price"].astype(float)  # int column can't hold a float avg in pandas 3
    si_hist.loc[si_hist.index[-1], "Price"] = si_avg
    si_horizons = [7, 15, 30, 60, si_horizon]
    si_pts, si_pred_df = make_prediction(si_hist, si_horizons, si_horizon)

    si_prev    = float(si_hist["Price"].iloc[-2]) if len(si_hist) >= 2 else si_avg
    si_day_chg = (si_avg - si_prev) / si_prev * 100 if si_prev else 0

    today_str  = CURRENT_DATE.strftime("%Y-%m-%d")
    sel_str_si = selected_date.strftime("%d %B %Y")
    sel_day_si = selected_date.strftime("%A")
    src_si     = "🟢 LIVE" if is_today and price_ok else ("🔵 SAVED" if price_ok else "🟡 SIMULATED")
    dot_col_si = "#10b981" if is_today else "#3b82f6"
    mode_si    = "LIVE" if is_today else "HISTORICAL"

    # ── Hero banner
    st.markdown(f"""
    <div class="si-hero">
      <div class="si-hero-title">🌾 Soybean Intelligence</div>
      <div class="si-hero-sub">
        <span class="live-dot" style="background:{dot_col_si};"></span>
        <strong style="color:{dot_col_si};">{mode_si}</strong> &nbsp;·&nbsp;
        Indian Soybean Mandi Prices &nbsp;·&nbsp;
        <strong style="color:#e2e8f0;">{sel_str_si}</strong> &nbsp;·&nbsp;
        <span style="color:#64748b;">{sel_day_si}</span>
        {f"&nbsp;·&nbsp; <span style='color:#94a3b8;'>{si_state}</span>" if si_state != 'All States' else ''}
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Source bar
    hist_lbl_si = hist_source  # Already set from the real data loading logic above
    st.markdown(f"""
    <div style="text-align:right;font-size:.78rem;color:#94a3b8;margin-bottom:12px;">
      <strong style="color:{'#10b981' if is_today and price_ok else '#3b82f6'}">{src_si}</strong>
      &nbsp;·&nbsp; {len(si_mandi)} mandis
      &nbsp;·&nbsp; Chart: {hist_lbl_si}
    </div>""", unsafe_allow_html=True)

    # ── KPI Cards
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    cc = "g" if si_day_chg >= 0 else "r"
    ca = "▲" if si_day_chg >= 0 else "▼"

    with c1:
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">📅 Viewing Date</div>
          <div class="kpi-val" style="font-size:1.3rem;">{selected_date.strftime("%d %b %Y")}</div>
          <div class="kpi-sub" style="color:#94a3b8;">{sel_day_si}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">🌾 Avg Modal</div>
          <div class="kpi-val">{_fmt(si_avg)}</div>
          <div class="kpi-sub" style="color:#64748b;">/Quintal</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">📈 Day Change</div>
          <div class="kpi-val {cc}">{ca} {abs(si_day_chg):.2f}%</div>
          <div class="kpi-sub" style="color:#64748b;">vs prev day</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        p30 = si_pts.get(30, si_avg); d30 = p30 - si_avg
        dc  = "g" if d30 >= 0 else "r"; da = "▲" if d30 >= 0 else "▼"
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">🔮 30D Forecast</div>
          <div class="kpi-val bl">{_fmt(p30)}</div>
          <div class="kpi-sub {dc}">{da} {_fmt(abs(d30))}</div>
        </div>""", unsafe_allow_html=True)
    with c5:
        ph = si_pts.get(si_horizon, si_avg); dh = ph - si_avg
        dc  = "g" if dh >= 0 else "r"; da = "▲" if dh >= 0 else "▼"
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">🔭 {si_horizon}D Forecast</div>
          <div class="kpi-val pu">{_fmt(ph)}</div>
          <div class="kpi-sub {dc}">{da} {_fmt(abs(dh))}</div>
        </div>""", unsafe_allow_html=True)
    with c6:
        md = si_avg - MSP; mc = "g" if md >= 0 else "r"; ma = "▲" if md >= 0 else "▼"
        st.markdown(f"""<div class="kpi">
          <div class="kpi-label">🏛️ vs MSP</div>
          <div class="kpi-val {mc}">{ma} {_fmt(abs(md))}</div>
          <div class="kpi-sub {mc}">{'Above' if md >= 0 else 'Below'} ₹{MSP:,}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════ TABS ══════════════════════════════════════════════
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📈 Chart & Forecast", "🏪 Mandi Prices", "🔢 Forecast Table", "📊 Signals"]
    )

    # ── TAB 1: Chart & Forecast
    with tab1:
        col_ch, col_inf = st.columns([3, 1])
        with col_ch:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=pd.to_datetime(si_hist["Date"]).dt.strftime("%Y-%m-%d"),
                y=si_hist["Price"],
                name="Historical", line=dict(color="#3b82f6", width=2),
                hovertemplate="<b>%{x}</b><br>₹%{y:,.0f}/qtl<extra></extra>",
            ))
            # Confidence band
            pred_dates_str = pd.to_datetime(si_pred_df["Date"]).dt.strftime("%Y-%m-%d").tolist()
            fig.add_trace(go.Scatter(
                x=pred_dates_str + pred_dates_str[::-1],
                y=list(si_pred_df["Upper Bound"]) + list(si_pred_df["Lower Bound"])[::-1],
                fill="toself", fillcolor="rgba(168,85,247,0.10)",
                line=dict(color="rgba(0,0,0,0)"), name="Confidence", hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=pred_dates_str, y=si_pred_df["Predicted Price"],
                name="AI Forecast", line=dict(color="#a855f7", width=2.5, dash="dot"),
                hovertemplate="<b>%{x}</b><br>₹%{y:,.0f}<extra></extra>",
            ))
            fig.add_hline(y=MSP, line_dash="dash", line_color="#f59e0b", line_width=1.5,
                          annotation_text=f"MSP ₹{MSP:,}", annotation_font_color="#f59e0b",
                          annotation_position="bottom right")
            fig.add_shape(type="line", x0=today_str, x1=today_str, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color="#10b981", width=2, dash="dash"))
            fig.add_annotation(x=today_str, y=1, xref="x", yref="paper", text="Today",
                               showarrow=False, font=dict(color="#10b981", size=11), yanchor="bottom")
            for h in [30, si_horizon]:
                fd  = (CURRENT_DATE + timedelta(days=h)).strftime("%Y-%m-%d")
                pp  = si_pts.get(h, si_avg)
                fig.add_annotation(
                    x=fd, y=pp, text=f"+{h}d: {_fmt(pp)}", showarrow=True,
                    arrowhead=2, arrowcolor="#a855f7", font=dict(color="#a855f7", size=10),
                    bgcolor="rgba(168,85,247,0.18)", bordercolor="#a855f7", borderwidth=1
                )
            fig.update_layout(
                template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                hovermode="x unified", legend=dict(orientation="h", y=1.07, x=0),
                margin=dict(l=0, r=0, t=40, b=0),
                yaxis=dict(title="₹/Quintal", tickprefix="₹", tickformat=","),
                xaxis=dict(title="", rangeslider=dict(visible=True)), height=470
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_inf:
            hist_info_si = hist_source
            st.markdown(f"""
            <div class="infobox">
              <b style="color:#f1f5f9;">📌 Chart Legend</b><br><br>
              🔵 Historical &nbsp; 🟣 Forecast &nbsp; 🟡 MSP &nbsp; 🟢 Today<br><br>
              <hr style="border-color:rgba(255,255,255,.06);">
              <b style="color:#f1f5f9;">📊 Data Source</b><br>{hist_info_si}<br><br>
              <b style="color:#f1f5f9;">📍 Region</b><br>
              Maharashtra · 27 Districts<br><br>
              <b style="color:#f1f5f9;">💾 Live Prices</b><br>
              Today's API data is saved as snapshots for future reference.<br><br>
              <span style="color:#f59e0b;font-size:.8rem;">⚠️ Not financial advice</span>
            </div>""", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            above = max(0, si_avg - MSP)
            fig_d = go.Figure(go.Pie(
                values=[MSP, above], labels=["MSP", "Premium"], hole=0.6,
                marker_colors=["#f59e0b", "#10b981"], textinfo="none"
            ))
            fig_d.update_layout(
                template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(t=10, b=10, l=10, r=10), height=170, showlegend=True,
                legend=dict(font=dict(size=10), orientation="h", y=-0.15),
                annotations=[dict(text=f"<b>{_fmt(si_avg)}</b>", x=0.5, y=0.5,
                                  font_size=13, showarrow=False, font_color="#f1f5f9")]
            )
            st.plotly_chart(fig_d, use_container_width=True)

    # ── TAB 2: Mandi Prices
    with tab2:
        st.markdown(f"### 🏪 Mandi Prices — {sel_str_si}")
        col_l, col_r = st.columns([3, 2])
        with col_l:
            for _, row in si_mandi.sort_values("modal_price", ascending=False).iterrows():
                modal = row["modal_price"]
                lo    = row.get("min_price", modal - 100)
                hi    = row.get("max_price", modal + 100)
                diff  = modal - si_avg
                dc    = "#10b981" if diff >= 0 else "#ef4444"
                dl    = f"▲ {_fmt(abs(diff))}" if diff >= 0 else f"▼ {_fmt(abs(diff))}"
                badge = "🏆 Highest" if modal == si_max else ("📉 Lowest" if modal == si_min else "")
                bh    = (f'<span style="font-size:.72rem;background:rgba(16,185,129,.2);'
                         f'color:#10b981;padding:2px 8px;border-radius:10px;margin-left:6px;">'
                         f'{badge}</span>') if badge else ""
                st.markdown(f"""
                <div class="mrow">
                  <div>
                    <div style="font-weight:700;color:#f1f5f9;font-size:.95rem;">{row.get('market','—')} {bh}</div>
                    <div style="font-size:.78rem;color:#94a3b8;margin-top:3px;">{row.get('state','—')} · {row.get('district','—')}</div>
                    <div style="font-size:.75rem;color:#334155;margin-top:2px;">Range: ₹{int(lo):,} – ₹{int(hi):,}</div>
                  </div>
                  <div style="text-align:right;">
                    <div style="font-size:1.4rem;font-weight:800;color:#f1f5f9;">₹{int(modal):,}</div>
                    <div style="font-size:.8rem;color:{dc};font-weight:600;">{dl} vs avg</div>
                  </div>
                </div>""", unsafe_allow_html=True)
        with col_r:
            sb = si_mandi.sort_values("modal_price")
            cb = ["#10b981" if v >= si_avg else "#3b82f6" for v in sb["modal_price"]]
            fig_b = go.Figure(go.Bar(
                x=sb["modal_price"], y=sb.get("market", sb.index), orientation="h",
                marker_color=cb,
                text=[f"₹{int(v):,}" for v in sb["modal_price"]], textposition="outside",
                hovertemplate="<b>%{y}</b><br>₹%{x:,}<extra></extra>"
            ))
            fig_b.add_vline(x=si_avg, line_dash="dash", line_color="#f59e0b", line_width=1.5)
            fig_b.update_layout(
                template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=60, t=20, b=0),
                xaxis=dict(tickprefix="₹", tickformat=",",
                           range=[sb["modal_price"].min() * 0.96, sb["modal_price"].max() * 1.04]),
                height=max(350, len(si_mandi) * 28), showlegend=False
            )
            st.plotly_chart(fig_b, use_container_width=True)
            st.markdown(f"""<div class="infobox" style="margin-top:8px;">
              <b style="color:#f1f5f9;">📊 Summary</b><br>
              🏆 Highest: <b style="color:#10b981;">{si_top} — {_fmt(si_max)}</b><br>
              📉 Lowest: <b style="color:#ef4444;">{_fmt(si_min)}</b><br>
              📊 Average: <b style="color:#f1f5f9;">{_fmt(si_avg)}</b><br>
              📦 Mandis: <b style="color:#3b82f6;">{len(si_mandi)}</b>
            </div>""", unsafe_allow_html=True)

    # ── TAB 3: Forecast Table
    with tab3:
        st.markdown("### 🔢 AI Forecast")
        all_h  = [1, 7, 15, 30, 45, 60, 75, 90]
        pa, _  = make_prediction(si_hist, all_h, 90)
        col_a, col_b = st.columns(2)
        with col_a:
            for h in all_h:
                fd   = (CURRENT_DATE + timedelta(days=h)).strftime("%d %b %Y")
                p    = pa[h]
                diff = p - si_avg
                pct  = diff / si_avg * 100
                col  = "#10b981" if diff >= 0 else "#ef4444"
                arr  = "▲" if diff >= 0 else "▼"
                st.markdown(f"""<div class="prow">
                  <div><div style="font-weight:700;color:#f1f5f9;">+{h} Days</div>
                  <div style="font-size:.78rem;color:#94a3b8;">{fd}</div></div>
                  <div style="text-align:right;">
                    <div style="font-size:1.25rem;font-weight:800;color:#f1f5f9;">₹{p:,}</div>
                    <div style="font-size:.8rem;color:{col};font-weight:600;">{arr} ₹{abs(diff):,} ({pct:+.1f}%)</div>
                  </div>
                </div>""", unsafe_allow_html=True)
        with col_b:
            vals = [pa[h] for h in all_h]
            cb   = ["#10b981" if v >= si_avg else "#ef4444" for v in vals]
            fig_fc = go.Figure(go.Bar(
                x=[f"+{h}d" for h in all_h], y=vals, marker_color=cb,
                text=[f"₹{v:,}" for v in vals], textposition="outside"
            ))
            fig_fc.add_hline(
                y=si_avg, line_dash="dash", line_color="#f59e0b", line_width=2,
                annotation_text=f"Today: ₹{int(si_avg):,}", annotation_font_color="#f59e0b"
            )
            fig_fc.update_layout(
                template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis=dict(tickprefix="₹", tickformat=",",
                           range=[min(vals) * 0.97, max(vals) * 1.04]),
                height=420, showlegend=False
            )
            st.plotly_chart(fig_fc, use_container_width=True)

    # ── TAB 4: Market Signals
    with tab4:
        st.markdown("### 📊 Market Signals")
        s1, s2, s3 = st.columns(3)
        recent30 = si_hist["Price"].tail(30).values
        if len(recent30) < 30:
            recent30 = np.pad(recent30, (30 - len(recent30), 0), mode="edge")
        slope = np.polyfit(np.arange(30), recent30, 1)[0]
        if   slope > 25:  tl, tc, ti = "STRONG UP",   "#10b981", "🚀"
        elif slope > 0:   tl, tc, ti = "MILD UP",     "#3b82f6", "📈"
        elif slope > -25: tl, tc, ti = "MILD DOWN",   "#f59e0b", "📉"
        else:             tl, tc, ti = "STRONG DOWN",  "#ef4444", "⚠️"
        with s1:
            st.markdown(f"""<div class="kpi" style="padding:24px;">
              <div class="kpi-label">Trend</div><div style="font-size:2.2rem;">{ti}</div>
              <div style="font-size:.95rem;font-weight:700;color:{tc};margin-top:8px;">{tl}</div>
              <div style="font-size:.78rem;color:#94a3b8;margin-top:4px;">₹{slope:+.1f}/day</div>
            </div>""", unsafe_allow_html=True)
        vol = si_hist["Price"].tail(30).std()
        if   vol < 100: vl, vc = "LOW",      "#10b981"
        elif vol < 200: vl, vc = "MODERATE", "#f59e0b"
        else:           vl, vc = "HIGH",      "#ef4444"
        with s2:
            st.markdown(f"""<div class="kpi" style="padding:24px;">
              <div class="kpi-label">Volatility</div>
              <div class="kpi-val" style="color:{vc};">₹{vol:.0f}</div>
              <div style="font-size:.95rem;font-weight:700;color:{vc};margin-top:6px;">{vl}</div>
            </div>""", unsafe_allow_html=True)
        p7  = float(si_hist["Price"].iloc[-8]) if len(si_hist) >= 8 else si_avg
        mom = (si_avg - p7) / p7 * 100 if p7 else 0
        mc  = "#10b981" if mom >= 0 else "#ef4444"
        with s3:
            st.markdown(f"""<div class="kpi" style="padding:24px;">
              <div class="kpi-label">7D Momentum</div>
              <div class="kpi-val" style="color:{mc};">{mom:+.2f}%</div>
              <div style="font-size:1.5rem;margin-top:6px;">{'📈' if mom >= 0 else '📉'}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        col_sea, col_drv = st.columns(2)
        with col_sea:
            st.markdown("#### 🌦️ Season")
            m = CURRENT_DATE.month
            if   m in [10, 11, 12]: ctx, clr = "🌾 **Kharif Harvest** — Arrivals surge. Prices dip.",         "#ef4444"
            elif m in [6, 7, 8]:    ctx, clr = "🌱 **Sowing Season** — Old stock low. Prices firm.",           "#10b981"
            elif m in [1, 2, 3]:    ctx, clr = "📦 **Post-Harvest** — Active selling. Soft prices.",           "#f59e0b"
            else:                   ctx, clr = "⏳ **Pre-Harvest** — Stocks low. Prices may recover.",          "#3b82f6"
            st.markdown(f"""<div style="background:rgba(15,30,51,0.7);border:1px solid rgba(255,255,255,.06);
                border-left:4px solid {clr};border-radius:12px;padding:20px;">
              <p style="color:#e2e8f0;font-size:.92rem;margin:0;">{ctx}</p></div>""", unsafe_allow_html=True)
        with col_drv:
            st.markdown("#### 📰 Key Drivers")
            for ico, nm, val, cl in [
                ("🏛️", "MSP",     f"₹{MSP:,}",                              "#f59e0b"),
                ("🏆", "Best",    f"{si_top}: {_fmt(si_max)}",               "#10b981"),
                ("📊", "Avg",     _fmt(si_avg),                              "#3b82f6"),
                ("📦", "Premium", _fmt(max(0, si_avg - MSP)),                "#10b981"),
                ("📈", "30D Fcst",_fmt(si_pts.get(30, si_avg)),              "#a855f7"),
                ("🔭", f"{si_horizon}D Fcst", _fmt(si_pts.get(si_horizon, si_avg)), "#a855f7"),
            ]:
                st.markdown(f"""<div style="display:flex;align-items:center;gap:12px;
                    padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
                  <span style="font-size:1.2rem;">{ico}</span>
                  <div style="flex:1;font-weight:600;color:#f1f5f9;font-size:.85rem;">{nm}</div>
                  <div style="font-weight:700;color:{cl};font-size:.9rem;">{val}</div>
                </div>""", unsafe_allow_html=True)

        # Saved date history strip
        if saved_dates:
            st.markdown("<br>#### 📁 Saved Data History", unsafe_allow_html=True)
            st.markdown(
                f"`{len(saved_dates)}` snapshots saved locally: "
                + " · ".join(f"`{d.strftime('%d %b %Y')}`" for d in saved_dates[:10])
                + (" · ..." if len(saved_dates) > 10 else "")
            )


elif page == "⚠️ Risk Assessment":
    st.title("🛡️ AgriCred Risk Assessment Engine")
    st.markdown("""
    <div class='metric-card' style='border-left:4px solid #3b82f6;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        <b>6-Pillar Data-Driven Risk Scoring</b> — Price Level · Volatility · Warehouse · LTV/Quality · Crop Yield · Borrower Credit
      </span>
    </div>""", unsafe_allow_html=True)

    if not RISK_ENGINE_AVAILABLE:
        st.error("⚠️ Risk engine module not found. Please ensure `risk_engine.py`, `volatility_model.py`, and `yield_risk_model.py` are in the app directory.")
        st.stop()

    # ── Receipt selector ──
    selected_receipt = st.selectbox("🧾 Select Warehouse Receipt for Risk Analysis", receipts_df['Receipt ID'])
    rd = receipts_df[receipts_df['Receipt ID'] == selected_receipt].iloc[0]

    # ── Build input dicts for the engine ──
    receipt_input = {
        'receipt_id':           rd['Receipt ID'],
        'farmer_name':          rd['Farmer Name'],
        'location':             rd['Location'],
        'quantity':             float(rd['Quantity (qtl)']),
        'grade':                rd['Grade'],
        'deposit_date':         rd['Deposit Date'],
        'market_value':         float(rd['Market Value']),
        'loan_amount':          float(rd['Loan Amount']),
        'deposit_price':        float(rd.get('Deposit Price', current_price)),
        'wdra_registered':      bool(rd.get('WDRA Registered', True)),
        'insurance_coverage':   rd.get('Insurance', 'partial'),
        'has_collateral_manager': bool(rd.get('Collateral Manager', True)),
        'storage_days':         int(rd.get('Storage Days', 30)),
        # ── New risk-roadmap fields ──
        'wdra_registration_number': rd.get('WDRA Reg Number'),
        'last_inspection_date':     rd.get('Last Inspection'),
        'insurance_expiry_date':    rd.get('Insurance Expiry'),
        'sum_insured':              float(rd.get('Sum Insured', 0)) or None,
        'moisture_level':           float(rd.get('Moisture %', 11.0)),
        'oil_content_pct':          float(rd.get('Oil Content %')) if pd.notna(rd.get('Oil Content %')) else None,
        'protein_content_pct':      float(rd.get('Protein Content %')) if pd.notna(rd.get('Protein Content %')) else None,
        'foreign_matter_pct':       float(rd.get('Foreign Matter %')) if pd.notna(rd.get('Foreign Matter %')) else None,
    }
    borrower_input = {
        'total_transactions':    int(rd.get('Borrower Txns', 10)),
        'successful_transactions': int(rd.get('Borrower Success', 9)),
        'years_on_platform':    int(rd.get('Years on Platform', 2)),
        'kyc_verified':         bool(rd.get('KYC Verified', True)),
        'is_fpo_member':        bool(rd.get('FPO Member', False)),
        'past_defaults':        int(rd.get('Past Defaults', 0)),
    }

    # ── Use a VERIFIED borrower (from the Borrower Verification page) if present ──
    verified_borrower = st.session_state.get("verified_borrower")
    borrower_is_verified = False
    if verified_borrower:
        use_vb = st.checkbox(
            f"🧾 Use verified borrower — run `{verified_borrower['run_id']}` "
            f"(FOIR {verified_borrower.get('foir')}, bureau {verified_borrower.get('bureau_score')}, "
            f"{verified_borrower.get('bounce_count')} bounces)",
            value=True, key="use_verified_borrower")
        if use_vb:
            borrower_input = dict(verified_borrower["borrower_input"])
            borrower_is_verified = True

    # ── Build ML predictions dict from existing forecast ──
    ml_predictions = {}
    if pred_pts:
        for k, v in pred_pts.items():
            ml_predictions[f"{k}d"] = v

    # ── Get price history as Series for volatility model ──
    price_series = hist_df['Price'].astype(float) if hist_df is not None else None

    # ── Best-effort real rainfall / production signals (roadmap #5, #8, #12) ──
    # Both calls are cached (24h) and fail silently to None on any network/API
    # issue, so a bad/slow connection just falls back to the synthetic
    # yield_risk_model path — same fallback discipline as the price data above.
    real_rain_dev = None
    production_signal = None
    if AGRI_DATA_SOURCES_AVAILABLE:
        try:
            rain_df = fetch_district_rainfall(api_key, state=None, use_cache=True)
            if rain_df is not None:
                from yield_risk_model import DISTRICT_PROFILES
                district_profile = DISTRICT_PROFILES.get(rd['Location'])
                if district_profile:
                    real_rain_dev = compute_district_rainfall_deviation(
                        rain_df, rd['Location'], district_profile['normal_rainfall'],
                        window_days=receipt_input['storage_days'] or 90,
                    )
        except Exception:
            real_rain_dev = None
        try:
            prod_df = fetch_soybean_production(api_key, use_cache=True)
            if prod_df is not None:
                production_signal = compute_production_yoy_change(prod_df)
        except Exception:
            production_signal = None

    # ── 🚀 Run the Risk Engine ──
    with st.spinner("🔄 Computing 6-pillar risk score..."):
        result = compute_composite_risk_score(
            current_price=current_price,
            msp=_msp_for_date(selected_date),
            receipt_data=receipt_input,
            live_price=current_price,
            predicted_prices=ml_predictions,
            price_history=price_series,
            borrower_data=borrower_input,
            real_rainfall_deviation_pct=real_rain_dev,
            production_signal=production_signal,
        )

    # ── Fold the VERIFIED borrower pillar into the composite ──
    if borrower_is_verified and isinstance(result.get('pillar_scores', {}).get('borrower'), dict):
        _vb = verified_borrower
        _bw = 0.10  # borrower pillar weight in the composite
        _old_b = float(result['pillar_scores']['borrower'].get('score', 50))
        _new_b = float(_vb.get('pillar_enhanced', _old_b))
        result['composite_score'] = round(
            min(100.0, max(0.0, float(result.get('composite_score', 50)) + (_new_b - _old_b) * _bw)), 2)
        result['pillar_scores']['borrower']['score'] = _new_b
        result['pillar_scores']['borrower']['weighted_score'] = round(_new_b * _bw, 2)
        result['pillar_scores']['borrower']['details'] = (
            f"Verified borrower (run {_vb['run_id']}): FOIR {_vb.get('foir')}, "
            f"bureau {_vb.get('bureau_score')}, {_vb.get('bounce_count')} bounces"
            + (" — write-off/suit flag" if _vb.get('bureau_write_off') else ""))
        _cs = result['composite_score']
        result['risk_grade'], result['recommendation'], result['suggested_interest_range'] = (
            ('Low', 'Auto-Approve', '10-12%') if _cs <= 25 else
            ('Medium', 'Standard Review', '13-15%') if _cs <= 50 else
            ('Elevated', 'Enhanced Diligence', '15-17%') if _cs <= 75 else
            ('High', 'Reject / Additional Collateral', '18%+'))

    composite = result.get('composite_score', 50)
    grade_str = result.get('risk_grade', 'Medium')
    recommendation = result.get('recommendation', 'Review')
    interest = result.get('suggested_interest_range', '13-15%')
    pillars = result.get('pillar_scores', {})
    if borrower_is_verified:
        st.info(f"🧾 Borrower pillar replaced with the **verified** borrower "
                f"(run `{verified_borrower['run_id']}`) — composite re-weighted accordingly.")

    # ── Data provenance for THIS receipt's score ──────────────────────────
    prov_summary = None
    if PROVENANCE_AVAILABLE:
        try:
            prov_summary = provenance.summarize(
                rd.to_dict(),
                rd.get('Source', 'Synthetic'),
                rainfall_is_real=(real_rain_dev is not None),
                production_is_real=(production_signal is not None),
                yield_history_is_real=False,
            )
        except Exception as _pe:
            prov_summary = None

    if prov_summary is not None:
        ov = prov_summary['overall']
        gcol = provenance.GRADE_COLORS.get(ov['grade'], '#f59e0b')
        c = ov['counts']
        st.markdown(f"""
        <div class='metric-card' style='border-left:4px solid {gcol};padding:14px 18px;margin-bottom:4px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;'>
            <div style='font-size:1.05rem;font-weight:800;color:{gcol};'>
              🔎 Data Provenance — {ov['grade']} · {ov['verified_pct']:.0f}% verified inputs
            </div>
            <div style='font-size:.8rem;color:#94a3b8;'>
              🟢 {c['real']} live &nbsp;·&nbsp; 🔵 {c['manual']} operator &nbsp;·&nbsp;
              🟣 {c['reference']} reference &nbsp;·&nbsp; 🟠 {c['estimated']} estimated
            </div>
          </div>
          <div style='font-size:.78rem;color:#64748b;margin-top:6px;'>
            {"This receipt was entered by an operator — its lab / WDRA / insurance fields are real."
             if str(rd.get('Source','')).lower() == 'manual' else
             "This is a <b>synthetic demo receipt</b>. Add a real receipt (➕ sidebar) to score a genuine lot."}
            The composite score is computed identically regardless of source; this bar shows how much of it rests on verified data.
          </div>
        </div>""", unsafe_allow_html=True)

    # ── Color mapping ──
    grade_colors = {'Low': '#10b981', 'Medium': '#f59e0b', 'Elevated': '#f97316', 'High': '#ef4444'}
    grade_icons  = {'Low': '🟢', 'Medium': '🟡', 'Elevated': '🟠', 'High': '🔴'}
    gc = grade_colors.get(grade_str, '#f59e0b')
    gi = grade_icons.get(grade_str, '🟡')

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 1: Gauge + Summary Card + Receipt Info
    # ══════════════════════════════════════════════════════════════════════════
    col_gauge, col_summary, col_receipt = st.columns([1, 1, 1])

    with col_gauge:
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=composite,
            number={'suffix': '/100', 'font': {'size': 36}},
            title={'text': "Composite Risk Score", 'font': {'size': 14}},
            gauge={
                'axis': {'range': [0, 100], 'tickwidth': 1},
                'bar':  {'color': gc, 'thickness': 0.3},
                'bgcolor': 'rgba(0,0,0,0)',
                'steps': [
                    {'range': [0, 25],   'color': 'rgba(16,185,129,0.2)'},
                    {'range': [25, 50],  'color': 'rgba(249,199,79,0.2)'},
                    {'range': [50, 75],  'color': 'rgba(249,115,22,0.2)'},
                    {'range': [75, 100], 'color': 'rgba(239,68,68,0.2)'},
                ],
                'threshold': {'line': {'color': 'white', 'width': 3}, 'thickness': 0.8, 'value': composite}
            }
        ))
        fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                          paper_bgcolor='rgba(0,0,0,0)', height=280, margin=dict(t=60,b=20,l=30,r=30))
        st.plotly_chart(fig, use_container_width=True)

    with col_summary:
        st.markdown(f"""
        <div class='metric-card' style='border-left:4px solid {gc};padding:20px;'>
          <div style='font-size:1.6rem;font-weight:800;color:{gc};'>{gi} {grade_str} Risk</div>
          <div style='font-size:.85rem;color:#94a3b8;margin-top:8px;'>
            <b>Recommendation:</b> {recommendation}<br>
            <b>Suggested Interest:</b> {interest}<br>
            <b>Receipt:</b> {rd['Receipt ID']}<br>
            <b>Farmer:</b> {rd['Farmer Name']}<br>
            <b>Location:</b> {rd['Location']}
          </div>
        </div>""", unsafe_allow_html=True)

        # Risk factors & mitigants
        risk_factors = result.get('risk_factors', [])
        risk_mitigants = result.get('risk_mitigants', [])
        if risk_factors:
            st.markdown(f"""
            <div class='metric-card' style='margin-top:8px;border-left:4px solid #ef4444;padding:12px 16px;'>
              <div style='font-size:.75rem;color:#ef4444;font-weight:700;text-transform:uppercase;'>⚠ Top Risk Factors</div>
              <div style='font-size:.82rem;color:#e2e8f0;margin-top:4px;'>{'<br>'.join(f"• {f}" for f in risk_factors)}</div>
            </div>""", unsafe_allow_html=True)
        if risk_mitigants:
            st.markdown(f"""
            <div class='metric-card' style='margin-top:8px;border-left:4px solid #10b981;padding:12px 16px;'>
              <div style='font-size:.75rem;color:#10b981;font-weight:700;text-transform:uppercase;'>✅ Strongest Factors</div>
              <div style='font-size:.82rem;color:#e2e8f0;margin-top:4px;'>{'<br>'.join(f"• {m}" for m in risk_mitigants)}</div>
            </div>""", unsafe_allow_html=True)

    with col_receipt:
        ltv = rd['Loan Amount'] / rd['Market Value'] * 100 if rd['Market Value'] > 0 else 0
        live_mktval = rd['Quantity (qtl)'] * current_price
        live_ltv = rd['Loan Amount'] / live_mktval * 100 if live_mktval > 0 else 0
        margin_dist = (live_mktval - rd['Loan Amount']) / rd['Loan Amount'] * 100 if rd['Loan Amount'] > 0 else 0
        st.markdown(f"""
        <div class='metric-card' style='padding:16px;'>
          <div style='font-size:.78rem;color:#64748b;font-weight:700;text-transform:uppercase;margin-bottom:10px;'>📊 Collateral Overview</div>
          <div style='font-size:.85rem;color:#e2e8f0;line-height:1.8;'>
            Qty: <b>{rd['Quantity (qtl)']} qtl</b> · Grade: <b style='color:{"#10b981" if rd["Grade"]=="A" else "#f59e0b" if rd["Grade"]=="B" else "#ef4444"}'>{rd['Grade']}</b><br>
            Deposit Price: <b>₹{int(rd.get('Deposit Price', current_price)):,}/qtl</b><br>
            Live Price: <b style='color:#10b981;'>₹{int(current_price):,}/qtl</b><br>
            Market Value: <b>₹{int(rd['Market Value']):,}</b><br>
            Live Mkt Val: <b>₹{int(live_mktval):,}</b><br>
            Loan Amount: <b>₹{int(rd['Loan Amount']):,}</b><br>
            LTV: <b style='color:{"#10b981" if ltv < 70 else "#f59e0b" if ltv < 80 else "#ef4444"}'>{ltv:.1f}%</b>
            → Live: <b style='color:{"#10b981" if live_ltv < 70 else "#f59e0b" if live_ltv < 80 else "#ef4444"}'>{live_ltv:.1f}%</b><br>
            Margin Call Buffer: <b style='color:{"#10b981" if margin_dist > 20 else "#f59e0b" if margin_dist > 10 else "#ef4444"}'>{margin_dist:.1f}%</b><br>
            Storage: <b>{rd.get('Storage Days', 'N/A')} days</b> · WDRA: <b style='color:{"#10b981" if rd.get("WDRA Registered") else "#ef4444"}'>{"✓" if rd.get("WDRA Registered") else "✗"}</b>
          </div>
        </div>""", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 2: 6-Pillar Breakdown
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏗️ 6-Pillar Risk Breakdown")

    pillar_display = [
        ('price_level',  '💰 Price Level',       '20%'),
        ('volatility',   '📉 Price Volatility',  '25%'),
        ('warehouse',    '🏭 Warehouse/Storage', '15%'),
        ('ltv_quality',  '🔒 LTV & Quality',     '15%'),
        ('crop_yield',   '🌾 Crop Yield/Weather','15%'),
        ('borrower',     '👤 Borrower Credit',   '10%'),
    ]

    # Horizontal bar chart of all pillars
    pillar_names = []
    pillar_scores_list = []
    pillar_weighted = []
    for key, name, wt in pillar_display:
        p = pillars.get(key, {})
        pillar_names.append(name)
        pillar_scores_list.append(p.get('score', 50))
        pillar_weighted.append(p.get('weighted_score', 0))

    fig_pillars = go.Figure()
    fig_pillars.add_trace(go.Bar(
        y=pillar_names[::-1], x=pillar_scores_list[::-1],
        orientation='h', name='Raw Score',
        marker_color=[
            '#10b981' if s <= 30 else '#f59e0b' if s <= 55 else '#f97316' if s <= 75 else '#ef4444'
            for s in pillar_scores_list[::-1]
        ],
        text=[f'{s:.0f}' for s in pillar_scores_list[::-1]],
        textposition='outside',
    ))
    fig_pillars.update_layout(
        template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)', height=300,
        xaxis=dict(range=[0, 110], title='Risk Score (0–100)'),
        margin=dict(l=10, r=40, t=20, b=40),
        showlegend=False
    )
    st.plotly_chart(fig_pillars, use_container_width=True)

    # ── 6 KPI cards for each pillar ──
    pcols = st.columns(6)
    for idx, (key, name, wt) in enumerate(pillar_display):
        p = pillars.get(key, {})
        sc = p.get('score', 50)
        wsc = p.get('weighted_score', 0)
        sc_color = '#10b981' if sc <= 30 else '#f59e0b' if sc <= 55 else '#f97316' if sc <= 75 else '#ef4444'
        prov_tag = ""
        if prov_summary is not None:
            pp = prov_summary['per_pillar'].get(key, {})
            pcol = provenance.GRADE_COLORS.get(pp.get('grade', ''), '#64748b')
            prov_tag = (f"<div style='font-size:.68rem;color:{pcol};margin-top:3px;font-weight:700;'>"
                        f"{pp.get('verified_pct', 0):.0f}% verified</div>")
        with pcols[idx]:
            st.markdown(f"""
            <div class='kpi' style='border-top:3px solid {sc_color};'>
              <div class='kpi-label'>{name}</div>
              <div class='kpi-val' style='color:{sc_color};font-size:1.5rem;'>{sc:.0f}</div>
              <div class='kpi-sub' style='color:#64748b;'>Weight: {wt} → <b>{wsc:.1f}</b></div>
              {prov_tag}
            </div>""", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 3: Detailed Pillar Breakdown (expandable)
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🔍 Detailed Pillar Analysis")

    with st.expander("💰 Pillar 1: Price Level Risk — Uses ML Forecast", expanded=False):
        p1 = pillars.get('price_level', {})
        bk = p1.get('raw', {}).get('breakdown', {})
        st.markdown(f"""
        | Factor | Score | Weight |
        |:---|:---:|:---:|
        | MSP Distance (₹{int(current_price):,} vs MSP ₹{MSP:,}) | {bk.get('msp_distance', 'N/A')} | 30% |
        | ML Forecast Trend ({', '.join(f'{k}: ₹{int(v):,}' for k,v in ml_predictions.items()) if ml_predictions else 'N/A'}) | {bk.get('forecast_trend', 'N/A')} | 35% |
        | Price Percentile (2yr range) | {bk.get('percentile', 'N/A')} | 20% |
        | Change Since Deposit | {bk.get('deposit_change', 'N/A')} | 15% |
        | **Composite** | **{p1.get('score', 50):.1f}** | |
        """)
        if production_signal:
            yoy = production_signal.get('yoy_change_pct')
            st.caption(
                f"📦 Supply signal (not yet weighted): India soybean production "
                f"{production_signal.get('prev_year')}→{production_signal.get('latest_year')} "
                f"changed **{yoy:+.1f}%** YoY — {'larger crop, typically softer prices' if yoy and yoy > 0 else 'smaller crop, typically firmer prices'}."
            )

    with st.expander("📉 Pillar 2: Price Volatility Risk — Rolling Vol, VaR, Regime", expanded=False):
        p2 = pillars.get('volatility', {})
        p2raw = p2.get('raw', {})
        st.markdown(f"""
        - **Current Annualized Volatility:** {p2raw.get('current_volatility_pct', 'N/A')}%
        - **Volatility Regime:** {p2raw.get('regime', 'N/A')}
        - **VaR (95%, {p2raw.get('loan_tenure_days', receipt_input.get('storage_days', 90))}d):** {p2raw.get('var_95', {}).get('var_pct', 'N/A') if isinstance(p2raw.get('var_95'), dict) else p2raw.get('var_95', 'N/A')}%
        - **CDVI (Instability Index):** {p2raw.get('cdvi', 'N/A')}
        - **Score:** **{p2.get('score', 50):.1f}/100**
        """)

    with st.expander("🏭 Pillar 3: Warehouse & Storage Risk", expanded=False):
        p3 = pillars.get('warehouse', {})
        p3bk = p3.get('raw', {}).get('breakdown', {})
        expiry_note = ""
        if p3bk.get('insurance_policy_expired'):
            expiry_note = " — ⚠️ **EXPIRED**"
        elif receipt_input.get('insurance_expiry_date'):
            expiry_note = f" (expires {receipt_input['insurance_expiry_date']})"
        cov_ratio = p3bk.get('insurance_coverage_ratio')
        st.markdown(f"""
        | Factor | Value | Score |
        |:---|:---|:---:|
        | WDRA Registered | {'✅ Yes' if receipt_input['wdra_registered'] else '❌ No'} | {p3bk.get('wdra_registered', '—')} |
        | WDRA Reg. Number | {receipt_input.get('wdra_registration_number') or 'Not on file'} | — |
        | Last Inspection | {receipt_input.get('last_inspection_date') or 'No record'} | — |
        | Insurance Coverage | {receipt_input['insurance_coverage'].title()}{expiry_note} | {p3bk.get('insurance_coverage', '—')} |
        | Insurance Coverage Ratio | {f'{cov_ratio:.0%}' if cov_ratio is not None else 'N/A (no sum insured on file)'} | — |
        | Storage Duration | {receipt_input['storage_days']} days | {p3bk.get('storage_duration', '—')} |
        | Collateral Manager | {'✅ Yes' if receipt_input['has_collateral_manager'] else '❌ No'} | {p3bk.get('collateral_manager', '—')} |
        | **Composite** | | **{p3.get('score', 50):.1f}** |
        """)

    with st.expander("🔒 Pillar 4: LTV, Quality & Shrinkage Risk", expanded=False):
        p4 = pillars.get('ltv_quality', {})
        p4raw = p4.get('raw', {})
        p4bk = p4raw.get('breakdown', {})
        qp = p4bk.get('quality_parameters', {}) or {}
        st.markdown(f"""
        | Factor | Value |
        |:---|:---|
        | Original LTV | {ltv:.1f}% |
        | Live LTV (shrinkage-adjusted, at ₹{int(current_price):,}) | {p4raw.get('live_ltv', live_ltv/100):.1%} |
        | Grade | {rd['Grade']} ({', '.join(f'{k}: {v:.0f}' for k, v in qp.items()) if qp else 'no lab slip on file'}) |
        | Margin Call Buffer | {margin_dist:.1f}% |
        | Weight/Shrinkage Loss to Date | {p4raw.get('shrinkage_loss_pct', 'N/A')}% |
        | **Score** | **{p4.get('score', 50):.1f}/100** |
        """)
        shrink = result.get('shrinkage_estimate', {})
        if shrink:
            sb = shrink.get('breakdown', {})
            st.caption(
                f"⚖️ **Shrinkage model:** moisture {sb.get('moisture_pct', 'N/A')}% "
                f"({sb.get('moisture_flag', 'n/a')}) over {sb.get('storage_days', 'N/A')} days → "
                f"est. **{shrink.get('estimated_weight_loss_pct', 'N/A')}%** weight loss "
                f"→ effective collateral value factor **{shrink.get('effective_value_factor', 'N/A')}**."
            )

    with st.expander("🌾 Pillar 5: Crop Yield & Weather Risk — District: " + rd['Location'], expanded=False):
        p5 = pillars.get('crop_yield', {})
        p5raw = p5.get('raw', {})
        p5bk = p5raw.get('breakdown', {})
        rain_src = p5bk.get('rainfall_data_source', 'synthetic')
        rain_src_note = "🟢 real IMD rainfall data" if rain_src == "real" else "🔵 synthetic district benchmark (real rainfall unavailable this run)"
        st.markdown(f"""
        - **District:** {rd['Location']}
        - **Yield Instability (CDVI):** {p5raw.get('yield_instability', 'N/A')}
        - **Yield Trend:** {p5raw.get('yield_trend', 'N/A')}
        - **Rainfall Risk:** {p5raw.get('rainfall_risk', 'N/A')} ({rain_src_note})
        - **Rainfall Deviation from Normal:** {p5bk.get('rainfall_deviation_pct', 'N/A')}%
        - **Seasonal Risk:** {p5raw.get('seasonal_risk', 'N/A')} — {p5bk.get('crop_stage', '')}
        - **Score:** **{p5.get('score', 50):.1f}/100**
        """)

    with st.expander("👤 Pillar 6: Borrower / Credit Risk", expanded=False):
        p6 = pillars.get('borrower', {})
        succ_rate = (borrower_input['successful_transactions'] / max(1, borrower_input['total_transactions'])) * 100
        st.markdown(f"""
        | Factor | Value |
        |:---|:---|
        | Total Transactions | {borrower_input['total_transactions']} |
        | Success Rate | {succ_rate:.0f}% |
        | Platform Tenure | {borrower_input['years_on_platform']} years |
        | KYC Verified | {'✅' if borrower_input['kyc_verified'] else '❌'} |
        | FPO Member | {'✅' if borrower_input['is_fpo_member'] else '❌'} |
        | Past Defaults | {borrower_input['past_defaults']} |
        | **Score** | **{p6.get('score', 50):.1f}/100** |
        """)

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 4: Investor Summary Card & Mitigations
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    col_inv, col_mit = st.columns(2)

    with col_inv:
        st.markdown(f"""
        <div class='metric-card' style='border:2px solid {gc};padding:24px;'>
          <div style='font-size:1rem;font-weight:700;color:{gc};text-transform:uppercase;margin-bottom:12px;'>
            📋 Investor Decision Card
          </div>
          <div style='font-size:.9rem;color:#e2e8f0;line-height:2;'>
            <b>Receipt:</b> {rd['Receipt ID']} — {rd['Farmer Name']}, {rd['Location']}<br>
            <b>Commodity:</b> Soybean ({rd['Grade']} Grade) · {rd['Quantity (qtl)']} qtl<br>
            <b>Loan Amount:</b> ₹{int(rd['Loan Amount']):,} · Tenure: 90 days<br>
            <b>Risk Score:</b> <span style='font-size:1.2rem;font-weight:800;color:{gc};'>{composite:.0f}/100 ({grade_str})</span><br>
            <b>Recommendation:</b> <span style='color:{gc};font-weight:700;'>{recommendation}</span><br>
            <b>Suggested Interest:</b> <span style='font-weight:700;'>{interest}</span><br>
            <b>WDRA:</b> {'✅' if receipt_input['wdra_registered'] else '❌'} · 
            <b>Insurance:</b> {receipt_input['insurance_coverage'].title()} · 
            <b>KYC:</b> {'✅' if borrower_input['kyc_verified'] else '❌'}
          </div>
        </div>""", unsafe_allow_html=True)

    with col_mit:
        st.markdown("### 🛡️ Risk Mitigation Strategies")
        st.markdown(f"""
        - **Mark-to-Market Calls:** Triggered if price drops by >15% (threshold: ₹{int(current_price * 0.85):,}/qtl vs live ₹{int(current_price):,}/qtl).
        - **Insurance:** {'Full coverage ✅' if receipt_input['insurance_coverage'] == 'full' else 'Partial coverage ⚠️' if receipt_input['insurance_coverage'] == 'partial' else 'No coverage ❌ — HIGH RISK'}
        - **WDRA Registration:** {'Registered ✅ — WDRA regulated warehouse' if receipt_input['wdra_registered'] else 'Not registered ❌ — recommend registered warehouse'}
        - **Quality Assaying:** Independent NABL accredited labs perform quality checks every 30 days.
        - **Collateral Manager:** {'Appointed ✅' if receipt_input['has_collateral_manager'] else 'Not appointed ❌'}
        - **Forward Contracts:** 40% of platform receipts are pre-matched with buyers.
        """)


# ═════════════════════════════════════════════════════════════════════════════
elif page == "➕ Add Real Receipt":
    st.title("➕ Add a Real Warehouse Receipt")
    st.markdown("""
    <div class='metric-card' style='border-left:4px solid #10b981;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Enter a <b>genuine lot</b> — real lab assay, weighbridge weight, WDRA e-NWR number and
        insurance policy. It is stored in <code>real_receipts.csv</code>, appears in every table,
        and the Risk Assessment page will score it with its inputs counted as
        <b>operator-verified</b> instead of estimated demo data.
      </span>
    </div>
    """, unsafe_allow_html=True)

    if not RECEIPT_STORE_AVAILABLE:
        st.error("`receipt_store.py` not found in the app directory.")
        st.stop()

    from datetime import date as _date

    existing = receipt_store.load_real_receipts()
    st.caption(f"📁 {len(existing)} real receipt(s) saved so far"
               + (f" · most recent: {existing['saved_at'].iloc[-1]}" if len(existing) and 'saved_at' in existing else ""))

    tab_form, tab_csv, tab_manage = st.tabs(["📝 Single entry", "📤 Bulk CSV import", "🗂️ Saved receipts"])

    # ── Tab 1: single-entry form ─────────────────────────────────────────
    with tab_form:
        with st.form("add_real_receipt", clear_on_submit=False):
            raw = {}
            _req = [f for f in receipt_store.FIELDS if f["required"]]
            _opt = [f for f in receipt_store.FIELDS if not f["required"]]

            st.markdown("##### Required")
            cols = st.columns(3)
            for i, f in enumerate(_req):
                with cols[i % 3]:
                    raw[f["key"]] = _render_receipt_field(f, _date)

            with st.expander("Lab assay · Warehouse / WDRA · Insurance · Borrower  (optional but each blank field is scored as *unverified*)", expanded=True):
                cols2 = st.columns(3)
                for i, f in enumerate(_opt):
                    with cols2[i % 3]:
                        raw[f["key"]] = _render_receipt_field(f, _date)

            submitted = st.form_submit_button("💾 Save receipt", use_container_width=True, type="primary")

        if submitted:
            rec = receipt_store.normalize_record(raw)
            problems = receipt_store.validate_record(rec)
            if problems:
                st.error("Fix these first:\n\n" + "\n".join(f"- {p}" for p in problems))
            else:
                receipt_store.append_real_receipt(rec)
                st.success(f"✅ Saved **{rec['receipt_id']}**. Open **⚠️ Risk Assessment** and pick it "
                           f"from the receipt list — it will show a high 'verified inputs' score.")
                st.cache_data.clear()

    # ── Tab 2: bulk CSV ─────────────────────────────────────────────────
    with tab_csv:
        st.download_button(
            "⬇️ Download blank template (with one worked example row)",
            data=receipt_store.blank_template_csv(),
            file_name="real_receipts_template.csv",
            mime="text/csv",
        )
        up = st.file_uploader("Upload a filled template", type=["csv"])
        if up is not None:
            res = receipt_store.import_csv(up)
            st.success(f"Added {res['added']} of {res['total']} rows.")
            if res["skipped"]:
                st.warning("Skipped rows:\n\n" + "\n".join(
                    f"- row {i}: {', '.join(pr)}" for i, pr in res["skipped"]))
            if res["added"]:
                st.cache_data.clear()

    # ── Tab 3: manage ──────────────────────────────────────────────────
    with tab_manage:
        if len(existing) == 0:
            st.info("No real receipts saved yet.")
        else:
            st.dataframe(existing, use_container_width=True, hide_index=True)
            st.download_button("⬇️ Export all real receipts",
                               data=existing.to_csv(index=False),
                               file_name="real_receipts.csv", mime="text/csv")
            wipe = st.text_input("Type DELETE to wipe all saved real receipts")
            if st.button("🗑️ Delete all", disabled=(wipe != "DELETE")):
                try:
                    os.remove(receipt_store.REAL_RECEIPTS_CSV)
                    st.cache_data.clear()
                    st.success("Deleted. Reloading…")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not delete: {e}")


# ═════════════════════════════════════════════════════════════════════════════
elif page == "🧾 Borrower Verification":
    st.title("🧾 Borrower Verification")
    if not BORROWER_VERIFY_AVAILABLE:
        st.error("`borrower_verification_demo.py` (or one of its libraries) could not be imported.")
        st.stop()
    st.markdown("""
    <div class='metric-card' style='border-left:4px solid #3b82f6;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Verify a real borrower — Aadhaar offline e-KYC · PAN · bank IFSC/PIN · <b>bank-statement
        analysis</b> (income, EMIs, FOIR, bounces) · credit report. Everything runs locally; the
        only network calls are the free IFSC and PIN registries. When you press
        <b>Compute borrower pillar</b> the result is stored and the <b>⚠️ Risk Assessment</b> page
        will offer to score a receipt with this verified borrower instead of the synthetic one.
      </span>
    </div>""", unsafe_allow_html=True)
    borrower_verify.inject_css()
    vb = st.session_state.get("verified_borrower")
    if vb:
        st.success(f"✅ A verified borrower is loaded (run `{vb['run_id']}`, "
                   f"pillar {vb['pillar_current']:.0f} → enhanced {vb['pillar_enhanced']:.0f}). "
                   f"Re-run below to replace it.")
    borrower_verify.render_real()



elif page == "🏢 FPO Verification":
    st.title("🏢 FPO Verification — Farmer Producer Company as borrower")
    if not FPO_VERIFY_AVAILABLE:
        st.error("`fpo_verification.py` (or one of its libraries) could not be imported.")
        st.stop()
    st.markdown("""
    <div class='metric-card' style='border-left:4px solid #a78bfa;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Verify an FPO that is pledging its members' soybean. <b>Identity</b> (CIN · GSTIN
        checksum · PAN cross-checks · MCA status), <b>governance</b> (5–15 directors, CEO),
        <b>membership</b> (duplicate members, and whether members could actually have grown
        the pledged quantity), <b>financials</b>, <b>bank conduct</b> and <b>compliance</b> —
        rolled into a 0–100 risk score on the same bands as the Risk Assessment. Free checks
        run for real; anything needing a paid source is labelled <b>NEEDS VENDOR</b>.
      </span>
    </div>""", unsafe_allow_html=True)
    fpo_verification.inject_css()
    vf = st.session_state.get("verified_fpo")
    if vf:
        st.success(f"✅ An FPO check is loaded — {vf['name'] or vf['cin']} "
                   f"(run `{vf['run_id']}`): **{vf['grade']}** risk, {vf['score']:.0f}/100. "
                   "Re-run below to replace it.")
    fpo_verification.render()


elif page == "🏞️ Land Verification":
    st.title("🏞️ Land Verification — Maharashtra 7/12")
    if not LAND_VERIFY_AVAILABLE:
        st.error("`land_record_verify.py` (or one of its libraries) could not be imported.")
        st.stop()
    st.markdown("""
    <div class='metric-card' style='border-left:4px solid #10b981;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Prove a farmer's <b>7/12 (सातबारा)</b> is real and that the borrower actually owns the
        land. The extract's 16-digit verification number is sent back to
        <b>digitalsatbara.mahabhumi.gov.in</b>, the government's own copy is fetched, and the two
        are compared field by field — then the holders on that record are matched against the
        borrower. Also surfaces any <b>existing mortgage</b> on the land, which is the
        double-pledge check. Free: no login, no API key, no vendor.
      </span>
    </div>""", unsafe_allow_html=True)
    land_record_verify.inject_css()
    vl = st.session_state.get("verified_land")
    if vl:
        st.success(f"✅ A land check is loaded — verification no `{vl['verification_no']}`, "
                   f"verdict **{vl['verdict'].upper()}**"
                   + (f", {vl['area_hectare']:.2f} ha" if vl.get("area_hectare") else "")
                   + ". Re-run below to replace it.")
    land_record_verify.render()
