"""Negotiation orchestrator – coordinates claims, counters, revisions, and mediator finalization."""

from typing import Any

from agents import logistics as logistics_agent
from agents import mediator as mediator_agent
from agents import medical as medical_agent
from agents import shelter as shelter_agent
from lib.schemas import (
    Claim,
    Counter,
    Estimate,
    MediatorAction,
    NegotiationState,
    Plan,
    Scenario,
)
from lib.scoring import over_subscribed
from lib.tracing import Tracer


def compute_claim_diff(prev: Claim, revised: Claim) -> tuple[dict[str, Any], str, bool]:
    """Calculate resource, urgency, and time window diffs between previous and revised claim."""
    res_diff: dict[str, dict[str, int]] = {}
    all_res = set(prev.resources.keys()) | set(revised.resources.keys())
    for r in all_res:
        b = prev.resources.get(r, 0)
        a = revised.resources.get(r, 0)
        if b != a:
            res_diff[r] = {"before": b, "after": a}

    urg_diff: dict[str, int] = {}
    if prev.urgency != revised.urgency:
        urg_diff = {"before": prev.urgency, "after": revised.urgency}

    tw_diff: dict[str, float] = {}
    if prev.time_window_hrs != revised.time_window_hrs:
        tw_diff = {"before": prev.time_window_hrs, "after": revised.time_window_hrs}

    changed = bool(res_diff or urg_diff or tw_diff)
    defended = not changed

    summary_parts = []
    if res_diff:
        for r, vals in res_diff.items():
            summary_parts.append(f"{r}: {vals['before']} -> {vals['after']}")
    if urg_diff:
        summary_parts.append(f"urgency: {urg_diff['before']} -> {urg_diff['after']}")
    if tw_diff:
        summary_parts.append(f"time_window_hrs: {tw_diff['before']} -> {tw_diff['after']}")

    if defended:
        summary = f"Claim defended unchanged: {revised.justification}"
    else:
        summary = f"Claim revised: {', '.join(summary_parts)}"

    diff_dict = {
        "resources": res_diff,
        "urgency": urg_diff,
        "time_window_hrs": tw_diff,
    }
    return diff_dict, summary, defended


def gather_initial_claims(state: NegotiationState, llm: Any, tracer: Tracer) -> list[Claim]:
    """Gather initial baseline claims from Medical, Shelter, and Logistics agents."""
    return [
        medical_agent.make_claim(state, llm, tracer),
        shelter_agent.make_claim(state, llm, tracer),
        logistics_agent.make_claim(state, llm, tracer),
    ]


def dispatch_claim(
    agent_name: str,
    state: NegotiationState,
    llm: Any,
    tracer: Tracer,
    counter: Counter | None = None,
) -> Claim:
    """Invoke the appropriate agent to make or revise a claim."""
    if agent_name == "medical":
        return medical_agent.make_claim(state, llm, tracer, counter=counter)
    elif agent_name == "shelter":
        return shelter_agent.make_claim(state, llm, tracer, counter=counter)
    elif agent_name == "logistics":
        return logistics_agent.make_claim(state, llm, tracer, counter=counter)
    raise ValueError(f"Unknown agent: {agent_name}")


def dispatch_estimate(
    agent_name: str,
    state: NegotiationState,
    question: str,
    llm: Any,
    tracer: Tracer,
) -> Estimate:
    """Invoke the appropriate agent to provide an estimate."""
    if agent_name == "medical":
        return medical_agent.estimate(state, question, llm, tracer)
    elif agent_name == "shelter":
        return shelter_agent.estimate(state, question, llm, tracer)
    elif agent_name == "logistics":
        return logistics_agent.estimate(state, question, llm, tracer)
    raise ValueError(f"Unknown agent: {agent_name}")


def run_negotiation(
    scenario_or_state: Scenario | NegotiationState,
    llm: Any,
    tracer: Tracer,
    max_rounds: int = 3,
) -> NegotiationState:
    """Run multi-agent negotiation loop until consensus or round cap."""
    if isinstance(scenario_or_state, Scenario):
        state = NegotiationState(scenario=scenario_or_state, round=0)
    else:
        state = scenario_or_state

    # 1. Collect initial claims if not present
    if not state.claims:
        state.claims = gather_initial_claims(state, llm, tracer)
        state.round = 1

    last_claims_hash: Any = None

    # 2. Main negotiation loop
    while state.plan is None and state.round <= max_rounds:
        # Check if claims stopped changing
        current_claims_hash = tuple(
            (c.agent, tuple(sorted(c.resources.items())), c.urgency, c.time_window_hrs)
            for c in state.claims
        )
        if last_claims_hash == current_claims_hash and state.counters:
            # Claims stopped changing; finalize immediately
            state.plan = mediator_agent.finalize(state, llm, tracer)
            break
        last_claims_hash = current_claims_hash

        # Safety net: round cap forces finalize
        if state.round >= max_rounds:
            state.plan = mediator_agent.finalize(state, llm, tracer)
            tracer.log("mediator", "system", "status", {"message": "Round cap reached."})
            break

        # Check oversubscription and decide next action via Mediator
        action = mediator_agent.decide_next(state, llm, tracer)

        if action == MediatorAction.finalize:
            state.plan = mediator_agent.finalize(state, llm, tracer)
            break

        elif action == MediatorAction.challenge_agent:
            if not state.counters:
                target = mediator_agent._pick_agent_to_challenge(state, oversub)
                counter = Counter(
                    to_agent=target,
                    question=f"Resources {oversub} are oversubscribed. Can you revise your claim?",
                )
                state.counters.append(counter)
                tracer.log("mediator", target, "counter", counter.model_dump())

            counter = state.counters[-1]
            target = counter.to_agent
            prev_claim = next((c for c in state.claims if c.agent == target), None)

            # Re-claim with Counter
            revised_claim = dispatch_claim(target, state, llm, tracer, counter=counter)

            # Trace revised claim with change summary
            if prev_claim:
                diff_dict, summary, defended = compute_claim_diff(prev_claim, revised_claim)
                tracer.log(
                    target,
                    "mediator",
                    "claim_revision",
                    {
                        "agent": target,
                        "previous_claim": prev_claim.model_dump(),
                        "revised_claim": revised_claim.model_dump(),
                        "changes": diff_dict,
                        "summary": summary,
                        "defended": defended,
                        "defense_reason": revised_claim.justification if defended else "",
                    },
                )
                state.claims = [revised_claim if c.agent == target else c for c in state.claims]
            else:
                state.claims.append(revised_claim)

            state.round += 1

        elif action in (MediatorAction.request_fallback, MediatorAction.ask_peer_estimate):
            if not state.counters:
                target = "shelter" if action == MediatorAction.request_fallback else "logistics"
                counter = Counter(to_agent=target, question="What is your fallback estimate?")
                state.counters.append(counter)
                tracer.log("mediator", target, "counter", counter.model_dump())

            counter = state.counters[-1]
            target = counter.to_agent
            est = dispatch_estimate(target, state, counter.question, llm, tracer)
            state.estimates.append(est)
            # Estimates feed back into state.estimates for subsequent mediator/agent turns

    return state
