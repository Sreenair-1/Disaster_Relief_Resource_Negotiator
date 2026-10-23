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

## Follow-up commit 2: Wayanad and seed operations

Public [IFRC Early Warning, Early Action guidance](https://www.ifrc.org/early-warning-early-action)
and the [IFRC Bangladesh Flood Early Action Protocol](https://go.ifrc.org/emergencies/7605/details)
support early evacuation, including assistance for people at risk; neither prescribes
the capacities or travel times below. These operational values are therefore
explicitly assumed for a software demo, not verified local data:

- Wayanad uses 10 persons per boat; its assumed round trips are 2 hours for
  Meppadi Valley, 4 for Vythiri High Range, 3 for Mananthavady Riverine, and 2
  for Sulthan Bathery Lowland. These distinguish steep/landslide routes from
  the lowerland demo routes; validate locally before operational use.
- Wayanad truck capacity is 20 person-equivalents per trip, a placeholder for
  testing the zone assignment path, not a vehicle specification.
- Wayanad ambulance availability and capacity are `null` because the scenario
  provides no count or passenger capacity. Both hospital ICU beds, transfer
  acceptance, and travel times are `null` because no facility records were
  supplied.
- The seed retains its existing 50 persons per boat and 2-hour round trips per
  zone as demonstration assumptions. Seed truck capacity and ambulance
  availability are unknown (`null`); the legacy ambulance count field is not
  treated as verified availability.
- Existing Wayanad inventory values (10 boats and 14 trucks) and demographic
  counts remain scenario inputs, not independently verified response data.
- Claims that state a known boat capacity or round-trip time inconsistent with
  `operations` are corrected to the scenario value and traced; no new factual
  capacity is inferred from claim prose.
- The three-round negotiation cap is a workflow safety setting, not an empirically
  derived response limit.
- One round is one complete cycle in which every challenged agent responds.
  `round` increments once when that cycle is complete, rather than once for
  each individual challenge; the hard cap is three.

## Follow-up commit 3: claim validation

- Claim resource amounts are capped at the scenario's listed inventory. Resource
  names absent from inventory are recorded as non-inventory requests and are
  never assigned an inferred stock value or treated as inventory shortfalls.
- A revision is meaningful only when it reduces a resource deficit or adds
  scenario-specific evidence; urgency or uncertainty edits alone are treated as
  an unchanged defense. This is an explicit negotiation policy.

## Follow-up commit 4: life-safety tiers

- Priority tiers follow the modeled demand classes (critical, stranded
  non-critical, supply-dependent); their ordering is a policy choice, not a
  measured clinical weighting. Within a tier the existing score is used.
- Coverage counts are capped at each tier's demand. No additional capacity is
  inferred when operations fields are unknown; such units cannot be assigned
  as feasible boats or trucks.

## Follow-up commit 5: honest mediator output

- Conflict-resolution statements are emitted only for a resource deficit that
  existed in earlier claim snapshots and is absent in the final validated
  requests. Remaining deficits belong in `unresolved_shortfalls`.
- The mediator's configured three-round cap is a workflow safety setting; its
  number is recorded under follow-up commit 2 above.

## Follow-up commit 6: trace self-check

- `scripts/check_trace.py` validates artifact structure and policy invariants;
  it does not certify that a live run occurred or replace review of the
  downloaded trace and plan.

## Food and water supply model

- One `food_packs` unit means one person-day of food by definition. The
  `food_packs_per_person_day=1` setting is therefore an assumed conversion
  used to keep demo quantities readable.
- `water_liters_per_person_day=10` litres is assumed for this software demo so
  the unit conversion and inventory checks can be exercised. Sphere Handbook
  water guidance is something to verify with a qualified response lead before
  citing or using operationally.
- `supply_horizon_days=2` is assumed to make the demo plan cover a short
  planning window; it is not an incident forecast.
- Truck payloads of 1,000 food packs and 10,000 litres, and boat payloads of
  200 food packs and 2,000 litres, are assumed demonstration capacities. They
  are chosen as round values for testing the delivery-capacity calculation and
  are not vehicle specifications.
- Scenario food stock is assumed to be 70% of the configured horizon need and
  water stock 60%. Those assumed ratios keep both supplies scarce while
  avoiding a claim of verified depot inventory.
- The depot preset's 40% loss is an assumed event severity chosen to provide a
  reproducible, non-empty stock diff for the demonstration.
- Wayanad and Cachar `supply_dependent` counts are explicitly set to the
  remaining population after stranded residents, an assumed arithmetic
  partition so the three demand groups do not overlap. The seed retains its
  existing explicit 150 and 50 demo counts.
- Trucks use `truck_trip_hours_by_zone`, falling back to the existing
  `trip_hours_by_zone` when no truck-specific time is present. Boats use
  `trip_hours_by_zone`. Whole trips only are counted.
- A boat assigned to evacuation is not also available to carry supplies in the
  same response window. Supply delivery uses only the boats and trucks
  allocated to Logistics, preventing same-window double use.
- Food/water supply coverage is delivered units divided by horizon need;
  days-of-cover is delivered stock divided by daily zone need. A zone is
  delivery-limited when its assigned vehicles cannot carry the full horizon
  need. Undeliverable stock is inventory left over because delivery capacity
  is lower than remaining stock, not an additional unmet-demand figure.
