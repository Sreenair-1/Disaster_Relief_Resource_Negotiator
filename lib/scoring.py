from collections import defaultdict

from lib.schemas import Claim, Scenario


def score_claim(claim: Claim, scenario: Scenario) -> float:
    """Return a deterministic priority score for a claim."""
    time_criticality = 1 / max(claim.time_window_hrs, 1)
    access = min((zone.road_access for zone in scenario.zones), default=1.0)
    return claim.people_affected * claim.urgency * time_criticality * access


def over_subscribed(claims: list[Claim], scenario: Scenario) -> dict[str, int]:
    requested: defaultdict[str, int] = defaultdict(int)
    for claim in claims:
        for resource, amount in claim.resources.items():
            requested[resource] += amount
    return {
        resource: amount - scenario.resources.get(resource, 0)
        for resource, amount in requested.items()
        if amount > scenario.resources.get(resource, 0)
    }
