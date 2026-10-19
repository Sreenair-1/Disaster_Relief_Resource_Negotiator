from lib.schemas import Claim, Estimate, NegotiationState
from lib.tracing import Tracer


def make_claim(state, llm, tracer: Tracer, counter=None) -> Claim:
    claim = Claim(agent="logistics", resources={"boats": 1}, people_affected=50, urgency=7,
                  time_window_hrs=8, justification="Maintain flexible transport capacity.",
                  fallback_option="Reallocate trucks.", uncertainty=0.3)
    tracer.log("logistics", "mediator", "claim", claim.model_dump())
    return claim


def estimate(state: NegotiationState, question: str, llm, tracer: Tracer) -> Estimate:
    result = Estimate(agent="logistics", question=question, value=1, confidence=0.75,
                      rationale="Placeholder logistics estimate.")
    tracer.log("logistics", "mediator", "estimate", result.model_dump())
    return result
