from typing import Any

from lib.schemas import Scenario
from tools.common import find_zone, get_tool_error, normalize_scenario
from tools.failure import check_failure


def zone_population(
    scenario: Scenario | dict[str, Any],
    zone: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Query demographic and vulnerability data for disaster zones.

    Args:
        scenario: Disaster Scenario model or dict.
        zone: Optional specific zone to query.
        **kw: Additional kwargs (e.g. zone_name, name).

    Returns:
        dict containing population, stranded, and critical patient counts.
    """
    check_failure("zone_population")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name") or kw.get("name")

    if target:
        z = find_zone(sc, target)
        if not z:
            known = [zn.name for zn in sc.zones]
            raise get_tool_error()(
                f"Zone '{target}' not found in scenario. Known zones: {known}"
            )

        safe_pop = max(0, z.population - z.stranded - z.critical_patients)
        return {
            "zone": z.name,
            "population": z.population,
            "stranded": z.stranded,
            "critical_patients": z.critical_patients,
            "road_access": z.road_access,
            "safe_population": safe_pop,
            "evacuation_needed": z.stranded + z.critical_patients,
        }

    return {
        "total_population": sum(z.population for z in sc.zones),
        "total_stranded": sum(z.stranded for z in sc.zones),
        "total_critical_patients": sum(z.critical_patients for z in sc.zones),
        "zones": {
            z.name: {
                "population": z.population,
                "stranded": z.stranded,
                "critical_patients": z.critical_patients,
                "road_access": z.road_access,
            }
            for z in sc.zones
        },
    }
