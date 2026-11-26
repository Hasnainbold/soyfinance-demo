"""
Soybean Yield and Weather Risk Scoring Model
============================================
Provides crop yield instability, historical trend analysis, rainfall deviation scoring,
and crop-cycle seasonal risk modeling for soybean warehouse receipt financing in India.

Focuses on key soybean producing districts across Maharashtra and Madhya Pradesh (MP).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Constants & Default Configurations
# ---------------------------------------------------------------------------

SOYBEAN_DISTRICTS: List[str] = [
    "Latur",
    "Osmanabad",
    "Nanded",
    "Beed",
    "Hingoli",
    "Parbhani",
    "Washim",
    "Akola",
    "Amravati",
    "Indore",
    "Ujjain",
    "Dewas",
    "Khandwa",
    "Sehore",
    "Rajgarh",
]

# Baseline agro-climatic profiles for synthetic data generation
DISTRICT_PROFILES: Dict[str, Dict[str, Any]] = {
    "Latur": {"state": "Maharashtra", "normal_rainfall": 780.0, "base_yield": 1080.0, "base_area": 185000.0, "trend": 0.012},
    "Osmanabad": {"state": "Maharashtra", "normal_rainfall": 720.0, "base_yield": 980.0, "base_area": 140000.0, "trend": 0.005},
    "Nanded": {"state": "Maharashtra", "normal_rainfall": 880.0, "base_yield": 1150.0, "base_area": 195000.0, "trend": 0.014},
    "Beed": {"state": "Maharashtra", "normal_rainfall": 680.0, "base_yield": 920.0, "base_area": 125000.0, "trend": -0.004},
    "Hingoli": {"state": "Maharashtra", "normal_rainfall": 850.0, "base_yield": 1100.0, "base_area": 110000.0, "trend": 0.010},
    "Parbhani": {"state": "Maharashtra", "normal_rainfall": 810.0, "base_yield": 1040.0, "base_area": 160000.0, "trend": 0.008},
    "Washim": {"state": "Maharashtra", "normal_rainfall": 890.0, "base_yield": 1210.0, "base_area": 175000.0, "trend": 0.015},
    "Akola": {"state": "Maharashtra", "normal_rainfall": 800.0, "base_yield": 1120.0, "base_area": 150000.0, "trend": 0.009},
    "Amravati": {"state": "Maharashtra", "normal_rainfall": 860.0, "base_yield": 1180.0, "base_area": 190000.0, "trend": 0.013},
    "Indore": {"state": "Madhya Pradesh", "normal_rainfall": 930.0, "base_yield": 1320.0, "base_area": 220000.0, "trend": 0.018},
    "Ujjain": {"state": "Madhya Pradesh", "normal_rainfall": 890.0, "base_yield": 1260.0, "base_area": 210000.0, "trend": 0.016},
    "Dewas": {"state": "Madhya Pradesh", "normal_rainfall": 950.0, "base_yield": 1290.0, "base_area": 180000.0, "trend": 0.015},
    "Khandwa": {"state": "Madhya Pradesh", "normal_rainfall": 840.0, "base_yield": 1060.0, "base_area": 115000.0, "trend": 0.006},
    "Sehore": {"state": "Madhya Pradesh", "normal_rainfall": 1020.0, "base_yield": 1350.0, "base_area": 205000.0, "trend": 0.019},
    "Rajgarh": {"state": "Madhya Pradesh", "normal_rainfall": 870.0, "base_yield": 1160.0, "base_area": 145000.0, "trend": 0.011},
}

# Module-level cached synthetic dataset
_CACHED_SYNTHETIC_DATA: Optional[pd.DataFrame] = None


# ---------------------------------------------------------------------------
# 1. Synthetic Data Generation
# ---------------------------------------------------------------------------

def generate_synthetic_district_data(seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic historical agro-climatic and yield data for 15 major
    soybean producing districts in Maharashtra and Madhya Pradesh (2015-2024).

    Parameters
    ----------
    seed : int, optional
        Random seed for reproducibility (default: 42).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:
        - district (str)
        - year (int)
        - area_hectares (float)
        - production_tonnes (float)
        - yield_kg_per_ha (float)
        - rainfall_mm (float)
        - rainfall_deviation_pct (float)
        - soil_moisture_index (float, 0-1)
        - temperature_avg_celsius (float)
    """
    global _CACHED_SYNTHETIC_DATA
    rng = np.random.default_rng(seed)

    years = list(range(2015, 2025))  # 10 years: 2015 to 2024
    records: List[Dict[str, Any]] = []

    # Historical macro weather shock factors per year (simulating actual monsoon patterns)
    # e.g., 2015 drought (-18%), 2018 deficit (-12%), 2019-2020 surplus (+15%), 2023 mild deficit (-8%)
    yearly_monsoon_shocks = {
        2015: -18.0,
        2016: 4.0,
        2017: -3.0,
        2018: -12.0,
        2019: 16.0,
        2020: 14.0,
        2021: 5.0,
        2022: 8.0,
        2023: -9.0,
        2024: 3.0,
    }

    for district_name, profile in DISTRICT_PROFILES.items():
        normal_rain = profile["normal_rainfall"]
        base_y = profile["base_yield"]
        base_a = profile["base_area"]
        annual_trend = profile["trend"]

        for idx, year in enumerate(years):
            macro_shock = yearly_monsoon_shocks.get(year, 0.0)
            local_noise = rng.normal(0.0, 7.5)
            rain_dev_pct = float(np.clip(macro_shock + local_noise, -45.0, 50.0))

            rainfall_mm = float(np.clip(normal_rain * (1.0 + rain_dev_pct / 100.0), 520.0, 1350.0))
            # Recalculate exact deviation from normal
            rain_dev_pct = float(((rainfall_mm - normal_rain) / normal_rain) * 100.0)

            # Crop yield response:
            # Positive response to rainfall deviation, technology trend, plus stochastic noise
            # Low rainfall significantly reduces yield
            if rain_dev_pct < 0:
                rain_yield_impact = rain_dev_pct * 6.5  # drought penalty
            else:
                rain_yield_impact = rain_dev_pct * 3.8 - max(0.0, (rain_dev_pct - 25.0) * 2.0)  # excess saturation penalty

            tech_trend_effect = base_y * annual_trend * idx
            yield_noise = rng.normal(0.0, 35.0)

            yield_kg_per_ha = float(np.clip(base_y + tech_trend_effect + rain_yield_impact + yield_noise, 750.0, 1550.0))

            # Area hectares with slight expansion and noise
            area_growth = 1.0 + (idx * 0.008) + rng.normal(0.0, 0.02)
            area_hectares = float(np.round(base_a * max(0.85, area_growth), 1))

            # Production in metric tonnes = (Area in ha * Yield in kg/ha) / 1000
            production_tonnes = float(np.round((area_hectares * yield_kg_per_ha) / 1000.0, 2))

            # Soil moisture index (0 to 1 scale, heavily driven by rainfall)
            base_smi = 0.58 + (rain_dev_pct / 100.0) * 0.35 + rng.normal(0.0, 0.04)
            soil_moisture_index = float(np.clip(base_smi, 0.20, 0.95))

            # Temperature avg celsius (kharif season avg 26-32 °C; dry years are hotter)
            temp_dev = -(rain_dev_pct / 100.0) * 1.8 + rng.normal(0.0, 0.4)
            temperature_avg_celsius = float(np.clip(28.2 + temp_dev, 24.5, 33.0))

            records.append({
                "district": district_name,
                "year": year,
                "area_hectares": round(area_hectares, 1),
                "production_tonnes": round(production_tonnes, 2),
                "yield_kg_per_ha": round(yield_kg_per_ha, 2),
                "rainfall_mm": round(rainfall_mm, 2),
                "rainfall_deviation_pct": round(rain_dev_pct, 2),
                "soil_moisture_index": round(soil_moisture_index, 3),
                "temperature_avg_celsius": round(temperature_avg_celsius, 2),
            })

    df = pd.DataFrame(records)
    _CACHED_SYNTHETIC_DATA = df
    return df


def _get_district_dataframe(district_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Helper to retrieve or generate district data."""
    global _CACHED_SYNTHETIC_DATA
    if district_data is not None and not district_data.empty:
        return district_data
    if _CACHED_SYNTHETIC_DATA is None:
        _CACHED_SYNTHETIC_DATA = generate_synthetic_district_data()
    return _CACHED_SYNTHETIC_DATA


# ---------------------------------------------------------------------------
# 2. Cuddy-Della Valle Instability Index (CDVI)
# ---------------------------------------------------------------------------

def compute_yield_instability(district_data: Optional[pd.DataFrame], district: str) -> float:
    """
    Compute Cuddy-Della Valle Instability Index (CDVI) for soybean yield of a specific district.

    The Cuddy-Della Valle Index adjusts the simple Coefficient of Variation (CV)
    by removing the secular linear trend from the time-series:
        CDVI = CV * sqrt(max(0, 1 - R^2))
    where:
        CV = (std_dev / mean_yield) * 100
        R^2 = Coefficient of determination from linear trend regression: Yield ~ Year

    Parameters
    ----------
    district_data : pd.DataFrame or None
        DataFrame containing district time-series data. If None, synthetic data is used.
    district : str
        Name of the district.

    Returns
    -------
    float
        Cuddy-Della Valle Instability Index (percentage, e.g., 8.45 for 8.45%).
        Returns default fallback 15.0 if district is not found or data is invalid.
    """
    df = _get_district_dataframe(district_data)

    # Filter district data (case-insensitive)
    d_df = df[df["district"].astype(str).str.strip().str.lower() == district.strip().lower()].sort_values("year")

    if len(d_df) < 3:
        return 15.0  # Safe moderate default if insufficient data points

    yields = d_df["yield_kg_per_ha"].to_numpy(dtype=float)
    years = d_df["year"].to_numpy(dtype=float)

    mean_yield = float(np.mean(yields))
    if mean_yield <= 0:
        return 15.0

    std_yield = float(np.std(yields, ddof=1))
    cv = (std_yield / mean_yield) * 100.0

    # Linear trend regression
    slope, intercept, r_value, p_value, std_err = stats.linregress(years, yields)
    r_squared = float(r_value ** 2)

    # CDVI formula
    cdvi = cv * np.sqrt(max(0.0, 1.0 - r_squared))
    return float(round(cdvi, 2))


# ---------------------------------------------------------------------------
# 3. Yield Trend Analysis
# ---------------------------------------------------------------------------

def compute_yield_trend(district_data: Optional[pd.DataFrame], district: str) -> str:
    """
    Fit a linear trend to historical district soybean yields over time.

    Parameters
    ----------
    district_data : pd.DataFrame or None
        DataFrame containing district time-series data.
    district : str
        Name of the district.

    Returns
    -------
    str
        'improving' if annual yield growth exceeds +0.8% / year (or slope > 8 kg/ha/yr),
        'declining' if annual yield rate is below -0.8% / year (or slope < -8 kg/ha/yr),
        'stable' otherwise.
    """
    df = _get_district_dataframe(district_data)
    d_df = df[df["district"].astype(str).str.strip().str.lower() == district.strip().lower()].sort_values("year")

    if len(d_df) < 3:
        return "stable"

    yields = d_df["yield_kg_per_ha"].to_numpy(dtype=float)
    years = d_df["year"].to_numpy(dtype=float)

    mean_yield = float(np.mean(yields))
    if mean_yield <= 0:
        return "stable"

    slope, intercept, r_value, p_value, std_err = stats.linregress(years, yields)
    annual_rate_pct = (slope / mean_yield) * 100.0

    if annual_rate_pct > 0.8 or slope > 9.0:
        return "improving"
    elif annual_rate_pct < -0.8 or slope < -9.0:
        return "declining"
    else:
        return "stable"


# ---------------------------------------------------------------------------
# 4. Rainfall Risk Scoring
# ---------------------------------------------------------------------------

def _score_rainfall_deviation(dev: float) -> float:
    """
    Pure scoring function: rainfall-deviation % -> 0-100 risk score.
    Split out from `compute_rainfall_risk` so it can be reused with a REAL
    deviation figure (e.g. from `agri_data_sources.compute_district_rainfall_deviation`,
    IMD grid data) as well as the synthetic per-district dataset.

    Risk Calibration:
    - Severe / Moderate Deficit (deviation < -20%): High risk (70 to 100)
    - Mild Deficit (-20% <= deviation < -10%): Moderate-High risk (20 to 70)
    - Normal range (-10% <= deviation <= +10%): Low risk (20)
    - Mild Excess (+10% < deviation <= +30%): Moderate risk (20 to 50)
    - Severe Excess (deviation > +30%): Waterlogging risk (50 to 80+)
    """
    if dev < -20.0:
        deficit_excess = abs(dev) - 20.0
        score = 70.0 + min(30.0, deficit_excess * 1.0)
    elif dev < -10.0:
        fraction = (-10.0 - dev) / 10.0
        score = 20.0 + fraction * 50.0
    elif dev <= 10.0:
        score = 20.0
    elif dev <= 30.0:
        fraction = (dev - 10.0) / 20.0
        score = 20.0 + fraction * 30.0
    else:
        excess_amount = dev - 30.0
        score = 50.0 + min(35.0, excess_amount * 0.75)
    return float(round(np.clip(score, 0.0, 100.0), 2))


def compute_rainfall_risk(district_data: Optional[pd.DataFrame], district: str) -> float:
    """
    Score rainfall risk on a 0-100 scale based on the latest year's rainfall
    deviation in the (synthetic, unless real data is supplied elsewhere)
    per-district dataset. See `_score_rainfall_deviation` for the scale.

    Parameters
    ----------
    district_data : pd.DataFrame or None
        DataFrame containing district time-series data.
    district : str
        Name of the district.

    Returns
    -------
    float
        Rainfall risk score between 0.0 and 100.0.
    """
    df = _get_district_dataframe(district_data)
    d_df = df[df["district"].astype(str).str.strip().str.lower() == district.strip().lower()].sort_values("year")

    if d_df.empty:
        return 45.0  # Moderate default when district is unknown

    latest_record = d_df.iloc[-1]
    dev = float(latest_record.get("rainfall_deviation_pct", 0.0))
    return _score_rainfall_deviation(dev)


# ---------------------------------------------------------------------------
# 5. Seasonal Crop-Cycle Risk Scoring
# ---------------------------------------------------------------------------

def compute_seasonal_risk(current_month: Optional[int] = None) -> float:
    """
    Score seasonal production uncertainty on a 0-100 scale based on the soybean crop calendar:
    - Sowing (Jun - Aug, Months 6, 7, 8): High monsoon uncertainty = 60.0
    - Growing (Sep, Month 9): Pod formation & moderate clarity = 40.0
    - Harvest & Arrival (Oct - Dec, Months 10, 11, 12): Output known, lowest risk = 20.0
    - Post-harvest Storage (Jan - May, Months 1, 2, 3, 4, 5): Stable warehouse holding = 25.0

    Parameters
    ----------
    current_month : int, optional
        Month number (1-12). Defaults to current system calendar month.

    Returns
    -------
    float
        Seasonal risk score between 0.0 and 100.0.
    """
    if current_month is None:
        current_month = datetime.now().month

    try:
        month = int(current_month)
        if not 1 <= month <= 12:
            month = datetime.now().month
    except (ValueError, TypeError):
        month = datetime.now().month

    if month in (6, 7, 8):
        # Sowing phase: monsoon onset and germination uncertainty
        return 60.0
    elif month == 9:
        # Growing / Pod development phase
        return 40.0
    elif month in (10, 11, 12):
        # Harvest and peak mandi arrival phase: crop yield is established
        return 20.0
    else:
        # Post-harvest storage months (Jan to May)
        return 25.0


def get_crop_stage_name(current_month: Optional[int] = None) -> str:
    """Return descriptive crop stage name for a given month."""
    if current_month is None:
        current_month = datetime.now().month
    try:
        month = int(current_month)
    except (ValueError, TypeError):
        month = datetime.now().month

    if month in (6, 7, 8):
        return "Sowing & Monsoon Onset"
    elif month == 9:
        return "Vegetative & Pod Formation"
    elif month in (10, 11, 12):
        return "Harvest & Mandi Intake"
    else:
        return "Post-Harvest Storage & Financing"


# ---------------------------------------------------------------------------
# 6. Combined Crop Yield Risk Score
# ---------------------------------------------------------------------------

def compute_crop_yield_risk_score(
    district: str,
    current_month: Optional[int] = None,
    district_data: Optional[pd.DataFrame] = None,
    real_rainfall_deviation_pct: Optional[float] = None,
    real_yield_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute aggregate crop yield and weather risk score (0-100) for warehouse receipt financing.

    Weighting Breakdown:
    - Yield Instability (CDVI): 30%
    - Yield Trend: 20%
    - Rainfall Risk: 25%
    - Seasonal Crop-Cycle Risk: 25%

    Parameters
    ----------
    district : str
        Target district name (e.g. 'Latur', 'Indore', 'Washim').
    current_month : int, optional
        Current month (1-12). Defaults to active calendar month.
    district_data : pd.DataFrame, optional
        Historical district dataset. If None, synthetic dataset is used.
    real_rainfall_deviation_pct : float, optional
        A REAL rainfall-deviation-from-normal figure (e.g. computed by
        `agri_data_sources.compute_district_rainfall_deviation` from live
        IMD data) to use in place of the synthetic dataset's
        `rainfall_deviation_pct` for this district. Pass this whenever real
        rainfall data is available — everything else (yield instability,
        trend) still comes from the synthetic dataset until real
        district-level yield history is wired in too.

    Returns
    -------
    dict
        Dictionary containing:
        - score : float (0-100 aggregate risk score)
        - yield_instability : float (CDVI percentage)
        - yield_trend : str ('improving', 'stable', or 'declining')
        - rainfall_risk : float (0-100)
        - seasonal_risk : float (0-100)
        - district : str
        - breakdown : dict with sub-scores, weights, metrics, and risk category
          (breakdown['rainfall_data_source'] is 'real' or 'synthetic')
    """
    df = _get_district_dataframe(district_data)
    district_clean = str(district).strip()

    # Check if district is recognized
    match_rows = df[df["district"].astype(str).str.strip().str.lower() == district_clean.lower()]

    if match_rows.empty:
        # Fallback for unrecognized districts
        s_risk = compute_seasonal_risk(current_month)
        stage = get_crop_stage_name(current_month)
        default_score = round(0.30 * 45.0 + 0.20 * 50.0 + 0.25 * 45.0 + 0.25 * s_risk, 2)
        return {
            "score": default_score,
            "yield_instability": 15.0,
            "yield_trend": "stable",
            "rainfall_risk": 45.0,
            "seasonal_risk": round(s_risk, 2),
            "district": district_clean,
            "breakdown": {
                "instability_index_cdvi": 15.0,
                "instability_subscore": 45.0,
                "trend": "stable",
                "trend_subscore": 50.0,
                "rainfall_risk_subscore": 45.0,
                "rainfall_deviation_pct": 0.0,
                "seasonal_risk_subscore": round(s_risk, 2),
                "crop_stage": stage,
                "month": current_month or datetime.now().month,
                "weights": {
                    "yield_instability": 0.30,
                    "yield_trend": 0.20,
                    "rainfall_risk": 0.25,
                    "seasonal_risk": 0.25,
                },
                "risk_category": "Moderate Risk",
                "note": f"District '{district_clean}' not found in database. Using regional benchmark baseline defaults.",
            },
        }

    # 1. Yield Instability (CDVI) - prefer REAL district history when supplied
    if real_yield_profile and real_yield_profile.get("yield_cdvi") is not None:
        cdvi = float(real_yield_profile["yield_cdvi"])
        yield_source = "real"
    else:
        cdvi = compute_yield_instability(df, district_clean)
        yield_source = "synthetic"
    # Convert CDVI into 0-100 risk subscore (CDVI 5% -> 15, CDVI 15% -> 45, CDVI 30%+ -> 90-100)
    instability_subscore = float(np.clip(cdvi * 3.0, 0.0, 100.0))

    # 2. Yield Trend
    if real_yield_profile and real_yield_profile.get("trend"):
        trend_str = str(real_yield_profile["trend"])
    else:
        trend_str = compute_yield_trend(df, district_clean)
    if trend_str == "improving":
        trend_subscore = 20.0
    elif trend_str == "declining":
        trend_subscore = 80.0
    else:
        trend_subscore = 50.0

    # 3. Rainfall Risk — prefer REAL data (agri_data_sources) if supplied
    if real_rainfall_deviation_pct is not None:
        rain_risk = _score_rainfall_deviation(float(real_rainfall_deviation_pct))
        rainfall_source = "real"
        effective_rain_dev = float(real_rainfall_deviation_pct)
    else:
        rain_risk = compute_rainfall_risk(df, district_clean)
        rainfall_source = "synthetic"
        effective_rain_dev = None  # filled from dataset below

    # 4. Seasonal Risk
    s_risk = compute_seasonal_risk(current_month)
    stage = get_crop_stage_name(current_month)

    # Weights
    w_instability = 0.30
    w_trend = 0.20
    w_rainfall = 0.25
    w_seasonal = 0.25

    # Aggregate weighted score
    aggregate_score = (
        (w_instability * instability_subscore)
        + (w_trend * trend_subscore)
        + (w_rainfall * rain_risk)
        + (w_seasonal * s_risk)
    )
    final_score = float(round(np.clip(aggregate_score, 0.0, 100.0), 2))

    # Categorize Risk
    if final_score < 30.0:
        risk_category = "Low Risk"
    elif final_score < 50.0:
        risk_category = "Moderate-Low Risk"
    elif final_score < 70.0:
        risk_category = "Moderate Risk"
    else:
        risk_category = "High Risk"

    # Contextual district statistics
    d_sorted = match_rows.sort_values("year")
    latest_row = d_sorted.iloc[-1]
    latest_yield = float(latest_row.get("yield_kg_per_ha", 0.0))
    latest_rain_dev = effective_rain_dev if effective_rain_dev is not None else float(latest_row.get("rainfall_deviation_pct", 0.0))
    mean_yield = float(round(d_sorted["yield_kg_per_ha"].mean(), 2))
    latest_year = int(latest_row.get("year", 2024))

    return {
        "score": final_score,
        "yield_instability": round(cdvi, 2),
        "yield_trend": trend_str,
        "rainfall_risk": round(rain_risk, 2),
        "seasonal_risk": round(s_risk, 2),
        "district": district_clean,
        "breakdown": {
            "instability_index_cdvi": round(cdvi, 2),
            "instability_subscore": round(instability_subscore, 2),
            "trend": trend_str,
            "trend_subscore": round(trend_subscore, 2),
            "rainfall_risk_subscore": round(rain_risk, 2),
            "rainfall_deviation_pct": round(latest_rain_dev, 2),
            "rainfall_data_source": rainfall_source,
            "seasonal_risk_subscore": round(s_risk, 2),
            "crop_stage": stage,
            "month": current_month or datetime.now().month,
            "weights": {
                "yield_instability": w_instability,
                "yield_trend": w_trend,
                "rainfall_risk": w_rainfall,
                "seasonal_risk": w_seasonal,
            },
            "risk_category": risk_category,
            "latest_yield_kg_per_ha": latest_yield,
            "historical_mean_yield_kg_per_ha": mean_yield,
            "latest_year": latest_year,
        },
    }


# ---------------------------------------------------------------------------
# 7. Helper Utilities
# ---------------------------------------------------------------------------

def get_available_districts() -> List[str]:
    """Return list of supported soybean producing districts."""
    return list(SOYBEAN_DISTRICTS)


def get_all_district_risk_scores(current_month: Optional[int] = None) -> pd.DataFrame:
    """
    Generate risk scores across all 15 supported soybean districts in a convenient DataFrame.

    Parameters
    ----------
    current_month : int, optional
        Month number (1-12).

    Returns
    -------
    pd.DataFrame
        Summary table with district risk metrics.
    """
    df = _get_district_dataframe()
    results = []
    for dist in SOYBEAN_DISTRICTS:
        res = compute_crop_yield_risk_score(dist, current_month=current_month, district_data=df)
        b = res["breakdown"]
        results.append({
            "district": dist,
            "state": DISTRICT_PROFILES.get(dist, {}).get("state", "Unknown"),
            "risk_score": res["score"],
            "risk_category": b["risk_category"],
            "yield_instability_cdvi": res["yield_instability"],
            "yield_trend": res["yield_trend"],
            "rainfall_risk": res["rainfall_risk"],
            "seasonal_risk": res["seasonal_risk"],
            "latest_yield_kg_per_ha": b["latest_yield_kg_per_ha"],
            "rainfall_deviation_pct": b["rainfall_deviation_pct"],
        })
    return pd.DataFrame(results)


if __name__ == "__main__":
    # Self-test when executed directly
    print("Generating synthetic district data...")
    data = generate_synthetic_district_data()
    print(f"Generated {len(data)} records for {data['district'].nunique()} districts across years {data['year'].min()}-{data['year'].max()}.")
    print(data.head())

    print("\n--- Individual District Risk Scores ---")
    for sample_district in ["Latur", "Indore", "Beed", "UnknownDistrict"]:
        score_info = compute_crop_yield_risk_score(sample_district, current_month=8)
        print(f"\nDistrict: {sample_district}")
        print(f"  Score: {score_info['score']} ({score_info['breakdown']['risk_category']})")
        print(f"  Instability CDVI: {score_info['yield_instability']}%")
        print(f"  Trend: {score_info['yield_trend']}")
        print(f"  Rainfall Risk: {score_info['rainfall_risk']}")
        print(f"  Seasonal Risk: {score_info['seasonal_risk']}")

    print("\n--- Summary across all districts ---")
    summary_df = get_all_district_risk_scores(current_month=8)
    print(summary_df.to_string(index=False))
