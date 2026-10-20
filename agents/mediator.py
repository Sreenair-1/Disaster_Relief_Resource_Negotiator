from pathlib import Path
from typing import Any

from lib.schemas import Counter, MediatorAction, MediatorDecision, NegotiationState, Plan
from lib.scoring import over_subscribed, score_claim
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "mediator.md"
_SYSTEM_PROMPT: str | None = None


def _system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _pick_agent_to_challenge(state: NegotiationState, oversub: dict[str, int]) -> str:
    """Pick which agent to challenge among those requesting oversubscribed resources."""
    claimants = [
        c for c in state.claims
        if any(c.resources.get(r, 0) > 0 for r in oversub)
    ]
    if not claimants:
        return state.claims[0].agent if state.claims else "shelter"

    # Prefer agent not yet challenged in the most recent counter
    recent_challenged = [cnt.to_agent for cnt in state.counters[-2:]]
    unchallenged = [c for c in claimants if c.agent not in recent_challenged]
    pool = unchallenged if unchallenged else claimants

    # Pick claimant with the lowest priority score
    return min(pool, key=lambda c: score_claim(c, state.scenario)).agent


def _build_user_message(state: NegotiationState) -> str:
    """Compose user context prompt for the Mediator LLM."""
    parts = []
    parts.append(f"Disaster type: {state.scenario.disaster_type}")
    parts.append(f"Available resources: {state.scenario.resources}")
    parts.append(f"Round: {state.round}")

    oversub = over_subscribed(state.claims, state.scenario)
    if oversub:
        parts.append(f"OVERSUBSCRIBED RESOURCES (deficits): {oversub}")
    else:
        parts.append("ALL RESOURCES SUFFICIENT: No oversubscription detected.")

    parts.append("Current claims:")
    for c in state.claims:
        score = score_claim(c, state.scenario)
        parts.append(
            f"  Agent '{c.agent}': resources={c.resources}, people_affected={c.people_affected}, "
            f"urgency={c.urgency}, time_window_hrs={c.time_window_hrs}, score={score:.1f}, "
            f"uncertainty={c.uncertainty}, justification=\"{c.justification}\", fallback=\"{c.fallback_option}\""
        )

    if state.estimates:
        parts.append("Stored estimates and telemetry feedback:")
        for est in state.estimates:
            parts.append(
                f"  From '{est.agent}': question=\"{est.question}\", value={est.value}, "
                f"confidence={est.confidence}, rationale=\"{est.rationale}\""
            )

    if state.counters:
        parts.append("Previous counters:")
        for cnt in state.counters:
            parts.append(f"  To '{cnt.to_agent}': \"{cnt.question}\"")

    parts.append(
        "Decide the next action. If resources are oversubscribed, prefer challenge_agent so the targeted agent "
        "re-claims with the Counter. Use request_fallback or ask_peer_estimate only for uncertain claims or tool failures. "
        "Choose finalize as soon as nothing is oversubscribed or claims stopped changing."
    )
    return "\n".join(parts)


def decide_next(state: NegotiationState, llm: Any, tracer: Tracer) -> MediatorAction:
    oversub = over_subscribed(state.claims, state.scenario)

    # Hard cap at 3 rounds as safety net only
    if state.round >= 3:
        tracer.log("mediator", "agents", "decision", {"action": MediatorAction.finalize.value, "reason": "Round cap reached."})
        return MediatorAction.finalize

    # Finalize as soon as nothing is oversubscribed
    if not oversub:
        tracer.log("mediator", "agents", "decision", {"action": MediatorAction.finalize.value, "reason": "No oversubscribed resources."})
        return MediatorAction.finalize

    system_msg = _system_prompt()
    user_msg = _build_user_message(state)

    try:
        decision = llm.complete_json(system_msg, user_msg, MediatorDecision)
        action = decision.action
    except Exception:
        # Deterministic fallback: if oversubscribed, challenge claimant with lowest priority score
        action = MediatorAction.challenge_agent if oversub else MediatorAction.finalize
        target = _pick_agent_to_challenge(state, oversub) if oversub else None
        decision = MediatorDecision(
            action=action,
            target_agent=target,
            counter_question=f"Resource deficit {oversub}. Please reduce your claim or use your fallback option.",
            rationale="Fallback decision logic based on deterministic scoring.",
        )

    # Handle challenge_agent setup
    if action == MediatorAction.challenge_agent:
        target = decision.target_agent
        if not target or target not in {c.agent for c in state.claims}:
            target = _pick_agent_to_challenge(state, oversub)
        question = decision.counter_question or f"Resources {oversub} are oversubscribed. Can you reduce your claim or use your fallback option?"
        counter = Counter(to_agent=target, question=question)
        state.counters.append(counter)
        tracer.log("mediator", target, "counter", counter.model_dump())

    elif action in (MediatorAction.request_fallback, MediatorAction.ask_peer_estimate):
        target = decision.target_agent or (state.claims[0].agent if state.claims else "medical")
        question = decision.counter_question or "What is your fallback estimate?"
        counter = Counter(to_agent=target, question=question)
        state.counters.append(counter)
        tracer.log("mediator", target, "counter", counter.model_dump())

    tracer.log(
        "mediator",
        "agents",
        "decision",
        {
            "action": action.value,
            "target": decision.target_agent,
            "rationale": decision.rationale,
        },
    )
    return action


def finalize(state: NegotiationState, llm, tracer: Tracer) -> Plan:
    allocation = {claim.agent: claim.resources for claim in state.claims}
    plan = Plan(allocation=allocation,
                justifications={claim.agent: claim.justification for claim in state.claims},
                conflicts_resolved=[], replans=state.replans, rounds_used=state.round,
                disputes_log=[])
    tracer.log("mediator", "system", "plan", plan.model_dump())
    return plan
