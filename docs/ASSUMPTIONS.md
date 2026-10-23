# Model assumptions (unverified)

Values in this list are demonstration or model parameters. They are not
verified operational data and must not be presented as field measurements.

## Commit 1: demand and transport feasibility

- For legacy zones without `supply_dependent`, the remaining population after
  critical and stranded non-critical groups is treated as supply-dependent.
- In `data/scenario.json`, Zone A's supply-dependent count is 150 and Zone B's
  is 50. These values partition the example population without overlap; they
  are demo inputs, not verified counts.
- The seed scenario assumes 50 persons per boat and a two-hour round trip in
  each zone. These are placeholders for demonstrating feasibility checks.
- The seed scenario's ambulance capacity of 2 denotes an assumed vehicle
  count; passenger capacity and travel time remain unverified unless supplied
  separately.
- Legacy hospital names normalize to facility records with `None` for ICU
  beds, transfer acceptance, and travel time. `None` means unverified, not zero.

## Commit 3: priority policy

- Priority severity weights are policy assumptions: Medical 20.0, Shelter 2.0,
  Logistics 1.0. They are demonstration weights, not clinical evidence.
- When equally scored claims compete for insufficient stock, the split is
  proportional to each claim's useful unit cap; largest remainders follow the
  existing urgency, time-window, agent tie-break.
- The illustrative `samples/output.json` uses Medical's sample claim of 12
  people, urgency 10, a two-hour window, and 2 boats; Shelter's 300 people,
  urgency 9, a six-hour window, 3 boats, and 312 beds; and Logistics' 200
  people, urgency 9, a twelve-hour window, 2 boats, 312 food rations, and 3120
  liters of water. These are demo claim inputs, not verified field data; the
  generated plan is not operational or field-verified.

## Follow-up commit 1: transport verification

- Ground routes are considered feasible only at `road_access >= 0.5`, unless
  the road condition is explicitly `blocked` or `landslide`. This is a demo
  routing threshold, not a road-engineering standard; road status and access
  are sourced from the scenario.
- Hospital and transport tools never fill unknown fields with assumed numeric
  values. `verified` is false when any field needed by a plan is unknown.
