"""
scoring.py — the underwriting pipeline for one listing.

    score_listing()  risk engine + investment score  → stored on the listing
    underwrite()     the above, then the rule engine, decision, narrative,
                     and the workflow transition that follows from it.
"""

from __future__ import annotations

import db
import market
import rules as rules_mod
import workflow

try:
    from risk_engine import compute_composite_risk_score
    from investment_score import compute_investment_score
    OK = True
except Exception as _e:                       # pragma: no cover
    OK = False
    _ERR = _e


def _tenure_bucket(days: int) -> int:
    return days if days in (60, 90, 120) else 90


def score_listing(listing_id: int, verified_borrower: dict | None = None) -> dict | None:
    if not OK:
        return None
    L = db.get_listing(listing_id)
    if not L:
        return None
    cp = market.get_current_price()
    cur, msp = cp["price"], market.get_msp()
    fc = market.get_forecast(_tenure_bucket(L["tenure_days"]))
    ctx = market.get_market_context(L["tenure_days"])
    q = L.get("quality") or {}

    receipt_data = {
        "deposit_price": L["deposit_price"], "quantity": L["qty_qtl"], "grade": L["grade"],
        "location": (L["warehouse"] or "Latur").split()[-2] if L.get("warehouse") else "Latur",
        "loan_amount": L["requested_amount"],
        "market_value": L["qty_qtl"] * (L["deposit_price"] or cur),
        "wdra_registered": bool(L["wdra_no"]), "wdra_registration_number": L["wdra_no"] or None,
        "insurance_coverage": q.get("insurance", "partial"),
        "has_collateral_manager": True,
        "storage_days": q.get("storage_days", 45),
        "moisture_level": q.get("moisture", 11.0),
        "oil_content_pct": q.get("oil"), "protein_content_pct": q.get("protein"),
        "foreign_matter_pct": q.get("fm"),
    }
    preds = {f"{h}d": v for h, v in (fc.get("points") or {}).items()}
    hist = market.get_price_history()
    price_series = hist.set_index("date")["price"] if len(hist) else None

    rr = compute_composite_risk_score(
        current_price=cur, msp=msp, receipt_data=receipt_data, live_price=cur,
        predicted_prices=preds, price_history=price_series)
    iv = compute_investment_score(
        risk_result=rr, forecast=fc, context=ctx, current_price=cur, msp=msp,
        receipt={"qty_qtl": L["qty_qtl"], "wdra_no": L["wdra_no"], "deposit_price": L["deposit_price"]},
        requested_amount=L["requested_amount"], verified_borrower=verified_borrower)

    db.update_listing_scores(
        listing_id,
        risk_score=rr.get("composite_score"), risk_grade=rr.get("risk_grade"),
        investment_score=iv["investment_score"], investment_grade=iv["investment_grade"],
        suggested_rate=iv["suggested_rate"],
        scores={"risk": rr, "investment": iv,
                "forecast_points": fc.get("points"), "forecast_delta_pct": fc.get("delta_pct"),
                "context": ctx, "current_price": cur, "msp": msp})
    return {"risk": rr, "investment": iv}


def underwrite(listing_id: int, *, user_id: int | None = None,
               auto_list: bool = True, fraud: dict | None = None) -> dict | None:
    """Full pipeline: score → rules → decision → narrative → workflow state.

    auto_list: on 'approve', move straight through to 'listed' so the listing
    appears on the bid board. 'conditional' stops at conditional — the borrower
    must accept the conditions before it lists.
    """
    L0 = db.get_listing(listing_id)
    if not L0:
        return None
    if (L0.get("workflow_state") or "draft") in ("draft", "rejected", "manual_review",
                                                 "fraud_hold", "conditional", "approved"):
        workflow.transition(listing_id, "underwriting", user_id=user_id,
                            note="Underwriting run started", force=True)

    bp = db.get_borrower_profile(L0["borrower_id"]) or {}
    vb = bp.get("verification") or None
    out = score_listing(listing_id, verified_borrower=vb)
    if not out:
        return None

    L = db.get_listing(listing_id)
    ev = rules_mod.evaluate(listing=L, borrower_profile=bp,
                            risk_result=out["risk"], investment=out["investment"], fraud=fraud)
    text = rules_mod.narrative(L, bp, ev, out["investment"])
    db.save_decision(listing_id, ev, text)
    db.log_action(listing_id, user_id, "decision", note=f"{ev['decision']}: {ev['rationale']}")

    target = workflow.DECISION_STATE.get(ev["decision"], "manual_review")
    workflow.transition(listing_id, target, user_id=user_id, note=ev["rationale"], force=True)
    if auto_list and ev["decision"] == "approve":
        workflow.transition(listing_id, "listed", user_id=user_id, note="Auto-listed on approval")

    return {**out, "evaluation": ev, "narrative": text,
            "workflow_state": db.get_listing(listing_id).get("workflow_state")}
