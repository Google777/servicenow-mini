"""SQLite schema for the mini-ITSM: CMDB (Configuration Items + relationships)
and Change Requests (state machine + approvals + audit trail).

Mirrors real ServiceNow table structure at small scale:
  cmdb_ci            ~= ServiceNow's cmdb_ci* tables (Configuration Items)
  cmdb_rel           ~= cmdb_rel_ci (CI-to-CI relationships, e.g. "Depends on")
  change_request     ~= change_request table (the workflow record)
  change_ci          ~= change_request_ci (CIs affected by a change, M:N)
  change_approval    ~= sysapproval_approver (per-approver decision records)
  audit_log          ~= sys_audit (every field-level change, for compliance)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cmdb_ci (
    ci_id       TEXT PRIMARY KEY,          -- e.g. "SRV-0001"
    name        TEXT NOT NULL,
    ci_type     TEXT NOT NULL,             -- server | pdu | generator | ups | site | network
    site        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'operational',  -- operational | maintenance | retired
    criticality TEXT NOT NULL DEFAULT 'medium',        -- low | medium | high | critical
    attributes  TEXT DEFAULT '{}'          -- JSON blob, like ServiceNow's flexible CI fields
);

CREATE TABLE IF NOT EXISTS cmdb_rel (
    parent_ci   TEXT NOT NULL REFERENCES cmdb_ci(ci_id),
    child_ci    TEXT NOT NULL REFERENCES cmdb_ci(ci_id),
    rel_type    TEXT NOT NULL,             -- "Powers"|"Depends on"|"Contains"|"Cools"
    PRIMARY KEY (parent_ci, child_ci, rel_type)
);

CREATE TABLE IF NOT EXISTS change_request (
    change_id       TEXT PRIMARY KEY,       -- e.g. "CHG0001000"
    short_desc      TEXT NOT NULL,
    description     TEXT DEFAULT '',
    reason          TEXT DEFAULT '',
    category        TEXT DEFAULT 'hardware',
    priority        TEXT DEFAULT 'medium',
    change_type     TEXT NOT NULL DEFAULT 'normal',  -- standard | normal | emergency
    state           TEXT NOT NULL DEFAULT 'draft',
    risk            TEXT,                    -- low | moderate | high (set at assess)
    risk_score      INTEGER,                 -- 0-100, computed
    requested_by    TEXT NOT NULL,
    assigned_to     TEXT,
    planned_start   TEXT,
    planned_end     TEXT,
    implementation_plan TEXT DEFAULT '',
    test_plan       TEXT DEFAULT '',
    backout_plan    TEXT DEFAULT '',
    close_notes     TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_ci (
    change_id   TEXT NOT NULL REFERENCES change_request(change_id),
    ci_id       TEXT NOT NULL REFERENCES cmdb_ci(ci_id),
    PRIMARY KEY (change_id, ci_id)
);

CREATE TABLE IF NOT EXISTS change_approval (
    change_id   TEXT NOT NULL REFERENCES change_request(change_id),
    approver    TEXT NOT NULL,
    decision    TEXT NOT NULL DEFAULT 'requested',  -- requested | approved | rejected
    comment     TEXT DEFAULT '',
    decided_at  TEXT,
    PRIMARY KEY (change_id, approver)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id   TEXT NOT NULL REFERENCES change_request(change_id),
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    event       TEXT NOT NULL,       -- e.g. "state_change", "approval", "ci_linked"
    detail      TEXT NOT NULL        -- human-readable, e.g. "draft -> assess"
);

-- Closed-loop operations: planned work -> execution evidence -> analytics -> follow-up work.
CREATE TABLE IF NOT EXISTS work_order (
    wo_id           TEXT PRIMARY KEY,
    short_desc      TEXT NOT NULL,
    ci_id           TEXT NOT NULL REFERENCES cmdb_ci(ci_id),
    state           TEXT NOT NULL DEFAULT 'planned', -- planned|in_progress|complete|cancelled
    priority        TEXT NOT NULL DEFAULT 'medium',
    assigned_to     TEXT,
    source_type     TEXT NOT NULL DEFAULT 'manual', -- manual|change|analytics
    source_ref      TEXT,
    parent_wo_id    TEXT REFERENCES work_order(wo_id),
    opened_at       TEXT NOT NULL,
    due_at          TEXT,
    completed_at    TEXT,
    outcome         TEXT, -- success|failed|follow_up_required
    completion_notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS work_log (
    log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_id            TEXT NOT NULL REFERENCES work_order(wo_id),
    at               TEXT NOT NULL,
    technician       TEXT NOT NULL,
    labor_minutes    INTEGER NOT NULL DEFAULT 0,
    action            TEXT NOT NULL,
    result            TEXT DEFAULT '',
    part_number       TEXT,
    part_description  TEXT,
    part_quantity     REAL NOT NULL DEFAULT 0,
    part_unit_cost    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_work_order_ci ON work_order(ci_id, state);
CREATE INDEX IF NOT EXISTS idx_work_log_wo ON work_log(wo_id, at);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init_schema()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys=ON;")
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)
            # Lightweight forward migration for local demo databases created
            # before the record-form fields were added. Streamlit sessions are
            # ephemeral, but this keeps the CLI database compatible too.
            columns = {r["name"] for r in c.execute("PRAGMA table_info(change_request)")}
            additions = {
                "reason": "TEXT DEFAULT ''",
                "category": "TEXT DEFAULT 'hardware'",
                "priority": "TEXT DEFAULT 'medium'",
                "implementation_plan": "TEXT DEFAULT ''",
                "test_plan": "TEXT DEFAULT ''",
                "backout_plan": "TEXT DEFAULT ''",
                "close_notes": "TEXT DEFAULT ''",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    c.execute(f"ALTER TABLE change_request ADD COLUMN {name} {ddl}")
