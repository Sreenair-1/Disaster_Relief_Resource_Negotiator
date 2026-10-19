from typing import Any

from lib.schemas import Scenario, Zone


def get_tool_error() -> type[Exception]:
    """Dynamically import and return ToolError to avoid top-level circular imports."""
    from lib.toolcall import ToolError

    return ToolError


def normalize_scenario(scenario: Scenario | dict[str, Any]) -> Scenario:
    """Ensure the scenario parameter is an instance of Scenario."""
    if isinstance(scenario, dict):
        return Scenario.model_validate(scenario)
    return scenario


def find_zone(scenario: Scenario, target_name: str) -> Zone | None:
    """Find a zone by exact or case-insensitive name match."""
    t = target_name.strip().lower()
    for z in scenario.zones:
        if z.name.strip().lower() == t:
            return z
    for z in scenario.zones:
        zn_lower = z.name.strip().lower()
        if zn_lower.endswith(t) or t.endswith(zn_lower):
            return z
    return None
