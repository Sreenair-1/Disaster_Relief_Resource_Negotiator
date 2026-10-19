from lib.schemas import NegotiationState
from lib.tracing import Tracer


def inject_event(state: NegotiationState, text: str, llm, tracer: Tracer) -> NegotiationState:
    updated = state.model_copy(update={"events": [*state.events, text]})
    tracer.log("scenario", "mediator", "event", {"text": text})
    return updated
