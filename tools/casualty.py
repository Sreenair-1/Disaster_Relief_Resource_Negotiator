from typing import Any

from lib.schemas import Scenario
from tools.common import find_zone, get_tool_error, normalize_scenario
from tools.failure import check_failure


def casualty_estimate(
    scenario: Scenario | dict[str, Any],
    zone: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Estimate casualties, critical patients, and triage urgency.

    Args:
        scenario: Disaster Scenario model or dict.
        zone: Optional specific zone to query.
        **kw: Additional kwargs (e.g. zone_name, name).

    Returns:
        dict containing casualty estimates and triage priorities.
    """
    check_failure("casualty_estimate")
    sc = normalize_scenario(scenario)
    target = zone or kw.get("zone_name") or kw.get("name")

    if target:
        z = find_zone(sc, target)
        if not z:
            known = [zn.name for zn in sc.zones]
            raise get_tool_error()(
                f"Zone '{target}' not found in scenario. Known zones: {known}"
            )

        estimated_injured = int(z.critical_patients * 2 + z.stranded * 0.1)
        urgency = (
            "CRITICAL"
            if z.critical_patients > 10
            else ("HIGH" if z.critical_patients > 0 else "MODERATE")
        )

        return {
            "zone": z.name,
            "critical_patients": z.critical_patients,
            "estimated_injured": estimated_injured,
            "stranded_at_risk": z.stranded,
            "total_population": z.population,
            "urgency": urgency,
            "triage_priority": 1 if z.critical_patients > 10 else (2 if z.critical_patients > 0 else 3),
        }

    total_critical = sum(z.critical_patients for z in sc.zones)
    total_injured = sum(int(z.critical_patients * 2 + z.stranded * 0.1) for z in sc.zones)
    total_stranded = sum(z.stranded for z in sc.zones)

    zones_summary = {}
    for z in sc.zones:
        zones_summary[z.name] = {
            "critical_patients": z.critical_patients,
            "estimated_injured": int(z.critical_patients * 2 + z.stranded * 0.1),
            "stranded": z.stranded,
            "urgency": (
                "CRITICAL"
                if z.critical_patients > 10
                else ("HIGH" if z.critical_patients > 0 else "MODERATE")
            ),
        }

    return {
        "total_critical_patients": total_critical,
        "total_estimated_injured": total_injured,
        "total_stranded": total_stranded,
        "zones": zones_summary,
    }
