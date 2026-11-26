"""Quick test to validate the ensemble ML pipeline works with the COMPLETE dataset."""
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor, VotingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from datetime import datetime

print("Loading complete dataset...")
df = pd.read_csv("soybean_mandi_COMPLETE.csv", low_memory=False)
df["date"] = pd.to_datetime(df["date"])
df["modal_price"] = pd.to_numeric(df["modal_price"], errors="coerce")
daily = df.groupby("date")["modal_price"].mean().reset_index().sort_values("date").reset_index(drop=True)
daily.columns = ["date", "price"]
print(f"Daily series: {len(daily)} days ({daily['date'].min().date()} to {daily['date'].max().date()})")

# Build features
print("Building 38 features...")
feat = pd.DataFrame({"price": daily["price"].values})
for lag in [1, 2, 3, 5, 7, 14, 21, 30]:
    feat[f"lag_{lag}"] = feat["price"].shift(lag)
for w in [7, 14, 30]:
    feat[f"roll_mean_{w}"] = feat["price"].rolling(w).mean()
    feat[f"roll_std_{w}"] = feat["price"].rolling(w).std()
    feat[f"roll_min_{w}"] = feat["price"].rolling(w).min()
    feat[f"roll_max_{w}"] = feat["price"].rolling(w).max()
feat["momentum_7d"] = feat["price"].pct_change(7) * 100
feat["momentum_14d"] = feat["price"].pct_change(14) * 100
feat["momentum_30d"] = feat["price"].pct_change(30) * 100
feat["vol_ratio_7_30"] = feat["roll_std_7"] / (feat["roll_std_30"] + 1e-6)
feat["mean_revert_30"] = (feat["price"] - feat["roll_mean_30"]) / (feat["roll_std_30"] + 1e-6)
feat["ema_7"] = feat["price"].ewm(span=7, adjust=False).mean()
feat["ema_21"] = feat["price"].ewm(span=21, adjust=False).mean()
feat["roc_7"] = (feat["price"] - feat["price"].shift(7)) / (feat["price"].shift(7) + 1e-6) * 100
feat["roc_14"] = (feat["price"] - feat["price"].shift(14)) / (feat["price"].shift(14) + 1e-6) * 100
bb_mid = feat["price"].rolling(20).mean()
bb_std = feat["price"].rolling(20).std()
feat["bb_position"] = (feat["price"] - bb_mid) / (2 * bb_std + 1e-6)
feat["spread_7_30"] = feat["roll_mean_7"] / (feat["roll_mean_30"] + 1e-6)
feat["month"] = daily["date"].dt.month
feat["day_of_week"] = daily["date"].dt.dayofweek
feat["day_of_year"] = daily["date"].dt.dayofyear
feat["is_harvest"] = feat["month"].isin([10, 11, 12]).astype(int)
feat["is_sowing"] = feat["month"].isin([6, 7, 8]).astype(int)
feat["quarter"] = ((feat["month"] - 1) // 3 + 1).astype(int)

def dsh(dt):
    cs = datetime(dt.year, 10, 1) if dt.month >= 10 else datetime(dt.year - 1, 10, 1)
    return (dt - cs).days

feat["days_since_harvest"] = daily["date"].apply(dsh)
feat = feat.dropna().reset_index(drop=True)
print(f"Features ready: {feat.shape[1] - 1} columns, {len(feat)} rows")

COLS = [c for c in feat.columns if c != "price"]
X = feat[COLS]
y = feat["price"]
sp = int(len(feat) * 0.8)
Xtr, Xte = X.iloc[:sp], X.iloc[sp:]
ytr, yte = y.iloc[:sp], y.iloc[sp:]
print(f"Train: {len(Xtr)}, Test: {len(Xte)}")

print("Training ensemble (GBR + RF + Ridge)...")
ens = VotingRegressor([
    ("gbr", GradientBoostingRegressor(n_estimators=400, max_depth=5, learning_rate=0.06,
                                       min_samples_split=10, min_samples_leaf=5, subsample=0.85, random_state=42)),
    ("rf", RandomForestRegressor(n_estimators=300, max_depth=12,
                                  min_samples_split=8, min_samples_leaf=4, random_state=42, n_jobs=-1)),
    ("ridge", Ridge(alpha=100.0))
], weights=[3, 2, 1])
ens.fit(Xtr, ytr)

pred = ens.predict(Xte)
mae = mean_absolute_error(yte, pred)
rmse = float(np.sqrt(mean_squared_error(yte, pred)))
mape = float(np.mean(np.abs((yte.values - pred) / yte.values)) * 100)
r2 = float(r2_score(yte, pred))

print(f"\n{'='*50}")
print(f"  ENSEMBLE MODEL RESULTS")
print(f"{'='*50}")
print(f"  MAE:      Rs {mae:.1f}")
print(f"  RMSE:     Rs {rmse:.1f}")
print(f"  MAPE:     {mape:.2f}%")
print(f"  R2:       {r2:.4f}")
print(f"  Accuracy: {100 - mape:.1f}%")
print(f"{'='*50}")
print("SUCCESS!")
