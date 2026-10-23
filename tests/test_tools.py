import pytest

from lib.schemas import Scenario
from lib.feasibility import demand_by_zone
from lib.toolcall import ToolError, ToolResult, call_tool
from tools.casualty import casualty_estimate
from tools.failure import (
    ToolTimeoutError,
    check_failure,
    is_failing,
    reset_failure,
    set_failure,
    set_random_mode,
    set_timeout_mode,
)
from tools.hospital import GROUND_ACCESS_THRESHOLD, hospital_capacity, receiving_capacity, transport_options
from tools.inventory import stock_levels, supply_needs, vehicle_inventory
from tools.population import zone_population
from tools.registry import TOOLS
from tools.roads import road_status
from tools.shelter import shelter_capacity


@pytest.fixture(autouse=True)
def clean_failure_state():
    """Ensure failure injection is reset before and after every test."""
    reset_failure()
    yield
    reset_failure()


def test_registry_contains_required_tools():
    expected_tools = {
        "hospital_capacity",
        "casualty_estimate",
        "shelter_capacity",
        "zone_population",
        "road_status",
        "vehicle_inventory",
        "stock_levels",
        "supply_needs",
    }
    assert expected_tools.issubset(set(TOOLS.keys()))
    assert len(TOOLS) == 10
    for tool_name in expected_tools:
        assert callable(TOOLS[tool_name])


def test_hospital_capacity_summary(sample_scenario):
    data = hospital_capacity(sample_scenario)
    assert isinstance(data, dict)
    assert "hospitals" in data
    assert data["hospitals"] == [item.model_dump() for item in sample_scenario.operations.hospitals]
    assert data["verified"] is False
    assert data["hospitals"][0]["icu_beds_free"] is None


def test_hospital_capacity_specific(sample_scenario):
    data = hospital_capacity(sample_scenario, hospital="Regional Hospital")
    assert data["hospitals"] == [sample_scenario.operations.hospitals[0].model_dump()]
    assert data["verified"] is False
    assert data == receiving_capacity(sample_scenario, hospital="Regional Hospital")


def test_transport_options_reflects_blocked_roads_and_unknown_capacity(sample_scenario):
    sample_scenario.zones[0].road_access = GROUND_ACCESS_THRESHOLD - 0.01
    sample_scenario.roads["Zone B"] = "landslide"
    sample_scenario.operations.boat_capacity_persons = None
    data = transport_options(sample_scenario)
    options = {item["zone"]: item for item in data["options"]}
    assert options["Zone A"]["ground_feasible"] is False
    assert options["Zone B"]["ground_feasible"] is False
    assert options["Zone A"]["boat_capacity_persons"] is None
    assert options["Zone A"]["verified"] is False


def test_hospital_capacity_unknown_raises(sample_scenario):
    with pytest.raises(ToolError, match="not found"):
        hospital_capacity(sample_scenario, hospital="NonExistent Hospital")


def test_casualty_estimate_summary(sample_scenario):
    data = casualty_estimate(sample_scenario)
    assert "total_critical_patients" in data
    assert "zones" in data
    assert data["total_critical_patients"] == 12  # Zone B has 12 in sample_scenario
    assert "Zone A" in data["zones"]
    assert "Zone B" in data["zones"]


def test_casualty_estimate_specific(sample_scenario):
    data = casualty_estimate(sample_scenario, zone="Zone B")
    assert data["zone"] == "Zone B"
    assert data["critical_patients"] == 12
    assert data["urgency"] == "CRITICAL"
    assert data["triage_priority"] == 1


def test_casualty_estimate_unknown_raises(sample_scenario):
    with pytest.raises(ToolError, match="not found"):
        casualty_estimate(sample_scenario, zone="Zone Z")


def test_shelter_capacity_summary(sample_scenario):
    data = shelter_capacity(sample_scenario)
    assert "total_stranded" in data
    assert "shelters" in data
    assert data["total_stranded"] == 312  # 300 in Zone A + 12 in Zone B


def test_shelter_capacity_specific(sample_scenario):
    data = shelter_capacity(sample_scenario, zone="Zone A")
    assert data["zone"] == "Zone A"
    assert data["stranded_seeking_shelter"] == 300
    assert "available_capacity" in data
    assert "shelter_deficit" in data


def test_shelter_capacity_unknown_raises(sample_scenario):
    with pytest.raises(ToolError, match="not found"):
        shelter_capacity(sample_scenario, zone="Unknown Zone")


def test_zone_population_summary(sample_scenario):
    data = zone_population(sample_scenario)
    assert data["total_population"] == 620  # 500 + 120
    assert data["total_stranded"] == 312
    assert "Zone A" in data["zones"]


def test_zone_population_specific(sample_scenario):
    data = zone_population(sample_scenario, zone="Zone A")
    assert data["zone"] == "Zone A"
    assert data["population"] == 500
    assert data["stranded"] == 300
    assert data["road_access"] == 0.5


def test_zone_population_unknown_raises(sample_scenario):
    with pytest.raises(ToolError, match="not found"):
        zone_population(sample_scenario, zone="Zone 99")


def test_road_status_summary(sample_scenario):
    data = road_status(sample_scenario)
    assert "roads" in data
    assert "passable_zones" in data
    assert "impassable_zones" in data
    assert "Zone A" in data["roads"]


def test_road_status_specific(sample_scenario):
    data = road_status(sample_scenario, zone="Zone A")
    assert data["zone"] == "Zone A"
    assert data["condition"] == "blocked"
    assert data["passable"] is False
    assert data["recommended_transport"] == "boats_or_airlift"


def test_road_status_unknown_raises(sample_scenario):
    with pytest.raises(ToolError, match="not found"):
        road_status(sample_scenario, zone="Highway 404")


def test_vehicle_inventory_summary(sample_scenario):
    data = vehicle_inventory(sample_scenario)
    assert "vehicles" in data
    assert data["total_vehicles"] == 3  # 3 boats in scenario.json
    assert "boats" in data["available_types"]


def test_vehicle_inventory_specific(sample_scenario):
    data = vehicle_inventory(sample_scenario, vehicle_type="boats")
    assert data["count"] == 3
    assert data["operational"] is True

    # Singular variant
    data_singular = vehicle_inventory(sample_scenario, vehicle_type="boat")
    assert data_singular["count"] == 3

    # Unknown vehicle
    data_helicopter = vehicle_inventory(sample_scenario, vehicle_type="helicopter")
    assert data_helicopter["count"] == 0
    assert data_helicopter["operational"] is False


def test_stock_levels_summary(sample_scenario):
    data = stock_levels(sample_scenario)
    assert "stocks" in data
    assert "boats" in data["in_stock"]


def test_stock_levels_specific(sample_scenario):
    data = stock_levels(sample_scenario, item="boats")
    assert data["quantity"] == 3
    assert data["status"] == "low"


def test_supply_tools_follow_scenario_and_preserve_unknown_stock(sample_scenario):
    food = stock_levels(sample_scenario, item="food")
    assert food["resource_key"] == "food_packs"
    assert food["quantity"] == sample_scenario.resources["food_packs"]
    expected = demand_by_zone(sample_scenario.zones[0]).total_union
    assert food["days_of_cover"] is not None
    needs = supply_needs(sample_scenario)
    assert needs["zones"][sample_scenario.zones[0].name]["people_in_need"] == expected

    unknown_rate = sample_scenario.model_copy(deep=True)
    unknown_rate.operations.food_packs_per_person_day = None
    unknown_rate.operations.water_liters_per_person_day = None
    unknown_needs = supply_needs(unknown_rate)
    assert unknown_needs["zones"][sample_scenario.zones[0].name]["food_packs"] is None
    assert unknown_needs["zones"][sample_scenario.zones[0].name]["water_liters"] is None

    unknown = sample_scenario.model_copy(deep=True)
    unknown.resources.pop("food_packs", None)
    assert stock_levels(unknown, item="food")["quantity"] is None


def test_supply_needs_is_failure_injectable(sample_scenario, tracer):
    set_failure("supply_needs")
    result = call_tool("supply_needs", sample_scenario, tracer)
    assert result.ok is False
    assert "injected failure" in result.error


def test_call_tool_all_registered_tools(sample_scenario, tracer):
    for tool_name in TOOLS:
        res = call_tool(tool_name, sample_scenario, tracer)
        assert res.ok is True
        assert res.data is not None
        assert res.error is None


def test_failure_injection_single_tool(sample_scenario, tracer):
    set_failure("hospital_capacity")
    assert is_failing("hospital_capacity") is True
    assert is_failing("road_status") is False

    res_hosp = call_tool("hospital_capacity", sample_scenario, tracer)
    assert res_hosp.ok is False
    assert "injected failure" in res_hosp.error

    res_road = call_tool("road_status", sample_scenario, tracer)
    assert res_road.ok is True


def test_failure_injection_wildcard(sample_scenario, tracer):
    set_failure("*")
    for tool_name in TOOLS:
        assert is_failing(tool_name) is True
        res = call_tool(tool_name, sample_scenario, tracer)
        assert res.ok is False
        assert "injected failure" in res.error


def test_failure_injection_reset(sample_scenario, tracer):
    set_failure("*")
    assert is_failing("casualty_estimate") is True

    set_failure(None)
    assert is_failing("casualty_estimate") is False

    res = call_tool("casualty_estimate", sample_scenario, tracer)
    assert res.ok is True


def test_failure_timeout_mode(sample_scenario, tracer):
    set_timeout_mode(enabled=True, timeout_sec=0.0)
    assert is_failing("zone_population") is True

    with pytest.raises(ToolTimeoutError, match="timed out"):
        zone_population(sample_scenario)

    res = call_tool("zone_population", sample_scenario, tracer)
    assert res.ok is False
    assert "timed out" in res.error


def test_failure_random_mode(sample_scenario, tracer):
    # Rate 1.0 -> always fails
    set_random_mode(enabled=True, rate=1.0)
    assert is_failing("stock_levels") is True
    res_fail = call_tool("stock_levels", sample_scenario, tracer)
    assert res_fail.ok is False
    assert "random failure mode" in res_fail.error

    # Rate 0.0 -> never fails
    set_random_mode(enabled=True, rate=0.0)
    assert is_failing("stock_levels") is False
    res_ok = call_tool("stock_levels", sample_scenario, tracer)
    assert res_ok.ok is True


def test_failure_named_mode(sample_scenario, tracer):
    set_failure("road_status", mode="timeout")
    assert is_failing("road_status") is True
    assert is_failing("vehicle_inventory") is False

    res = call_tool("road_status", sample_scenario, tracer)
    assert res.ok is False
    assert "timed out" in res.error

    res_other = call_tool("vehicle_inventory", sample_scenario, tracer)
    assert res_other.ok is True


def test_failure_string_shortcuts(sample_scenario, tracer):
    # set_failure("random")
    set_failure("random")
    assert is_failing("hospital_capacity") in (True, False)  # probabilistic
    set_random_mode(enabled=True, rate=1.0)
    assert is_failing("hospital_capacity") is True

    # set_failure("timeout")
    set_failure("timeout")
    assert is_failing("hospital_capacity") is True
    res = call_tool("hospital_capacity", sample_scenario, tracer)
    assert res.ok is False
    assert "timed out" in res.error
