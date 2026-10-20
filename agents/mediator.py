"""LLM-guided negotiation decisions and inventory-safe plan finalization."""

from pathlib import Path
import json
from math import floor
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
            "action and, for challenge_agent, request_fallback, or ask_peer_estimate, "
            "to_agent and question. "
            "Include concise reasoning.\n" + json.dumps(context),
            _Decision,
        )

    counter = None
    if decision.action in {
        MediatorAction.challenge_agent,
        MediatorAction.request_fallback,
        MediatorAction.ask_peer_estimate,
    }:
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
    if action == MediatorAction.ask_peer_estimate:
        return f"Please provide an independent estimate relevant to {agent}'s uncertain claim."
    if action == MediatorAction.request_fallback:
        return f"Please propose a fallback that uses fewer resources than your current {agent} claim."
    if deficits:
        resource, excess = next(iter(deficits.items()))
        return f"Please justify your request for {resource}, including the {excess}-unit shortfall, or propose a fallback."
    return "Please justify your requested resources and address any uncertainty, or propose a fallback."


def _claim_order(claim, scenario):
    """Order equal-score claims by urgency, response window, and agent name."""
    return (
        -score_claim(claim, scenario),
        -claim.urgency,
        claim.time_window_hrs,
        claim.agent,
    )


def _resource_awards(claims, resource, inventory, scores, order):
    """Split an oversubscribed resource by score with capped largest remainders."""
    requests = {claim.agent: max(0, claim.resources.get(resource, 0)) for claim in claims}
    target = min(max(0, inventory), sum(requests.values()))
    if target == sum(requests.values()):
        return requests

    ordered_agents = [claim.agent for claim in order]
    order_index = {agent: index for index, agent in enumerate(ordered_agents)}
    awards = {agent: 0 for agent in requests}
    active_agents = [agent for agent in ordered_agents if requests[agent] > 0]

    # Preserve a one-unit foothold for each claimant when stock can support one.
    # Divide the remaining stock by score; this keeps a small urgent claim viable.
    if target >= len(active_agents):
        for agent in active_agents:
            awards[agent] = 1
        budget = target - len(active_agents)
    else:
        budget = target

    while budget > 0:
        available = [agent for agent in active_agents if awards[agent] < requests[agent]]
        if not available:
            break
        weighted = [agent for agent in available if scores[agent] > 0]
        pool = weighted or available
        weights = {agent: scores[agent] if weighted else 1.0 for agent in pool}
        total_weight = sum(weights.values())
        quotas = {agent: budget * weights[agent] / total_weight for agent in pool}

        capped = [
            agent for agent in pool
            if quotas[agent] >= requests[agent] - awards[agent]
        ]
        if capped:
            for agent in capped:
                amount = requests[agent] - awards[agent]
                awards[agent] += amount
                budget -= amount
                active_agents.remove(agent)
            continue

        base = {agent: floor(quotas[agent]) for agent in pool}
        for agent, amount in base.items():
            awards[agent] += amount
            budget -= amount

        remainders = sorted(
            pool,
            key=lambda agent: (-(quotas[agent] - base[agent]), order_index[agent]),
        )
        for agent in remainders:
            if budget == 0:
                break
            if awards[agent] < requests[agent]:
                awards[agent] += 1
                budget -= 1
        break

    return awards


def _missing_justifications(reasoning, agents):
    boilerplate = "allocated according to deterministic priority and available inventory."
    return [
        agent for agent in agents
        if not reasoning.justifications.get(agent, "").strip()
        or boilerplate.rstrip(".") in reasoning.justifications[agent].strip().casefold()
    ]


def _resource_text(resources):
    if not resources:
        return "none"
    return ", ".join(f"{name}={amount}" for name, amount in sorted(resources.items()))


def _plan_reasoning_context(state, claims, allocation, scores):
    rank = {claim.agent: index for index, claim in enumerate(claims, start=1)}
    requested = {claim.agent: claim.resources for claim in claims}
    awarded = {
        claim.agent: {
            resource: allocation.get(claim.agent, {}).get(resource, 0)
            for resource in claim.resources
        }
        for claim in claims
    }
    shortfalls = {
        claim.agent: {
            resource: max(0, amount - awarded[claim.agent].get(resource, 0))
            for resource, amount in claim.resources.items()
        }
        for claim in claims
    }
    competitors = {
        claim.agent: [
            {"agent": other.agent, "score": round(scores[other.agent], 3)}
            for other in claims if other.agent != claim.agent
        ]
        for claim in claims
    }
    return {
        "allocation": allocation,
        "claims": [claim.model_dump() for claim in state.claims],
        "scores": {agent: round(score, 3) for agent, score in scores.items()},
        "rank": rank,
        "requested": requested,
        "awarded": awarded,
        "shortfalls": shortfalls,
        "competing_claims": competitors,
        "unmet_resource_amounts": over_subscribed(state.claims, state.scenario),
        "rounds_used": state.round,
    }


def _deterministic_justification(claim, allocation, scores, rank, competitors):
    requested = claim.resources
    awarded = {
        resource: allocation.get(claim.agent, {}).get(resource, 0)
        for resource in requested
    }
    shortfalls = {
        resource: max(0, amount - awarded.get(resource, 0))
        for resource, amount in requested.items()
    }
    competitor_text = "; ".join(
        f"{agent} (score {score:.3f})" for agent, score in competitors
    ) or "no other claims"
    return (
        f"Score {scores[claim.agent]:.3f}; rank {rank}. "
        f"Requested {_resource_text(requested)}; awarded {_resource_text(awarded)}; "
        f"shortfall {_resource_text(shortfalls)}. Trade-off: this award balances "
        f"{claim.agent}'s need against competing claims from {competitor_text}."
    )


def finalize(state: NegotiationState, llm, tracer: Tracer) -> Plan:
    """Allocate by shared claim scores within inventory, then explain."""
    allocation: dict[str, dict[str, int]] = {}
    claims = sorted(state.claims, key=lambda claim: _claim_order(claim, state.scenario))
    scores = {claim.agent: score_claim(claim, state.scenario) for claim in claims}
    for claim in claims:
        allocation[claim.agent] = {}

    resources = sorted({resource for claim in claims for resource in claim.resources})
    for resource in resources:
        awards = _resource_awards(
            claims,
            resource,
            state.scenario.resources.get(resource, 0),
            scores,
            claims,
        )
        for agent, amount in awards.items():
            if amount:
                allocation[agent][resource] = amount

    context = _plan_reasoning_context(state, claims, allocation, scores)
    prompt = _system_prompt()
    user = (
        "Explain this fixed allocation. Return one non-empty justification for every "
        "agent in allocation. Each must cite its numeric score, rank, requested and "
        "awarded amounts, shortfall, and the trade-off against competing claims. "
        "Record resolved conflicts and unmet requests. Do not change the allocation.\n"
        + json.dumps(context)
    )
    reasoning = llm.complete_json(prompt, user, _PlanReasoning)
    missing = _missing_justifications(reasoning, allocation)
    if missing:
        retry_user = (
            "Retry the allocation explanation. These entries were missing or empty: "
            f"{missing}. Return a non-empty justification for every agent, including "
            "numeric score, rank, requested and awarded amounts, shortfall, and the "
            "trade-off against competing claims. Do not change the allocation.\n"
            + json.dumps(context)
        )
        reasoning = llm.complete_json(prompt, retry_user, _PlanReasoning)

    ranks = {claim.agent: index for index, claim in enumerate(claims, start=1)}
    competitors = {
        claim.agent: [
            (other.agent, scores[other.agent])
            for other in claims if other.agent != claim.agent
        ]
        for claim in claims
    }
    justifications = {}
    for claim in claims:
        note = reasoning.justifications.get(claim.agent, "").strip()
        if "allocated according to deterministic priority and available inventory" in note.casefold():
            note = ""
        detail = _deterministic_justification(
            claim, allocation, scores, ranks[claim.agent], competitors[claim.agent]
        )
        justifications[claim.agent] = f"{note} {detail}".strip()

    shortfalls_by_agent = {
        claim.agent: {
            resource: requested - allocation.get(claim.agent, {}).get(resource, 0)
            for resource, requested in claim.resources.items()
            if requested > allocation.get(claim.agent, {}).get(resource, 0)
        }
        for claim in state.claims
    }
    reduced_agents = {agent for agent, shortfalls in shortfalls_by_agent.items() if shortfalls}
    disputes = [
        dispute for dispute in reasoning.disputes_log
        if not any(agent.casefold() in dispute.casefold() for agent in reduced_agents)
    ]
    for claim in state.claims:
        shortfalls = shortfalls_by_agent[claim.agent]
        if shortfalls and not any(claim.agent.casefold() in item.casefold() for item in disputes):
            disputes.append(f"{claim.agent} request was reduced: {shortfalls}.")
    conflicts = list(reasoning.conflicts_resolved)
    deficits = over_subscribed(state.claims, state.scenario)
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
