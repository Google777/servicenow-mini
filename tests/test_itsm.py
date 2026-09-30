"""Tests for the mini-ITSM: state machine transitions + CMDB relationship
queries + the approval gate."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from itsm import cmdb, change  # noqa: E402
from itsm.db import Database  # noqa: E402


def _fresh_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(tmp.name)
    cmdb.seed_small_datacenter(db)
    return db


def test_seed_creates_expected_topology():
    db = _fresh_db()
    cis = cmdb.list_cis(db)
    assert len(cis) == 1 + 2 + 2 + 4 + 8 + 1  # site+gens+ups+pdus+servers+switch
    srv = cmdb.get_ci(db, "SRV-0001")
    assert srv is not None and srv.criticality == "critical"


def test_downstream_impact_walks_power_chain():
    db = _fresh_db()
    impacted = cmdb.downstream_impact(db, "GEN-01")
    # GEN-01 -> UPS-01 -> PDU-01,PDU-02 -> SRV-0001..0004
    assert "UPS-01" in impacted
    assert "PDU-01" in impacted and "PDU-02" in impacted
    assert "SRV-0001" in impacted and "SRV-0004" in impacted
    assert "SRV-0005" not in impacted  # that's on UPS-02's chain
    assert "GEN-02" not in impacted    # sibling, not downstream


def test_invalid_transition_rejected():
    db = _fresh_db()
    c = change.create_change(db, "Test change", "alice")
    try:
        change.transition(db, c.change_id, "scheduled", "alice")  # skip states
        assert False, "expected InvalidTransition"
    except change.InvalidTransition:
        pass


def test_scheduling_requires_full_approval():
    db = _fresh_db()
    c = change.create_change(db, "Replace UPS-01 battery string", "alice",
                             change_type="normal")
    change.link_ci(db, c.change_id, "UPS-01", "alice")
    change.transition(db, c.change_id, "assess", "alice")
    c = change.transition(db, c.change_id, "authorize", "alice")
    assert c.risk in ("low", "moderate", "high")

    # No approvals yet -> blocked
    try:
        change.transition(db, c.change_id, "scheduled", "alice")
        assert False, "expected ApprovalRequired"
    except change.ApprovalRequired:
        pass

    change.request_approval(db, c.change_id, "cab-chair", "alice")
    change.decide_approval(db, c.change_id, "cab-chair", approve=True)
    c = change.transition(db, c.change_id, "scheduled", "alice")
    assert c.state == "scheduled"


def test_rejected_approval_blocks_scheduling_even_if_others_approve():
    db = _fresh_db()
    c = change.create_change(db, "Risky network change", "bob", change_type="emergency")
    change.link_ci(db, c.change_id, "NET-01", "bob")
    change.transition(db, c.change_id, "assess", "bob")
    change.transition(db, c.change_id, "authorize", "bob")
    change.request_approval(db, c.change_id, "cab-chair", "bob")
    change.request_approval(db, c.change_id, "site-lead", "bob")
    change.decide_approval(db, c.change_id, "cab-chair", approve=True)
    change.decide_approval(db, c.change_id, "site-lead", approve=False, comment="too risky")
    try:
        change.transition(db, c.change_id, "scheduled", "bob")
        assert False, "expected ApprovalRequired due to rejection"
    except change.ApprovalRequired:
        pass


def test_risk_score_reflects_criticality_and_blast_radius():
    db = _fresh_db()
    # Touching a generator (feeds everything) should score higher than a
    # single low-criticality-adjacent server change.
    c_gen = change.create_change(db, "Generator maintenance", "alice", change_type="normal")
    change.link_ci(db, c_gen.change_id, "GEN-01", "alice")
    label_gen, score_gen = change.compute_risk(db, c_gen.change_id)

    c_srv = change.create_change(db, "Patch one app server", "alice", change_type="standard")
    change.link_ci(db, c_srv.change_id, "SRV-0007", "alice")  # medium criticality, few deps
    label_srv, score_srv = change.compute_risk(db, c_srv.change_id)

    assert score_gen > score_srv
    assert label_gen in ("moderate", "high")


def test_audit_trail_records_every_transition():
    db = _fresh_db()
    c = change.create_change(db, "Audit trail check", "alice")
    change.link_ci(db, c.change_id, "SRV-0001", "alice")
    change.transition(db, c.change_id, "assess", "alice")
    change.transition(db, c.change_id, "authorize", "alice")
    trail = change.audit_trail(db, c.change_id)
    events = [r["event"] for r in trail]
    assert events == ["created", "ci_linked", "state_change", "state_change"]


def test_full_lifecycle_to_closed():
    db = _fresh_db()
    c = change.create_change(db, "Full lifecycle smoke test", "alice", change_type="standard")
    change.link_ci(db, c.change_id, "PDU-03", "alice")
    change.transition(db, c.change_id, "assess", "alice")
    change.transition(db, c.change_id, "authorize", "alice")
    change.request_approval(db, c.change_id, "cab-chair", "alice")
    change.decide_approval(db, c.change_id, "cab-chair", approve=True)
    change.transition(db, c.change_id, "scheduled", "alice")
    change.transition(db, c.change_id, "implement", "alice")
    change.transition(db, c.change_id, "review", "alice")
    c = change.transition(db, c.change_id, "closed", "alice")
    assert c.state == "closed"
    assert change.TRANSITIONS["closed"] == set()  # terminal


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(fns)-failed}/{len(fns)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
