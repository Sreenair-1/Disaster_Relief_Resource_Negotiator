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
