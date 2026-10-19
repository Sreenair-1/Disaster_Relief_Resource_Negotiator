"""Thin boundary between the UI and the future negotiation graph."""

from typing import Any
from tempfile import NamedTemporaryFile

from lib.schemas import Scenario
from lib.tracing import Tracer


def run_negotiation(scenario: Scenario) -> dict[str, Any]:
    """Placeholder entry point; replace this body with the graph invocation."""
    tracer = _new_tracer()
    tracer.log("system", "mediator", "scenario_loaded", scenario.model_dump())
    tracer.log("mediator", "system", "status", {"message": "Negotiation graph is not wired yet."})
    return {"events": tracer.events, "plan": None}


def inject_event(scenario: Scenario, text: str) -> dict[str, Any]:
    """Placeholder event injection boundary for the future graph."""
    tracer = _new_tracer()
    tracer.log("scenario", "mediator", "event", {"text": text})
    return {"events": tracer.events, "plan": None}


def _new_tracer() -> Tracer:
    trace_file = NamedTemporaryFile(prefix="relief-negotiation-", suffix=".jsonl", delete=False)
    trace_file.close()
    return Tracer(trace_file.name)
