"""Replan orchestrator logic – determines which agents must re-evaluate or re-claim."""

from typing import Any

from lib.schemas import Claim, Scenario


def claim_references_zone(claim: Claim, zone_name: str) -> bool:
    """Check if an agent's claim references a specific zone in text or properties."""
    text = f"{claim.justification} {claim.fallback_option}".lower()
    zn = zone_name.lower().strip()
    if zn in text:
        return True

    # Handle common zone naming conventions like "Zone A" -> "z1", "zone 1", "zone a"
    if zn.startswith("zone "):
        ident = zn.split("zone ", 1)[1].strip()
        if f"zone {ident}" in text or f"z{ident}" in text or f"zone-{ident}" in text:
            return True
        if ident == "a" and ("z1" in text or "zone 1" in text):
            return True
        if ident == "b" and ("z2" in text or "zone 2" in text):
            return True

    # Word match for compound zone names (e.g. "Silchar Urban" -> "silchar")
    distinct_words = [
        w for w in zn.split()
        if len(w) > 3 and w not in ("zone", "area", "district", "plain", "urban", "rural")
    ]
    for w in distinct_words:
        if w in text:
            return True

    return False


def derive_affected_agents(
    before: Scenario,
    after: Scenario,
    claims: list[Claim] | None = None,
    llm_affected_agents: list[str] | None = None,
) -> list[str]:
    """Derive affected_agents from the scenario fields that actually changed.

    Rules:
    - road/route changed -> logistics plus any agent whose claim references that zone.
    - casualties/hospital changed -> medical.
    - stranded/shelter changed -> shelter.
    - resources changed -> logistics plus agents whose claims requested the resource
      (and LLM nominated agents for resource changes).
    When road access changes, every agent whose claim references that zone must re-claim.
    """
    affected: set[str] = set()
    claims = claims or []

    # 1. Road and route changes
    changed_road_zones: set[str] = set()
    before_zones = {z.name: z for z in before.zones}

    for az in after.zones:
        bz = before_zones.get(az.name)
        if bz and bz.road_access != az.road_access:
            changed_road_zones.add(az.name)

    if before.roads != after.roads:
        for rk, rcond in after.roads.items():
            if before.roads.get(rk) != rcond:
                for z in after.zones:
                    if z.name.lower() in rk.lower() or rk.lower() in z.name.lower():
                        changed_road_zones.add(z.name)
                if rk.strip().lower() in ("all", "*", "roads", "all roads", "every zone", "all zones"):
                    for z in after.zones:
                        changed_road_zones.add(z.name)

    if changed_road_zones or (before.roads != after.roads):
        affected.add("logistics")
        for claim in claims:
            for z_name in changed_road_zones:
                if claim_references_zone(claim, z_name):
                    affected.add(claim.agent)

    # 2. Casualties / hospital changes -> medical
    casualties_changed = any(
        bz and bz.critical_patients != az.critical_patients
        for az in after.zones
        if (bz := before_zones.get(az.name))
    )
    hospital_changed = before.hospitals != after.hospitals
    if casualties_changed or hospital_changed:
        affected.add("medical")

    # 3. Stranded / shelter changes -> shelter
    stranded_changed = any(
        bz and (bz.stranded != az.stranded or bz.population != az.population)
        for az in after.zones
        if (bz := before_zones.get(az.name))
    )
    if stranded_changed:
        affected.add("shelter")

    # 4. Resources changes
    if before.resources != after.resources:
        affected.add("logistics")
        changed_res = {
            k for k in set(before.resources.keys()) | set(after.resources.keys())
            if before.resources.get(k) != after.resources.get(k)
        }
        for claim in claims:
            if any(r in changed_res for r in claim.resources):
                affected.add(claim.agent)
        if llm_affected_agents:
            for a in llm_affected_agents:
                if a in ("medical", "shelter", "logistics"):
                    affected.add(a)

    # Order deterministically, respecting LLM nomination order where applicable
    ordered: list[str] = []
    if llm_affected_agents:
        for a in llm_affected_agents:
            if a in affected and a not in ordered:
                ordered.append(a)
    for a in ("logistics", "shelter", "medical"):
        if a in affected and a not in ordered:
            ordered.append(a)

    return ordered
