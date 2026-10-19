"""Tests for agents/medical.py – MedicalAgent claim, revision, estimate, and tool failure."""

import pytest

from lib.llm import StubLLM
from lib.schemas import Claim, Counter, Estimate, NegotiationState, Scenario
from lib.tracing import Tracer
from agents.medical import make_claim, estimate
from tools.failure import reset_failure, set_failure


@pytest.fixture(autouse=True)
def clean_failure():
    """Reset failure injection around every test."""
    reset_failure()
    yield
    reset_failure()


@pytest.fixture
def neg_state(sample_scenario) -> NegotiationState:
    """Minimal negotiation state wrapping the sample scenario."""
    return NegotiationState(scenario=sample_scenario, round=1)


# ── Initial claim tests ──────────────────────────────────────────────


def _initial_claim_response() -> dict:
    """A realistic LLM response for an initial medical claim."""
    return {
        "agent": "medical",
        "resources": {"boats": 2},
        "people_affected": 12,
        "urgency": 10,
        "time_window_hrs": 2.0,
        "justification": (
            "Zone B has 12 critical patients requiring immediate evacuation. "
            "Regional Hospital has limited ICU availability. "
            "Boats are the only viable transport since roads are flooded."
        ),
        "fallback_option": "Ambulance convoy via cleared alternate route.",
        "uncertainty": None,
    }


def test_initial_claim_is_valid(neg_state, tracer):
    """make_claim returns a valid Claim with agent='medical'."""
    llm = StubLLM([_initial_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "medical"
    assert claim.resources == {"boats": 2}
    assert claim.people_affected == 12
    assert claim.urgency == 10
    assert claim.time_window_hrs == 2.0
    assert len(claim.justification) > 0
    assert len(claim.fallback_option) > 0
    assert claim.uncertainty is None  # tools succeeded


def test_claim_enforces_medical_agent(neg_state, tracer):
    """Even if the LLM returns a wrong agent name, it is overridden to 'medical'."""
    resp = _initial_claim_response()
    resp["agent"] = "shelter"
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)
    assert claim.agent == "medical"


def test_claim_traces_are_logged(neg_state, tracer):
    """make_claim logs gather_data, claim_request, tool calls, and the final claim."""
    llm = StubLLM([_initial_claim_response()])
    make_claim(neg_state, llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "gather_data" in types
    assert "claim_request" in types
    assert "claim" in types
    # Tool calls produce tool_requested + tool_result pairs
    assert types.count("tool_requested") >= 2
    assert types.count("tool_result") >= 2


# ── Counter / revision tests ────────────────────────────────────────


def _revised_claim_response() -> dict:
    """A realistic LLM response defending/revising after a counter."""
    return {
        "agent": "medical",
        "resources": {"boats": 2},
        "people_affected": 12,
        "urgency": 9,
        "time_window_hrs": 3.0,
        "justification": (
            "Revised: 12 critical patients still require 2 boats. "
            "Extended time window to 3 hrs based on triage reassessment. "
            "ICU beds are nearly full at Regional Hospital."
        ),
        "fallback_option": "Request helicopter medevac for 4 most critical patients.",
        "uncertainty": 0.15,
    }


def test_revision_after_counter(neg_state, tracer):
    """When a Counter is supplied, make_claim produces a revised Claim."""
    counter = Counter(to_agent="medical", question="Can you reduce to 1 boat?")
    llm = StubLLM([_revised_claim_response()])
    claim = make_claim(neg_state, llm, tracer, counter=counter)

    assert isinstance(claim, Claim)
    assert claim.agent == "medical"
    assert claim.urgency == 9
    assert claim.time_window_hrs == 3.0
    assert "Revised" in claim.justification or len(claim.justification) > 0
    assert claim.fallback_option != ""


def test_counter_is_included_in_trace(neg_state, tracer):
    """The trace log for gather_data should include the counter context."""
    counter = Counter(to_agent="medical", question="Justify urgency 10.")
    llm = StubLLM([_revised_claim_response()])
    make_claim(neg_state, llm, tracer, counter=counter)

    gather_events = [e for e in tracer.events if e["type"] == "gather_data"]
    assert len(gather_events) >= 1
    assert gather_events[0]["payload"]["counter"] is not None
    assert gather_events[0]["payload"]["counter"]["question"] == "Justify urgency 10."


# ── Tool failure tests ──────────────────────────────────────────────


def _tool_failure_claim_response() -> dict:
    """LLM response when tools have failed – should include uncertainty."""
    return {
        "agent": "medical",
        "resources": {"boats": 2},
        "people_affected": 12,
        "urgency": 9,
        "time_window_hrs": 2.0,
        "justification": (
            "Tool data unavailable. Based on scenario zone data alone, "
            "estimating 12 critical patients needing evacuation."
        ),
        "fallback_option": "Deploy ambulance convoy.",
        "uncertainty": 0.6,
    }


def test_tool_failure_sets_uncertainty(neg_state, tracer):
    """When tools fail, the returned Claim has uncertainty set."""
    set_failure("*")  # All tools fail
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "medical"
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0


def test_tool_failure_default_uncertainty_if_llm_omits(neg_state, tracer):
    """If tools fail and the LLM returns uncertainty=None, the agent defaults to 0.5."""
    set_failure("*")
    resp = _tool_failure_claim_response()
    resp["uncertainty"] = None  # LLM didn't set it
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.uncertainty == 0.5


def test_partial_tool_failure(neg_state, tracer):
    """When only one tool fails, uncertainty is still set."""
    set_failure("hospital_capacity")  # only hospital tool fails
    resp = _tool_failure_claim_response()
    resp["uncertainty"] = None
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.uncertainty == 0.5
    assert claim.agent == "medical"


def test_claim_still_valid_on_full_tool_failure(neg_state, tracer):
    """Even with all tools down, the Claim schema is valid."""
    set_failure("*")
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    # Validate through Pydantic round-trip
    validated = Claim.model_validate(claim.model_dump())
    assert validated.agent == "medical"
    assert validated.resources == {"boats": 2}


# ── Estimate tests ──────────────────────────────────────────────────


def _estimate_response() -> dict:
    """A realistic LLM response for an estimate query."""
    return {
        "agent": "medical",
        "question": "How many patients need boats?",
        "value": 12.0,
        "confidence": 0.85,
        "rationale": "Zone B has 12 critical patients, all requiring boat evacuation.",
    }


def test_estimate_returns_valid_schema(neg_state, tracer):
    """estimate() returns a valid Estimate with agent='medical'."""
    llm = StubLLM([_estimate_response()])
    result = estimate(neg_state, "How many patients need boats?", llm, tracer)

    assert isinstance(result, Estimate)
    assert result.agent == "medical"
    assert result.value == 12.0
    assert 0 <= result.confidence <= 1
    assert len(result.rationale) > 0


def test_estimate_enforces_medical_agent(neg_state, tracer):
    """Even if the LLM returns a wrong agent, it is overridden."""
    resp = _estimate_response()
    resp["agent"] = "logistics"
    llm = StubLLM([resp])
    result = estimate(neg_state, "How many patients need boats?", llm, tracer)
    assert result.agent == "medical"


def test_estimate_traces_are_logged(neg_state, tracer):
    """estimate() logs estimate_request and the final estimate."""
    llm = StubLLM([_estimate_response()])
    estimate(neg_state, "How many patients need boats?", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "estimate_request" in types
    assert "estimate" in types
