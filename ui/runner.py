"""UI boundary for negotiation and scenario replanning graphs."""

import os
from tempfile import NamedTemporaryFile
from typing import Any

from graph.negotiation import run_negotiation as run_graph
from graph.replan import replan as replan_graph
from lib.schemas import NegotiationState, Scenario
from lib.tracing import Tracer


def run_negotiation(scenario: Scenario, llm: Any | None = None) -> dict[str, Any]:
    """Run the graph and return serializable display data, or a friendly error."""
    tracer = _new_tracer()
    if llm is None:
        if not os.getenv("OPENAI_API_KEY"):
            return _error_result(tracer, "No API key configured. Set OPENAI_API_KEY to run a live negotiation, or pass a StubLLM for a local demo.")
        try:
            from lib.llm import LLMClient

            llm = LLMClient()
        except Exception as exc:
            return _error_result(tracer, f"Could not initialize the language model: {exc}")
    try:
        tracer.log("system", "mediator", "scenario_loaded", scenario.model_dump())
        state = run_graph(scenario, llm, tracer)
        return _result(state, tracer)
    except Exception as exc:
        return _error_result(tracer, _friendly_error(exc))


def inject_event(state_or_scenario: NegotiationState | Scenario, text: str,
                 llm: Any | None = None) -> dict[str, Any]:
    """Apply an event to a prior run and return its before/after plans."""
    tracer = _new_tracer()
    if llm is None:
        if not os.getenv("OPENAI_API_KEY"):
            return _error_result(tracer, "No API key configured. Set OPENAI_API_KEY to apply a live scenario event.")
        try:
            from lib.llm import LLMClient

            llm = LLMClient()
        except Exception as exc:
            return _error_result(tracer, f"Could not initialize the language model: {exc}")
    state = (NegotiationState(scenario=state_or_scenario) if isinstance(state_or_scenario, Scenario)
             else state_or_scenario)
    before_plan = state.plan.model_copy(deep=True) if state.plan else None
    try:
        updated = replan_graph(state, text, llm, tracer)
        result = _result(updated, tracer)
        result["before_plan"] = before_plan.model_dump() if before_plan else None
        return result
    except Exception as exc:
        result = _error_result(tracer, _friendly_error(exc))
        result["state"] = state
        result["before_plan"] = before_plan.model_dump() if before_plan else None
        return result


def _result(state: NegotiationState, tracer: Tracer) -> dict[str, Any]:
    warnings = [event.get("payload", {}).get("error", "A relief data tool failed.")
                for event in tracer.events
                if event.get("type") == "tool_result"
                and isinstance(event.get("payload"), dict)
                and not event["payload"].get("ok", True)]
    return {"state": state, "plan": state.plan.model_dump() if state.plan else None,
            "events": tracer.events, "error": None, "warnings": warnings,
            "before_plan": None}


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip()
    if "api key" in message.lower() or "openai_api_key" in message.lower():
        return "No API key configured. Set OPENAI_API_KEY and try again."
    if "tool" in message.lower():
        return f"A relief data tool failed: {message}"
    return f"Negotiation could not finish: {message or type(exc).__name__}"


def _error_result(tracer: Tracer, message: str) -> dict[str, Any]:
    tracer.log("system", "user", "error", {"message": message})
    return {"state": None, "plan": None, "events": tracer.events, "error": message,
            "warnings": [], "before_plan": None}


def _new_tracer() -> Tracer:
    trace_file = NamedTemporaryFile(prefix="relief-negotiation-", suffix=".jsonl", delete=False)
    trace_file.close()
    return Tracer(trace_file.name)
