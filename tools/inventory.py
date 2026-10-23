from typing import Any

from lib.schemas import Scenario
from lib.feasibility import canonical_resource, days_of_cover, delivery_capacity, supply_need
from tools.common import get_tool_error, normalize_scenario
from tools.failure import check_failure


def vehicle_inventory(
    scenario: Scenario | dict[str, Any],
    vehicle_type: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query vehicle inventory, counts, and operational availability.

    Args:
        scenario: Disaster Scenario model or dict.
        vehicle_type: Optional vehicle category (e.g. boats, trucks).
        **kw: Additional kwargs (e.g. vehicle, resource, name).

    Returns:
        dict containing vehicle inventory status.
    """
    check_failure("vehicle_inventory")
    sc = normalize_scenario(scenario)
    target = vehicle_type or kw.get("vehicle") or kw.get("resource") or kw.get("name")

    if target:
        key = target.strip().lower()
        matched_key = None
        count = 0
        for r_key, r_val in sc.resources.items():
            rk = r_key.strip().lower()
            if rk == key or rk == f"{key}s" or f"{rk}s" == key:
                matched_key = r_key
                count = r_val
                break

        return {
            "vehicle_type": target,
            "resource_key": matched_key or target,
            "count": count,
            "available": count,
            "operational": count > 0,
        }

    vehicles = {k: v for k, v in sc.resources.items()
                if canonical_resource(k) not in {"food_packs", "water_liters"}}
    return {
        "vehicles": vehicles,
        "total_vehicles": sum(vehicles.values()),
        "available_types": [k for k, v in vehicles.items() if v > 0],
    }


def stock_levels(
    scenario: Scenario | dict[str, Any],
    item: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query stockpile quantities and resource reserves.

    Args:
        scenario: Disaster Scenario model or dict.
        item: Optional item or resource name to query.
        **kw: Additional kwargs (e.g. resource, name).

    Returns:
        dict containing stockpile inventory metrics.
    """
    check_failure("stock_levels")
    sc = normalize_scenario(scenario)
    target = item or kw.get("resource") or kw.get("name")

    if target:
        key = canonical_resource(target)
        matched_key = next((r_key for r_key in sc.resources
                            if canonical_resource(r_key) == key), None)
        quantity = sc.resources.get(matched_key) if matched_key is not None else None
        supply_configured = (sc.operations.supply_horizon_days is not None
                             and key in {"food_packs", "water_liters"})
        if supply_configured:
            people = sum(supply_need(zone, sc.operations)["people_in_need"] for zone in sc.zones)
            rate = (sc.operations.food_packs_per_person_day if key == "food_packs"
                    else sc.operations.water_liters_per_person_day)
            cover = days_of_cover(quantity, int(people), rate)
            status = "unknown" if cover is None else "critical" if cover < 1 else "low" if cover < 2 else "sufficient"
            return {"item": target, "resource_key": matched_key or key,
                    "quantity": quantity, "days_of_cover": cover, "status": status}
        status = ("unknown" if quantity is None else
                  "sufficient" if quantity >= 10 else "low" if quantity > 0 else "depleted")
        return {
            "item": target,
            "resource_key": matched_key or target,
            "quantity": quantity,
            "status": status,
        }

    return {
        "stocks": dict(sc.resources),
        "total_items": sum(sc.resources.values()),
        "in_stock": [k for k, v in sc.resources.items() if v > 0],
        "depleted": [k for k, v in sc.resources.items() if v <= 0],
    }


def supply_needs(scenario: Scenario | dict[str, Any], zone: str | None = None,
                 **kw: Any) -> dict[str, Any]:
    """Report food/water horizon needs and one-vehicle route capacity by zone."""
    check_failure("supply_needs")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name")
    selected = [z for z in sc.zones if not target or z.name.casefold() == str(target).casefold()]
    result: dict[str, Any] = {}
    for item in selected:
        need = supply_need(item, sc.operations)
        truck = delivery_capacity(sc, item, sc.operations.supply_horizon_days or 0,
                                  {"trucks": 1})
        boat = delivery_capacity(sc, item, sc.operations.supply_horizon_days or 0,
                                 {"boats": 1})
        result[item.name] = {
            **need,
            "feasible_route_modes": sorted(set(truck["modes"] + boat["modes"])),
            "per_vehicle_capacity": {
                "truck": {"food_packs": truck["food_packs"], "water_liters": truck["water_liters"]},
                "boat": {"food_packs": boat["food_packs"], "water_liters": boat["water_liters"]},
            },
        }
    return {"zones": result, "horizon_days": sc.operations.supply_horizon_days}
