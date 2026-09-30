"""Service-operations workspace for the Mini-ITSM interview demo.

This is a record-centric UI analogue—not a pixel clone of ServiceNow. The UI
is intentionally thin: all workflow and approval rules remain in
``itsm.change`` and are shared with the CLI/tests. Every browser session uses
an isolated temporary SQLite database.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from itsm import change, cmdb, work
from itsm.db import Database
from itsm.demo import seed_demo

STATE_ORDER = ["draft", "assess", "authorize", "scheduled", "implement", "review", "closed"]
STATE_LABELS = {s: s.title() for s in STATE_ORDER}
RISK_COLOR = {"low": "#2e844a", "moderate": "#a15c00", "high": "#ba0517", None: "#5c6b73"}


def _css() -> None:
    st.markdown("""
    <style>
      .stApp { background:radial-gradient(circle at 75% 0%,#122c3a 0,#09151f 34%,#071019 100%); color:#e8f2f7; }
      [data-testid="stSidebar"] { background:linear-gradient(180deg,#0d2330,#081721); border-right:1px solid #1c3a49; }
      [data-testid="stSidebar"] * { color:#eaf3f7 !important; }
      [data-testid="stHeader"] { background:rgba(7,16,25,.9); }
      .block-container { max-width:1400px; padding-top:1.15rem; padding-bottom:2rem; }
      h1,h2,h3 { color:#edf7fb; letter-spacing:-.02em; }
      p,li { color:#bdced7; }
      .brand { color:white; font-size:1.15rem; font-weight:780; line-height:1.15; margin:.2rem 0; }
      .brand-sub { color:#56d4ff; font:650 .66rem ui-monospace,monospace; letter-spacing:.13em; text-transform:uppercase; }
      .breadcrumb { color:#6d9bad; font-size:.78rem; margin-bottom:.15rem; }
      .record-head { background:#0d1e29; border:1px solid #204152; border-radius:10px; padding:15px 18px;
                     box-shadow:0 8px 24px rgba(0,0,0,.2); margin-bottom:10px; }
      .record-number { font:700 .73rem ui-monospace,monospace; color:#65cbe8; letter-spacing:.08em; }
      .record-title { font-size:1.45rem; font-weight:760; color:#f2f8fb; margin:.18rem 0 .5rem; }
      .pill { display:inline-block; border-radius:999px; padding:3px 8px; margin-right:5px;
              font:700 .68rem ui-monospace,monospace; border:1px solid #315364; background:#142b38; color:#a9d9e8; }
      .pill-risk { color:white; border:0; }
      .step { display:inline-block; padding:5px 8px; margin:2px 1px; border-radius:4px;
              font:700 .66rem ui-monospace,monospace; border:1px solid #274454; }
      .step.done { background:#103524; border-color:#246443; color:#63dda0; }
      .step.now { background:#12384a; border-color:#3187a2; color:#77dcf5; }
      .step.future { background:#0b1922; color:#617e8c; }
      .list-card { background:#0d1e29; border:1px solid #203e4d; border-radius:9px; padding:11px 13px; margin:5px 0; }
      .callout { background:#0c2b38; border-left:4px solid #39bddd; border-radius:4px;
                 padding:10px 12px; color:#b9dce7; margin:.5rem 0 1rem; }
      .loop { display:flex; gap:6px; flex-wrap:wrap; margin:.4rem 0 1rem; }
      .loop-step { flex:1; min-width:145px; background:#0d1e29; border:1px solid #204152;
                   border-radius:8px; padding:10px; color:#bed3dc; }
      .loop-step b { color:#56d4ff; font-family:ui-monospace,monospace; }
      div[data-testid="stMetric"] { background:#0d1e29; border:1px solid #203e4d; border-radius:9px; padding:10px 13px; }
      .stTabs [data-baseweb="tab-list"] { gap:3px; border-bottom:1px solid #284653; }
      .stTabs [data-baseweb="tab"] { padding:8px 13px; }
      [data-testid="stForm"] { background:#0d1e29; border:1px solid #203e4d; border-radius:9px; padding:12px; }
      footer { visibility:hidden; }
    </style>
    """, unsafe_allow_html=True)


def _db() -> Database:
    if "db_path" not in st.session_state:
        st.session_state.db_path = str(Path(tempfile.gettempdir()) / f"mini_itsm_{uuid.uuid4().hex}.db")
    db = Database(st.session_state.db_path)
    seed_demo(db)
    return db


def _reset() -> None:
    if st.session_state.get("db_path"):
        Path(st.session_state.db_path).unlink(missing_ok=True)
    for key in ("db_path", "selected_change", "selected_wo", "show_new"):
        st.session_state.pop(key, None)


def _query(db: Database, sql: str, params=()) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _changes(db: Database) -> list[dict]:
    return _query(db, """
      SELECT c.change_id,c.short_desc,c.description,c.change_type,c.state,c.risk,c.risk_score,
             c.priority,c.category,c.requested_by,c.assigned_to,c.planned_start,c.planned_end,
             COUNT(DISTINCT cc.ci_id) affected_cis
      FROM change_request c LEFT JOIN change_ci cc ON cc.change_id=c.change_id
      GROUP BY c.change_id ORDER BY CASE c.state WHEN 'authorize' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END,c.change_id
    """)


def _ribbon(state: str) -> str:
    if state == "cancelled":
        return '<span class="step now">Cancelled</span>'
    here = STATE_ORDER.index(state) if state in STATE_ORDER else -1
    return "".join(f'<span class="step {"done" if i < here else "now" if i == here else "future"}">{STATE_LABELS[s]}</span>'
                   for i, s in enumerate(STATE_ORDER))


def _risk_pill(risk: str | None, score=None) -> str:
    label = (risk or "not assessed").upper()
    suffix = f" · {score}" if score is not None else ""
    return f'<span class="pill pill-risk" style="background:{RISK_COLOR.get(risk, RISK_COLOR[None])}">{label}{suffix}</span>'


def _page_header(area: str, title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="breadcrumb">Service Operations / {area}</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def _summary_metrics(db: Database) -> None:
    values = [
        ("Active changes", "SELECT COUNT(*) n FROM change_request WHERE state NOT IN ('closed','cancelled')"),
        ("Awaiting approval", "SELECT COUNT(*) n FROM change_approval WHERE decision='requested'"),
        ("High risk", "SELECT COUNT(*) n FROM change_request WHERE risk='high' AND state NOT IN ('closed','cancelled')"),
        ("CMDB items", "SELECT COUNT(*) n FROM cmdb_ci"),
        ("Dependencies", "SELECT COUNT(*) n FROM cmdb_rel"),
    ]
    cols = st.columns(len(values))
    for col, (label, sql) in zip(cols, values):
        col.metric(label, _query(db, sql)[0]["n"])


def _my_work(db: Database) -> None:
    _page_header("Workspace", "My Work", "Operational priorities, approvals, and change health")
    _summary_metrics(db)
    left, right = st.columns([.64, .36], gap="large")
    with left:
        st.subheader("Open change work")
        frame = pd.DataFrame(_changes(db))
        frame = frame[~frame.state.isin(["closed", "cancelled"])].rename(columns={
            "change_id":"Number", "short_desc":"Short description", "state":"State", "risk":"Risk",
            "priority":"Priority", "assigned_to":"Assigned to", "planned_start":"Planned start"})
        st.dataframe(frame[["Number","Short description","State","Risk","Priority","Assigned to","Planned start"]],
                     width="stretch", hide_index=True)
    with right:
        st.subheader("Attention required")
        pending = _query(db, """SELECT a.change_id,c.short_desc,a.approver,a.decision
                                  FROM change_approval a JOIN change_request c USING(change_id)
                                  WHERE a.decision='requested'""")
        if pending:
            for item in pending:
                st.markdown(f"""<div class="list-card"><span class="record-number">{item['change_id']}</span><br/>
                <b>{item['short_desc']}</b><br/><small>Approval pending · {item['approver']}</small></div>""",
                            unsafe_allow_html=True)
        st.markdown("""<div class="callout"><b>Suggested demo:</b> open CHG1000001 in
        Changes, attempt to schedule it, then approve and retry. The backend enforces the gate.</div>""",
                    unsafe_allow_html=True)


def _save_record(db: Database, record: dict, values: dict) -> None:
    changed = change.update_fields(db, record["change_id"], "demo.user", **values)
    if changed:
        st.success("Saved: " + ", ".join(changed))
    else:
        st.info("No field values changed.")


def _governed_actions(db: Database, cr: change.ChangeRequest) -> None:
    allowed = sorted(change.TRANSITIONS.get(cr.state, set()),
                     key=lambda x: STATE_ORDER.index(x) if x in STATE_ORDER else 99)
    if not allowed:
        st.success("Terminal state — no further transitions are permitted.")
        return
    cols = st.columns(min(3, len(allowed)))
    for i, target in enumerate(allowed):
        if cols[i % len(cols)].button(f"Move to {target.title()}", key=f"action_{cr.change_id}_{target}",
                                      type="primary" if target != "cancelled" else "secondary", width="stretch"):
            try:
                change.transition(db, cr.change_id, target, "demo.user")
                st.rerun()
            except (change.InvalidTransition, change.ApprovalRequired) as exc:
                st.error(f"Governance gate blocked this transition: {exc}")


def _affected_ci_panel(db: Database, change_id: str) -> None:
    linked = change.affected_cis(db, change_id)
    if linked:
        rows = []
        for ci_id in linked:
            ci = cmdb.get_ci(db, ci_id)
            impact = cmdb.downstream_impact(db, ci_id)
            rows.append({"CI":ci_id,"Name":ci.name if ci else "—","Class":ci.ci_type if ci else "—",
                         "Criticality":ci.criticality if ci else "—","Downstream impact":len(impact)})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No affected CIs linked.")
    available = [ci for ci in cmdb.list_cis(db) if ci.ci_id not in linked]
    if available:
        with st.form(f"add_ci_{change_id}", clear_on_submit=True):
            ci_id = st.selectbox("Add affected CI", [c.ci_id for c in available],
                                 format_func=lambda x: next(f"{c.ci_id} · {c.name}" for c in available if c.ci_id==x))
            if st.form_submit_button("Add to related list"):
                change.link_ci(db, change_id, ci_id, "demo.user")
                st.rerun()


def _approvals_panel(db: Database, change_id: str, state: str) -> None:
    approvals = change.approval_status(db, change_id)
    if approvals:
        for approver, decision in approvals.items():
            icon = {"approved":"✅","rejected":"⛔","requested":"⏳"}.get(decision,"•")
            cols = st.columns([.55,.22,.23])
            cols[0].write(f"{icon} **{approver}**")
            cols[1].write(decision.title())
            if decision == "requested":
                a,b = cols[2].columns(2)
                if a.button("✓", key=f"approve_{change_id}_{approver}", help="Approve"):
                    change.decide_approval(db, change_id, approver, True, "Approved in demo workspace.")
                    st.rerun()
                if b.button("✕", key=f"reject_{change_id}_{approver}", help="Reject"):
                    change.decide_approval(db, change_id, approver, False, "Rejected in demo workspace.")
                    st.rerun()
    else:
        st.info("No approval records.")
    if state in ("assess", "authorize"):
        with st.form(f"request_approval_{change_id}"):
            approver = st.text_input("Approver", "cab-chair")
            if st.form_submit_button("Request approval"):
                change.request_approval(db, change_id, approver.strip() or "cab-chair", "demo.user")
                st.rerun()


def _activity_panel(db: Database, change_id: str) -> None:
    trail = change.audit_trail(db, change_id)
    if trail:
        for event in reversed(trail):
            st.markdown(f"""<div class="list-card"><span class="record-number">{event['event'].replace('_',' ').upper()}</span>
            &nbsp; <b>{event['actor']}</b><br/>{event['detail']}<br/>
            <small>{event['at']} UTC</small></div>""", unsafe_allow_html=True)


def _record_detail(db: Database, change_id: str) -> None:
    cr = change.get_change(db, change_id)
    rec = change.change_record(db, change_id)
    if not cr or not rec:
        st.error("Change record not found.")
        return
    st.markdown(f"""<div class="record-head"><span class="record-number">{cr.change_id}</span>
      <div class="record-title">{cr.short_desc}</div>
      <span class="pill">{cr.change_type.upper()}</span><span class="pill">{cr.state.upper()}</span>
      {_risk_pill(cr.risk, cr.risk_score)}</div>""", unsafe_allow_html=True)
    st.markdown(_ribbon(cr.state), unsafe_allow_html=True)
    st.write("")
    _governed_actions(db, cr)
    if cr.state == "authorize":
        st.info("The record is in Authorize. Try scheduling before approval to demonstrate the server-side governance gate.")

    st.subheader("Change request")
    with st.form(f"record_form_{change_id}"):
        a,b = st.columns(2)
        short_desc = a.text_input("Short description", rec["short_desc"])
        change_type = b.selectbox("Type", ["standard","normal","emergency"],
                                  index=["standard","normal","emergency"].index(rec["change_type"]))
        c,d = st.columns(2)
        priority_opts=["low","medium","moderate","high","critical"]
        priority = c.selectbox("Priority", priority_opts,
                               index=priority_opts.index(rec.get("priority") or "medium") if (rec.get("priority") or "medium") in priority_opts else 1)
        category_opts=["hardware","software","network","facility","security","other"]
        category = d.selectbox("Category", category_opts,
                               index=category_opts.index(rec.get("category") or "hardware") if (rec.get("category") or "hardware") in category_opts else 0)
        e,f = st.columns(2)
        assigned_to = e.text_input("Assigned to", rec.get("assigned_to") or "")
        requested_by = f.text_input("Requested by", rec.get("requested_by") or "")
        g,h = st.columns(2)
        planned_start = g.text_input("Planned start", rec.get("planned_start") or "")
        planned_end = h.text_input("Planned end", rec.get("planned_end") or "")
        description = st.text_area("Description", rec.get("description") or "", height=80)
        reason = st.text_area("Justification / reason", rec.get("reason") or "", height=70)
        implementation = st.text_area("Implementation plan", rec.get("implementation_plan") or "", height=85)
        test_plan = st.text_area("Test / validation plan", rec.get("test_plan") or "", height=85)
        backout = st.text_area("Backout plan", rec.get("backout_plan") or "", height=85)
        if st.form_submit_button("Save record", type="primary"):
            _save_record(db, rec, {"short_desc":short_desc,"change_type":change_type,
                "priority":priority,"category":category,"assigned_to":assigned_to,
                "requested_by":requested_by,"planned_start":planned_start,"planned_end":planned_end,
                "description":description,"reason":reason,"implementation_plan":implementation,
                "test_plan":test_plan,"backout_plan":backout})
            st.rerun()

    related = st.tabs(["Affected CIs", "Approvals", "Activity"])
    with related[0]: _affected_ci_panel(db, change_id)
    with related[1]: _approvals_panel(db, change_id, cr.state)
    with related[2]: _activity_panel(db, change_id)


def _changes_workspace(db: Database) -> None:
    _page_header("Change", "All Changes", "List → record → related lists → governed actions")
    rows = _changes(db)
    left,right = st.columns([.30,.70], gap="large")
    with left:
        search = st.text_input("Search", placeholder="Number or description")
        state_filter = st.multiselect("State", STATE_ORDER + ["cancelled"], placeholder="All states")
        filtered = [r for r in rows if (not search or search.lower() in (r["change_id"]+" "+r["short_desc"]).lower())
                    and (not state_filter or r["state"] in state_filter)]
        if not filtered:
            st.warning("No matching changes.")
            return
        ids=[r["change_id"] for r in filtered]
        selected=st.session_state.get("selected_change","CHG1000001")
        if selected not in ids: selected=ids[0]
        selected=st.radio("Change records",ids,index=ids.index(selected),
                          format_func=lambda x: next(f"{x} · {r['short_desc']} · {r['state'].upper()}" for r in filtered if r['change_id']==x),
                          label_visibility="collapsed")
        st.session_state.selected_change=selected
        with st.expander("＋ New change"):
            with st.form("new_change"):
                desc=st.text_input("Short description")
                ctype=st.selectbox("Type",["normal","standard","emergency"])
                requester=st.text_input("Requested by","demo.user")
                if st.form_submit_button("Create"):
                    if desc.strip():
                        created=change.create_change(db,desc.strip(),requester.strip() or "demo.user",change_type=ctype)
                        st.session_state.selected_change=created.change_id
                        st.rerun()
                    else: st.error("Short description is required.")
    with right:
        _record_detail(db, selected)


def _cmdb_workspace(db: Database) -> None:
    _page_header("Configuration", "CMDB", "Configuration Items, relationships, and operational blast radius")
    cis=cmdb.list_cis(db); ids=[c.ci_id for c in cis]
    left,right=st.columns([.32,.68],gap="large")
    with left:
        selected=st.selectbox("Configuration item",ids,index=ids.index("UPS-01"),
                              format_func=lambda x:next(f"{c.ci_id} · {c.name}" for c in cis if c.ci_id==x))
        ci=cmdb.get_ci(db,selected); impact=cmdb.downstream_impact(db,selected)
        st.markdown(f"""<div class="record-head"><span class="record-number">{ci.ci_id}</span>
          <div class="record-title">{ci.name}</div><span class="pill">{ci.ci_type.upper()}</span>
          <span class="pill">{ci.status.upper()}</span>{_risk_pill(ci.criticality)}</div>""",unsafe_allow_html=True)
        st.metric("Downstream blast radius",len(impact))
        impacted=[cmdb.get_ci(db,x) for x in impact]
        if impacted:
            st.dataframe(pd.DataFrame([{"CI":x.ci_id,"Name":x.name,"Class":x.ci_type,"Criticality":x.criticality}
                                       for x in impacted if x]),width="stretch",hide_index=True)
    with right:
        st.subheader("Dependency View")
        rels=_query(db,"SELECT parent_ci,child_ci,rel_type FROM cmdb_rel ORDER BY parent_ci,child_ci")
        dot=['digraph G {','rankdir="LR";','bgcolor="transparent";',
             'node [shape=box style="rounded,filled" fillcolor="#eef5f7" fontcolor="#173445" color="#9bb8c6"];',
             'edge [color="#2d8ca3" fontcolor="#567786" fontsize=9];']
        for r in rels:
            color="#ba0517" if selected in (r["parent_ci"],r["child_ci"]) else "#2d8ca3"
            dot.append(f'"{r["parent_ci"]}" -> "{r["child_ci"]}" [label="{r["rel_type"]}" color="{color}"];')
        dot.append("}")
        st.graphviz_chart("\n".join(dot),width="stretch")
        st.caption("ServiceNow uses typed CI relationships and impacted-service related lists; this compact graph demonstrates the same reasoning mechanism.")


def _work_orders(db: Database) -> None:
    _page_header("Field Service", "Work Orders", "Plan work, capture labor and parts, record outcomes")
    orders = work.list_work_orders(db)
    left, right = st.columns([.30, .70], gap="large")
    with left:
        state_filter = st.multiselect("State", ["planned","in_progress","complete","cancelled"], placeholder="All states")
        filtered = [r for r in orders if not state_filter or r["state"] in state_filter]
        if not filtered:
            st.warning("No matching work orders.")
            return
        ids = [r["wo_id"] for r in filtered]
        selected = st.session_state.get("selected_wo", ids[0])
        if selected not in ids: selected = ids[0]
        selected = st.radio("Work order records", ids, index=ids.index(selected),
                            format_func=lambda x: next(f"{x} · {r['short_desc']} · {r['state'].upper()}" for r in filtered if r["wo_id"]==x),
                            label_visibility="collapsed")
        st.session_state.selected_wo = selected
        with st.expander("＋ New work order"):
            with st.form("new_work_order"):
                desc = st.text_input("Short description")
                all_cis = cmdb.list_cis(db)
                ci_id = st.selectbox("Configuration item", [c.ci_id for c in all_cis],
                                     format_func=lambda x: next(f"{c.ci_id} · {c.name}" for c in all_cis if c.ci_id==x))
                assigned = st.text_input("Assigned to", "service.operations")
                if st.form_submit_button("Create") and desc.strip():
                    st.session_state.selected_wo = work.create_work_order(db, desc.strip(), ci_id, assigned_to=assigned)
                    st.rerun()
    with right:
        wo = work.get_work_order(db, selected)
        summary = next(r for r in orders if r["wo_id"] == selected)
        outcome = (wo.get("outcome") or "pending").upper()
        st.markdown(f"""<div class="record-head"><span class="record-number">{wo['wo_id']}</span>
          <div class="record-title">{wo['short_desc']}</div><span class="pill">{wo['state'].upper()}</span>
          <span class="pill">{wo['priority'].upper()}</span><span class="pill">{outcome}</span></div>""", unsafe_allow_html=True)
        a,b,c,d = st.columns(4)
        a.metric("Configuration item", wo["ci_id"])
        b.metric("Labor", f"{summary['labor_minutes']/60:.1f} hr")
        c.metric("Parts", f"${summary['parts_cost']:,.0f}")
        d.metric("Source", wo["source_type"].title())
        if wo["state"] == "planned" and st.button("Start work", type="primary"):
            work.transition(db, selected, "in_progress", "demo.technician"); st.rerun()
        if wo["state"] == "in_progress":
            st.subheader("Complete work")
            with st.form(f"complete_{selected}"):
                completion_outcome = st.selectbox("Outcome", ["success","follow_up_required","failed"])
                notes = st.text_area("Completion notes")
                if st.form_submit_button("Complete work", type="primary"):
                    work.complete(db, selected, "demo.technician", completion_outcome, notes)
                    st.rerun()

        tabs = st.tabs(["Execution log", "Labor / parts entry", "Source & lineage"])
        with tabs[0]:
            logs = work.work_logs(db, selected)
            frame = pd.DataFrame(logs)
            if not frame.empty:
                frame["part_cost"] = frame["part_quantity"] * frame["part_unit_cost"]
                st.dataframe(frame.rename(columns={"at":"Timestamp UTC","technician":"Technician","action":"Work performed",
                    "result":"Result","labor_minutes":"Labor min","part_number":"Part","part_quantity":"Qty","part_cost":"Cost"})[
                    ["Timestamp UTC","Technician","Work performed","Result","Labor min","Part","Qty","Cost"]], width="stretch", hide_index=True)
        with tabs[1]:
            if wo["state"] in ("planned","in_progress"):
                with st.form(f"work_log_{selected}", clear_on_submit=True):
                    tech = st.text_input("Technician", "demo.technician")
                    action = st.text_input("Work performed")
                    labor = st.number_input("Labor minutes", min_value=0, step=15)
                    p1,p2 = st.columns(2)
                    part = p1.text_input("Part number (optional)")
                    part_desc = p2.text_input("Part description")
                    q1,q2 = st.columns(2)
                    qty = q1.number_input("Quantity", min_value=0.0, step=1.0)
                    unit = q2.number_input("Unit cost", min_value=0.0, step=10.0)
                    result = st.text_input("Result / observation")
                    if st.form_submit_button("Add execution entry"):
                        work.log_execution(db, selected, tech, action or "Execution update", labor_minutes=int(labor),
                                           result=result, part_number=part or None, part_description=part_desc or None,
                                           part_quantity=qty, part_unit_cost=unit)
                        st.rerun()
            else:
                st.info("Execution is complete; the evidence record is read-only in this demo.")
        with tabs[2]:
            st.write(f"**Source:** `{wo['source_type']}`")
            st.write(f"**Source reference:** `{wo.get('source_ref') or '—'}`")
            st.write(f"**Parent work order:** `{wo.get('parent_wo_id') or '—'}`")
            st.write(f"**Assigned to:** {wo.get('assigned_to') or 'Unassigned'}")
            st.write(f"**Completion notes:** {wo.get('completion_notes') or '—'}")


def _insights(db: Database) -> None:
    _page_header("Performance Analytics", "Operational Insights", "Execution evidence converted into better future work")
    st.markdown("""<div class="loop"><div class="loop-step"><b>1 · WORK ORDER</b><br/>Plan against a CI</div>
      <div class="loop-step"><b>2 · EXECUTION</b><br/>Capture time, parts, result</div>
      <div class="loop-step"><b>3 · ANALYTICS</b><br/>Find repeat cost and failure</div>
      <div class="loop-step"><b>4 · NEW WORK</b><br/>Generate a governed follow-up</div></div>""", unsafe_allow_html=True)
    insights = work.operational_insights(db)
    orders = work.list_work_orders(db)
    cols = st.columns(4)
    cols[0].metric("Work orders", len(orders))
    cols[1].metric("Labor captured", f"{sum(r['labor_minutes'] for r in orders)/60:.1f} hr")
    cols[2].metric("Parts consumed", f"${sum(r['parts_cost'] for r in orders):,.0f}")
    cols[3].metric("Unsuccessful outcomes", sum(1 for r in orders if r.get("outcome") in ("failed","follow_up_required")))
    frame = pd.DataFrame(insights).rename(columns={"ci_id":"CI","ci_name":"Configuration item","work_orders":"Work orders",
        "failures":"Failures","followups":"Follow-up outcomes","labor_hours":"Labor hours","parts_cost":"Parts cost",
        "insight_score":"Insight score","recommendation":"Recommendation"})
    st.dataframe(frame[["CI","Configuration item","Work orders","Failures","Follow-up outcomes","Labor hours","Parts cost","Insight score","Recommendation"]],
                 width="stretch", hide_index=True,
                 column_config={"Insight score":st.column_config.ProgressColumn(min_value=0,max_value=100),
                                "Parts cost":st.column_config.NumberColumn(format="$%.0f")})
    st.subheader("Act on an insight")
    actionable = [r for r in insights if r["insight_score"] > 0]
    if actionable:
        ci_id = st.selectbox("Configuration item", [r["ci_id"] for r in actionable],
                             format_func=lambda x: next(f"{r['ci_id']} · {r['ci_name']} · score {r['insight_score']}" for r in actionable if r['ci_id']==x))
        insight = next(r for r in actionable if r["ci_id"]==ci_id)
        st.markdown(f"""<div class="callout"><b>Recommendation:</b> {insight['recommendation']}<br/>
        Evidence: {insight['work_orders']} work orders · {insight['labor_hours']} labor hours · ${insight['parts_cost']:,.0f} parts · {insight['failures']} failures.</div>""",
                    unsafe_allow_html=True)
        if st.button("Create follow-up work order from insight", type="primary"):
            wo_id = work.create_followup_from_insight(db, ci_id)
            st.success(f"Created {wo_id}. Source and parent lineage were preserved; open Work Orders to inspect it.")
    st.subheader("Analytics-generated lineage")
    generated = [r for r in orders if r["source_type"] == "analytics"]
    if generated:
        st.dataframe(pd.DataFrame(generated).rename(columns={"wo_id":"Work order","short_desc":"Description","ci_id":"CI",
            "state":"State","source_ref":"Insight source","parent_wo_id":"Parent work"})[
            ["Work order","Description","CI","State","Insight source","Parent work"]], width="stretch", hide_index=True)
    else:
        st.info("No analytics-generated work yet. Use the action above to close the loop.")


def _performance(db: Database) -> None:
    _page_header("Analytics", "Operational Performance", "Change governance and audit visibility")
    _summary_metrics(db)
    st.subheader("Change portfolio")
    frame=pd.DataFrame(_changes(db))
    st.dataframe(frame.rename(columns={"change_id":"Number","short_desc":"Description","state":"State","risk":"Risk",
                                      "risk_score":"Risk score","affected_cis":"Affected CIs"})[
                                      ["Number","Description","State","Risk","Risk score","Affected CIs"]],
                 width="stretch",hide_index=True,
                 column_config={"Risk score":st.column_config.ProgressColumn(min_value=0,max_value=100)})
    st.subheader("Audit events")
    events=_query(db,"SELECT at,change_id,actor,event,detail FROM audit_log ORDER BY audit_id DESC")
    st.dataframe(pd.DataFrame(events).rename(columns={"at":"Timestamp UTC","change_id":"Change","actor":"Actor",
                                                      "event":"Event","detail":"Detail"}),width="stretch",hide_index=True)


def _about() -> None:
    _page_header("Help", "Architecture & Scope", "Accurate mechanics, deliberately limited product surface")
    st.markdown("""
    ### What is ServiceNow-like here
    - Record-centric change forms with type, priority, reason, assignment, schedule, implementation, test and backout plans.
    - Affected-CI, approval and activity **related lists**.
    - CMDB Configuration Items with typed relationships and dependency impact.
    - Backend-enforced lifecycle transitions, approval gates, explainable risk, and field-level audit.

    ### What is deliberately not a clone
    ServiceNow is a broad enterprise platform with table inheritance, Discovery and reconciliation,
    Flow Designer, RBAC/ACLs, integrations, notifications, SLA timers, change calendars, service
    mapping and many workspaces. This project is a scoped learning analogue for the governing
    mechanics of operational change control—not a substitute for the Now Platform.

    ### Production evolution
    Integrate with the enterprise CMDB rather than duplicating it; add authentication/RBAC,
    maintenance-window and concurrent-change checks, customer/SLA impact, notifications,
    configurable workflows, rollback/validation tasks, API integrations, and metrics such as
    change success, failed-change rate, incidents caused by change, emergency-change rate,
    unauthorized changes, approval lead time and repeat failures.
    """)


def main() -> None:
    st.set_page_config(page_title="Service Operations Workspace",page_icon="⚙️",layout="wide",
                       initial_sidebar_state="expanded")
    _css(); db=_db()
    with st.sidebar:
        st.markdown('<div class="brand-sub">Mini-ITSM</div><div class="brand">Service Operations<br/>Workspace</div>',unsafe_allow_html=True)
        st.caption("Scoped ServiceNow analogue")
        st.divider()
        nav=st.radio("Modules",["My Work","Changes","Work Orders","CMDB","Operational Insights","Performance","About"],index=1)
        st.divider()
        st.caption("Session-isolated deterministic data")
        if st.button("Reset demo",width="stretch"):
            _reset(); st.rerun()
    if nav=="My Work": _my_work(db)
    elif nav=="Changes": _changes_workspace(db)
    elif nav=="Work Orders": _work_orders(db)
    elif nav=="CMDB": _cmdb_workspace(db)
    elif nav=="Operational Insights": _insights(db)
    elif nav=="Performance": _performance(db)
    else: _about()
