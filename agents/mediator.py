from lib.schemas import MediatorAction, NegotiationState, Plan
from lib.tracing import Tracer


def decide_next(state: NegotiationState, llm, tracer: Tracer) -> MediatorAction:
    action = MediatorAction.finalize if state.round >= 1 else MediatorAction.challenge_agent
    tracer.log("mediator", "agents", "decision", {"action": action.value})
    return action


def finalize(state: NegotiationState, llm, tracer: Tracer) -> Plan:
    allocation = {claim.agent: claim.resources for claim in state.claims}
    plan = Plan(allocation=allocation,
                justifications={claim.agent: claim.justification for claim in state.claims},
                conflicts_resolved=[], replans=state.replans, rounds_used=state.round,
                disputes_log=[])
    tracer.log("mediator", "system", "plan", plan.model_dump())
    return plan
