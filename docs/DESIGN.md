# Design

## Architecture

```mermaid
flowchart TD
  UI[Streamlit control room<br/>planned] --> G[LangGraph orchestrator<br/>planned]
  G --> S[Scenario agent<br/>stub]
  G --> M[Medical agent<br/>stub]
  G --> H[Shelter agent<br/>stub]
  G --> L[Logistics agent<br/>stub]
  M --> X[Mediator agent<br/>stub]
  H --> X
  L --> X
  S --> X
  X --> G
  G --> T[Tracer]
  G --> O[Plan output]
```

## Pattern

Orchestrator-worker with bounded negotiation loop.

## Role table

| Agent | Responsibility | Status |
|---|---|---|
| Medical | Prioritize critical patients | Stub |
| Shelter | Prioritize stranded residents | Stub |
| Logistics | Model transport constraints | Stub |
| Mediator | Resolve conflicts and finalize | Stub |
| Scenario | Inject changing conditions | Stub |

## I/O schema

Inputs are `Scenario` and optional scenario events. Workers emit `Claim` or
`Estimate`; the mediator emits `MediatorAction` and `Plan`. `NegotiationState`
holds the complete loop state. LangGraph and Streamlit integration are planned.
