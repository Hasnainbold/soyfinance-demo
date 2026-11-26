"""auth.py — thin session auth over db.py for the Streamlit platform app."""

from __future__ import annotations

import streamlit as st

import db

_KEY = "uid"


def current_user() -> dict | None:
    uid = st.session_state.get(_KEY)
    if not uid:
        return None
    u = db.get_user(uid)
    if not u or u["status"] != "active":
        st.session_state.pop(_KEY, None)
        return None
    return u


def login(email: str, password: str) -> dict | None:
    u = db.authenticate(email, password)
    if u:
        st.session_state[_KEY] = u["id"]
    return u


def logout() -> None:
    for k in (_KEY, "verified_borrower"):
        st.session_state.pop(k, None)


def signup(role: str, name: str, email: str, password: str, phone: str = "") -> tuple[dict | None, str | None]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None, "Enter a valid email."
    if len(password or "") < 6:
        return None, "Password must be at least 6 characters."
    if db.get_user_by_email(email):
        return None, "That email is already registered — try logging in."
    try:
        uid = db.create_user(role, email, password, phone)
    except Exception as e:
        return None, f"Could not create the account: {e}"
    if role == "borrower":
        db.upsert_borrower_profile(uid, name=name or "")
    else:
        db.upsert_investor_profile(uid, entity_type="individual")
    st.session_state[_KEY] = uid
    return db.get_user(uid), None
