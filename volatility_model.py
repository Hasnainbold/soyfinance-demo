"""
volatility_model.py
===================
Volatility-based risk scoring module for soybean prices in a warehouse receipt finance platform.

This module analyzes historical daily average mandi prices to quantify commodity price risk,
downside loss potential, and structural instability. It provides standardized metrics for
collateral risk assessment, loan-to-value (LTV) calibration, and liquidation margin buffers.

Dependencies:
    - numpy
    - pandas
    - scipy.stats

Author: SoyFinance Platform Team
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats


def _prepare_price_series(prices: Union[pd.Series, np.ndarray, list]) -> pd.Series:
    """
    Clean, validate, and chronologically sort a price series.

    Parameters
    ----------
    prices : pd.Series, np.ndarray, or list
        Input price sequence.

    Returns
    -------
    pd.Series
        Cleaned, positive numeric series sorted chronologically.
    """
    if not isinstance(prices, pd.Series):
        prices = pd.Series(prices)

    # Convert to numeric, dropping NaNs and non-numeric values
    s = pd.to_numeric(prices, errors="coerce").dropna()
    # Filter positive prices only
    s = s[s > 0]

    if s.empty:
        return s

    # Ensure chronological order if index has datetimes or date strings
    if isinstance(s.index, pd.DatetimeIndex):
        if not s.index.is_monotonic_increasing:
            s = s.sort_index()
    elif s.index.dtype == object and len(s) > 1:
        try:
            sample = str(s.index[0])
            if any(sep in sample for sep in ("-", "/", ".")) and len(sample) >= 8:
                parsed_index = pd.to_datetime(s.index, errors="coerce")
                if not parsed_index.isna().any():
                    s.index = parsed_index
                    if not s.index.is_monotonic_increasing:
                        s = s.sort_index()
        except Exception:
            pass

    return s


def compute_rolling_volatility(prices: pd.Series, window: int = 30) -> pd.Series:
    """
    Compute annualized rolling volatility from daily price returns.

    Calculation:
        1. Daily percentage returns: r_t = (P_t - P_{t-1}) / P_{t-1}
        2. Rolling standard deviation of returns over `window` days.
        3. Annualized volatility: daily_std * sqrt(252)

    Parameters
    ----------
    prices : pd.Series
        Historical series of daily commodity prices (e.g. INR per quintal).
    window : int, default=30
        Rolling window size in trading days.

    Returns
    -------
    pd.Series
        Series of annualized volatility values (as decimal fractions, e.g. 0.22 = 22%),
        indexed matching the input price series.
    """
    s = _prepare_price_series(prices)
    if len(s) < 2:
        return pd.Series(dtype=float, index=s.index)

    # Daily simple returns
    returns = s.pct_change()

    # Minimum observations required to produce a rolling estimate
    min_periods = min(window, max(2, len(returns) // 5)) if len(returns) < window else min(window, 5)

    # Rolling standard deviation annualized by sqrt(252 trading days)
    rolling_std = returns.rolling(window=window, min_periods=min_periods).std()
    annualized_vol = rolling_std * np.sqrt(252.0)

    # Maintain original series name if present
    annualized_vol.name = f"volatility_{window}d"
    return annualized_vol


def compute_cdvi(series: pd.Series) -> float:
    """
    Compute the Cuddy-Della Valle Instability Index (CDVI).

    The Cuddy-Della Valle index is an adjusted coefficient of variation widely
    used in Indian agricultural economics to assess commodity price instability
    after removing deterministic linear trend.

    Formula:
        CV = (std / mean) * 100
        CDVI = CV * sqrt(1 - R^2)
    where:
        - CV is the sample coefficient of variation in percent.
        - R^2 is the coefficient of determination from the linear regression
          of prices on time (t = 0, 1, 2, ..., N-1).

    Parameters
    ----------
    series : pd.Series
        Historical price or commodity metric series.

    Returns
    -------
    float
        CDVI instability index percentage (e.g., 14.50 for 14.50%).
        Returns a moderate default of 15.0 if data is insufficient.
    """
    s = _prepare_price_series(series)
    if len(s) < 3:
        return 15.0  # Moderate default for insufficient data points

    mean_val = float(s.mean())
    std_val = float(s.std(ddof=1))

    if mean_val <= 0.0 or np.isnan(mean_val) or np.isclose(std_val, 0.0):
        return 0.0

    # Coefficient of variation in percentage
    cv = (std_val / mean_val) * 100.0

    # Time index t for linear trend regression
    t = np.arange(len(s), dtype=float)
    y = s.values.astype(float)

    try:
        slope, intercept, r_value, p_value, std_err = stats.linregress(t, y)
        r_squared = float(r_value ** 2) if not np.isnan(r_value) else 0.0
        # Bound R^2 between 0.0 and 1.0 to guard against precision anomalies
        r_squared = max(0.0, min(1.0, r_squared))
        cdvi = cv * np.sqrt(1.0 - r_squared)
    except Exception:
        cdvi = cv

    return float(round(cdvi, 2))


def compute_var(
    prices: pd.Series,
    confidence: float = 0.95,
    horizon_days: int = 90
) -> dict:
    """
    Compute Value-at-Risk (VaR) using historical simulation.

    Determines the maximum expected percentage and absolute price loss over the
    specified horizon (e.g., 90-day warehouse loan tenure) at the chosen confidence level.

    Parameters
    ----------
    prices : pd.Series
        Historical daily price series.
    confidence : float, default=0.95
        Confidence level (e.g. 0.95 for 95% VaR).
    horizon_days : int, default=90
        Holding horizon / loan tenure in days.

    Returns
    -------
    dict
        A dictionary containing:
            - var_pct (float): Downside loss in percent (e.g. 12.5 for 12.5% loss).
            - var_absolute (float): Downside loss in INR per quintal based on current price.
            - confidence (float): The confidence level used (e.g. 0.95).
            - horizon (int): The horizon in days (e.g. 90).
            - current_price (float): Latest commodity price per quintal.
    """
    s = _prepare_price_series(prices)

    if len(s) < 5:
        default_price = 4500.0 if s.empty else float(s.iloc[-1])
        default_var_pct = 12.5
        return {
            "var_pct": round(default_var_pct, 2),
            "var_absolute": round(default_price * (default_var_pct / 100.0), 2),
            "confidence": confidence,
            "horizon": horizon_days,
            "current_price": round(default_price, 2),
        }

    current_price = float(s.iloc[-1])
    alpha_percentile = (1.0 - confidence) * 100.0  # e.g., 5.0% for 95% confidence

    # If dataset has sufficient history, compute empirical horizon returns directly
    if len(s) >= horizon_days + 15:
        # Overlapping H-day returns: (P_t - P_{t-H}) / P_{t-H}
        horizon_returns = (s / s.shift(horizon_days) - 1.0).dropna()
        if len(horizon_returns) >= 10:
            q = float(np.percentile(horizon_returns, alpha_percentile))
            # Downside loss is positive if return is negative
            var_pct = max(0.0, -q * 100.0)
        else:
            # Fallback to square-root scaled daily returns
            daily_returns = (s / s.shift(1) - 1.0).dropna()
            q_daily = float(np.percentile(daily_returns, alpha_percentile))
            daily_loss_pct = max(0.0, -q_daily * 100.0)
            var_pct = daily_loss_pct * np.sqrt(horizon_days)
    else:
        # Scale daily empirical distribution to horizon using standard root-T rule
        daily_returns = (s / s.shift(1) - 1.0).dropna()
        if len(daily_returns) >= 3:
            q_daily = float(np.percentile(daily_returns, alpha_percentile))
            daily_loss_pct = max(0.0, -q_daily * 100.0)
            var_pct = daily_loss_pct * np.sqrt(horizon_days)
        else:
            var_pct = 12.5

    # Cap percentage loss between 0% and 100%
    var_pct = float(min(100.0, max(0.0, var_pct)))
    var_absolute = float(round(current_price * (var_pct / 100.0), 2))

    return {
        "var_pct": round(var_pct, 2),
        "var_absolute": var_absolute,
        "confidence": confidence,
        "horizon": horizon_days,
        "current_price": round(current_price, 2),
    }


def detect_volatility_regime(prices: pd.Series, window: int = 30) -> str:
    """
    Detect current volatility regime relative to historical percentiles.

    Compares the latest 30-day annualized volatility against the 25th and 75th
    percentiles of historical rolling volatility.

    Parameters
    ----------
    prices : pd.Series
        Historical daily price series.
    window : int, default=30
        Rolling window size in trading days.

    Returns
    -------
    str
        'low'    : Current volatility < 25th percentile
        'medium' : Current volatility between 25th and 75th percentiles
        'high'   : Current volatility > 75th percentile
    """
    rolling_vol = compute_rolling_volatility(prices, window=window).dropna()

    if len(rolling_vol) < 5:
        return "medium"  # Moderate default for insufficient data points

    current_vol = float(rolling_vol.iloc[-1])
    p25 = float(np.percentile(rolling_vol, 25.0))
    p75 = float(np.percentile(rolling_vol, 75.0))

    if current_vol < p25:
        return "low"
    elif current_vol > p75:
        return "high"
    else:
        return "medium"


def compute_volatility_risk_score(
    prices: pd.Series,
    loan_tenure_days: int = 90
) -> dict:
    """
    Compute comprehensive volatility risk score (0-100) for soybean warehouse receipt financing.

    Combines four weighted risk pillars:
      1. Current Annualized Volatility (35% weight):
         - < 15%      -> 10 pts
         - 15% - 25%  -> 30 pts
         - 25% - 35%  -> 60 pts
         - > 35%      -> 85 pts
      2. Volatility Regime (25% weight):
         - 'low'      -> 10 pts
         - 'medium'   -> 40 pts
         - 'high'     -> 80 pts
      3. Value-at-Risk (95% Conf, Tenure Horizon) (25% weight):
         - < 5% loss   -> 10 pts
         - 5% - 10%   -> 35 pts
         - 10% - 20%  -> 65 pts
         - > 20%      -> 90 pts
      4. Cuddy-Della Valle Instability Index (CDVI) (15% weight):
         - < 10       -> 10 pts
         - 10 - 20    -> 30 pts
         - 20 - 35    -> 60 pts
         - > 35       -> 85 pts

    Parameters
    ----------
    prices : pd.Series
        Historical daily price series.
    loan_tenure_days : int, default=90
        Loan tenure / holding period in days for VaR evaluation.

    Returns
    -------
    dict
        {
            "score": float,                  # Combined risk score (0-100)
            "current_volatility": float,     # Latest annualized volatility (decimal)
            "current_volatility_pct": float, # Latest annualized volatility (%)
            "regime": str,                   # 'low' | 'medium' | 'high'
            "var_95": dict,                  # VaR result dict from compute_var()
            "cdvi": float,                   # Cuddy-Della Valle Instability Index (%)
            "breakdown": dict,               # Sub-scores and component weights
            "risk_level": str,               # Risk category classification
            "loan_tenure_days": int          # Tenure evaluated
        }
    """
    s = _prepare_price_series(prices)

    # Default fallback if insufficient data
    if len(s) < 2:
        default_var = {
            "var_pct": 12.5,
            "var_absolute": 562.5,
            "confidence": 0.95,
            "horizon": loan_tenure_days,
            "current_price": 4500.0,
        }
        breakdown = {
            "volatility": {"score": 30.0, "weight": 0.35, "weighted_score": 10.5, "value_pct": 20.0},
            "regime": {"score": 40.0, "weight": 0.25, "weighted_score": 10.0, "value": "medium"},
            "var_95": {"score": 65.0, "weight": 0.25, "weighted_score": 16.25, "value_pct": 12.5},
            "cdvi": {"score": 30.0, "weight": 0.15, "weighted_score": 4.5, "value": 15.0},
            "volatility_score": 30.0,
            "regime_score": 40.0,
            "var_score": 65.0,
            "cdvi_score": 30.0,
        }
        return {
            "score": 41.25,
            "current_volatility": 0.20,
            "current_volatility_pct": 20.0,
            "regime": "medium",
            "var_95": default_var,
            "cdvi": 15.0,
            "breakdown": breakdown,
            "risk_level": "Moderate Risk",
            "loan_tenure_days": loan_tenure_days,
        }

    # 1. Compute rolling volatility and latest annualized volatility
    rolling_vol = compute_rolling_volatility(s, window=30).dropna()
    if rolling_vol.empty:
        current_vol = 0.20
        current_vol_pct = 20.0
    else:
        current_vol = float(rolling_vol.iloc[-1])
        # If vol is expressed as decimal (e.g. 0.22), convert to percentage for scoring
        current_vol_pct = current_vol * 100.0 if current_vol <= 2.0 else current_vol
        current_vol = current_vol_pct / 100.0

    # Volatility scoring (Weight 35%)
    if current_vol_pct < 15.0:
        vol_score = 10.0
    elif current_vol_pct <= 25.0:
        vol_score = 30.0
    elif current_vol_pct <= 35.0:
        vol_score = 60.0
    else:
        vol_score = 85.0

    vol_weighted = vol_score * 0.35

    # 2. Detect Volatility Regime (Weight 25%)
    regime = detect_volatility_regime(s, window=30)
    if regime == "low":
        regime_score = 10.0
    elif regime == "high":
        regime_score = 80.0
    else:
        regime_score = 40.0

    regime_weighted = regime_score * 0.25

    # 3. Value-at-Risk (95% Conf, Loan Tenure Horizon) (Weight 25%)
    var_res = compute_var(s, confidence=0.95, horizon_days=loan_tenure_days)
    var_pct = float(var_res.get("var_pct", 10.0))

    if var_pct < 5.0:
        var_score = 10.0
    elif var_pct <= 10.0:
        var_score = 35.0
    elif var_pct <= 20.0:
        var_score = 65.0
    else:
        var_score = 90.0

    var_weighted = var_score * 0.25

    # 4. Cuddy-Della Valle Instability Index (CDVI) (Weight 15%)
    cdvi_val = compute_cdvi(s)

    if cdvi_val < 10.0:
        cdvi_score = 10.0
    elif cdvi_val <= 20.0:
        cdvi_score = 30.0
    elif cdvi_val <= 35.0:
        cdvi_score = 60.0
    else:
        cdvi_score = 85.0

    cdvi_weighted = cdvi_score * 0.15

    # Composite Score (0-100)
    total_score = vol_weighted + regime_weighted + var_weighted + cdvi_weighted
    total_score = round(total_score, 2)

    # Risk level classification
    if total_score < 30.0:
        risk_level = "Low Risk"
    elif total_score <= 55.0:
        risk_level = "Moderate Risk"
    elif total_score <= 75.0:
        risk_level = "Elevated Risk"
    else:
        risk_level = "High Risk"

    breakdown = {
        "volatility": {
            "value_pct": round(current_vol_pct, 2),
            "score": vol_score,
            "weight": 0.35,
            "weighted_score": round(vol_weighted, 2),
        },
        "regime": {
            "value": regime,
            "score": regime_score,
            "weight": 0.25,
            "weighted_score": round(regime_weighted, 2),
        },
        "var_95": {
            "value_pct": round(var_pct, 2),
            "var_absolute": var_res.get("var_absolute", 0.0),
            "score": var_score,
            "weight": 0.25,
            "weighted_score": round(var_weighted, 2),
        },
        "cdvi": {
            "value": round(cdvi_val, 2),
            "score": cdvi_score,
            "weight": 0.15,
            "weighted_score": round(cdvi_weighted, 2),
        },
        "volatility_score": vol_score,
        "regime_score": regime_score,
        "var_score": var_score,
        "cdvi_score": cdvi_score,
    }

    return {
        "score": total_score,
        "current_volatility": round(current_vol, 4),
        "current_volatility_pct": round(current_vol_pct, 2),
        "regime": regime,
        "var_95": var_res,
        "cdvi": round(cdvi_val, 2),
        "breakdown": breakdown,
        "risk_level": risk_level,
        "loan_tenure_days": loan_tenure_days,
    }


def load_soybean_prices(
    csv_path: Optional[str] = None,
    price_column: str = "avg_price",
    date_column: str = "date"
) -> pd.Series:
    """
    Load and preprocess daily soybean prices from local CSV datasets.

    Searches common standard file paths in the demo directory if no path is provided.

    Parameters
    ----------
    csv_path : str, optional
        Path to CSV file. If None, looks for known dataset files.
    price_column : str, default='avg_price'
        Column name containing daily price data.
    date_column : str, default='date'
        Column name containing date values.

    Returns
    -------
    pd.Series
        Sorted daily price series indexed by DatetimeIndex.
    """
    candidate_paths = []
    if csv_path:
        candidate_paths.append(Path(csv_path))

    # Search in current directory and parent directory
    base_dir = Path(__file__).resolve().parent
    candidate_paths.extend([
        base_dir / "soybean_daily_avg_FINAL.csv",
        base_dir / "soybean_daily_avg_2022_2026.csv",
        base_dir / "soybean_daily_avg.csv",
        base_dir / "soybean_history.csv",
        base_dir.parent / "soybean_daily_avg_FINAL.csv",
        base_dir.parent / "historical-prices-04-29-2025.csv",
    ])

    target_file = None
    for p in candidate_paths:
        if p.exists() and p.is_file():
            target_file = p
            break

    if not target_file:
        raise FileNotFoundError(
            f"No soybean price CSV found. Searched locations: {[str(p) for p in candidate_paths]}"
        )

    df = pd.read_csv(target_file)

    # Detect price column dynamically if default is absent
    if price_column not in df.columns:
        possible_price_cols = ["avg_price", "modal_price", "price", "close", "Close", "Price"]
        for col in possible_price_cols:
            if col in df.columns:
                price_column = col
                break

    # Detect date column dynamically if default is absent
    if date_column not in df.columns:
        possible_date_cols = ["date", "Date", "trade_date", "timestamp"]
        for col in possible_date_cols:
            if col in df.columns:
                date_column = col
                break

    if price_column not in df.columns:
        raise KeyError(f"Price column '{price_column}' not found in {target_file}. Available: {df.columns.tolist()}")

    # Parse and clean
    if date_column in df.columns:
        df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
        df = df.dropna(subset=[date_column])
        df = df.sort_values(by=date_column, ascending=True)
        df = df.set_index(date_column)

    prices = pd.to_numeric(df[price_column], errors="coerce").dropna()
    prices = prices[prices > 0]
    prices.name = "soybean_price"

    return prices


if __name__ == "__main__":
    print("=" * 60)
    print("SoyFinance Volatility Model - Self Test")
    print("=" * 60)

    try:
        prices = load_soybean_prices()
        print(f"Loaded price series: {len(prices)} observations")
        print(f"Date range: {prices.index[0]} to {prices.index[-1]}")
        print(f"Latest price: INR {prices.iloc[-1]:,.2f}/quintal\n")

        # 1. Rolling volatility
        rolling_vol = compute_rolling_volatility(prices, window=30)
        current_vol = rolling_vol.dropna().iloc[-1]
        print(f"1. 30-Day Annualized Volatility: {current_vol * 100:.2f}%")

        # 2. CDVI
        cdvi = compute_cdvi(prices)
        print(f"2. Cuddy-Della Valle Instability Index (CDVI): {cdvi:.2f}%")

        # 3. Value-at-Risk (95%, 90-day)
        var_95 = compute_var(prices, confidence=0.95, horizon_days=90)
        print(f"3. 90-Day 95% Historical VaR: {var_95['var_pct']:.2f}% (INR {var_95['var_absolute']:,.2f}/quintal)")

        # 4. Volatility Regime
        regime = detect_volatility_regime(prices, window=30)
        print(f"4. Volatility Regime: {regime.upper()}")

        # 5. Composite Risk Score
        risk_profile = compute_volatility_risk_score(prices, loan_tenure_days=90)
        print(f"\n5. Composite Volatility Risk Score: {risk_profile['score']:.2f}/100 ({risk_profile['risk_level']})")
        print("   Breakdown:")
        for k, v in risk_profile["breakdown"].items():
            if isinstance(v, dict):
                print(f"   - {k.capitalize():12}: Score = {v['score']:4.1f} (Weight {v['weight']*100:.0f}%) -> {v['weighted_score']:.2f} pts")

        print("\nSelf test completed successfully.")
    except Exception as e:
        print(f"Error during self-test: {e}")
