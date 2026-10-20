# Synopsis

## Problem

Flood coordinators allocate limited boats, transport capacity, shelter, and
supplies among competing urgent needs while routes, casualties, and resource
availability change.

## Solution

The system uses five agents: Medical, Shelter, Logistics, Mediator, and
Scenario. The first three make domain-specific typed claims. Mediator requests
revisions or evidence and produces a deterministic inventory-safe plan.
Scenario validates event text, updates the scenario, and triggers re-planning
for affected agents. LangGraph coordinates the bounded workflow; Pydantic
validates agent I/O; Tracer records JSONL evidence; Streamlit provides the
control room.

## Target user

Disaster-control-room coordinators who need to review and explain resource
trade-offs as field conditions change.

## Shared schemas

The core contracts are `Zone`, `Scenario`, `Claim`, `Counter`, `Estimate`,
`MediatorAction`, `Plan`, and `NegotiationState`. The Scenario agent also uses
`ScenarioPatch`. Full examples and architecture are in [DESIGN.md](DESIGN.md).
