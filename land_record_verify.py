"""
land_record_verify.py -- verify a farmer's uploaded Maharashtra 7/12 against the record.

Flow
----
    upload 7/12 (PDF or photo)
        -> extract  : District / Taluka / Village / Survey(Gat) no. + whatever else is readable
        -> operator confirms the 4 lookup keys           (OCR suggests, human confirms)
        -> provider.fetch(keys)  : the OFFICIAL record from a Maharashtra 7/12 source
        -> match(uploaded, official, borrower_kyc_name)   : field-by-field verdict
        -> save_audit(...)       : JSON to verification_runs/

Provider adapter
----------------
The exact request/response of a paid aggregator (Surepass / Landeed / Karza) is only
documented after signup, so this module talks to a `LandRecordProvider` interface:

    MockProvider      -- works today; echoes the parsed upload back as the "official"
                         record with small perturbations, so the whole flow is testable
                         without credentials.
    SurepassProvider  -- real adapter. Endpoint + payload shape are best-effort guesses
                         from public docs and marked TODO; once you have an API key and
                         the developer docs, only `_call()` and `_normalise()` change.

Nothing here fabricates a verification result. MockProvider clearly labels its output
`source="mock"`; a real run carries the provider's own source string.

OCR note
--------
7/12 extracts are in Marathi (Devanagari). This machine's tesseract has the English
pack only, so:
  * digitally-signed 7/12 PDFs (Mahabhulekh, ~Rs.15) carry a text layer -> pdfplumber
    reads them directly, no OCR, best case.
  * photos / plain scans -> English OCR recovers the digits (survey no., area, dates,
    year) and any Latin text; Marathi name/crop fields are flagged `needs_manual`.
Install `tesseract-ocr-mar` to lift that limitation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_DIR = os.path.join(_DIR, "verification_runs")
_TOKEN_FILE = os.path.join(_DIR, "surepass_token.txt")


def _load_token() -> str:
    """Surepass token from SUREPASS_TOKEN env var, else surepass_token.txt (gitignored)."""
    t = os.environ.get("SUREPASS_TOKEN", "").strip()
    if not t and os.path.exists(_TOKEN_FILE):
        t = open(_TOKEN_FILE, encoding="utf-8").read().strip()
    return t.removeprefix("Bearer ").strip()

MH_DISTRICTS = {
    "ahmednagar", "akola", "amravati", "beed", "bhandara", "buldhana", "chandrapur",
    "dhule", "gadchiroli", "gondia", "hingoli", "jalgaon", "jalna", "kolhapur",
    "latur", "nagpur", "nanded", "nandurbar", "nashik", "osmanabad", "dharashiv",
    "parbhani", "pune", "raigad", "ratnagiri", "sangli", "satara", "sindhudurg",
    "solapur", "thane", "wardha", "washim", "yavatmal", "aurangabad",
    "chhatrapati sambhajinagar", "palghar", "mumbai",
}

# A tenure string is pledgeable if its normalised form contains any of these.
# Covers "Class I", "Class-1", "Bhogvatadar Varg-1", "Bhogvatadar Class-1",
# "Occupant Class-1", "Khalsa", plain "Occupant".
PLEDGEABLE_TENURE_MARKERS = ("classi", "class1", "varg1", "varga1", "occupant", "khalsa")


def _is_pledgeable_tenure(tc: str) -> bool:
    n = _norm(tc)
    if not n:
        return False
    # explicit restricted classes never pledgeable
    if "class2" in n or "classii" in n or "varg2" in n or "inam" in n or "devasthan" in n:
        return False
    return any(m in n for m in PLEDGEABLE_TENURE_MARKERS)


# ─────────────────────────────────────────────────────────── extraction ──
@dataclass
class ExtractResult:
    fields: dict = field(default_factory=dict)     # best-guess structured values
    needs_manual: list = field(default_factory=list)
    raw_text: str = ""
    method: str = ""                                # "pdf_text" | "ocr_eng" | "none"

    def lookup_keys_ready(self) -> bool:
        need = ("district", "taluka", "village", "survey_no")
        return all(self.fields.get(k) for k in need)


def _pdf_text(data: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


def _ocr_image(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(io.BytesIO(data)), lang="eng")
    except Exception:
        return ""


_SURVEY_RE = re.compile(
    r"(?:gat|g\.?\s*no|survey|s\.?\s*no|भूमापन|गट)\D{0,12}(\d{1,4}(?:\s*/\s*\d{1,3})?[A-Za-z]?)",
    re.I)
_AREA_RE = re.compile(
    r"(\d{1,2}\.\d{1,4})\s*(?:h(?:ect)?(?:are)?s?|ha)\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _guess_fields(text: str) -> tuple[dict, list]:
    # Maharashtra's digitally-signed 7/12 PDFs embed a subset font with no ToUnicode
    # map, so pdfplumber emits "(cid:605)" placeholders for most Devanagari glyphs.
    # Latin digits survive. Strip the cid junk first so "(cid:605)" isn't read as "605".
    text = re.sub(r"\(cid:\d+\)", " ", text)
    t = text.lower()
    f, manual = {}, []

    m = _SURVEY_RE.search(text)
    if m:
        f["survey_no"] = re.sub(r"\s+", "", m.group(1))
    else:
        # fallback: "126/1" style token near a PU-ID / भूमापन marker
        m2 = re.search(r"(?:PU-ID|भूमापन)[^\d]{0,40}?(\d{1,4}/\d{1,3})", text)
        if m2:
            f["survey_no"] = m2.group(1)
        else:
            manual.append("survey_no")

    found_dist = next((d for d in MH_DISTRICTS if d in t), None)
    if found_dist:
        f["district"] = found_dist.title()
    else:
        manual.append("district")

    # taluka / village rarely OCR cleanly from a Marathi doc -> always operator-confirm
    manual += ["taluka", "village"]

    m = _AREA_RE.search(text)
    if m:
        f["area_hectare"] = float(m.group(1))

    yrs = _YEAR_RE.findall(text)
    if yrs:
        f["record_year"] = max(int(y) for y in yrs)

    for kw, key in (("soyabean", "Soyabean"), ("soybean", "Soyabean"),
                    ("wheat", "Wheat"), ("cotton", "Cotton"), ("tur", "Tur"),
                    ("gram", "Gram"), ("sugarcane", "Sugarcane")):
        if kw in t:
            f["crop"] = key
            break

    if "class i" in t or "class-1" in t or "वर्ग १" in text or "varg 1" in t:
        f["tenure_class"] = "Class I"

    # Encumbrance: the Devanagari words (बोजा/कर्ज/बँक) are usually garbled, but the
    # loan AMOUNTS come through as Latin digits. If the doc's "other rights" area
    # carries big rupee figures, treat it as an encumbrance the operator must clear.
    # (the 6-digit village code printed as "गाव :- <name> ( 560515 )" is not a loan)
    # mutation entries are "( 790 )"-style 3-4 digits, so a parenthesised 6-digit
    # number can only be the village code
    no_code = re.sub(r"\(\s*\d{6}\s*\)", " ", text)
    # digits often run straight into Devanagari ("कर्ज49000") or "रु.420000/-", so no \b
    loan_amts = re.findall(r"(?<![\d/])(\d{5,7})(?!\d|\.\d)", no_code)
    bank_words = re.search(r"(bank|बँक|ब.क|gramin|कज.|कर.ज|loan|mortgage|बोजा)", t)
    if re.search(r"\b(nil|n/?a|no\s+encumbrance|कोरा)\b", t) and not loan_amts:
        f["encumbrance"] = "Nil"
    elif loan_amts and bank_words:
        big = sorted({int(x) for x in loan_amts}, reverse=True)[:3]
        f["encumbrance"] = ("PRESENT — loan amount(s) on record: "
                            + ", ".join(f"Rs.{x:,}" for x in big))
        manual.append("encumbrance")           # operator must read the actual entries
    elif bank_words:
        f["encumbrance"] = "PRESENT — see document"
        manual.append("encumbrance")

    return f, manual


def extract_from_upload(file) -> ExtractResult:
    """`file`: a path, bytes, or an object with .read()/.name (Streamlit UploadedFile)."""
    if isinstance(file, (bytes, bytearray)):
        data, name = bytes(file), ""
    elif hasattr(file, "read"):
        data, name = file.read(), getattr(file, "name", "")
    else:
        name = str(file)
        with open(name, "rb") as fh:
            data = fh.read()
    ext = os.path.splitext(name)[1].lower()

    text, method = "", "none"
    if ext == ".pdf" or data[:5] == b"%PDF-":
        text = _pdf_text(data)
        method = "pdf_text" if text.strip() else "none"
        if not text.strip():                       # scanned PDF, no text layer
            text, method = _ocr_image(data), "ocr_eng"
    elif ext in (".txt", ".text") or (b"\x00" not in data[:1024] and ext == ""):
        try:
            text, method = data.decode("utf-8", "replace"), "plain_text"
        except Exception:
            text, method = "", "none"
    else:
        text, method = _ocr_image(data), "ocr_eng"

    fields, manual = _guess_fields(text) if text.strip() else ({}, [
        "district", "taluka", "village", "survey_no"])
    return ExtractResult(fields=fields, needs_manual=manual, raw_text=text, method=method)


# ──────────────────────────────────────────────────────── provider layer ──
class LandRecordProvider:
    source = "abstract"

    def fetch(self, *, district: str, taluka: str, village: str,
              survey_no: str, year: int | None = None) -> dict:
        raise NotImplementedError

    @staticmethod
    def _blank() -> dict:
        return {"owner_names": [], "co_owners": [], "area_hectare": None,
                "tenure_class": None, "crop": None, "crop_season": None,
                "encumbrance": None, "mutation_no": None, "mutation_date": None,
                "as_of": None, "source": None, "raw": None}


class MockProvider(LandRecordProvider):
    """Testable stand-in: returns a plausible 'official' record derived from the query."""
    source = "mock"

    def fetch(self, *, district, taluka, village, survey_no, year=None) -> dict:
        seed = int(hashlib.sha256(
            f"{district}{taluka}{village}{survey_no}".encode()).hexdigest(), 16)
        rng = seed % 1000
        rec = self._blank()
        rec.update({
            "owner_names": [f"{'Ramesh Bhagwan Patil' if rng % 2 else 'Suresh Kailas Deshmukh'}"],
            "co_owners": (["Sunanda Ramesh Patil"] if rng % 3 == 0 else []),
            "area_hectare": round(0.4 + (rng % 250) / 100.0, 2),
            "tenure_class": "Class I" if rng % 5 else "Class II",
            "crop": "Soyabean" if rng % 4 else "Wheat",
            "crop_season": f"Kharif {year or datetime.now().year}",
            "encumbrance": "Nil" if rng % 7 else
                           "State Bank of India — crop loan Rs.1,20,000 dt.12/06/2024",
            "mutation_no": f"{4000 + rng}", "mutation_date": "2019-09-03",
            "as_of": datetime.now().date().isoformat(),
            "source": "mock",
        })
        return rec


class SurepassProvider(LandRecordProvider):
    """
    Real adapter for Surepass.

    CONFIRMED by probing the live API (2026-09-10):
      * sandbox base   : https://sandbox.surepass.io/api/v1
      * production base : https://kyc-api.surepass.io/api/v1
      * auth header     : Authorization: Bearer <token>
      * endpoint shape  : /<category>/<action>   (e.g. /pan/pan)
      * response envelope: {"data": {...}, "message": str, "status_code": int, "success": bool}
        -> errors still return 200-shaped JSON with success=false, plus the real HTTP code

    STILL TODO — get these two from the Surepass console (app.surepass.app/docs, needs login):
      * PATH            : the land-record endpoint path (not publicly documented; probing
                          every obvious guess returned 404). Something like
                          "/land-records/land-records" — confirm in the console.
      * request fields  : the exact key names for state / district / taluka / village /
                          survey number. Adjust `fetch()` payload + `_normalise()` to match.

    A sandbox trial token expires ~30 days after issue and then every call returns
    401 "Your token is expired ... please consider upgrading to a paid subscription".
    """
    source = "surepass"
    SANDBOX_BASE = "https://sandbox.surepass.io/api/v1"
    PROD_BASE = "https://kyc-api.surepass.io/api/v1"
    PATH = "/land-records/land-records"                  # TODO confirm in the Surepass console

    def __init__(self, token: str | None = None, *, sandbox: bool = True,
                 timeout: float = 30.0):
        self.token = token or _load_token()
        self.base = self.SANDBOX_BASE if sandbox else self.PROD_BASE
        self.timeout = timeout

    def _call(self, payload: dict) -> dict:
        import requests
        if not self.token:
            raise RuntimeError(
                "no Surepass token — put it in surepass_token.txt or set SUREPASS_TOKEN")
        r = requests.post(self.base + self.PATH, json=payload, timeout=self.timeout,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"})
        try:
            body = r.json()
        except ValueError:
            if r.status_code == 404:
                raise RuntimeError(
                    f"Surepass 404 on {self.PATH} — this is a placeholder path; "
                    "get the real land-record endpoint from app.surepass.app/docs")
            raise RuntimeError(f"non-JSON response ({r.status_code}) from {self.PATH}")
        if r.status_code == 401:
            raise RuntimeError("Surepass 401: " + str(body.get("message", body)))
        if r.status_code == 404:
            raise RuntimeError(
                f"Surepass 404 on {self.PATH} — confirm the land-record path in the console")
        if not body.get("success", r.ok):
            raise RuntimeError("Surepass error: " + str(body.get("message", body)))
        return body

    def _normalise(self, resp: dict) -> dict:
        d = (resp or {}).get("data", resp) or {}
        rec = self._blank()
        rec.update({
            "owner_names": d.get("owner_names") or ([d["owner_name"]] if d.get("owner_name") else []),
            "co_owners": d.get("co_owners") or [],
            "area_hectare": _to_float(d.get("area_hectare") or d.get("area")),
            "tenure_class": d.get("tenure_class") or d.get("tenure"),
            "crop": d.get("crop") or d.get("current_crop"),
            "crop_season": d.get("crop_season") or d.get("season"),
            "encumbrance": d.get("encumbrance") or d.get("other_rights") or d.get("loan_details"),
            "mutation_no": d.get("mutation_no") or d.get("mutation_number"),
            "mutation_date": d.get("mutation_date"),
            "as_of": d.get("as_of") or datetime.now().date().isoformat(),
            "source": "surepass", "raw": d,
        })
        return rec

    def fetch(self, *, district, taluka, village, survey_no, year=None) -> dict:
        payload = {"state": "maharashtra", "district": district, "taluka": taluka,
                   "village": village, "survey_number": survey_no}
        if year:
            payload["year"] = year
        return self._normalise(self._call(payload))


class DigiLockerProvider(LandRecordProvider):
    """
    DigiLocker (MeriPehchaan) Requester adapter for Maharashtra 7/12.

    Why this beats parsing the uploaded PDF: a document pulled from DigiLocker comes
    with a machine-readable XML certificate-data layer, so the broken-font problem that
    scrambles the downloaded 7/12 PDF does not apply — you get owner / area / tenure /
    encumbrance as real XML fields.

    The flow is different from Surepass — you do NOT query by survey number. The farmer
    logs into their own DigiLocker, consents, and you read whichever documents they have
    linked. So:

        p = DigiLockerProvider(client_id=..., client_secret=..., redirect_uri=...)
        url, verifier = p.authorize_url()          # send the farmer here
        # ... farmer consents, DigiLocker redirects to redirect_uri?code=...&state=...
        tok = p.exchange_code(code, verifier)      # {access_token, ...}
        docs = p.list_issued(tok["access_token"])  # [{uri, doctype, issuer, name}, ...]
        rec  = p.fetch(access_token=tok["access_token"], uri=<the 7/12 uri>)

    CONFIRMED (public docs, 2026):
      * authorize : https://digilocker.meripehchaan.gov.in/public/oauth2/1/authorize
      * token     : https://digilocker.meripehchaan.gov.in/public/oauth2/2/token
      * OIDC + PKCE (S256), prompt=consent, client secret sent as a POST param
      * issued-files item fields: name, type, size, date, uri, doctype, description,
        issuerid, issuer   (Aadhaar's doctype is "ADHAR")

    TODO — from the Partner API v2.2 spec / an actual issued-files response after you
    register as a Requester:
      * ISSUED_PATH / PULL_XML_PATH  — confirm the exact v2 paths
      * DOCTYPE_712                  — the doctype code for Maharashtra 7/12
      * _parse_712_xml tag names     — the issuer's XML schema

    Maharashtra 7/12 auto-fetch is rated flaky in 2026 (fails 30–60% of the time); handle
    a missing / failed document gracefully.
    """
    source = "digilocker"
    AUTHORIZE = "https://digilocker.meripehchaan.gov.in/public/oauth2/1/authorize"
    TOKEN = "https://digilocker.meripehchaan.gov.in/public/oauth2/2/token"
    ISSUED_PATH = "https://digilocker.meripehchaan.gov.in/public/oauth2/2/files/issued"  # TODO confirm
    PULL_XML_PATH = "https://digilocker.meripehchaan.gov.in/public/oauth2/1/xml/{uri}"   # TODO confirm
    PULL_FILE_PATH = "https://digilocker.meripehchaan.gov.in/public/oauth2/1/file/{uri}"  # TODO confirm

    DOCTYPE_712 = "MHRTB"                    # TODO confirm — placeholder for MH 7/12
    ISSUER_712 = "maharashtra.gov.in"       # seen as the MH state issuer id

    def __init__(self, *, client_id: str | None = None, client_secret: str | None = None,
                 redirect_uri: str | None = None, timeout: float = 30.0):
        self.client_id = client_id or os.environ.get("DIGILOCKER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("DIGILOCKER_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.environ.get("DIGILOCKER_REDIRECT_URI", "")
        self.timeout = timeout

    # ---- OAuth2 (authorization-code + PKCE) --------------------------------
    def authorize_url(self, *, scope: str = "openid files.issueddocs",
                      state: str | None = None) -> tuple[str, str]:
        """Return (url_to_send_the_user_to, code_verifier_to_keep_for_exchange)."""
        import base64
        import hashlib as _h
        import secrets
        import urllib.parse
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            _h.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = urllib.parse.urlencode({
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.redirect_uri, "state": state or secrets.token_hex(8),
            "scope": scope, "prompt": "consent",
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return f"{self.AUTHORIZE}?{q}", verifier

    def exchange_code(self, code: str, code_verifier: str) -> dict:
        import requests
        r = requests.post(self.TOKEN, timeout=self.timeout, data={
            "grant_type": "authorization_code", "code": code,
            "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "code_verifier": code_verifier,
        })
        r.raise_for_status()
        return r.json()

    # ---- document access -------------------------------------------------
    def list_issued(self, access_token: str) -> list:
        import requests
        r = requests.get(self.ISSUED_PATH, timeout=self.timeout,
                         headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        j = r.json()
        return j.get("items") or j.get("data") or (j if isinstance(j, list) else [])

    def find_712(self, issued: list) -> dict | None:
        for it in issued:
            dt = str(it.get("doctype", "")).upper()
            iss = str(it.get("issuerid") or it.get("issuer") or "").lower()
            nm = str(it.get("name", "")).lower()
            if dt == self.DOCTYPE_712 or "7/12" in nm or "satbara" in nm or (
                    "maharashtra" in iss and ("land" in nm or "rtc" in nm or "ror" in nm)):
                return it
        return None

    def pull_xml(self, access_token: str, uri: str) -> bytes:
        import requests
        import urllib.parse
        url = self.PULL_XML_PATH.format(uri=urllib.parse.quote(uri, safe=""))
        r = requests.get(url, timeout=self.timeout,
                         headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        return r.content

    def fetch(self, *, access_token: str = "", uri: str = "",
              district=None, taluka=None, village=None, survey_no=None, year=None) -> dict:
        if not access_token:
            raise RuntimeError("DigiLocker needs an access_token from the consent flow")
        if not uri:
            it = self.find_712(self.list_issued(access_token))
            if not it:
                raise RuntimeError(
                    "no 7/12 found in this DigiLocker account — the farmer must link it "
                    "first (and MH 7/12 auto-fetch is flaky; may need a retry)")
            uri = it["uri"]
        return self._parse_712_xml(self.pull_xml(access_token, uri))

    # ---- XML -> our standard record ------------------------------------
    def _parse_712_xml(self, xml_bytes: bytes) -> dict:
        """
        Map the issuer's certificate XML to the standard record dict.

        Tag names below are best-effort — a Maharashtra 7/12 certificate XML has not been
        seen yet. Once you have one, adjust the `.find(...)` paths. The DataContent that
        DigiLocker returns is base64-encoded XML, so callers may need to base64-decode
        first; this handles both.
        """
        import base64
        import xml.etree.ElementTree as ET

        raw = xml_bytes.strip()
        if not raw.startswith(b"<"):
            try:
                raw = base64.b64decode(raw)
            except Exception:
                pass
        rec = self._blank()
        rec["source"] = "digilocker"
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            rec["raw"] = raw[:2000].decode("utf-8", "replace")
            rec["parse_error"] = str(e)
            return rec

        txt = lambda *names: next(
            (el.text.strip() for n in names
             for el in root.iter() if el.tag.split("}")[-1].lower() == n.lower()
             and el.text and el.text.strip()), None)

        owners = [el.text.strip() for el in root.iter()
                  if el.tag.split("}")[-1].lower() in ("owner", "ownername", "khatedar", "holder")
                  and el.text and el.text.strip()]
        rec.update({
            "owner_names": owners or ([txt("owner_name", "name")] if txt("owner_name", "name") else []),
            "area_hectare": _to_float(txt("area", "areahectare", "kshetra", "totalarea")),
            "tenure_class": txt("tenure", "tenureclass", "bhudharana", "landtenure"),
            "crop": txt("crop", "currentcrop", "pik"),
            "crop_season": txt("season", "cropseason", "hangam"),
            "encumbrance": txt("encumbrance", "otherrights", "boja", "loan", "charge"),
            "mutation_no": txt("mutation", "mutationno", "ferfar"),
            "mutation_date": txt("mutationdate"),
            "as_of": txt("issuedate", "generatedon", "asof") or datetime.now().date().isoformat(),
            "raw": raw[:4000].decode("utf-8", "replace"),
        })
        return rec


def _to_float(x):
    try:
        return round(float(re.sub(r"[^\d.]", "", str(x))), 4)
    except (TypeError, ValueError):
        return None


class MahaBhumiProvider(LandRecordProvider):
    """
    Verify a submitted 7/12 directly against the Maharashtra government — no signup.

    Every 7/12 downloaded from digitalsatbara.mahabhumi.gov.in carries a 16-digit
    VERIFICATION NUMBER printed at the bottom, next to Marathi text that reads
    "to verify validity, go to <portal> and use this number". That portal exposes a
    public page with no login and no captcha:

        POST https://digitalsatbara.mahabhumi.gov.in/DSLR/Login/Verify712
             verificaionno=<16 digits>          # note: the field name is misspelled
        -> 200 application/pdf : the AUTHORITATIVE document, freshly generated

    So we don't look the record up by survey number — we hand back the farmer's own
    verification number and get the government's copy of that exact document, then diff
    the two. That catches both a forged document and an edited one.

    CONFIRMED working 2026-09-10 against a real 7/12 (verification no 2805100001066211):
    survey no, all five area figures, both loan amounts and the PU-ID matched exactly
    between the submitted copy and the government's. Only the download timestamp differs,
    because the portal regenerates the PDF on each request — so compare TEXT, never hashes.

    SECURITY MODEL — read this before relying on it.
    The verification number is a LOOKUP KEY, not a checksum. Changing its last four
    digits returned a *different real property's* 7/12, not an error. So a PDF coming
    back proves nothing on its own. Authenticity needs two layers:

      1. compare_documents()  — does the submitted PDF match the government's copy for
                                that number?  catches edited / fabricated documents.
      2. match(..., borrower_kyc_name=...)
                              — is the owner on that record actually the borrower?
                                catches someone submitting a genuine document for land
                                they do not own.

    Layer 1 alone is not enough. Always run both.

    Sibling endpoints on the same portal: Verify8A, VerifyFerfar, VerifyPropertyCard,
    VerifyeRecords.
    """
    source = "mahabhumi"
    BASE = "https://digitalsatbara.mahabhumi.gov.in"
    VERIFY_PATHS = {"7/12": "/DSLR/Login/Verify712", "8A": "/DSLR/Login/Verify8A",
                    "ferfar": "/DSLR/Login/VerifyFerfar",
                    "property_card": "/DSLR/Login/VerifyPropertyCard",
                    "erecords": "/DSLR/Login/VerifyeRecords"}
    FIELD = "verificaionno"                     # sic - the portal's own spelling

    def __init__(self, *, doc: str = "7/12", timeout: float = 90.0):
        self.path = self.VERIFY_PATHS[doc]
        self.timeout = timeout

    def fetch_official_pdf(self, verification_no: str) -> bytes:
        """The government's copy of the document behind this verification number."""
        import requests
        vn = re.sub(r"\D", "", str(verification_no))
        if len(vn) < 12:
            raise ValueError(f"verification number looks wrong: {verification_no!r}")
        url = self.BASE + self.path
        h = {"User-Agent": "Mozilla/5.0", "Referer": url}
        s = requests.Session()
        s.get(url, headers=h, timeout=(8, 30))          # pick up any session cookie
        r = s.post(url, data={self.FIELD: vn}, headers=h, timeout=(10, self.timeout))
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200:
            raise RuntimeError(f"MahaBhumi returned HTTP {r.status_code}")
        if "pdf" not in ctype.lower():
            raise RuntimeError(
                f"MahaBhumi did not return a document for {vn} (got {ctype!r})")
        # NOTE: getting a PDF back does NOT mean the number is "valid". The verification
        # number is a LOOKUP KEY, not a checksum -- tested 2026-09-10, altering the last
        # four digits returned a different real property's 7/12 (another village, survey
        # number and owner) rather than an error. Authenticity therefore comes from
        # comparing this document against the submitted one, never from the fact that a
        # PDF came back. See compare_documents() / verify_submitted_document().
        return r.content

    def fetch(self, *, verification_no: str = "", district=None, taluka=None,
              village=None, survey_no=None, year=None) -> dict:
        pdf = self.fetch_official_pdf(verification_no)
        text = _pdf_text(pdf)
        fields, _ = _guess_fields(text)
        owners = parse_owner_rows(text)
        rec = self._blank()
        rec.update({
            "owner_names": [o["name"] for o in owners],
            "owner_rows": owners,                     # khata + name + area per holder
            "area_hectare": (fields.get("area_hectare")
                             or (sum(o["area_hectare"] for o in owners if o["area_hectare"])
                                 or None)),
            "tenure_class": parse_tenure(text) or fields.get("tenure_class"),
            "crop": fields.get("crop"),
            "encumbrance": fields.get("encumbrance"),
            "as_of": datetime.now().date().isoformat(),
            "source": "mahabhumi",
            "verification_no": re.sub(r"\D", "", str(verification_no)),
            "official_pdf_bytes": len(pdf),
            "raw": text[:4000],
        })
        return rec


# ── reading the Devanagari off a MahaBhumi 7/12 ──────────────────────────
# The PDF's font drops ~21% of the Devanagari as "(cid:NNN)". Dropping those tokens
# outright (rather than substituting a placeholder) leaves a shortened but still
# distinctive name: "रमेश तातेराव बिडवे" reads back as "रमे तायारा िबडे". That is enough
# to tell the right holder from a stranger, but NOT enough to auto-approve — see
# OWNER_MATCH_* below.
_DEV = r"ऀ-ॿ"
_OWNER_ROW_RE = re.compile(
    rf"(\d{{2,4}})\s+([{_DEV}][{_DEV}\s]{{3,40}}?)\s+(\d\.\d\d\.\d\d)")

# Owner-name confidence bands for a lossy Devanagari comparison.
# Calibrated 2026-09-10 on verification no 2805100001066211: the two genuine holders
# scored 69 and 75 against their own rows; an unrelated name scored 41.
OWNER_MATCH_CONFIRM = 60.0     # below this -> treat as a different person
OWNER_MATCH_STRONG = 80.0      # at/above this -> good enough to pass unattended


def strip_cid(text: str) -> str:
    """Remove the PDF font's unmappable-glyph tokens, leaving the readable characters."""
    return re.sub(r"\(cid:\d+\)", "", text or "")


def parse_owner_rows(text: str) -> list:
    """
    Holder rows from a 7/12: [{khata, name, area_hectare}, ...].

    A row is "<khata no.> <Devanagari name> <a.bb.cc area>" in the ownership table.
    """
    out, seen = [], set()
    for khata, name, area in _OWNER_ROW_RE.findall(strip_cid(text)):
        nm = re.sub(r"\s+", " ", name).strip()
        if len(nm) < 4 or nm in seen:
            continue
        seen.add(nm)
        try:
            h, a, sq = (int(x) for x in area.split("."))
            hectare = round(h + a / 100 + sq / 10000, 4)
        except ValueError:
            hectare = None
        out.append({"khata": khata, "name": nm, "area_hectare": hectare,
                    "area_raw": area})
    return out


def parse_tenure(text: str) -> str | None:
    """Tenure class from the भुधारणा पद्धती line (e.g. 'भोगवटादार वर्ग -1')."""
    t = strip_cid(text)
    m = re.search(r"भुधारणा\s*प\S*ती\s*([^\n:]{0,40})", t)
    seg = m.group(1) if m else t[:400]
    if re.search(r"भोग\S*टादार", seg) and re.search(r"-?\s*1\b|\bवग\S*\s*-?1", seg):
        return "Bhogvatadar Varg-1 (Class I)"
    if re.search(r"-?\s*2\b|\bवग\S*\s*-?2", seg) and re.search(r"भोग\S*टादार", seg):
        return "Bhogvatadar Varg-2 (Class II)"
    return None


def match_owner(owner_rows: list, kyc_name_devanagari: str) -> dict:
    """
    Best holder match for the borrower, with an explicit confidence band.

    Returns {score, best, status}: "pass" (>=STRONG), "confirm" (>=CONFIRM, an operator
    must eyeball the two names), or "fail". The middle band exists because the PDF font
    loses characters — a genuine owner can legitimately score in the 60s.
    """
    try:
        from rapidfuzz import fuzz
        score_fn = lambda a, b: max(fuzz.partial_ratio(a, b), fuzz.token_set_ratio(a, b))
    except Exception:
        # no rapidfuzz: difflib approximation of partial_ratio / token_set_ratio, so a
        # lossy-but-genuine name still lands in the 'confirm' band instead of scoring 0
        import difflib
        def score_fn(a, b):
            if not a or not b:
                return 0.0
            short, long_ = sorted((a, b), key=len)
            win = max((difflib.SequenceMatcher(None, short, long_[i:i + len(short)]).ratio()
                       for i in range(0, len(long_) - len(short) + 1)), default=0.0)
            ta, tb = sorted(set(a.split())), sorted(set(b.split()))
            tok = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
            return 100.0 * max(win, tok)
    kyc = strip_cid(kyc_name_devanagari or "").strip()
    best, best_score = None, 0.0
    for row in owner_rows:
        s = score_fn(row["name"], kyc)
        if s > best_score:
            best, best_score = row, s
    status = ("pass" if best_score >= OWNER_MATCH_STRONG else
              "confirm" if best_score >= OWNER_MATCH_CONFIRM else "fail")
    return {"score": round(best_score, 1), "best": best, "status": status}


# ── everything a Maharashtra 7/12 actually carries ───────────────────────
# Mapped 2026-09-10 by dumping a real government extract line by line. A 7/12 is two
# forms: Form 7 (अधिकार अभिलेख — the rights record) and Form 12 (पिकांची नोंदी — the
# crop record). Form 12 is frequently EMPTY, which is not an error.
LAND_RECORD_FIELDS = {
    # ── identity ──
    "village": "गाव — village name",
    "village_code": "village LGD/census code",
    "taluka": "तालुका",
    "district": "जिल्हा",
    "ulpin": "PU-ID / ULPIN — the plot's unique national land id",
    "survey_no": "भूमापन क्रमांक व उपविभाग — survey/gat no. + subdivision",
    "local_name": "क्षेत्राचे स्थानीक नाव — local field name",
    # ── tenure ──
    "tenure_class": "भुधारणा पद्धती — Class I is freely pledgeable, Class II is not",
    # ── holders ──
    "holders": "भोगवटादाराचे नाव — occupant rows: khata no. + name + area + assessment",
    # ── area breakdown ──
    "area_cultivable_rainfed": "जिरायत — rain-fed cultivable area",
    "area_cultivable_irrigated": "बागायत — irrigated cultivable area",
    "area_uncultivable": "पोटखराब — uncultivable area (वर्ग अ + वर्ग ब)",
    "area_total": "एकूण क्षेत्र (अ+ब) — total area",
    "assessment": "आकारणी — land revenue assessment",
    "judi": "जुडी वा विशेष आकारणी — special assessment/quit rent",
    # ── other rights + encumbrances ──
    "easements": "गाडीमार्ग / पायमार्ग — cart-track and foot-path rights of way",
    "wells": "बोअरवेल / विहीर — well and borewell entries",
    "encumbrances": "बोजा — mortgages/charges: bank, borrower, amount, date, entry no.",
    # ── mutation history ──
    "mutation_last_no": "शेवटचा फेरफार क्रमांक — most recent mutation entry",
    "mutation_last_date": "date of that mutation",
    "mutation_pending": "प्रलंबित फेरफार — pending mutation (नाही = none)",
    "mutation_old": "जुने फेरफार — earlier mutation entries",
    # ── provenance ──
    "digitally_signed_on": "when the state digitally signed this record",
    "downloaded_on": "when this copy was pulled from the portal",
    "verification_no": "16-digit number used to re-fetch this record",
    # ── Form 12 ──
    "crops": "पिकांची नोंदी — season/crop/irrigation rows (OFTEN EMPTY)",
}

_ENC_ENTRY_RE = re.compile(r"\(\s*(\d{3,4})\s*\)")


def parse_full_record(text: str) -> dict:
    """
    Pull every field a Maharashtra 7/12 carries. See LAND_RECORD_FIELDS for what each is.

    Values that genuinely are not on the document come back as None or [] — an empty
    Form 12 crop table is normal, not a parse failure.
    """
    t = strip_cid(text)
    flat = re.sub(r"[ \t]+", " ", t)
    g = lambda pat, grp=1: (m.group(grp).strip() if (m := re.search(pat, flat)) else None)
    area = lambda pat: (_to_float(m.group(1).replace(".", "", 1))
                        if (m := re.search(pat, flat)) else None)

    def area_hh(pat):
        """Areas print as h.aa.ss (hectare.are.sq.metre) -> decimal hectares."""
        m = re.search(pat, flat)
        if not m:
            return None
        try:
            h, a, s = (int(x) for x in m.group(1).split("."))
            return round(h + a / 100 + s / 10000, 4)
        except ValueError:
            return None

    rec = {k: None for k in LAND_RECORD_FIELDS}
    rec["village"] = g(r"गा\S*\s*:-\s*(\S+)")
    rec["village_code"] = g(r"गा\S*\s*:-\s*\S+\s*\(\s*(\d+)\s*\)")
    rec["taluka"] = g(r"ता\S*ुका\s*:-\s*(\S+)")
    rec["district"] = g(r"िज\S*ा\s*:-\s*(\S+)")
    rec["ulpin"] = g(r"\b(\d{11})\b")
    rec["survey_no"] = g(r"भूमापन\s*\S*मांक\s*\S*\s*उपि?\S*भाग\s*(\d{1,4}(?:/\d{1,3})?)")
    rec["tenure_class"] = parse_tenure(t)
    rec["holders"] = parse_owner_rows(t)

    rec["area_cultivable_rainfed"] = area_hh(r"िजरायत\s+(\d\.\d\d\.\d\d)")
    rec["area_cultivable_irrigated"] = area_hh(r"बागायत\s+(\d\.\d\d\.\d\d)")
    rec["area_uncultivable"] = area_hh(r"पो\.ख\.\S*\s*(\d\.\d\d\.\d\d)")
    rec["area_total"] = area_hh(r"एकुण\s*\S*\s*(\d\.\d\d\.\d\d)\s*\n?\s*\(अ\+ब\)") \
        or area_hh(r"एकुण\s*े\s*(\d\.\d\d\.\d\d)")
    rec["assessment"] = _to_float(g(r"आकारणी\s+(\d+\.\d+)"))
    rec["judi"] = g(r"जुडी\s*\S*\s*\S*ि?\S*े\s*([^\n]{0,30})") or None

    rec["easements"] = [w for w in ("गाडीमाग", "पायमाग", "पायाट") if w in flat]
    rec["wells"] = [re.sub(r"\s+", " ", l).strip()
                    for l in t.split("\n") if re.search(r"बोअर|िि?ीर", l)]

    # encumbrances: bank/loan lines, each usually ending in a "( nnnn )" mutation entry
    encs = []
    for line in t.split("\n"):
        l = re.sub(r"\s+", " ", line).strip()
        if not re.search(r"बँक|कज|बोजा|ामीण", l):
            continue
        amt = re.findall(r"(\d{5,7})", l)
        entry = _ENC_ENTRY_RE.findall(l)
        date = re.search(r"(\d{2}/\d{2}/\d{4})", l)
        # skip section headers and rule lines: a real charge carries an amount
        # or a mutation entry number
        if not amt and not entry:
            continue
        if set(l) <= set("- "):
            continue
        encs.append({"line": re.sub(r"-{3,}", " ", l).strip()[:160],
                     "amount": int(amt[0]) if amt else None,
                     "entry_no": entry[0] if entry else None,
                     "date": date.group(1) if date else None})
    rec["encumbrances"] = encs

    rec["mutation_last_no"] = g(r"ेटचा\s*फेरफार\s*मांक\s*:\s*(\d+)")
    rec["mutation_last_date"] = g(r"ेटचा\s*फेरफार[^\n]*\n?[^\d]*(\d{2}/\d{2}/\d{4})")
    rec["mutation_pending"] = ("none" if re.search(r"ंिबत\s*फेरफार\s*:\s*ना", flat)
                               else g(r"ंिबत\s*फेरफार\s*:\s*([^\n]{0,30})"))
    rec["mutation_old"] = re.findall(r"जुने\s*फेरफार\s*:\s*((?:\(\d+\)\s*)+)", flat)[:1]
    if rec["mutation_old"]:
        rec["mutation_old"] = re.findall(r"\((\d+)\)", rec["mutation_old"][0])

    rec["digitally_signed_on"] = g(r"िदनांक\s*(\d{2}/\d{2}/\d{4}:\d{2}:\d{2}:\d{2}\s*[AP]M)")
    rec["downloaded_on"] = g(r"डाउन\S*ोड\s*िद\.\s*:\s*([\d\-]+\s*:\s*[\d:]+\s*[AP]M)")
    rec["verification_no"] = g(r"\b(\d{16})\b")

    # Form 12 crop rows — season / khata / crop / area. Usually absent.
    crops = []
    for m in re.finditer(r"(\d{1,4})\s+([ऀ-ॿ][ऀ-ॿ\s]{2,25}?)\s+"
                         r"(\d\.\d\d\.\d\d)", t.split("बारा")[-1] if "बारा" in t else ""):
        crops.append({"khata": m.group(1), "crop": m.group(2).strip(), "area": m.group(3)})
    rec["crops"] = crops
    return rec


def print_full_record(rec: dict, *, file=None) -> str:
    """Human-readable dump of parse_full_record(). Returns the text as well as printing it."""
    L = []
    add = lambda s="": L.append(s)
    add("=" * 74)
    add("  MAHARASHTRA 7/12 — EVERYTHING ON THE GOVERNMENT RECORD")
    add("=" * 74)

    add("\n[ IDENTITY ]")
    add(f"  Village              : {rec.get('village')}  (code {rec.get('village_code')})")
    add(f"  Taluka / District    : {rec.get('taluka')} / {rec.get('district')}")
    add(f"  Survey (gat) no.     : {rec.get('survey_no')}")
    add(f"  ULPIN / PU-ID        : {rec.get('ulpin')}")
    add(f"  Tenure class         : {rec.get('tenure_class')}")

    add("\n[ HOLDERS ]")
    for h in rec.get("holders") or []:
        add(f"  khata {h['khata']:<5} {h['area_raw']} ha   {h['name']}")
    if not rec.get("holders"):
        add("  (none parsed)")

    add("\n[ AREA ]")
    add(f"  Rain-fed (जिरायत)     : {rec.get('area_cultivable_rainfed')} ha")
    add(f"  Irrigated (बागायत)    : {rec.get('area_cultivable_irrigated')} ha")
    add(f"  Uncultivable (पोटखराब): {rec.get('area_uncultivable')} ha")
    add(f"  TOTAL                : {rec.get('area_total')} ha")
    add(f"  Assessment (आकारणी)   : {rec.get('assessment')}")

    add("\n[ ENCUMBRANCES / बोजा ]  <- the double-pledge check")
    for e in rec.get("encumbrances") or []:
        amt = f"Rs.{e['amount']:,}" if e.get("amount") else "-"
        add(f"  {amt:>14}  entry {e.get('entry_no') or '-':<6} {e.get('date') or '':<11}")
        add(f"                  {e['line'][:100]}")
    if not rec.get("encumbrances"):
        add("  NIL — no charge registered on this land")

    add("\n[ OTHER RIGHTS ]")
    add(f"  Easements            : {', '.join(rec.get('easements') or []) or 'none'}")
    for w in (rec.get("wells") or [])[:3]:
        add(f"  Well/borewell        : {w[:90]}")

    add("\n[ MUTATION HISTORY ]")
    add(f"  Last mutation        : {rec.get('mutation_last_no')} on {rec.get('mutation_last_date')}")
    add(f"  Pending mutation     : {rec.get('mutation_pending')}")
    add(f"  Older mutations      : {', '.join(rec.get('mutation_old') or []) or '-'}")

    add("\n[ CROPS — Form 12 ]")
    for c in rec.get("crops") or []:
        add(f"  khata {c['khata']:<5} {c['area']}  {c['crop']}")
    if not rec.get("crops"):
        add("  (empty — Form 12 carries no crop entries for this plot; this is common "
            "and is NOT a parse failure)")

    add("\n[ PROVENANCE ]")
    add(f"  Digitally signed on  : {rec.get('digitally_signed_on')}")
    add(f"  This copy downloaded : {rec.get('downloaded_on')}")
    add(f"  Verification number  : {rec.get('verification_no')}")
    add("=" * 74)

    out = "\n".join(L)
    print(out, file=file)
    return out


def full_record_from_verification_no(verification_no: str, *, doc: str = "7/12") -> dict:
    """Fetch a document from MahaBhumi by its verification number and parse every field."""
    pdf = MahaBhumiProvider(doc=doc).fetch_official_pdf(verification_no)
    rec = parse_full_record(_pdf_text(pdf))
    rec["_pdf_bytes"] = len(pdf)
    return rec


_VERIF_NO_RE = re.compile(r"\b(\d{16})\b")


def extract_verification_no(text_or_result) -> str | None:
    """The 16-digit MahaBhumi verification number printed on a digital 7/12."""
    text = (text_or_result.raw_text if isinstance(text_or_result, ExtractResult)
            else str(text_or_result))
    m = _VERIF_NO_RE.search(re.sub(r"\(cid:\d+\)", " ", text))
    return m.group(1) if m else None


# Facts that must be identical between the farmer's copy and the government's.
# Deliberately all numeric: the Devanagari is scrambled by the PDF's broken font,
# but digits survive intact, so this comparison is reliable where text matching is not.
_DOC_FACTS = {
    "verification_no": r"\b\d{16}\b",
    "pu_id": r"\b\d{11}\b",
    "survey_no": r"\b\d{1,4}/\d{1,3}\b",
    "areas": r"\b\d\.\d\d\.\d\d\b",
    "amounts": r"\b\d{5,7}\b",
}


# An 8A prints areas as 1.7500 (not h.aa.ss) and carries khata no. + levy figures.
_DOC_FACTS_8A = {
    "verification_no": r"\b\d{16}\b",
    "survey_no": r"(?m)^\s*(\d{1,4}(?:/\d{1,3})?)\s+\d+\.\d{4}\b",
    "areas": r"\b\d+\.\d{4}\b",
    "khata_no": r"खाते\s*\S*मांक\s*:\s*(\d+)",
    "levy_figures": r"(?<![\d.])\d{1,6}\.\d{2}(?![\d.])",
}


def compare_documents(submitted: bytes, official: bytes, *, doc: str = "7/12") -> dict:
    """
    Diff a farmer-submitted 7/12 (or 8A) against the government's copy of the same document.

    Compares extracted FACTS, not bytes: the portal regenerates the PDF on every
    request (new download timestamp), so hashes never match even for a genuine document.
    """
    clean = lambda b: re.sub(r"\(cid:\d+\)", " ", _pdf_text(b))
    ts, to = clean(submitted), clean(official)
    checks, mismatched = [], []
    facts = _DOC_FACTS_8A if doc == "8A" else _DOC_FACTS
    for name, pat in facts.items():
        a, b = sorted(set(re.findall(pat, ts))), sorted(set(re.findall(pat, to)))
        ok = a == b
        checks.append({"fact": name, "submitted": a, "official": b,
                       "status": "pass" if ok else "fail"})
        if not ok:
            mismatched.append(name)
    return {"identical": not mismatched, "mismatched": mismatched, "checks": checks,
            "submitted_bytes": len(submitted), "official_bytes": len(official)}


def verify_submitted_document(file, *, doc: str = "7/12") -> dict:
    """
    End-to-end authenticity check of a farmer's uploaded 7/12, with no credentials.

        1. read the upload, pull its 16-digit verification number
        2. ask MahaBhumi for the government's copy of that document
        3. diff the facts

    Returns {"authentic": bool, "verdict": str, ...}. `authentic` is True only when the
    government recognised the number AND every compared fact matched.
    """
    if hasattr(file, "read"):
        data = file.read()
    elif isinstance(file, (bytes, bytearray)):
        data = bytes(file)
    else:
        data = open(file, "rb").read()

    ext = extract_from_upload(data if not isinstance(file, str) else file)
    vn = extract_verification_no(ext)
    out = {"verification_no": vn, "extract_method": ext.method,
           "authentic": False, "verdict": "reject", "reason": None}
    if not vn:
        out["reason"] = ("no 16-digit verification number found — this is probably a "
                         "view-only print, not a digitally-issued 7/12 from "
                         "digitalsatbara.mahabhumi.gov.in")
        out["verdict"] = "review"
        return out
    try:
        official = MahaBhumiProvider(doc=doc).fetch_official_pdf(vn)
    except Exception as e:
        out["reason"] = str(e)
        return out
    cmp = compare_documents(data, official, doc=doc)
    out["comparison"] = cmp
    out["authentic"] = cmp["identical"]
    out["verdict"] = "verified" if cmp["identical"] else "reject"
    out["reason"] = ("document matches the government copy" if cmp["identical"]
                     else "differs from the government copy in: "
                          + ", ".join(cmp["mismatched"]))
    return out


def verify_land_ownership(file, *, borrower_name_devanagari: str = "",
                          borrower_name_latin: str = "", doc: str = "7/12",
                          require_clear_encumbrance: bool = True,
                          locate: bool = True, bhunaksha_client=None,
                          location_overrides: dict | None = None) -> dict:
    """
    COMPLETE verification of a farmer's 7/12 — all layers, one call.

        Layer 1  is this document genuine?      -> verify_submitted_document()
        Layer 2  does the borrower own it?      -> match_owner() on the holder rows
        Layer 3  is the land pledgeable & free? -> tenure class + encumbrance
        Layer 4  where is it, and does the map agree?  (locate=True)
                                                -> plot_locator: Bhunaksha polygon +
                                                   lat/long, khata / owner / area on the
                                                   cadastral map. The map's owner names
                                                   are clean Unicode, so a strong map
                                                   match settles a lossy 'confirm' from
                                                   Layer 2 when the khata agrees too.

    Returns a dict with an overall `verdict`:
        verified  — genuine, borrower is a holder, tenure OK, no encumbrance
        confirm   — genuine, but a human must eyeball the owner names (lossy Devanagari)
        review    — could not complete a check (e.g. no verification number on the PDF)
        reject    — failed a hard check: not genuine, wrong owner, or already mortgaged

    `borrower_name_devanagari` is what actually matters: the 7/12 names are in Marathi,
    so a Latin-script KYC name cannot be compared against them directly. Pass the
    Devanagari spelling from the borrower's Aadhaar. Latin is kept for the audit only.
    """
    if hasattr(file, "read"):
        data = file.read()
    elif isinstance(file, (bytes, bytearray)):
        data = bytes(file)
    else:
        with open(file, "rb") as fh:
            data = fh.read()
    auth = verify_submitted_document(data, doc=doc)
    out = {"authenticity": auth, "verdict": "reject", "checks": [],
           "borrower_name_devanagari": borrower_name_devanagari,
           "borrower_name_latin": borrower_name_latin}
    add = lambda **k: out["checks"].append(k)

    add(check="document_authentic",
        status="pass" if auth["authentic"] else ("warn" if auth["verdict"] == "review"
                                                 else "fail"),
        detail=auth["reason"])
    if not auth["authentic"]:
        out["verdict"] = auth["verdict"]          # reject, or review if no number found
        return out

    rec = MahaBhumiProvider(doc=doc).fetch(verification_no=auth["verification_no"])
    out["official_record"] = {k: v for k, v in rec.items() if k != "raw"}

    # ── Layer 2: is the borrower one of the holders? ──
    if not borrower_name_devanagari:
        add(check="owner_is_borrower", status="warn",
            detail="no Devanagari borrower name supplied — cannot check ownership")
    else:
        om = match_owner(rec.get("owner_rows") or [], borrower_name_devanagari)
        out["owner_match"] = om
        add(check="owner_is_borrower", status=om["status"],
            detail=(f"best holder match {om['score']}% "
                    f"({(om['best'] or {}).get('name', 'no holder rows')})"
                    + ("" if om["status"] != "confirm" else
                       " — operator must compare the names on screen")))

    # ── Layer 3: pledgeable, and not already mortgaged ──
    tc = rec.get("tenure_class")
    add(check="tenure_pledgeable",
        status="pass" if (tc and _is_pledgeable_tenure(tc)) else
               ("warn" if not tc else "fail"),
        detail=tc or "tenure class not readable — confirm manually")

    enc = rec.get("encumbrance") or ""
    clear = _norm(enc) in {"nil", "na", "none", ""}
    add(check="no_existing_loan",
        status="pass" if clear else ("fail" if require_clear_encumbrance else "warn"),
        detail="clear" if clear else enc)

    # ── Layer 4: locate the plot on the cadastral map and cross-check it ──
    if locate:
        _add_location_layer(out, parse_full_record(_pdf_text(data)),
                            borrower_name_devanagari, client=bhunaksha_client,
                            overrides=location_overrides)

    _finalise_verdict(out)
    return out


def _finalise_verdict(out: dict) -> None:
    """Worst status wins; 'info' checks are shown but never count against."""
    order = ["info", "pass", "warn", "confirm", "fail"]
    worst = max((c["status"] for c in out["checks"] if c["status"] in order),
                key=order.index, default="warn")
    out["verdict"] = {"info": "verified", "pass": "verified", "warn": "review",
                      "confirm": "confirm", "fail": "reject"}[worst]
    out["failed_checks"] = [c["check"] for c in out["checks"] if c["status"] == "fail"]


def _add_location_layer(out: dict, rec: dict, borrower_name_devanagari: str, *,
                        client=None, overrides: dict | None = None) -> None:
    """Run plot_locator on a parsed 7/12 or 8A and fold its checks into `out`."""
    try:
        import plot_locator
        best = (out.get("owner_match") or {}).get("best") or {}
        # only trust the paper's khata when the paper's owner match was at least 'confirm'
        khata = best.get("khata") if out.get("owner_match", {}).get("status") != "fail" else None
        loc = plot_locator.locate_from_record(
            rec, borrower_name_devanagari=borrower_name_devanagari, khata_no=khata,
            client=client, overrides=overrides)
    except Exception as e:
        # the map is corroboration, not the title check -- an outage must not block
        out["location"] = {"error": str(e)}
        out["checks"].append({"check": "map_location", "status": "info",
                              "detail": f"Bhunaksha lookup failed ({e}) — location not checked"})
        return
    out["location"] = loc
    for p in loc["plots"]:
        for c in p["checks"]:
            out["checks"].append({**c, "check": "map_" + c["check"]
                                  if not c["check"].startswith("map_") else c["check"],
                                  "survey_no": p["survey_no"]})

    # A 7/12's lossy Devanagari leaves a genuine owner in the 'confirm' band. If the
    # cadastral map -- clean Unicode -- names the borrower on the SAME khata, that
    # settles it.
    st = {c["check"]: c["status"] for c in out["checks"]}
    if (st.get("owner_is_borrower") == "confirm" and st.get("map_owner_on_map") == "pass"
            and st.get("map_khata_on_map") == "pass"):
        for c in out["checks"]:
            if c["check"] == "owner_is_borrower":
                c["status"] = "pass"
                c["detail"] = (c["detail"].split(" — operator must")[0]
                               + " — confirmed: the Bhunaksha map (clean text) names the "
                                 "borrower on the same khata")


def verify_8a_holding(file, *, borrower_name_devanagari: str = "",
                      borrower_name_latin: str = "", locate: bool = True,
                      bhunaksha_client=None, location_overrides: dict | None = None) -> dict:
    """
    Verify a farmer's 8A (खाते उतारा — every plot one khata holds in a village).

        1. authenticity  : its own 16-digit number -> MahaBhumi Verify8A -> diff the facts
        2. parse         : khata no., holder, every survey no. with its area
        3. locate        : each survey no. on Bhunaksha -> polygon + lat/long, and check
                           the khata / owner / area on the map

    The 8A is what tells you the borrower's TOTAL holding in the village — use it for
    the production-capacity check (deposited quantity vs. area x district yield).
    """
    import plot_locator
    if hasattr(file, "read"):
        data = file.read()
    elif isinstance(file, (bytes, bytearray)):
        data = bytes(file)
    else:
        with open(file, "rb") as fh:
            data = fh.read()
    text = _pdf_text(data)
    rec = plot_locator.parse_8a(text)
    out = {"doc": "8A", "record": rec, "checks": [], "verdict": "reject",
           "borrower_name_devanagari": borrower_name_devanagari,
           "borrower_name_latin": borrower_name_latin}
    add = lambda **k: out["checks"].append(k)

    if not plot_locator.is_8a(text):
        add(check="is_8a", status="warn",
            detail="this does not look like an 8A — is it a 7/12? use the 7/12 tab")
    if not rec["plots"]:
        add(check="plots_parsed", status="warn",
            detail="no survey rows could be read off this 8A — enter them manually")

    auth = verify_submitted_document(data, doc="8A")
    out["authenticity"] = auth
    add(check="document_authentic",
        status="pass" if auth["authentic"] else ("warn" if auth["verdict"] == "review"
                                                 else "fail"),
        detail=auth["reason"])

    if borrower_name_devanagari and rec.get("holder_names_lossy"):
        om = match_owner([{"name": n, "khata": rec.get("khata_no"), "area_hectare": None}
                          for n in rec["holder_names_lossy"]], borrower_name_devanagari)
        out["owner_match"] = om
        add(check="owner_is_borrower", status=om["status"],
            detail=f"8A holder '{(om['best'] or {}).get('name', '—')}' vs borrower: "
                   f"{om['score']}% (8A text is lossy)")

    if locate and rec["plots"]:
        _add_location_layer(out, rec, borrower_name_devanagari,
                            client=bhunaksha_client, overrides=location_overrides)
    _finalise_verdict(out)
    return out


_PROVIDERS = {"mock": MockProvider, "surepass": SurepassProvider,
              "digilocker": DigiLockerProvider, "mahabhumi": MahaBhumiProvider}


def get_provider(name: str = "mock", **kw) -> LandRecordProvider:
    cls = _PROVIDERS.get(name, MockProvider)
    return cls(**kw) if name != "mock" else MockProvider()


# ─────────────────────────────────────────────────────────────── match ──
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _name_score(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz
        return round(fuzz.token_sort_ratio(a or "", b or ""), 1)
    except Exception:
        return 100.0 if _norm(a) == _norm(b) and a else 0.0


@dataclass
class MatchResult:
    checks: list = field(default_factory=list)     # [{field, uploaded, official, status, note}]
    verdict: str = "review"                         # "verified" | "review" | "reject"
    hard_flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "hard_flags": self.hard_flags, "checks": self.checks}


def match(uploaded: dict, official: dict, borrower_kyc_name: str = "",
          *, area_tol_pct: float = 5.0, name_min: float = 82.0,
          stale_months: int = 6) -> MatchResult:
    res = MatchResult()
    add = lambda **k: res.checks.append(k)

    # ── owner name: official ↔ KYC, and official ↔ upload if the upload had a name
    off_names = official.get("owner_names") or []
    best_kyc = max((_name_score(borrower_kyc_name, n) for n in off_names), default=0.0)
    add(field="owner_vs_kyc", uploaded=borrower_kyc_name or "—",
        official="; ".join(off_names) or "—",
        status="pass" if best_kyc >= name_min else "fail",
        note=f"name match {best_kyc:.0f}% (min {name_min:.0f})")
    if best_kyc < name_min:
        res.hard_flags.append("OWNER_NAME_MISMATCH")

    up_owner = uploaded.get("owner_name")
    if up_owner:
        best_up = max((_name_score(up_owner, n) for n in off_names), default=0.0)
        add(field="owner_vs_upload", uploaded=up_owner,
            official="; ".join(off_names) or "—",
            status="pass" if best_up >= name_min else "warn",
            note=f"paper vs record {best_up:.0f}%")

    # ── survey / village / district consistency (upload keys are the lookup keys)
    for k, label in (("district", "district"), ("taluka", "taluka"),
                     ("village", "village"), ("survey_no", "survey no.")):
        uv = uploaded.get(k)
        if uv:
            add(field=label, uploaded=uv, official=uv, status="pass",
                note="used as the lookup key")

    # ── area
    ua, oa = _to_float(uploaded.get("area_hectare")), _to_float(official.get("area_hectare"))
    if ua and oa:
        diff = abs(ua - oa) / oa * 100
        add(field="area_hectare", uploaded=ua, official=oa,
            status="pass" if diff <= area_tol_pct else "fail",
            note=f"differs {diff:.1f}% (tol {area_tol_pct:.0f}%)")
        if diff > area_tol_pct:
            res.hard_flags.append("AREA_MISMATCH")
    elif oa:
        add(field="area_hectare", uploaded=ua or "—", official=oa, status="warn",
            note="not readable on upload — confirm manually")

    # ── tenure class: must be pledgeable
    tc = official.get("tenure_class")
    if tc is not None:
        ok = _is_pledgeable_tenure(tc)
        add(field="tenure_class", uploaded=uploaded.get("tenure_class") or "—",
            official=tc, status="pass" if ok else "fail",
            note="pledgeable" if ok else "NOT pledgeable as collateral")
        if not ok:
            res.hard_flags.append("TENURE_NOT_PLEDGEABLE")

    # ── existing loan / encumbrance -> the double-pledge check
    enc = (official.get("encumbrance") or "").strip()
    clean = _norm(enc) in {"nil", "na", "none", ""} or "no encumbrance" in enc.lower()
    add(field="encumbrance", uploaded=uploaded.get("encumbrance") or "—",
        official=enc or "—", status="pass" if clean else "fail",
        note="clear" if clean else "land already carries a loan/charge")
    if not clean:
        res.hard_flags.append("EXISTING_ENCUMBRANCE")

    # ── crop: only a soft flag, and only if the record has a Kharif-season crop
    oc, uc = official.get("crop"), uploaded.get("crop")
    if oc and uc and _norm(oc) != _norm(uc):
        add(field="crop", uploaded=uc, official=oc, status="warn",
            note="record crop differs — check it's the same season")

    # ── staleness of the record
    as_of = official.get("as_of") or ""
    try:
        months = (datetime.now() - datetime.fromisoformat(as_of)).days / 30.4
        add(field="record_as_of", uploaded="—", official=as_of,
            status="pass" if months <= stale_months else "warn",
            note=f"{months:.0f} months old (fresh ≤ {stale_months})")
    except ValueError:
        pass

    # ── verdict
    if res.hard_flags:
        res.verdict = "reject" if any(
            f in res.hard_flags for f in ("OWNER_NAME_MISMATCH", "EXISTING_ENCUMBRANCE",
                                          "TENURE_NOT_PLEDGEABLE")) else "review"
    elif any(c["status"] in ("fail", "warn") for c in res.checks):
        res.verdict = "review"
    else:
        res.verdict = "verified"
    return res


# ─────────────────────────────────────────────────────────────── audit ──
def save_audit(*, extract: ExtractResult, lookup_keys: dict, official: dict,
               match_result: MatchResult, borrower_kyc_name: str = "",
               provider: str = "mock") -> str:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    run_id = "LR-" + hashlib.sha256(
        (json.dumps(lookup_keys, sort_keys=True) + datetime.now().isoformat()).encode()
    ).hexdigest()[:14].upper()
    bundle = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "state": "maharashtra", "provider": provider,
        "borrower_kyc_name": borrower_kyc_name,
        "extract": {"method": extract.method, "fields": extract.fields,
                    "needs_manual": extract.needs_manual},
        "lookup_keys": lookup_keys,
        "official_record": {k: v for k, v in official.items() if k != "raw"},
        "match": match_result.to_dict(),
    }
    path = os.path.join(AUDIT_DIR, run_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)
    return path


# ─────────────────────────────────────────────────────────────── cli ──
# ──────────────────────────────────────────────────────── streamlit UI ──
def inject_css() -> None:
    import streamlit as st
    st.markdown("""<style>
      .lr-card{background:#0f172a;border:1px solid #1e293b;border-radius:10px;
               padding:.8rem 1rem;margin:.4rem 0;}
      .lr-pass{color:#10b981;font-weight:600;} .lr-fail{color:#ef4444;font-weight:600;}
      .lr-warn{color:#f59e0b;font-weight:600;} .lr-confirm{color:#38bdf8;font-weight:600;}
      .lr-info{color:#94a3b8;font-weight:600;}
      .lr-name{font-size:1.15rem;letter-spacing:.5px;}
    </style>""", unsafe_allow_html=True)


_STATUS_CLS = {"pass": "lr-pass", "fail": "lr-fail", "warn": "lr-warn",
               "confirm": "lr-confirm", "info": "lr-info"}
_VERDICT_UI = {"verified": ("✅ VERIFIED", "#10b981"), "confirm": ("🔎 CONFIRM NAMES", "#38bdf8"),
               "review": ("⚠️ NEEDS REVIEW", "#f59e0b"), "reject": ("⛔ REJECT", "#ef4444")}


def render() -> None:
    """
    Land-verification page, two tabs:
      * 7/12 — title check against MahaBhumi + where the plot is
      * 8A   — every plot the borrower's khata holds, each located on the map

    On success the results are stored in st.session_state['verified_land'] /
    ['verified_8a'] so the risk engine / receipt intake can reuse them (including the
    plot's lat/long for rainfall and warehouse-distance checks).
    """
    import streamlit as st
    t1, t2 = st.tabs(["📜 7/12 — title & plot location", "📒 8A — all plots of the khata"])
    with t1:
        _render_712()
    with t2:
        _render_8a()


def _run_cached(key: str, sig: tuple, fn):
    """Keep a verification result across Streamlit reruns until its inputs change."""
    import streamlit as st
    hit = st.session_state.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    res = fn()
    st.session_state[key] = (sig, res)
    return res


def _render_checks(checks: list) -> None:
    import streamlit as st
    for c in checks:
        cls = _STATUS_CLS.get(c["status"], "")
        st.markdown(
            f"<div class='lr-card'><span class='{cls}'>{c['status'].upper():8s}</span> "
            f"&nbsp; <b>{c['check'].replace('_', ' ')}</b>"
            + (f" <span style='color:#64748b;'>· survey {c['survey_no']}</span>"
               if c.get("survey_no") else "")
            + f"<br><span style='color:#94a3b8;font-size:.85rem;'>{c['detail']}</span></div>",
            unsafe_allow_html=True)


def _render_location(res: dict, *, key: str) -> dict | None:
    """
    Map + details for res['location'] (from plot_locator). Lets the operator correct the
    village if the lossy name on the paper was matched with low confidence.
    Returns a compact summary for session_state, or None.
    """
    import streamlit as st
    loc = res.get("location")
    st.markdown("#### 📍 Where is this land? (Bhunaksha cadastral map)")
    if not loc:
        st.caption("Location was not checked.")
        return None
    if loc.get("error"):
        st.warning(f"Could not reach Bhunaksha: {loc['error']}. The title checks above "
                   "still stand; retry the location later.")
        return None
    import plot_locator
    where = loc["location"]
    conf = "✅" if where.get("confident") else "⚠️ low confidence —"
    st.markdown(
        f"{conf} matched to **{where['village_name']}**, taluka **{where['taluka_name']}**, "
        f"district **{where['district_name']}** "
        f"<span style='color:#64748b;'>(village match {where['scores']['village']:.0f}% "
        f"by {where['village_matched_by']})</span>", unsafe_allow_html=True)

    vils = loc.get("all_villages") or []
    with st.expander("Wrong village? Pick the right one",
                     expanded=not where.get("confident")):
        names = [f"{v['name']}  ({v['code']})" for v in vils]
        cur = next((i for i, v in enumerate(vils) if v["code"] == where["village_code"]), 0)
        pick = st.selectbox("Village on Bhunaksha", names, index=cur, key=f"{key}_vil")
        if names and pick != names[cur] and st.button("Re-locate with this village",
                                                      key=f"{key}_reloc"):
            v = vils[names.index(pick)]
            st.session_state[f"{key}_overrides"] = {
                "district_code": where["district_code"], "taluka_code": where["taluka_code"],
                "village_code": v["code"]}
            st.session_state.pop(f"{key}_result", None)
            st.rerun()

    plots = loc.get("plots") or []
    found = [p for p in plots if p["location"].get("found")]
    if found:
        plot_locator.render_map(found)
    for p in plots:
        L = p["location"]
        if not L.get("found"):
            st.warning(f"Survey {p['survey_no']}: {L.get('reason')}")
            continue
        c = L["centroid"]
        a, b, cc, d = st.columns(4)
        a.metric(f"Survey {p['survey_no']}", f"{c['lat']:.5f}, {c['lon']:.5f}")
        b.metric("Surveyed polygon", f"{L['map_area_ha']:.2f} ha" if L["map_area_ha"] else "—",
                 help=f"Whole survey no. {L['mapped_plotno']} as drawn on the village map")
        row = p.get("row") or {}
        cc.metric("Borrower's share (map)",
                  f"{row['area_total_ha']:.2f} ha" if row else "—",
                  help="Area of the borrower's khata row on this survey, per Bhunaksha")
        d.markdown(f"[Open in Google Maps]({L['google_maps_url']})  \n"
                   f"[Bhunaksha map report]({L['map_report_url']})")
        with st.expander(f"Holders of survey {L['mapped_plotno']} on the map "
                         "(clean text — compare with the Aadhaar name)"):
            st.dataframe([{"survey/sub": h["survey_no"], "khata": h["khata"],
                           "owner": h["owner"], "area ha": h["area_total_ha"],
                           "pot-kharab ha": h["pot_kharab_ha"],
                           "borrower's row": "◀" if row and h["khata"] == row.get("khata")
                           and h["survey_no"] == row.get("survey_no") else ""}
                          for h in L["holders"]], use_container_width=True)
    st.caption("Bhunaksha maps are published for viewing only and are not a legal record "
               "on their own, so they corroborate the 7/12 and do not replace it.")
    if not found:
        return None
    return {"centroid": loc.get("centroid"), "khata_no": loc.get("khata_no"),
            "giscode": where["giscode"], "village": where["village_name"],
            "taluka": where["taluka_name"], "district": where["district_name"],
            "plots": [{"survey_no": p["survey_no"], "centroid": p["location"]["centroid"],
                       "polygon_lonlat": p["location"]["polygon_lonlat"],
                       "map_area_ha": p["location"]["map_area_ha"]} for p in found],
            "verdict": loc.get("verdict")}


def _render_8a() -> None:
    import streamlit as st
    import plot_locator
    st.markdown("""
    <div class='lr-card' style='border-left:4px solid #38bdf8;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Upload the farmer's <b>8A (खाते उतारा)</b>: the list of <i>every</i> plot his khata
        holds in the village. We verify it against MahaBhumi with its own verification
        number, then locate each survey number on the Bhunaksha map and check that the
        khata, owner and area on the map agree.
      </span>
    </div>""", unsafe_allow_html=True)
    up = st.file_uploader("8A extract (PDF)", type=["pdf"], key="lr8_pdf")
    c1, c2 = st.columns([2, 1])
    name_dev = c1.text_input("Borrower name in Marathi / Devanagari", key="lr8_name_dev",
                             placeholder="रमेश तात्याराव बिडवे")
    name_lat = c2.text_input("Name in English (for the audit)", key="lr8_name_lat")
    if not up:
        st.info("Upload a digitally-issued 8A to begin.")
        return
    if st.button("🔍 Verify 8A and locate its plots", type="primary", key="lr8_go"):
        st.session_state["lr8_armed"] = up.file_id
    if st.session_state.get("lr8_armed") != up.file_id:
        return
    data = up.getvalue()
    ov = st.session_state.get("lr8_overrides")
    sig = (up.file_id, name_dev, str(ov))
    with st.spinner("Checking with MahaBhumi and Bhunaksha …"):
        try:
            res = _run_cached("lr8_result", sig, lambda: verify_8a_holding(
                data, borrower_name_devanagari=name_dev, borrower_name_latin=name_lat,
                location_overrides=ov))
        except Exception as e:
            st.error(f"Verification could not complete: {e}")
            return

    label, colour = _VERDICT_UI.get(res["verdict"], ("?", "#94a3b8"))
    st.markdown(f"<div class='lr-card' style='border-left:5px solid {colour};'>"
                f"<span style='font-size:1.3rem;color:{colour};font-weight:700;'>{label}</span>"
                "</div>", unsafe_allow_html=True)
    rec = res["record"]
    a, b, cc, d = st.columns(4)
    a.metric("Khata no.", rec.get("khata_no") or "—")
    b.metric("Plots on 8A", len(rec.get("plots") or []))
    tot = (rec.get("totals") or {}).get("area_total_ha") or sum(
        p["area_total_ha"] for p in rec.get("plots") or [])
    cc.metric("Total holding", f"{tot:.2f} ha")
    d.metric("Year", rec.get("year") or "—")
    st.dataframe([{"survey no.": p["survey_no"], "cultivable ha": p["area_cultivable_ha"],
                   "pot-kharab ha": p["pot_kharab_ha"], "total ha": p["area_total_ha"],
                   "assessment": p["assessment"]} for p in rec.get("plots") or []],
                 use_container_width=True)
    st.markdown("#### Checks")
    _render_checks(res["checks"])
    summary = _render_location(res, key="lr8")
    st.session_state["verified_8a"] = {
        "verdict": res["verdict"], "khata_no": rec.get("khata_no"),
        "verification_no": rec.get("verification_no"), "plots": rec.get("plots"),
        "total_holding_ha": tot, "plot_location": summary,
        "authentic": (res.get("authenticity") or {}).get("authentic"),
        "borrower_name_devanagari": name_dev, "borrower_name_latin": name_lat,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _render_712() -> None:
    import streamlit as st

    st.markdown("""
    <div class='lr-card' style='border-left:4px solid #10b981;'>
      <span style='font-size:.85rem;color:#94a3b8;'>
        Upload a farmer's <b>7/12 (सातबारा)</b>. Every extract downloaded from
        <code>digitalsatbara.mahabhumi.gov.in</code> carries a 16-digit verification number —
        we send that number back to the government portal, fetch <b>their</b> copy of the same
        document, and compare it field by field. <b>Free, no login, no API key.</b>
      </span>
    </div>""", unsafe_allow_html=True)

    up = st.file_uploader("7/12 extract (PDF)", type=["pdf"], key="lr_pdf")
    c1, c2 = st.columns([2, 1])
    name_dev = c1.text_input(
        "Borrower name in Marathi / Devanagari", key="lr_name_dev",
        placeholder="रमेश तातेराव बिडवे",
        help="A 7/12 lists names in Marathi. A Latin-script name cannot be compared "
             "against them — copy the Devanagari spelling from the borrower's Aadhaar.")
    name_lat = c2.text_input("Name in English (for the audit)", key="lr_name_lat",
                             placeholder="Ramesh Taterao Bidve")
    allow_enc = st.checkbox(
        "Allow land that already carries a loan (for testing only)", value=False,
        key="lr_allow_enc",
        help="Leave OFF in production — land already mortgaged elsewhere should be rejected.")

    if not up:
        st.info("Upload a digitally-issued 7/12 to begin.")
        return
    if st.button("🔍 Verify against the Maharashtra government", type="primary"):
        st.session_state["lr_armed"] = up.file_id
    if st.session_state.get("lr_armed") != up.file_id:
        return

    data = up.getvalue()
    ov = st.session_state.get("lr_overrides")
    sig = (up.file_id, name_dev, allow_enc, str(ov))
    with st.spinner("Reading the document, querying digitalsatbara.mahabhumi.gov.in "
                    "and locating the plot on Bhunaksha …"):
        try:
            res = _run_cached("lr_result", sig, lambda: verify_land_ownership(
                data, borrower_name_devanagari=name_dev, borrower_name_latin=name_lat,
                require_clear_encumbrance=not allow_enc, location_overrides=ov))
        except Exception as e:
            st.error(f"Verification could not complete: {e}")
            return

    label, colour = _VERDICT_UI.get(res["verdict"], ("?", "#94a3b8"))
    st.markdown(
        f"<div class='lr-card' style='border-left:5px solid {colour};'>"
        f"<span style='font-size:1.3rem;color:{colour};font-weight:700;'>{label}</span>"
        + (f"<br><span style='color:#94a3b8;font-size:.85rem;'>failed: "
           f"{', '.join(res['failed_checks'])}</span>" if res.get("failed_checks") else "")
        + "</div>", unsafe_allow_html=True)

    st.markdown("#### Checks")
    _render_checks(res["checks"])

    plot_summary = _render_location(res, key="lr")

    auth = res.get("authenticity", {})
    if auth.get("comparison"):
        with st.expander("Document vs the government's copy — fact by fact"):
            st.caption("Compares extracted facts, not file hashes: the portal regenerates "
                       "the PDF on every request, so the timestamp and hash always differ.")
            st.dataframe([{"fact": c["fact"], "status": c["status"],
                           "on the paper": ", ".join(c["submitted"])[:60],
                           "government copy": ", ".join(c["official"])[:60]}
                          for c in auth["comparison"]["checks"]], use_container_width=True)

    rec = res.get("official_record") or {}
    if rec.get("owner_rows"):
        st.markdown("#### Holders on the government record")
        st.caption("The PDF's font drops ~21% of the Marathi characters, so names read back "
                   "shortened. Compare them against the borrower's ID by eye before approving.")
        om = res.get("owner_match") or {}
        for r in rec["owner_rows"]:
            is_best = om.get("best") and r["name"] == om["best"]["name"]
            st.markdown(
                f"<div class='lr-card'"
                f"{' style=\"border-left:4px solid #38bdf8;\"' if is_best else ''}>"
                f"<span class='lr-name'>{r['name']}</span>"
                f"<span style='color:#64748b;'> &nbsp; khata {r['khata']} &nbsp;·&nbsp; "
                f"{r['area_raw']} ha</span>"
                + (f"<br><span class='lr-confirm'>best match for the borrower — "
                   f"{om.get('score')}%</span>" if is_best else "")
                + "</div>", unsafe_allow_html=True)
        if name_dev:
            st.markdown(f"Borrower's stated name: &nbsp; <span class='lr-name'>{name_dev}</span>",
                        unsafe_allow_html=True)

    a, b, c3 = st.columns(3)
    a.metric("Total area", f"{rec.get('area_hectare') or 0:.2f} ha")
    b.metric("Tenure", (rec.get("tenure_class") or "—").replace(" (Class I)", ""))
    c3.metric("Verification no.", rec.get("verification_no") or "—")
    if rec.get("encumbrance"):
        st.warning(f"**Existing charge on this land:** {rec['encumbrance']}")

    # ── everything else the government record carries ──
    full = None
    try:
        full = _run_cached("lr_full", (rec.get("verification_no"),),
                           lambda: full_record_from_verification_no(rec.get("verification_no")))
    except Exception as e:
        st.caption(f"(could not load the full field dump: {e})")
    if full:
        st.markdown("#### Full government record")
        i1, i2 = st.columns(2)
        with i1:
            st.markdown("**Identity**")
            st.write({"village": full["village"], "village_code": full["village_code"],
                      "taluka": full["taluka"], "district": full["district"],
                      "survey_no": full["survey_no"], "ULPIN / PU-ID": full["ulpin"],
                      "tenure": full["tenure_class"]})
            st.markdown("**Area breakdown (ha)**")
            st.write({"rain-fed (जिरायत)": full["area_cultivable_rainfed"],
                      "irrigated (बागायत)": full["area_cultivable_irrigated"],
                      "uncultivable (पोटखराब)": full["area_uncultivable"],
                      "TOTAL": full["area_total"],
                      "assessment (आकारणी)": full["assessment"]})
        with i2:
            st.markdown("**Mutation history**")
            st.write({"last mutation": full["mutation_last_no"],
                      "on": full["mutation_last_date"],
                      "pending": full["mutation_pending"],
                      "older": ", ".join(full["mutation_old"] or []) or "—"})
            st.markdown("**Other rights**")
            st.write({"easements": ", ".join(full["easements"] or []) or "none",
                      "wells/borewells": len(full["wells"] or [])})
            st.markdown("**Provenance**")
            st.write({"digitally signed": full["digitally_signed_on"],
                      "this copy downloaded": full["downloaded_on"]})

        if full["encumbrances"]:
            st.markdown("**Charges registered on this land (बोजा)**")
            st.dataframe([{"amount": (f"Rs.{e['amount']:,}" if e["amount"] else "—"),
                           "mutation entry": e["entry_no"] or "—",
                           "date": e["date"] or "—",
                           "entry text": e["line"]} for e in full["encumbrances"]],
                         use_container_width=True)
        else:
            st.success("No charge registered on this land (बोजा: NIL).")

        st.markdown("**Crops (Form 12 — पिकांची नोंदी)**")
        if full["crops"]:
            st.dataframe(full["crops"], use_container_width=True)
        else:
            st.caption("Form 12 carries no crop entries for this plot. This is common on "
                       "Maharashtra records and does not mean the document is faulty.")

        with st.expander("Field reference — what each item on a 7/12 means"):
            st.dataframe([{"field": k, "meaning": v}
                          for k, v in LAND_RECORD_FIELDS.items()],
                         use_container_width=True)
        with st.expander("Plain-text dump (copyable)"):
            st.code(print_full_record(full, file=io.StringIO()) or "", language="text")

    st.session_state["verified_land"] = {
        "verdict": res["verdict"], "verification_no": rec.get("verification_no"),
        "owner_rows": rec.get("owner_rows"), "owner_match": res.get("owner_match"),
        "area_hectare": rec.get("area_hectare"), "tenure_class": rec.get("tenure_class"),
        "encumbrance": rec.get("encumbrance"), "authentic": auth.get("authentic"),
        "borrower_name_devanagari": name_dev, "borrower_name_latin": name_lat,
        "plot_location": plot_summary,       # lat/long + polygon for rainfall / distance
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if st.session_state.get("lr_audited") == sig:      # one audit file per verification,
        return                                          # not one per Streamlit rerun
    st.session_state["lr_audited"] = sig
    os.makedirs(AUDIT_DIR, exist_ok=True)
    run_id = "LV-" + hashlib.sha256(
        (str(rec.get("verification_no")) + datetime.now().isoformat()).encode()
    ).hexdigest()[:14].upper()
    path = os.path.join(AUDIT_DIR, run_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"run_id": run_id, **res}, fh, indent=2, ensure_ascii=False, default=str)
    st.caption(f"Audit written to `verification_runs/{run_id}.json` — "
               f"stored in session as `verified_land`.")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Verify a Maharashtra 7/12 against the government (digitalsatbara).")
    ap.add_argument("file", nargs="?",
                    help="the farmer's 7/12 PDF — the real flow")
    ap.add_argument("--verification-no", "-v",
                    help="16-digit number, if you don't have the PDF to hand")
    ap.add_argument("--name", "-n", default="",
                    help="borrower's name in DEVANAGARI, e.g. रमेश तातेराव बिडवे")
    ap.add_argument("--name-latin", default="")
    ap.add_argument("--allow-encumbrance", action="store_true",
                    help="don't reject land that already carries a loan")
    ap.add_argument("--no-locate", action="store_true",
                    help="skip the Bhunaksha plot-location / map cross-check")
    ap.add_argument("--dump", action="store_true",
                    help="print every field on the government record")
    ap.add_argument("--provider", default="mahabhumi",
                    choices=["mahabhumi", "mock"],
                    help="mahabhumi = the real government check (default). "
                         "mock = INVENTED data, for offline UI testing only.")
    a = ap.parse_args()

    if a.provider == "mock":
        print("!" * 74)
        print("!!  PROVIDER = MOCK — every field below is INVENTED from a hash of the")
        print("!!  lookup keys. Names, areas, tenure and encumbrance are NOT REAL and")
        print("!!  must never be shown to an operator or used for a lending decision.")
        print("!!  Drop --provider mock to run the real government check.")
        print("!" * 74)
        keys = {"district": "Latur", "taluka": "Ausa",
                "village": "Kharola", "survey_no": "142/2"}
        official = MockProvider().fetch(**keys)
        up = dict(keys, area_hectare=1.42, crop="Soyabean")
        mr = match(up, official, borrower_kyc_name=a.name_latin or "Ramesh Bhagwan Patil")
        print(f"\n[FAKE] record (source={official['source']}):")
        for k, v in official.items():
            if k != "raw":
                print(f"  {k:16s}: {v}")
        print(f"\n[FAKE] VERDICT: {mr.verdict.upper()}  "
              f"flags: {mr.hard_flags or 'none'}")
        raise SystemExit(0)

    # ── the real check ────────────────────────────────────────────────
    if not a.file and not a.verification_no:
        ap.error("give a 7/12 PDF, or --verification-no. "
                 "(Use --provider mock only for offline UI testing.)")

    if a.verification_no and not a.file:
        rec = full_record_from_verification_no(a.verification_no)
        print_full_record(rec)
        raise SystemExit(0)

    ext = extract_from_upload(a.file)
    print(f"read {os.path.basename(a.file)} via {ext.method}")
    import plot_locator
    if plot_locator.is_8a(ext.raw_text):
        print("  document     : 8A (holding extract)")
        res = verify_8a_holding(a.file, borrower_name_devanagari=a.name,
                                borrower_name_latin=a.name_latin, locate=not a.no_locate)
        r8 = res["record"]
        print(f"  khata {r8['khata_no']}  plots: "
              + ", ".join(f"{p['survey_no']} ({p['area_total_ha']} ha)" for p in r8["plots"]))
    else:
        print(f"  fields       : {ext.fields}")
        print(f"  needs manual : {ext.needs_manual}")
        res = verify_land_ownership(a.file, borrower_name_devanagari=a.name,
                                    borrower_name_latin=a.name_latin,
                                    require_clear_encumbrance=not a.allow_encumbrance,
                                    locate=not a.no_locate)
    print(f"\nVERDICT: {res['verdict'].upper()}"
          f"   failed: {res.get('failed_checks') or 'none'}")
    print(f"{'check':22s} {'status':9s} detail")
    print("-" * 78)
    for c in res["checks"]:
        print(f"{c['check']:22s} {c['status']:9s} {c['detail'][:46]}")

    loc = res.get("location") or {}
    for p in loc.get("plots") or []:
        L = p["location"]
        if L.get("found"):
            print(f"\nPLOT {p['survey_no']}: {L['centroid']['lat']:.6f}, "
                  f"{L['centroid']['lon']:.6f}   {L['google_maps_url']}")
            print(f"  {L['location_label']}")
            print(f"  map report: {L['map_report_url']}")

    if not a.name:
        print("\nnote: no --name given, so ownership was NOT checked. A 7/12 lists "
              "names in Marathi —\n      pass the Devanagari spelling from the "
              "borrower's Aadhaar.")

    if a.dump and res.get("official_record", {}).get("verification_no"):
        print()
        print_full_record(
            full_record_from_verification_no(res["official_record"]["verification_no"]))
