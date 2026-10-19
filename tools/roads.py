from typing import Any

from lib.schemas import Scenario
from tools.common import find_zone, get_tool_error, normalize_scenario
from tools.failure import check_failure


def road_status(
    scenario: Scenario | dict[str, Any],
    zone: str | None = None,
    road: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query road passability, condition, and transport recommendations.

    Args:
        scenario: Disaster Scenario model or dict.
        zone: Optional zone name to check.
        road: Optional road name to check.
        **kw: Additional kwargs (e.g. zone_name, name).

    Returns:
        dict containing road conditions and access recommendations.
    """
    check_failure("road_status")
    sc = normalize_scenario(scenario)
    target = zone or road or kw.get("zone_name") or kw.get("name")

    if target:
        z = find_zone(sc, target)
        road_condition = None
        for r_name, r_cond in sc.roads.items():
            if r_name.strip().lower() == target.strip().lower():
                road_condition = r_cond
                break

        if z is None and road_condition is None:
            known_roads = list(sc.roads.keys())
            known_zones = [zn.name for zn in sc.zones]
            raise get_tool_error()(
                f"Road/Zone '{target}' not found. Known zones: {known_zones}, known roads: {known_roads}"
            )

        condition = road_condition or "clear"
        road_access = (
            z.road_access
            if z
            else (0.0 if str(condition).lower() in ("blocked", "flooded", "closed") else 1.0)
        )
        passable = (
            road_access >= 0.5
            and str(condition).lower() not in ("blocked", "flooded", "impassable", "closed")
        )

        return {
            "zone": z.name if z else target,
            "condition": str(condition),
            "road_access": road_access,
            "passable": passable,
            "recommended_transport": (
                "ground_vehicles" if passable else "boats_or_airlift"
            ),
        }

    passable_zones = []
    impassable_zones = []
    for z in sc.zones:
        r_cond = sc.roads.get(z.name, "clear")
        is_passable = (
            z.road_access >= 0.5
            and str(r_cond).lower() not in ("blocked", "flooded", "impassable", "closed")
        )
        if is_passable:
            passable_zones.append(z.name)
        else:
            impassable_zones.append(z.name)

    return {
        "roads": sc.roads,
        "zone_access": {z.name: z.road_access for z in sc.zones},
        "passable_zones": passable_zones,
        "impassable_zones": impassable_zones,
    }
