# Mini-ITSM (CMDB + Change Management) — Notes & Mapping

**What this is:** a small, working analogue of ServiceNow's two most relevant
modules for tomorrow's interview — the **CMDB** (Configuration Management
Database) and **Change Management** (state machine + approvals + audit trail).
Built the same way `orbital-sentinel` was built for the LeoLabs interview:
not a toy, not a full clone — a **scoped, honest, working system** that proves
you understand the real architecture by rebuilding its core mechanics.

**Deliberate scope cuts (say these out loud if asked — honesty > bluffing):**
- No REST API / web UI. This is a Python package + CLI. A real ServiceNow
  instance is Now Platform (a full app server + UI + workflow engine); I built
  the *data model and governance logic*, not the platform shell.
- One CI table with a `ci_type` discriminator, not ServiceNow's true
  class-hierarchy (`cmdb_ci` base table + per-class extension tables like
  `cmdb_ci_server`). I know the real model is class-based; I simplified for a
  one-night build.
- Risk scoring is a deterministic formula I designed, not ServiceNow's
  configurable risk-assessment questionnaire. Same *idea* (turn CI facts into
  a leadership-legible risk number), simpler mechanism.
- No SLA timers, no notifications, no CAB meeting scheduling — the workflow
  *shape* is real; the surrounding process tooling is out of scope.

---

## How this maps to real ServiceNow (the vocabulary to have ready)

| This project | Real ServiceNow | What it teaches |
|---|---|---|
| `cmdb_ci` table | `cmdb_ci` + class tables (`cmdb_ci_server`, `cmdb_ci_ups`, ...) | CIs are the CMDB's atoms; every table inherits a common base |
| `cmdb_rel` table | `cmdb_rel_ci` | Relationships are typed and directional (`Powers`, `Depends on`, `Contains`) — not just "linked" |
| `downstream_impact()` | "Dependency Views" / CMDB impact analysis | Walking the CI graph forward to answer "what breaks if I touch this?" — the analytical basis of change risk |
| `change_request` table + `STATES`/`TRANSITIONS` | `change_request` table + a **Flow Designer / Workflow state model** | **The core concept:** state is *governed*, not a free-text field. You cannot jump straight to "Scheduled" — the platform enforces the graph server-side |
| `compute_risk()` | Change Risk Assessment (questionnaire → risk value) | Turning operational/CMDB facts into a risk score leadership can act on — this is literally the JD's "assess the impact of operational initiatives... risks, potential outcomes" |
| `change_approval` table + the scheduling gate | `sysapproval_approver` + CAB (Change Advisory Board) approval requirement | **Governance gate:** you cannot move a change forward without approval, and a single rejection blocks it even if others approved — this is the mechanism, not just policy |
| `audit_log` table | `sys_audit` (platform-wide field-level audit) | Nothing changes silently — every transition, link, and approval decision is a permanent record. This is what makes ITSM data trustworthy for compliance/audit |
| `change_ci` join table | `change_request_ci` | Changes affect *specific* CIs — this is what makes risk scoring and impact analysis possible in the first place |
| `work_order` + `work_log` | Product/implementation-specific field/facilities work records | Work is not complete without execution evidence: labor, parts, observation, outcome |
| `operational_insights()` | Performance Analytics / operational reporting | Aggregates repeat work, failure, labor and parts cost by CI |
| `create_followup_from_insight()` | Flow Designer/custom automation | Converts evidence into new governed work and preserves source/parent lineage |

### The closed-loop sentence

"A ticket closing is not the operational outcome. The loop closes only when we
capture what work consumed and achieved, learn from that evidence, create better
follow-up work, preserve why it was created, and measure the next outcome."

Real ServiceNow table names for work orders vary by product and implementation
(Field Service Management, facilities/workplace operations, or a scoped app).
Do not bluff a universal table name; lead with the platform pattern: records,
CI relationships, workflows, execution evidence, Performance Analytics, and
automation.

---

## The one idea to lead with in the interview

**"State transitions in ServiceNow (and in my mini version) are enforced by a
graph, not just a status field a user can set to anything."** This single
sentence is the most important thing to say if either interviewer probes your
understanding of the platform. It's *why* Change Management can be trusted for
compliance: you can prove, from the audit log, that no change ever skipped
authorization or approval — the system physically would not allow it.

Say it almost exactly like that if asked "do you know how ServiceNow works?"
or "what does change control actually enforce?"

---

## Direct tie to tomorrow's interview

**Chad Coyhis — Operational Change Control Specialist.** This project is
*literally his job's mechanism*, rebuilt small. If he asks anything about
change process, you now have a concrete, correct vocabulary and a real (if
tiny) working system to reference:

- *"How do you think about change risk?"* → "I built a small scorer for exactly
  this — it takes the criticality of the CIs a change touches, the blast
  radius (everything downstream that depends on them), and the change type
  (standard/normal/emergency), and turns that into a 0-100 score and a
  low/moderate/high label. A real system's risk assessment is a configurable
  questionnaire, but the underlying idea — turn operational facts into a
  number leadership can act on — is the same."
- *"What stops a change from going live without approval?"* → "In the system I
  built, the state machine itself refuses the transition — you cannot move a
  change into 'scheduled' unless every requested approval is 'approved' and
  none are 'rejected.' It's enforced in code, not just policy, and I have tests
  proving a single rejection blocks scheduling even if everyone else approved."
- *"How do you handle auditability?"* → "Every state change, CI link, and
  approval decision writes an immutable row to an audit table with an actor,
  timestamp, and detail — so you can always answer 'who approved this and
  when' after the fact." (This is also a direct callback to your real resume
  line: "documenting business rules for auditability.")
- *"Walk me through a change lifecycle."* → Narrate the CLI demo below from
  memory: draft → assess (link the CI) → authorize (risk computed) → blocked
  on scheduling until approved → approved → scheduled → implement → review →
  closed.

**Dale Keebler — Senior Manager, Service Operations.** Less directly relevant
to him, but the CMDB/impact-analysis piece is a good bridge to your *actual*
operational floor experience: "the CMDB models exactly the kind of power chain
I worked on as a DC technician — generator feeds UPS feeds PDU feeds server —
and the impact analysis answers the question I used to answer by hand: if this
breaker trips, what actually goes dark."

---

## Demo script (rehearse this once before the interview)

```bash
cd servicenow-mini
python3 -m itsm.cli seed
python3 -m itsm.cli cmdb-impact PDU-01          # show the power-chain graph query
python3 -m itsm.cli change-new "Replace UPS-01 battery string" --by dcoyhis
python3 -m itsm.cli change-link CHG1000001 UPS-01 --by dcoyhis
python3 -m itsm.cli change-transition CHG1000001 assess --by dcoyhis
python3 -m itsm.cli change-transition CHG1000001 authorize --by dcoyhis   # risk computed
python3 -m itsm.cli change-transition CHG1000001 scheduled --by dcoyhis   # BLOCKED — no approval
python3 -m itsm.cli change-approve CHG1000001 --approver cab-chair --request --by dcoyhis
python3 -m itsm.cli change-approve CHG1000001 --approver cab-chair --approve
python3 -m itsm.cli change-transition CHG1000001 scheduled --by dcoyhis   # now succeeds
python3 -m itsm.cli change-show CHG1000001                                # full audit trail
```

Run `python3 tests/test_itsm.py` beforehand to confirm the environment still
works (8/8 tests as of last run) — nothing worse than a live demo failing.

## Files
- `itsm/db.py` — SQLite schema (6 tables, mirrors ServiceNow table names loosely)
- `itsm/cmdb.py` — CI CRUD, relationship graph, `downstream_impact()`, DC seed data
- `itsm/change.py` — the state machine, risk scoring, approval gate, audit trail
- `itsm/cli.py` — CLI to drive the whole lifecycle
- `tests/test_itsm.py` — 8 tests: topology, impact walk, invalid transitions,
  approval gating (including the "one rejection blocks it" case), risk scoring,
  audit completeness, full lifecycle to closed
