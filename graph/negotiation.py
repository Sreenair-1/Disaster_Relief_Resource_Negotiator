"""Bounded multi-agent negotiation graph."""

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agents.logistics import estimate as logistics_estimate, make_claim as logistics_claim
from agents.medical import estimate as medical_estimate, make_claim as medical_claim
from agents.mediator import decide_next, finalize as mediator_finalize
from agents.shelter import estimate as shelter_estimate, make_claim as shelter_claim
from lib.feasibility import clamp_claim_resources, demand_totals, has_added_evidence, validate_claim_demand, validate_claim_facts
from lib.scoring import over_subscribed
from lib.schemas import ClaimRevision, Counter, MediatorAction, NegotiationState

CLAIMERS = {"medical": medical_claim, "shelter": shelter_claim, "logistics": logistics_claim}
ESTIMATORS = {"medical": medical_estimate, "shelter": shelter_estimate, "logistics": logistics_estimate}


class _GraphState(TypedDict):
    state: NegotiationState
    llm: Any
    tracer: Any
    action: MediatorAction | None
    next_agent: str | None


def _counter_for(state: NegotiationState, agent: str):
    return next((counter for counter in reversed(state.counters) if counter.to_agent == agent), None)


def _collect_claims(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    state.claims = []
    totals = demand_totals(state.scenario)
    ctx["tracer"].log("feasibility", "mediator", "demand_partition", totals)
    for agent, make_claim in CLAIMERS.items():
        claim = make_claim(state, ctx["llm"], ctx["tracer"])
        original_claim = claim
        claim, corrections = validate_claim_facts(claim, state.scenario)
        if corrections:
            ctx["tracer"].log(agent, "mediator", "claim_corrected", {
                "corrections": corrections,
                "before": original_claim.model_dump(), "after": claim.model_dump(),
            })
        checked = validate_claim_demand(claim, state.scenario)
        bounded, non_inventory, resource_changes = clamp_claim_resources(checked.claim, state.scenario)
        state.claims.append(bounded)
        state.non_inventory_requests.extend({"agent": agent, **item} for item in non_inventory)
        if non_inventory or resource_changes:
            ctx["tracer"].log(agent, "mediator", "claim_clamped", {
                "non_inventory_requests": non_inventory,
                "resource_changes": resource_changes,
                "before": checked.claim.model_dump(), "after": bounded.model_dump(),
            })
        if checked.requested_people != checked.eligible_people:
            ctx["tracer"].log(agent, "mediator", "claim_demand_clamped", {
                "requested_people": checked.requested_people,
                "eligible_people": checked.eligible_people,
                "overlaps": list(checked.overlaps),
            })
    state.round = 1
    return {**ctx, "state": state}


def _mediate(ctx: _GraphState) -> _GraphState:
    state = ctx["state"]
    action = decide_next(state, ctx["llm"], ctx["tracer"])
    uncertain = [claim for claim in state.claims if claim.uncertainty is None or claim.uncertainty > 0]
    if action == MediatorAction.accept_revision and uncertain and state.round < 3:
        action = MediatorAction.ask_peer_estimate
        uncertain_agent = max(uncertain, key=lambda claim: claim.uncertainty or 1).agent
        peer = {"medical": "logistics", "shelter": "medical", "logistics": "shelter"}.get(uncertain_agent, "logistics")
        state.counters.append(Counter(
            to_agent=peer,
            question=f"Please provide an independent estimate relevant to {uncertain_agent}'s uncertain claim.",
        ))
    target = state.counters[-1].to_agent if state.counters else None
    return {**ctx, "action": action, "next_agent": target}


def _claim_change_summary(old_claim, new_claim) -> dict[str, Any]:
    """Compute a change summary between previous and revised claims."""
    changes: dict[str, Any] = {}
    if old_claim.resources != new_claim.resources:
        changes["resources"] = {"before": old_claim.resources, "after": new_claim.resources}
    if old_claim.urgency != new_claim.urgency:
        changes["urgency"] = {"before": old_claim.urgency, "after": new_claim.urgency}
    if old_claim.time_window_hrs != new_claim.time_window_hrs:
        changes["time_window_hrs"] = {"before": old_claim.time_window_hrs, "after": new_claim.time_window_hrs}
    if old_claim.people_affected != new_claim.people_affected:
        changes["people_affected"] = {"before": old_claim.people_affected, "after": new_claim.people_affected}
    if old_claim.uncertainty != new_claim.uncertainty:
        changes["uncertainty"] = {"before": old_claim.uncertainty, "after": new_claim.uncertainty}
    if old_claim.justification != new_claim.justification:
        changes["justification"] = {"before": old_claim.justification, "after": new_claim.justification}
    return changes


def _challenge(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    agent = ctx["next_agent"]
    if agent in CLAIMERS:
        index = next((i for i, claim in enumerate(state.claims) if claim.agent == agent), None)
        old_claim = state.claims[index] if index is not None else None
        old_deficits = over_subscribed(state.claims, state.scenario)
        revision = CLAIMERS[agent](state, ctx["llm"], ctx["tracer"], _counter_for(state, agent))
        original_revision = revision
        revision, corrections = validate_claim_facts(revision, state.scenario)
        if corrections:
            ctx["tracer"].log(agent, "mediator", "claim_corrected", {
                "corrections": corrections,
                "before": original_revision.model_dump(), "after": revision.model_dump(),
            })
        checked = validate_claim_demand(revision, state.scenario)
        revision, non_inventory, resource_changes = clamp_claim_resources(checked.claim, state.scenario)
        state.non_inventory_requests.extend({"agent": agent, **item} for item in non_inventory)
        if non_inventory or resource_changes:
            ctx["tracer"].log(agent, "mediator", "claim_clamped", {
                "non_inventory_requests": non_inventory,
                "resource_changes": resource_changes,
                "before": checked.claim.model_dump(), "after": revision.model_dump(),
            })
        if checked.requested_people != checked.eligible_people:
            ctx["tracer"].log(agent, "mediator", "claim_demand_clamped", {
                "requested_people": checked.requested_people,
                "eligible_people": checked.eligible_people,
                "overlaps": list(checked.overlaps),
            })
        if index is None:
            state.claims.append(revision)
        else:
            state.claims[index] = revision

        # Trace the revised claim with a change summary
        changes = _claim_change_summary(old_claim, revision) if old_claim else {}
        candidate_claims = [revision if claim.agent == agent else claim for claim in state.claims]
        new_deficits = over_subscribed(candidate_claims, state.scenario)
        reduced_oversubscription = any(new_deficits.get(resource, 0) < amount
                                       for resource, amount in old_deficits.items())
        has_evidence = has_added_evidence(old_claim, revision)
        meaningful = reduced_oversubscription or has_evidence
        defended_unchanged = not meaningful
        revision_record = ClaimRevision(
            agent=agent,
            round=state.round + 1,
            previous_claim=old_claim,
            current_claim=revision.model_copy(deep=True),
            changes=changes,
            defended_unchanged=defended_unchanged,
            has_evidence=has_evidence,
            meaningful_revision=meaningful,
        )
        state.claim_revisions.append(revision_record)
        ctx["tracer"].log(agent, "mediator", "claim_revision", {
            "agent": agent,
            "changes": changes,
            "defended_unchanged": defended_unchanged,
            "justification": revision.justification,
            "revised_claim": revision.model_dump(),
            "revision_round": revision_record.round,
            "has_evidence": revision_record.has_evidence,
            "meaningful_revision": revision_record.meaningful_revision,
        })
    state.round += 1
    return {**ctx, "state": state, "action": None}


def _fallback(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    counter = state.counters[-1] if state.counters else None
    target = ctx["next_agent"]
    if target in ESTIMATORS and counter:
        estimate = ESTIMATORS[target](state, counter.question, ctx["llm"], ctx["tracer"])
        state.estimates.append(estimate)
        ctx["tracer"].log("mediator", target, "fallback_estimate", estimate.model_dump())
    state.round += 1
    return {**ctx, "state": state, "action": None}


def _finish(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    state.plan = mediator_finalize(state, ctx["llm"], ctx["tracer"])
    return {**ctx, "state": state}


def _route(ctx: _GraphState) -> str:
    if ctx["state"].round >= 6 or ctx["action"] == MediatorAction.finalize:
        return "finalize"
    if ctx["action"] == MediatorAction.challenge_agent:
        return "challenge"
    if ctx["action"] in (MediatorAction.request_fallback, MediatorAction.ask_peer_estimate):
        return "fallback"
    return "finalize"


def _build_graph():
    graph = StateGraph(_GraphState)
    graph.add_node("collect_claims", _collect_claims)
    graph.add_node("mediator_decide", _mediate)
    graph.add_node("challenge", _challenge)
    graph.add_node("fallback", _fallback)
    graph.add_node("finalize", _finish)
    graph.add_edge(START, "collect_claims")
    graph.add_edge("collect_claims", "mediator_decide")
    graph.add_conditional_edges("mediator_decide", _route, {
        "challenge": "challenge", "fallback": "fallback", "finalize": "finalize",
    })
    graph.add_edge("challenge", "mediator_decide")
    graph.add_edge("fallback", "mediator_decide")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_negotiation(scenario, llm, tracer) -> NegotiationState:
    """Run claims, bounded mediator decisions, and final allocation."""
    return _build_graph().invoke({
        "state": NegotiationState(scenario=scenario), "llm": llm, "tracer": tracer,
        "action": None, "next_agent": None,
    })["state"]
