"""Deterministic demand partitioning and transport feasibility checks."""

from dataclasses import dataclass
from math import ceil, floor
import re

from lib.schemas import Claim, Operations, Scenario, Zone


@dataclass(frozen=True)
class ZoneDemand:
    critical_patients: int
    stranded_non_critical: int
    supply_dependent: int
    total_union: int
    overlaps: tuple[str, ...] = ()
    inferred_supply_dependent: bool = False


@dataclass(frozen=True)
class ClaimDemandCheck:
    claim: Claim
    requested_people: int
    eligible_people: int
    overlaps: tuple[str, ...] = ()


def validate_claim_facts(claim: Claim, scenario: Scenario) -> tuple[Claim, list[str]]:
    """Correct explicit capacity facts that disagree with known scenario data."""
    text = claim.justification
    corrections: list[str] = []
    capacity = scenario.operations.boat_capacity_persons
    if capacity is not None:
        patterns = (
            re.compile(r"(?P<full>(?:about\s+|~\s*)?\d[\d,]*\s*(?:people|persons)\s+per\s+boat)", re.I),
            re.compile(r"(?P<full>boats?\s+(?:can\s+)?carry\s+\d[\d,]*\s*(?:people|persons)?)", re.I),
            re.compile(r"(?P<full>capacity\s+of\s+(?:each\s+)?boat\s*(?:is|=)\s*\d[\d,]*)", re.I),
        )
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                old = re.search(r"\d[\d,]*", match.group("full")).group().replace(",", "")
                if int(old) != capacity:
                    text = text[:match.start()] + f"{capacity} persons per boat" + text[match.end():]
                    corrections.append(f"boat capacity corrected from {old} to {capacity} persons")
                    break

    zone_names = [z.name for z in scenario.zones if z.name.casefold() in text.casefold()]
    roundtrips = {scenario.operations.trip_hours_by_zone[name] for name in zone_names
                  if name in scenario.operations.trip_hours_by_zone}
    if not roundtrips and len(set(scenario.operations.trip_hours_by_zone.values())) == 1:
        roundtrips = set(scenario.operations.trip_hours_by_zone.values())
    if len(roundtrips) == 1:
        expected = next(iter(roundtrips))
        pattern = re.compile(r"(?P<full>\d+(?:\.\d+)?\s*[- ]?hours?\s+(?:round[- ]?trip|round trip time))", re.I)
        match = pattern.search(text)
        if match:
            old = float(re.search(r"\d+(?:\.\d+)?", match.group()).group())
            if old != expected:
                text = text[:match.start()] + f"{expected:g}-hour round trip" + text[match.end():]
                corrections.append(f"round-trip time corrected from {old:g} to {expected:g} hours")

    updates = {"justification": text, "contradicts_data": bool(corrections)} if corrections else {}
    return (claim.model_copy(update=updates) if updates else claim), corrections


def demand_by_zone(zone: Zone) -> ZoneDemand:
    """Partition a zone population into disjoint demand groups.

    Critical patients take precedence, followed by stranded non-critical
    residents, then supply-dependent residents. Missing legacy
    ``supply_dependent`` values use the remaining population as the last group.
    """
    population = zone.population
    overlaps: list[str] = []
    if zone.critical_patients > population:
        overlaps.append("critical_patients exceed population")
    if zone.stranded > population:
        overlaps.append("stranded exceeds population")

    critical = min(zone.critical_patients, population)
    stranded_non_critical = min(
        max(0, zone.stranded - critical), population - critical
    )
    remaining = population - critical - stranded_non_critical
    inferred = zone.supply_dependent is None
    supply_requested = remaining if inferred else zone.supply_dependent or 0
    if supply_requested > remaining:
        overlaps.append("supply_dependent overlaps another demand group")
    supply_dependent = min(supply_requested, remaining)
    total_union = critical + stranded_non_critical + supply_dependent

    return ZoneDemand(
        critical_patients=critical,
        stranded_non_critical=stranded_non_critical,
        supply_dependent=supply_dependent,
        total_union=total_union,
        overlaps=tuple(overlaps),
        inferred_supply_dependent=inferred,
    )


def demand_totals(scenario: Scenario) -> dict[str, int | list[str]]:
    """Return disjoint scenario totals and any population-overlap findings."""
    partitions = [(zone.name, demand_by_zone(zone)) for zone in scenario.zones]
    return {
        "critical_patients": sum(part.critical_patients for _, part in partitions),
        "stranded_non_critical": sum(part.stranded_non_critical for _, part in partitions),
        "supply_dependent": sum(part.supply_dependent for _, part in partitions),
        "total_union": sum(part.total_union for _, part in partitions),
        "overlaps": [f"{name}: {issue}" for name, part in partitions for issue in part.overlaps],
    }


def demand_cap_for_agent(agent: str, scenario: Scenario) -> int:
    field = {
        "medical": "critical_patients",
        "shelter": "stranded_non_critical",
        "logistics": "supply_dependent",
    }.get(agent)
    if field is None:
        return sum(demand_by_zone(zone).total_union for zone in scenario.zones)
    return sum(getattr(demand_by_zone(zone), field) for zone in scenario.zones)


def validate_claim_demand(claim: Claim, scenario: Scenario) -> ClaimDemandCheck:
    """Clamp a claim to its non-overlapping demand group and report excess."""
    cap = demand_cap_for_agent(claim.agent, scenario)
    eligible = min(claim.people_affected, cap)
    checked = (
        claim.model_copy(update={"people_affected": eligible})
        if eligible != claim.people_affected else claim
    )
    partition_overlaps = tuple(demand_totals(scenario)["overlaps"])
    claim_overlap = (
        (f"{claim.agent} claimed {claim.people_affected} people; partition allows {cap}",)
        if eligible != claim.people_affected else ()
    )
    return ClaimDemandCheck(
        claim=checked,
        requested_people=claim.people_affected,
        eligible_people=eligible,
        overlaps=(*partition_overlaps, *claim_overlap),
    )


def boats_needed(
    zone: Zone,
    people: int,
    window_hrs: float,
    operations: Operations,
) -> int | None:
    """Return boats required using capacity and whole round trips, or None if unknown."""
    capacity = operations.boat_capacity_persons
    round_trip = operations.trip_hours_by_zone.get(zone.name)
    if capacity is None or round_trip is None or round_trip <= 0:
        return None
    trips_per_boat = floor(window_hrs / round_trip)
    if trips_per_boat <= 0:
        return None
    return ceil(max(0, people) / (capacity * trips_per_boat))


def route_feasible(zone: Zone, mode: str) -> bool | None:
    """Check ground-route access; unknown modes and water routes remain unverified."""
    normalized_mode = mode.casefold().replace("-", "_")
    if normalized_mode in {"road", "ground", "ambulance", "truck", "boat_by_road"}:
        return zone.road_access > 0
    return None


def unit_cap(claim: Claim, scenario: Scenario, resource: str) -> int:
    """Maximum useful units of a requested resource under known scenario capacity."""
    requested = max(0, claim.resources.get(resource, 0))
    if resource == "shelter_beds":
        return min(requested, claim.people_affected)
    if resource != "boats":
        return requested

    capacity = scenario.operations.boat_capacity_persons
    if capacity is None:
        return requested
    group = {
        "medical": "critical_patients",
        "shelter": "stranded_non_critical",
        "logistics": "supply_dependent",
    }.get(claim.agent)
    relevant = [(zone, demand_by_zone(zone)) for zone in scenario.zones]
    if group:
        relevant = [(zone, part) for zone, part in relevant if getattr(part, group) > 0]
    if not relevant or any(
        zone.name not in scenario.operations.trip_hours_by_zone for zone, _ in relevant
    ):
        return requested
    trips = [
        floor(claim.time_window_hrs / scenario.operations.trip_hours_by_zone[zone.name])
        for zone, _ in relevant
    ]
    if any(count <= 0 for count in trips):
        return 0
    people = min(claim.people_affected, demand_cap_for_agent(claim.agent, scenario))
    capacity_per_boat = capacity * min(trips)
    if capacity_per_boat <= 0:
        return 0
    return min(requested, ceil(people / capacity_per_boat))
