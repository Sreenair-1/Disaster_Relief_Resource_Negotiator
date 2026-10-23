from typing import Any

from lib.schemas import Scenario
from tools.common import get_tool_error, normalize_scenario
from tools.failure import check_failure

GROUND_ACCESS_THRESHOLD = 0.5


def hospital_capacity(
    scenario: Scenario | dict[str, Any],
    hospital: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query hospital capacity, occupancy, and ICU availability.

    Args:
        scenario: Disaster Scenario model or dict.
        hospital: Optional specific hospital name to query.
        **kw: Additional kwargs (e.g. hospital_name, name).

    Returns:
        dict containing hospital capacity metrics.
    """
    check_failure("hospital_capacity")
    sc = normalize_scenario(scenario)
    target = hospital or kw.get("hospital_name") or kw.get("name")

    if target:
        matched = next((h.name for h in sc.operations.hospitals
                        if h.name.strip().lower() == target.strip().lower()), None)
        if not matched:
            raise get_tool_error()(
                f"Hospital '{target}' not found in scenario. Known hospitals: {sc.hospitals}"
            )
        facilities = [h for h in sc.operations.hospitals if h.name == matched]
    else:
        facilities = sc.operations.hospitals
    return {
        "hospitals": [h.model_dump() for h in facilities],
        "verified": bool(facilities) and all(
            h.icu_beds_free is not None and h.accepts_transfers is not None
            and h.travel_hours is not None for h in facilities
        ),
    }


def receiving_capacity(scenario: Scenario | dict[str, Any], hospital: str | None = None, **kw):
    """Return explicit receiving-facility transfer/ICU data; unknown stays unknown."""
    check_failure("receiving_capacity")
    sc = normalize_scenario(scenario)
    target = hospital or kw.get("hospital_name") or kw.get("name")
    facilities = [h for h in sc.operations.hospitals
                  if target is None or h.name.casefold() == target.casefold()]
    if target and not facilities:
        raise get_tool_error()(f"Hospital '{target}' not found in scenario.")
    return {"hospitals": [h.model_dump() for h in facilities],
            "verified": bool(facilities) and all(
                h.icu_beds_free is not None and h.accepts_transfers is not None
                and h.travel_hours is not None
                for h in facilities)}


def transport_options(scenario: Scenario | dict[str, Any], zone: str | None = None, **kw):
    """Report only transports supported by explicit scenario fields/inventory."""
    check_failure("transport_options")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name")
    zones = [z for z in sc.zones if target is None or z.name.casefold() == target.casefold()]
    options = [{
        "zone": z.name,
        "ground_feasible": z.road_access >= GROUND_ACCESS_THRESHOLD
        and sc.roads.get(z.name, "").casefold() not in {"blocked", "landslide"},
        "ground_access": z.road_access,
        "boats_available": sc.resources.get("boats", 0),
        "boat_capacity_persons": sc.operations.boat_capacity_persons,
        "round_trip_hours": sc.operations.trip_hours_by_zone.get(z.name),
        "ambulance_capacity": sc.operations.ambulance_capacity,
    } for z in zones]
    required = ("ground_access", "boats_available", "boat_capacity_persons",
                "round_trip_hours", "ambulance_capacity")
    for option in options:
        option["verified"] = all(option[field] is not None for field in required)
    return {"options": options}
