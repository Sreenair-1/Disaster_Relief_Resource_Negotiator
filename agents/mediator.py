"""LLM-guided negotiation decisions and inventory-safe plan finalization."""

from pathlib import Path
import json
from math import ceil, floor
import re
from pydantic import BaseModel, Field

from lib.schemas import Counter, MediatorAction, NegotiationState, Plan, UnresolvedShortfall
from lib.feasibility import (demand_by_zone, ground_route_feasible, unit_cap,
                             boats_needed)
from lib.feasibility import demand_totals
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


def _agents_revised_since_last_counter(state: NegotiationState) -> set[str]:
    """Return agents whose latest post-counter claim changed materially."""
    revised: set[str] = set()
    for agent in {counter.to_agent for counter in state.counters}:
        response = _latest_counter_response(state, agent)
        if response and response.meaningful_revision:
            revised.add(agent)
    return revised


def _agents_defended_unchanged_since_last_counter(state: NegotiationState) -> set[str]:
    """Return unchanged claims defended with non-empty evidence after a counter."""
    defended: set[str] = set()
    for agent in {counter.to_agent for counter in state.counters}:
        response = _latest_counter_response(state, agent)
        if response and response.defended_unchanged and response.has_evidence:
            defended.add(agent)
    return defended


def _latest_counter_response(state: NegotiationState, agent: str):
    counter = next((item for item in reversed(state.counters) if item.to_agent == agent), None)
    if counter is None:
        return None
    return next((revision for revision in reversed(state.claim_revisions)
                 if revision.agent == agent and (
                     revision.counter_id == counter.counter_id if counter.counter_id
                     else revision.round > counter.issued_round
                 )), None)


def _unresolved_conflict_agents(state, deficits, revised, defended):
    involved = {
        claim.agent
        for claim in state.claims
        if any(claim.resources.get(resource, 0) > 0 for resource in deficits)
    }
    return sorted(involved - revised - defended)


def decide_next(state: NegotiationState, llm, tracer: Tracer) -> MediatorAction:
    """Choose an action from evidence; round counts complete response cycles."""
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

    # Only recorded post-counter claim revisions count as responses.
    countered_agents = {c.to_agent for c in state.counters}
    revised_agents = _agents_revised_since_last_counter(state)
    defended_agents = _agents_defended_unchanged_since_last_counter(state)
    unresolved_agents = _unresolved_conflict_agents(
        state, deficits, revised_agents, defended_agents
    )

    context = {
        "round": state.round,
        "round_cap": 3,
        "claims": [claim.model_dump() for claim in state.claims],
        "scores": scores,
        "over_subscription": deficits,
        "missing_agents": missing,
        "uncertain_claims": uncertain,
        "prior_counters": [counter.model_dump() for counter in state.counters],
        "estimates": [est.model_dump() for est in state.estimates],
        "events": state.events,
        "agents_revised_since_counter": sorted(revised_agents),
        "agents_defended_unchanged_since_counter": sorted(defended_agents),
        "agents_not_yet_responded": unresolved_agents,
    }

    llm_reasoning = True
    try:
        decision = llm.complete_json(
            _system_prompt(),
            "Choose the next action based on this negotiation state. Return JSON with "
            "action and, for challenge_agent, request_fallback, or ask_peer_estimate, "
            "to_agent and question. "
            "Include concise reasoning.\n" + json.dumps(context),
            _Decision,
        )
    except Exception:
        llm_reasoning = False
        decision = _Decision(action=MediatorAction.finalize if state.round >= 3
                             else MediatorAction.challenge_agent)

    if state.round >= 3 or (not deficits and decision.action not in {
        MediatorAction.request_fallback, MediatorAction.ask_peer_estimate,
    }):
        decision.action = MediatorAction.finalize

    # Every oversubscribed claimant must revise or defend with evidence before
    # an early final decision can be accepted.
    forced_conflict_challenge = bool(
        deficits and unresolved_agents and state.round < 3
        and decision.action in {MediatorAction.finalize, MediatorAction.accept_revision}
    )
    if forced_conflict_challenge:
        target = unresolved_agents[0]
        decision = _Decision(
            action=MediatorAction.challenge_agent,
            to_agent=target,
            question=_counter_question(target, MediatorAction.challenge_agent, deficits),
        )
    if state.round_challenged_agents and unresolved_agents and state.round < 3:
        target = unresolved_agents[0]
        decision = _Decision(
            action=MediatorAction.challenge_agent,
            to_agent=target,
            question=_counter_question(target, MediatorAction.challenge_agent, deficits),
            reasoning="Completing the current challenge cycle for every unresolved claimant.",
        )
    if not decision.reasoning.strip():
        decision.reasoning = (
            "Finalized because validated demand fits inventory."
            if not deficits else
            "Safety fallback selected the required conflict response."
            if unresolved_agents and state.round < 3 else
            "Safety cap requires a constrained final plan."
        )
        llm_reasoning = False

    counter = None
    if decision.action in {
        MediatorAction.challenge_agent,
        MediatorAction.request_fallback,
        MediatorAction.ask_peer_estimate,
    }:
        agent = decision.to_agent or _counter_target(state, deficits, missing, uncertain)
        question = decision.question or _counter_question(agent, decision.action, deficits)
        snapshot = next((claim.model_copy(deep=True) for claim in state.claims
                         if claim.agent == agent), None)
        if decision.action == MediatorAction.challenge_agent:
            if not state.round_challenged_agents:
                state.round_challenged_agents = list(unresolved_agents or [agent])
            if agent not in state.round_challenged_agents:
                state.round_challenged_agents.append(agent)
        counter = Counter(
            to_agent=agent,
            question=question,
            issued_round=state.round,
            claim_snapshot=snapshot,
            counter_id=len(state.counters) + 1,
        )
        state.counters.append(counter)

    tracer.log("mediator", "agents", "decision", {
        "action": decision.action.value,
        "reasoning": decision.reasoning,
        "reasoning_source": "llm" if llm_reasoning else "fallback",
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


def _resource_awards(claims, resource, inventory, scores, order, scenario):
    """Allocate by score tiers, proportionally splitting only a tied tier."""
    requests = {claim.agent: min(
        max(0, claim.resources.get(resource, 0)), unit_cap(claim, scenario, resource)
    ) for claim in claims}
    awards = {agent: 0 for agent in requests}
    budget = max(0, inventory)
    ordered = [claim for claim in order if requests[claim.agent] > 0]
    tiers: list[list] = []
    for claim in ordered:
        if not tiers or scores[tiers[-1][0].agent] != scores[claim.agent]:
            tiers.append([claim])
        else:
            tiers[-1].append(claim)
    for tier in tiers:
        need = sum(requests[c.agent] for c in tier)
        if need <= budget:
            for claim in tier:
                awards[claim.agent] = requests[claim.agent]
            budget -= need
            continue
        if budget <= 0:
            break
        # Equal score tier: divide proportionally to capped useful requests.
        total_weight = sum(requests[c.agent] for c in tier)
        quotas = {c.agent: budget * requests[c.agent] / total_weight for c in tier}
        base = {agent: floor(value) for agent, value in quotas.items()}
        for agent, value in base.items():
            awards[agent] = value
        left = budget - sum(base.values())
        by_tie_break = sorted(tier, key=lambda c: _claim_order(c, scenario))
        remainder_order = sorted(by_tie_break, key=lambda c: -(quotas[c.agent] - base[c.agent]))
        for claim in remainder_order[:left]:
            awards[claim.agent] += 1
        break
    return awards


def _claim_tier(agent: str) -> int:
    return {"medical": 1, "shelter": 2, "logistics": 3}.get(agent, 3)


def _zone_people(agent: str, zone, scenario) -> int:
    part = demand_by_zone(zone)
    field = {"medical": "critical_patients", "shelter": "stranded_non_critical",
             "logistics": "supply_dependent"}.get(agent)
    return getattr(part, field) if field else part.total_union


def _zone_unit_caps(claim, resource, scenario):
    caps = {}
    for zone in scenario.zones:
        people = _zone_people(claim.agent, zone, scenario)
        if people <= 0:
            continue
        if resource == "boats":
            need = boats_needed(zone, people, claim.time_window_hrs, scenario.operations)
            if need is not None:
                caps[zone.name] = need
        elif resource == "trucks":
            capacity = scenario.operations.truck_capacity_persons
            if capacity and ground_route_feasible(zone, scenario):
                caps[zone.name] = ceil(people / capacity)
        elif resource == "shelter_beds":
            caps[zone.name] = people
        else:
            caps[zone.name] = max(1, people)
    return caps


def _proportional_tier_awards(claims, resource, budget, scores, caps, scenario):
    requests = {c.agent: min(max(0, c.resources.get(resource, 0)), caps.get(c.agent, 0))
                for c in claims}
    awards = {agent: 0 for agent in requests}
    remaining = min(max(0, budget), sum(requests.values()))
    active = [c for c in claims if requests[c.agent] > 0]
    while remaining and active:
        weights = {c.agent: max(scores[c.agent], 0.0) for c in active}
        if sum(weights.values()) == 0:
            weights = {c.agent: 1.0 for c in active}
        weight_total = sum(weights.values())
        quotas = {c.agent: remaining * weights[c.agent] / weight_total for c in active}
        capped = [c for c in active if quotas[c.agent] >= requests[c.agent] - awards[c.agent]]
        if capped:
            for c in capped:
                take = requests[c.agent] - awards[c.agent]
                awards[c.agent] += take
                remaining -= take
                active.remove(c)
            continue
        floors = {c.agent: floor(quotas[c.agent]) for c in active}
        for agent, amount in floors.items():
            awards[agent] += amount
        remainder = remaining - sum(floors.values())
        tie_order = sorted(active, key=lambda c: _claim_order(c, scenario))
        tie_order.sort(key=lambda c: -(quotas[c.agent] - floors[c.agent]))
        for c in tie_order[:remainder]:
            awards[c.agent] += 1
        remaining = 0
    return awards


def _allocation_by_zone(claims, scenario, scores):
    by_zone = {zone.name: {} for zone in scenario.zones}
    by_agent_zone = {claim.agent: {zone.name: {} for zone in scenario.zones} for claim in claims}
    totals = {claim.agent: {} for claim in claims}
    remaining_stock = dict(scenario.resources)
    all_resources = sorted({r for claim in claims for r in claim.resources
                            if r in scenario.resources})
    for resource in all_resources:
        for tier in (1, 2, 3):
            tier_claims = [c for c in claims if _claim_tier(c.agent) == tier]
            zone_caps = {c.agent: _zone_unit_caps(c, resource, scenario) for c in tier_claims}
            total_caps = {agent: sum(values.values()) for agent, values in zone_caps.items()}
            awards = _proportional_tier_awards(
                tier_claims, resource, remaining_stock.get(resource, 0), scores,
                total_caps, scenario,
            )
            for claim in tier_claims:
                award = awards.get(claim.agent, 0)
                if not award:
                    continue
                available_zones = sorted(
                    zone_caps[claim.agent].items(),
                    key=lambda item: (-_zone_people(claim.agent,
                        next(z for z in scenario.zones if z.name == item[0]), scenario), item[0]),
                )
                for name, cap in available_zones:
                    placed = min(award, cap)
                    if placed:
                        by_zone[name][resource] = by_zone[name].get(resource, 0) + placed
                        by_agent_zone[claim.agent][name][resource] = (
                            by_agent_zone[claim.agent][name].get(resource, 0) + placed
                        )
                        totals[claim.agent][resource] = totals[claim.agent].get(resource, 0) + placed
                        award -= placed
                remaining_stock[resource] -= awards.get(claim.agent, 0)
    unassigned = {r: amount for r, amount in remaining_stock.items() if amount > 0}
    return totals, by_zone, unassigned, by_agent_zone


def _coverage_by_zone(allocation_by_zone, by_agent_zone, claims, scenario):
    coverage = {}
    for zone in scenario.zones:
        demand = demand_by_zone(zone)
        tiers = {
            "critical_patients": (demand.critical_patients, "medical"),
            "stranded": (demand.stranded_non_critical, "shelter"),
            "supply_dependent": (demand.supply_dependent, "logistics"),
        }
        zone_result = {}
        for tier, (people, agent) in tiers.items():
            claim = next((c for c in claims if c.agent == agent), None)
            if not people or claim is None:
                zone_result[tier] = {"demand": people, "people_served": 0, "coverage_percent": 100.0 if not people else 0.0}
                continue
            served = 0
            allocation = by_agent_zone.get(agent, {}).get(zone.name, {})
            trips = floor(claim.time_window_hrs / scenario.operations.trip_hours_by_zone.get(zone.name, 0)) if scenario.operations.trip_hours_by_zone.get(zone.name, 0) else 0
            if scenario.operations.boat_capacity_persons:
                served += allocation.get("boats", 0) * scenario.operations.boat_capacity_persons * trips
            if scenario.operations.truck_capacity_persons and ground_route_feasible(zone, scenario):
                served += allocation.get("trucks", 0) * scenario.operations.truck_capacity_persons * trips
            served = min(people, served)
            zone_result[tier] = {
                "demand": people, "people_served": served,
                "coverage_percent": round(100 * served / people, 1),
            }
        coverage[zone.name] = zone_result
    return coverage


def _missing_justifications(reasoning, agents):
    boilerplate = "allocated according to deterministic priority and available inventory."
    return [
        agent for agent in agents
        if not reasoning.justifications.get(agent, "").strip()
        or boilerplate.rstrip(".") in reasoning.justifications[agent].strip().casefold()
    ]


def _revision_since_latest_counter(state, agent):
    counter = next((item for item in reversed(state.counters) if item.to_agent == agent), None)
    if counter is None:
        return None
    return next((revision for revision in reversed(state.claim_revisions)
                 if revision.agent == agent and revision.round > counter.issued_round), None)


def _disputes_for_allocation(state, claims, allocation):
    """Produce one accurate dispute entry per revised or capped claim."""
    entries: list[str] = []
    for claim in claims:
        revision = _revision_since_latest_counter(state, claim.agent)
        details: list[str] = []
        if revision and not revision.defended_unchanged and revision.previous_claim:
            before = revision.previous_claim.resources
            after = revision.current_claim.resources
            for resource in sorted(set(before) | set(after)):
                old, new = before.get(resource, 0), after.get(resource, 0)
                if old != new:
                    details.append(f"revised {resource} request from {old} to {new}")

        caps: list[str] = []
        for resource, requested in sorted(claim.resources.items()):
            if resource not in state.scenario.resources:
                continue
            awarded = allocation.get(claim.agent, {}).get(resource, 0)
            if awarded < requested:
                caps.append(
                    f"awarded {awarded} of {requested} requested for {resource} "
                    "(capped by Mediator from stock)"
                )
        if details or caps:
            entries.append(f"{claim.agent}: " + "; ".join([*details, *caps]) + ".")
    return entries


def _unresolved_shortfalls(state, allocation):
    shortfalls: list[UnresolvedShortfall] = []
    for resource, excess in over_subscribed(state.claims, state.scenario).items():
        if excess <= 0:
            continue
        agents = [
            claim.agent for claim in state.claims
            if claim.resources.get(resource, 0) > allocation.get(claim.agent, {}).get(resource, 0)
        ]
        shortfalls.append(UnresolvedShortfall(
            resource=resource,
            unmet_amount=sum(
                max(0, claim.resources.get(resource, 0) - allocation.get(claim.agent, {}).get(resource, 0))
                for claim in state.claims
            ),
            agents=agents,
        ))
    return shortfalls


def _resource_text(resources):
    if not resources:
        return "none"
    return ", ".join(f"{name}={amount}" for name, amount in sorted(resources.items()))


def _plan_reasoning_context(state, claims, allocation, scores, life_safety_gap):
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
        "priority_tiers": {claim.agent: _claim_tier(claim.agent) for claim in claims},
        "life_safety_gap": life_safety_gap,
        "requested": requested,
        "awarded": awarded,
        "shortfalls": shortfalls,
        "competing_claims": competitors,
        "unmet_resource_amounts": over_subscribed(state.claims, state.scenario),
        "rounds_used": state.round,
    }


def _deterministic_justification(claim, allocation, scores, rank, competitors,
                                 life_safety_gap=False):
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
    tier = _claim_tier(claim.agent)
    tier_note = f"Priority tier {tier}. " if tier > 1 else "Priority tier 1. "
    gap_note = "life_safety_gap: critical patient coverage remains incomplete. " if life_safety_gap else ""
    return (
        f"Score {scores[claim.agent]:.3f}; rank {rank}. "
        f"{tier_note}{gap_note}"
        f"Requested {_resource_text(requested)}; awarded {_resource_text(awarded)}; "
        f"shortfall {_resource_text(shortfalls)}. Trade-off: this award balances "
        f"{claim.agent}'s need against competing claims from {competitor_text}."
    )


def _justification_contradictions(reasoning, claims, allocation, scores,
                                  life_safety_gap=False):
    ranks = {claim.agent: index for index, claim in enumerate(claims, start=1)}
    problems = []
    for claim in claims:
        note = reasoning.justifications.get(claim.agent, "").strip()
        if not note:
            problems.append(f"{claim.agent}: missing justification")
            continue
        expected_score = f"{scores[claim.agent]:.3f}"
        if expected_score not in note:
            problems.append(f"{claim.agent}: score citation does not match {expected_score}")
        if not re.search(rf"\brank\s*{ranks[claim.agent]}\b", note, re.I):
            problems.append(f"{claim.agent}: rank citation does not match {ranks[claim.agent]}")
        for resource, requested in claim.resources.items():
            award = allocation.get(claim.agent, {}).get(resource, 0)
            if not re.search(rf"Requested\s+{re.escape(resource)}\s*=\s*{requested}\b", note, re.I):
                problems.append(f"{claim.agent}: requested {resource} citation mismatch")
            if not re.search(rf"awarded\s+{re.escape(resource)}\s*=\s*{award}\b", note, re.I):
                problems.append(f"{claim.agent}: awarded {resource} citation mismatch")
        if ranks[claim.agent] > 1 and re.search(r"highest\s+priority|top\s+priority", note, re.I):
            problems.append(f"{claim.agent}: lower-ranked claim described as highest priority")
        if _claim_tier(claim.agent) > 1 and not re.search(
            rf"priority\s+tier\s*{_claim_tier(claim.agent)}|higher[- ]priority tier|lower tier", note, re.I
        ):
            problems.append(f"{claim.agent}: missing its lower life-safety tier")
        if life_safety_gap and not re.search(r"life_safety_gap|critical patient coverage", note, re.I):
            problems.append(f"{claim.agent}: omitted the critical-patient coverage gap")
    return problems


def finalize(state: NegotiationState, llm, tracer: Tracer) -> Plan:
    """Allocate by shared claim scores within inventory, then explain."""
    allocation: dict[str, dict[str, int]] = {}
    claims = sorted(state.claims, key=lambda claim: (
        _claim_tier(claim.agent), *_claim_order(claim, state.scenario)
    ))
    scores = {claim.agent: score_claim(claim, state.scenario) for claim in claims}
    allocation, allocation_by_zone, unassigned_resources, by_agent_zone = _allocation_by_zone(
        claims, state.scenario, scores
    )
    coverage = _coverage_by_zone(allocation_by_zone, by_agent_zone, claims, state.scenario)
    total_critical = sum(demand_by_zone(zone).critical_patients for zone in state.scenario.zones)
    served_critical = sum(value["critical_patients"]["people_served"]
                          for value in coverage.values())
    life_safety_gap = served_critical < total_critical

    context = _plan_reasoning_context(state, claims, allocation, scores, life_safety_gap)
    prompt = _system_prompt()
    user = (
        "Explain this fixed allocation. Return one non-empty justification for every "
        "agent in allocation. Each must cite its numeric score, rank, priority tier, requested and "
        "awarded amounts, shortfall, and trade-off against competing claims. Explicitly state "
        "the life_safety_gap and critical-patient coverage issue when true. "
        "Record resolved conflicts and unmet requests. Do not change the allocation.\n"
        + json.dumps(context)
    )
    reasoning_source = "llm"
    try:
        reasoning = llm.complete_json(prompt, user, _PlanReasoning)
    except Exception:
        reasoning = _PlanReasoning()
        reasoning_source = "fallback"
    missing = _missing_justifications(reasoning, allocation)
    contradictions = _justification_contradictions(
        reasoning, claims, allocation, scores, life_safety_gap
    )
    if contradictions or _missing_justifications(reasoning, allocation):
        reasoning_source = "fallback"
    if missing or contradictions:
        retry_user = (
            "Retry the allocation explanation. Missing/empty entries: "
            f"{missing}. Data contradictions to correct: {contradictions}. "
            "Return a justification for every agent, including "
            "numeric score, rank, requested and awarded amounts, shortfall, and the "
            "trade-off against competing claims. Do not change the allocation.\n"
            + json.dumps(context)
        )
        try:
            reasoning = llm.complete_json(prompt, retry_user, _PlanReasoning)
            reasoning_source = "llm"
        except Exception:
            # The deterministic report remains usable if the repair call fails.
            reasoning_source = "fallback"

    ranks = {claim.agent: index for index, claim in enumerate(claims, start=1)}
    competitors = {
        claim.agent: [
            (other.agent, scores[other.agent])
            for other in claims if other.agent != claim.agent
        ]
        for claim in claims
    }
    contradictions = _justification_contradictions(
        reasoning, claims, allocation, scores, life_safety_gap
    )
    justifications = {}
    for claim in claims:
        note = reasoning.justifications.get(claim.agent, "").strip()
        if "allocated according to deterministic priority and available inventory" in note.casefold():
            note = ""
        detail = _deterministic_justification(
            claim, allocation, scores, ranks[claim.agent], competitors[claim.agent],
            life_safety_gap,
        )
        valid_note = note if not contradictions and claim.agent not in _missing_justifications(reasoning, allocation) else ""
        justifications[claim.agent] = f"{valid_note} {detail}".strip()

    deficits = over_subscribed(state.claims, state.scenario)
    unresolved = _unresolved_shortfalls(state, allocation) if deficits else []
    prior_claims = {claim.agent: claim for claim in state.claims}
    for revision in reversed(state.claim_revisions):
        if revision.previous_claim is not None:
            prior_claims[revision.agent] = revision.previous_claim
    prior_deficits = over_subscribed(list(prior_claims.values()), state.scenario)
    conflicts = [
        f"{resource} oversubscription was resolved after claim evidence changed."
        for resource in sorted(prior_deficits)
        if resource not in deficits
    ]
    status = "constrained" if deficits or life_safety_gap else "resolved"

    disputes = _disputes_for_allocation(state, claims, allocation)

    plan = Plan(
        allocation=allocation,
        justifications=justifications,
        conflicts_resolved=conflicts,
        replans=state.replans,
        rounds_used=state.round,
        disputes_log=disputes,
        status=status,
        unresolved_shortfalls=unresolved,
        risks=[f"{claim.agent}: medical receiving support is unverified or insufficient"
               for claim in state.claims if claim.unverified_support],
        allocation_by_zone=allocation_by_zone,
        coverage=coverage,
        life_safety_gap=life_safety_gap,
        unassigned_resources=unassigned_resources,
        feasibility_report={
            "checks_passed": ["demand partitions are disjoint", "allocation capped by inventory and useful unit cap"],
            "checks_failed": ["oversubscribed demand remains"] if deficits else [],
            "unresolved_shortfalls": [item.model_dump() for item in unresolved],
            "unverified_assumptions": sorted({
                label for label, unknown in (
                    ("boat capacity", state.scenario.operations.boat_capacity_persons is None),
                    ("route trip times", not state.scenario.operations.trip_hours_by_zone),
                    ("hospital receiving capacity", any(h.icu_beds_free is None for h in state.scenario.operations.hospitals)),
                ) if unknown
            }),
            "demand_totals": demand_totals(state.scenario),
        },
        non_inventory_requests=[
            *state.non_inventory_requests,
            *({"agent": claim.agent, "resource": resource, "requested": amount}
              for claim in state.claims for resource, amount in claim.resources.items()
              if resource not in state.scenario.resources),
        ],
        reasoning_source=reasoning_source,
    )
    tracer.log("mediator", "system", "plan", plan.model_dump())
    return plan
