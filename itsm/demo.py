"""Deterministic demo builder for the public Streamlit application."""
from __future__ import annotations

from itsm import change, cmdb, work
from itsm.db import Database


def seed_demo(db: Database) -> None:
    """Seed the CMDB and three changes at useful lifecycle stages.

    Idempotent: if any change exists, the session has already been initialized.
    """
    cmdb.seed_small_datacenter(db)
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM change_request").fetchone()[0]:
            _seed_work_demo(db)
            return

    # High-risk change deliberately awaiting approval: ideal governance demo.
    pending = change.create_change(
        db, "Replace UPS-01 battery string", "j.gogle", change_type="normal",
        description="Replace an aging battery string during a controlled maintenance window.",
    )
    change.update_fields(
        db, pending.change_id, "j.gogle", reason="Battery health trend breached replacement threshold.",
        category="hardware", priority="high", assigned_to="dc.facilities",
        planned_start="2026-10-03 22:00", planned_end="2026-10-04 01:00",
        implementation_plan="Transfer protected load, isolate UPS-01 battery string, replace modules, restore and monitor.",
        test_plan="Validate UPS alarms clear; perform discharge test; confirm downstream PDU load and telemetry.",
        backout_plan="Reconnect validated spare string and return load to the pre-change power path.",
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
    change.update_fields(
        db, scheduled.change_id, "service.ops", reason="Current image reaches vendor support boundary.",
        category="network", priority="moderate", assigned_to="network.operations",
        planned_start="2026-10-06 23:00", planned_end="2026-10-07 00:30",
        implementation_plan="Fail traffic to redundant path, install signed image, reboot, and restore traffic.",
        test_plan="Validate routing adjacencies, packet loss, latency, and management-plane health.",
        backout_plan="Boot the prior image and restore the previous startup configuration.",
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
    change.update_fields(
        db, closed.change_id, "automation", reason="Monthly security baseline.",
        category="software", priority="low", assigned_to="platform.automation",
        implementation_plan="Drain workload, apply approved patch bundle, reboot, and return to service.",
        test_plan="Run health check, synthetic transaction, and monitoring validation.",
        backout_plan="Roll back the image snapshot and restore workload placement.",
        close_notes="Patch succeeded; health checks passed; no customer impact observed.",
    )
    change.link_ci(db, closed.change_id, "SRV-0007", "automation")
    change.transition(db, closed.change_id, "assess", "automation")
    change.transition(db, closed.change_id, "authorize", "automation")
    change.request_approval(db, closed.change_id, "standard-change-owner", "automation")
    change.decide_approval(db, closed.change_id, "standard-change-owner", True,
                           "Pre-authorized standard change template.")
    for state in ("scheduled", "implement", "review", "closed"):
        change.transition(db, closed.change_id, state, "automation")


    _seed_work_demo(db)


def _seed_work_demo(db: Database) -> None:
    """Seed execution history that produces useful, explainable insights."""
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM work_order").fetchone()[0]:
            return

    wo1 = work.create_work_order(db, "Quarterly UPS-01 battery inspection", "UPS-01",
                                 assigned_to="dc.facilities")
    work.log_execution(db, wo1, "tech.alex", "Battery impedance test",
                       labor_minutes=90, result="String B exceeded variance threshold")
    work.complete(db, wo1, "tech.alex", "follow_up_required",
                  "Replace four degraded modules and repeat discharge validation.")

    wo2 = work.create_work_order(db, "Retorque UPS-01 battery terminals", "UPS-01",
                                 assigned_to="dc.facilities", parent_wo_id=wo1)
    work.log_execution(db, wo2, "tech.jordan", "Retorque and thermal inspection",
                       labor_minutes=120, result="Connections within specification",
                       part_number="LUG-4/0", part_description="Battery terminal lug",
                       part_quantity=4, part_unit_cost=85)
    work.complete(db, wo2, "tech.jordan", "success", "Thermal scan passed after load transfer.")

    wo3 = work.create_work_order(db, "Investigate PDU-01 breaker trip", "PDU-01",
                                 priority="high", assigned_to="electrical.ops")
    work.log_execution(db, wo3, "tech.sam", "Inspect breaker and downstream load",
                       labor_minutes=180, result="Trip repeated under normal downstream load",
                       part_number="BRK-400A", part_description="400A breaker",
                       part_quantity=1, part_unit_cost=950)
    work.complete(db, wo3, "tech.sam", "failed", "Replacement did not eliminate repeat trip.")

    wo4 = work.create_work_order(db, "Thermal survey of PDU-01 feeder", "PDU-01",
                                 priority="high", assigned_to="electrical.ops", parent_wo_id=wo3)
    work.log_execution(db, wo4, "tech.sam", "Infrared survey and torque verification",
                       labor_minutes=135, result="Loose upstream termination corrected")
    work.complete(db, wo4, "tech.sam", "success", "No thermal anomaly after 60-minute load test.")

    wo5 = work.create_work_order(db, "Monthly OS patch validation", "SRV-0007",
                                 source_type="change", source_ref="CHG1000003",
                                 assigned_to="platform.automation")
    work.log_execution(db, wo5, "automation", "Patch and synthetic validation",
                       labor_minutes=25, result="All checks passed")
    work.complete(db, wo5, "automation", "success", "No regression detected.")

    wo6 = work.create_work_order(db, "Execute core switch firmware change", "NET-01",
                                 source_type="change", source_ref="CHG1000002",
                                 assigned_to="network.operations", priority="high")
    work.transition(db, wo6, "in_progress", "network.operations")
    work.log_execution(db, wo6, "net.tech", "Pre-change validation",
                       labor_minutes=35, result="Redundant path and rollback image verified")
