"""Apply scenario events and renegotiate only affected claims."""

from typing import Any

from agents.logistics import make_claim as logistics_claim
from agents.mediator import finalize as mediator_finalize
from agents.medical import make_claim as medical_claim
from agents.scenario import inject_event
from agents.shelter import make_claim as shelter_claim
from lib.schemas import NegotiationState

CLAIMERS = {"medical": medical_claim, "shelter": shelter_claim, "logistics": logistics_claim}


def replan(state: NegotiationState, event_text: str, llm: Any, tracer: Any) -> NegotiationState:
    """Inject an event, refresh only affected agent claims, and reallocate."""
    updated = inject_event(state, event_text, llm, tracer)
    updated.replans += 1
    existing = {claim.agent: claim for claim in updated.claims}
    for agent in updated.affected_agents:
        make_claim = CLAIMERS.get(agent)
        if make_claim:
            existing[agent] = make_claim(updated, llm, tracer)
    updated.claims = list(existing.values())
    updated.plan = mediator_finalize(updated, llm, tracer)
    return updated
