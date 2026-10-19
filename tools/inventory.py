from typing import Any

from lib.schemas import Scenario
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

    return {
        "vehicles": dict(sc.resources),
        "total_vehicles": sum(sc.resources.values()),
        "available_types": [k for k, v in sc.resources.items() if v > 0],
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
        key = target.strip().lower()
        matched_key = None
        quantity = 0
        for r_key, r_val in sc.resources.items():
            rk = r_key.strip().lower()
            if rk == key or rk == f"{key}s" or f"{rk}s" == key:
                matched_key = r_key
                quantity = r_val
                break

        status = "sufficient" if quantity >= 10 else ("low" if quantity > 0 else "depleted")
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
