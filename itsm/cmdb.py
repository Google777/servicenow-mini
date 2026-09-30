"""CMDB layer: Configuration Items + relationships + seed data.

A real ServiceNow CMDB (per the CSDM — Common Service Data Model) organizes CIs
by class (cmdb_ci_server, cmdb_ci_ups, ...) and links them with typed
relationships (cmdb_rel_ci) so impact analysis can walk the dependency graph —
"if this PDU goes down, which servers lose power?" This module is a small,
honest analogue: one CI table with a `ci_type` discriminator, one relationship
table, and a graph query that mirrors ServiceNow's "Dependency Views" / impact
analysis.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from itsm.db import Database

CI_TYPES = ("site", "generator", "ups", "pdu", "server", "network")


@dataclass
class CI:
    ci_id: str
    name: str
    ci_type: str
    site: str
    status: str
    criticality: str
    attributes: dict


def _row_to_ci(row) -> CI:
    return CI(row["ci_id"], row["name"], row["ci_type"], row["site"],
              row["status"], row["criticality"], json.loads(row["attributes"]))


def upsert_ci(db: Database, ci_id: str, name: str, ci_type: str, site: str,
              criticality: str = "medium", status: str = "operational",
              attributes: dict | None = None) -> None:
    if ci_type not in CI_TYPES:
        raise ValueError(f"unknown ci_type {ci_type!r}; must be one of {CI_TYPES}")
    with db.conn() as c:
        c.execute(
            """INSERT INTO cmdb_ci (ci_id, name, ci_type, site, status, criticality, attributes)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ci_id) DO UPDATE SET
                 name=excluded.name, ci_type=excluded.ci_type, site=excluded.site,
                 status=excluded.status, criticality=excluded.criticality,
                 attributes=excluded.attributes""",
            (ci_id, name, ci_type, site, status, criticality,
             json.dumps(attributes or {})),
        )


def link(db: Database, parent_ci: str, child_ci: str, rel_type: str) -> None:
    """Record a typed relationship, e.g. link(db, 'GEN-01', 'PDU-01', 'Powers')."""
    with db.conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO cmdb_rel (parent_ci, child_ci, rel_type) VALUES (?, ?, ?)",
            (parent_ci, child_ci, rel_type),
        )


def get_ci(db: Database, ci_id: str) -> CI | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM cmdb_ci WHERE ci_id=?", (ci_id,)).fetchone()
        return _row_to_ci(row) if row else None


def list_cis(db: Database, ci_type: str | None = None, site: str | None = None) -> list[CI]:
    q = "SELECT * FROM cmdb_ci"
    clauses, params = [], []
    if ci_type:
        clauses.append("ci_type=?"); params.append(ci_type)
    if site:
        clauses.append("site=?"); params.append(site)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY ci_id"
    with db.conn() as c:
        return [_row_to_ci(r) for r in c.execute(q, params).fetchall()]


def downstream_impact(db: Database, ci_id: str, _seen: set[str] | None = None) -> list[str]:
    """Walk the relationship graph forward (parent->child) to find everything
    that depends on ``ci_id`` — the mini-analogue of ServiceNow's CI
    'Dependency Views' / impact analysis used during Change risk assessment:
    "if I touch this CI, what else is affected?"
    """
    seen = _seen if _seen is not None else set()
    with db.conn() as c:
        children = [r["child_ci"] for r in c.execute(
            "SELECT child_ci FROM cmdb_rel WHERE parent_ci=?", (ci_id,)).fetchall()]
    impacted = []
    for child in children:
        if child in seen:
            continue
        seen.add(child)
        impacted.append(child)
        impacted.extend(downstream_impact(db, child, seen))
    return impacted


def seed_small_datacenter(db: Database) -> None:
    """Seed a small, realistic DC topology: 1 site -> 2 generators -> 2 UPS ->
    4 PDUs -> 8 servers, plus a network switch. Mirrors the kind of
    power-chain CMDB relationships real DC Business/Service Operations teams
    reason about during change risk assessment.
    """
    upsert_ci(db, "SITE-DEN1", "Denver DC1", "site", "Denver", criticality="critical")
    upsert_ci(db, "GEN-01", "Generator 1", "generator", "Denver", criticality="critical")
    upsert_ci(db, "GEN-02", "Generator 2", "generator", "Denver", criticality="critical")
    upsert_ci(db, "UPS-01", "UPS Bank 1", "ups", "Denver", criticality="high")
    upsert_ci(db, "UPS-02", "UPS Bank 2", "ups", "Denver", criticality="high")
    upsert_ci(db, "NET-01", "Core Switch 1", "network", "Denver", criticality="high")

    link(db, "SITE-DEN1", "GEN-01", "Contains")
    link(db, "SITE-DEN1", "GEN-02", "Contains")
    link(db, "GEN-01", "UPS-01", "Powers")
    link(db, "GEN-02", "UPS-02", "Powers")

    for i in range(1, 5):
        pdu = f"PDU-{i:02d}"
        ups = "UPS-01" if i <= 2 else "UPS-02"
        upsert_ci(db, pdu, f"PDU {i}", "pdu", "Denver", criticality="high")
        link(db, ups, pdu, "Powers")

    for i in range(1, 9):
        srv = f"SRV-{i:04d}"
        pdu = f"PDU-{((i - 1) // 2) + 1:02d}"
        crit = "critical" if i <= 2 else "medium"
        upsert_ci(db, srv, f"App Server {i}", "server", "Denver", criticality=crit)
        link(db, pdu, srv, "Powers")
        link(db, "NET-01", srv, "Depends on")
