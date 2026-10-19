from lib.schemas import Claim, Estimate, NegotiationState
from lib.tracing import Tracer


def make_claim(state, llm, tracer: Tracer, counter=None) -> Claim:
    claim = Claim(agent="medical", resources={"boats": 2}, people_affected=12, urgency=10,
                  time_window_hrs=2, justification="Critical patients require rapid transport.",
                  fallback_option="Use ambulance convoy.", uncertainty=0.2)
    tracer.log("medical", "mediator", "claim", claim.model_dump())
    return claim


def estimate(state: NegotiationState, question: str, llm, tracer: Tracer) -> Estimate:
    result = Estimate(agent="medical", question=question, value=2, confidence=0.8,
                      rationale="Placeholder medical estimate.")
    tracer.log("medical", "mediator", "estimate", result.model_dump())
    return result
