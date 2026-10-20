# Design

## Architecture

```mermaid
flowchart TD
  UI[Streamlit control room] --> R[ui.runner boundary<br/>placeholder]
  R --> G[LangGraph orchestrator<br/>planned]
  G --> S[Scenario agent<br/>event injector & diff validator]
  G --> M[Medical agent<br/>critical care]
  G --> H[Shelter agent<br/>evacuation & displaced]
  G --> L[Logistics agent<br/>routes & resources]
  M --> X[Mediator agent<br/>conflict resolution]
  H --> X
  L --> X
  S --> G
  G --> RP[graph.replan<br/>affected-agent derivation]
  RP --> G
  X --> G
  G --> T[Tracer]
  G --> O[Plan output]
```

## Pattern

Orchestrator-worker with bounded negotiation loop.

## Role table

| Agent | Responsibility | Status |
|---|---|---|
| Medical | Prioritize critical patients | Implemented |
| Shelter | Prioritize stranded residents | Implemented |
| Logistics | Model transport constraints | Implemented |
| Mediator | Resolve conflicts and finalize | Stub |
| Scenario | Inject changing conditions & validate state mutations | Implemented |

## I/O schema

Inputs are `Scenario` and optional scenario events. Workers emit `Claim` or
`Estimate`; the mediator emits `MediatorAction` and `Plan`. `NegotiationState`
holds the complete loop state. `ScenarioDiff` models before/after field mutations.

## Scenario Event Injection & Diff Validation

The Scenario agent converts environmental events into structured `ScenarioPatch` mutations:
- **Context Resolution**: Events are evaluated against the current scenario context (zones, roads, hospitals, resources). Vague events naming no zone apply across all plausible zones.
- **Concrete Deltas**: Physical events must yield concrete numeric deltas (e.g. `road_access: {"Zone A": 0.0}`). Empty patches are strictly prohibited for valid physical events.
- **Diff & Retry**: Patches are applied to a deep copy of the scenario and diffed against the original. If the diff is empty (a no-op), the agent retries once with an instruction to be specific. If still unchanged, the event is rejected with `is_valid=false` and reason `"event had no effect on the scenario"`.
- **Tracing**: Valid mutations emit an `event_applied` trace with the full before/after diff of changed fields.

## Replan & Affected-Agent Derivation (`graph/replan.py`)

Rather than relying purely on LLM guesses, `affected_agents` are derived deterministically from the fields that actually changed:
- `road/route` changes -> `logistics` is affected, plus every agent whose active claim references that zone must re-claim.
- `casualties/hospital` changes -> `medical` is affected.
- `stranded/shelter` changes -> `shelter` is affected.
- `resources` changes -> `logistics` plus any agent whose claim requested that resource (and LLM nominated resource claimants).

## UI

`app.py` provides a Streamlit control room with a JSON scenario loader/editor
validated by the `Scenario` Pydantic model, a Run action, transcript rendering
from `Tracer` events, allocation and justification rows, event injection
presets plus free text, failure injection, and JSONL trace download. Preset buttons
name specific zones and magnitudes (e.g., "Bridge to Zone A collapses, cutting all ground road access to Zone A.").
A UI warning is displayed whenever an event has no effect or is rejected. `ui/runner.py`
is the narrow replacement point for the graph.
