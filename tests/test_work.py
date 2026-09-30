"""Tests for the closed-loop work-order and analytics model."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from itsm import cmdb, work  # noqa: E402
from itsm.db import Database  # noqa: E402
from itsm.demo import seed_demo  # noqa: E402


def _db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(tmp.name)
    seed_demo(db)
    return db


def test_demo_seeds_execution_history():
    db = _db()
    orders = work.list_work_orders(db)
    assert len(orders) == 6
    assert any(r["outcome"] == "failed" for r in orders)
    assert any(r["parts_cost"] > 0 for r in orders)
    assert any(r["state"] == "in_progress" for r in orders)


def test_work_lifecycle_and_execution_evidence():
    db = _db()
    wo = work.create_work_order(db, "Inspect PDU-02", "PDU-02", assigned_to="ops")
    work.transition(db, wo, "in_progress", "tech")
    work.log_execution(db, wo, "tech", "Thermal inspection", labor_minutes=45,
                       part_number="SENSOR-1", part_quantity=2, part_unit_cost=50,
                       result="Two sensors replaced")
    work.complete(db, wo, "tech", "success", "Validation passed")
    record = work.get_work_order(db, wo)
    summary = next(r for r in work.list_work_orders(db) if r["wo_id"] == wo)
    assert record["state"] == "complete" and record["outcome"] == "success"
    assert summary["labor_minutes"] == 45
    assert summary["parts_cost"] == 100


def test_invalid_work_transition_is_blocked():
    db = _db()
    wo = work.create_work_order(db, "Inspect server", "SRV-0001")
    try:
        work.transition(db, wo, "complete", "tech")
        assert False, "expected invalid transition"
    except ValueError:
        pass


def test_insights_do_not_double_count_outcomes():
    db = _db()
    insight = next(r for r in work.operational_insights(db) if r["ci_id"] == "PDU-01")
    assert insight["work_orders"] == 2
    assert insight["failures"] == 1
    assert insight["completed"] == 2
    assert insight["parts_cost"] == 950


def test_analytics_creates_idempotent_followup_with_lineage():
    db = _db()
    wo1 = work.create_followup_from_insight(db, "PDU-01")
    wo2 = work.create_followup_from_insight(db, "PDU-01")
    assert wo1 == wo2
    record = work.get_work_order(db, wo1)
    assert record["source_type"] == "analytics"
    assert record["source_ref"].startswith("ci-insight:PDU-01")
    assert record["parent_wo_id"] is not None
    assert record["state"] == "planned"


def _run():
    tests = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed=0
    for fn in tests:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1; print(f"FAIL {fn.__name__}: {exc!r}")
    print(f"\n{len(tests)-failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
