"""
yield_provider.py — pluggable soybean yield intelligence.

Traan section 9: "The product must be built with provider abstraction so
vendors can be changed over time." Same idea here — the risk engine asks for a
district yield profile and does not care where it came from.

Providers, in the order the front door tries them:

  SatSureProvider   Commercial Farm Credit Score / yield API. Needs a contract.
  GeeProvider       Google Earth Engine — in-season Sentinel-2 NDVI/LAI for the
                    CURRENT kharif. Needs a GEE service account.
  IcrisatProvider   REAL, works today, zero cost. District soybean yield history
                    (kg/ha) 1987-2017 from the ICRISAT district CSV already in
                    this repo. Gives baseline yield, trend and CDVI instability
                    — the three things yield_risk_model currently invents.

Each returns the same shape, so swapping one in changes nothing downstream.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from typing import Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ICRISAT_ZIP = os.path.join(os.path.dirname(_HERE), "ICRISAT-District Level Data.csv.zip")

# ICRISAT keeps 1966-era parent districts so the time series stays comparable.
# Newer districts inherit their parent's history.
PARENT_DISTRICT = {
    "latur": "Osmanabad",      # carved out of Osmanabad, 1982
    "hingoli": "Parbhani",     # carved out of Parbhani, 1999
    "washim": "Akola",         # carved out of Akola, 1998
    "amravati": "Amarawati",   # ICRISAT spelling
    "yavatmal": "Yeotmal",     # ICRISAT spelling
    "nashik": "Nasik",
}


def _cdvi(years: list, vals: list) -> float:
    """Cuddy-Della Valle instability index: CV * sqrt(1 - R^2). Detrended."""
    v = np.asarray(vals, dtype=float)
    if len(v) < 3 or v.mean() <= 0:
        return 15.0
    cv = v.std(ddof=1) / v.mean() * 100.0
    t = np.asarray(years, dtype=float)
    r = np.corrcoef(t, v)[0, 1] if len(set(years)) > 1 else 0.0
    r2 = 0.0 if np.isnan(r) else min(1.0, max(0.0, r * r))
    return round(float(cv * np.sqrt(1.0 - r2)), 2)


class IcrisatProvider:
    """Real district soybean yield history from the ICRISAT district dataset."""

    name = "ICRISAT district statistics"
    kind = "historical"
    _cache = None

    def available(self) -> bool:
        return os.path.exists(_ICRISAT_ZIP)

    def _load(self) -> dict:
        if IcrisatProvider._cache is not None:
            return IcrisatProvider._cache
        out = {}
        with zipfile.ZipFile(_ICRISAT_ZIP) as z:
            name = z.namelist()[0]
            rdr = csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="ignore"))
            for row in rdr:
                if "MAHARASHTRA" not in (row.get("State Name") or "").upper():
                    continue
                try:
                    y = float(row["SOYABEAN YIELD (Kg per ha)"])
                    a = float(row["SOYABEAN AREA (1000 ha)"])
                    yr = int(row["Year"])
                except (TypeError, ValueError, KeyError):
                    continue
                if y <= 0:
                    continue
                out.setdefault(row["Dist Name"].strip(), []).append((yr, y, a))
        for k in out:
            out[k].sort()
        IcrisatProvider._cache = out
        return out

    def districts(self) -> list:
        return sorted(self._load())

    def get_district_yield_profile(self, district: str) -> Optional[dict]:
        data = self._load()
        key = PARENT_DISTRICT.get(district.strip().lower(), district.strip())
        rec = data.get(key)
        if rec is None:
            rec = next((v for k, v in data.items() if k.lower() == key.lower()), None)
        if not rec:
            return None

        years = [r[0] for r in rec]
        yields = [r[1] for r in rec]
        areas = [r[2] for r in rec]
        recent = [(y, v) for y, v in zip(years, yields) if y >= max(years) - 9]
        base = float(np.mean([v for _, v in recent])) if recent else float(np.mean(yields))

        slope = float(np.polyfit(years, yields, 1)[0]) if len(years) > 2 else 0.0
        trend_pct = slope / base * 100 if base else 0.0
        direction = ("improving" if trend_pct > 0.5 else
                     "declining" if trend_pct < -0.5 else "stable")

        sub = PARENT_DISTRICT.get(district.strip().lower())
        return {
            "district": district,
            "source_district": key,
            "substituted_parent": sub if sub and sub.lower() != district.strip().lower() else None,
            "baseline_yield_kg_ha": round(base, 1),
            "latest_year": years[-1],
            "latest_yield_kg_ha": round(yields[-1], 1),
            "yield_cdvi": _cdvi(years, yields),
            "trend_pct_per_year": round(trend_pct, 2),
            "trend": direction,
            "peak_area_1000ha": round(max(areas), 1),
            "n_years": len(years),
            "year_range": f"{years[0]}-{years[-1]}",
            "provider": self.name,
            "kind": self.kind,
            "in_season": False,
        }


class GeeProvider:
    """In-season crop health from Google Earth Engine (Sentinel-2 + Sentinel-1).

    Not wired to a live GEE account yet. available() returns False until
    EE_SERVICE_ACCOUNT / EE_PRIVATE_KEY_FILE are set and earthengine-api is
    installed, so the front door falls through to ICRISAT.
    """

    name = "Google Earth Engine (Sentinel-2/1)"
    kind = "in-season"

    def available(self) -> bool:
        if not (os.getenv("EE_SERVICE_ACCOUNT") and os.getenv("EE_PRIVATE_KEY_FILE")):
            return False
        try:
            import ee  # noqa: F401
            return True
        except Exception:
            return False

    def get_district_yield_profile(self, district: str) -> Optional[dict]:
        if not self.available():
            return None
        raise NotImplementedError(
            "GEE pipeline not built yet. Plan: Sentinel-1 VH time series for the soybean "
            "crop mask (monsoon cloud makes optical unusable Jun-Sep), Sentinel-2 SR for "
            "NDVI/EVI in the cloud-free Sep-Oct window, derive peak NDVI + integrated NDVI "
            "+ date-of-peak per district, then regress those on the ICRISAT yield history.")


class SatSureProvider:
    """SatSure Sage / Farm Credit Score API. Requires a commercial contract.

    Kept as a first-class adapter so the day a key exists nothing downstream
    changes — set SATSURE_API_KEY and implement the two calls.
    """

    name = "SatSure Sage"
    kind = "commercial"
    base_url = os.getenv("SATSURE_BASE_URL", "https://api.satsure.co")

    def available(self) -> bool:
        return bool(os.getenv("SATSURE_API_KEY"))

    def get_district_yield_profile(self, district: str) -> Optional[dict]:
        if not self.available():
            return None
        raise NotImplementedError(
            "Set SATSURE_API_KEY and implement against the contracted endpoints. Ask them "
            "for (1) district/taluka soybean yield forecast covering Marathwada+Vidarbha, "
            "(2) per-plot Farm Credit Score by lat/lon or survey number. info@satsure.co")

    def get_plot_score(self, lat: float, lon: float, season: str) -> Optional[dict]:
        if not self.available():
            return None
        raise NotImplementedError("Contracted endpoint — see get_district_yield_profile.")


# provider chain: richest first, fall back to what we actually have
PROVIDERS = [SatSureProvider(), GeeProvider(), IcrisatProvider()]


def get_district_yield_profile(district: str) -> Optional[dict]:
    """Front door. Returns the best profile available, or None."""
    for p in PROVIDERS:
        try:
            if not p.available():
                continue
            prof = p.get_district_yield_profile(district)
            if prof:
                return prof
        except NotImplementedError:
            continue
        except Exception:
            continue
    return None


def provider_status() -> list:
    return [{"name": p.name, "kind": p.kind, "available": bool(p.available())}
            for p in PROVIDERS]


if __name__ == "__main__":
    for p in provider_status():
        print(f"{'OK ' if p['available'] else '-- '} {p['name']:34} ({p['kind']})")
    print()
    for d in ["Latur", "Osmanabad", "Nanded", "Beed", "Hingoli",
              "Parbhani", "Washim", "Akola", "Amravati"]:
        pr = get_district_yield_profile(d)
        if not pr:
            print(f"{d:11} no data")
            continue
        sub = f"  [via {pr['substituted_parent']}]" if pr["substituted_parent"] else ""
        print(f"{d:11} base={pr['baseline_yield_kg_ha']:7.1f} kg/ha  "
              f"CDVI={pr['yield_cdvi']:5.1f}  trend={pr['trend']:9} "
              f"({pr['trend_pct_per_year']:+.2f}%/yr)  n={pr['n_years']}{sub}")
