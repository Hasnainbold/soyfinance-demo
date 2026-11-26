"""
workflow.py — lifecycle state machine + audit trail (Traan §5.9, §12).

    draft ─► underwriting ─► approved ──────────► listed ─► matched ─► funded ─► closed
                          ├► conditional ──────►┘
                          ├► manual_review ─► approved | conditional | rejected
                          ├► fraud_hold ────► manual_review | rejected
                          └► rejected

Every transition is recorded in audit_log with who, when, and why.
"""

from __future__ import annotations

import db

STATES = {
    "draft":         ("Draft", "#64748b", "Created but not submitted for underwriting."),
    "underwriting":  ("Underwriting", "#3b82f6", "Rule engine and scoring in progress."),
    "approved":      ("Approved", "#10b981", "Cleared every rule — ready to list."),
    "conditional":   ("Conditionally approved", "#84cc16", "Clear the conditions, then list."),
    "manual_review": ("Manual review", "#f59e0b", "A credit officer must decide."),
    "fraud_hold":    ("Fraud hold", "#a855f7", "Escalated to the fraud team."),
    "rejected":      ("Rejected", "#ef4444", "Failed a hard rule — not fundable."),
    "listed":        ("Listed", "#22d3ee", "Live on the bid board."),
    "matched":       ("Matched", "#14b8a6", "A bid was accepted."),
    "funded":        ("Funded", "#059669", "Loan disbursed."),
    "closed":        ("Closed", "#475569", "Repaid or otherwise settled."),
}

ALLOWED = {
    "draft":         {"underwriting", "rejected"},
    "underwriting":  {"approved", "conditional", "manual_review", "fraud_hold", "rejected"},
    "approved":      {"listed", "rejected"},
    "conditional":   {"listed", "manual_review", "rejected"},
    "manual_review": {"approved", "conditional", "rejected", "fraud_hold"},
    "fraud_hold":    {"manual_review", "rejected"},
    "listed":        {"matched", "rejected", "closed"},
    "matched":       {"funded", "closed"},
    "funded":        {"closed"},
    "rejected":      set(),
    "closed":        set(),
}

# states in which a listing is visible to investors on the bid board
LIVE_STATES = {"listed"}
# decision → the workflow state it lands in
DECISION_STATE = {"approve": "approved", "conditional": "conditional",
                  "manual_review": "manual_review", "fraud_hold": "fraud_hold",
                  "reject": "rejected"}


def label(state: str) -> str:
    return STATES.get(state, (state,))[0]


def colour(state: str) -> str:
    return STATES.get(state, ("", "#64748b"))[1]


def can(frm: str, to: str) -> bool:
    return to in ALLOWED.get(frm, set())


def transition(listing_id: int, to_state: str, *, user_id: int | None = None,
               note: str = "", force: bool = False) -> tuple[bool, str]:
    """Move a listing to a new workflow state, logging the transition."""
    L = db.get_listing(listing_id)
    if not L:
        return False, "Listing not found."
    frm = L.get("workflow_state") or "draft"
    if to_state not in STATES:
        return False, f"Unknown state '{to_state}'."
    if not force and not can(frm, to_state):
        return False, f"Cannot move from {label(frm)} to {label(to_state)}."
    db.set_workflow_state(listing_id, to_state)
    # keep the marketplace status in step with the lifecycle
    if to_state in LIVE_STATES:
        db.set_listing_status(listing_id, "open")
    elif to_state in ("rejected", "fraud_hold", "closed"):
        db.set_listing_status(listing_id, "closed")
    elif to_state in ("draft", "underwriting", "approved", "conditional", "manual_review"):
        db.set_listing_status(listing_id, "pending")
    db.log_action(listing_id, user_id, "workflow", from_state=frm, to_state=to_state, note=note)
    return True, f"{label(frm)} → {label(to_state)}"
