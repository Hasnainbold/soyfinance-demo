"""
seed.py — populate soyfinance.db with a demo marketplace so the UI has
something to render on day one.

    python seed.py           # seed only if the DB is empty
    python seed.py --reset   # wipe and reseed

Every demo user's password is  demo1234  (printed at the end).
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta

import db
import market
import scoring

PW = "demo1234"
RNG = random.Random(20260831)

BORROWERS = [
    ("Ramesh Patidar", "Latur", "Origo Latur WH", True),
    ("Suresh Chouhan", "Indore", "NCML Indore WH", True),
    ("Vijay Deshmukh", "Washim", "StarAgri Washim", True),
    ("Anil Yadav", "Ujjain", "SLCM Ujjain", False),
    ("Gopal Patil", "Nagpur", "Origo Nagpur", True),
    ("Hari Jadhav", "Akola", "NBHC Akola", True),
    ("Mukesh Rathod", "Dewas", "NCML Dewas", False),
    ("Shivaji Pawar", "Nanded", "StarAgri Nanded", True),
]
INVESTORS = [
    ("Anand Rathi Capital", "nbfc"),
    ("Prakash Family Office", "huf"),
    ("Sunrise Agri Fund", "fund"),
    ("Neha Investments", "individual"),
    ("Kotak Agri NBFC", "nbfc"),
    ("Damani Holdings", "individual"),
]


def _reset():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)



def main():
    if "--reset" in sys.argv:
        _reset()
    db.init_db()
    if db.counts()["borrowers"] and "--reset" not in sys.argv:
        print("DB already seeded — use --reset to wipe. Counts:", db.counts())
        return

    cp = market.get_current_price()
    cur_price, msp = cp["price"], market.get_msp()
    print(f"current price ₹{cur_price} ({cp['source']}) · MSP ₹{msp} · training forecast…")
    fc = market.get_forecast(90)
    ctx = market.get_market_context(90)
    print(f"forecast +{fc['delta_pct']}% over 90d · regime {ctx['regime']}")

    borrower_ids = []
    for i, (name, district, wh, verified) in enumerate(BORROWERS):
        uid = db.create_user("borrower", f"{name.split()[0].lower()}{i}@demo.in", PW,
                             f"9{RNG.randint(100000000, 999999999)}")
        db.upsert_borrower_profile(
            uid, name=name, district=district,
            pan=f"{name[:3].upper()}P{RNG.randint(1000,9999)}{RNG.choice('ABCDEFGH')}",
            kyc_status="verified" if verified else "pending",
            verification={"foir": round(RNG.uniform(0.2, 0.6), 2),
                          "bureau_score": RNG.choice([None, 610, 665, 720, 748, 772]),
                          "run_id": f"VR-SEED{i:02d}"} if verified else None)
        borrower_ids.append((uid, district, wh))

    investor_ids = []
    for j, (ename, etype) in enumerate(INVESTORS):
        uid = db.create_user("investor", f"{ename.split()[0].lower()}{j}@demo.in", PW)
        db.upsert_investor_profile(uid, entity_type=etype,
                                   pan_or_cin=f"CIN{RNG.randint(10000,99999)}" if etype != "individual"
                                   else f"INV{RNG.randint(1000,9999)}X",
                                   kyc_status="verified",
                                   funding_bank=RNG.choice(["HDFC", "ICICI", "Axis", "Kotak"]))
        investor_ids.append(uid)

    listing_ids = []
    for k in range(12):
        uid, district, wh = RNG.choice(borrower_ids)
        qty = RNG.choice([80, 120, 150, 200, 250, 320, 400])
        grade = RNG.choices(["A", "B", "C"], weights=[5, 3, 2])[0]
        dep_price = round(cur_price * RNG.uniform(0.92, 1.02))
        dep_date = (datetime.now() - timedelta(days=RNG.randint(5, 70))).date().isoformat()
        wdra = f"{district[:2].upper()}-ENWR-{RNG.randint(10000,99999)}" if RNG.random() > 0.2 else ""
        rid = db.create_receipt(
            uid, qty_qtl=qty, grade=grade, warehouse=wh, wdra_no=wdra,
            deposit_date=dep_date, deposit_price=dep_price,
            quality={"moisture": round(RNG.uniform(9.0, 13.5), 1),
                     "oil": round(RNG.uniform(17, 20.5), 1),
                     "protein": round(RNG.uniform(33, 39), 1),
                     "fm": round(RNG.uniform(0.4, 3.0), 1),
                     "insurance": RNG.choice(["full", "partial", "partial", "none"]),
                     "storage_days": (datetime.now().date() - datetime.fromisoformat(dep_date).date()).days})
        mkt_val = qty * dep_price
        req = round(mkt_val * RNG.uniform(0.62, 0.78) / 1000) * 1000
        lid = db.create_listing(rid, uid, requested_amount=req,
                                tenure_days=RNG.choice([60, 90, 90, 120]))
        res = scoring.underwrite(lid)
        # demo borrowers accept their conditions, so those listings go live too
        if res and res["evaluation"]["decision"] == "conditional":
            import workflow
            workflow.transition(lid, "listed", user_id=uid,
                                note="Borrower accepted the pre-funding conditions")
        listing_ids.append(lid)

    for lid in listing_ids:
        L = db.get_listing(lid)
        if L.get("workflow_state") != "listed":
            continue                      # only live listings collect bids
        for _ in range(RNG.randint(0, 4)):
            inv = RNG.choice(investor_ids)
            db.create_bid(lid, inv,
                          amount=round(L["requested_amount"] * RNG.uniform(0.9, 1.0) / 1000) * 1000,
                          interest_rate=round(RNG.uniform(11.5, 18.5), 2))

    print("\nseeded:", db.counts())
    print("\n─ demo logins (password: demo1234) ─")
    with db.get_conn() as c:
        for r in c.execute("SELECT role, email FROM users ORDER BY role, id").fetchall():
            print(f"  {r['role']:9} {r['email']}")


if __name__ == "__main__":
    main()
