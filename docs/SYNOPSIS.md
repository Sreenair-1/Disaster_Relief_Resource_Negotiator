# Synopsis

During a flood response, coordinators must decide how to use limited boats,
transport, shelter, food, and water as people and routes need change.

Medical, Shelter, and Logistics agents submit claims; Mediator assigns the
available resources and explains the trade-offs; Scenario applies events and
replans the agents affected by changed conditions.

The target user is a disaster-control-room coordinator who needs to review a
resource plan and its changes as field conditions evolve.

## Scope changes from CA2

- The agent count remains five: Medical, Shelter, Logistics, Mediator, and
  Scenario. Logistics covers vehicles, routes, food, and water; Mediator
  allocates resources per agent.
- Zone fields are `name`, `population`, `stranded`, `critical_patients`,
  `road_access` (0 to 1), and optional `supply_dependent`.
- The scenario contains no verified hospital ICU or bed numbers by default.
  Unknown values remain `null`, and Medical flags unverified support.
- Scoring adds severity weights and a feasibility multiplier to the
  `people_affected` x urgency x time-criticality x road-access score. Awards follow the fixed
  Medical-to-Shelter-to-Logistics life-safety order; scores rank claims and
  inform explanations but do not override that order.
- Additions include disjoint demand partitions, boat-trip feasibility,
  per-zone allocation and coverage, optional food/water modeling with assumed
  rates, estimate feedback, claim validation, free-text and preset events
  validated by their actual diff, and a trace self-check.
