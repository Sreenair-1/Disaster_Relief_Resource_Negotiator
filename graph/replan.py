"""Apply scenario events and renegotiate only affected claims."""

from typing import Any

from agents.logistics import make_claim as logistics_claim
from agents.mediator import finalize as mediator_finalize, _counter_question, _counter_target
from lib.scoring import over_subscribed, score_claim
from agents.medical import make_claim as medical_claim
from agents.scenario import inject_event
from agents.shelter import make_claim as shelter_claim
from lib.feasibility import (clamp_claim_resources, demand_totals, has_added_evidence,
                             validate_claim_demand, validate_claim_facts)
from lib.schemas import Counter, ClaimRevision, MediatorAction, NegotiationState

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

    before_plan = state.plan.model_copy(deep=True) if state.plan else None
    before_claims = {claim.agent: claim for claim in state.claims}

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
            original_claim = claim
            claim, corrections = validate_claim_facts(claim, updated.scenario)
            if corrections:
                tracer.log(agent, "mediator", "claim_corrected", {
                    "corrections": corrections,
                    "before": original_claim.model_dump(), "after": claim.model_dump(),
                })
            checked = validate_claim_demand(claim, updated.scenario)
            bounded, non_inventory, resource_changes = clamp_claim_resources(checked.claim, updated.scenario)
            updated.non_inventory_requests.extend({"agent": agent, **item} for item in non_inventory)
            existing[agent] = bounded
            if non_inventory or resource_changes:
                tracer.log(agent, "mediator", "claim_clamped", {
                    "non_inventory_requests": non_inventory,
                    "resource_changes": resource_changes,
                    "before": checked.claim.model_dump(), "after": bounded.model_dump(),
                })
            if checked.requested_people != checked.eligible_people:
                tracer.log(agent, "mediator", "claim_demand_clamped", {
                    "requested_people": checked.requested_people,
                    "eligible_people": checked.eligible_people,
                    "overlaps": list(checked.overlaps),
                })
    updated.claims = list(existing.values())
    # After an event changes an oversubscribed negotiation, ask one affected
    # claimant to respond to the new conditions before reallocating.
    deficits = over_subscribed(updated.claims, updated.scenario)
    if deficits and updated.round < 6:
        missing, uncertain = [], []
        target = _counter_target(updated, deficits, missing, uncertain)
        question = _counter_question(target, MediatorAction.challenge_agent, deficits)
        counter = Counter(to_agent=target, question=question, issued_round=updated.round,
                          claim_snapshot=existing.get(target).model_copy(deep=True)
                          if existing.get(target) else None)
        updated.counters.append(counter)
        previous = existing.get(target)
        prior_deficits = over_subscribed(updated.claims, updated.scenario)
        revised = CLAIMERS[target](updated, llm, tracer, counter)
        original_revision = revised
        revised, corrections = validate_claim_facts(revised, updated.scenario)
        if corrections:
            tracer.log(target, "mediator", "claim_corrected", {
                "corrections": corrections,
                "before": original_revision.model_dump(), "after": revised.model_dump(),
            })
        checked = validate_claim_demand(revised, updated.scenario)
        revised, non_inventory, resource_changes = clamp_claim_resources(checked.claim, updated.scenario)
        updated.non_inventory_requests.extend({"agent": target, **item} for item in non_inventory)
        existing[target] = revised
        if non_inventory or resource_changes:
            tracer.log(target, "mediator", "claim_clamped", {
                "non_inventory_requests": non_inventory,
                "resource_changes": resource_changes,
                "before": checked.claim.model_dump(), "after": revised.model_dump(),
            })
        updated.claims = list(existing.values())
        changes = {}
        if previous:
            if previous.resources != revised.resources:
                changes["resources"] = {"before": previous.resources, "after": revised.resources}
            if previous.urgency != revised.urgency:
                changes["urgency"] = {"before": previous.urgency, "after": revised.urgency}
            if previous.time_window_hrs != revised.time_window_hrs:
                changes["time_window_hrs"] = {"before": previous.time_window_hrs, "after": revised.time_window_hrs}
        new_deficits = over_subscribed(updated.claims, updated.scenario)
        evidence = has_added_evidence(previous, revised)
        reduced_oversubscription = any(new_deficits.get(key, 0) < amount
                                       for key, amount in prior_deficits.items())
        meaningful = reduced_oversubscription or evidence
        record = ClaimRevision(agent=target, round=updated.round + 1,
                               previous_claim=previous, current_claim=revised,
                               changes=changes, defended_unchanged=not meaningful,
                               has_evidence=evidence, meaningful_revision=meaningful)
        updated.claim_revisions.append(record)
        tracer.log(target, "mediator", "claim_revision", {
            "agent": target, "changes": changes,
            "defended_unchanged": record.defended_unchanged,
            "justification": revised.justification,
            "revised_claim": revised.model_dump(),
            "revision_round": record.round,
            "has_evidence": record.has_evidence,
            "meaningful_revision": record.meaningful_revision,
        })
        updated.round += 1

    updated.plan = mediator_finalize(updated, llm, tracer)
    before_awards = before_plan.allocation if before_plan else {}
    after_awards = updated.plan.allocation
    reassessments = []
    for agent in updated.affected_agents:
        old_claim, new_claim = before_claims.get(agent), existing.get(agent)
        old_score = score_claim(old_claim, state.scenario) if old_claim else 0.0
        new_score = score_claim(new_claim, updated.scenario) if new_claim else 0.0
        old_award = before_awards.get(agent, {})
        new_award = after_awards.get(agent, {})
        if old_award != new_award:
            reason = f"claim score changed from {old_score:.3f} to {new_score:.3f}; allocation was reassessed"
        elif deficits:
            reason = "score still highest; resource cap binding" if new_score == max(
                (score_claim(c, updated.scenario) for c in updated.claims), default=0
            ) else "allocation unchanged because higher-priority demand and resource cap remain binding"
        else:
            reason = "allocation unchanged because available stock still covers the request"
        entry = {
            "agent": agent, "previous_score": round(old_score, 3),
            "new_score": round(new_score, 3),
            "previous_request": old_claim.resources if old_claim else {},
            "new_request": new_claim.resources if new_claim else {},
            "previous_award": old_award, "new_award": new_award,
            "reason": reason,
        }
        reassessments.append(entry)
        tracer.log("mediator", agent, "reassessment", entry)
    updated.plan.reassessments = reassessments
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
