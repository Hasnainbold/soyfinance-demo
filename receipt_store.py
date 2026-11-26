"""
receipt_store.py
================
Persistence + schema for REAL warehouse receipts entered/imported by a
platform operator, as opposed to the synthetic demo receipts generated in
app.py by `generate_warehouse_receipts()`.

A real receipt is the single highest-leverage input to the risk engine:
one genuine lot with a real lab assay slip, weighbridge weight, WDRA
e-NWR number and insurance policy makes the Warehouse, LTV/Quality and
(partly) Crop-Yield pillars real instead of estimated.

Storage: a flat CSV (`real_receipts.csv`) next to app.py. Deliberately a
plain file so it's inspectable, diff-able and trivially replaced by a DB
call later. No hidden state.

Every row saved here is tagged Source="Manual" so the provenance layer
(data_provenance.py) counts its fields as operator-verified.
"""

from __future__ import annotations

import io
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_RECEIPTS_CSV = os.path.join(_APP_DIR, "real_receipts.csv")

# Known soybean districts (kept in sync with yield_risk_model.SOYBEAN_DISTRICTS)
KNOWN_LOCATIONS = [
    "Latur", "Osmanabad", "Nanded", "Beed", "Hingoli", "Parbhani", "Washim",
    "Akola", "Amravati", "Indore", "Ujjain", "Dewas", "Khandwa", "Sehore",
    "Rajgarh", "Nagpur",
]

# ── Intake schema ───────────────────────────────────────────────────────
# (key, label, type, required, default, help)
#   type: "text" | "int" | "float" | "date" | "bool" | "choice:a|b|c"
FIELDS: List[Dict[str, Any]] = [
    {"key": "receipt_id", "label": "Receipt ID", "type": "text", "required": True,
     "default": "", "help": "Your internal warehouse-receipt / e-NWR reference"},
    {"key": "farmer_name", "label": "Depositor / Farmer name", "type": "text",
     "required": True, "default": "", "help": ""},
    {"key": "location", "label": "Warehouse district", "type": "choice:" + "|".join(KNOWN_LOCATIONS),
     "required": True, "default": "Latur",
     "help": "District determines the rainfall / yield profile used by the crop-yield pillar"},
    {"key": "quantity_qtl", "label": "Quantity — receipt (qtl)", "type": "float",
     "required": True, "default": 0.0, "help": "Quintals stated on the receipt"},
    {"key": "weighbridge_qtl", "label": "Quantity — weighbridge net (qtl)", "type": "float",
     "required": False, "default": 0.0,
     "help": "Actual net weight at intake; leave 0 to use the receipt quantity"},
    {"key": "grade", "label": "Grade", "type": "choice:A|B|C", "required": True,
     "default": "B", "help": "Coarse FAQ grade; the lab assay below refines it"},
    {"key": "deposit_date", "label": "Deposit date", "type": "date", "required": True,
     "default": None, "help": ""},
    {"key": "deposit_price", "label": "Price at deposit (₹/qtl)", "type": "float",
     "required": True, "default": 0.0, "help": "Mandi modal price on the deposit date"},
    {"key": "loan_amount", "label": "Loan amount (₹)", "type": "float", "required": True,
     "default": 0.0, "help": "Disbursed principal against this receipt"},

    # ── Lab assay slip ──
    {"key": "moisture_pct", "label": "Moisture %", "type": "float", "required": False,
     "default": 0.0, "help": "From the lab / quality slip at intake"},
    {"key": "oil_content_pct", "label": "Oil content %", "type": "float", "required": False,
     "default": 0.0, "help": ""},
    {"key": "protein_content_pct", "label": "Protein content %", "type": "float",
     "required": False, "default": 0.0, "help": ""},
    {"key": "foreign_matter_pct", "label": "Foreign matter %", "type": "float",
     "required": False, "default": 0.0, "help": ""},

    # ── Warehouse / WDRA ──
    {"key": "wdra_registered", "label": "WDRA-registered warehouse", "type": "bool",
     "required": False, "default": True, "help": ""},
    {"key": "wdra_registration_number", "label": "WDRA e-NWR / registration no.",
     "type": "text", "required": False, "default": "",
     "help": "Enables registry verification; a blank here is scored as unverified"},
    {"key": "last_inspection_date", "label": "Last physical inspection date",
     "type": "date", "required": False, "default": None, "help": ""},

    # ── Insurance ──
    {"key": "insurance_coverage", "label": "Insurance coverage", "type": "choice:full|partial|none",
     "required": False, "default": "partial", "help": ""},
    {"key": "insurance_expiry_date", "label": "Policy expiry date", "type": "date",
     "required": False, "default": None, "help": ""},
    {"key": "sum_insured", "label": "Sum insured (₹)", "type": "float", "required": False,
     "default": 0.0, "help": "Used for the coverage-ratio check vs market value"},
    {"key": "has_collateral_manager", "label": "Independent collateral manager appointed",
     "type": "bool", "required": False, "default": True, "help": ""},

    # ── Borrower ──
    {"key": "borrower_total_txns", "label": "Borrower — total past transactions",
     "type": "int", "required": False, "default": 0, "help": ""},
    {"key": "borrower_successful_txns", "label": "Borrower — successful transactions",
     "type": "int", "required": False, "default": 0, "help": ""},
    {"key": "years_on_platform", "label": "Borrower — years on platform", "type": "int",
     "required": False, "default": 0, "help": ""},
    {"key": "kyc_verified", "label": "Borrower — KYC verified", "type": "bool",
     "required": False, "default": True, "help": ""},
    {"key": "is_fpo_member", "label": "Borrower — FPO member", "type": "bool",
     "required": False, "default": False, "help": ""},
    {"key": "past_defaults", "label": "Borrower — past defaults", "type": "int",
     "required": False, "default": 0, "help": ""},
]

FIELD_KEYS = [f["key"] for f in FIELDS]


def _coerce(field: Dict[str, Any], value: Any) -> Any:
    t = field["type"]
    try:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return field["default"]
        if t == "int":
            return int(float(value))
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "y", "t")
        if t == "date":
            if isinstance(value, (date, datetime)):
                return pd.Timestamp(value).strftime("%Y-%m-%d")
            s = str(value).strip()
            try:
                return pd.to_datetime(s).strftime("%Y-%m-%d")           # ISO / unambiguous
            except Exception:
                return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d")  # DD/MM/YYYY
        if t.startswith("choice:"):
            return str(value).strip()
        return str(value).strip()
    except Exception:
        return field["default"]


def normalize_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a raw dict (from the form or a CSV row) to the canonical schema."""
    rec = {}
    for f in FIELDS:
        rec[f["key"]] = _coerce(f, raw.get(f["key"]))
    return rec


def validate_record(rec: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable problems; empty list = OK to save."""
    problems: List[str] = []
    for f in FIELDS:
        if f["required"]:
            v = rec.get(f["key"])
            if v in (None, "", 0, 0.0):
                problems.append(f"{f['label']} is required")
    q = rec.get("quantity_qtl") or 0
    if q <= 0:
        problems.append("Quantity must be greater than 0")
    if (rec.get("loan_amount") or 0) <= 0:
        problems.append("Loan amount must be greater than 0")
    if (rec.get("deposit_price") or 0) <= 0:
        problems.append("Deposit price must be greater than 0")
    bt = rec.get("borrower_total_txns") or 0
    bs = rec.get("borrower_successful_txns") or 0
    if bs > bt:
        problems.append("Successful transactions cannot exceed total transactions")
    return problems


def load_real_receipts() -> pd.DataFrame:
    """Load saved real receipts as a canonical-schema DataFrame (may be empty)."""
    if not os.path.exists(REAL_RECEIPTS_CSV):
        return pd.DataFrame(columns=FIELD_KEYS + ["saved_at"])
    try:
        df = pd.read_csv(REAL_RECEIPTS_CSV)
    except Exception:
        return pd.DataFrame(columns=FIELD_KEYS + ["saved_at"])
    for k in FIELD_KEYS:
        if k not in df.columns:
            df[k] = None
    return df


def append_real_receipt(rec: Dict[str, Any]) -> None:
    """Validate-free append; caller should validate first."""
    rec = normalize_record(rec)
    rec["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df = load_real_receipts()
    new_row = pd.DataFrame([rec])
    df = new_row if df.empty else pd.concat([df, new_row], ignore_index=True)
    df.to_csv(REAL_RECEIPTS_CSV, index=False)


def import_csv(file_or_buffer) -> Dict[str, Any]:
    """
    Bulk-import receipts from an uploaded CSV. Returns
    {"added": n, "skipped": [(row_index, [problems])], "total": n}.
    """
    df = pd.read_csv(file_or_buffer)
    added, skipped = 0, []
    for i, row in df.iterrows():
        rec = normalize_record(row.to_dict())
        problems = validate_record(rec)
        if problems:
            skipped.append((int(i), problems))
            continue
        append_real_receipt(rec)
        added += 1
    return {"added": added, "skipped": skipped, "total": len(df)}


def blank_template_csv() -> str:
    """A header row + one worked example, as a CSV string for download."""
    example = {
        "receipt_id": "WR-REAL-0001", "farmer_name": "Ramesh Patidar",
        "location": "Indore", "quantity_qtl": 250, "weighbridge_qtl": 248.4,
        "grade": "A", "deposit_date": "2026-06-18", "deposit_price": 4720,
        "loan_amount": 850000, "moisture_pct": 10.8, "oil_content_pct": 18.9,
        "protein_content_pct": 37.2, "foreign_matter_pct": 1.1,
        "wdra_registered": True, "wdra_registration_number": "MP-ENWR-45821",
        "last_inspection_date": "2026-07-30", "insurance_coverage": "full",
        "insurance_expiry_date": "2026-12-31", "sum_insured": 1150000,
        "has_collateral_manager": True, "borrower_total_txns": 14,
        "borrower_successful_txns": 14, "years_on_platform": 3,
        "kyc_verified": True, "is_fpo_member": True, "past_defaults": 0,
    }
    buf = io.StringIO()
    pd.DataFrame([{k: example.get(k, "") for k in FIELD_KEYS}]).to_csv(buf, index=False)
    return buf.getvalue()


def to_display_rows(current_price: float) -> Optional[pd.DataFrame]:
    """
    Convert saved real receipts into rows shaped exactly like
    app.generate_warehouse_receipts() output, so they drop straight into
    the same tables / risk page. Tagged Source="Manual".
    """
    src = load_real_receipts()
    if src is None or len(src) == 0:
        return None

    today = pd.Timestamp(datetime.now().date())
    rows = []
    for _, r in src.iterrows():
        rec = normalize_record(r.to_dict())
        qty = rec["weighbridge_qtl"] or rec["quantity_qtl"]
        deposit_price = rec["deposit_price"] or float(current_price)
        market_val = int(round(qty * deposit_price))
        loan_amt = int(round(rec["loan_amount"]))
        try:
            dep_dt = pd.to_datetime(rec["deposit_date"])
            storage_days = max(0, int((today - dep_dt).days))
        except Exception:
            dep_dt, storage_days = today, 0

        rows.append({
            "Receipt ID": rec["receipt_id"] or f"WR-REAL-{len(rows)+1:04d}",
            "Farmer Name": rec["farmer_name"] or "—",
            "Location": rec["location"],
            "Quantity (qtl)": int(round(qty)),
            "Grade": rec["grade"],
            "Deposit Date": dep_dt.strftime("%Y-%m-%d"),
            "Market Value": market_val,
            "Loan Amount": loan_amt,
            "Status": "Active",
            "Risk Score": 0,
            "Tenure (days)": 90,
            "WDRA Registered": bool(rec["wdra_registered"]),
            "WDRA Reg Number": rec["wdra_registration_number"] or None,
            "Last Inspection": rec["last_inspection_date"] or None,
            "Insurance": rec["insurance_coverage"] or "none",
            "Insurance Expiry": rec["insurance_expiry_date"] or None,
            "Sum Insured": int(round(rec["sum_insured"] or 0)),
            "Collateral Manager": bool(rec["has_collateral_manager"]),
            "Storage Days": storage_days,
            "Deposit Price": int(round(deposit_price)),
            "Moisture %": rec["moisture_pct"] or None,
            "Oil Content %": rec["oil_content_pct"] or None,
            "Protein Content %": rec["protein_content_pct"] or None,
            "Foreign Matter %": rec["foreign_matter_pct"] or None,
            "Borrower Txns": int(rec["borrower_total_txns"] or 0),
            "Borrower Success": int(rec["borrower_successful_txns"] or 0),
            "Years on Platform": int(rec["years_on_platform"] or 0),
            "KYC Verified": bool(rec["kyc_verified"]),
            "FPO Member": bool(rec["is_fpo_member"]),
            "Past Defaults": int(rec["past_defaults"] or 0),
            "Source": "Manual",
        })
    return pd.DataFrame(rows)
