"""
fpo_verification.py -- verify a Farmer Producer Organisation (FPO) that is BORROWING
against warehouse receipts. Mirrors borrower_verification_demo.py: everything that can
be checked for free is checked for real; anything that needs a paid/licensed source is
labelled as such, never faked.

An FPO borrower is a company (a Producer Company, Companies Act 2013 Ch. XXIA), so the
questions are different from an individual's:

    1. IDENTITY      is it a real, active producer company, and do its IDs agree?
                     CIN structure  · GSTIN checksum · PAN embedded in GSTIN = company PAN
                     · PAN is a company PAN whose 5th letter = first letter of the name
                     · GST state = the CIN's RoC state · MCA status "Active"
    2. GOVERNANCE    5-15 directors (producer-company rule), a CEO, directors who are
                     themselves members, DINs well-formed
    3. MEMBERSHIP    real member farmers, no duplicate members, land on record -- and the
       & CAPACITY    KEY CHECK: could the members actually have grown the soybean the FPO
                     is pledging?  pledged quintals vs member soybean area x yield
    4. FINANCIALS    net worth, debt/equity, current ratio, profitability, growth
    5. BANKING       statement conduct (bounces, balances) and whether bank credits
                     support the turnover the FPO declares
    6. COMPLIANCE    age, last AGM / balance-sheet date, GST registered

REAL, free, no contract
    - CIN decode + validation (structure, class, industry code, RoC, year)
    - GSTIN checksum (the official mod-36 algorithm) and PAN extraction
    - PAN structure, entity type, name-initial rule
    - MCA "View Company Master Data" -- the page is free on mca.gov.in but sits behind a
      captcha, so the operator opens it, copies the page, and pastes it here; we parse it
    - Member list (CSV/XLSX) analytics + production-capacity check
    - Bank statement analysis (reuses borrower_verification_demo's parser)
    - IFSC / PIN live lookups (free public registries)
    - FPO risk score (0-100, higher = riskier, same bands as risk_engine)
    - Audit JSON to ./verification_runs/

STILL NEEDS A PAID / LICENSED SOURCE (clearly labelled in the UI)
    - Automated MCA master-data / charge (loan) register pull  (Surepass / Karza / etc.)
    - Live GSTN taxpayer status + return-filing history          (GSP licence)
    - Company credit-bureau report (CIBIL Rank / commercial)      (bureau membership)
    - Director KYC / DIN status beyond format                     (MCA / vendor)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import date, datetime

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verification_runs")

# ───────────────────────────────────────────────────────────── reference ──
# CIN = L/U · 5-digit NIC industry · 2-letter RoC · 4-digit year · 3-letter class · 6 digits
CIN_RE = re.compile(r"^([LU])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$")
CIN_CLASS = {
    "PTC": "Private limited", "PLC": "Public limited", "OPC": "One person company",
    "NPL": "Section 8 (not for profit)", "GOI": "Government of India company",
    "SGC": "State government company", "FTC": "Subsidiary of a foreign company",
    "FLC": "Foreign company (public)", "GAP": "Guarantee + share capital (public)",
    "GAT": "Guarantee + share capital (private)", "ULL": "Unlimited (public)",
    "ULT": "Unlimited (private)",
}
# RoC code in the CIN -> GST state code. Several RoCs sit in one state (MH = Mumbai,
# PN = Pune; TN = Chennai, TZ = Coimbatore). Unknown codes are a warning, not a failure.
ROC_TO_GST_STATE = {
    "MH": ("27", "Maharashtra"), "PN": ("27", "Maharashtra"), "GJ": ("24", "Gujarat"),
    "MP": ("23", "Madhya Pradesh"), "CT": ("22", "Chhattisgarh"), "RJ": ("08", "Rajasthan"),
    "UP": ("09", "Uttar Pradesh"), "KA": ("29", "Karnataka"), "TN": ("33", "Tamil Nadu"),
    "TZ": ("33", "Tamil Nadu"), "AP": ("37", "Andhra Pradesh"), "TG": ("36", "Telangana"),
    "KL": ("32", "Kerala"), "WB": ("19", "West Bengal"), "OR": ("21", "Odisha"),
    "BR": ("10", "Bihar"), "JH": ("20", "Jharkhand"), "PB": ("03", "Punjab"),
    "HR": ("06", "Haryana"), "HP": ("02", "Himachal Pradesh"), "DL": ("07", "Delhi"),
    "UR": ("05", "Uttarakhand"), "AS": ("18", "Assam"), "GA": ("30", "Goa"),
    "JK": ("01", "Jammu & Kashmir"), "CH": ("04", "Chandigarh"), "PY": ("34", "Puducherry"),
}
GST_STATE_NAME = {v[0]: v[1] for v in ROC_TO_GST_STATE.values()}
# NIC 2008 divisions an FPO plausibly sits in: crop/animal (01), forestry (02), fishing (03),
# food manufacturing (10), wholesale trade (46), warehousing (52)
FPO_NIC_DIVISIONS = {"01": "Crop & animal production", "02": "Forestry", "03": "Fishing",
                     "10": "Food products manufacturing", "46": "Wholesale trade",
                     "52": "Warehousing & storage"}

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PAN_ENTITY = {"P": "Individual", "C": "Company", "H": "HUF", "F": "Firm / LLP",
              "A": "Association of persons", "T": "Trust", "B": "Body of individuals",
              "L": "Local authority", "J": "Artificial juridical person", "G": "Government"}
GSTIN_RE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIN_RE = re.compile(r"^\d{8}$")

# Producer-company rules (Companies Act 2013, Ch. XXIA) and 10,000-FPO scheme norms
MIN_DIRECTORS, MAX_DIRECTORS = 5, 15
MIN_MEMBERS_LEGAL = 10            # producers needed to incorporate a producer company
SCHEME_MIN_MEMBERS_PLAINS = 300   # 10,000-FPO scheme norm, plains
SCHEME_MIN_MEMBERS_HILLY = 100    # NE / hilly areas


# ────────────────────────────────────────────────────────── ID validators ──
def gstin_check_char(first14: str) -> str:
    """Official GSTIN check digit (mod-36, alternating weights 1/2)."""
    total = 0
    for i, ch in enumerate(first14):
        prod = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += prod // 36 + prod % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def validate_gstin(gstin: str) -> dict:
    g = re.sub(r"\s", "", gstin or "").upper()
    out = {"input": g, "valid_format": False, "checksum_ok": False}
    m = GSTIN_RE.match(g)
    if not m:
        out["reason"] = "not 15 characters of the GSTIN pattern (SS + PAN + n + Z + check)"
        return out
    out.update(valid_format=True, state_code=m.group(1),
               state=GST_STATE_NAME.get(m.group(1), f"state code {m.group(1)}"),
               pan=m.group(2), entity_no=m.group(3))
    out["checksum_ok"] = gstin_check_char(g[:14]) == g[14]
    if not out["checksum_ok"]:
        out["reason"] = f"check digit should be {gstin_check_char(g[:14])}, not {g[14]} — mistyped or fabricated"
    return out


def validate_pan(pan: str, entity_name: str = "") -> dict:
    p = re.sub(r"\s", "", pan or "").upper()
    out = {"input": p, "valid_format": bool(PAN_RE.match(p))}
    if not out["valid_format"]:
        out["reason"] = "PAN must be 5 letters, 4 digits, 1 letter"
        return out
    out["entity_type"] = PAN_ENTITY.get(p[3], f"unknown ({p[3]})")
    out["is_company"] = p[3] == "C"
    # for non-individuals the 5th character is the first letter of the entity's name
    first = _name_initial(entity_name)
    out["name_initial_ok"] = (p[4] == first) if first else None
    out["expected_initial"] = first
    return out


def _name_initial(name: str) -> str | None:
    words = [w for w in re.sub(r"[^A-Za-z ]", " ", name or "").upper().split()
             if w not in ("THE", "M/S", "MS")]
    return words[0][0] if words else None


def validate_cin(cin: str) -> dict:
    c = re.sub(r"\s", "", cin or "").upper()
    out = {"input": c, "valid_format": False}
    m = CIN_RE.match(c)
    if not m:
        out["reason"] = ("CIN must be 21 characters: L/U + 5-digit industry code + 2-letter "
                         "RoC + year + 3-letter class + 6-digit number")
        return out
    listing, nic, roc, year, cls, regno = m.groups()
    yr = int(year)
    out.update(valid_format=True, listed=listing == "L", nic=nic, nic_division=nic[:2],
               nic_label=FPO_NIC_DIVISIONS.get(nic[:2], "other activity"),
               roc=roc, roc_state=ROC_TO_GST_STATE.get(roc, (None, None))[1],
               gst_state_code=ROC_TO_GST_STATE.get(roc, (None, None))[0],
               year=yr, class_code=cls, class_label=CIN_CLASS.get(cls, "unknown class"),
               reg_no=regno)
    out["year_plausible"] = 1850 <= yr <= date.today().year
    # producer companies are incorporated as private companies (class PTC)
    out["producer_company_class"] = cls == "PTC"
    out["agri_industry"] = nic[:2] in FPO_NIC_DIVISIONS
    return out


# ────────────────────────────────────────────── MCA master-data paste parser ──
_MCA_FIELDS = {
    "company_name": r"Company Name",
    "cin": r"CIN",
    "roc": r"ROC(?: Name| Code)?",
    "registration_number": r"Registration Number",
    "category": r"Company Category|Category of (?:the )?Company",
    "subcategory": r"Company Sub[- ]?Category|Sub[- ]?category of (?:the )?Company",
    "class": r"Class of Company",
    "authorised_capital": r"Authori[sz]ed Capital(?:\s*\(Rs\))?",
    "paid_up_capital": r"Paid[- ]up Capital(?:\s*\(Rs\))?",
    "incorporation_date": r"Date of Incorporation",
    "status": r"Company Status(?:\s*\(for e-filing\))?|Status",
    "last_agm": r"Date of last AGM|Date of Last Annual General Meeting",
    "last_balance_sheet": r"Date of Balance Sheet|Date of Latest Balance Sheet",
    "email": r"Email Id",
    "address": r"Registered Address",
    "listing": r"Whether Listed or not|Listing Status|Listed in Stock Exchange\(s\) \(Y/N\)",
}
_DATE_FMTS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y",
              "%d %B %Y", "%m/%d/%Y")


def parse_date(s) -> date | None:
    if isinstance(s, date):
        return s if not isinstance(s, datetime) else s.date()
    s = (str(s or "")).strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def parse_money(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = re.sub(r"[^\d.]", "", str(s))
    try:
        return float(t) if t else None
    except ValueError:
        return None


def parse_mca_master_text(text: str) -> dict:
    """
    Parse text copied from MCA's 'View Company/LLP Master Data' page.

    Handles both "Label: value", "Label<TAB>value" and label-on-one-line/value-on-next,
    which is what a browser copy of that page produces. Directors (DIN + name +
    designation) are read from the 'Directors/Signatory Details' table when present.
    """
    lines = [l.strip() for l in (text or "").replace("\r", "").split("\n")]
    lines = [l for l in lines if l]
    rec = {k: None for k in _MCA_FIELDS}
    for i, line in enumerate(lines):
        for key, lab in _MCA_FIELDS.items():
            if rec[key]:
                continue
            m = re.match(rf"^\s*(?:{lab})\s*[:\t]\s*(.+)$", line, re.I)
            if m and m.group(1).strip() not in ("-", ""):
                rec[key] = m.group(1).strip()
            elif re.fullmatch(rf"\s*(?:{lab})\s*:?\s*", line, re.I) and i + 1 < len(lines):
                nxt = lines[i + 1]
                if not any(re.fullmatch(rf"\s*(?:{l2})\s*:?\s*", nxt, re.I)
                           for l2 in _MCA_FIELDS.values()):
                    rec[key] = nxt
    # the bare "CIN" label can catch the CIN of a similarly named field; prefer a real CIN
    m = re.search(r"\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b", text or "")
    if m:
        rec["cin"] = m.group(1)
    rec["authorised_capital_rs"] = parse_money(rec["authorised_capital"])
    rec["paid_up_capital_rs"] = parse_money(rec["paid_up_capital"])
    for k in ("incorporation_date", "last_agm", "last_balance_sheet"):
        rec[k + "_parsed"] = parse_date(rec[k]).isoformat() if parse_date(rec[k]) else None
    directors = []
    # DIN (8 digits) for directors; a CEO / CS without a DIN is listed by PAN
    for m in re.finditer(r"\b(\d{8}|[A-Z]{5}\d{4}[A-Z])\b[\t ]+([A-Z][A-Za-z .']{2,60}?)[\t ]+"
                         r"(Director|Managing Director|Whole[- ]?time Director|Additional Director|"
                         r"Nominee Director|CEO|Chief Executive Officer|Company Secretary|"
                         r"Chairman|Chairperson)\b", text or "", re.I):
        directors.append({"din": m.group(1), "name": m.group(2).strip(),
                          "designation": m.group(3)})
    rec["directors"] = directors
    return rec


# ───────────────────────────────────────────────────────── member analytics ──
_MEMBER_COLS = {
    "name": ("name", "member name", "farmer name", "shareholder", "member"),
    "village": ("village", "gaon", "gav"),
    "mobile": ("mobile", "phone", "contact", "mobile no"),
    "aadhaar_last4": ("aadhaar", "aadhar", "uid"),
    "land_ha": ("land", "land ha", "land_ha", "area", "holding", "land holding",
                "land (ha)", "area (ha)", "total land"),
    "soybean_ha": ("soybean", "soyabean", "soybean ha", "soybean area", "crop area"),
    "gender": ("gender", "sex"),
    "shares": ("shares", "share", "no of shares", "share capital", "shares held"),
    "is_director": ("director", "is_director", "board"),
}


def _map_cols(cols) -> dict:
    low = {c: re.sub(r"[_\s]+", " ", str(c).strip().lower()) for c in cols}
    out = {}
    for key, cands in _MEMBER_COLS.items():
        for c, l in low.items():
            if c in out.values():
                continue
            if l in cands or any(l.startswith(x) for x in cands if len(x) > 3):
                out[key] = c
                break
    return out


def read_member_file(file):
    """CSV / XLSX -> pandas DataFrame with our standard column names."""
    import pandas as pd
    name = getattr(file, "name", str(file)).lower()
    data = file.getvalue() if hasattr(file, "getvalue") else (
        file.read() if hasattr(file, "read") else open(file, "rb").read())
    if name.endswith((".xlsx", ".xls")):
        raw = pd.read_excel(io.BytesIO(data))
    else:
        raw = pd.read_csv(io.BytesIO(data))
    m = _map_cols(raw.columns)
    df = pd.DataFrame({k: raw[c] for k, c in m.items()})
    return df, m


def analyze_members(df, *, pledged_quintals: float | None = None,
                    soybean_yield_t_per_ha: float = 1.0, hilly_area: bool = False,
                    director_names: list | None = None) -> dict:
    """
    Member-base quality and the production-capacity check.

    Capacity: the soybean an FPO pledges must come from its members' fields. Members'
    soybean area (or, failing that, their total land) x yield gives what they could have
    grown this season. Pledging more than that means the stock came from somewhere else
    (traders, other FPOs, or it doesn't exist).
    """
    import pandas as pd
    n = int(len(df))
    out = {"members": n}
    norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    # Duplicates = the SAME person entered twice: same mobile, same Aadhaar digits, or an
    # identical row. Two members sharing a name in one village is common (namesakes), so
    # that alone is only surfaced for a spot-check, never counted as a duplicate.
    extra = {}
    for col in ("mobile", "aadhaar_last4"):
        if col in df:
            v = df[col].astype(str).str.replace(r"\D", "", regex=True)
            v = v[v.str.len() >= 4]
            extra[col] = int(v.duplicated(keep="first").sum())
            out[f"duplicate_{col}"] = extra[col]
    full = df.astype(str).apply(lambda r: "|".join(norm(x) for x in r), axis=1)
    extra["identical_rows"] = int(full.duplicated(keep="first").sum())
    out["duplicate_identical_rows"] = extra["identical_rows"]
    if "name" in df:
        key = df["name"].map(norm) + ("|" + df["village"].map(norm) if "village" in df else "")
        out["namesake_rows"] = int((key.duplicated(keep=False) & (key.str.len() > 1)).sum())
    out["duplicate_rows"] = max(extra.values()) if extra else 0
    out["duplicate_rate"] = round(out["duplicate_rows"] / n, 3) if n else 0.0

    land = pd.to_numeric(df["land_ha"], errors="coerce") if "land_ha" in df else None
    soy = pd.to_numeric(df["soybean_ha"], errors="coerce") if "soybean_ha" in df else None
    if land is not None:
        out["total_land_ha"] = round(float(land.sum(skipna=True)), 2)
        out["land_coverage"] = round(float(land.notna().mean()), 3)
        out["median_land_ha"] = round(float(land.median()), 2) if land.notna().any() else None
        out["members_over_10ha"] = int((land > 10).sum())
    if soy is not None:
        out["soybean_area_ha"] = round(float(soy.sum(skipna=True)), 2)
    if "gender" in df:
        g = df["gender"].astype(str).str.strip().str.upper().str[0]
        out["women_share"] = round(float((g == "F").mean()), 3) if n else None
    if "shares" in df:
        sh = pd.to_numeric(df["shares"], errors="coerce")
        out["total_shares"] = float(sh.sum(skipna=True))
        top = sh.sort_values(ascending=False)
        out["top5_share_concentration"] = (round(float(top.head(5).sum() / sh.sum()), 3)
                                           if sh.sum() else None)

    area_for_capacity = out.get("soybean_area_ha") or out.get("total_land_ha")
    out["capacity_basis"] = ("member soybean area" if out.get("soybean_area_ha")
                             else "member total land (assumes all soybean)"
                             if out.get("total_land_ha") else None)
    if area_for_capacity:
        out["capacity_quintals"] = round(area_for_capacity * soybean_yield_t_per_ha * 10, 1)
    if pledged_quintals and out.get("capacity_quintals"):
        out["pledge_to_capacity"] = round(pledged_quintals / out["capacity_quintals"], 3)

    need = SCHEME_MIN_MEMBERS_HILLY if hilly_area else SCHEME_MIN_MEMBERS_PLAINS
    out["scheme_member_norm"] = need
    if director_names and "name" in df:
        names = set(df["name"].map(norm))
        out["directors_who_are_members"] = sum(1 for d in director_names if norm(d) in names)
        out["directors_checked"] = len(director_names)
    return out


# ──────────────────────────────────────────────────────────────── scoring ──
def _band(v, cuts):
    """cuts = [(threshold, score), ...] ascending on threshold; first v <= t wins."""
    for t, s in cuts:
        if v <= t:
            return s
    return cuts[-1][1]


def run_checks(fpo: dict) -> dict:
    """
    Every check on an FPO, grouped by pillar, plus a 0-100 risk score per pillar and
    a composite. `fpo` is a plain dict (see the UI or SAMPLE_FPOS for its shape).

    Each check: {pillar, check, status: pass|warn|fail|info|vendor, detail}.
    'vendor' = could not be verified without a paid source (neutral for scoring).
    """
    checks, flags = [], []
    add = lambda pillar, check, status, detail: checks.append(
        {"pillar": pillar, "check": check, "status": status, "detail": detail})
    today = date.today()
    name = (fpo.get("name") or "").strip()
    mca = fpo.get("mca") or {}

    # ── 1. IDENTITY ───────────────────────────────────────────────────────
    P = "identity"
    cin = validate_cin(fpo.get("cin", ""))
    if not cin["valid_format"]:
        add(P, "cin_format", "fail", cin["reason"]); flags.append("CIN_INVALID")
    else:
        add(P, "cin_format", "pass",
            f"{cin['class_label']} · RoC {cin['roc']} ({cin['roc_state'] or 'unknown RoC'}) · "
            f"incorporated {cin['year']} · NIC {cin['nic']} ({cin['nic_label']})")
        add(P, "producer_company_class", "pass" if cin["producer_company_class"] else "warn",
            "class PTC, as producer companies are" if cin["producer_company_class"]
            else f"class {cin['class_code']} — producer companies are normally PTC")
        add(P, "agri_industry_code", "pass" if cin["agri_industry"] else "warn",
            f"NIC division {cin['nic_division']}: {cin['nic_label']}")
        if not cin["year_plausible"]:
            add(P, "cin_year", "fail", f"year {cin['year']} is impossible"); flags.append("CIN_INVALID")
    add(P, "name_is_producer_company",
        "pass" if re.search(r"PRODUCER\s+COMPANY", name, re.I) else "warn",
        "name contains 'Producer Company'" if re.search(r"PRODUCER\s+COMPANY", name, re.I)
        else "a producer company's name must end with 'Producer Company Limited'")

    pan = validate_pan(fpo.get("pan", ""), name)
    if fpo.get("pan"):
        if not pan["valid_format"]:
            add(P, "pan_format", "fail", pan["reason"]); flags.append("PAN_INVALID")
        else:
            add(P, "pan_is_company", "pass" if pan["is_company"] else "fail",
                f"4th letter '{pan['input'][3]}' = {pan['entity_type']}")
            if not pan["is_company"]:
                flags.append("PAN_NOT_COMPANY")
            if pan["name_initial_ok"] is not None:
                add(P, "pan_name_initial", "pass" if pan["name_initial_ok"] else "fail",
                    f"5th letter '{pan['input'][4]}' vs name initial '{pan['expected_initial']}'")
                if not pan["name_initial_ok"]:
                    flags.append("PAN_NAME_MISMATCH")
    else:
        add(P, "pan_format", "warn", "company PAN not provided")

    g = validate_gstin(fpo.get("gstin", ""))
    if fpo.get("gstin"):
        if not g["valid_format"]:
            add(P, "gstin_format", "fail", g["reason"]); flags.append("GSTIN_INVALID")
        else:
            add(P, "gstin_checksum", "pass" if g["checksum_ok"] else "fail",
                "check digit correct" if g["checksum_ok"] else g["reason"])
            if not g["checksum_ok"]:
                flags.append("GSTIN_INVALID")
            if pan.get("valid_format"):
                ok = g["pan"] == pan["input"]
                add(P, "gstin_pan_match", "pass" if ok else "fail",
                    f"PAN inside GSTIN {g['pan']} {'=' if ok else '≠'} company PAN {pan['input']}")
                if not ok:
                    flags.append("GSTIN_PAN_MISMATCH")
            if cin.get("gst_state_code"):
                ok = g["state_code"] == cin["gst_state_code"]
                add(P, "gstin_state_vs_cin", "pass" if ok else "warn",
                    f"GST state {g['state']} vs registered office RoC state {cin['roc_state']}"
                    + ("" if ok else " — can be a branch registration; confirm"))
        add(P, "gstin_live_status", "vendor",
            "active/cancelled status and return filing need GSTN (GSP licence) — "
            "check manually on services.gst.gov.in")
    else:
        add(P, "gstin_format", "warn", "no GSTIN — fine below the GST threshold, but "
                                       "limits turnover verification")

    if mca:
        st_ = (mca.get("status") or "").lower()
        ok = st_.startswith("active")
        add(P, "mca_status", "pass" if ok else "fail",
            f"MCA status: {mca.get('status') or 'not shown'}")
        if not ok:
            flags.append("COMPANY_NOT_ACTIVE")
        if mca.get("cin") and cin.get("valid_format"):
            same = mca["cin"].upper() == cin["input"]
            add(P, "mca_cin_matches", "pass" if same else "fail",
                "pasted MCA record is for this CIN" if same
                else f"MCA page is for {mca['cin']}, not {cin['input']}")
            if not same:
                flags.append("MCA_RECORD_MISMATCH")
        if mca.get("company_name") and name:
            nm = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper().replace("LIMITED", "LTD"))
            same = nm(mca["company_name"]) == nm(name)
            add(P, "mca_name_matches", "pass" if same else "warn",
                f"MCA name '{mca['company_name']}'")
    else:
        add(P, "mca_master_data", "warn",
            "MCA master data not pasted — status (Active / Struck off) unverified")
    add(P, "charges_register", "vendor",
        "existing loans secured on the FPO's assets (MCA charge register) need a paid "
        "MCA-data provider, or a manual check on mca.gov.in")

    # ── 2. GOVERNANCE ─────────────────────────────────────────────────────
    P = "governance"
    dirs = fpo.get("directors") or mca.get("directors") or []
    board = [d for d in dirs if not re.search(r"secretary|ceo|chief executive",
                                             d.get("designation", ""), re.I)]
    nd = len(board)
    if nd:
        ok = MIN_DIRECTORS <= nd <= MAX_DIRECTORS
        add(P, "director_count", "pass" if ok else "fail",
            f"{nd} directors (producer company needs {MIN_DIRECTORS}-{MAX_DIRECTORS})")
        if not ok:
            flags.append("BOARD_SIZE")
        bad = [d["din"] for d in board if d.get("din") and not DIN_RE.match(str(d["din"]))]
        add(P, "din_format", "fail" if bad else "pass",
            f"malformed DIN: {', '.join(bad)}" if bad else "all DINs are 8 digits")
        add(P, "din_status", "vendor", "DIN active / disqualified status needs MCA data")
    else:
        add(P, "director_count", "warn", "directors not provided")
    ceo = fpo.get("has_ceo")
    if ceo is None:
        ceo = any(re.search(r"ceo|chief executive", d.get("designation", ""), re.I) for d in dirs)
    add(P, "ceo_appointed", "pass" if ceo else "warn",
        "full-time CEO in place" if ceo else "no CEO — producer companies must appoint one")

    # ── 3. MEMBERSHIP & CAPACITY ─────────────────────────────────────────
    P = "membership"
    mem = fpo.get("members") or {}
    n = mem.get("members") or fpo.get("declared_members")
    if n:
        norm_ = mem.get("scheme_member_norm", SCHEME_MIN_MEMBERS_PLAINS)
        st_ = ("fail" if n < MIN_MEMBERS_LEGAL else "pass" if n >= norm_ else "warn")
        add(P, "member_count", st_,
            f"{n} members (legal minimum {MIN_MEMBERS_LEGAL}; 10,000-FPO scheme norm {norm_})"
            + ("" if mem else " — declared, member list not uploaded"))
        if n < MIN_MEMBERS_LEGAL:
            flags.append("TOO_FEW_MEMBERS")
    else:
        add(P, "member_count", "warn", "member count unknown")
    if mem:
        dr = mem.get("duplicate_rate", 0)
        add(P, "duplicate_members", "pass" if dr <= .01 else "warn" if dr <= .05 else "fail",
            f"{mem.get('duplicate_rows', round(dr * n))} extra rows ({dr:.1%}) repeat a "
            "mobile, Aadhaar digits or a whole entry — the same farmer counted twice")
        if dr > .05:
            flags.append("DUPLICATE_MEMBERS")
        if mem.get("namesake_rows"):
            add(P, "namesakes", "info",
                f"{mem['namesake_rows']} rows share a name within a village — normal in "
                "villages, but spot-check a few against Aadhaar")
        if mem.get("land_coverage") is not None:
            add(P, "land_on_record", "pass" if mem["land_coverage"] >= .8 else "warn",
                f"{mem['land_coverage']:.0%} of members have land area; total "
                f"{mem.get('total_land_ha', 0):.1f} ha, median {mem.get('median_land_ha')} ha")
        if mem.get("members_over_10ha"):
            add(P, "large_holders", "warn",
                f"{mem['members_over_10ha']} members report >10 ha — unusual for a "
                "smallholder FPO; verify with 7/12s")
        if mem.get("top5_share_concentration") is not None:
            c = mem["top5_share_concentration"]
            add(P, "share_concentration", "pass" if c <= .25 else "warn",
                f"top 5 members hold {c:.0%} of shares")
        if mem.get("directors_checked"):
            k, t = mem["directors_who_are_members"], mem["directors_checked"]
            add(P, "directors_are_members", "pass" if k == t else "warn",
                f"{k}/{t} directors found in the member list (directors must be members)")
    ratio = mem.get("pledge_to_capacity")
    if ratio is not None:
        st_ = "pass" if ratio <= .6 else "warn" if ratio <= 1.0 else "fail"
        add(P, "production_capacity", st_,
            f"pledging {fpo.get('pledged_quintals'):,.0f} q vs members' capacity "
            f"{mem['capacity_quintals']:,.0f} q ({mem['capacity_basis']}) = {ratio:.0%}")
        if ratio > 1.0:
            flags.append("PLEDGE_EXCEEDS_MEMBER_CAPACITY")
    elif fpo.get("pledged_quintals"):
        add(P, "production_capacity", "warn",
            "member land/soybean area not available — cannot test whether members "
            "could have grown the pledged stock")

    # ── 4. FINANCIALS ─────────────────────────────────────────────────────
    P = "financial"
    fin = fpo.get("financials") or {}
    nw = fin.get("net_worth"); debt = fin.get("total_debt"); ca = fin.get("current_assets")
    cl = fin.get("current_liabilities"); rev = fin.get("revenue"); pat = fin.get("pat")
    prev = fin.get("revenue_prev")
    fin_scores = []
    if nw is not None:
        add(P, "net_worth", "pass" if nw > 0 else "fail", f"net worth ₹{nw:,.0f}")
        if nw <= 0:
            flags.append("NEGATIVE_NET_WORTH")
        fin_scores.append(_band(nw, [(0, 90), (500000, 60), (2500000, 35), (1e18, 15)]))
        if debt is not None and nw > 0:
            de = debt / nw
            add(P, "debt_to_equity", "pass" if de <= 2 else "warn" if de <= 4 else "fail",
                f"debt/equity {de:.2f}")
            fin_scores.append(_band(de, [(1, 15), (2, 30), (4, 60), (1e9, 90)]))
    if ca is not None and cl:
        cr = ca / cl
        add(P, "current_ratio", "pass" if cr >= 1.2 else "warn" if cr >= 1 else "fail",
            f"current ratio {cr:.2f}")
        fin_scores.append(_band(-cr, [(-1.5, 15), (-1.2, 30), (-1.0, 55), (1e9, 85)]))
    if rev:
        m = (pat or 0) / rev
        add(P, "profitability", "pass" if m > 0 else "warn",
            f"revenue ₹{rev:,.0f}, PAT ₹{(pat or 0):,.0f} ({m:.1%})")
        fin_scores.append(_band(-m, [(-0.03, 20), (0, 40), (0.05, 65), (1e9, 85)]))
        if prev:
            gr = rev / prev - 1
            add(P, "revenue_growth", "pass" if gr >= 0 else "warn", f"revenue growth {gr:+.0%}")
            fin_scores.append(_band(-gr, [(-0.2, 20), (0, 35), (0.25, 60), (1e9, 80)]))
    if not fin:
        add(P, "financial_statements", "warn", "no audited financials entered")
    add(P, "credit_bureau", "vendor", "commercial bureau report (CIBIL Rank etc.) needs "
                                      "bureau membership — upload a pulled report manually")

    # ── 5. BANKING ────────────────────────────────────────────────────────
    P = "banking"
    bank = fpo.get("bank") or {}
    bank_scores = []
    if bank:
        b = bank.get("bounce_count", 0)
        add(P, "bounces", "pass" if b == 0 else "warn" if b <= 2 else "fail",
            f"{b} cheque/ECS returns in {bank.get('months_covered')} months")
        bank_scores.append(_band(b, [(0, 10), (2, 40), (5, 70), (1e9, 90)]))
        if rev and bank.get("total_credit") and bank.get("months_covered"):
            annual_cr = bank["total_credit"] / bank["months_covered"] * 12
            ratio_ = annual_cr / rev
            add(P, "turnover_vs_bank_credits",
                "pass" if .6 <= ratio_ <= 1.8 else "warn",
                f"bank credits annualise to ₹{annual_cr:,.0f} vs declared revenue "
                f"₹{rev:,.0f} ({ratio_:.0%})")
            bank_scores.append(20 if .6 <= ratio_ <= 1.8 else 60)
        if bank.get("large_cash_deposits"):
            add(P, "cash_deposits", "warn",
                f"{bank['large_cash_deposits']} cash deposits above ₹50,000")
            bank_scores.append(55)
    else:
        add(P, "bank_statement", "warn", "no bank statement analysed")
    if fpo.get("ifsc_info") is not None:
        add(P, "ifsc", "pass" if fpo["ifsc_info"] else "fail",
            f"{fpo['ifsc_info'].get('BANK')}, {fpo['ifsc_info'].get('BRANCH')}"
            if fpo["ifsc_info"] else "IFSC not found")
    add(P, "penny_drop", "vendor", "account-holder name match needs a penny-drop provider")

    # ── 6. COMPLIANCE ─────────────────────────────────────────────────────
    P = "compliance"
    inc = parse_date(mca.get("incorporation_date")) if mca else None
    if not inc and cin.get("valid_format"):
        inc = date(cin["year"], 7, 1)          # CIN gives only the year
    comp_scores = []
    if inc:
        age = (today - inc).days / 365.25
        add(P, "age", "pass" if age >= 3 else "warn",
            f"{age:.1f} years since incorporation"
            + (" (year from CIN)" if not mca.get("incorporation_date") else ""))
        comp_scores.append(_band(age, [(1, 80), (3, 50), (5, 30), (1e9, 15)]))
    for key, label in (("last_agm", "last AGM"), ("last_balance_sheet", "last balance sheet")):
        d = parse_date(mca.get(key)) if mca else None
        if d:
            months = (today - d).days / 30.4
            add(P, key, "pass" if months <= 18 else "fail",
                f"{label} {d.isoformat()} ({months:.0f} months ago)")
            if months > 18:
                flags.append("FILINGS_OVERDUE")
            comp_scores.append(20 if months <= 18 else 85)
        else:
            add(P, key, "warn", f"{label} date not available")

    # ── scores ────────────────────────────────────────────────────────────
    def pillar_score(p, extra=None):
        pts = {"pass": 10, "info": 30, "vendor": 45, "warn": 55, "fail": 90}
        sts = [pts[c["status"]] for c in checks if c["pillar"] == p]
        base = sum(sts) / len(sts) if sts else 50.0
        if extra:
            base = 0.5 * base + 0.5 * (sum(extra) / len(extra))
        return round(base, 1)

    pillars = {
        "identity": pillar_score("identity"),
        "governance": pillar_score("governance"),
        "membership": pillar_score("membership"),
        "financial": pillar_score("financial", fin_scores),
        "banking": pillar_score("banking", bank_scores),
        "compliance": pillar_score("compliance", comp_scores),
    }
    weights = {"identity": .20, "governance": .15, "membership": .20,
               "financial": .25, "banking": .10, "compliance": .10}
    composite = round(sum(pillars[k] * w for k, w in weights.items()), 1)
    hard = {"CIN_INVALID", "COMPANY_NOT_ACTIVE", "GSTIN_PAN_MISMATCH", "GSTIN_INVALID",
            "PAN_NOT_COMPANY", "MCA_RECORD_MISMATCH", "PLEDGE_EXCEEDS_MEMBER_CAPACITY",
            "NEGATIVE_NET_WORTH", "TOO_FEW_MEMBERS"}
    hard_hit = sorted(set(flags) & hard)
    if hard_hit:
        composite = max(composite, 76.0)        # a hard failure can't score below 'High'
    grade, rec, rate = (("Low", "Auto-Approve", "10-12%") if composite <= 25 else
                        ("Medium", "Standard Review", "13-15%") if composite <= 50 else
                        ("Elevated", "Enhanced Diligence", "15-17%") if composite <= 75 else
                        ("High", "Reject / Additional Collateral", "18%+"))
    return {"checks": checks, "flags": sorted(set(flags)), "hard_flags": hard_hit,
            "pillars": pillars, "weights": weights, "score": composite,
            "grade": grade, "recommendation": rec, "suggested_rate": rate,
            "decoded": {"cin": cin, "pan": pan, "gstin": g}}


def save_audit(fpo: dict, result: dict) -> str:
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_id = "FPO-" + hashlib.sha256(
        (json.dumps(fpo, sort_keys=True, default=str) + datetime.now().isoformat()).encode()
    ).hexdigest()[:12].upper()
    path = os.path.join(RUNS_DIR, run_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"run_id": run_id, "generated_at": datetime.now().isoformat(timespec="seconds"),
                   "fpo": fpo, "result": result}, fh, indent=2, ensure_ascii=False, default=str)
    return run_id


# ───────────────────────────────────────────────────── sample (DUMMY) FPOs ──
def _dummy_gstin(state: str, pan: str) -> str:
    body = f"{state}{pan}1Z"
    return body + gstin_check_char(body)


SAMPLE_FPOS = {
    "Healthy FPO (DUMMY)": {
        "name": "Latur Soya Farmers Producer Company Limited",
        "cin": "U01403MH2018PTC300001", "pan": "AAACL1234K",
        "gstin": _dummy_gstin("27", "AAACL1234K"),
        "declared_members": 742, "pledged_quintals": 3200, "has_ceo": True,
        "directors": [{"din": f"0870{i:04d}", "name": n, "designation": "Director"}
                      for i, n in enumerate(["Ramesh Patil", "Sunita Jadhav", "Anil More",
                                             "Kavita Shinde", "Vijay Pawar", "Meena Kale",
                                             "Sachin Deshmukh"])],
        "members": {"members": 742, "duplicate_rate": 0.004, "land_coverage": 0.96,
                    "total_land_ha": 1310.0, "median_land_ha": 1.6, "soybean_area_ha": 905.0,
                    "capacity_quintals": 9050.0, "capacity_basis": "member soybean area",
                    "pledge_to_capacity": 0.354, "scheme_member_norm": 300,
                    "top5_share_concentration": 0.04, "directors_who_are_members": 7,
                    "directors_checked": 7},
        "financials": {"revenue": 38000000, "revenue_prev": 31000000, "pat": 1400000,
                       "net_worth": 6200000, "total_debt": 5100000,
                       "current_assets": 9800000, "current_liabilities": 6100000},
        "bank": {"months_covered": 12, "total_credit": 41500000, "bounce_count": 0,
                 "large_cash_deposits": 0},
        "mca": {"company_name": "LATUR SOYA FARMERS PRODUCER COMPANY LIMITED",
                "cin": "U01403MH2018PTC300001", "status": "Active",
                "incorporation_date": "14/08/2018", "last_agm": "28/09/2025",
                "last_balance_sheet": "31/03/2025"},
    },
    "Young FPO, thin financials (DUMMY)": {
        "name": "Chakur Krishi Producer Company Limited",
        "cin": "U01110MH2024PTC400002", "pan": "AABCC4321M",
        "gstin": _dummy_gstin("27", "AABCC4321M"),
        "declared_members": 186, "pledged_quintals": 900, "has_ceo": False,
        "directors": [{"din": f"1020{i:04d}", "name": n, "designation": "Director"}
                      for i, n in enumerate(["Prakash Bidve", "Sharad Kamble", "Asha Gore",
                                             "Dattatray Mane", "Ganesh Shelke"])],
        "members": {"members": 186, "duplicate_rate": 0.027, "land_coverage": 0.71,
                    "total_land_ha": 260.0, "median_land_ha": 1.2,
                    "capacity_quintals": 2600.0,
                    "capacity_basis": "member total land (assumes all soybean)",
                    "pledge_to_capacity": 0.346, "scheme_member_norm": 300},
        "financials": {"revenue": 2400000, "pat": -60000, "net_worth": 410000,
                       "total_debt": 300000, "current_assets": 700000,
                       "current_liabilities": 650000},
        "bank": {"months_covered": 8, "total_credit": 1900000, "bounce_count": 1,
                 "large_cash_deposits": 2},
        "mca": {"cin": "U01110MH2024PTC400002", "status": "Active",
                "incorporation_date": "03/02/2024"},
    },
    "Red flags (DUMMY)": {
        "name": "Deccan Agro Traders Private Limited",
        "cin": "U46209MH2016PTC500003", "pan": "AAFCD7788Q",
        "gstin": _dummy_gstin("27", "AAFCX7788Q"),         # PAN inside GSTIN differs
        "declared_members": 420, "pledged_quintals": 6000, "has_ceo": True,
        "directors": [{"din": "22334455", "name": "Rohit Shah", "designation": "Director"},
                      {"din": "2233445", "name": "Neha Shah", "designation": "Director"}],
        "members": {"members": 420, "duplicate_rate": 0.083, "land_coverage": 0.55,
                    "total_land_ha": 310.0, "median_land_ha": 0.4, "members_over_10ha": 6,
                    "capacity_quintals": 3100.0,
                    "capacity_basis": "member total land (assumes all soybean)",
                    "pledge_to_capacity": 1.935, "scheme_member_norm": 300,
                    "top5_share_concentration": 0.61},
        "financials": {"revenue": 52000000, "pat": 300000, "net_worth": 900000,
                       "total_debt": 7800000, "current_assets": 5000000,
                       "current_liabilities": 6900000},
        "bank": {"months_covered": 12, "total_credit": 14000000, "bounce_count": 6,
                 "large_cash_deposits": 9},
        "mca": {"cin": "U46209MH2016PTC500003", "status": "Active",
                "incorporation_date": "11/05/2016", "last_agm": "30/09/2022",
                "last_balance_sheet": "31/03/2022"},
    },
}


# ───────────────────────────────────────────────────────────── streamlit UI ──
_STATUS_UI = {"pass": ("PASS", "#10b981"), "warn": ("WARN", "#f59e0b"),
              "fail": ("FAIL", "#ef4444"), "info": ("INFO", "#94a3b8"),
              "vendor": ("NEEDS VENDOR", "#a78bfa")}
_PILLAR_LABEL = {"identity": "Identity & registration", "governance": "Governance",
                 "membership": "Membership & capacity", "financial": "Financial strength",
                 "banking": "Banking conduct", "compliance": "Compliance"}
_GRADE_COLOUR = {"Low": "#10b981", "Medium": "#38bdf8", "Elevated": "#f59e0b", "High": "#ef4444"}


def inject_css() -> None:
    import streamlit as st
    st.markdown("""<style>
      .fpo-card{background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                padding:.7rem 1rem;margin:.35rem 0;}
    </style>""", unsafe_allow_html=True)


def _show_result(fpo: dict, res: dict, *, sample: bool) -> None:
    import streamlit as st
    col = _GRADE_COLOUR[res["grade"]]
    st.markdown(
        f"<div class='fpo-card' style='border-left:5px solid {col};'>"
        f"<span style='font-size:1.3rem;font-weight:700;color:{col};'>"
        f"{res['grade'].upper()} RISK · {res['score']:.0f}/100</span>"
        f"<br><span style='color:#cbd5e1;'>{res['recommendation']} · suggested rate "
        f"{res['suggested_rate']}</span>"
        + (f"<br><span style='color:#ef4444;font-size:.85rem;'>hard flags: "
           f"{', '.join(res['hard_flags'])}</span>" if res["hard_flags"] else "")
        + ("<br><span style='color:#f59e0b;font-size:.8rem;'>SAMPLE — invented data, "
           "not a real FPO</span>" if sample else "")
        + "</div>", unsafe_allow_html=True)
    cols = st.columns(6)
    for c, (k, v) in zip(cols, res["pillars"].items()):
        c.metric(_PILLAR_LABEL[k], f"{v:.0f}", help=f"weight {res['weights'][k]:.0%} · "
                                                  "0 = no risk, 100 = maximum risk")
    for p in _PILLAR_LABEL:
        items = [c for c in res["checks"] if c["pillar"] == p]
        worst = max((c["status"] for c in items),
                    key=["pass", "info", "vendor", "warn", "fail"].index, default="pass")
        with st.expander(f"{_PILLAR_LABEL[p]} — {res['pillars'][p]:.0f}/100",
                         expanded=worst == "fail"):
            for c in items:
                lab, colr = _STATUS_UI[c["status"]]
                st.markdown(
                    f"<div class='fpo-card'><span style='color:{colr};font-weight:600;'>"
                    f"{lab}</span> &nbsp;<b>{c['check'].replace('_', ' ')}</b><br>"
                    f"<span style='color:#94a3b8;font-size:.85rem;'>{c['detail']}</span></div>",
                    unsafe_allow_html=True)


def render_sample() -> None:
    import streamlit as st
    st.caption("Three invented FPOs that exercise the checks. Their CINs, PANs and GSTINs "
               "are DUMMY numbers built to pass or fail the format rules on purpose.")
    pick = st.radio("Sample FPO", list(SAMPLE_FPOS), horizontal=True, key="fpo_sample")
    fpo = SAMPLE_FPOS[pick]
    a, b, c = st.columns(3)
    a.markdown(f"**{fpo['name']}**")
    b.markdown(f"CIN `{fpo['cin']}`")
    c.markdown(f"GSTIN `{fpo['gstin']}`")
    _show_result(fpo, run_checks(fpo), sample=True)


def render_real() -> None:
    """Enter / upload an FPO's details; every free check runs for real."""
    import streamlit as st
    ss = st.session_state

    st.markdown("##### 1 · Identity")
    c1, c2 = st.columns([2, 1])
    name = c1.text_input("Registered name", key="fpo_name",
                         placeholder="Latur Soya Farmers Producer Company Limited")
    cin = c2.text_input("CIN", key="fpo_cin", placeholder="U01403MH2018PTC300001")
    c1, c2, c3 = st.columns(3)
    pan = c1.text_input("Company PAN", key="fpo_pan", placeholder="AAACL1234K")
    gstin = c2.text_input("GSTIN", key="fpo_gstin", placeholder="27AAACL1234K1Z?")
    ifsc = c3.text_input("Bank IFSC", key="fpo_ifsc")
    with st.expander("Paste MCA 'View Company Master Data' (free, has a captcha)"):
        st.caption("Open mca.gov.in → MCA Services → Master Data → View Company/LLP Master "
                   "Data, enter the CIN and captcha, select the whole page (Ctrl+A), copy, "
                   "and paste it here. We read status, capital, AGM / balance-sheet dates "
                   "and the director table from it.")
        mca_text = st.text_area("MCA page text", key="fpo_mca", height=160)

    st.markdown("##### 2 · Governance")
    c1, c2 = st.columns([3, 1])
    dir_text = c1.text_area("Directors — one per line: DIN, Name, Designation",
                            key="fpo_dirs", height=110,
                            placeholder="08700001, Ramesh Patil, Director")
    has_ceo = c2.checkbox("CEO appointed", key="fpo_ceo")

    st.markdown("##### 3 · Members & the pledge")
    c1, c2, c3, c4 = st.columns(4)
    pledged = c1.number_input("Soybean being pledged (quintals)", min_value=0.0,
                              step=100.0, key="fpo_pledge")
    yld = c2.number_input("Soybean yield (t/ha)", min_value=0.2, max_value=4.0, value=1.0,
                          step=0.1, key="fpo_yield",
                          help="Maharashtra soybean usually yields around 1 t/ha; use the "
                               "district figure for the season")
    declared = c3.number_input("Declared members", min_value=0, step=10, key="fpo_decl")
    hilly = c4.checkbox("NE / hilly area", key="fpo_hilly",
                        help="10,000-FPO scheme norm is 100 members there, 300 in plains")
    mfile = st.file_uploader("Member list (CSV / XLSX): name, village, mobile, "
                             "land_ha, soybean_ha, gender, shares", type=["csv", "xlsx", "xls"],
                             key="fpo_members")

    st.markdown("##### 4 · Financials (latest audited year, ₹)")
    f = st.columns(4)
    rev = f[0].number_input("Revenue", min_value=0.0, step=100000.0, key="fpo_rev")
    rev_prev = f[1].number_input("Revenue, previous year", min_value=0.0, step=100000.0,
                                 key="fpo_revp")
    pat = f[2].number_input("Profit after tax", step=10000.0, key="fpo_pat")
    nw = f[3].number_input("Net worth", step=100000.0, key="fpo_nw")
    f = st.columns(3)
    debt = f[0].number_input("Total borrowings", min_value=0.0, step=100000.0, key="fpo_debt")
    ca = f[1].number_input("Current assets", min_value=0.0, step=100000.0, key="fpo_ca")
    cl = f[2].number_input("Current liabilities", min_value=0.0, step=100000.0, key="fpo_cl")

    st.markdown("##### 5 · Bank statement (optional)")
    sfile = st.file_uploader("FPO current-account statement (CSV / XLSX / PDF)",
                             type=["csv", "xlsx", "xls", "pdf"], key="fpo_stmt")
    spw = st.text_input("PDF password (if any)", type="password", key="fpo_stmt_pw")

    if not st.button("🔍 Verify FPO", type="primary", key="fpo_go"):
        return

    mca = parse_mca_master_text(mca_text) if mca_text.strip() else {}
    directors = []
    for line in dir_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            directors.append({"din": parts[0], "name": parts[1],
                              "designation": parts[2] if len(parts) > 2 else "Director"})
    fpo = {"name": name or mca.get("company_name") or "", "cin": cin or mca.get("cin") or "",
           "pan": pan, "gstin": gstin, "directors": directors or None,
           "has_ceo": has_ceo or None, "declared_members": int(declared) or None,
           "pledged_quintals": pledged or None, "mca": mca}

    if mfile is not None:
        try:
            df, mapping = read_member_file(mfile)
            fpo["members"] = analyze_members(
                df, pledged_quintals=pledged or None, soybean_yield_t_per_ha=yld,
                hilly_area=hilly,
                director_names=[d["name"] for d in (directors or mca.get("directors") or [])])
            st.caption(f"Member columns recognised: {mapping}")
        except Exception as e:
            st.error(f"Could not read the member list: {e}")
    fin = {k: v for k, v in {"revenue": rev, "revenue_prev": rev_prev, "pat": pat,
                             "net_worth": nw, "total_debt": debt, "current_assets": ca,
                             "current_liabilities": cl}.items() if v}
    if fin:
        fpo["financials"] = fin
    if sfile is not None:
        try:
            import borrower_verification_demo as bv
            sdf, msg = bv.parse_statement(sfile, spw)
            if sdf is not None and len(sdf):
                fpo["bank"] = {k: v for k, v in bv.analyze_statement(sdf).items()
                               if k != "recurring_items"}
            else:
                st.warning(f"Statement not parsed: {msg}")
        except Exception as e:
            st.warning(f"Statement analysis unavailable: {e}")
    if ifsc.strip():
        try:
            import borrower_verification_demo as bv
            fpo["ifsc_info"] = bv.fetch_ifsc(ifsc) or {}
        except Exception:
            pass

    res = run_checks(fpo)
    run_id = save_audit(fpo, res)
    _show_result(fpo, res, sample=False)
    if mca:
        with st.expander("What we read from the MCA page"):
            st.json({k: v for k, v in mca.items() if v})
    ss["verified_fpo"] = {"run_id": run_id, "name": fpo["name"], "cin": fpo["cin"],
                          "score": res["score"], "grade": res["grade"],
                          "recommendation": res["recommendation"],
                          "pillars": res["pillars"], "hard_flags": res["hard_flags"],
                          "pledge_to_capacity": (fpo.get("members") or {}).get("pledge_to_capacity"),
                          "checked_at": datetime.now().isoformat(timespec="seconds")}
    st.caption(f"Audit written to `verification_runs/{run_id}.json` — stored in session as "
               "`verified_fpo`.")


def render() -> None:
    import streamlit as st
    t1, t2 = st.tabs(["🔍 Verify an FPO", "🧪 Sample walkthrough"])
    with t1:
        render_real()
    with t2:
        render_sample()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--samples":
        for k, v in SAMPLE_FPOS.items():
            r = run_checks(v)
            print(f"{k:40s} {r['score']:5.1f} {r['grade']:9s} {r['recommendation']:32s} "
                  f"{','.join(r['hard_flags']) or '-'}")
    else:
        import streamlit  # noqa: F401 -- `streamlit run fpo_verification.py`
        inject_css()
        render()
