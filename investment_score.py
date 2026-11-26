"""
investment_score.py — the investor-facing headline.

Re-presents the 6-pillar risk_engine output + the price forecast as two
legible sub-scores and one blended number, higher = better bet:

    INVESTMENT SCORE
      ├─ BORROWER SCORE   30%   can & will they repay / cooperate
      └─ COLLATERAL SCORE 70%   will the soybean hold its value over the tenure
            └─ PRICE OUTLOOK SCORE   ← the ML forecast lives here

No new model. Pure function. Feed it:
    risk_result       dict from risk_engine.compute_composite_risk_score()
    forecast          dict from market.get_forecast()
    context           dict from market.get_market_context()
    current_price     float (market.get_current_price()['price'])
    msp               int   (market.get_msp())
    receipt           dict  {qty_qtl, deposit_price}
    requested_amount  float (the loan the borrower is asking for)
    verified_borrower dict | None  (from the borrower-verification flow)
"""

from __future__ import annotations

_GRADES = [(75, "A", "10-12%"), (60, "B", "13-15%"), (45, "C", "16-18%"), (0, "D", "19%+")]


def _grade(score: float):
    for cut, g, rate in _GRADES:
        if score >= cut:
            return g, rate
    return "D", "19%+"


def _price_outlook(forecast: dict, context: dict, msp: float) -> tuple[float, list[str]]:
    s, notes = 60.0, []
    d = float(forecast.get("delta_pct") or 0.0)
    if d <= -12:
        s -= 45; notes.append(f"Forecast {d:+.1f}% over the tenure — sharp downside")
    elif d <= -5:
        s -= 30; notes.append(f"Forecast {d:+.1f}% over the tenure — falling")
    elif d <= 0:
        s -= 10; notes.append(f"Forecast {d:+.1f}% over the tenure — soft")
    elif d <= 5:
        s += 10; notes.append(f"Forecast {d:+.1f}% over the tenure — mildly up")
    else:
        s += 25; notes.append(f"Forecast {d:+.1f}% over the tenure — rising")

    min_fc = float(forecast.get("min_forecast") or 0.0)
    if min_fc:
        if min_fc >= msp * 1.02:
            s += 10; notes.append("Forecast low stays above MSP")
        elif min_fc >= msp * 0.98:
            notes.append("Forecast low sits near MSP")
        else:
            s += 8; notes.append("Forecast dips below MSP — procurement floor caps the loss")

    regime = str(context.get("regime") or "medium").lower()
    s += {"low": 8, "medium": 0, "high": -12}.get(regime, 0)
    if regime != "medium":
        notes.append(f"Price volatility regime: {regime}")

    var = context.get("var_pct")
    if var is not None:
        var = float(var)
        s += -20 if var > 20 else -10 if var > 10 else 0 if var >= 5 else 7
        if var > 10:
            notes.append(f"90-day VaR {var:.0f}% — meaningful drawdown risk")

    return max(0.0, min(100.0, round(s, 1))), notes


# collateral pillars and their renormalised weights (borrower excluded)
_COLL_W = {"price_level": 0.20, "volatility": 0.25, "warehouse": 0.15,
           "ltv_quality": 0.15, "crop_yield": 0.15}


def compute_investment_score(*, risk_result: dict, forecast: dict, context: dict,
                             current_price: float, msp: float, receipt: dict,
                             requested_amount: float,
                             verified_borrower: dict | None = None) -> dict:
    pillars = (risk_result or {}).get("pillar_scores", {}) or {}

    def pscore(key, default=50.0):
        p = pillars.get(key)
        return float(p.get("score", default)) if isinstance(p, dict) else default

    # ── borrower score (higher = safer) ──
    if verified_borrower and verified_borrower.get("pillar_enhanced") is not None:
        borrower_risk = float(verified_borrower["pillar_enhanced"])
        borrower_basis = "verified"
    else:
        borrower_risk = pscore("borrower")
        borrower_basis = "profile estimate"
    borrower_score = round(100.0 - borrower_risk, 1)
    hard_flag = bool((verified_borrower or {}).get("bureau_write_off")
                     or (verified_borrower or {}).get("bureau_suit_filed"))
    if hard_flag:
        borrower_score = min(borrower_score, 25.0)

    # ── collateral score (higher = safer) ──
    wsum = sum(_COLL_W.values())
    coll_risk = sum(pscore(k) * w for k, w in _COLL_W.items()) / wsum
    coll_safety = 100.0 - coll_risk
    price_outlook_score, po_notes = _price_outlook(forecast, context, msp)
    collateral_score = round(0.75 * coll_safety + 0.25 * price_outlook_score, 1)

    # ── LTV now vs stressed ──
    qty = float(receipt.get("qty_qtl") or receipt.get("quantity") or 0) or 1.0
    loan = float(requested_amount or 0) or 1.0
    shrink = 0.985
    sh = ((risk_result or {}).get("shrinkage_estimate") or {})
    if isinstance(sh, dict) and sh.get("effective_value_factor"):
        shrink = float(sh["effective_value_factor"])
    min_fc = float(forecast.get("min_forecast") or current_price)
    val_now = qty * float(current_price) * shrink
    val_stress = qty * min_fc * shrink
    ltv_now = round(loan / val_now, 4) if val_now else None
    ltv_stress = round(loan / val_stress, 4) if val_stress else None
    buf_now = round((val_now - loan) / loan, 4) if loan else None
    buf_stress = round((val_stress - loan) / loan, 4) if loan else None

    wdra_ok = bool(str(receipt.get("wdra_no") or receipt.get("wdra_registration_number") or "").strip())

    # ── blend ──
    investment_score = round(0.30 * borrower_score + 0.70 * collateral_score, 1)
    if hard_flag:
        investment_score = min(investment_score, 35.0)
    if not wdra_ok:
        investment_score = min(investment_score, 60.0)
    investment_score = max(0.0, min(100.0, investment_score))
    grade, rate = _grade(investment_score)

    # ── drivers (ranked-ish, most decision-relevant first) ──
    drivers = []
    for n in po_notes:
        sign = "-" if any(w in n.lower() for w in ("fall", "soft", "downside", "below", "drawdown", "high")) else "+"
        drivers.append({"text": n, "sign": sign})
    if verified_borrower:
        foir = verified_borrower.get("foir")
        if foir is not None:
            drivers.append({"text": f"Borrower FOIR {float(foir):.2f}",
                            "sign": "+" if float(foir) < 0.45 else "-"})
        bs = verified_borrower.get("bureau_score")
        if bs:
            drivers.append({"text": f"Bureau score {bs}", "sign": "+" if bs >= 700 else "-"})
        if hard_flag:
            drivers.append({"text": "Prior write-off / suit-filed on bureau", "sign": "-"})
    else:
        drivers.append({"text": "Borrower not yet verified — pillar is an estimate", "sign": "-"})
    drivers.append({"text": "WDRA-registered warehouse", "sign": "+"} if wdra_ok
                   else {"text": "Warehouse not WDRA-registered", "sign": "-"})
    if ltv_now is not None:
        drivers.append({"text": f"LTV {ltv_now:.0%} now → {ltv_stress:.0%} if the forecast low hits",
                        "sign": "+" if ltv_stress and ltv_stress < 0.8 else "-"})

    return {
        "investment_score": investment_score,
        "investment_grade": grade,
        "suggested_rate": rate,
        "borrower_score": borrower_score,
        "borrower_basis": borrower_basis,
        "collateral_score": collateral_score,
        "price_outlook_score": price_outlook_score,
        "ltv_now": ltv_now, "ltv_stressed": ltv_stress,
        "margin_buffer_now": buf_now, "margin_buffer_stressed": buf_stress,
        "collateral_value_now": round(val_now), "collateral_value_stressed": round(val_stress),
        "drivers": drivers,
        "weights": {"borrower": 0.30, "collateral": 0.70},
    }


if __name__ == "__main__":
    demo_risk = {"pillar_scores": {k: {"score": v} for k, v in {
        "price_level": 38, "volatility": 60, "warehouse": 22, "ltv_quality": 30,
        "crop_yield": 45, "borrower": 35}.items()},
        "shrinkage_estimate": {"effective_value_factor": 0.98}}
    demo_fc = {"delta_pct": -6.2, "min_forecast": 6010, "current_anchor": 6398}
    demo_ctx = {"regime": "high", "var_pct": 17.5}
    out = compute_investment_score(
        risk_result=demo_risk, forecast=demo_fc, context=demo_ctx,
        current_price=6398, msp=5328, receipt={"qty_qtl": 250, "wdra_no": "MP-ENWR-4501"},
        requested_amount=850000,
        verified_borrower={"pillar_enhanced": 33, "foir": 0.32, "bureau_score": 742,
                           "bureau_write_off": False, "bureau_suit_filed": False})
    for k, v in out.items():
        print(f"{k:24} {v}")
