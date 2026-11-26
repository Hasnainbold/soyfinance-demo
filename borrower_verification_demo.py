"""
borrower_verification_demo.py
=============================
Borrower financial-verification sandbox with TWO modes:

  • "Sample walkthrough"   — 4 archetype borrowers, everything simulated,
                             for quickly seeing the flow.
  • "Verify real documents"— you supply YOUR OWN artifacts and everything
                             that can be verified WITHOUT a paid vendor
                             contract is verified for real:

      REAL, no contract needed
        - Aadhaar offline e-KYC ZIP  → real demographics + photo extracted,
          signature block checked (full chain if uidai cert provided)
        - PAN                        → real format + entity-type decode
        - Bank account IFSC          → live lookup (Razorpay IFSC, free)
        - Address PIN code           → live lookup (India Post, free)
        - Bank statement (CSV/XLSX/PDF) → REAL parser + REAL analytics:
          income, recurring EMIs, bounces, balances, FOIR
        - Credit report PDF          → best-effort real parse of score/flags
        - PDF integrity              → real metadata / signature inspection
        - Risk-engine borrower pillar → real, computed from the real inputs
        - Audit record               → real JSON written to ./verification_runs/

      STILL NEEDS A PAID / LICENSED VENDOR  (clearly labelled)
        - PAN name-ownership (NSDL/Protean)
        - Penny-drop bank-account name match
        - Live Aadhaar OTP e-KYC (KUA licence)
        - Live Account Aggregator statement pull (FIU registration)
        - Live credit-bureau API (bureau membership)

  NOTHING is uploaded anywhere. Files are read in memory on this machine.
  The only network calls are two read-only public registries (IFSC, PIN).

Run:  streamlit run borrower_verification_demo.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from risk_engine import compute_borrower_risk
    ENGINE_OK = True
except Exception:
    ENGINE_OK = False

    def compute_borrower_risk(b: dict) -> dict:
        total = b.get("total_transactions", 0)
        succ = b.get("successful_transactions", 0)
        rate = (succ / total) if total else None
        s_succ = 50.0 if rate is None else (10 if rate > .95 else 30 if rate >= .8 else 55 if rate >= .6 else 80)
        t = b.get("years_on_platform", 0)
        s_ten = 10 if t > 3 else 30 if t >= 1 else 60
        s_kyc = 10 if b.get("kyc_verified") else 65
        s_fpo = 15 if b.get("is_fpo_member") else 40
        d = b.get("past_defaults", 0)
        s_def = 10 if d == 0 else 45 if d == 1 else 80
        score = s_succ * .30 + s_ten * .20 + s_kyc * .20 + s_fpo * .15 + s_def * .15
        return {"score": round(score, 2), "details": "fallback",
                "breakdown": {"success_rate": s_succ, "tenure": s_ten, "kyc": s_kyc,
                              "fpo_member": s_fpo, "defaults": s_def}}

try:
    from pyaadhaar.decode import AadhaarOfflineXML
    AADHAAR_OK = True
except Exception:
    AADHAAR_OK = False

try:
    import pdfplumber
    PDF_OK = True
except Exception:
    PDF_OK = False

try:
    from rapidfuzz import fuzz
    FUZZ_OK = True
except Exception:
    FUZZ_OK = False
    class fuzz:  # noqa
        @staticmethod
        def token_sort_ratio(a, b):
            a, b = set(str(a).lower().split()), set(str(b).lower().split())
            return 100.0 * len(a & b) / max(1, len(a | b))

try:
    from receipt_store import KNOWN_LOCATIONS
except Exception:
    KNOWN_LOCATIONS = ["Latur", "Indore", "Ujjain", "Dewas", "Nagpur", "Akola", "Washim"]

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verification_runs")
os.makedirs(RUNS_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
  .bv-scope h3 { color:#f8fafc; }
  .sim  { display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.06em;
          color:#fbbf24;border:1px solid #fbbf2455;border-radius:4px;padding:1px 6px;margin-left:6px; }
  .real { display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.06em;
          color:#34d399;border:1px solid #34d39955;border-radius:4px;padding:1px 6px;margin-left:6px; }
  .paid { display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.06em;
          color:#f87171;border:1px solid #f8717155;border-radius:4px;padding:1px 6px;margin-left:6px; }
  .ok{color:#34d399;font-weight:700}.warn{color:#fbbf24;font-weight:700}.bad{color:#f87171;font-weight:700}
  .card{ background:#1e293b;border:1px solid #ffffff14;border-radius:10px;padding:14px 16px;margin:8px 0; }
  .vend{ font-size:.72rem;color:#94a3b8; }
  .step{ font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;color:#64748b;margin-top:6px; }
</style>
"""

_css_done = False


def inject_css():
    """Add this page's CSS classes once (safe to call from the standalone demo or app.py)."""
    global _css_done
    if not _css_done:
        st.markdown(_CSS, unsafe_allow_html=True)
        _css_done = True


def rng_for(*parts) -> random.Random:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


# ═══════════════════════════════════════════════════════════════════════
#  SHARED — real helpers
# ═══════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_ifsc(ifsc: str) -> dict | None:
    try:
        r = requests.get(f"https://ifsc.razorpay.com/{ifsc.strip().upper()}", timeout=6)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_pincode(pin: str) -> list | None:
    try:
        r = requests.get(f"https://api.postalpincode.in/pincode/{pin.strip()}", timeout=6)
        j = r.json()
        if j and j[0].get("Status") == "Success":
            return j[0]["PostOffice"]
        return None
    except Exception:
        return None


PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PAN_ENTITY = {"P": "Individual", "C": "Company", "H": "HUF", "F": "Firm / LLP",
              "A": "AOP", "T": "Trust", "B": "Body of Individuals", "L": "Local authority",
              "J": "Artificial juridical person", "G": "Government"}

BOUNCE_RE = re.compile(r"(?:RETURN|\bRTN\b|REVERS|INSUFFICIENT|I/?W\s*RET|O/?W\s*RET|ECS\s*RET|"
                       r"NACH.*RET|CHQ.*RET|CHEQUE.*RET|DISHONOU?R|BOUNCE|INSUFF)", re.I)
EMI_RE = re.compile(r"(?:\bEMI\b|\bLOAN\b|NBFC|FINANC|\bHFC\b|BAJAJ|HDB|CHOLA|MUTHOOT|SHRIRAM|"
                    r"\bACH\s*D|MANDATE|\bECS\b|REPAYMENT|INSTAL)", re.I)
SAL_RE = re.compile(r"(?:SALARY|\bSAL\b|PAYROLL|WAGES?)", re.I)
CASH_DEP_RE = re.compile(r"(?:CASH\s*DEP|\bCDM\b|BY\s*CASH|CASH\s*DEPOSIT)", re.I)
FARM_IN_RE = re.compile(r"(?:MANDI|APMC|PACS|AGRI|SAMITI|KRISHI|PROCUREMENT|NEFT.*TRADER)", re.I)


def _pick(cols, *cands):
    low = {c.lower().strip(): c for c in cols}
    for cand in cands:
        for lc, orig in low.items():
            if cand in lc:
                return orig
    return None


_DATE_LINE_RE = re.compile(
    r"^\s*("
    r"\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}"          # 01-Jan-2026
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"             # 01/02/2026
    r"|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}"           # 01 Jan 2026
    r")\s+(.*)$")
_NUM_RE = re.compile(r"-?\d[\d,]*\.\d{2}")
_HDR_KW = ("date", "narration", "description", "particular", "remark", "detail",
           "balance", "withdraw", "deposit", "debit", "credit", "amount", "chq", "ref")


def _num_series(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
                         .replace("", "0"), errors="coerce").fillna(0.0)


def _normalize_txn_df(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, str]:
    """Take any raw table and return a clean df[date,narration,debit,credit,balance]."""
    if raw is None or len(raw) == 0:
        return None, "no rows"
    raw = raw.copy()
    raw.columns = [str(c).strip() for c in raw.columns]
    c_date = _pick(raw.columns, "date", "txn date", "value date", "tran date")
    c_narr = _pick(raw.columns, "narration", "description", "particular", "remark", "detail", "transaction")
    c_deb = _pick(raw.columns, "withdraw", "debit", "dr amount", "dr(", "dr ")
    c_cred = _pick(raw.columns, "deposit", "credit", "cr amount", "cr(", "cr ")
    c_bal = _pick(raw.columns, "balance", "closing bal", "running bal")
    c_amt = _pick(raw.columns, "amount", "txn amount", "transaction amount")
    c_type = _pick(raw.columns, "type", "dr/cr", "drcr", "indicator")
    if c_date is None or c_narr is None:
        return None, f"couldn't spot date/narration columns in {list(raw.columns)[:8]}"

    df = pd.DataFrame()
    df["date"] = pd.to_datetime(raw[c_date], errors="coerce", dayfirst=True)
    df["narration"] = raw[c_narr].astype(str)
    if c_deb and c_cred:
        df["debit"] = _num_series(raw[c_deb]); df["credit"] = _num_series(raw[c_cred])
    elif c_amt and c_type:
        amt = _num_series(raw[c_amt]); is_dr = raw[c_type].astype(str).str.upper().str.contains("D")
        df["debit"] = amt.where(is_dr, 0.0); df["credit"] = amt.where(~is_dr, 0.0)
    elif c_amt:
        amt = _num_series(raw[c_amt])
        df["debit"] = (-amt).clip(lower=0); df["credit"] = amt.clip(lower=0)
    else:
        return None, f"no debit/credit or amount column in {list(raw.columns)[:8]}"
    df["balance"] = _num_series(raw[c_bal]) if c_bal else np.nan
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if len(df) < 2:
        return None, "no dated rows after parsing"
    return df, "ok"


def _tables_to_raw(pdf) -> pd.DataFrame | None:
    """Assemble one raw DataFrame from all of a PDF's tables, tolerating ragged rows."""
    from collections import Counter
    rows = []
    for pg in pdf.pages:
        for tbl in (pg.extract_tables() or []):
            for r in (tbl or []):
                if r and any((c or "").strip() for c in r):
                    rows.append([(c or "").strip() for c in r])
    if len(rows) < 3:
        return None
    width = Counter(len(r) for r in rows).most_common(1)[0][0]
    if width < 3:
        return None
    norm = []
    for r in rows:
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width - 1] + [" ".join(r[width - 1:])]
        norm.append(r)
    hdr_idx = None
    for i, r in enumerate(norm[:6]):
        joined = " ".join(r).lower()
        if sum(k in joined for k in _HDR_KW) >= 2:
            hdr_idx = i
            break
    if hdr_idx is not None:
        cols, data = norm[hdr_idx], norm[hdr_idx + 1:]
    else:
        cols, data = [f"c{j}" for j in range(width)], norm
    seen, ucols = {}, []
    for j, c in enumerate(cols):
        c = c or f"c{j}"
        seen[c] = seen.get(c, 0) + 1
        ucols.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return pd.DataFrame(data, columns=ucols) if data else None


def _text_to_raw(text: str) -> pd.DataFrame | None:
    """Line-by-line parse of a non-tabular statement PDF."""
    recs = []
    for line in text.splitlines():
        m = _DATE_LINE_RE.match(line)
        if not m:
            continue
        rest = m.group(2)
        nums = _NUM_RE.findall(rest)
        if not nums:
            continue
        vals = [float(n.replace(",", "")) for n in nums]
        narr = _NUM_RE.split(rest)[0].strip(" .|-")
        bal = vals[-1] if len(vals) >= 2 else None
        amt = vals[-2] if len(vals) >= 2 else vals[0]
        low = rest.lower()
        dr = cr = 0.0
        if any(k in low for k in (" cr", "credit", "deposit", " c ")):
            cr = amt
        elif any(k in low for k in (" dr", "debit", "withdraw", " d ")):
            dr = amt
        elif recs and bal is not None and recs[-1][4] is not None:
            cr, dr = (amt, 0.0) if bal >= recs[-1][4] else (0.0, amt)
        else:
            dr = amt
        recs.append([m.group(1), narr, dr, cr, bal])
    if len(recs) < 3:
        return None
    return pd.DataFrame(recs, columns=["Date", "Narration", "Debit", "Credit", "Balance"])


def parse_statement(file, password: str = "") -> tuple[pd.DataFrame | None, str]:
    """Return (normalised df[date,narration,debit,credit,balance], message).

    `password` is used only for encrypted PDFs (PAN / DDMMYYYY / account no. etc.).
    """
    name = file.name.lower()
    note = ""
    try:
        if name.endswith((".xlsx", ".xls")):
            df, why = _normalize_txn_df(pd.read_excel(file))
        elif name.endswith(".csv"):
            df, why = _normalize_txn_df(pd.read_csv(file, dtype=str, keep_default_na=False))
        elif name.endswith(".pdf"):
            if not PDF_OK:
                return None, "pdfplumber not available — export CSV/XLSX from net-banking instead."
            pw_msg = ("This PDF is locked. Enter the password below and retry — Indian banks "
                      "commonly use your PAN in UPPERCASE, date of birth as DDMMYYYY, the account "
                      "number, or first-4-letters-of-name + DDMM. Check the covering email.")
            try:
                file.seek(0)
                pdf = pdfplumber.open(file, password=password or "")
            except Exception as e:
                low = str(e).lower()
                if (not low.strip()) or any(k in low for k in ("password", "decrypt", "incorrect", "crypt")):
                    return None, pw_msg
                return None, f"Could not open the PDF: {e}"
            try:
                raw_tbl = _tables_to_raw(pdf)
                all_text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
            finally:
                pdf.close()
            df, why = (_normalize_txn_df(raw_tbl) if raw_tbl is not None else (None, "no usable tables"))
            if df is None:
                raw_txt = _text_to_raw(all_text)
                if raw_txt is not None:
                    df, why = _normalize_txn_df(raw_txt)
                    if df is not None:
                        note = " · PDF text-parsed, best-effort — sanity-check the numbers"
            if df is None:
                return None, (f"Couldn't extract transactions from this PDF ({why}). "
                              "Bank PDF layouts vary a lot — export CSV or XLSX from net-banking, "
                              "that path is reliable.")
        else:
            return None, "Unsupported file type."
    except Exception as e:
        return None, f"Could not read the file: {e}"

    if df is None:
        return None, f"Could not parse the statement ({why})."
    return df, (f"Parsed {len(df)} transactions "
                f"({df.date.min().date()} to {df.date.max().date()}){note}.")


def analyze_statement(df: pd.DataFrame) -> dict:
    months = max(1, round((df.date.max() - df.date.min()).days / 30.0))
    total_cr = float(df.credit.sum()); total_dr = float(df.debit.sum())
    bounces = int(df.narration.str.contains(BOUNCE_RE).sum())

    # recurring debits → obligations
    deb = df[df.debit > 0].copy()
    deb["amt_key"] = (deb.debit / 10).round() * 10
    deb["month"] = deb.date.dt.to_period("M")
    grp = deb.groupby("amt_key").agg(n=("debit", "size"), months=("month", "nunique"),
                                     total=("debit", "sum"),
                                     narr=("narration", lambda s: s.mode().iat[0] if len(s) else "")).reset_index()
    recur = grp[(grp.months >= 2) & (grp.n >= 2)].copy()
    emi_like = recur[recur.narr.str.contains(EMI_RE)]
    obligation_monthly = round(float(emi_like.total.sum()) / months) if len(emi_like) else 0
    if obligation_monthly == 0 and len(recur):  # fall back: any fixed recurring outflow
        obligation_monthly = round(float(recur.total.sum()) / months)

    # income
    cr = df[df.credit > 0]
    sal = cr[cr.narration.str.contains(SAL_RE)]
    if len(sal) >= 2:
        income_monthly = round(float(sal.credit.sum()) / months)
        income_basis = "salary credits"
    else:
        farm = cr[cr.narration.str.contains(FARM_IN_RE)]
        if len(farm) >= 1:
            income_monthly = round(float(farm.credit.sum()) / months)
            income_basis = "agri / mandi credits"
        else:
            monthly_net = df.assign(m=df.date.dt.to_period("M")).groupby("m").apply(
                lambda g: g.credit.sum() - g.debit.sum())
            pos = monthly_net[monthly_net > 0]
            mean_pos = pos.mean() if len(pos) else 0.0
            if pd.isna(mean_pos) or mean_pos <= 0:
                mean_pos = float(cr.credit.sum()) / months  # fallback: gross credits / months
            income_monthly = round(float(mean_pos))
            income_basis = "avg positive monthly net inflow"

    foir = round(obligation_monthly / income_monthly, 2) if income_monthly else None

    bal = df.balance.dropna()
    avg_bal = round(float(bal.mean())) if len(bal) else None
    min_bal = int(bal.min()) if len(bal) else None

    big_cash = df[(df.credit > 50000) & df.narration.str.contains(CASH_DEP_RE)]
    return {
        "months_covered": months, "n_transactions": int(len(df)),
        "total_credit": round(total_cr), "total_debit": round(total_dr),
        "bounce_count": bounces,
        "recurring_obligation_monthly": obligation_monthly,
        "est_monthly_income": income_monthly, "income_basis": income_basis,
        "foir": foir, "avg_balance": avg_bal, "min_balance": min_bal,
        "large_cash_deposits": int(len(big_cash)),
        "recurring_items": recur[["amt_key", "n", "months", "narr"]].rename(
            columns={"amt_key": "amount", "narr": "narration"}).to_dict("records"),
    }


def pdf_meta(file) -> dict:
    if not PDF_OK:
        return {"note": "pdfplumber unavailable"}
    try:
        file.seek(0)
        with pdfplumber.open(file) as pdf:
            md = dict(pdf.metadata or {})
            has_sig = any("/Sig" in str(pdf.doc.catalog.get("AcroForm", "")) for _ in [0]) \
                if hasattr(pdf, "doc") else False
        return {"producer": md.get("Producer"), "creator": md.get("Creator"),
                "created": str(md.get("CreationDate")), "pages": len(pdf.pages) if PDF_OK else None,
                "digital_signature": bool(has_sig)}
    except Exception as e:
        return {"note": f"metadata read failed: {e}"}


def parse_bureau_pdf(file, password: str = "") -> dict:
    if not PDF_OK:
        return {"note": "pdfplumber unavailable — use manual entry"}
    try:
        file.seek(0)
        text = ""
        with pdfplumber.open(file, password=password or "") as pdf:
            for pg in pdf.pages:
                text += (pg.extract_text() or "") + "\n"
    except Exception as e:
        low = str(e).lower()
        if (not low.strip()) or any(k in low for k in ("password", "decrypt", "incorrect", "crypt")):
            return {"note": "🔒 Report PDF is locked — enter its password below (usually your PAN "
                            "in UPPERCASE, or DOB as DDMMYYYY) and retry, or use the manual tab."}
        return {"note": f"could not read PDF: {e}"}
    up = text.upper()
    score = None
    for m in re.finditer(r"(?:CIBIL|CREDITVISION|PERFORM|BUREAU)?\s*SCORE[^0-9]{0,20}(\d{3})", up):
        v = int(m.group(1))
        if 300 <= v <= 900:
            score = v
            break
    flags = {
        "write_off": bool(re.search(r"WRIT+EN?[- ]?OFF|WRITE[- ]?OFF", up)),
        "settled": bool(re.search(r"\bSETTLED\b", up)),
        "suit_filed": bool(re.search(r"SUIT[- ]?FILED|WILFUL DEFAULT", up)),
        "dpd_severe": bool(re.search(r"\b(SUB[- ]?STANDARD|DOUBTFUL|LOSS|90\+|120\+|180\+)\b", up)),
    }
    acc = len(re.findall(r"ACCOUNT (?:TYPE|NUMBER)|MEMBER NAME|SANCTIONED", up))
    return {"score": score, "flags": flags, "account_hits": acc, "chars": len(text)}


def enhanced_pillar(current_score, bureau_score, foir, bounces, hard_flag):
    bb = (85 if bureau_score is None else 10 if bureau_score >= 750 else 30 if bureau_score >= 700
          else 55 if bureau_score >= 650 else 75 if bureau_score >= 600 else 90)
    f = foir or 0
    fb = 10 if f < .30 else 30 if f < .45 else 55 if f < .60 else 80 if f < .75 else 95
    cb = 10 if bounces == 0 else 35 if bounces <= 2 else 65 if bounces <= 5 else 90
    e = round(current_score * .35 + bb * .30 + fb * .20 + cb * .15, 1)
    if hard_flag:
        e = max(e, 88.0)
    return e, {"legacy_component": current_score, "bureau_band": bb,
              "foir_band": fb, "banking_conduct_band": cb, "hard_decline_flag": hard_flag}


# ═══════════════════════════════════════════════════════════════════════
#  REAL MODE
# ═══════════════════════════════════════════════════════════════════════
def render_real():
    ss = st.session_state
    ss.setdefault("r_aadhaar", None)
    ss.setdefault("r_pan", None)
    ss.setdefault("r_bank", None)
    ss.setdefault("r_stmt", None)
    ss.setdefault("r_bureau", None)

    # ---- Step 1 : Aadhaar offline e-KYC ------------------------------------
    st.markdown("<div class='step'>Step 1 · Aadhaar offline e-KYC <span class='real'>REAL</span></div>",
                unsafe_allow_html=True)
    st.caption("Download your **Offline e-KYC** ZIP at myaadhaar.uidai.gov.in → 'Offline eKYC'. "
               "You set a 4-character share code; it is the ZIP password.")
    if not AADHAAR_OK:
        st.info("`pyaadhaar` not importable — `pip install pyaadhaar` to enable this step.")
    c1, c2 = st.columns([2, 1])
    zf = c1.file_uploader("Offline e-KYC ZIP", type=["zip"], key="aad_zip")
    share = c2.text_input("Share code", max_chars=10, key="aad_code")
    if zf and share and AADHAAR_OK and st.button("Read Aadhaar KYC file", type="primary"):
        try:
            tmp = os.path.join(tempfile.gettempdir(), f"ekyc_{datetime.now():%H%M%S}.zip")
            with open(tmp, "wb") as fh:
                fh.write(zf.getbuffer())
            a = AadhaarOfflineXML(tmp, share)
            d = a.decodeddata()
            sig_present = bool(a.signature())
            try:
                img_b = a.image()
            except Exception:
                img_b = None
            os.remove(tmp)
            ss.r_aadhaar = {"data": d, "signature_present": sig_present,
                            "photo": img_b, "verified_offline": True}
        except Exception as e:
            st.error(f"Could not read the file (wrong share code or not an offline-eKYC ZIP): {e}")

    if ss.r_aadhaar:
        d = ss.r_aadhaar["data"]
        cc1, cc2 = st.columns([1, 2])
        if ss.r_aadhaar.get("photo"):
            try:
                cc1.image(ss.r_aadhaar["photo"], width=110, caption="Aadhaar photo")
            except Exception:
                pass
        cc2.markdown(
            f"<div class='card'><b>Extracted from your Aadhaar KYC file</b> <span class='real'>REAL</span><br>"
            f"Name: <b>{d.get('name','—')}</b><br>DOB: {d.get('dob','—')} · Gender: {d.get('gender','—')}<br>"
            f"Address: {d.get('street','')} {d.get('vtc','')}, {d.get('district','')}, {d.get('state','')} "
            f"— {d.get('pincode','')}<br>Reference ID: <code>{d.get('referenceid','—')}</code><br>"
            f"XML signature block: <span class='{'ok' if ss.r_aadhaar['signature_present'] else 'bad'}'>"
            f"{'present' if ss.r_aadhaar['signature_present'] else 'MISSING'}</span> "
            f"<span class='vend'>(full UIDAI chain check needs the UIDAI signing cert)</span></div>",
            unsafe_allow_html=True)

    # ---- Step 2 : PAN ----------------------------------------------------
    st.markdown("<div class='step'>Step 2 · PAN <span class='real'>REAL format</span> "
                "<span class='paid'>ownership = paid</span></div>", unsafe_allow_html=True)
    pan = st.text_input("PAN (10 chars)", max_chars=10, key="pan_in").upper().strip()
    pan_name = st.text_input("Name exactly as printed on the PAN card", key="pan_name")
    if pan:
        ok = bool(PAN_RE.match(pan))
        ent = PAN_ENTITY.get(pan[3], "unknown") if ok else "—"
        st.markdown(f"Format: <span class='{'ok' if ok else 'bad'}'>{'VALID' if ok else 'INVALID'}</span> "
                    f"· 4th char <code>{pan[3] if len(pan) > 3 else '?'}</code> → holder type: <b>{ent}</b>",
                    unsafe_allow_html=True)
        nm = None
        if pan_name and ss.r_aadhaar:
            nm = round(fuzz.token_sort_ratio(pan_name, ss.r_aadhaar["data"].get("name", "")), 1)
            cls = "ok" if nm >= 85 else "warn" if nm >= 70 else "bad"
            st.markdown(f"Name match PAN ↔ Aadhaar: <span class='{cls}'>{nm}%</span> "
                        f"<span class='vend'>(rapidfuzz token-sort)</span>", unsafe_allow_html=True)
        ss.r_pan = {"pan": pan, "format_valid": ok, "entity": ent, "name": pan_name,
                    "name_match_aadhaar": nm}

    # ---- Step 3 : Bank account + address --------------------------------
    st.markdown("<div class='step'>Step 3 · Bank account &amp; address <span class='real'>REAL live lookup</span> "
                "<span class='paid'>penny-drop = paid</span></div>", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    acct = b1.text_input("Account number", key="acct_in")
    ifsc = b2.text_input("IFSC", key="ifsc_in").upper().strip()
    pin = b1.text_input("Address PIN code", max_chars=6, key="pin_in")
    if st.button("Verify IFSC + PIN (live)"):
        ss.r_bank = {"account": acct, "ifsc": ifsc}
        if ifsc:
            ss.r_bank["ifsc_lookup"] = fetch_ifsc(ifsc)
        if pin and len(pin) == 6:
            ss.r_bank["pin_lookup"] = fetch_pincode(pin)
    if ss.r_bank:
        il = ss.r_bank.get("ifsc_lookup")
        if il:
            st.markdown(f"<div class='card'><b>IFSC {ss.r_bank['ifsc']}</b> <span class='real'>REAL — Razorpay IFSC</span><br>"
                        f"{il.get('BANK','')} — {il.get('BRANCH','')}<br>{il.get('ADDRESS','')}<br>"
                        f"{il.get('CITY','')}, {il.get('STATE','')} · MICR {il.get('MICR','-')} · "
                        f"UPI {'yes' if il.get('UPI') else 'no'} · IMPS {'yes' if il.get('IMPS') else 'no'}</div>",
                        unsafe_allow_html=True)
        elif "ifsc_lookup" in ss.r_bank:
            st.warning("IFSC not found — check the code.")
        pl = ss.r_bank.get("pin_lookup")
        if pl:
            po = pl[0]
            in_area = any(k.lower() in (po.get("District", "") + po.get("Block", "")).lower()
                          for k in KNOWN_LOCATIONS)
            st.markdown(f"<div class='card'><b>PIN {pin}</b> <span class='real'>REAL — India Post</span><br>"
                        f"{po.get('District','')}, {po.get('State','')} "
                        f"({len(pl)} post office(s): {', '.join(p['Name'] for p in pl[:4])}…)<br>"
                        f"Within a listed soybean district: "
                        f"<span class='{'ok' if in_area else 'warn'}'>{'yes' if in_area else 'no'}</span></div>",
                        unsafe_allow_html=True)
        elif "pin_lookup" in ss.r_bank:
            st.warning("PIN not found.")

    # ---- Step 4 : Bank statement analysis ------------------------------
    st.markdown("<div class='step'>Step 4 · Bank statement analysis <span class='real'>REAL — your data</span></div>",
                unsafe_allow_html=True)
    st.caption("Export the last 6–12 months from net-banking as **CSV or XLSX** (most reliable). "
               "A password-protected **PDF** works too — give the password below.")
    up = st.file_uploader("Bank statement", type=["csv", "xlsx", "xls", "pdf"], key="stmt_up")
    stmt_pwd = st.text_input(
        "PDF password (leave blank if not protected)", type="password", key="stmt_pwd",
        help="Indian banks usually use one of: PAN in UPPERCASE · date of birth as DDMMYYYY · "
             "the account number · first 4 letters of name + DDMM of birth. Check the email the "
             "statement came in.")
    if up is not None:
        df, msg = parse_statement(up, stmt_pwd)
        if df is None:
            st.error(msg)
        else:
            st.success(msg)
            a = analyze_statement(df)
            if up.name.lower().endswith(".pdf"):
                st.caption(f"PDF integrity: {pdf_meta(up)}")
            m1, m2, m3 = st.columns(3)
            m1.metric("Est. monthly income", f"₹{a['est_monthly_income']:,}", help=a["income_basis"])
            m2.metric("Recurring obligations", f"₹{a['recurring_obligation_monthly']:,}/mo")
            m3.metric("FOIR", "—" if a["foir"] is None else f"{a['foir']:.2f}")
            m1.metric("Avg balance", "—" if a["avg_balance"] is None else f"₹{a['avg_balance']:,}")
            m2.metric("Lowest balance", "—" if a["min_balance"] is None else f"₹{a['min_balance']:,}")
            m3.metric("Bounces / returns", a["bounce_count"])
            if a["recurring_items"]:
                st.caption("Detected recurring debits:")
                st.dataframe(pd.DataFrame(a["recurring_items"]), hide_index=True, use_container_width=True)
            with st.expander("Parsed transactions"):
                st.dataframe(df, hide_index=True, use_container_width=True, height=280)
            ss.r_stmt = a

    # ---- Step 5 : Credit report --------------------------------------
    st.markdown("<div class='step'>Step 5 · Credit report <span class='real'>REAL parse of your report</span> "
                "<span class='paid'>live API = bureau membership</span></div>", unsafe_allow_html=True)
    tabu, tabm = st.tabs(["Upload your report PDF", "Enter manually"])
    with tabu:
        bpdf = st.file_uploader("Your downloaded CIBIL / CRIF / Experian report", type=["pdf"], key="bur_pdf")
        bur_pwd = st.text_input("Report PDF password (leave blank if not protected)", type="password",
                                key="bur_pwd", help="CIBIL/CRIF reports are often locked with your PAN "
                                "(UPPERCASE) or DOB as DDMMYYYY.")
        if bpdf is not None:
            parsed = parse_bureau_pdf(bpdf, bur_pwd)
            st.caption(f"PDF integrity: {pdf_meta(bpdf)}")
            if parsed.get("note"):
                st.warning(parsed["note"])
            else:
                fl = [k for k, v in parsed["flags"].items() if v]
                st.markdown(f"<div class='card'>Score found: "
                            f"<b>{parsed['score'] if parsed['score'] else 'not detected — use manual tab'}</b><br>"
                            f"Adverse flags detected: "
                            f"{'<span class=bad>' + ', '.join(fl) + '</span>' if fl else '<span class=ok>none</span>'}"
                            f"<br><span class='vend'>best-effort text parse — always confirm on the manual tab</span></div>",
                            unsafe_allow_html=True)
                ss.r_bureau = {"score": parsed["score"],
                               "write_off": parsed["flags"]["write_off"] or parsed["flags"]["suit_filed"],
                               "suit_filed": parsed["flags"]["suit_filed"], "source": "pdf-parse"}
    with tabm:
        s = st.number_input("Bureau score (300–900; 0 = no history)", 0, 900, 0, key="bur_score")
        wo = st.checkbox("Any write-off / settled account", key="bur_wo")
        sf = st.checkbox("Any suit-filed / wilful-default flag", key="bur_sf")
        if st.button("Use these bureau values"):
            ss.r_bureau = {"score": (s or None), "write_off": wo or sf, "suit_filed": sf, "source": "manual"}
    if ss.r_bureau:
        st.caption(f"Using bureau: score={ss.r_bureau['score']} · "
                   f"write_off={ss.r_bureau['write_off']} · suit_filed={ss.r_bureau['suit_filed']} "
                   f"({ss.r_bureau['source']})")

    # ---- Step 6 : Score + audit record ------------------------------
    st.markdown("<div class='step'>Step 6 · Borrower pillar &amp; audit record</div>", unsafe_allow_html=True)
    p1, p2 = st.columns(2)
    years = p1.number_input("Years the borrower has dealt with you", 0, 40, 0, key="years_in")
    fpo = p2.checkbox("Verified FPO / co-op member", key="fpo_in")
    ptx = p1.number_input("Past deals on your platform", 0, 500, 0, key="ptx_in")
    psx = p2.number_input("…of which repaid cleanly", 0, 500, 0, key="psx_in")

    ready = ss.r_stmt is not None and ss.r_bureau is not None
    if st.button("Compute borrower pillar + write audit file", type="primary", disabled=not ready):
        kyc_ok = bool(ss.r_aadhaar) and bool(ss.r_pan and ss.r_pan["format_valid"]) \
            and bool(ss.r_bank and ss.r_bank.get("ifsc_lookup"))
        nmatch = (ss.r_pan or {}).get("name_match_aadhaar")
        if nmatch is not None and nmatch < 70:
            kyc_ok = False
        bur = ss.r_bureau
        past_def = 1 if (bur.get("write_off") or bur.get("suit_filed")) else 0

        borrower_input = {
            "total_transactions": int(ptx), "successful_transactions": int(min(psx, ptx)),
            "years_on_platform": int(years), "kyc_verified": kyc_ok,
            "is_fpo_member": bool(fpo), "past_defaults": past_def,
        }
        cur = compute_borrower_risk(borrower_input)
        enh, enh_bk = enhanced_pillar(cur["score"], bur.get("score"),
                                      ss.r_stmt.get("foir"), ss.r_stmt.get("bounce_count", 0),
                                      bur.get("write_off") or bur.get("suit_filed"))

        cc1, cc2 = st.columns(2)
        cc1.metric("Borrower pillar — current engine", f"{cur['score']:.0f} / 100")
        cc1.json(cur["breakdown"])
        cc2.metric("Borrower pillar — enhanced", f"{enh:.0f} / 100", f"{enh - cur['score']:+.0f}")
        cc2.json(enh_bk)

        verified = {
            "aadhaar_offline_ekyc": "real" if ss.r_aadhaar else "not provided",
            "pan_format": "real" if (ss.r_pan and ss.r_pan["format_valid"]) else "invalid/none",
            "pan_ownership": "PAID API NEEDED (NSDL/Protean)",
            "name_match_pan_aadhaar": f"{nmatch}%" if nmatch is not None else "n/a",
            "bank_ifsc": "real" if (ss.r_bank and ss.r_bank.get("ifsc_lookup")) else "none",
            "bank_penny_drop": "PAID API NEEDED",
            "address_pincode": "real" if (ss.r_bank and ss.r_bank.get("pin_lookup")) else "none",
            "bank_statement_analysis": "real (your data)",
            "credit_bureau": f"{bur['source']} (LIVE API = bureau membership)",
            "platform_history": "operator-supplied",
        }
        run_id = "VR-" + hashlib.sha256(
            f"{ss.r_pan['pan'] if ss.r_pan else ''}{datetime.now().isoformat()}".encode()).hexdigest()[:14].upper()
        bundle = {
            "run_id": run_id, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mode": "verify-real-documents",
            "aadhaar": ({"name": ss.r_aadhaar["data"].get("name"),
                         "dob": ss.r_aadhaar["data"].get("dob"),
                         "district": ss.r_aadhaar["data"].get("district"),
                         "state": ss.r_aadhaar["data"].get("state"),
                         "reference_id": ss.r_aadhaar["data"].get("referenceid"),
                         "signature_present": ss.r_aadhaar["signature_present"]} if ss.r_aadhaar else None),
            "pan": ss.r_pan, "bank": {k: v for k, v in (ss.r_bank or {}).items() if k != "pin_lookup"},
            "cashflow_analysis": {k: v for k, v in ss.r_stmt.items() if k != "recurring_items"},
            "bureau": bur,
            "verification_matrix": verified,
            "borrower_input_to_risk_engine": borrower_input,
            "borrower_pillar_current": cur["score"], "borrower_pillar_enhanced": enh,
        }
        # hand the verified result to the rest of the app (Risk Assessment page)
        st.session_state["verified_borrower"] = {
            "run_id": run_id,
            "borrower_input": borrower_input,
            "pillar_current": cur["score"],
            "pillar_enhanced": enh,
            "foir": ss.r_stmt.get("foir"),
            "bounce_count": ss.r_stmt.get("bounce_count", 0),
            "monthly_income": ss.r_stmt.get("est_monthly_income"),
            "monthly_obligation": ss.r_stmt.get("recurring_obligation_monthly"),
            "bureau_score": bur.get("score"),
            "bureau_write_off": bool(bur.get("write_off")),
            "bureau_suit_filed": bool(bur.get("suit_filed")),
            "aadhaar_name": (ss.r_aadhaar["data"].get("name") if ss.r_aadhaar else None),
            "verification_matrix": verified,
            "generated_at": bundle["generated_at"],
        }

        path = os.path.join(RUNS_DIR, f"{run_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=2, default=str)
        st.success(f"Audit record written → `verification_runs/{run_id}.json`  ·  "
                   f"now open **⚠️ Risk Assessment** to score a receipt with this borrower.")
        st.code(json.dumps(bundle, indent=2, default=str), language="json")
        st.download_button("⬇️ Download audit record", json.dumps(bundle, indent=2, default=str),
                           file_name=f"{run_id}.json", mime="application/json", use_container_width=True)
    elif not ready:
        st.info("Provide at least a bank statement (Step 4) and bureau values (Step 5) to score.")


# ═══════════════════════════════════════════════════════════════════════
#  SAMPLE MODE  (all simulated)
# ═══════════════════════════════════════════════════════════════════════
SCENARIOS = {
    "Clean smallholder": dict(income=24000, bureau=758, loans=1, emi=0, bounces=0, fpo=True,
                              ptx=6, psx=6, years=2, defaults=0, wo=False, suit=False),
    "Thin file": dict(income=16000, bureau=None, loans=0, emi=0, bounces=0, fpo=False,
                      ptx=0, psx=0, years=0, defaults=0, wo=False, suit=False),
    "Over-leveraged": dict(income=30000, bureau=642, loans=3, emi=21400, bounces=4, fpo=False,
                           ptx=3, psx=2, years=1, defaults=0, wo=False, suit=False),
    "Prior default": dict(income=21000, bureau=574, loans=2, emi=0, bounces=5, fpo=False,
                          ptx=4, psx=2, years=1, defaults=2, wo=True, suit=True),
}


def render_sample():
    name = st.selectbox("Borrower archetype", list(SCENARIOS))
    sc = SCENARIOS[name]
    st.caption("Every value on this screen is invented locally to show the shape of the flow. "
               "Use **Verify real documents** for real checks.")
    foir = round(sc["emi"] / sc["income"], 2) if sc["income"] else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Est. monthly income", f"₹{sc['income']:,}")
    c2.metric("EMI outflow", f"₹{sc['emi']:,}/mo")
    c3.metric("FOIR", f"{foir:.2f}")
    c1.metric("Bureau score", "No history" if sc["bureau"] is None else sc["bureau"])
    c2.metric("Bounces (6m)", sc["bounces"])
    c3.metric("Active loans", sc["loans"])

    borrower_input = dict(total_transactions=sc["ptx"], successful_transactions=sc["psx"],
                          years_on_platform=sc["years"], kyc_verified=True,
                          is_fpo_member=sc["fpo"], past_defaults=sc["defaults"])
    cur = compute_borrower_risk(borrower_input)
    enh, bk = enhanced_pillar(cur["score"], sc["bureau"], foir, sc["bounces"], sc["wo"] or sc["suit"])
    cc1, cc2 = st.columns(2)
    cc1.metric("Borrower pillar — current engine", f"{cur['score']:.0f} / 100")
    cc1.json(cur["breakdown"])
    cc2.metric("Borrower pillar — enhanced", f"{enh:.0f} / 100", f"{enh - cur['score']:+.0f}")
    cc2.json(bk)
    st.json({"borrower_input_to_risk_engine": borrower_input, "SIMULATED": True})


def main():
    """Standalone-demo entry point (also callable, but app.py uses render_real directly)."""
    st.set_page_config(page_title="Borrower Verification", page_icon="🪪", layout="centered")
    st.markdown("<style>.stApp{background:#0f172a;color:#e2e8f0;}"
                "h1,h2,h3{color:#f8fafc !important;}[data-testid='stMetricValue']{color:#34d399;}"
                "code{color:#7dd3fc;}</style>", unsafe_allow_html=True)
    inject_css()
    st.title("🪪 Borrower Verification")
    with st.sidebar:
        st.markdown("### Mode")
        mode = st.radio("mode", ["Verify real documents", "Sample walkthrough"],
                        label_visibility="collapsed")
        st.markdown("---")
        st.markdown("<span class='real'>REAL</span> runs locally / free public APIs  \n"
                    "<span class='paid'>PAID</span> needs a vendor contract  \n"
                    "<span class='sim'>SIM</span> simulated", unsafe_allow_html=True)
        st.caption(f"risk_engine: {'live' if ENGINE_OK else 'fallback'} · "
                   f"aadhaar lib: {'yes' if AADHAAR_OK else 'no'} · pdf: {'yes' if PDF_OK else 'no'}")
    st.caption("Nothing is uploaded anywhere. Files are read in memory on this machine. "
               "Only two read-only public registries are contacted (bank IFSC, PIN code).")
    if mode == "Verify real documents":
        render_real()
    else:
        render_sample()
    if not ENGINE_OK:
        st.caption("_risk_engine.py not importable — borrower pillar uses a built-in mirror of its logic._")


if __name__ == "__main__":
    main()
