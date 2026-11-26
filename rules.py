"""
rules.py — hard-rule engine + decision recommendation.

Traan §7: "The system needs a rule-check engine alongside any weighted scoring.
Rules are necessary because some failures should be explicit regardless of
average score."  Fraud overrides credit; no score offsets a failed collateral rule.

Adapted for smallholder-farmer, warehouse-receipt-secured lending in India —
so the collateral rules are the hard ones, and there are no business-KYB /
financial-statement / tax rules.

Every threshold is a named constant in THRESHOLDS so an admin screen can
tune them later without touching logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import market

# ── tunable thresholds (Traan §14 phase 4: admin controls) ──────────────
THRESHOLDS = {
    "ltv_max": 0.80,                 # LTV at today's mandi price
    "ltv_stressed_max": 0.85,        # LTV if the forecast low is realised
    "margin_buffer_min": 0.15,       # stressed cushion over the loan
    "moisture_max_pct": 12.0,        # safe long-term storage
    "foreign_matter_max_pct": 3.0,
    "foir_max": 0.55,                # obligations ÷ income
    "bounce_max_6m": 3,
    "bureau_min": 650,
    "storage_days_max": 240,         # days in store + tenure
    "deposit_price_tolerance": 0.15, # deposit price vs mandi price that day
    "insurance_min_coverage": 0.80,  # sum insured ÷ collateral value
    "approve_score_min": 70.0,       # investment score for straight approve
    "conditional_score_min": 55.0,
    # When True, a hard rule with no evidence yet ("na") blocks to manual review
    # instead of becoming a pre-funding condition. Turn on once document
    # forensics and the bureau feed are live in production.
    "require_full_evidence": False,
}

CATEGORIES = ["Collateral", "Borrower", "Document integrity", "Market"]


def _r(code, name, cat, status, detail, *, hard=False, value=None, threshold=None):
    return {"code": code, "name": name, "category": cat, "status": status,
            "detail": detail, "hard": hard, "value": value, "threshold": threshold}


def _price_on(date_str) -> float | None:
    """Mandi price on (or nearest to) a date — used for deposit-price sanity."""
    if not date_str:
        return None
    try:
        d = pd.Timestamp(str(date_str)[:10])
    except Exception:
        return None
    hist = market.get_price_history()
    if hist is None or len(hist) == 0:
        return None
    win = hist[(hist["date"] >= d - pd.Timedelta(days=12)) &
               (hist["date"] <= d + pd.Timedelta(days=12))]
    if len(win) == 0:
        return None
    idx = (win["date"] - d).abs().idxmin()
    return float(hist.loc[idx, "price"])


def evaluate(*, listing: dict, borrower_profile: dict | None,
             risk_result: dict | None, investment: dict | None,
             fraud: dict | None = None) -> dict:
    """Run every rule and return {decision, score, rules, hard_fails, conditions, ...}."""
    T = THRESHOLDS
    rules: list[dict] = []
    q = listing.get("quality") or {}
    ver = (borrower_profile or {}).get("verification") or {}
    inv = investment or {}
    cp = market.get_current_price()
    cur_price, msp = cp["price"], market.get_msp()

    # ══ Collateral (hard) ═══════════════════════════════════════════════
    enwr = str(listing.get("wdra_no") or "").strip()
    rules.append(_r("ENWR_PRESENT", "WDRA e-NWR on the receipt", "Collateral",
                    "pass" if enwr else "fail", hard=True,
                    detail=f"e-NWR {enwr}" if enwr else
                           "No WDRA e-NWR number — the receipt cannot be verified or lien-marked"))

    ltv = inv.get("ltv_now")
    if ltv is None:
        rules.append(_r("LTV_CEILING", "LTV within ceiling", "Collateral", "na",
                        "LTV not computed", hard=True))
    else:
        rules.append(_r("LTV_CEILING", "LTV within ceiling", "Collateral",
                        "pass" if ltv <= T["ltv_max"] else "fail", hard=True,
                        detail=f"LTV {ltv:.0%} vs ceiling {T['ltv_max']:.0%}",
                        value=ltv, threshold=T["ltv_max"]))

    ltv_s = inv.get("ltv_stressed")
    if ltv_s is None:
        rules.append(_r("LTV_STRESSED", "LTV holds if the price forecast falls", "Collateral",
                        "na", "Stressed LTV not computed", hard=True))
    else:
        rules.append(_r("LTV_STRESSED", "LTV holds if the price forecast falls", "Collateral",
                        "pass" if ltv_s <= T["ltv_stressed_max"] else "fail", hard=True,
                        detail=f"Stressed LTV {ltv_s:.0%} vs ceiling {T['ltv_stressed_max']:.0%} "
                               f"(at the 90-day forecast low)",
                        value=ltv_s, threshold=T["ltv_stressed_max"]))

    # deposit price vs the real mandi price that day — inflated-collateral fraud check
    dep_p = listing.get("deposit_price")
    ref = _price_on(listing.get("deposit_date"))
    if dep_p and ref:
        dev = (float(dep_p) - ref) / ref
        ok = abs(dev) <= T["deposit_price_tolerance"]
        rules.append(_r("DEPOSIT_PRICE_SANITY", "Deposit price matches the mandi price that day",
                        "Collateral", "pass" if ok else "fail", hard=True,
                        detail=f"Stated ₹{dep_p:,.0f}/qtl vs mandi ₹{ref:,.0f}/qtl on "
                               f"{str(listing.get('deposit_date'))[:10]} ({dev:+.0%})",
                        value=round(dev, 4), threshold=T["deposit_price_tolerance"]))
    else:
        rules.append(_r("DEPOSIT_PRICE_SANITY", "Deposit price matches the mandi price that day",
                        "Collateral", "na", "No mandi reference price for that date", hard=True))

    # ══ Document integrity (hard) ═══════════════════════════════════════
    fr_level = str((fraud or {}).get("risk_level") or "not run").lower()
    if fraud is None:
        rules.append(_r("FRAUD_NOT_HIGH", "Documents show no tampering", "Document integrity",
                        "na", "Document forensics not run on this package — "
                              "required before disbursement", hard=True))
    else:
        rules.append(_r("FRAUD_NOT_HIGH", "Documents show no tampering", "Document integrity",
                        "fail" if fr_level == "high" else "pass", hard=True,
                        detail=f"Document risk level: {fr_level}"))

    # ══ Borrower (hard) ═════════════════════════════════════════════════
    kyc = (borrower_profile or {}).get("kyc_status", "pending")
    rules.append(_r("KYC_VERIFIED", "Borrower KYC verified", "Borrower",
                    "pass" if kyc == "verified" else "fail", hard=True,
                    detail=f"KYC status: {kyc}"))

    wo = bool(ver.get("bureau_write_off") or ver.get("bureau_suit_filed"))
    if not ver:
        rules.append(_r("NO_WRITE_OFF", "No write-off or suit-filed on the bureau", "Borrower",
                        "na", "No bureau report on file — pull before disbursement", hard=True))
    else:
        rules.append(_r("NO_WRITE_OFF", "No write-off or suit-filed on the bureau", "Borrower",
                        "fail" if wo else "pass", hard=True,
                        detail="Prior write-off / suit-filed found" if wo else "Clean bureau record"))

    # ══ Collateral (soft) ═══════════════════════════════════════════════
    buf = inv.get("margin_buffer_stressed")
    if buf is not None:
        rules.append(_r("MARGIN_BUFFER", "Margin cushion survives a price fall", "Collateral",
                        "pass" if buf >= T["margin_buffer_min"] else "fail",
                        detail=f"Stressed buffer {buf:.0%} vs minimum {T['margin_buffer_min']:.0%}",
                        value=buf, threshold=T["margin_buffer_min"]))

    moist = q.get("moisture")
    if moist is not None:
        rules.append(_r("MOISTURE_SAFE", "Moisture within safe-storage limit", "Collateral",
                        "pass" if float(moist) <= T["moisture_max_pct"] else "fail",
                        detail=f"Moisture {moist}% vs limit {T['moisture_max_pct']}% "
                               f"(above this, shrinkage and mould risk climb)",
                        value=float(moist), threshold=T["moisture_max_pct"]))
    else:
        rules.append(_r("MOISTURE_SAFE", "Moisture within safe-storage limit", "Collateral",
                        "na", "No lab assay on file"))

    fm = q.get("fm")
    if fm is not None:
        rules.append(_r("FOREIGN_MATTER", "Foreign matter within spec", "Collateral",
                        "pass" if float(fm) <= T["foreign_matter_max_pct"] else "fail",
                        detail=f"Foreign matter {fm}% vs limit {T['foreign_matter_max_pct']}%",
                        value=float(fm), threshold=T["foreign_matter_max_pct"]))

    ins = str(q.get("insurance") or "none").lower()
    rules.append(_r("INSURANCE_VALID", "Stock is insured", "Collateral",
                    "pass" if ins == "full" else "warn" if ins == "partial" else "fail",
                    detail=f"Insurance cover: {ins}"))

    sd = int(q.get("storage_days") or 0) + int(listing.get("tenure_days") or 90)
    rules.append(_r("STORAGE_WINDOW", "Total storage stays inside the safe window", "Collateral",
                    "pass" if sd <= T["storage_days_max"] else "fail",
                    detail=f"{sd} days in store by maturity vs limit {T['storage_days_max']}",
                    value=sd, threshold=T["storage_days_max"]))

    grade = str(listing.get("grade") or "").upper()
    rules.append(_r("GRADE_OK", "Grade acceptable as collateral", "Collateral",
                    "pass" if grade in ("A", "B") else "warn",
                    detail=f"Grade {grade or '—'}"))

    # ══ Borrower (soft) ═════════════════════════════════════════════════
    foir = ver.get("foir")
    if foir is not None:
        rules.append(_r("FOIR_OK", "Debt burden within limit", "Borrower",
                        "pass" if float(foir) <= T["foir_max"] else "fail",
                        detail=f"FOIR {float(foir):.2f} vs limit {T['foir_max']:.2f}",
                        value=float(foir), threshold=T["foir_max"]))
    else:
        rules.append(_r("FOIR_OK", "Debt burden within limit", "Borrower", "na",
                        "No verified bank-statement analysis"))

    bs = ver.get("bureau_score")
    if bs:
        rules.append(_r("BUREAU_SCORE", "Bureau score above floor", "Borrower",
                        "pass" if int(bs) >= T["bureau_min"] else "fail",
                        detail=f"Bureau score {bs} vs floor {T['bureau_min']}",
                        value=int(bs), threshold=T["bureau_min"]))
    else:
        rules.append(_r("BUREAU_SCORE", "Bureau score above floor", "Borrower", "na",
                        "Thin file — no bureau history (common for first-time farm borrowers)"))

    bc = ver.get("bounce_count")
    if bc is not None:
        rules.append(_r("BOUNCES_OK", "Banking conduct clean", "Borrower",
                        "pass" if int(bc) <= T["bounce_max_6m"] else "fail",
                        detail=f"{bc} returns/bounces vs limit {T['bounce_max_6m']}",
                        value=int(bc), threshold=T["bounce_max_6m"]))

    # ══ Market (soft) ═══════════════════════════════════════════════════
    rules.append(_r("PRICE_ABOVE_MSP", "Mandi price above MSP", "Market",
                    "pass" if cur_price > msp else "warn",
                    detail=f"₹{cur_price:,.0f}/qtl vs MSP ₹{msp:,.0f} — "
                           f"{'headroom above the procurement floor' if cur_price > msp else 'sitting on the floor'}",
                    value=cur_price, threshold=msp))

    ctx = market.get_market_context(int(listing.get("tenure_days") or 90))
    regime = str(ctx.get("regime") or "medium")
    rules.append(_r("VOL_REGIME", "Price volatility not extreme", "Market",
                    "pass" if regime != "high" else "warn",
                    detail=f"Volatility regime: {regime}"
                           + (f" · VaR {ctx['var_pct']:.0f}%" if ctx.get("var_pct") else "")))

    # ══ Decision ════════════════════════════════════════════════════════
    # A rule that FAILED is not the same as a rule we could not CHECK.
    #   fail → the evidence contradicts the rule            → blocks
    #   warn → evidence present but marginal                → noted
    #   na   → no evidence yet (module not run / thin file) → becomes a condition
    hard_fails = [r for r in rules if r["hard"] and r["status"] == "fail"]
    hard_warns = [r for r in rules if r["hard"] and r["status"] == "warn"]
    hard_unchecked = [r for r in rules if r["hard"] and r["status"] == "na"]
    soft_fails = [r for r in rules if not r["hard"] and r["status"] == "fail"]
    soft_unchecked = [r for r in rules if not r["hard"] and r["status"] == "na"]
    score = float(inv.get("investment_score") or 0)

    fraud_failed = any(r["code"] == "FRAUD_NOT_HIGH" for r in hard_fails)
    if fraud_failed:
        decision, why = "fraud_hold", "Document tampering detected — held for the fraud team."
    elif hard_fails:
        decision = "reject"
        why = ("Failed " + str(len(hard_fails)) + " hard rule(s): "
               + ", ".join(r["name"] for r in hard_fails) + ".")
    elif hard_warns:
        decision = "manual_review"
        why = ("Evidence is marginal on: " + ", ".join(r["name"] for r in hard_warns)
               + " — a credit officer must decide.")
    elif hard_unchecked and T.get("require_full_evidence"):
        decision = "manual_review"
        why = ("Full evidence required by policy but missing for: "
               + ", ".join(r["name"] for r in hard_unchecked) + ".")
    elif score >= T["approve_score_min"] and not soft_fails and not hard_unchecked:
        decision, why = "approve", f"Every rule passes and the investment score is {score:.0f}/100."
    elif score >= T["conditional_score_min"]:
        n = len(soft_fails) + len(hard_unchecked)
        decision = "conditional"
        why = (f"Investment score {score:.0f}/100. No rule failed, but {n} item(s) "
               f"must be cleared before disbursement." if n else
               f"Investment score {score:.0f}/100 — approve with standard conditions.")
    else:
        decision, why = "manual_review", f"Investment score {score:.0f}/100 is below the auto-decision band."

    conditions = ([f"{r['name']}: {r['detail']}" for r in soft_fails]
                  + [f"{r['name']} — not yet verified: {r['detail']}"
                     for r in hard_unchecked + soft_unchecked])
    positives = [r["detail"] for r in rules if r["status"] == "pass"][:5]
    negatives = [r["detail"] for r in rules if r["status"] in ("fail", "warn")][:5]

    return {
        "decision": decision, "rationale": why, "score": score,
        "rules": rules,
        "counts": {"pass": sum(r["status"] == "pass" for r in rules),
                   "fail": sum(r["status"] == "fail" for r in rules),
                   "warn": sum(r["status"] == "warn" for r in rules),
                   "na": sum(r["status"] == "na" for r in rules),
                   "total": len(rules)},
        "hard_fails": [r["name"] for r in hard_fails],
        "conditions": conditions,
        "positives": positives, "negatives": negatives,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "thresholds": dict(T),
    }


DECISION_META = {
    "approve":        ("Approve", "#10b981", "Meets every rule — list it for investors."),
    "conditional":    ("Conditionally approve", "#84cc16", "List it once the conditions below are cleared."),
    "manual_review":  ("Manual review", "#f59e0b", "A credit officer must look at this."),
    "fraud_hold":     ("Fraud hold", "#a855f7", "Escalated to the fraud team. Not fundable."),
    "reject":         ("Reject", "#ef4444", "Failed a hard rule. Not fundable as submitted."),
}


def narrative(listing: dict, borrower_profile: dict | None, ev: dict, inv: dict | None) -> str:
    """Templated case write-up (Traan §6). Deterministic — not an LLM."""
    inv = inv or {}
    name = (borrower_profile or {}).get("name") or "The borrower"
    district = (borrower_profile or {}).get("district") or "—"
    label = DECISION_META.get(ev["decision"], (ev["decision"],))[0]
    fc_pts = (listing.get("scores") or {}).get("forecast_points") or {}
    fc_delta = (listing.get("scores") or {}).get("forecast_delta_pct")
    lines = [
        f"**{name}** ({district}) has deposited **{listing['qty_qtl']:.0f} quintals** of grade "
        f"{listing.get('grade','—')} soybean at {listing.get('warehouse','—')} and is seeking "
        f"**₹{listing['requested_amount']:,.0f}** for **{listing['tenure_days']} days**.",

        f"Collateral is valued at **₹{inv.get('collateral_value_now', 0):,.0f}** at today's mandi price, "
        f"giving an LTV of **{(inv.get('ltv_now') or 0):.0%}**. If the {listing['tenure_days']}-day price "
        f"forecast{f' ({fc_delta:+.1f}%)' if fc_delta is not None else ''} reaches its low, the LTV moves to "
        f"**{(inv.get('ltv_stressed') or 0):.0%}** with a **{(inv.get('margin_buffer_stressed') or 0):.0%}** "
        f"margin cushion remaining.",

        f"Scoring: borrower **{inv.get('borrower_score','—')}/100** ({inv.get('borrower_basis','—')}), "
        f"collateral **{inv.get('collateral_score','—')}/100**, price outlook "
        f"**{inv.get('price_outlook_score','—')}/100** → investment score "
        f"**{ev['score']:.0f}/100**.",

        f"Rule check: **{ev['counts']['pass']} passed, {ev['counts']['fail']} failed, "
        f"{ev["counts"]["warn"] + ev["counts"]["na"]} unverified** of {ev['counts']['total']}. "
        f"Recommendation: **{label}** — {ev['rationale']}",
    ]
    # conditions are rendered as their own block in the UI — not repeated here
    return "\n\n".join(lines)
