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
import warnings
warnings.filterwarnings("ignore")

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
                 color:#475569; text-transform:uppercase; margin-bottom:8px; }
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
MANDI_DETAIL_CSV       = os.path.join(_APP_DIR, "soybean_mandi_detail_2022_2026.csv")
EXCEL_MANDI_FILE       = os.path.join(_APP_DIR, "Mandi_price_data.xlsx")

def format_inr(number):
    try:
        s = str(int(round(number)))
        r = ",".join([s[x-2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
        return f"₹ {r}"
    except:
        return f"₹ {number}"

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
def _api_call(api_key, extra_params=None, timeout=60):
    params = {"api-key": api_key, "format": "json", "filters[commodity]": "Soyabean"}
    if extra_params:
        params.update(extra_params)
    # Build full URL manually (requests URL-encodes brackets which some APIs reject)
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    full_url  = f"{BASE_URL}?{param_str}"
    try:
        s = requests.Session()
        s.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1)))
        r = s.get(full_url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
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
    for offset in range(0, 1000, 100):
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


def make_prediction(hist_df, horizons, days_ahead=90):
    """
    Realistic short-term price prediction:
    - Uses last 60 days of linear trend (not long polynomial that blow up)
    - Adds gentle seasonal dampening (harvest pressure in Oct-Dec)
    - Caps change at ±12% over 90 days to stay realistic
    """
    prices  = hist_df["Price"].values.astype(float)
    current = prices[-1]

    # Use only last 60 days for trend (more stable than 5-year poly)
    window = min(60, len(prices))
    recent = prices[-window:]
    x      = np.arange(window)
    slope, intercept = np.polyfit(x, recent, 1)   # linear only

    # Damp the slope: realistic max movement ₹15/day
    slope = np.clip(slope, -15, 15)

    # Seasonal factor: harvest pressure (Sep-Dec pulls prices down)
    today_month = datetime.today().month
    # How many months until harvest peak (Oct=month 10)
    months_to_harvest = (10 - today_month) % 12
    harvest_factor = -0.04 if months_to_harvest <= 3 else 0.01

    max_change_pct = 0.12   # ±12% max over 90 days

    pts = {}
    for h in horizons:
        trend_component    = slope * h
        seasonal_component = current * harvest_factor * (h / 90)
        raw = current + trend_component + seasonal_component
        # Clip to realistic band
        p = float(np.clip(raw,
                          current * (1 - max_change_pct * h / 90),
                          current * (1 + max_change_pct * h / 90)))
        pts[h] = round(p)

    future_dates = [pd.to_datetime(hist_df["Date"].iloc[-1]) + timedelta(days=i + 1)
                    for i in range(days_ahead)]
    curve, upper, lower = [], [], []
    for i in range(days_ahead):
        h  = i + 1
        trend_component    = slope * h
        seasonal_component = current * harvest_factor * (h / 90)
        raw = current + trend_component + seasonal_component
        p   = float(np.clip(raw,
                             current * (1 - max_change_pct * h / 90),
                             current * (1 + max_change_pct * h / 90)))
        # Confidence band grows with horizon (±0.5% per 10 days)
        w = current * 0.005 * (h / 10)
        curve.append(round(p))
        upper.append(round(p + w))
        lower.append(round(p - w))
    return pts, pd.DataFrame({"Date": future_dates, "Predicted Price": curve,
                               "Upper Bound": upper, "Lower Bound": lower})


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
    data = []
    for i in range(1, 101):
        qty        = np.random.randint(50, 500)
        # Use actual live price ± small variance for market value
        price_var  = int(current_price * np.random.uniform(0.97, 1.03))
        market_val = qty * price_var
        loan_amt   = int(market_val * np.random.uniform(0.70, 0.80))
        deposit_date = CURRENT_DATE - timedelta(days=np.random.randint(1, 120))
        data.append({
            'Receipt ID':      f"WR-2026-{1000+i}",
            'Farmer Name':     np.random.choice(farmers),
            'Location':        np.random.choice(locations),
            'Quantity (qtl)':  qty,
            'Grade':           np.random.choice(grades, p=[0.6, 0.3, 0.1]),
            'Deposit Date':    deposit_date.strftime("%Y-%m-%d"),
            'Market Value':    market_val,
            'Loan Amount':     loan_amt,
            'Status':          np.random.choice(statuses, p=[0.15, 0.1, 0.5, 0.2, 0.05]),
            'Risk Score':      np.random.randint(10, 85),
            'Tenure (days)':   90
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
     "🌾 Soybean Intelligence"]
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
    hist_source = f"Real Maharashtra Data ({len(real_hist)} days, 2021–2025)"
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

    tabs = st.tabs(["Historical Trends", "Price Prediction (90 Days)", "Weather & Mandi Data"])

    with tabs[0]:
        st.subheader("Historical Price Trend (₹/Quintal)")
        st.caption(f"Source: {hist_source}")

        fig = go.Figure()
        hist_plot = hist_df.copy()
        hist_plot["Date"] = pd.to_datetime(hist_plot["Date"])
        fig.add_trace(go.Scatter(x=hist_plot['Date'], y=hist_plot['Price'],
                                 name='Market Price', line=dict(color='#3b82f6', width=2)))
        fig.add_trace(go.Scatter(x=hist_plot['Date'], y=hist_plot['MSP'],
                                 name='Govt MSP', line=dict(color='#f59e0b', width=2, dash='dash')))
        fig.update_layout(
            template='plotly_dark',
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(rangeslider=dict(visible=True), type="date"),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)

    with tabs[1]:
        st.subheader("AI Price Prediction (Next 90 Days)")
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
                                     fillcolor='rgba(59,130,246,0.2)', name='Confidence Interval'))
            fig.add_trace(go.Scatter(x=pred_plot['Date'], y=pred_plot['Predicted Price'],
                                     mode='lines', name='Predicted Price', line=dict(color='#3b82f6', width=3)))
            fig.add_vline(x=CURRENT_DATE, line_width=2, line_dash="dash", line_color="#10b981")
            fig.add_annotation(x=CURRENT_DATE, y=current_price,
                               text=f"Today ₹{int(current_price):,}",
                               showarrow=True, arrowhead=2, arrowcolor="#10b981",
                               font=dict(color="#10b981", size=11))
            fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                              paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified")
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
        {f"&nbsp;·&nbsp; <span style='color:#475569;'>{si_state}</span>" if si_state != 'All States' else ''}
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Source bar
    hist_lbl_si = hist_source  # Already set from the real data loading logic above
    st.markdown(f"""
    <div style="text-align:right;font-size:.78rem;color:#475569;margin-bottom:12px;">
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
          <div class="kpi-sub" style="color:#475569;">{sel_day_si}</div>
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
                    <div style="font-size:.78rem;color:#475569;margin-top:3px;">{row.get('state','—')} · {row.get('district','—')}</div>
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
                  <div style="font-size:.78rem;color:#475569;">{fd}</div></div>
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
              <div style="font-size:.78rem;color:#475569;margin-top:4px;">₹{slope:+.1f}/day</div>
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
    st.title("Risk Assessment Engine")

    selected_receipt = st.selectbox("Select Receipt for Risk Analysis", receipts_df['Receipt ID'])
    receipt_data = receipts_df[receipts_df['Receipt ID'] == selected_receipt].iloc[0]

    col1, col2 = st.columns([1, 2])

    with col1:
        risk_score = receipt_data['Risk Score']
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=risk_score,
            title={'text': "Overall Risk Score"},
            gauge={
                'axis': {'range': [0, 100]},
                'bar':  {'color': "white"},
                'steps': [
                    {'range': [0, 30],   'color': "#10b981"},
                    {'range': [30, 70],  'color': "#f59e0b"},
                    {'range': [70, 100], 'color': "#ef4444"}
                ],
            }
        ))
        fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                          paper_bgcolor='rgba(0,0,0,0)', height=300)
        st.plotly_chart(fig, use_container_width=True)

        if risk_score <= 30:
            st.success("🟢 LOW RISK: Auto-approve. Suggested Interest: 12%")
        elif risk_score <= 70:
            st.warning("🟡 MEDIUM RISK: Manual review. Suggested Interest: 15%")
        else:
            st.error("🔴 HIGH RISK: Enhanced diligence required. Suggested Interest: 18%+")

        # Live price context
        ltv = receipt_data['Loan Amount'] / receipt_data['Market Value'] * 100
        live_mktval = receipt_data['Quantity (qtl)'] * current_price
        st.markdown(f"""
        <div class='metric-card' style='margin-top:12px;'>
          <div style='font-size:.8rem;color:#64748b;'>Live Price Context</div>
          <div style='font-size:.95rem;color:#f1f5f9;margin-top:6px;'>
            Live Avg Price: <b style='color:#10b981;'>₹ {int(current_price):,}/qtl</b><br>
            Qty: <b>{receipt_data['Quantity (qtl)']} qtl</b><br>
            Live Mkt Val: <b>₹ {int(live_mktval):,}</b><br>
            LTV Ratio: <b style='color:{"#10b981" if ltv < 75 else "#ef4444"};'>{ltv:.1f}%</b>
          </div>
        </div>""", unsafe_allow_html=True)

    with col2:
        st.subheader("Risk Breakdown")
        price_risk   = risk_score * 0.4
        weather_risk = risk_score * 0.2
        quality_risk = risk_score * 0.2
        market_risk  = risk_score * 0.1
        credit_risk  = risk_score * 0.1

        risk_components = pd.DataFrame({
            'Category': ['Price Risk', 'Weather Risk', 'Quality/Storage Risk', 'Market/Demand Risk', 'Credit/Farmer Risk'],
            'Score':    [price_risk, weather_risk, quality_risk, market_risk, credit_risk],
            'Weight':   ['30%', '25%', '15%', '15%', '15%']
        })

        fig2 = px.bar(risk_components, x='Score', y='Category', orientation='h',
                      color='Score', color_continuous_scale=['#10b981', '#f59e0b', '#ef4444'])
        fig2.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)',
                           paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.markdown("### Risk Mitigation Strategies in Place")
    st.markdown(f"""
    - **Mark-to-Market Calls:** Triggered if price drops by >15% (threshold: ₹{int(current_price * 0.85):,}/qtl vs live ₹{int(current_price):,}/qtl).
    - **Insurance:** 100% warehouse stock insured against fire, theft, and natural perils.
    - **Quality Assaying:** Independent NABL accredited labs perform quality checks every 30 days.
    - **Forward Contracts:** 40% of platform receipts are pre-matched with buyers using forward contracts.
    """)
