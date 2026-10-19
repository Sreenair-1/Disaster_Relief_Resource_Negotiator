from lib.schemas import Claim, Estimate, NegotiationState
from lib.tracing import Tracer


def make_claim(state, llm, tracer: Tracer, counter=None) -> Claim:
    claim = Claim(agent="shelter", resources={"boats": 2}, people_affected=300, urgency=8,
                  time_window_hrs=6, justification="Evacuate stranded residents from Zone A.",
                  fallback_option="Coordinate community boats.", uncertainty=0.25)
    tracer.log("shelter", "mediator", "claim", claim.model_dump())
    return claim


def estimate(state: NegotiationState, question: str, llm, tracer: Tracer) -> Estimate:
    result = Estimate(agent="shelter", question=question, value=300, confidence=0.8,
                      rationale="Placeholder shelter estimate.")
    tracer.log("shelter", "mediator", "estimate", result.model_dump())
    return result
