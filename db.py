"""
db.py — SQLite data layer for the SoyFinance P2P platform.

Model: P2P matchmaker · one investor per receipt.
  borrower  → deposits a receipt → a LISTING is created (open)
  investor  → sees the listing + risk/price scores → places a BID
  borrower  → accepts one bid → LISTING matched, a stub LOAN row is created

Pure stdlib (sqlite3 + hashlib). No ORM. Every read returns plain dicts.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime
from hashlib import pbkdf2_hmac
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soyfinance.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL CHECK (role IN ('borrower','investor','admin')),
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    pw_hash     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borrower_profiles (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name              TEXT,
    pan               TEXT,
    aadhaar_ref       TEXT,
    district          TEXT,
    kyc_status        TEXT NOT NULL DEFAULT 'pending',   -- pending | verified | rejected
    verification_json TEXT,
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS investor_profiles (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    entity_type   TEXT,                                   -- individual | nbfc | fund | huf
    pan_or_cin    TEXT,
    kyc_status    TEXT NOT NULL DEFAULT 'pending',
    funding_bank  TEXT,
    mandate_json  TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS receipts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    commodity     TEXT NOT NULL DEFAULT 'Soyabean',
    qty_qtl       REAL NOT NULL,
    grade         TEXT,
    warehouse     TEXT,
    wdra_no       TEXT,
    deposit_date  TEXT,
    deposit_price REAL,
    quality_json  TEXT,
    status        TEXT NOT NULL DEFAULT 'stored',         -- stored | listed | pledged | released
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id        INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    borrower_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_amount  REAL NOT NULL,
    tenure_days       INTEGER NOT NULL DEFAULT 90,
    risk_score        REAL,       -- 0-100, higher = riskier (risk_engine composite)
    risk_grade        TEXT,
    investment_score  REAL,       -- 0-100, higher = better bet (investment_score module)
    investment_grade  TEXT,       -- A/B/C/D
    suggested_rate    TEXT,       -- e.g. "13-15%"
    scores_json       TEXT,       -- full breakdown for the due-diligence view
    status            TEXT NOT NULL DEFAULT 'open',        -- open | matched | closed | cancelled
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bids (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    investor_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount         REAL NOT NULL,
    interest_rate  REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',        -- pending | accepted | rejected | withdrawn
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    bid_id       INTEGER NOT NULL REFERENCES bids(id),
    investor_id  INTEGER NOT NULL REFERENCES users(id),
    borrower_id  INTEGER NOT NULL REFERENCES users(id),
    principal    REAL NOT NULL,
    rate         REAL NOT NULL,
    tenure_days  INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'created',          -- created | disbursed | repaid | defaulted (later phases)
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER,
    user_id     INTEGER,
    action      TEXT NOT NULL,          -- workflow | decision | edit | rerun | bid | accept
    from_state  TEXT,
    to_state    TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_listings_status  ON listings(status);
CREATE INDEX IF NOT EXISTS ix_bids_listing     ON bids(listing_id);
CREATE INDEX IF NOT EXISTS ix_receipts_borrower ON receipts(borrower_id);
CREATE INDEX IF NOT EXISTS ix_audit_listing    ON audit_log(listing_id);
"""

# columns added after the first release — applied by _migrate()
_ADDED_COLUMNS = {
    "listings": [
        ("workflow_state", "TEXT NOT NULL DEFAULT 'draft'"),
        ("decision", "TEXT"),
        ("decision_rationale", "TEXT"),
        ("decision_at", "TEXT"),
        ("rules_json", "TEXT"),
        ("conditions_json", "TEXT"),
        ("narrative", "TEXT"),
    ],
}


def _migrate(conn) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# ── connection ──────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(r) if r is not None else None


def _rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


def _dumps(obj) -> Optional[str]:
    return json.dumps(obj, default=str) if obj is not None else None


def loads(s) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


# ── password hashing (stdlib pbkdf2) ────────────────────────────────────
def hash_password(pw: str, *, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── users ───────────────────────────────────────────────────────────────
def create_user(role: str, email: str, password: str, phone: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (role, email, phone, pw_hash, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (role, email.strip().lower(), phone, hash_password(password), "active", _now()))
        uid = cur.lastrowid
        if role == "borrower":
            conn.execute("INSERT OR IGNORE INTO borrower_profiles (user_id, updated_at) VALUES (?,?)",
                         (uid, _now()))
        elif role == "investor":
            conn.execute("INSERT OR IGNORE INTO investor_profiles (user_id, updated_at) VALUES (?,?)",
                         (uid, _now()))
        return uid


def get_user_by_email(email: str) -> Optional[dict]:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM users WHERE email = ?",
                                 (email.strip().lower(),)).fetchone())


def get_user(uid: int) -> Optional[dict]:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def authenticate(email: str, password: str) -> Optional[dict]:
    u = get_user_by_email(email)
    if u and u["status"] == "active" and verify_password(password, u["pw_hash"]):
        return u
    return None


# ── profiles ────────────────────────────────────────────────────────────
def get_borrower_profile(uid: int) -> Optional[dict]:
    with get_conn() as conn:
        r = _row(conn.execute("SELECT * FROM borrower_profiles WHERE user_id = ?", (uid,)).fetchone())
    if r:
        r["verification"] = loads(r.pop("verification_json", None))
    return r


def get_investor_profile(uid: int) -> Optional[dict]:
    with get_conn() as conn:
        r = _row(conn.execute("SELECT * FROM investor_profiles WHERE user_id = ?", (uid,)).fetchone())
    if r:
        r["mandate"] = loads(r.pop("mandate_json", None))
    return r


def upsert_borrower_profile(uid: int, **f) -> None:
    cols = {"name", "pan", "aadhaar_ref", "district", "kyc_status"}
    sets, vals = [], []
    for k in cols:
        if k in f:
            sets.append(f"{k} = ?"); vals.append(f[k])
    if "verification" in f:
        sets.append("verification_json = ?"); vals.append(_dumps(f["verification"]))
    sets.append("updated_at = ?"); vals.append(_now())
    vals.append(uid)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO borrower_profiles (user_id, updated_at) VALUES (?,?)",
                     (uid, _now()))
        conn.execute(f"UPDATE borrower_profiles SET {', '.join(sets)} WHERE user_id = ?", vals)


def upsert_investor_profile(uid: int, **f) -> None:
    cols = {"entity_type", "pan_or_cin", "kyc_status", "funding_bank"}
    sets, vals = [], []
    for k in cols:
        if k in f:
            sets.append(f"{k} = ?"); vals.append(f[k])
    if "mandate" in f:
        sets.append("mandate_json = ?"); vals.append(_dumps(f["mandate"]))
    sets.append("updated_at = ?"); vals.append(_now())
    vals.append(uid)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO investor_profiles (user_id, updated_at) VALUES (?,?)",
                     (uid, _now()))
        conn.execute(f"UPDATE investor_profiles SET {', '.join(sets)} WHERE user_id = ?", vals)


def kyc_status(uid: int, role: str) -> str:
    p = get_borrower_profile(uid) if role == "borrower" else get_investor_profile(uid)
    return (p or {}).get("kyc_status", "pending")


# ── receipts ────────────────────────────────────────────────────────────
def create_receipt(borrower_id: int, *, qty_qtl: float, grade: str, warehouse: str,
                   wdra_no: str = "", deposit_date: str = "", deposit_price: float = 0.0,
                   quality: dict | None = None, commodity: str = "Soyabean") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO receipts (borrower_id, commodity, qty_qtl, grade, warehouse, wdra_no, "
            "deposit_date, deposit_price, quality_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (borrower_id, commodity, qty_qtl, grade, warehouse, wdra_no, deposit_date,
             deposit_price, _dumps(quality), "stored", _now()))
        return cur.lastrowid


def get_receipt(rid: int) -> Optional[dict]:
    with get_conn() as conn:
        r = _row(conn.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone())
    if r:
        r["quality"] = loads(r.pop("quality_json", None))
    return r


def list_receipts_for_borrower(borrower_id: int) -> list[dict]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM receipts WHERE borrower_id = ? ORDER BY id DESC", (borrower_id,)).fetchall())


# ── listings ────────────────────────────────────────────────────────────
def create_listing(receipt_id: int, borrower_id: int, *, requested_amount: float,
                   tenure_days: int = 90) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO listings (receipt_id, borrower_id, requested_amount, tenure_days, "
            "status, created_at) VALUES (?,?,?,?,?,?)",
            (receipt_id, borrower_id, requested_amount, tenure_days, "open", _now()))
        conn.execute("UPDATE receipts SET status = 'listed' WHERE id = ?", (receipt_id,))
        return cur.lastrowid


def update_listing_scores(listing_id: int, *, risk_score=None, risk_grade=None,
                          investment_score=None, investment_grade=None,
                          suggested_rate=None, scores: dict | None = None) -> None:
    sets, vals = [], []
    for k, v in (("risk_score", risk_score), ("risk_grade", risk_grade),
                 ("investment_score", investment_score), ("investment_grade", investment_grade),
                 ("suggested_rate", suggested_rate)):
        if v is not None:
            sets.append(f"{k} = ?"); vals.append(v)
    if scores is not None:
        sets.append("scores_json = ?"); vals.append(_dumps(scores))
    if not sets:
        return
    vals.append(listing_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE listings SET {', '.join(sets)} WHERE id = ?", vals)


def get_listing(listing_id: int) -> Optional[dict]:
    with get_conn() as conn:
        r = _row(conn.execute(
            "SELECT l.*, r.qty_qtl, r.grade, r.warehouse, r.wdra_no, r.deposit_price, "
            "r.deposit_date, r.quality_json, r.commodity "
            "FROM listings l JOIN receipts r ON r.id = l.receipt_id WHERE l.id = ?",
            (listing_id,)).fetchone())
    if r:
        r["quality"] = loads(r.pop("quality_json", None))
        r["scores"] = loads(r.pop("scores_json", None))
        r["rules"] = loads(r.pop("rules_json", None))
        r["conditions"] = loads(r.pop("conditions_json", None)) or []
    return r


def list_open_listings() -> list[dict]:
    """Listings live on the bid board: marketplace-open AND cleared underwriting."""
    with get_conn() as conn:
        rows = _rows(conn.execute(
            "SELECT l.*, r.qty_qtl, r.grade, r.warehouse, r.wdra_no, r.deposit_price "
            "FROM listings l JOIN receipts r ON r.id = l.receipt_id "
            "WHERE l.status = 'open' AND l.workflow_state = 'listed' "
            "ORDER BY l.created_at DESC").fetchall())
    for r in rows:
        r["scores"] = loads(r.pop("scores_json", None))
        r["conditions"] = loads(r.pop("conditions_json", None)) or []
    return rows


# ── workflow / decision / audit ─────────────────────────────────────────
def set_workflow_state(listing_id: int, state: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE listings SET workflow_state = ? WHERE id = ?", (state, listing_id))


def set_listing_status(listing_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))


def save_decision(listing_id: int, evaluation: dict, narrative_text: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE listings SET decision = ?, decision_rationale = ?, decision_at = ?, "
            "rules_json = ?, conditions_json = ?, narrative = ? WHERE id = ?",
            (evaluation.get("decision"), evaluation.get("rationale"), _now(),
             _dumps(evaluation), _dumps(evaluation.get("conditions") or []),
             narrative_text, listing_id))


def log_action(listing_id: int | None, user_id: int | None, action: str, *,
               from_state: str = "", to_state: str = "", note: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (listing_id, user_id, action, from_state, to_state, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (listing_id, user_id, action, from_state, to_state, note, _now()))


def get_audit(listing_id: int) -> list[dict]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT a.*, u.email AS actor FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
            "WHERE a.listing_id = ? ORDER BY a.id ASC", (listing_id,)).fetchall())


def listings_by_state(states: list[str]) -> list[dict]:
    qs = ",".join("?" * len(states))
    with get_conn() as conn:
        rows = _rows(conn.execute(
            f"SELECT l.*, r.qty_qtl, r.grade, r.warehouse FROM listings l "
            f"JOIN receipts r ON r.id = l.receipt_id WHERE l.workflow_state IN ({qs}) "
            f"ORDER BY l.created_at DESC", states).fetchall())
    for r in rows:
        r["scores"] = loads(r.pop("scores_json", None))
    return rows


def list_listings_for_borrower(borrower_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = _rows(conn.execute(
            "SELECT l.*, r.qty_qtl, r.grade, r.warehouse FROM listings l "
            "JOIN receipts r ON r.id = l.receipt_id WHERE l.borrower_id = ? "
            "ORDER BY l.created_at DESC", (borrower_id,)).fetchall())
    for r in rows:
        r["scores"] = loads(r.pop("scores_json", None))
        r["conditions"] = loads(r.pop("conditions_json", None)) or []
    return rows


# ── bids ────────────────────────────────────────────────────────────────
def create_bid(listing_id: int, investor_id: int, *, amount: float, interest_rate: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bids (listing_id, investor_id, amount, interest_rate, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (listing_id, investor_id, amount, interest_rate, "pending", _now()))
        return cur.lastrowid


def list_bids_for_listing(listing_id: int) -> list[dict]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT b.*, u.email AS investor_email FROM bids b JOIN users u ON u.id = b.investor_id "
            "WHERE b.listing_id = ? ORDER BY b.interest_rate ASC, b.created_at ASC",
            (listing_id,)).fetchall())


def list_bids_for_investor(investor_id: int) -> list[dict]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT b.*, l.requested_amount, l.tenure_days, l.investment_grade, l.status AS listing_status, "
            "r.grade AS receipt_grade, r.qty_qtl "
            "FROM bids b JOIN listings l ON l.id = b.listing_id "
            "JOIN receipts r ON r.id = l.receipt_id "
            "WHERE b.investor_id = ? ORDER BY b.created_at DESC", (investor_id,)).fetchall())


def withdraw_bid(bid_id: int, investor_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE bids SET status = 'withdrawn' WHERE id = ? AND investor_id = ? AND status = 'pending'",
            (bid_id, investor_id))
        return cur.rowcount > 0


def accept_bid(bid_id: int, borrower_id: int) -> Optional[int]:
    """Borrower accepts one bid: that bid -> accepted, siblings -> rejected,
    listing -> matched, receipt -> pledged, a stub loan row is created.
    Returns the new loan id, or None if not permitted."""
    with get_conn() as conn:
        b = conn.execute(
            "SELECT b.*, l.borrower_id AS l_borrower, l.receipt_id, l.tenure_days, l.status AS l_status "
            "FROM bids b JOIN listings l ON l.id = b.listing_id WHERE b.id = ?", (bid_id,)).fetchone()
        if not b or b["l_borrower"] != borrower_id or b["l_status"] != "open" or b["status"] != "pending":
            return None
        conn.execute("UPDATE bids SET status = 'accepted' WHERE id = ?", (bid_id,))
        conn.execute("UPDATE bids SET status = 'rejected' WHERE listing_id = ? AND id != ? AND status = 'pending'",
                     (b["listing_id"], bid_id))
        conn.execute("UPDATE listings SET status = 'matched', workflow_state = 'matched' WHERE id = ?",
                     (b["listing_id"],))
        conn.execute("UPDATE receipts SET status = 'pledged' WHERE id = ?", (b["receipt_id"],))
        conn.execute(
            "INSERT INTO audit_log (listing_id, user_id, action, from_state, to_state, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (b["listing_id"], borrower_id, "accept", "listed", "matched",
             f"Accepted bid #{bid_id} at {b['interest_rate']}% for {b['amount']:.0f}", _now()))
        cur = conn.execute(
            "INSERT INTO loans (listing_id, bid_id, investor_id, borrower_id, principal, rate, "
            "tenure_days, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (b["listing_id"], bid_id, b["investor_id"], borrower_id, b["amount"],
             b["interest_rate"], b["tenure_days"], "created", _now()))
        return cur.lastrowid


# ── misc ────────────────────────────────────────────────────────────────
def counts() -> dict:
    with get_conn() as conn:
        def n(q, *a):
            return conn.execute(q, a).fetchone()[0]
        return {
            "borrowers": n("SELECT COUNT(*) FROM users WHERE role='borrower'"),
            "investors": n("SELECT COUNT(*) FROM users WHERE role='investor'"),
            "receipts": n("SELECT COUNT(*) FROM receipts"),
            "listings": n("SELECT COUNT(*) FROM listings"),
            "open_listings": n("SELECT COUNT(*) FROM listings WHERE workflow_state='listed'"),
            "in_underwriting": n("SELECT COUNT(*) FROM listings WHERE workflow_state IN "
                                 "('draft','underwriting','manual_review','fraud_hold','conditional','approved')"),
            "rejected": n("SELECT COUNT(*) FROM listings WHERE workflow_state='rejected'"),
            "matched_listings": n("SELECT COUNT(*) FROM listings WHERE workflow_state IN ('matched','funded')"),
            "bids": n("SELECT COUNT(*) FROM bids"),
            "loans": n("SELECT COUNT(*) FROM loans"),
        }


if __name__ == "__main__":
    init_db()
    print(f"initialised {DB_PATH}")
    print(counts())
