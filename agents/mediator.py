"""LLM-guided negotiation decisions and inventory-safe plan finalization."""

from pathlib import Path
import json
from pydantic import BaseModel, Field

from lib.schemas import Counter, MediatorAction, NegotiationState, Plan
from lib.scoring import over_subscribed, score_claim
from lib.tracing import Tracer

PROMPT_PATH = Path(__file__).parent / "prompts" / "mediator.md"


class _Decision(BaseModel):
    action: MediatorAction
    to_agent: str | None = None
    question: str | None = None
    reasoning: str = ""


class _PlanReasoning(BaseModel):
    justifications: dict[str, str] = Field(default_factory=dict)
    conflicts_resolved: list[str] = Field(default_factory=list)
    disputes_log: list[str] = Field(default_factory=list)


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def decide_next(state: NegotiationState, llm, tracer: Tracer) -> MediatorAction:
    """Choose a negotiation action from current evidence; finalize by round 3."""
    deficits = over_subscribed(state.claims, state.scenario)
    scores = {
        claim.agent: round(score_claim(claim, state.scenario), 3)
        for claim in state.claims
    }
    uncertain = [
        {"agent": claim.agent, "uncertainty": claim.uncertainty}
        for claim in state.claims
        if claim.uncertainty is None or claim.uncertainty > 0
    ]
    missing = [agent for agent in ("medical", "shelter", "logistics")
               if agent not in {claim.agent for claim in state.claims}]
    context = {
        "round": state.round,
        "round_cap": 3,
        "claims": [claim.model_dump() for claim in state.claims],
        "scores": scores,
        "over_subscription": deficits,
        "missing_agents": missing,
        "uncertain_claims": uncertain,
        "prior_counters": [counter.model_dump() for counter in state.counters],
        "events": state.events,
    }

    if state.round >= 3:
        decision = _Decision(action=MediatorAction.finalize, reasoning="Round cap reached.")
    else:
        decision = llm.complete_json(
            _system_prompt(),
            "Choose the next action based on this negotiation state. Return JSON with "
            "action and, for challenge_agent or request_fallback, to_agent and question. "
            "Include concise reasoning.\n" + json.dumps(context),
            _Decision,
        )

    counter = None
    if decision.action in {MediatorAction.challenge_agent, MediatorAction.request_fallback}:
        agent = decision.to_agent or _counter_target(state, deficits, missing, uncertain)
        question = decision.question or _counter_question(agent, decision.action, deficits)
        counter = Counter(to_agent=agent, question=question)
        state.counters.append(counter)

    tracer.log("mediator", "agents", "decision", {
        "action": decision.action.value,
        "reasoning": decision.reasoning,
        "counter": counter.model_dump() if counter else None,
        "round": state.round,
        "scores": scores,
        "over_subscription": deficits,
    })
    return decision.action


def _counter_target(state, deficits, missing, uncertain) -> str:
    if deficits:
        resource = max(deficits, key=deficits.get)
        return max(
            (claim for claim in state.claims if claim.resources.get(resource, 0)),
            key=lambda claim: claim.resources[resource],
        ).agent
    if uncertain:
        return max(uncertain, key=lambda item: item["uncertainty"] or 1)["agent"]
    if missing:
        return missing[0]
    return state.claims[0].agent if state.claims else "agents"


def _counter_question(agent, action, deficits) -> str:
    if action == MediatorAction.request_fallback:
        return f"Please propose a fallback that uses fewer resources than your current {agent} claim."
    if deficits:
        resource, excess = next(iter(deficits.items()))
        return f"Please justify your request for {resource}, including the {excess}-unit shortfall, or propose a fallback."
    return "Please justify your requested resources and address any uncertainty, or propose a fallback."


def _priority(claim, scenario) -> float:
    """Score urgency per affected person so urgent specialist claims lead allocation."""
    return score_claim(claim, scenario) / max(claim.people_affected, 1)


def finalize(state: NegotiationState, llm, tracer: Tracer) -> Plan:
    """Allocate by deterministic claim priority within inventory, then explain."""
    remaining = dict(state.scenario.resources)
    allocation: dict[str, dict[str, int]] = {}
    claims = sorted(state.claims, key=lambda claim: (-_priority(claim, state.scenario), claim.agent))
    for claim in claims:
        awarded: dict[str, int] = {}
        for resource, requested in claim.resources.items():
            available = max(remaining.get(resource, 0), 0)
            amount = min(max(requested, 0), available)
            if amount:
                awarded[resource] = amount
                remaining[resource] = available - amount
        allocation[claim.agent] = awarded

    deficits = over_subscribed(state.claims, state.scenario)
    reasoning = llm.complete_json(
        _system_prompt(),
        "Write concise allocation justifications and record resolved conflicts and "
        "unmet requests as disputes. Do not alter this allocation.\n" +
        json.dumps({
            "allocation": allocation,
            "claims": [claim.model_dump() for claim in state.claims],
            "scores": {claim.agent: round(score_claim(claim, state.scenario), 3) for claim in state.claims},
            "unmet_resource_amounts": deficits,
            "rounds_used": state.round,
        }),
        _PlanReasoning,
    )
    justifications = {
        agent: reasoning.justifications.get(agent, "Allocated according to deterministic priority and available inventory.")
        for agent in allocation
    }
    disputes = list(reasoning.disputes_log)
    for claim in state.claims:
        shortfalls = {
            resource: requested - allocation.get(claim.agent, {}).get(resource, 0)
            for resource, requested in claim.resources.items()
            if requested > allocation.get(claim.agent, {}).get(resource, 0)
        }
        if shortfalls:
            disputes.append(f"{claim.agent} request was reduced: {shortfalls}.")
    conflicts = list(reasoning.conflicts_resolved)
    if deficits and not conflicts:
        conflicts.append("Shared resource demand exceeded inventory; awards were capped to available stock.")

    plan = Plan(
        allocation=allocation,
        justifications=justifications,
        conflicts_resolved=conflicts,
        replans=state.replans,
        rounds_used=state.round,
        disputes_log=disputes,
    )
    tracer.log("mediator", "system", "plan", plan.model_dump())
    return plan
