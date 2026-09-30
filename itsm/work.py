"""Closed-loop operational work management.

The model demonstrates the loop ServiceNow-family work-management products are
used to support: work order -> labor/parts/outcome evidence -> analytics ->
analytics-generated follow-up work, with source lineage back to prior work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from itsm.db import Database

WORK_TRANSITIONS = {
    "planned": {"in_progress", "cancelled"},
    "in_progress": {"complete", "cancelled"},
    "complete": set(),
    "cancelled": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_id(db: Database) -> str:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM work_order").fetchone()[0]
    return f"WO{2000000 + n + 1}"


def create_work_order(db: Database, short_desc: str, ci_id: str, *,
                      priority: str = "medium", assigned_to: str = "",
                      source_type: str = "manual", source_ref: str | None = None,
                      parent_wo_id: str | None = None, due_at: str | None = None) -> str:
    wo_id = _next_id(db)
    with db.conn() as c:
        c.execute("""INSERT INTO work_order
          (wo_id,short_desc,ci_id,state,priority,assigned_to,source_type,source_ref,
           parent_wo_id,opened_at,due_at)
          VALUES (?,?,?,'planned',?,?,?,?,?,?,?)""",
          (wo_id, short_desc, ci_id, priority, assigned_to, source_type,
           source_ref, parent_wo_id, _now(), due_at))
    log_execution(db, wo_id, "system", "Work order created", result=f"source={source_type}")
    return wo_id


def list_work_orders(db: Database, state: str | None = None) -> list[dict]:
    q = """SELECT w.*,c.name ci_name,c.ci_type,c.criticality,
                  COALESCE(SUM(l.labor_minutes),0) labor_minutes,
                  COALESCE(SUM(l.part_quantity*l.part_unit_cost),0) parts_cost,
                  COUNT(l.log_id) log_count
           FROM work_order w JOIN cmdb_ci c ON c.ci_id=w.ci_id
           LEFT JOIN work_log l ON l.wo_id=w.wo_id"""
    params = []
    if state:
        q += " WHERE w.state=?"; params.append(state)
    q += " GROUP BY w.wo_id ORDER BY CASE w.state WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,w.wo_id DESC"
    with db.conn() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def get_work_order(db: Database, wo_id: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM work_order WHERE wo_id=?", (wo_id,)).fetchone()
        return dict(row) if row else None


def log_execution(db: Database, wo_id: str, technician: str, action: str, *,
                  labor_minutes: int = 0, result: str = "", part_number: str | None = None,
                  part_description: str | None = None, part_quantity: float = 0,
                  part_unit_cost: float = 0) -> None:
    with db.conn() as c:
        c.execute("""INSERT INTO work_log
          (wo_id,at,technician,labor_minutes,action,result,part_number,
           part_description,part_quantity,part_unit_cost)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (wo_id, _now(), technician, max(0, int(labor_minutes)), action, result,
           part_number, part_description, max(0, float(part_quantity)),
           max(0, float(part_unit_cost))))


def transition(db: Database, wo_id: str, to_state: str, actor: str) -> None:
    wo = get_work_order(db, wo_id)
    if not wo:
        raise ValueError(f"no such work order {wo_id}")
    if to_state not in WORK_TRANSITIONS.get(wo["state"], set()):
        raise ValueError(f"cannot move {wo_id} from {wo['state']} to {to_state}")
    with db.conn() as c:
        c.execute("UPDATE work_order SET state=? WHERE wo_id=?", (to_state, wo_id))
    log_execution(db, wo_id, actor, "State transition",
                  result=f"{wo['state']} -> {to_state}")


def complete(db: Database, wo_id: str, actor: str, outcome: str,
             completion_notes: str = "") -> None:
    if outcome not in ("success", "failed", "follow_up_required"):
        raise ValueError("invalid outcome")
    wo = get_work_order(db, wo_id)
    if not wo:
        raise ValueError(f"no such work order {wo_id}")
    if wo["state"] == "planned":
        transition(db, wo_id, "in_progress", actor)
    wo = get_work_order(db, wo_id)
    if wo["state"] != "in_progress":
        raise ValueError(f"cannot complete {wo_id} from {wo['state']}")
    with db.conn() as c:
        c.execute("""UPDATE work_order SET state='complete',outcome=?,completion_notes=?,completed_at=?
                     WHERE wo_id=?""", (outcome, completion_notes, _now(), wo_id))
    log_execution(db, wo_id, actor, "Work completed", result=outcome)


def work_logs(db: Database, wo_id: str) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM work_log WHERE wo_id=? ORDER BY log_id", (wo_id,)).fetchall()]


def operational_insights(db: Database) -> list[dict]:
    """Aggregate execution history by CI and produce transparent recommendations."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("""
          WITH order_stats AS (
            SELECT ci_id,
                   COUNT(*) work_orders,
                   SUM(CASE WHEN state='complete' THEN 1 ELSE 0 END) completed,
                   SUM(CASE WHEN outcome='failed' THEN 1 ELSE 0 END) failures,
                   SUM(CASE WHEN outcome='follow_up_required' THEN 1 ELSE 0 END) followups
            FROM work_order GROUP BY ci_id
          ), log_stats AS (
            SELECT w.ci_id,COALESCE(SUM(l.labor_minutes),0) labor_minutes,
                   COALESCE(SUM(l.part_quantity*l.part_unit_cost),0) parts_cost
            FROM work_order w LEFT JOIN work_log l ON l.wo_id=w.wo_id GROUP BY w.ci_id
          )
          SELECT o.ci_id,c.name ci_name,c.ci_type,c.criticality,o.work_orders,o.completed,
                 o.failures,o.followups,l.labor_minutes,l.parts_cost
          FROM order_stats o JOIN log_stats l USING(ci_id) JOIN cmdb_ci c ON c.ci_id=o.ci_id
          ORDER BY o.work_orders DESC,o.failures DESC
        """).fetchall()]
    for r in rows:
        score = min(100, r["failures"] * 35 + r["followups"] * 25 +
                    max(r["work_orders"] - 1, 0) * 8 + int(r["labor_minutes"] / 120) * 4)
        if r["failures"]:
            recommendation = "Open root-cause inspection and validate the maintenance procedure."
        elif r["followups"]:
            recommendation = "Create condition-based follow-up work from the recorded outcome."
        elif r["work_orders"] >= 2:
            recommendation = "Review repeat work; consider a preventive standard-work template."
        else:
            recommendation = "Monitor; insufficient repeat evidence for new work."
        r.update(insight_score=score, recommendation=recommendation,
                 labor_hours=round(r["labor_minutes"] / 60, 1),
                 parts_cost=round(r["parts_cost"], 2))
    return rows


def create_followup_from_insight(db: Database, ci_id: str, actor: str = "analytics.engine") -> str:
    """Turn an insight into governed new work while preserving lineage."""
    insights = {r["ci_id"]: r for r in operational_insights(db)}
    if ci_id not in insights:
        raise ValueError(f"no insight data for {ci_id}")
    insight = insights[ci_id]
    with db.conn() as c:
        existing = c.execute("""SELECT wo_id FROM work_order
          WHERE ci_id=? AND source_type='analytics' AND state IN ('planned','in_progress')
          ORDER BY wo_id DESC LIMIT 1""", (ci_id,)).fetchone()
        parent = c.execute("""SELECT wo_id FROM work_order WHERE ci_id=? AND state='complete'
                              ORDER BY completed_at DESC,wo_id DESC LIMIT 1""", (ci_id,)).fetchone()
    if existing:
        return existing["wo_id"]
    parent_id = parent["wo_id"] if parent else None
    return create_work_order(
        db, f"Analytics follow-up: {insight['ci_name']}", ci_id,
        priority="high" if insight["insight_score"] >= 50 else "medium",
        assigned_to="service.operations", source_type="analytics",
        source_ref=f"ci-insight:{ci_id}:score-{insight['insight_score']}",
        parent_wo_id=parent_id,
    )
