"""
data_provenance.py
==================
Provenance / data-lineage layer for the AgriCred risk engine.

The risk engine (risk_engine.py + the 4 pillar models) is deliberately
source-agnostic: it scores whatever numbers it is handed. That means a
score computed from a genuine lab assay slip and one computed from a
`np.random.uniform()` demo value look identical downstream.

This module makes the difference visible. For a given receipt it labels
every input that feeds each of the 6 pillars as one of:

    real      - pulled live from an external system of record
                (mandi price API, IMD rainfall, WDRA e-NWR, ...)
    manual    - entered / uploaded by a platform operator for a real lot
                (lab slip, weighbridge ticket, insurance policy)
    reference - a published constant / lookup table, not lot-specific
                (MSP by crop year, FAQ trade specs)
    estimated - synthetic or default demo value, NOT tied to a real lot

`summarize()` rolls those labels up into a per-pillar and overall
coverage figure the UI can show next to the score, so an investor sees
"composite 34 / 100, computed on 71% verified inputs" rather than a bare
number.

Nothing here changes a score. It only annotates.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ── Provenance labels ────────────────────────────────────────────────────
REAL = "real"
MANUAL = "manual"
REFERENCE = "reference"
ESTIMATED = "estimated"

# Order used for display + "worst wins" roll-up
_LABEL_ORDER = [REAL, MANUAL, REFERENCE, ESTIMATED]

LABEL_META = {
    REAL:      {"icon": "🟢", "text": "Live source",  "verified": True},
    MANUAL:    {"icon": "🔵", "text": "Operator-entered", "verified": True},
    REFERENCE: {"icon": "🟣", "text": "Reference table", "verified": True},
    ESTIMATED: {"icon": "🟠", "text": "Estimated / demo", "verified": False},
}

# ── Which inputs feed which pillar ───────────────────────────────────────
# Each entry: (logical_input_label, kind)
#   kind = "receipt"  -> resolved from the receipt's per-field provenance map
#   kind = "<label>"  -> a fixed provenance label (real / reference / ...)
#
# Keys match risk_engine.compute_composite_risk_score()'s pillar_scores keys.
PILLAR_INPUTS: Dict[str, List[Tuple[str, str]]] = {
    "price_level": [
        ("Live mandi price", REAL),
        ("Price history (2y)", REAL),
        ("90-day price forecast", REAL),
        ("MSP (crop year)", REFERENCE),
        ("Deposit price", "receipt:deposit_price"),
    ],
    "volatility": [
        ("Daily price series", REAL),
    ],
    "warehouse": [
        ("WDRA registered", "receipt:wdra_registered"),
        ("WDRA e-NWR number", "receipt:wdra_registration_number"),
        ("Last inspection date", "receipt:last_inspection_date"),
        ("Insurance coverage", "receipt:insurance_coverage"),
        ("Insurance expiry", "receipt:insurance_expiry_date"),
        ("Sum insured", "receipt:sum_insured"),
        ("Storage days", "receipt:storage_days"),
        ("Collateral manager", "receipt:has_collateral_manager"),
        ("Warehouse location", "receipt:location"),
    ],
    "ltv_quality": [
        ("Loan amount", "receipt:loan_amount"),
        ("Market value at deposit", "receipt:market_value"),
        ("Quantity (qtl)", "receipt:quantity"),
        ("Live mandi price", REAL),
        ("Grade", "receipt:grade"),
        ("Moisture %", "receipt:moisture_level"),
        ("Oil content %", "receipt:oil_content_pct"),
        ("Protein content %", "receipt:protein_content_pct"),
        ("Foreign matter %", "receipt:foreign_matter_pct"),
    ],
    "crop_yield": [
        ("Warehouse location", "receipt:location"),
        ("District rainfall deviation", "rainfall"),
        ("District yield history", "yield_history"),
        ("Crop-cycle month", REFERENCE),
    ],
    "borrower": [
        ("Total transactions", "receipt:total_transactions"),
        ("Successful transactions", "receipt:successful_transactions"),
        ("Years on platform", "receipt:years_on_platform"),
        ("KYC verified", "receipt:kyc_verified"),
        ("FPO member", "receipt:is_fpo_member"),
        ("Past defaults", "receipt:past_defaults"),
    ],
}

PILLAR_LABELS = {
    "price_level": "Price Level",
    "volatility": "Price Volatility",
    "warehouse": "Warehouse / Storage",
    "ltv_quality": "LTV & Quality",
    "crop_yield": "Crop Yield / Weather",
    "borrower": "Borrower Credit",
}

# Receipt fields that are structural (always known for any real lot) vs
# fields that must come from an external check / lab to count as verified.
_ALWAYS_KNOWN_IF_MANUAL = {
    "deposit_price", "loan_amount", "market_value", "quantity", "grade",
    "storage_days", "location", "wdra_registered", "insurance_coverage",
    "has_collateral_manager", "years_on_platform", "kyc_verified",
    "is_fpo_member", "past_defaults", "total_transactions",
    "successful_transactions",
}


def build_receipt_field_provenance(receipt_row: dict, source: str) -> Dict[str, str]:
    """
    Given one receipt (dict / Series-as-dict) and its Source tag
    ("Manual" for an operator-entered real lot, anything else = synthetic),
    return {engine_field_name: provenance_label} for every field the
    engine consumes.

    A synthetic receipt -> every field ESTIMATED.
    A manual receipt    -> every supplied field MANUAL; blank optional
                           fields (e.g. an assay parameter left empty)
                           fall back to ESTIMATED.
    """
    engine_fields = [
        "deposit_price", "loan_amount", "market_value", "quantity", "grade",
        "storage_days", "location", "wdra_registered",
        "wdra_registration_number", "last_inspection_date",
        "insurance_coverage", "insurance_expiry_date", "sum_insured",
        "has_collateral_manager", "moisture_level", "oil_content_pct",
        "protein_content_pct", "foreign_matter_pct", "total_transactions",
        "successful_transactions", "years_on_platform", "kyc_verified",
        "is_fpo_member", "past_defaults",
    ]
    is_manual = str(source).strip().lower() == "manual"
    if not is_manual:
        return {f: ESTIMATED for f in engine_fields}

    # Column aliases as they appear on the display DataFrame
    display_alias = {
        "deposit_price": "Deposit Price", "loan_amount": "Loan Amount",
        "market_value": "Market Value", "quantity": "Quantity (qtl)",
        "grade": "Grade", "storage_days": "Storage Days", "location": "Location",
        "wdra_registered": "WDRA Registered",
        "wdra_registration_number": "WDRA Reg Number",
        "last_inspection_date": "Last Inspection", "insurance_coverage": "Insurance",
        "insurance_expiry_date": "Insurance Expiry", "sum_insured": "Sum Insured",
        "has_collateral_manager": "Collateral Manager", "moisture_level": "Moisture %",
        "oil_content_pct": "Oil Content %", "protein_content_pct": "Protein Content %",
        "foreign_matter_pct": "Foreign Matter %", "total_transactions": "Borrower Txns",
        "successful_transactions": "Borrower Success",
        "years_on_platform": "Years on Platform", "kyc_verified": "KYC Verified",
        "is_fpo_member": "FPO Member", "past_defaults": "Past Defaults",
    }

    def _present(val) -> bool:
        if val is None:
            return False
        try:
            import math
            if isinstance(val, float) and math.isnan(val):
                return False
        except Exception:
            pass
        s = str(val).strip().lower()
        return s not in ("", "nan", "none", "0", "0.0")

    out: Dict[str, str] = {}
    for f in engine_fields:
        raw = receipt_row.get(display_alias.get(f, f), receipt_row.get(f))
        if _present(raw) or (f in _ALWAYS_KNOWN_IF_MANUAL and raw is not None):
            out[f] = MANUAL
        else:
            out[f] = ESTIMATED
    return out


def _resolve(kind: str, field_prov: Dict[str, str], context: Dict[str, str]) -> str:
    """Resolve one PILLAR_INPUTS entry's `kind` to a concrete provenance label."""
    if kind in _LABEL_ORDER:
        return kind
    if kind.startswith("receipt:"):
        return field_prov.get(kind.split(":", 1)[1], ESTIMATED)
    if kind == "rainfall":
        return context.get("rainfall", ESTIMATED)
    if kind == "yield_history":
        return context.get("yield_history", ESTIMATED)
    return ESTIMATED


def summarize(
    receipt_row: dict,
    source: str,
    *,
    rainfall_is_real: bool = False,
    production_is_real: bool = False,
    yield_history_is_real: bool = False,
) -> dict:
    """
    Produce the full provenance summary for one receipt's risk score.

    Returns
    -------
    dict with:
        per_pillar : {pillar_key: {
            counts: {label: n}, total: n, verified_pct: float,
            fields: [(label_text, provenance_label), ...],
            grade: "Verified" | "Mostly verified" | "Partial" | "Demo data"
        }}
        overall : {counts, total, verified_pct, grade}
        context : {rainfall, yield_history, production}
    """
    field_prov = build_receipt_field_provenance(receipt_row, source)
    context = {
        "rainfall": REAL if rainfall_is_real else ESTIMATED,
        "yield_history": REAL if yield_history_is_real else ESTIMATED,
        "production": REAL if production_is_real else ESTIMATED,
    }

    per_pillar = {}
    overall_counts = {lbl: 0 for lbl in _LABEL_ORDER}
    overall_total = 0

    for pillar, inputs in PILLAR_INPUTS.items():
        counts = {lbl: 0 for lbl in _LABEL_ORDER}
        fields = []
        for label_text, kind in inputs:
            prov = _resolve(kind, field_prov, context)
            counts[prov] += 1
            fields.append((label_text, prov))
        total = sum(counts.values())
        verified = counts[REAL] + counts[MANUAL] + counts[REFERENCE]
        verified_pct = round(verified / total * 100, 1) if total else 0.0
        per_pillar[pillar] = {
            "counts": counts,
            "total": total,
            "verified_pct": verified_pct,
            "fields": fields,
            "grade": _grade(verified_pct),
        }
        for lbl in _LABEL_ORDER:
            overall_counts[lbl] += counts[lbl]
        overall_total += total

    ov_verified = overall_counts[REAL] + overall_counts[MANUAL] + overall_counts[REFERENCE]
    ov_pct = round(ov_verified / overall_total * 100, 1) if overall_total else 0.0

    return {
        "per_pillar": per_pillar,
        "overall": {
            "counts": overall_counts,
            "total": overall_total,
            "verified_pct": ov_pct,
            "grade": _grade(ov_pct),
        },
        "context": context,
    }


def _grade(verified_pct: float) -> str:
    if verified_pct >= 90:
        return "Verified"
    if verified_pct >= 65:
        return "Mostly verified"
    if verified_pct >= 35:
        return "Partial"
    return "Demo data"


GRADE_COLORS = {
    "Verified": "#10b981",
    "Mostly verified": "#84cc16",
    "Partial": "#f59e0b",
    "Demo data": "#ef4444",
}
