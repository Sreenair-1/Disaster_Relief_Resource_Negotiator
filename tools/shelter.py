from typing import Any

from lib.schemas import Scenario
from tools.common import find_zone, get_tool_error, normalize_scenario
from tools.failure import check_failure


def shelter_capacity(
    scenario: Scenario | dict[str, Any],
    zone: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query shelter capacity, stranded residents seeking shelter, and space deficits.

    Args:
        scenario: Disaster Scenario model or dict.
        zone: Optional specific zone to query.
        **kw: Additional kwargs (e.g. zone_name, name, shelter).

    Returns:
        dict containing shelter metrics.
    """
    check_failure("shelter_capacity")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name") or kw.get("name") or kw.get("shelter")

    if target:
        z = find_zone(sc, target)
        if not z:
            known = [zn.name for zn in sc.zones]
            raise get_tool_error()(
                f"Zone '{target}' not found in scenario. Known zones: {known}"
            )

        total_cap = max(200, int(z.population * 0.5))
        available_cap = max(50, z.stranded + 50)
        deficit = max(0, z.stranded - available_cap)

        return {
            "zone": z.name,
            "shelter_name": f"{z.name} Relief Center",
            "total_capacity": total_cap,
            "available_capacity": available_cap,
            "stranded_seeking_shelter": z.stranded,
            "shelter_deficit": deficit,
            "status": "adequate" if deficit == 0 else "overflow_warning",
        }

    total_stranded = sum(z.stranded for z in sc.zones)
    shelters_map = {}
    for z in sc.zones:
        avail = max(50, z.stranded + 50)
        deficit = max(0, z.stranded - avail)
        shelters_map[z.name] = {
            "shelter_name": f"{z.name} Relief Center",
            "available_capacity": avail,
            "stranded": z.stranded,
            "deficit": deficit,
            "status": "adequate" if deficit == 0 else "overflow_warning",
        }

    return {
        "total_stranded": total_stranded,
        "total_available_capacity": sum(s["available_capacity"] for s in shelters_map.values()),
        "total_deficit": sum(s["deficit"] for s in shelters_map.values()),
        "shelters": shelters_map,
    }
