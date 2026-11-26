"""
fetch_agmarknet.py -- backfill real mandi prices from Agmarknet 2.0 using YOUR login token.

Background
----------
Agmarknet migrated to "Agmarknet 2.0" around 2025-11-07. The old open feed died, and so
did every mirror that depended on it (CEDA Ashoka also stops at 2025-10-30). The new API
at https://api.agmarknet.gov.in/v1 gates its price report:

    POST /daily-price-arrival/report
    -> 400 {"code": "TOKEN_OR_CAPTCHA_REQUIRED"}

There are two ways past that gate: solve a CAPTCHA, or present a bearer token. This
script uses the token -- which YOU obtain by logging in yourself. It never handles your
password, your OTP, or the CAPTCHA.

Getting your token (about 60 seconds)
-------------------------------------
 1. Open https://agmarknet.gov.in in a browser and log in
    (guest login works: mobile number -> OTP -> the CAPTCHA on screen).
 2. Press F12 -> Application tab -> Local Storage -> https://agmarknet.gov.in
 3. Click the key named `profile`. It holds JSON with an `accessToken` field.
 4. Copy the WHOLE value and save it to `agmarknet_token.txt` next to this script,
    or export it as AGMARKNET_TOKEN. Either the raw token or the full JSON works.

`agmarknet_token.txt` is gitignored. The token is short-lived -- if it expires,
repeat step 2-4 and re-run; the script resumes from what it already downloaded.

Usage
-----
    python fetch_agmarknet.py --check          # is my token valid?
    python fetch_agmarknet.py --discover       # what payload does the report want?
    python fetch_agmarknet.py --backfill       # pull 2025-11-01 -> today
    python fetch_agmarknet.py --backfill --from 2026-01-01 --to 2026-03-31
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://api.agmarknet.gov.in/v1"
TOKEN_FILE = os.path.join(_DIR, "agmarknet_token.txt")
RAW_DIR = os.path.join(_DIR, "agmarknet_raw")
OUT_CSV = os.path.join(_DIR, "soybean_mandi_backfill_2025_2026.csv")

SOYBEAN_CMDT_ID = 13                 # confirmed against /daily-price-arrival/filters
GAP_START = "2025-11-01"             # first day our real data is missing

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


# -- token ----------------------------------------------------------------
def load_token() -> str | None:
    """Accepts a raw JWT or the whole localStorage `profile` JSON blob."""
    raw = os.environ.get("AGMARKNET_TOKEN", "").strip()
    if not raw and os.path.exists(TOKEN_FILE):
        raw = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            for k in ("accessToken", "access_token", "token", "jwt"):
                if obj.get(k):
                    return str(obj[k]).strip()
        except json.JSONDecodeError:
            pass
    return raw.removeprefix("Bearer ").strip()


def auth_headers(token: str) -> dict:
    return {**BASE_HEADERS, "Authorization": f"Bearer {token}"}


def _explain_auth_failure(r: requests.Response) -> str:
    if r.status_code in (401, 403):
        return ("token rejected or expired -- redo steps 2-4 in this file's docstring "
                "and refresh agmarknet_token.txt")
    return f"HTTP {r.status_code}: {r.text[:200]}"


# -- endpoints ------------------------------------------------------------
def check_token(token: str) -> bool:
    r = requests.get(f"{API}/user-login_report/filters", headers=auth_headers(token),
                     timeout=(8, 45))
    if r.status_code == 200:
        d = r.json()
        data = d.get("data", d)
        print("  token OK -- /user-login_report/filters responded")
        if isinstance(data, dict):
            print(f"  filter groups: {list(data.keys())[:12]}")
        return True
    print("  " + _explain_auth_failure(r))
    return False


# Ranked guesses at the report payload. The bundle builds it from URLSearchParams over
# the filter form, so key names track /daily-price-arrival/filters. With a live token the
# server's validation errors tell us which is right -- that is what --discover is for.
def candidate_payloads(a: str, b: str) -> list:
    return [
        {"cmdt_id": SOYBEAN_CMDT_ID, "from_date": a, "to_date": b, "page": 1},
        {"commodity_id": SOYBEAN_CMDT_ID, "from_date": a, "to_date": b, "page": 1},
        {"cmdt_id": SOYBEAN_CMDT_ID, "start_date": a, "end_date": b, "page": 1},
        {"cmdt_id": [SOYBEAN_CMDT_ID], "from_date": a, "to_date": b,
         "state_id": [], "district_id": [], "market_id": [], "page": 1},
        {"cmdt_id": SOYBEAN_CMDT_ID, "date_from": a, "date_to": b, "page": 1},
        {"commodity": "Soyabean", "from_date": a, "to_date": b, "page": 1},
    ]


def discover(token: str, a="2026-08-01", b="2026-08-07") -> dict | None:
    """Find the payload shape the report accepts, by reading the server's own errors."""
    print(f"probing /user-login_report for {a} -> {b} …\n")
    for i, p in enumerate(candidate_payloads(a, b), 1):
        try:
            r = requests.post(f"{API}/user-login_report", json=p,
                              headers=auth_headers(token), timeout=(8, 90))
        except Exception as e:
            print(f"  [{i}] {type(e).__name__}: {e}")
            continue
        body = r.text[:260].replace("\n", " ")
        print(f"  [{i}] keys={list(p)}\n      -> {r.status_code}  {body}")
        if r.status_code == 200:
            try:
                j = r.json()
            except ValueError:
                continue
            rows = _extract_rows(j)
            if rows:
                print(f"\n  ACCEPTED. {len(rows)} rows; fields: {list(rows[0].keys())}")
                return p
            print("      (200 but no rows -- shape may still be right, window empty)")
        time.sleep(1.5)
    print("\n  no candidate accepted. Paste one failing response above and I'll adapt "
          "the payload -- the server names the field it wants.")
    return None


def _extract_rows(j) -> list:
    """The API wraps rows differently per endpoint; find the list of dicts."""
    if isinstance(j, list):
        return j
    if not isinstance(j, dict):
        return []
    for k in ("data", "records", "result", "rows", "report_data"):
        v = j.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
        if isinstance(v, dict):
            for k2 in ("data", "records", "rows", "results"):
                v2 = v.get(k2)
                if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                    return v2
    return []


def fetch_window(token: str, a: str, b: str, shape: dict, *, max_pages=200) -> list:
    """All rows for one date window, following pagination."""
    out, page = [], 1
    while page <= max_pages:
        p = dict(shape)
        for ka, kb in (("from_date", "to_date"), ("start_date", "end_date"),
                       ("date_from", "date_to")):
            if ka in p:
                p[ka], p[kb] = a, b
        p["page"] = page
        try:
            r = requests.post(f"{API}/user-login_report", json=p,
                              headers=auth_headers(token), timeout=(8, 120))
        except Exception as e:
            print(f"      page {page}: {type(e).__name__} -- retrying once")
            time.sleep(4)
            try:
                r = requests.post(f"{API}/user-login_report", json=p,
                                  headers=auth_headers(token), timeout=(8, 120))
            except Exception as e2:
                print(f"      page {page}: giving up ({type(e2).__name__})")
                break
        if r.status_code in (401, 403):
            raise PermissionError(_explain_auth_failure(r))
        if r.status_code != 200:
            print(f"      page {page}: HTTP {r.status_code} {r.text[:120]}")
            break
        rows = _extract_rows(r.json())
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 20:                       # short page = last page
            break
        page += 1
        time.sleep(0.8)
    return out


def _months(a: date, b: date):
    cur = date(a.year, a.month, 1)
    while cur <= b:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        yield max(cur, a), min(last, b)
        cur = (last + timedelta(days=1))


def normalise(rows: list) -> pd.DataFrame:
    """Map Agmarknet 2.0 fields onto our soybean_mandi_COMPLETE.csv schema."""
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    alias = {
        "date": ["arrival_date", "price_date", "date", "rpt_date", "trans_date"],
        "state_name": ["state_name", "state", "stateName"],
        "district_name": ["district_name", "district", "districtName"],
        "market_name": ["market_name", "market", "mkt_name", "marketName"],
        "min_price": ["min_price", "minPrice", "min_prc", "modal_min"],
        "max_price": ["max_price", "maxPrice", "max_prc", "modal_max"],
        "modal_price": ["modal_price", "modalPrice", "modal_prc", "mdl_price"],
        "variety": ["variety", "variety_name"],
        "grade": ["grade", "grade_name"],
        "arrivals": ["arrival", "arrivals", "arrival_qty", "quantity"],
    }
    out = pd.DataFrame()
    for tgt, cands in alias.items():
        for c in cands:
            if c in d.columns:
                out[tgt] = d[c]
                break
    if "date" in out:
        out["date"] = pd.to_datetime(out["date"], errors="coerce", dayfirst=True)
    for c in ("min_price", "max_price", "modal_price", "arrivals"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["commodity"] = "Soyabean"
    out["source"] = "agmarknet2.0"
    return out.dropna(subset=["date", "modal_price"]) if "modal_price" in out else out


def backfill(token: str, a: str, b: str, shape: dict | None) -> pd.DataFrame:
    shape = shape or discover(token)
    if not shape:
        print("cannot proceed without a working payload shape.")
        return pd.DataFrame()
    os.makedirs(RAW_DIR, exist_ok=True)
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    frames = []
    for m0, m1 in _months(d0, d1):
        tag = m0.strftime("%Y-%m")
        cache = os.path.join(RAW_DIR, f"soybean_{tag}.json")
        if os.path.exists(cache):
            rows = json.load(open(cache, encoding="utf-8"))
            print(f"  {tag}  {len(rows):>5} rows (cached)")
        else:
            rows = fetch_window(token, m0.isoformat(), m1.isoformat(), shape)
            json.dump(rows, open(cache, "w", encoding="utf-8"))
            print(f"  {tag}  {len(rows):>5} rows")
            time.sleep(1.2)
        if rows:
            frames.append(normalise(rows))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--from", dest="frm", default=GAP_START)
    ap.add_argument("--to", dest="to", default=date.today().isoformat())
    a = ap.parse_args()

    token = load_token()
    if not token:
        print(__doc__.split("Getting your token")[1].split("Usage")[0].strip())
        print(f"\nNo token found. Save it to {TOKEN_FILE} or set AGMARKNET_TOKEN.")
        return 1
    print(f"token loaded ({len(token)} chars, ...{token[-8:]})\n")

    if a.check or not (a.discover or a.backfill):
        return 0 if check_token(token) else 1
    if a.discover:
        return 0 if discover(token) else 1

    try:
        df = backfill(token, a.frm, a.to, None)
    except PermissionError as e:
        print(f"\n{e}")
        return 1
    if df.empty:
        print("\nnothing downloaded.")
        return 1

    df.to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV}  {len(df):,} rows  "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")

    # never trust a download we haven't audited -- that is how we got here
    from data_integrity import audit_price_series
    ser = (df.groupby("date", as_index=False)["modal_price"].mean()
           .rename(columns={"modal_price": "price"}))
    print()
    print(audit_price_series(ser, detail=df[["date", "modal_price"]]).summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
