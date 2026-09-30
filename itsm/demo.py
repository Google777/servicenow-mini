"""Deterministic demo builder for the public Streamlit application."""
from __future__ import annotations

from itsm import change, cmdb
from itsm.db import Database


def seed_demo(db: Database) -> None:
    """Seed the CMDB and three changes at useful lifecycle stages.

    Idempotent: if any change exists, the session has already been initialized.
    """
    cmdb.seed_small_datacenter(db)
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM change_request").fetchone()[0]:
            return

    # High-risk change deliberately awaiting approval: ideal governance demo.
    pending = change.create_change(
        db, "Replace UPS-01 battery string", "j.gogle", change_type="normal",
        description="Replace an aging battery string during a controlled maintenance window.",
    )
    change.link_ci(db, pending.change_id, "UPS-01", "j.gogle")
    change.transition(db, pending.change_id, "assess", "j.gogle")
    change.transition(db, pending.change_id, "authorize", "j.gogle")
    change.request_approval(db, pending.change_id, "cab-chair", "j.gogle")

    # Scheduled change: demonstrates a passed approval gate.
    scheduled = change.create_change(
        db, "Upgrade core switch firmware", "service.ops", change_type="normal",
        description="Apply validated firmware to the Denver core switch.",
    )
    change.link_ci(db, scheduled.change_id, "NET-01", "service.ops")
    change.transition(db, scheduled.change_id, "assess", "service.ops")
    change.transition(db, scheduled.change_id, "authorize", "service.ops")
    change.request_approval(db, scheduled.change_id, "network-lead", "service.ops")
    change.decide_approval(db, scheduled.change_id, "network-lead", True,
                           "Rollback image and validation plan reviewed.")
    change.transition(db, scheduled.change_id, "scheduled", "service.ops")

    # Closed standard change: demonstrates full lifecycle and audit history.
    closed = change.create_change(
        db, "Patch App Server 7", "automation", change_type="standard",
        description="Apply the approved monthly OS patch baseline.",
    )
    change.link_ci(db, closed.change_id, "SRV-0007", "automation")
    change.transition(db, closed.change_id, "assess", "automation")
    change.transition(db, closed.change_id, "authorize", "automation")
    change.request_approval(db, closed.change_id, "standard-change-owner", "automation")
    change.decide_approval(db, closed.change_id, "standard-change-owner", True,
                           "Pre-authorized standard change template.")
    for state in ("scheduled", "implement", "review", "closed"):
        change.transition(db, closed.change_id, state, "automation")
