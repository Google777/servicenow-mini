#!/usr/bin/env python3
"""CLI for the mini-ITSM: create/inspect the CMDB, and drive a Change Request
through its full lifecycle. Not a REST API + web UI (that's a real, deliberate
scope cut — see docs/NOTES.md) — this is a learning tool, not a product.

Examples:
    python -m itsm.cli seed
    python -m itsm.cli cmdb-list
    python -m itsm.cli cmdb-impact PDU-01
    python -m itsm.cli change-new "Replace UPS-01 battery string" --by dcoyhis
    python -m itsm.cli change-link CHG1000001 UPS-01
    python -m itsm.cli change-transition CHG1000001 assess --by dcoyhis
    python -m itsm.cli change-transition CHG1000001 authorize --by dcoyhis
    python -m itsm.cli change-approve CHG1000001 --approver cab-chair --approve
    python -m itsm.cli change-transition CHG1000001 scheduled --by dcoyhis
    python -m itsm.cli change-show CHG1000001
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from itsm import cmdb, change
from itsm.db import Database

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "itsm.db"


def _db() -> Database:
    DB_PATH.parent.mkdir(exist_ok=True)
    return Database(DB_PATH)


def cmd_seed(args):
    db = _db()
    cmdb.seed_small_datacenter(db)
    print(f"Seeded a small DC topology into {DB_PATH}")


def cmd_cmdb_list(args):
    db = _db()
    for ci in cmdb.list_cis(db, ci_type=args.type, site=args.site):
        print(f"{ci.ci_id:10s} {ci.name:20s} {ci.ci_type:10s} {ci.site:10s} "
              f"{ci.status:12s} crit={ci.criticality}")


def cmd_cmdb_impact(args):
    db = _db()
    impacted = cmdb.downstream_impact(db, args.ci_id)
    print(f"CIs downstream of {args.ci_id} ({len(impacted)}):")
    for ci_id in impacted:
        ci = cmdb.get_ci(db, ci_id)
        print(f"  {ci_id:10s} {ci.name if ci else '?':20s} crit={ci.criticality if ci else '?'}")


def cmd_change_new(args):
    db = _db()
    c = change.create_change(db, args.short_desc, args.by, change_type=args.type)
    print(f"Created {c.change_id} in state '{c.state}'")


def cmd_change_link(args):
    db = _db()
    change.link_ci(db, args.change_id, args.ci_id, args.by)
    print(f"Linked {args.ci_id} to {args.change_id}")


def cmd_change_transition(args):
    db = _db()
    try:
        c = change.transition(db, args.change_id, args.to_state, args.by, comment=args.comment or "")
    except (change.InvalidTransition, change.ApprovalRequired) as e:
        print(f"BLOCKED: {e}")
        return 1
    print(f"{c.change_id}: now '{c.state}'"
          + (f" (risk={c.risk}, score={c.risk_score})" if c.risk else ""))
    return 0


def cmd_change_approve(args):
    db = _db()
    if args.request:
        change.request_approval(db, args.change_id, args.approver, args.by or args.approver)
        print(f"Requested approval from {args.approver} on {args.change_id}")
    else:
        change.decide_approval(db, args.change_id, args.approver, args.approve, args.comment or "")
        print(f"{args.approver}: {'approved' if args.approve else 'rejected'} "
              f"{args.change_id}")


def cmd_change_show(args):
    db = _db()
    c = change.get_change(db, args.change_id)
    if not c:
        print("not found"); return 1
    print(f"{c.change_id}  [{c.state}]  {c.short_desc}")
    print(f"  type={c.change_type}  risk={c.risk} score={c.risk_score}  "
          f"requested_by={c.requested_by}")
    print(f"  CIs: {change.affected_cis(db, c.change_id)}")
    print(f"  Approvals: {change.approval_status(db, c.change_id)}")
    print("  Audit trail:")
    for row in change.audit_trail(db, c.change_id):
        print(f"    {row['at']}  {row['actor']:12s} {row['event']:18s} {row['detail']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mini-ITSM CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seed"); p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("cmdb-list")
    p.add_argument("--type"); p.add_argument("--site")
    p.set_defaults(fn=cmd_cmdb_list)

    p = sub.add_parser("cmdb-impact")
    p.add_argument("ci_id"); p.set_defaults(fn=cmd_cmdb_impact)

    p = sub.add_parser("change-new")
    p.add_argument("short_desc"); p.add_argument("--by", required=True)
    p.add_argument("--type", default="normal", choices=["standard", "normal", "emergency"])
    p.set_defaults(fn=cmd_change_new)

    p = sub.add_parser("change-link")
    p.add_argument("change_id"); p.add_argument("ci_id"); p.add_argument("--by", required=True)
    p.set_defaults(fn=cmd_change_link)

    p = sub.add_parser("change-transition")
    p.add_argument("change_id"); p.add_argument("to_state")
    p.add_argument("--by", required=True); p.add_argument("--comment")
    p.set_defaults(fn=cmd_change_transition)

    p = sub.add_parser("change-approve")
    p.add_argument("change_id"); p.add_argument("--approver", required=True)
    p.add_argument("--request", action="store_true")
    p.add_argument("--approve", action="store_true")
    p.add_argument("--comment"); p.add_argument("--by")
    p.set_defaults(fn=cmd_change_approve)

    p = sub.add_parser("change-show")
    p.add_argument("change_id"); p.set_defaults(fn=cmd_change_show)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
