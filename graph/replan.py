"""Apply scenario events and renegotiate only affected claims."""

from typing import Any

from agents.logistics import make_claim as logistics_claim
from agents.mediator import finalize as mediator_finalize
from agents.medical import make_claim as medical_claim
from agents.scenario import inject_event
from agents.shelter import make_claim as shelter_claim
from lib.feasibility import demand_totals, validate_claim_demand
from lib.schemas import NegotiationState

CLAIMERS = {"medical": medical_claim, "shelter": shelter_claim, "logistics": logistics_claim}


def replan(state: NegotiationState, event_text: str, llm: Any, tracer: Any) -> NegotiationState:
    """Inject an event, refresh only affected agent claims, and reallocate.

    When road access changes for a zone, every agent whose existing claim
    references that zone is added to affected_agents so they re-claim with
    updated road conditions.
    """
    updated = inject_event(state, event_text, llm, tracer)

    # If inject_event rejected the event (no affected agents), skip replan
    if not updated.affected_agents:
        return updated

    tracer.log("feasibility", "mediator", "demand_partition", demand_totals(updated.scenario))

    updated.replans += 1

    # Extend affected agents: if road_access changed for a zone, include
    # every agent whose prior claim references that zone in its justification
    _extend_for_road_changes(updated, state)

    existing = {claim.agent: claim for claim in updated.claims}
    for agent in updated.affected_agents:
        make_claim = CLAIMERS.get(agent)
        if make_claim:
            claim = make_claim(updated, llm, tracer)
            checked = validate_claim_demand(claim, updated.scenario)
            existing[agent] = checked.claim
            if checked.requested_people != checked.eligible_people:
                tracer.log(agent, "mediator", "claim_demand_clamped", {
                    "requested_people": checked.requested_people,
                    "eligible_people": checked.eligible_people,
                    "overlaps": list(checked.overlaps),
                })
    updated.claims = list(existing.values())
    updated.plan = mediator_finalize(updated, llm, tracer)
    return updated


def _extend_for_road_changes(updated: NegotiationState, original: NegotiationState) -> None:
    """When road access changes for a zone, ensure every agent that had a
    claim is included in affected_agents so they re-claim."""
    # Detect which zones had road_access changes
    orig_zones = {z.name: z.road_access for z in original.scenario.zones}
    new_zones = {z.name: z.road_access for z in updated.scenario.zones}
    changed_road_zones = {
        name for name in orig_zones
        if orig_zones.get(name) != new_zones.get(name)
    }
    if not changed_road_zones:
        return
    # Every agent with an existing claim that references a changed zone
    # must re-claim
    affected = set(updated.affected_agents)
    for claim in original.claims:
        for zone_name in changed_road_zones:
            if zone_name.lower() in claim.justification.lower():
                affected.add(claim.agent)
    # When roads change, logistics is always affected
    affected.add("logistics")
    updated.affected_agents = sorted(affected)
