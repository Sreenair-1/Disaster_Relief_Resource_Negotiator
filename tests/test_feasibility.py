from lib.feasibility import (
    boats_needed,
    demand_by_zone,
    demand_totals,
    route_feasible,
    unit_cap,
    validate_claim_demand,
    validate_claim_facts,
)
from lib.schemas import Claim, Operations, Scenario, Zone


def test_demand_partition_is_disjoint_and_reports_overlaps():
    zone = Zone(name="A", population=100, stranded=80, critical_patients=20,
                supply_dependent=30)
    partition = demand_by_zone(zone)
    assert partition.critical_patients == 20
    assert partition.stranded_non_critical == 60
    assert partition.supply_dependent == 20
    assert partition.total_union == 100
    assert partition.overlaps == ("supply_dependent overlaps another demand group",)


def test_legacy_demand_defaults_to_remaining_population():
    zone = Zone(name="A", population=100, stranded=50, critical_patients=10)
    partition = demand_by_zone(zone)
    assert partition.stranded_non_critical == 40
    assert partition.supply_dependent == 50
    assert partition.total_union == 100
    assert partition.inferred_supply_dependent


def test_claim_people_are_clamped_to_non_overlapping_agent_partition():
    scenario = Scenario.model_validate({
        "disaster_type": "flood",
        "zones": [{"name": "A", "population": 100, "stranded": 50,
                   "critical_patients": 10, "supply_dependent": 20}],
    })
    claim = Claim(agent="shelter", resources={}, people_affected=50, urgency=5,
                  time_window_hrs=2, justification="Zone A", fallback_option="phase")
    checked = validate_claim_demand(claim, scenario)
    assert checked.claim.people_affected == 40
    assert checked.overlaps
    assert demand_totals(scenario)["total_union"] == 70


def test_boat_need_and_unit_cap_use_round_trip_capacity():
    zone = Zone(name="A", population=100, critical_patients=20)
    operations = Operations(boat_capacity_persons=10, trip_hours_by_zone={"A": 2})
    assert boats_needed(zone, 20, 4, operations) == 1
    assert boats_needed(zone, 20, 1, operations) is None
    scenario = Scenario(disaster_type="flood", zones=[zone], operations=operations)
    claim = Claim(agent="medical", resources={"boats": 5}, people_affected=20,
                  urgency=10, time_window_hrs=4, justification="Zone A",
                  fallback_option="phase")
    assert unit_cap(claim, scenario, "boats") == 1


def test_zero_road_access_rejects_ground_fallback():
    assert route_feasible(Zone(name="A", population=1, road_access=0), "ambulance") is False


def test_claim_capacity_fact_is_corrected_to_scenario_value(sample_scenario):
    claim = Claim(agent="shelter", resources={"boats": 4}, people_affected=300,
                  urgency=8, time_window_hrs=4,
                  justification="A boat carries about 1,300 persons per boat in Zone A.",
                  fallback_option="wait")
    corrected, notes = validate_claim_facts(claim, sample_scenario)
    assert corrected.contradicts_data is True
    assert "50 persons per boat" in corrected.justification
    assert "1,300 persons per boat" not in corrected.justification
    assert notes == ["boat capacity corrected from 1300 to 50 persons"]
