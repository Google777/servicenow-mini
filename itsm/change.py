"""Change Request workflow engine: the state machine, risk scoring, approval
gating, and audit trail — the mini-analogue of ServiceNow's Change Management
application (the closest real module to "Operational Change Control").

Real ServiceNow change lifecycle (ITIL-aligned, simplified for standard/normal
changes): New -> Assess -> Authorize -> Scheduled -> Implement -> Review ->
Closed (with a Cancelled exit at most stages). This module implements that
exact shape as an explicit state machine (not just a status string you can set
to anything) — the single most important ServiceNow concept to understand:
**state transitions are governed, not free-form.**
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from itsm import cmdb
from itsm.db import Database

# --------------------------------------------------------------------------- #
# The state machine: explicit allowed transitions (mirrors a ServiceNow
# Workflow/Flow Designer "state model" enforced server-side, not just a
# dropdown the UI happens to show).
# --------------------------------------------------------------------------- #
STATES = ("draft", "assess", "authorize", "scheduled", "implement", "review",
          "closed", "cancelled")

TRANSITIONS: dict[str, set[str]] = {
    "draft":      {"assess", "cancelled"},
    "assess":     {"authorize", "draft", "cancelled"},
    "authorize":  {"scheduled", "assess", "cancelled"},
    "scheduled":  {"implement", "cancelled"},
    "implement":  {"review", "cancelled"},
    "review":     {"closed", "implement"},   # failed implementation -> retry
    "closed":     set(),
    "cancelled":  set(),
}


class InvalidTransition(Exception):
    pass


class ApprovalRequired(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_change_id(db: Database) -> str:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM change_request").fetchone()[0]
    return f"CHG{1000000 + n + 1}"


def _audit(db: Database, change_id: str, actor: str, event: str, detail: str) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO audit_log (change_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
            (change_id, _now(), actor, event, detail),
        )


@dataclass
class ChangeRequest:
    change_id: str
    short_desc: str
    state: str
    change_type: str
    risk: str | None
    risk_score: int | None
    requested_by: str
    assigned_to: str | None


def _row_to_change(row) -> ChangeRequest:
    return ChangeRequest(row["change_id"], row["short_desc"], row["state"],
                         row["change_type"], row["risk"], row["risk_score"],
                         row["requested_by"], row["assigned_to"])


def create_change(db: Database, short_desc: str, requested_by: str,
                  description: str = "", change_type: str = "normal") -> ChangeRequest:
    """Open a new change in 'draft' — the ServiceNow analogue of a new
    change_request record (state = -5/'New' in the real product's numeric
    state model; we use readable strings instead)."""
    change_id = _next_change_id(db)
    now = _now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO change_request
               (change_id, short_desc, description, change_type, state,
                requested_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (change_id, short_desc, description, change_type, requested_by, now, now),
        )
    _audit(db, change_id, requested_by, "created", f"change opened as draft ({change_type})")
    return get_change(db, change_id)  # type: ignore[return-value]


def get_change(db: Database, change_id: str) -> ChangeRequest | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM change_request WHERE change_id=?", (change_id,)).fetchone()
        return _row_to_change(row) if row else None


def link_ci(db: Database, change_id: str, ci_id: str, actor: str) -> None:
    """Associate a CI with the change — ServiceNow's change_request_ci join,
    the record that lets impact/risk analysis know WHAT is being touched."""
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO change_ci (change_id, ci_id) VALUES (?, ?)",
                  (change_id, ci_id))
    _audit(db, change_id, actor, "ci_linked", f"linked CI {ci_id}")


def affected_cis(db: Database, change_id: str) -> list[str]:
    with db.conn() as c:
        return [r["ci_id"] for r in c.execute(
            "SELECT ci_id FROM change_ci WHERE change_id=?", (change_id,)).fetchall()]


# --------------------------------------------------------------------------- #
# Risk scoring — the analytical core, and the piece that maps most directly
# onto the "Business Operations Program Analyst" JD language: "assess the
# impact of operational initiatives... support prioritization by helping
# leaders understand operational impacts, risks, potential outcomes."
#
# Real ServiceNow risk assessment is a configurable questionnaire scored into
# a risk value; this is a deterministic, explainable scoring function over
# the CMDB's own criticality + blast-radius data — simpler, but the SAME
# underlying idea: turn CI-level facts into a leadership-legible number.
# --------------------------------------------------------------------------- #
_CRIT_WEIGHT = {"low": 5, "medium": 15, "high": 30, "critical": 45}
_TYPE_WEIGHT = {"emergency": 25, "normal": 10, "standard": 0}


def compute_risk(db: Database, change_id: str) -> tuple[str, int]:
    """Score 0-100 from: (a) the highest criticality among directly-linked
    CIs, (b) the total blast radius (downstream-impacted CI count) across all
    linked CIs, (c) the change_type. Returns (risk_label, score)."""
    change = get_change(db, change_id)
    if change is None:
        raise ValueError(f"no such change {change_id}")
    cis = affected_cis(db, change_id)
    if not cis:
        return "low", 5  # no CIs touched yet -> can't really be high-risk

    max_crit_weight = 0
    blast_radius: set[str] = set()
    for ci_id in cis:
        ci = cmdb.get_ci(db, ci_id)
        if ci:
            max_crit_weight = max(max_crit_weight, _CRIT_WEIGHT.get(ci.criticality, 15))
        blast_radius.update(cmdb.downstream_impact(db, ci_id))
        blast_radius.add(ci_id)

    blast_weight = min(len(blast_radius) * 3, 30)   # cap so it doesn't dominate
    type_weight = _TYPE_WEIGHT.get(change.change_type, 10)
    score = min(max_crit_weight + blast_weight + type_weight, 100)

    if score >= 60:
        label = "high"
    elif score >= 30:
        label = "moderate"
    else:
        label = "low"
    return label, score


def request_approval(db: Database, change_id: str, approver: str, actor: str) -> None:
    with db.conn() as c:
        c.execute(
            """INSERT INTO change_approval (change_id, approver, decision)
               VALUES (?, ?, 'requested')
               ON CONFLICT(change_id, approver) DO UPDATE SET decision='requested', decided_at=NULL""",
            (change_id, approver),
        )
    _audit(db, change_id, actor, "approval_requested", f"requested approval from {approver}")


def decide_approval(db: Database, change_id: str, approver: str, approve: bool,
                    comment: str = "") -> None:
    decision = "approved" if approve else "rejected"
    with db.conn() as c:
        c.execute(
            """UPDATE change_approval SET decision=?, comment=?, decided_at=?
               WHERE change_id=? AND approver=?""",
            (decision, comment, _now(), change_id, approver),
        )
    _audit(db, change_id, approver, "approval_decision", f"{decision}: {comment}".strip(": "))


def approval_status(db: Database, change_id: str) -> dict[str, str]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT approver, decision FROM change_approval WHERE change_id=?",
            (change_id,)).fetchall()
    return {r["approver"]: r["decision"] for r in rows}


def _fully_approved(db: Database, change_id: str) -> bool:
    statuses = approval_status(db, change_id)
    return bool(statuses) and all(v == "approved" for v in statuses.values())


def _any_rejected(db: Database, change_id: str) -> bool:
    return any(v == "rejected" for v in approval_status(db, change_id).values())


def transition(db: Database, change_id: str, to_state: str, actor: str,
               comment: str = "") -> ChangeRequest:
    """Move a change to ``to_state``, enforcing:
      1. The transition is legal per the state graph (TRANSITIONS).
      2. Entering 'authorize' requires risk to have been computed (assess done).
      3. Leaving 'authorize' into 'scheduled' requires full approval and no
         rejections — this is THE core governance gate, the ServiceNow
         equivalent of the CAB (Change Advisory Board) approval requirement.
    Every transition is written to audit_log — nothing changes state silently.
    """
    change = get_change(db, change_id)
    if change is None:
        raise ValueError(f"no such change {change_id}")

    if to_state not in TRANSITIONS.get(change.state, set()):
        raise InvalidTransition(
            f"cannot move {change_id} from '{change.state}' to '{to_state}' "
            f"(allowed: {sorted(TRANSITIONS.get(change.state, set()))})")

    risk_label, risk_score = change.risk, change.risk_score
    if to_state == "authorize":
        risk_label, risk_score = compute_risk(db, change_id)

    if to_state == "scheduled":
        if _any_rejected(db, change_id):
            raise ApprovalRequired(f"{change_id} has a rejected approval — cannot schedule")
        if not _fully_approved(db, change_id):
            raise ApprovalRequired(
                f"{change_id} requires full approval before scheduling "
                f"(current: {approval_status(db, change_id)})")

    with db.conn() as c:
        c.execute(
            "UPDATE change_request SET state=?, risk=?, risk_score=?, updated_at=? WHERE change_id=?",
            (to_state, risk_label, risk_score, _now(), change_id),
        )
    detail = f"{change.state} -> {to_state}"
    if comment:
        detail += f" ({comment})"
    _audit(db, change_id, actor, "state_change", detail)
    return get_change(db, change_id)  # type: ignore[return-value]


def audit_trail(db: Database, change_id: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT at, actor, event, detail FROM audit_log WHERE change_id=? ORDER BY audit_id",
            (change_id,)).fetchall()
    return [dict(r) for r in rows]
