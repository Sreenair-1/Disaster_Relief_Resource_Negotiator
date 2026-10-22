from typing import Any

from lib.schemas import Scenario
from tools.common import get_tool_error, normalize_scenario
from tools.failure import check_failure


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

    total_critical = sum(z.critical_patients for z in sc.zones)

    if target:
        matched = next(
            (h for h in sc.hospitals if h.strip().lower() == target.strip().lower()), None
        )
        if not matched:
            raise get_tool_error()(
                f"Hospital '{target}' not found in scenario. Known hospitals: {sc.hospitals}"
            )

        total_beds = 100
        critical_share = total_critical // max(len(sc.hospitals), 1)
        occupied_beds = min(total_beds, 70 + critical_share)
        available_beds = max(0, total_beds - occupied_beds)
        icu_total = 20
        icu_occupied = min(icu_total, 10 + critical_share)
        icu_available = max(0, icu_total - icu_occupied)

        return {
            "hospital": matched,
            "total_beds": total_beds,
            "occupied_beds": occupied_beds,
            "available_beds": available_beds,
            "icu_total": icu_total,
            "icu_occupied": icu_occupied,
            "icu_available": icu_available,
            "surge_capacity": 30,
            "status": "operational" if available_beds > 5 else "near_capacity",
        }

    hospitals_info = []
    for h in sc.hospitals:
        total_beds = 100
        critical_share = total_critical // max(len(sc.hospitals), 1)
        occupied_beds = min(total_beds, 70 + critical_share)
        available_beds = max(0, total_beds - occupied_beds)
        icu_total = 20
        icu_occupied = min(icu_total, 10 + critical_share)
        icu_available = max(0, icu_total - icu_occupied)
        hospitals_info.append(
            {
                "hospital": h,
                "total_beds": total_beds,
                "available_beds": available_beds,
                "icu_available": icu_available,
                "status": "operational" if available_beds > 5 else "near_capacity",
            }
        )

    return {
        "hospitals": hospitals_info,
        "count": len(hospitals_info),
        "total_available_beds": sum(h["available_beds"] for h in hospitals_info),
        "total_icu_available": sum(h["icu_available"] for h in hospitals_info),
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
                for h in facilities)}


def transport_options(scenario: Scenario | dict[str, Any], zone: str | None = None, **kw):
    """Report only transports supported by explicit scenario fields/inventory."""
    check_failure("transport_options")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name")
    zones = [z for z in sc.zones if target is None or z.name.casefold() == target.casefold()]
    return {"options": [{
        "zone": z.name,
        "ground_feasible": z.road_access > 0,
        "ground_access": z.road_access,
        "boats_available": sc.resources.get("boats", 0),
        "boat_capacity_persons": sc.operations.boat_capacity_persons,
        "round_trip_hours": sc.operations.trip_hours_by_zone.get(z.name),
        "ambulance_capacity": sc.operations.ambulance_capacity,
        "verified": z.road_access is not None,
    } for z in zones]}
