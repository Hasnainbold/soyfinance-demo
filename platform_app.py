"""
platform_app.py — SoyFinance P2P marketplace.

Two logins (borrower / investor) → role-gated portal.
  Borrower : verify profile · deposit soybean → listing · review & accept bids
  Investor : browse listings with risk + price-forecast scores · place bids · portfolio
Shared    : Market (soybean price + 90-day forecast + volatility context)

Run:  streamlit run platform_app.py
Demo logins (password: demo1234) are printed by  python seed.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import db
import market
import rules as rules_mod
import scoring
import workflow

try:
    import borrower_verification_demo as bverify
    BVERIFY = True
except Exception:
    BVERIFY = False

st.set_page_config(page_title="SoyFinance — Marketplace", page_icon="🌱", layout="wide")
db.init_db()

st.markdown("""
<style>
  .stApp { background:#0b1120; color:#e2e8f0; }
  h1,h2,h3 { color:#f8fafc !important; }
  .card { background:#111a2e; border:1px solid #1e293b; border-radius:12px; padding:16px 18px; margin:8px 0; }
  .g-A{color:#10b981}.g-B{color:#84cc16}.g-C{color:#f59e0b}.g-D{color:#ef4444}
  .badge{ display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;font-size:.8rem;
          border:1px solid currentColor; }
  .muted{ color:#94a3b8;font-size:.85rem; }
  .kv{ color:#94a3b8;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em; }
  [data-testid="stMetricValue"]{ color:#10b981; }
  .pos{color:#34d399}.neg{color:#f87171}
</style>
""", unsafe_allow_html=True)


# ── helpers ─────────────────────────────────────────────────────────────
def inr(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "₹—"
    s = f"{int(round(n)):,}"
    return f"₹{s}"


def gbadge(grade: str) -> str:
    g = (grade or "?")[0].upper()
    return f"<span class='badge g-{g}'>{g}</span>"


def score_bar(label, value, hint=""):
    v = 0 if value is None else float(value)
    st.markdown(f"<div class='kv'>{label}</div>", unsafe_allow_html=True)
    st.progress(min(1.0, max(0.0, v / 100)))
    st.markdown(f"<div class='muted'>{v:.0f}/100 {hint}</div>", unsafe_allow_html=True)


_RULE_ICON = {"pass": "✓", "fail": "✕", "warn": "!", "na": "–"}
_RULE_COLOUR = {"pass": "#10b981", "fail": "#ef4444", "warn": "#f59e0b", "na": "#64748b"}


def state_chip(state: str) -> str:
    return (f"<span class='badge' style='color:{workflow.colour(state)}'>"
            f"{workflow.label(state)}</span>")


def decision_chip(decision: str) -> str:
    label, colour, _ = rules_mod.DECISION_META.get(
        decision or "", (decision or "—", "#64748b", ""))
    return f"<span class='badge' style='color:{colour}'>{label}</span>"


def render_rule_checklist(ev: dict, key: str = ""):
    """Traan §5.1 'Credit rule analysis' — the pass/fail checklist."""
    if not ev or not ev.get("rules"):
        st.caption("No rule run recorded for this case.")
        return
    c = ev["counts"]
    st.markdown(
        f"<span class='muted'>Rule check: "
        f"<b style='color:#10b981'>{c['pass']} passed</b> · "
        f"<b style='color:#ef4444'>{c['fail']} failed</b> · "
        f"<b style='color:#f59e0b'>{c.get('warn',0)} marginal</b> · "
        f"<b style='color:#64748b'>{c.get('na',0)} not yet verified</b> "
        f"of {c['total']}</span>", unsafe_allow_html=True)
    only_issues = st.checkbox("Show only failures and gaps", value=False, key=f"ri{key}")
    for cat in rules_mod.CATEGORIES:
        items = [r for r in ev["rules"] if r["category"] == cat]
        if only_issues:
            items = [r for r in items if r["status"] != "pass"]
        if not items:
            continue
        st.markdown(f"<div class='kv' style='margin-top:8px'>{cat}</div>", unsafe_allow_html=True)
        for r in items:
            col = _RULE_COLOUR[r["status"]]
            hard = " <span class='muted'>· hard rule</span>" if r["hard"] else ""
            st.markdown(
                f"<div style='margin:2px 0'><span style='color:{col};font-weight:700'>"
                f"{_RULE_ICON[r['status']]}</span> <b>{r['name']}</b>{hard}<br>"
                f"<span class='muted' style='margin-left:18px'>{r['detail']}</span></div>",
                unsafe_allow_html=True)


def render_case_decision(L: dict, *, key: str = "", show_narrative: bool = True):
    ev = L.get("rules") or {}
    st.markdown(
        f"{decision_chip(L.get('decision'))} &nbsp; {state_chip(L.get('workflow_state') or 'draft')}"
        f" &nbsp; <span class='muted'>{L.get('decision_rationale') or ''}</span>",
        unsafe_allow_html=True)
    if show_narrative and L.get("narrative"):
        st.markdown(f"<div class='card'>{L['narrative']}</div>", unsafe_allow_html=True)
    if L.get("conditions"):
        st.markdown("**Conditions to clear before disbursement**")
        for c in L["conditions"]:
            st.markdown(f"<div class='muted'>• {c}</div>", unsafe_allow_html=True)
    render_rule_checklist(ev, key=key)


def render_audit(listing_id: int):
    rows = db.get_audit(listing_id)
    if not rows:
        st.caption("No audit entries.")
        return
    st.dataframe(pd.DataFrame([{
        "When": r["created_at"], "Action": r["action"],
        "From": workflow.label(r["from_state"]) if r["from_state"] else "",
        "To": workflow.label(r["to_state"]) if r["to_state"] else "",
        "By": r["actor"] or "system", "Note": r["note"] or "",
    } for r in rows]), hide_index=True, use_container_width=True)


def kyc_banner(user):
    status = db.kyc_status(user["id"], user["role"])
    if status == "verified":
        return True
    st.warning(f"Your profile is **{status}**. Complete verification on the *My Profile* page "
               f"to {'list receipts' if user['role'] == 'borrower' else 'place bids'} for real. "
               f"(You can still explore.)")
    return False


def forecast_chart(days=90, height=320):
    fc = market.get_forecast(days)
    hist = fc["history"].tail(240)
    f = fc["forecast"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist["date"], y=hist["price"], name="History",
                             line=dict(color="#64748b", width=1.5)))
    fig.add_trace(go.Scatter(x=f["date"], y=f["hi"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=f["date"], y=f["lo"], fill="tonexty", line=dict(width=0),
                             fillcolor="rgba(16,185,129,0.12)", name="90% band"))
    fig.add_trace(go.Scatter(x=f["date"], y=f["price"], name="Forecast",
                             line=dict(color="#10b981", width=2.5)))
    fig.add_hline(y=market.get_msp(), line=dict(color="#f59e0b", dash="dot"),
                  annotation_text=f"MSP {inr(market.get_msp())}", annotation_position="bottom left")
    fig.update_layout(template="plotly_dark", height=height, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.1))
    return fig, fc


# ═══════════════════════════════════════════════════════════════════════
#  LOGIN
# ═══════════════════════════════════════════════════════════════════════
def login_screen():
    st.markdown("<h1>🌱 SoyFinance</h1><p class='muted'>Warehouse-receipt finance — "
                "borrowers raise working capital against stored soybean; investors fund the best-priced risk.</p>",
                unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        tab_login, tab_signup = st.tabs(["Log in", "Sign up"])
        with tab_login:
            with st.form("login"):
                email = st.text_input("Email")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", type="primary", use_container_width=True):
                    if auth.login(email, pw):
                        st.rerun()
                    else:
                        st.error("Wrong email or password.")
            st.caption("Demo: `ramesh0@demo.in` (borrower) / `anand0@demo.in` (investor) — password `demo1234`")
        with tab_signup:
            with st.form("signup"):
                role = st.radio("I am a", ["borrower", "investor"], horizontal=True)
                name = st.text_input("Name / entity")
                email2 = st.text_input("Email", key="su_email")
                phone = st.text_input("Mobile", key="su_phone")
                pw2 = st.text_input("Password", type="password", key="su_pw")
                if st.form_submit_button("Create account", type="primary", use_container_width=True):
                    u, err = auth.signup(role, name, email2, pw2, phone)
                    if err:
                        st.error(err)
                    else:
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════════
#  BORROWER
# ═══════════════════════════════════════════════════════════════════════
def b_listings(user):
    st.header("My listings & bids")
    kyc_banner(user)
    rows = db.list_listings_for_borrower(user["id"])
    if not rows:
        st.info("No listings yet — go to **Deposit soybean** to create one.")
        return
    for row in rows:
        L = db.get_listing(row["id"])
        state = L.get("workflow_state") or "draft"
        bids = db.list_bids_for_listing(L["id"])
        pend = [b for b in bids if b["status"] == "pending"]
        head = (f"#{L['id']} · {workflow.label(state)} · {L['qty_qtl']:.0f} qtl {L['grade']} · "
                f"ask {inr(L['requested_amount'])} · {L['tenure_days']}d · "
                f"score {L['investment_score'] or '—'}/100" + (f" · {len(pend)} bid(s)" if pend else ""))
        with st.expander(head, expanded=(state in ("conditional", "manual_review", "rejected"))):
            render_case_decision(L, key=f"b{L['id']}")

            if state == "conditional":
                st.warning("Underwriting cleared with conditions. Publishing tells investors the "
                           "conditions must be met before disbursement.")
                if st.button("Accept conditions & publish to the bid board",
                             key=f"pub{L['id']}", type="primary"):
                    ok, msg = workflow.transition(L["id"], "listed", user_id=user["id"],
                                                  note="Borrower accepted the pre-funding conditions")
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            elif state == "approved":
                if st.button("Publish to the bid board", key=f"pub{L['id']}", type="primary"):
                    ok, msg = workflow.transition(L["id"], "listed", user_id=user["id"])
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            elif state == "rejected":
                st.error("Not fundable as submitted. Fix the failed hard rule(s) above "
                         "(complete KYC, obtain a WDRA e-NWR, or reduce the amount) and resubmit.")
                if st.button("Re-run underwriting", key=f"rr{L['id']}"):
                    with st.spinner("Re-running the rule engine…"):
                        scoring.underwrite(L["id"], user_id=user["id"])
                    st.rerun()
            elif state == "manual_review":
                st.info("A credit officer is reviewing this case.")
            elif state == "listed":
                if pend:
                    st.markdown(f"**{len(pend)} bid(s) received**")
                    for b in pend:
                        c1, c2, c3, c4 = st.columns([2, 1.4, 1.4, 1])
                        c1.write(b["investor_email"])
                        c2.write(inr(b["amount"]))
                        c3.write(f"{b['interest_rate']:.2f}% p.a.")
                        if c4.button("Accept", key=f"acc{b['id']}"):
                            loan = db.accept_bid(b["id"], user["id"])
                            if loan:
                                st.success(f"Accepted. Loan #{loan} created.")
                                st.rerun()
                            else:
                                st.error("Could not accept (already matched?).")
                else:
                    st.caption("Live on the bid board — no bids yet.")
            elif state in ("matched", "funded"):
                acc = next((b for b in bids if b["status"] == "accepted"), None)
                if acc:
                    st.success(f"Matched with {acc['investor_email']} at {acc['interest_rate']:.2f}% "
                               f"for {inr(acc['amount'])}.")

            if st.checkbox("Audit trail", key=f"au{L['id']}"):
                render_audit(L["id"])


def b_deposit(user):
    st.header("Deposit soybean → create a listing")
    verified = kyc_banner(user)
    with st.form("deposit"):
        c1, c2, c3 = st.columns(3)
        qty = c1.number_input("Quantity (qtl)", 10.0, 5000.0, 200.0, step=10.0)
        grade = c2.selectbox("Grade", ["A", "B", "C"])
        wh = c3.text_input("Warehouse", "Origo Latur WH")
        c4, c5, c6 = st.columns(3)
        wdra = c4.text_input("WDRA e-NWR no.", "LA-ENWR-40021")
        dep_date = c5.date_input("Deposit date")
        cur = market.get_current_price()["price"]
        dep_price = c6.number_input("Deposit price (₹/qtl)", 2000.0, 12000.0, float(round(cur)), step=10.0)
        st.markdown("**Lab assay** (optional but improves the score)")
        c7, c8, c9, c10 = st.columns(4)
        moist = c7.number_input("Moisture %", 6.0, 18.0, 11.0, step=0.1)
        oil = c8.number_input("Oil %", 14.0, 24.0, 18.5, step=0.1)
        prot = c9.number_input("Protein %", 28.0, 45.0, 36.0, step=0.1)
        fm = c10.number_input("Foreign matter %", 0.0, 6.0, 1.2, step=0.1)
        ins = st.selectbox("Insurance", ["full", "partial", "none"])
        c11, c12 = st.columns(2)
        mkt_val = qty * dep_price
        req = c11.number_input("Loan requested (₹)", 10000.0, float(mkt_val),
                               float(round(mkt_val * 0.7 / 1000) * 1000), step=5000.0)
        tenure = c12.selectbox("Tenure (days)", [60, 90, 120], index=1)
        st.caption(f"Collateral value ≈ {inr(mkt_val)} · requested LTV ≈ {req / mkt_val:.0%}")
        go_ = st.form_submit_button("Create listing", type="primary", use_container_width=True)
    if go_:
        rid = db.create_receipt(user["id"], qty_qtl=qty, grade=grade, warehouse=wh, wdra_no=wdra,
                                deposit_date=str(dep_date), deposit_price=dep_price,
                                quality={"moisture": moist, "oil": oil, "protein": prot, "fm": fm,
                                         "insurance": ins, "storage_days": 1})
        lid = db.create_listing(rid, user["id"], requested_amount=req, tenure_days=tenure)
        db.log_action(lid, user["id"], "edit", note=f"Receipt #{rid} deposited and listing created")
        with st.spinner("Underwriting: risk engine · price forecast · rule check…"):
            res = scoring.underwrite(lid, user_id=user["id"])
        L = db.get_listing(lid)
        st.markdown(f"### Underwriting result — listing #{lid}")
        render_case_decision(L, key=f"new{lid}")
        if L.get("workflow_state") == "listed":
            st.success("Approved and published to the bid board.")
        else:
            st.info("Open **My listings** to act on this case.")


def b_profile(user):
    st.header("My profile & verification")
    p = db.get_borrower_profile(user["id"]) or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("KYC status", p.get("kyc_status", "pending"))
    c2.metric("District", p.get("district") or "—")
    c3.metric("PAN", p.get("pan") or "—")
    with st.form("bprof"):
        name = st.text_input("Name", p.get("name") or "")
        district = st.text_input("District", p.get("district") or "")
        pan = st.text_input("PAN", p.get("pan") or "")
        if st.form_submit_button("Save"):
            db.upsert_borrower_profile(user["id"], name=name, district=district, pan=pan.upper())
            st.success("Saved."); st.rerun()

    st.divider()
    st.subheader("Run verification")
    if not BVERIFY:
        st.info("`borrower_verification_demo.py` not importable.")
        return
    st.caption("Aadhaar offline e-KYC · PAN · bank IFSC/PIN · bank-statement analysis · credit report. "
               "Runs locally.")
    bverify.inject_css()
    bverify.render_real()
    vb = st.session_state.get("verified_borrower")
    if vb:
        if st.button("✅ Save this verification to my profile", type="primary"):
            db.upsert_borrower_profile(
                user["id"], kyc_status="verified",
                verification={"run_id": vb["run_id"], "foir": vb.get("foir"),
                              "bureau_score": vb.get("bureau_score"),
                              "pillar_enhanced": vb.get("pillar_enhanced")})
            st.success("Profile verified. New listings will use this borrower score.")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════
#  INVESTOR
# ═══════════════════════════════════════════════════════════════════════
def _due_diligence(L, user):
    sc = L.get("scores") or {}
    iv = sc.get("investment") or {}
    rr = sc.get("risk") or {}
    st.markdown(f"### Listing #{L['id']} — {gbadge(L['investment_grade'])} "
                f"Investment {L['investment_score'] or '—'}/100", unsafe_allow_html=True)
    st.markdown(f"Underwriting: {decision_chip(L.get('decision'))} "
                f"{state_chip(L.get('workflow_state') or 'listed')}", unsafe_allow_html=True)
    if L.get("narrative"):
        st.markdown(f"<div class='card'>{L['narrative']}</div>", unsafe_allow_html=True)
    if L.get("conditions"):
        st.markdown("<span class='neg'>Conditions the borrower must clear before disbursement:</span>",
                    unsafe_allow_html=True)
        for cnd in L["conditions"]:
            st.markdown(f"<div class='muted'>• {cnd}</div>", unsafe_allow_html=True)

    a, b, c = st.columns(3)
    a.metric("Borrower score", f"{iv.get('borrower_score', '—')}/100", iv.get("borrower_basis", ""))
    b.metric("Collateral score", f"{iv.get('collateral_score', '—')}/100")
    c.metric("Price outlook", f"{iv.get('price_outlook_score', '—')}/100")

    st.markdown("**Collateral**")
    q = L.get("quality") or {}
    st.markdown(
        f"<div class='card'>{L['qty_qtl']:.0f} qtl · grade {L['grade']} · {L['warehouse'] or '—'}<br>"
        f"WDRA e-NWR: <b>{L['wdra_no'] or '❌ not registered'}</b> · deposit price {inr(L['deposit_price'])}<br>"
        f"moisture {q.get('moisture','—')}% · oil {q.get('oil','—')}% · insurance {q.get('insurance','—')}</div>",
        unsafe_allow_html=True)

    ltv_now, ltv_str = iv.get("ltv_now"), iv.get("ltv_stressed")
    if ltv_now is not None:
        st.markdown("**Price-adjusted LTV**")
        cur = sc.get("current_price")
        fpts = sc.get("forecast_points") or {}
        st.table(pd.DataFrame({
            "": ["Soybean price", "Collateral value", "LTV", "Margin buffer"],
            "Now": [inr(cur), inr(iv.get("collateral_value_now")),
                    f"{ltv_now:.0%}", f"{(iv.get('margin_buffer_now') or 0):.0%}"],
            "If forecast low hits": [inr(min(fpts.values()) if fpts else cur),
                                     inr(iv.get("collateral_value_stressed")),
                                     f"{ltv_str:.0%}", f"{(iv.get('margin_buffer_stressed') or 0):.0%}"],
        }))

    st.markdown("**What's driving the score**")
    for d in iv.get("drivers", []):
        cls = "pos" if d["sign"] == "+" else "neg"
        st.markdown(f"<span class='{cls}'>{d['sign']}</span> {d['text']}", unsafe_allow_html=True)

    st.markdown("**Underwriting rule check**")
    render_rule_checklist(L.get("rules") or {}, key=f"i{L['id']}")

    st.markdown("**6-pillar risk breakdown**")
    for k, p in (rr.get("pillar_scores") or {}).items():
        if isinstance(p, dict):
            st.markdown(f"<span class='muted'>{k.replace('_',' ').title()} — "
                        f"{p.get('score','—')}/100 (weight {p.get('weight','—')})</span>",
                        unsafe_allow_html=True)
    st.caption(f"Composite risk {rr.get('composite_score','—')}/100 · {rr.get('risk_grade','')}")

    if st.checkbox("Audit trail", key=f"ia{L['id']}"):
        render_audit(L["id"])

    if st.checkbox("Show price-forecast chart", key=f"pf{L['id']}"):
        fig, _ = forecast_chart(L["tenure_days"] if L["tenure_days"] in (60, 90, 120) else 90, height=260)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Place a bid**")
    already = [b for b in db.list_bids_for_listing(L["id"])
               if b["investor_id"] == user["id"] and b["status"] == "pending"]
    if already:
        st.info(f"You have a pending bid: {inr(already[0]['amount'])} at {already[0]['interest_rate']:.2f}%.")
        return
    with st.form(f"bid{L['id']}"):
        c1, c2 = st.columns(2)
        amt = c1.number_input("Amount (₹)", 10000.0, float(L["requested_amount"]),
                              float(L["requested_amount"]), step=5000.0, key=f"ba{L['id']}")
        rate = c2.number_input("Interest rate (% p.a.)", 8.0, 30.0,
                               float((L["suggested_rate"] or "14%").split("-")[0].replace("%", "")),
                               step=0.25, key=f"br{L['id']}")
        if st.form_submit_button("Submit bid", type="primary"):
            db.create_bid(L["id"], user["id"], amount=amt, interest_rate=rate)
            st.success("Bid submitted."); st.rerun()


def i_bidboard(user):
    st.header("Bid board")
    kyc_banner(user)
    rows = db.list_open_listings()
    if not rows:
        st.info("No open listings right now.")
        return
    order = st.selectbox("Sort by", ["Investment score", "Requested amount", "Newest"])
    if order == "Investment score":
        rows.sort(key=lambda r: (r["investment_score"] or 0), reverse=True)
    elif order == "Requested amount":
        rows.sort(key=lambda r: r["requested_amount"], reverse=True)
    for L in rows:
        head = (f"#{L['id']}  ·  [{L['investment_grade'] or '?'}] {L['investment_score'] or '—'}/100  ·  "
                f"{L['qty_qtl']:.0f} qtl {L['grade']}  ·  ask {inr(L['requested_amount'])}  ·  "
                f"{L['tenure_days']}d  ·  rate {L['suggested_rate'] or '—'}")
        with st.expander(head):
            _due_diligence(db.get_listing(L["id"]), user)


def i_mybids(user):
    st.header("My bids & portfolio")
    rows = db.list_bids_for_investor(user["id"])
    if not rows:
        st.info("No bids yet — place one from the **Bid board**.")
        return
    for b in rows:
        c1, c2, c3, c4, c5 = st.columns([1.2, 1.3, 1.2, 1.4, 1])
        c1.write(f"Listing #{b['listing_id']}")
        c2.write(inr(b["amount"]))
        c3.write(f"{b['interest_rate']:.2f}%")
        c4.write(f"**{b['status']}** · listing {b['listing_status']}")
        if b["status"] == "pending" and c5.button("Withdraw", key=f"wd{b['id']}"):
            db.withdraw_bid(b["id"], user["id"]); st.rerun()
    won = [b for b in rows if b["status"] == "accepted"]
    if won:
        st.divider()
        st.subheader("Funded")
        tot = sum(b["amount"] for b in won)
        wavg = sum(b["amount"] * b["interest_rate"] for b in won) / tot if tot else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Deployed", inr(tot))
        c2.metric("Loans", len(won))
        c3.metric("Wtd. avg rate", f"{wavg:.2f}%")


def i_profile(user):
    st.header("My profile & verification")
    p = db.get_investor_profile(user["id"]) or {}
    with st.form("iprof"):
        et = st.selectbox("Entity type", ["individual", "huf", "nbfc", "fund"],
                          index=["individual", "huf", "nbfc", "fund"].index(p.get("entity_type", "individual")))
        pc = st.text_input("PAN / CIN", p.get("pan_or_cin") or "")
        bank = st.text_input("Funding bank", p.get("funding_bank") or "")
        acc = st.checkbox("I confirm I am eligible to lend and the funds are my own / mandated",
                          value=(p.get("kyc_status") == "verified"))
        if st.form_submit_button("Save", type="primary"):
            db.upsert_investor_profile(user["id"], entity_type=et, pan_or_cin=pc.upper(),
                                       funding_bank=bank,
                                       kyc_status="verified" if (acc and pc and bank) else "pending")
            st.success("Saved."); st.rerun()
    st.metric("KYC status", db.kyc_status(user["id"], "investor"))


# ═══════════════════════════════════════════════════════════════════════
#  SHARED
# ═══════════════════════════════════════════════════════════════════════
def page_market(user):
    st.header("Soybean market")
    cp = market.get_current_price()
    ctx = market.get_market_context(90)
    fig, fc = forecast_chart(90, height=380)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current price", inr(cp["price"]), cp["source"])
    c2.metric("MSP (crop year)", inr(market.get_msp()))
    c3.metric("90-day forecast", f"{fc['delta_pct']:+.1f}%")
    c4.metric("Volatility regime", str(ctx.get("regime", "—")).title(),
              f"VaR90 {ctx.get('var_pct','—')}%")
    st.plotly_chart(fig, use_container_width=True)
    if fc.get("points"):
        st.table(pd.DataFrame({"Horizon": [f"{h} days" for h in fc["points"]],
                               "Forecast price": [inr(v) for v in fc["points"].values()]}))
    st.caption(f"Model: {fc.get('trained','')} · backtest MAE {fc.get('metrics',{}).get('mae','—')} "
               f"· MAPE {fc.get('metrics',{}).get('mape','—')}% · R² {fc.get('metrics',{}).get('r2','—')} · "
               f"CDVI {ctx.get('cdvi','—')}")


# ═══════════════════════════════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════════════════════════════
user = auth.current_user()
if not user:
    login_screen()
    st.stop()

with st.sidebar:
    st.markdown(f"### 🌱 SoyFinance")
    st.markdown(f"**{user['email']}**  \n<span class='muted'>{user['role'].title()}</span>",
                unsafe_allow_html=True)
    if st.button("Log out"):
        auth.logout(); st.rerun()
    st.divider()
    if user["role"] == "borrower":
        pages = {"My listings": b_listings, "Deposit soybean": b_deposit,
                 "Market": page_market, "My profile": b_profile}
    elif user["role"] == "investor":
        pages = {"Bid board": i_bidboard, "My bids": i_mybids,
                 "Market": page_market, "My profile": i_profile}
    else:
        pages = {"Market": page_market}
    choice = st.radio("Go to", list(pages), label_visibility="collapsed")
    st.divider()
    c = db.counts()
    st.caption(f"{c['open_listings']} listed · {c['in_underwriting']} in underwriting · "
               f"{c['rejected']} rejected · {c['bids']} bids · {c['matched_listings']} matched")

pages[choice](user)
