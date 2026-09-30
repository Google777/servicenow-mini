"""Polished Streamlit console for the Mini-ITSM interview demo.

The UI is intentionally thin: all governance rules live in ``itsm.change`` so
a button cannot bypass the same state machine exercised by the CLI and tests.
Each browser session gets its own temporary SQLite database, preventing public
visitors from changing another visitor's demo state.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from itsm import change, cmdb
from itsm.db import Database
from itsm.demo import seed_demo

STATE_ORDER = ["draft", "assess", "authorize", "scheduled", "implement", "review", "closed"]
STATE_LABELS = {s: s.title() for s in STATE_ORDER}
RISK_COLORS = {"low": "#42d392", "moderate": "#ffb454", "high": "#ff6464", None: "#7f8c9a"}


def _inject_css() -> None:
    st.markdown("""
    <style>
      .stApp { background: linear-gradient(145deg,#081019 0%,#0d1823 48%,#0a1119 100%); }
      [data-testid="stHeader"] { background: rgba(8,16,25,.72); }
      .block-container { max-width: 1240px; padding-top: 1.4rem; }
      h1,h2,h3 { letter-spacing:-.025em; }
      .eyebrow { color:#56d4ff; text-transform:uppercase; letter-spacing:.16em;
                 font:700 .72rem/1.2 ui-monospace,monospace; }
      .hero { border:1px solid #203648; border-radius:16px; padding:20px 24px;
              background:linear-gradient(135deg,rgba(14,35,52,.95),rgba(11,24,36,.85));
              box-shadow:0 18px 55px rgba(0,0,0,.22); margin-bottom:16px; }
      .hero-title { font-size:2rem; font-weight:760; margin:.2rem 0 .35rem; color:#f0f7fb; }
      .hero-copy { color:#a9becd; max-width:850px; line-height:1.5; }
      .badge { display:inline-block; padding:4px 9px; border-radius:999px;
               background:#13293a; border:1px solid #2b5068; color:#78dcff;
               font:650 .74rem ui-monospace,monospace; margin-right:5px; }
      div[data-testid="stMetric"] { background:#0e1d29; border:1px solid #20394a;
              border-radius:13px; padding:12px 15px; }
      div[data-testid="stMetricValue"] { color:#f4f9fc; }
      .risk { padding:3px 8px; border-radius:999px; font-weight:700; font-size:.76rem; }
      .step { display:inline-block; padding:6px 9px; border-radius:7px; margin:2px;
              font:650 .72rem ui-monospace,monospace; border:1px solid #284152; color:#8ca4b5; }
      .step.done { background:#123526; color:#62dfa1; border-color:#246044; }
      .step.now { background:#123c50; color:#78dcff; border-color:#28708e; }
      .step.future { background:#111d27; color:#607686; }
      .callout { border-left:3px solid #56d4ff; background:#0e1d29; padding:10px 13px;
                 border-radius:0 8px 8px 0; color:#aec2cf; margin:.4rem 0 1rem; }
      .stTabs [data-baseweb="tab-list"] { gap:8px; }
      .stTabs [data-baseweb="tab"] { background:#0e1d29; border-radius:8px; padding:8px 14px; }
      footer { visibility:hidden; }
    </style>
    """, unsafe_allow_html=True)


def _session_db() -> Database:
    if "itsm_db_path" not in st.session_state:
        token = uuid.uuid4().hex
        st.session_state.itsm_db_path = str(Path(tempfile.gettempdir()) / f"mini_itsm_{token}.db")
    db = Database(st.session_state.itsm_db_path)
    seed_demo(db)
    return db


def _reset_demo() -> None:
    path = st.session_state.get("itsm_db_path")
    if path:
        Path(path).unlink(missing_ok=True)
    st.session_state.pop("itsm_db_path", None)
    st.session_state.pop("selected_change", None)


def _query(db: Database, sql: str, params=()) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _change_rows(db: Database) -> list[dict]:
    return _query(db, """
        SELECT c.change_id, c.short_desc, c.change_type, c.state, c.risk,
               c.risk_score, c.requested_by, COUNT(DISTINCT cc.ci_id) affected_cis
        FROM change_request c
        LEFT JOIN change_ci cc ON cc.change_id=c.change_id
        GROUP BY c.change_id ORDER BY c.change_id
    """)


def _state_ribbon(state: str) -> str:
    if state == "cancelled":
        return '<span class="step now">Cancelled</span>'
    current = STATE_ORDER.index(state) if state in STATE_ORDER else -1
    html = []
    for i, s in enumerate(STATE_ORDER):
        cls = "done" if i < current else "now" if i == current else "future"
        html.append(f'<span class="step {cls}">{STATE_LABELS[s]}</span>')
    return " ".join(html)


def _metrics(db: Database) -> None:
    cis = _query(db, "SELECT COUNT(*) n FROM cmdb_ci")[0]["n"]
    rels = _query(db, "SELECT COUNT(*) n FROM cmdb_rel")[0]["n"]
    active = _query(db, "SELECT COUNT(*) n FROM change_request WHERE state NOT IN ('closed','cancelled')")[0]["n"]
    pending = _query(db, "SELECT COUNT(*) n FROM change_approval WHERE decision='requested'")[0]["n"]
    high = _query(db, "SELECT COUNT(*) n FROM change_request WHERE risk='high' AND state NOT IN ('closed','cancelled')")[0]["n"]
    cols = st.columns(5)
    for col, label, value, help_text in zip(cols,
            ["Configuration items", "CI relationships", "Active changes", "Pending approvals", "High-risk active"],
            [cis, rels, active, pending, high],
            ["CMDB records", "Typed dependency edges", "Not closed/cancelled", "Approval decisions outstanding", "High blast-radius changes"]):
        col.metric(label, value, help=help_text)


def _overview(db: Database) -> None:
    _metrics(db)
    st.subheader("Change queue")
    rows = _change_rows(db)
    display = pd.DataFrame(rows).rename(columns={
        "change_id": "Change", "short_desc": "Description", "change_type": "Type",
        "state": "State", "risk": "Risk", "risk_score": "Score",
        "requested_by": "Requested by", "affected_cis": "CIs",
    })
    st.dataframe(display, width="stretch", hide_index=True,
                 column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100)})
    st.markdown("""<div class="callout"><b>Demo scenario:</b> CHG1000001 is deliberately
    parked in <b>Authorize</b>. Its UPS dependency graph produces a high risk score, and the
    workflow will refuse to schedule it until CAB approval is recorded.</div>""",
                unsafe_allow_html=True)


def _cmdb_tab(db: Database) -> None:
    left, right = st.columns([0.42, 0.58], gap="large")
    cis = cmdb.list_cis(db)
    ids = [c.ci_id for c in cis]
    labels = {c.ci_id: f"{c.ci_id} · {c.name}" for c in cis}
    with left:
        st.subheader("Configuration item")
        selected = st.selectbox("Select a CI", ids, format_func=lambda x: labels[x], index=ids.index("UPS-01"))
        ci = cmdb.get_ci(db, selected)
        if ci:
            st.markdown(f"**Type:** `{ci.ci_type}`  \n**Site:** {ci.site}  \n"
                        f"**Status:** {ci.status}  \n**Criticality:** :{RISK_COLORS.get(ci.criticality, '#aaa')}[{ci.criticality.upper()}]")
        impact = cmdb.downstream_impact(db, selected)
        st.metric("Downstream blast radius", len(impact))
        if impact:
            impacted = [cmdb.get_ci(db, i) for i in impact]
            st.dataframe(pd.DataFrame([{"CI": x.ci_id, "Name": x.name, "Type": x.ci_type,
                                       "Criticality": x.criticality} for x in impacted if x]),
                         width="stretch", hide_index=True)
        else:
            st.info("No downstream CIs. This is a leaf node.")
    with right:
        st.subheader("Dependency view")
        relationships = _query(db, """
            SELECT r.parent_ci, p.name parent_name, r.rel_type,
                   r.child_ci, c.name child_name
            FROM cmdb_rel r
            JOIN cmdb_ci p ON p.ci_id=r.parent_ci
            JOIN cmdb_ci c ON c.ci_id=r.child_ci
            ORDER BY r.parent_ci, r.child_ci
        """)
        dot = ["digraph G {", 'rankdir="LR";', 'bgcolor="transparent";',
               'node [shape=box style="rounded,filled" fillcolor="#13293a" fontcolor="#e8f4fa" color="#2b5068"];',
               'edge [color="#56d4ff" fontcolor="#8cb9cd" fontsize=9];']
        for r in relationships:
            color = "#ffb454" if r["parent_ci"] == selected or r["child_ci"] == selected else "#56d4ff"
            dot.append(f'"{r["parent_ci"]}" -> "{r["child_ci"]}" [label="{r["rel_type"]}" color="{color}"];')
        dot.append("}")
        st.graphviz_chart("\n".join(dot), width="stretch")
        st.caption("Typed, directional relationships provide the operational context for impact and risk analysis.")


def _workflow_tab(db: Database) -> None:
    rows = _change_rows(db)
    ids = [r["change_id"] for r in rows]
    labels = {r["change_id"]: f"{r['change_id']} · {r['short_desc']}" for r in rows}
    default = st.session_state.get("selected_change", "CHG1000001")
    if default not in ids:
        default = ids[0]
    selected = st.selectbox("Select a change", ids, index=ids.index(default),
                            format_func=lambda x: labels[x], key="workflow_change")
    st.session_state.selected_change = selected
    cr = change.get_change(db, selected)
    raw = _query(db, "SELECT * FROM change_request WHERE change_id=?", (selected,))[0]

    st.markdown(_state_ribbon(cr.state), unsafe_allow_html=True)
    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("State", cr.state.title())
    c2.metric("Risk", (cr.risk or "Not assessed").title())
    c3.metric("Risk score", cr.risk_score if cr.risk_score is not None else "—")
    c4.metric("Affected CIs", len(change.affected_cis(db, selected)))
    st.caption(raw.get("description") or "No long description")

    left, right = st.columns([0.58, 0.42], gap="large")
    with left:
        st.subheader("Governed actions")
        allowed = sorted(change.TRANSITIONS.get(cr.state, set()),
                         key=lambda s: (STATE_ORDER.index(s) if s in STATE_ORDER else 99))
        if not allowed:
            st.success("Terminal state — no further transitions allowed.")
        else:
            cols = st.columns(min(len(allowed), 3))
            for i, target in enumerate(allowed):
                if cols[i % len(cols)].button(f"Move to {target.title()}", key=f"to_{selected}_{target}",
                                              type="primary" if target != "cancelled" else "secondary",
                                              width="stretch"):
                    try:
                        change.transition(db, selected, target, "demo.user")
                        st.success(f"Transitioned to {target.title()}.")
                        st.rerun()
                    except (change.InvalidTransition, change.ApprovalRequired) as exc:
                        st.error(f"Governance gate blocked the action: {exc}")
        if cr.state == "authorize":
            st.info("Try **Move to Scheduled** before approving CHG1000001. The backend—not the UI—will block it.")

        st.subheader("Link another CI")
        linked = set(change.affected_cis(db, selected))
        available = [c for c in cmdb.list_cis(db) if c.ci_id not in linked]
        if available:
            with st.form(f"link_ci_{selected}"):
                ci_id = st.selectbox("Configuration item", [c.ci_id for c in available],
                                     format_func=lambda x: next(f"{c.ci_id} · {c.name}" for c in available if c.ci_id == x))
                if st.form_submit_button("Link CI"):
                    change.link_ci(db, selected, ci_id, "demo.user")
                    st.rerun()

    with right:
        st.subheader("Approvals")
        approvals = change.approval_status(db, selected)
        if approvals:
            for approver, decision in approvals.items():
                icon = {"approved": "✅", "rejected": "⛔", "requested": "⏳"}.get(decision, "•")
                st.write(f"{icon} **{approver}** — {decision}")
                if decision == "requested":
                    a, b = st.columns(2)
                    if a.button("Approve", key=f"approve_{selected}_{approver}", width="stretch"):
                        change.decide_approval(db, selected, approver, True,
                                               "Approved in the interview demo.")
                        st.rerun()
                    if b.button("Reject", key=f"reject_{selected}_{approver}", width="stretch"):
                        change.decide_approval(db, selected, approver, False,
                                               "Rejected in the interview demo.")
                        st.rerun()
        else:
            st.warning("No approval records yet.")
        if cr.state in ("assess", "authorize"):
            with st.form(f"approval_{selected}"):
                approver = st.text_input("Approver", "cab-chair")
                if st.form_submit_button("Request approval"):
                    change.request_approval(db, selected, approver.strip() or "cab-chair", "demo.user")
                    st.rerun()

        st.subheader("Create change")
        with st.expander("Open a new draft"):
            with st.form("create_change"):
                desc = st.text_input("Short description", "Inspect PDU-03 breaker settings")
                ctype = st.selectbox("Type", ["normal", "standard", "emergency"])
                requester = st.text_input("Requested by", "demo.user")
                if st.form_submit_button("Create draft"):
                    created = change.create_change(db, desc, requester, change_type=ctype)
                    st.session_state.selected_change = created.change_id
                    st.success(f"Created {created.change_id}")
                    st.rerun()


def _audit_tab(db: Database) -> None:
    rows = _change_rows(db)
    ids = [r["change_id"] for r in rows]
    selected = st.selectbox("Change", ids, format_func=lambda x: f"{x} · {next(r['short_desc'] for r in rows if r['change_id']==x)}",
                            key="audit_change")
    trail = change.audit_trail(db, selected)
    st.subheader(f"Immutable audit timeline · {selected}")
    st.dataframe(pd.DataFrame(trail).rename(columns={"at": "Timestamp (UTC)", "actor": "Actor",
                                                     "event": "Event", "detail": "Detail"}),
                 width="stretch", hide_index=True)
    st.caption("Every state transition, CI link, and approval decision is reconstructable after the fact.")


def _about_tab() -> None:
    st.subheader("What this demonstrates")
    st.markdown("""
    - **CMDB records and typed relationships** model operational context.
    - **Dependency impact analysis** turns a selected CI into an explainable blast radius.
    - **A server-side state machine** enforces legal change transitions.
    - **Risk scoring** combines CI criticality, blast radius, and change type.
    - **Approval records** form a real governance gate—not a checkbox in the UI.
    - **Audit history** records actor, time, event, and detail.

    ### Deliberate scope honesty
    This is a learning analogue, **not a ServiceNow replacement**. Real ServiceNow
    includes a CMDB class hierarchy, discovery/reconciliation, Flow Designer,
    role-based access, notifications, SLA timers, integrations, and a mature UI.
    This project isolates the governing mechanics most relevant to operational
    change control and makes them concrete, testable, and discussable.
    """)
    st.code("python3 tests/test_itsm.py   # 8 backend tests", language="bash")


def main() -> None:
    st.set_page_config(page_title="Change Control Command Center", page_icon="⚙️",
                       layout="wide", initial_sidebar_state="collapsed")
    _inject_css()
    db = _session_db()

    st.markdown("""
    <div class="hero">
      <div class="eyebrow">Mini-ITSM · Interview Engineering Demo</div>
      <div class="hero-title">Change Control Command Center</div>
      <div class="hero-copy">A scoped ServiceNow analogue: CMDB dependency context,
      explainable change risk, enforced approval gates, and an immutable audit trail—
      modeled on a small data-center power chain.</div><br/>
      <span class="badge">CMDB</span><span class="badge">Change Management</span>
      <span class="badge">Impact Analysis</span><span class="badge">Governance</span>
    </div>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.header("Demo controls")
        st.caption("Each browser session has an isolated SQLite database.")
        if st.button("Reset deterministic demo", width="stretch"):
            _reset_demo()
            st.rerun()
        st.divider()
        st.caption("Built as a focused learning analogue—not a clone of the full Now Platform.")

    tabs = st.tabs(["Operations overview", "CMDB impact", "Change workflow", "Audit trail", "Architecture & scope"])
    with tabs[0]: _overview(db)
    with tabs[1]: _cmdb_tab(db)
    with tabs[2]: _workflow_tab(db)
    with tabs[3]: _audit_tab(db)
    with tabs[4]: _about_tab()
