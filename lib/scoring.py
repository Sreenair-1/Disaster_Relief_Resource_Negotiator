from collections import defaultdict

from lib.schemas import Claim, Scenario


def _claim_zones(claim: Claim, scenario: Scenario):
    """Find zones named by a claim, otherwise use the agent's concern domain."""
    text = f"{claim.justification} {claim.fallback_option}".casefold()
    named = [zone for zone in scenario.zones if zone.name.casefold() in text]
    if named:
        return named

    if claim.agent == "medical":
        concerned = [zone for zone in scenario.zones if zone.critical_patients > 0]
    elif claim.agent == "shelter":
        concerned = [zone for zone in scenario.zones if zone.stranded > 0]
    else:
        concerned = []
    return concerned or scenario.zones


def _people_weight(claim: Claim, zone) -> int:
    if claim.agent == "medical":
        return zone.critical_patients or zone.population
    if claim.agent == "shelter":
        return zone.stranded or zone.population
    return zone.population


def road_access_factor(claim: Claim, scenario: Scenario) -> float:
    """Return relevant, people-weighted road access, with a non-zero floor."""
    zones = _claim_zones(claim, scenario)
    if not zones:
        return 1.0
    weights = [_people_weight(claim, zone) for zone in zones]
    total_weight = sum(weights)
    access = (
        sum(zone.road_access * weight for zone, weight in zip(zones, weights)) / total_weight
        if total_weight
        else sum(zone.road_access for zone in zones) / len(zones)
    )
    return max(0.05, access)


def score_claim(claim: Claim, scenario: Scenario) -> float:
    """Score people at risk by urgency, time criticality, and relevant access."""
    time_criticality = 1 / max(claim.time_window_hrs, 1)
    return (
        claim.people_affected
        * claim.urgency
        * time_criticality
        * road_access_factor(claim, scenario)
    )


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
