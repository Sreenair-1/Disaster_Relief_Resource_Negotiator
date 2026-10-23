# System Design

## Purpose and agents

The Disaster Relief Resource Negotiator allocates scarce response resources
under changing flood conditions. It has **five agents** with distinct roles:
Medical, Shelter, Logistics, Mediator, and Scenario. Medical, Shelter, and
Logistics submit competing `Claim`s. Mediator challenges claims, requests
fallbacks or estimates, and produces an inventory-safe `Plan`. Scenario turns
an event into a validated patch, identifies affected agents, and enables
replanning.

The design uses an **orchestrator-worker** pattern. LangGraph owns the bounded
negotiation state and routes mediator actions. Workers gather domain evidence
and advocate for their assigned needs; the Mediator weighs claims and makes a
deterministic final allocation. A single general-purpose agent would hide
trade-offs between urgent patient transport, mass evacuation, and supply
delivery inside one response. Separate claims make those competing needs,
concessions, and unresolved shortfalls visible and auditable.

## Architecture and data flow

```mermaid
flowchart TD
  UI[Streamlit control room] -->|Scenario / event text| RUN[ui.runner]
  RUN -->|Scenario, LLM, Tracer| GRAPH[LangGraph orchestrator]
  GRAPH -->|NegotiationState| MED[Medical agent]
  GRAPH -->|NegotiationState| SHEL[ Shelter agent]
  GRAPH -->|NegotiationState| LOG[Logistics agent]
  GRAPH -->|Event text| SCEN[Scenario agent]
  MED -->|Claim / Estimate| GRAPH
  SHEL -->|Claim / Estimate| GRAPH
  LOG -->|Claim / Estimate| GRAPH
  SCEN -->|Patched state + affected_agents| GRAPH
  GRAPH -->|Claims, counters, estimates| M[ Mediator agent]
  M -->|Counter / decision| GRAPH
  M -->|Plan allocation| GRAPH
  GRAPH -->|Plan + trace events| RUN
  RUN -->|Transcript, allocation, warnings| UI

  MED -->|hospital_capacity, receiving_capacity, transport_options, casualty_estimate| TOOLS[lib.toolcall / tools.registry]
  SHEL -->|shelter_capacity, zone_population| TOOLS
  LOG -->|road_status, vehicle_inventory, stock_levels, supply_needs| TOOLS
  TOOLS -->|ToolResult / failure details| MED
  TOOLS -->|ToolResult / failure details| SHEL
  TOOLS -->|ToolResult / failure details| LOG
  TOOLS -->|tool_requested / tool_result| TRACE[Tracer / JSONL]
  MED -->|claims / estimates| TRACE
  SHEL -->|claims / estimates| TRACE
  LOG -->|claims / estimates| TRACE
  SCEN -->|event / rejection| TRACE
  M -->|decisions / plan| TRACE
  GRAPH -->|state transitions| TRACE
  LLM[LLMClient or StubLLM] <-->|Structured JSON| MED
  LLM <-->|Structured JSON| SHEL
  LLM <-->|Structured JSON| LOG
  LLM <-->|Decisions / reasoning| M
  LLM <-->|ScenarioPatch| SCEN
```

Each need agent calls tools through `lib.toolcall.call_tool`. That boundary
looks up the function in `tools.registry.TOOLS`, records the request and
result, catches `ToolError`, and returns `ToolResult`. Ten tools are registered:
`hospital_capacity`, `receiving_capacity`, `transport_options`,
`casualty_estimate`, `shelter_capacity`, `zone_population`, `road_status`,
`vehicle_inventory`, `stock_levels`, and `supply_needs`. Medical calls its four
tools on the Medical edge; Logistics calls `supply_needs` only when a supply
horizon is configured. `stock_levels` accepts
food/water aliases and returns scenario-backed quantities; unknown quantities
or rates remain `null`. Configured supply status is `critical` below one day,
`low` below two days, and `sufficient` at two or more days. `supply_needs(zone)`
reports disjoint people in need, horizon quantities, route modes, and per
vehicle payload; missing capacities are never invented.

Food and water are optional. `supply_need` derives the people count from
`demand_by_zone`'s mutually exclusive critical, stranded non-critical, and
supply-dependent groups, then multiplies by daily rate and horizon. One
`food_packs` is one person-day; water is measured in litres. Logistics gathers
`supply_needs` only when a supply horizon is configured and claims food/water
plus delivery vehicles. Aliases (`food`, `food_rations`, `water`,
`drinking_water`) canonicalize to `food_packs` and `water_liters` during claim
clamping, oversubscription checks, and event resource losses.

For each resource, allocation processes vehicles first and food/water stocks
last. Vehicles use the fixed life-safety tiers below. Food/water is assigned
only to Logistics, by zone, up to horizon need, remaining stock, and delivery capacity
(`vehicle units × payload × floor(window / trip time)`). Ground trucks require
a feasible road and use `truck_trip_hours_by_zone`, falling back to
`trip_hours_by_zone`; boats require known zone trip hours. Cut-off zones are
considered first, followed by larger uncovered needs. A vehicle assigned to
another team's evacuation work is not reused for Logistics supply in the same
window. `Plan.supply_coverage` reports need, delivery, percent covered,
days-of-cover, and delivery limits by zone. `undeliverable_stock` is stock left
because remaining delivery capacity cannot carry it; it is separate from
demand shortfalls. `unresolved_shortfalls` also records horizon demand above
on-hand food/water inventory, without counting stock blocked only by delivery
limits as an inventory shortfall. Stock with no deliverable route appears in
risks. Legacy
scenarios without supply fields omit supply coverage and supply risks.

Bundled water rates, horizon, and vehicle payloads are assumed demo values;
see [ASSUMPTIONS.md](ASSUMPTIONS.md). Verify Sphere Handbook water guidance
before citing it.
`tools.failure` can inject targeted, wildcard, random, or timeout failures.
Agents record unavailable telemetry, set claim uncertainty, and retain a
baseline fallback estimate.

`lib.feasibility` partitions each zone's population into critical patients,
stranded non-critical residents, and other supply-dependent residents. The
groups are mutually exclusive: critical people are removed from the stranded
group, and supply-dependent people are capped to the remaining population.
Claims are clamped to their agent's partition; any requested overlap is
recorded as a `claim_demand_clamped` trace event. The reported total demand is
the union of these groups. Older scenarios without a `supply_dependent` value
use the remaining unclassified population for that group, which is an explicit
model assumption rather than verified field data.

The scenario's `operations` schema carries optional boat capacity, round-trip
hours by zone, ambulance capacity, hospital facility objects, daily food and
water rates, a horizon, vehicle payloads, and optional truck-specific trip
times. Missing
capacity and facility values remain `None` and are treated as unverified.
Legacy `hospitals: ["name"]` inputs remain valid and normalize into facility
objects with unknown capacity and transfer status. `boats_needed` uses whole
round trips within the response window. Truck allocation and
`transport_options` use the `0.5` access threshold and also reject roads marked
`blocked` or `landslide`. Separately, Medical rejects a fallback mentioning
road, ground, or ambulance if any zone has `road_access <= 0`; this is a coarse
scenario-wide rule. Medical also rejects a boat fallback when the boat
inventory is unavailable. `unit_cap` limits known resource awards to the
maximum useful units. These checks report unknowns as unverified, never as
confirmed capacity.

Medical also queries `receiving_capacity` and `transport_options`. Missing ICU
or transfer-acceptance data marks support unverified; known ICU shortfall with
no accepting facility raises a plan risk. Medical rejects and traces ground
fallbacks when any zone has zero access and boat fallbacks when no boats are
available; the ground check is scenario-wide rather than zone-specific.

`hospital_capacity` and `receiving_capacity` return facility objects directly
from `Scenario.operations.hospitals`; unknown fields stay `null`. Transport
options mark ground access infeasible below the configured `0.5` demo threshold
or when the named road is blocked or landslide-damaged. `verified` is true only
when every returned capacity and route field relied on by planning is known.
Wayanad's operations values are demonstration assumptions, not verified local
fleet or route measurements. The cited IFRC early-action guidance supports
prioritizing early evacuation and at-risk people but supplies no capacities.

## Orchestration and replanning

`graph.negotiation.run_negotiation(scenario, llm, tracer)` collects claims from
Medical, Shelter, and Logistics, asks Mediator for the next
`MediatorAction`, and routes `challenge_agent` to a revision or
`request_fallback` / `ask_peer_estimate` to an estimate. The graph finalizes
when Mediator selects `finalize` or the three-round safety cap is reached.
One round is a full challenge cycle: every challenged agent must respond
before `round` advances once. It advances once per completed cycle, not once
per counter, and the hard cap remains three rounds.

**Estimate feedback loop.** Estimates returned by agents during
`request_fallback` / `ask_peer_estimate` are stored in
`NegotiationState.estimates` (not just logged). They are included in the next
Mediator context and available to the target agent's revision prompt. An
estimate informs the next decision or challenge rather than ending at the trace.

**Decision logic.** The Mediator's `decide_next` follows this priority:
1. If a resource is oversubscribed, challenge each affected claimant until its
   latest counter receives a changed claim or an unchanged defense with
   evidence. Counter snapshots and rounds are retained; unchanged claims are
   defended, never counted as revisions. Finalize early only after all affected
   claimants respond, or when total requests fit inventory.
2. Use `request_fallback` / `ask_peer_estimate` only for uncertain claims or
   failed tools.
3. The three-round cap is a safety net. At the cap, finalize with
   `status="constrained"` and explicit `unresolved_shortfalls` (resource, unmet
   amount, claimants), rather than implying unresolved demand was settled.

**Claim revision trace.** After a challenge, the graph traces the revised claim
as event type `claim_revision` with a change summary (resources, urgency,
time_window_hrs vs. the previous claim). An agent may defend its claim unchanged,
but the trace records `defended_unchanged: true` with the justification.
After every specialist response, numeric boat-capacity and route-time claims
are checked against `operations`; contradictions are corrected and traced as
`claim_corrected`. Every known resource request is clamped to inventory, while
unknown resource names are dropped from allocation and listed separately as
`Plan.non_inventory_requests`. A revision counts only if it reduces an
oversubscription or adds scenario-specific evidence; urgency and uncertainty
changes alone count as an unchanged defense.

`graph.replan.replan(state, event_text, llm, tracer)` invokes
`agents.scenario.inject_event`. The Scenario agent resolves the event against
the **current** scenario (zones, roads, hospitals, resources passed in context)
and must output concrete numeric deltas. If the event names no specific zone,
the agent applies it to every zone it plausibly affects and explains this in the
summary.

**Patch validation, diff, and retry.** After receiving a `ScenarioPatch`, the
agent applies the patch to a deep copy of the scenario and computes a
`ScenarioDiff` (`lib/schemas.py`). If the patch has empty mutation fields or the
resulting diff is empty (nothing actually changed), the agent retries the event
generation once with an explicit instruction to produce concrete, effective
changes. If the second attempt still produces no actual change, the event is
rejected with `is_valid=false` and the reason `"event had no effect on the
scenario"`. A UI warning is shown, and no-ops are never reported as successful
replans.

Exact demo `PRESET_EVENTS` have deterministic patches so the displayed event
always produces the described diff. `hospital_capacity` updates the named
receiving-facility record (including zero free ICU beds and transfer refusal
for the full-capacity preset), and the actual field diff re-triggers Medical.

**Derived affected agents.** `affected_agents` is derived from the fields that
actually changed in the diff, not solely from the LLM's guess:
- `road_access` or `roads` change → `logistics`, `medical`, `shelter`
- `critical_patients` or `hospital_capacity` change → `medical`
- `stranded` change → `shelter`
- `resources` change → all agents

When road access changes for a zone, every agent whose existing claim references
that zone is also added so they re-claim with updated conditions.

**Trace.** A successful patch logs an `event_applied` trace event containing the
patch, the `ScenarioDiff`, and the derived `affected_agents`. Rejected no-op
events instead produce `event_rejected` and do not advance replanning.

Replan refreshes only affected agents' claims, keeps all other claims,
increments `replans`, and, if those claims remain oversubscribed, asks one
affected claimant to answer a short counter before Mediator reallocates. Each
affected agent receives a `reassessment` trace with before/after scores,
requests, awards, and reason; the UI uses that reason in the allocation table.
If the event was
rejected (no affected agents), replan returns the unmodified state without
incrementing `replans`. The UI compares this plan with the first run.

Allocation and Mediator context use `score_claim`:
`people_affected * severity_weight * urgency * (1 / max(time_window_hrs, 1)) * road_access_factor * feasibility_multiplier`.
Feasibility is usable requested quantity divided by requested quantity, capped at 1.
Severity weights are policy assumptions listed in `docs/ASSUMPTIONS.md`.
Road access is computed per claim from zones named in its justification or
fallback. If no zone is named, Medical uses critical-patient zones and Shelter
uses zones with stranded residents; other claims use all zones. Access is
weighted by the agent's relevant people count when available, otherwise by
population. The road-access factor is floored at `0.05`, so a blocked route
lowers a score without erasing it.

Awards follow fixed life-safety tiers: Medical (1), Shelter (2), then Logistics
(3). For each non-food/water resource, tiers receive stock in that order,
subject to inventory and useful `unit_cap`; a short tier divides its share by
`score_claim` with largest-remainder rounding. The current specialist setup has
one claimant in each tier. Scores rank claims and weight shares but never
override tier order. Each claimant's total award is capped by its request and
useful cap, with per-zone caps applied while placing the award. Food and water
stocks are allocated afterward to Logistics zones using horizon need, delivery
capacity, and remaining inventory. `Plan` reports
both agent totals and `allocation_by_zone`; boats require known feasible round
trips and trucks require a ground-feasible zone. Unusable inventory remains
unassigned. Per-zone `coverage` reports demand, people served, and percentage
using `units × capacity × floor(response_window / round_trip_hours)`. The plan
sets `life_safety_gap` if any critical patients remain uncovered.

Final allocation reasoning must give each claimant a non-empty explanation with
its score, rank, requested and awarded amounts, shortfall, and comparison with
competing claims. The Mediator validates the numeric citations and priority
language, life-safety tier, and critical-patient gap, retries once with listed
contradictions, then uses deterministic data-based explanations if the repair
is still inconsistent or fails. Decision events preserve the LLM's reasoning;
when unavailable, the safety fallback is explicitly traced with
`reasoning_source="fallback"`. `Plan`
also carries `feasibility_report`: partition disjointness is computed from
`demand_totals.overlaps`, and allocation caps are checked against total
inventory and each claim's request and useful `unit_cap`. Failed checks appear
in `checks_failed`; successful checks appear in `checks_passed`, alongside
unresolved shortfalls, unknown assumptions, and demand totals. It records each claim once in
`disputes_log`: actual claim changes are
described as revisions; Mediator stock caps say "awarded X of Y requested" and
are never misattributed to the agent.

```mermaid
stateDiagram-v2
  [*] --> collect_claims
  collect_claims --> mediator_decide
  mediator_decide --> challenge: challenge_agent
  mediator_decide --> fallback: request_fallback / ask_peer_estimate / accept_revision with uncertainty
  mediator_decide --> finalize: finalize / accept_revision without uncertainty / no deficits / round >= 3
  mediator_decide --> challenge: challenge_agent for remaining challenged agents
  challenge --> mediator_decide: claim_revision; advance round after all challenged agents respond
  fallback --> mediator_decide: save Estimate to state
  finalize --> [*]
  [*] --> event_inject: replan(event_text)
  event_inject --> diff_check: apply patch & diff
  diff_check --> reject_noop: diff empty
  diff_check --> derive_agents: diff non-empty
  reject_noop --> [*]: is_valid=false, UI warning
  derive_agents --> trace_event_applied: event_applied
  trace_event_applied --> refresh_affected_claims
  refresh_affected_claims --> short_challenge: oversubscribed
  short_challenge --> mediator_reallocate: claim_revision
  refresh_affected_claims --> mediator_reallocate: otherwise
  mediator_reallocate --> trace_reassessment: per affected agent
  trace_reassessment --> [*]
```

## Roles and domain tools

| Agent | Responsibility | Tools | Output |
|---|---|---|---|
| Medical | Critical patients, triage urgency, hospital capacity, medical transport | `hospital_capacity`, `receiving_capacity`, `transport_options`, `casualty_estimate` | `Claim`; peer `Estimate` |
| Shelter | Stranded residents, shelter capacity, evacuation and phased rescue | `shelter_capacity`, `zone_population` | `Claim`; peer `Estimate` |
| Logistics | Supplies, vehicles, route access, transport feasibility | `road_status`, `vehicle_inventory`, `stock_levels`, `supply_needs` (when configured) | `Claim`; peer `Estimate` |
| Mediator | Challenge claims, request fallback/peer evidence, resolve conflict and allocate inventory | Deterministic scoring and inventory | `MediatorAction`, `Counter`, `Plan` |
| Scenario | Validate event text, patch scenario facts, diff before/after, derive affected agents | LLM-validated `ScenarioPatch`, `ScenarioDiff` | Updated `NegotiationState` |

## Contracts and samples

The Pydantic models in `lib/schemas.py` define the shared contracts. `Scenario`
is the input model and `Plan` is the output model. The Scenario agent also
validates `ScenarioPatch` and computes a `ScenarioDiff`. Field types, defaults,
descriptions, and the complete input/output JSON examples are generated in
[IO_DEFINITIONS.md](IO_DEFINITIONS.md) by `python scripts/gen_io_docs.py`.

`samples/input.json` is the seed scenario. `samples/output.json` is an
illustrative plan; `samples/output_full_run.json` and
`samples/trace_full_run.jsonl` are stored run artifacts. The generator validates
the input and output models and embeds both examples, then checks that the
trace's `scenario_loaded` payload matches `samples/input.json`; it exits with an
error until the stored run matches that seed scenario.

`NegotiationState` holds the scenario, current round, claims, counters,
estimates, events, optional plan, replan count, and affected-agent list.
Estimates collected during `request_fallback` / `ask_peer_estimate` rounds
are stored in `estimates` and fed back into the Mediator context and the
challenged agent's revision prompt. Agent functions accept that state with
an `LLMClient` or `StubLLM` and a `Tracer`. `Tracer.log` keeps in-memory
events and writes each event as JSONL.

## Stack rationale

| Component | Reason |
|---|---|
| Python 3.14.7 | Project runtime; standard typing, JSON, and filesystem APIs |
| Pydantic | Validate scenario, claims, estimates, patches, and plans at agent boundaries |
| LangGraph | Explicit state graph, conditional edges, bounded loop, and replan flow |
| OpenAI-compatible JSON client | Typed model output via OpenAI or Groq with retry and JSON repair; same interface for `StubLLM` |
| Deterministic Python scoring | Reproducible priorities and inventory caps independent of LLM prose |
| Streamlit | Scenario editing, event injection, transcript, allocation comparison, and downloads |
| python-dotenv | Load ignored local `.env` settings without overriding process environment values |
| JSONL Tracer | Auditable, append-only run record suitable for replay and review |

## Runtime and deployment

The dependency pins in `requirements.txt` are the versions installed in the
Python 3.14.7 development environment. Streamlit Community Cloud documents
Python 3.12 as its default and provides a Python version selector. The deployed
runtime must match the Python version tested for this application; this project
does not designate a deployment version. See [Cloud deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
and [supported Python versions](https://docs.streamlit.io/knowledge-base/using-streamlit/sanity-checks).

## Trace self-check

`scripts/check_trace.py` reads the JSONL, output `Plan`, and matching
`Scenario`. It checks agent participation, challenge/revision, event
reassessment, recovery after tool failure, scenario-supported hospital facts,
inventory totals, supply delivery caps, stock/supply tool facts, payload and
per-person claims, numeric justifications, and shortfall resource names. It
prints PASS/FAIL for each invariant and exits nonzero on failure. It does not
repair or rewrite either artifact.

## UI presentation

Pure helpers in `ui/format.py` convert trace events into readable summaries,
group events by negotiation round, and summarize plans without relying on newer
optional plan fields. The same helpers produce explicit arrows and reasons for
before/after allocation comparisons. The Streamlit page gives coordinators a
scenario summary and advanced editor in the sidebar, a welcome state with a
read-only sample option, and four views: Plan, Negotiation, What-if events, and
Raw trace. Run progress and missing-key errors use short, actionable messages.
Plan cards report allocations, food/water inventory and days-of-cover when
configured, coverage, delivery limits, undeliverable stock, risks, remaining
shortfalls, and unknown assumptions. Older plans without zone coverage or other optional fields
render with clear empty states. The Negotiation tab groups plain-language event
summaries by round, supports agent filtering, and hides routine tool calls by
default. The What-if tab builds zone-specific physical events, keeps custom
text separate, exposes the tool-failure toggle, and displays applied/rejected
event history with affected teams and allocation changes. Recorded sample
runs are clearly labelled and keep event controls disabled. Raw trace is
paginated with individual JSON details collapsed.
