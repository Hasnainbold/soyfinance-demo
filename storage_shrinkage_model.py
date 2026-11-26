"""
storage_shrinkage_model.py
===========================
Warehouse-receipt weight/shrinkage loss model for soybean warehouse financing
(risk category #1 on the AgriCred roadmap: "Weight/Shrinkage Loss").

A soybean warehouse receipt is collateral by *weight*. Two independent effects
reduce the physical quintals actually recoverable at redemption:

1. Moisture loss (physical, one-time): grain deposited above the safe-storage
   moisture target sheds that excess water as it equilibrates with warehouse
   humidity. This is the dominant effect and the one farmers/investors call
   "shrinkage." It is governed by the standard grain-trade shrink formula
   (used by US grain elevators and widely applied to any commodity where
   %moisture is measured on a wet basis):

        shrink_% = (M_wet - M_dry) / (100 - M_dry) * 100

   where M_wet is the moisture % at deposit and M_dry is the safe-storage
   target moisture %. This is *not* simply "M_wet - M_dry" because moisture
   percentage is measured against total (wet) weight, so removing water
   concentrates the dry matter and the weight loss is slightly larger than
   the raw percentage-point difference. Reference: Purdue Univ. Agronomy
   Extension, "Converting Wet Corn Weight to Dry Corn Weight."

   The full potential shrink does not happen on day 1 — it is realized
   gradually as the lot ventilates/ages, modeled here with an exponential
   approach curve (~30-day half-life for a WDRA-standard godown).

2. Dry-matter loss (biological, ongoing): even at safe moisture, grain
   loses a small amount of mass every month to respiration, minor pest
   activity, and handling loss. This accrues linearly with storage_days
   and is higher for poorly managed warehouses (no collateral manager,
   not WDRA-registered).

Neither effect is measured by a live sensor feed in this project (no IoT/
moisture-logger integration exists), so this module is a *rule-based*
estimator calibrated to published storage-loss ranges, not a live
prediction. It should be replaced with an ML model once real weigh-in/
weigh-out records are available (see `to_be_predict.txt` item #1).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

# ── Calibration constants ─────────────────────────────────────────────────
# Safe long-term storage moisture target for soybean (WDRA/trade practice
# generally cites 9-10% for extended safe storage; MSP procurement allows
# up to 12% at intake). We use 10% as the equilibrium target — grain
# deposited wetter than this will shed the excess as shrink.
SAFE_STORAGE_MOISTURE_PCT = 10.0

# Moisture at/above which a lot is effectively unsafe to store long-term
# (mould/heating risk rises sharply beyond this).
HIGH_RISK_MOISTURE_PCT = 13.0

# Half-life (days) for moisture equilibration in a ventilated WDRA-standard
# godown — i.e. how quickly the "potential" moisture shrink actually
# realizes as physical weight loss.
MOISTURE_EQUILIBRATION_HALF_LIFE_DAYS = 30.0

# Ongoing biological dry-matter loss, %/month, well-managed vs poorly
# managed storage (respiration + minor pest/handling loss).
DRY_MATTER_LOSS_PCT_PER_MONTH_GOOD = 0.15
DRY_MATTER_LOSS_PCT_PER_MONTH_POOR = 0.35

# Hard cap — beyond this we're clearly in "the receipt is compromised"
# territory and the LTV/quality pillar should be flagging it directly.
MAX_MODELED_LOSS_PCT = 8.0


def _moisture_shrink_potential_pct(moisture_pct: float, target_pct: float = SAFE_STORAGE_MOISTURE_PCT) -> float:
    """
    Standard grain-trade shrink formula: % of wet weight that is "excess
    water" beyond the safe-storage target, i.e. the weight that will
    eventually be lost as the lot dries down to `target_pct`.
    """
    if moisture_pct <= target_pct:
        return 0.0
    denom = max(1e-6, 100.0 - target_pct)
    shrink = (moisture_pct - target_pct) / denom * 100.0
    return max(0.0, shrink)


def compute_shrinkage_risk(
    moisture_pct: float,
    storage_days: int,
    wdra_registered: bool = True,
    has_collateral_manager: bool = True,
) -> Dict[str, Any]:
    """
    Estimate weight/shrinkage loss and a 0-100 risk score for a warehouse
    receipt, given the deposit moisture reading and days in storage.

    Parameters
    ----------
    moisture_pct : float
        Moisture % measured at deposit (e.g. from the lab/quality slip).
    storage_days : int
        Days the lot has been in the warehouse.
    wdra_registered : bool
        Whether the warehouse is WDRA-registered (affects the biological
        loss rate — regulated warehouses are assumed better managed).
    has_collateral_manager : bool
        Whether an independent collateral manager monitors the lot.

    Returns
    -------
    dict with:
        score                       : float 0-100 (higher = more collateral erosion risk)
        estimated_weight_loss_pct   : float, total modeled loss to date
        effective_value_factor      : float in (0, 1], multiply market value by this
                                       to get the shrinkage-adjusted collateral value
        breakdown                   : dict of intermediate components
    """
    try:
        moisture = float(moisture_pct)
        days = max(0, int(storage_days))

        # 1. Moisture-driven shrink (realized fraction grows with time)
        potential_pct = _moisture_shrink_potential_pct(moisture)
        realized_fraction = 1.0 - 0.5 ** (days / MOISTURE_EQUILIBRATION_HALF_LIFE_DAYS)
        moisture_loss_pct = potential_pct * realized_fraction

        # 2. Ongoing biological / handling loss
        well_managed = bool(wdra_registered) and bool(has_collateral_manager)
        monthly_rate = DRY_MATTER_LOSS_PCT_PER_MONTH_GOOD if well_managed else DRY_MATTER_LOSS_PCT_PER_MONTH_POOR
        dry_matter_loss_pct = monthly_rate * (days / 30.0)

        total_loss_pct = min(MAX_MODELED_LOSS_PCT, moisture_loss_pct + dry_matter_loss_pct)

        # 3. Score thresholds
        if total_loss_pct < 1.0:
            score = 10.0 + total_loss_pct * 10.0          # 10-20
        elif total_loss_pct < 2.0:
            score = 20.0 + (total_loss_pct - 1.0) * 15.0   # 20-35
        elif total_loss_pct < 3.5:
            score = 35.0 + (total_loss_pct - 2.0) * 16.0   # 35-59
        elif total_loss_pct < 5.0:
            score = 59.0 + (total_loss_pct - 3.5) * 14.0   # 59-80
        else:
            score = 80.0 + min(20.0, (total_loss_pct - 5.0) * 6.7)  # 80-100

        score = round(min(100.0, max(0.0, score)), 2)
        effective_value_factor = round(max(0.0, 1.0 - total_loss_pct / 100.0), 4)

        moisture_flag = (
            "high" if moisture >= HIGH_RISK_MOISTURE_PCT else
            "elevated" if moisture > SAFE_STORAGE_MOISTURE_PCT else
            "safe"
        )

        return {
            "score": score,
            "estimated_weight_loss_pct": round(total_loss_pct, 3),
            "effective_value_factor": effective_value_factor,
            "details": "Shrinkage risk computed from moisture + storage duration.",
            "breakdown": {
                "moisture_pct": round(moisture, 2),
                "moisture_flag": moisture_flag,
                "moisture_shrink_potential_pct": round(potential_pct, 3),
                "moisture_realized_pct": round(moisture_loss_pct, 3),
                "dry_matter_loss_pct": round(dry_matter_loss_pct, 3),
                "storage_days": days,
                "well_managed_storage": well_managed,
                "safe_moisture_target_pct": SAFE_STORAGE_MOISTURE_PCT,
            },
        }
    except Exception as e:
        return {
            "score": 50.0,
            "estimated_weight_loss_pct": 2.0,
            "effective_value_factor": 0.98,
            "details": f"Error computing shrinkage risk: {e}",
            "breakdown": {},
        }


if __name__ == "__main__":
    for m, d in [(9.5, 15), (11.5, 45), (13.5, 90), (14.5, 150)]:
        r = compute_shrinkage_risk(m, d, wdra_registered=True, has_collateral_manager=True)
        print(f"moisture={m}% days={d}: loss={r['estimated_weight_loss_pct']}% "
              f"score={r['score']} factor={r['effective_value_factor']}")
