"""Tests for agents/shelter.py – ShelterAgent claim, phased fallback, escalation, estimate, and tool failure."""

import pytest

from lib.llm import StubLLM
from lib.schemas import Claim, Counter, Estimate, NegotiationState, Scenario
from lib.tracing import Tracer
from agents.shelter import make_claim, estimate
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


def _initial_shelter_claim_response() -> dict:
    """A realistic LLM response for an initial shelter claim."""
    return {
        "agent": "shelter",
        "resources": {"boats": 3},
        "people_affected": 300,
        "urgency": 7,
        "time_window_hrs": 6.0,
        "justification": (
            "Zone A has 300 stranded residents. Relief Center shelter deficit is 0, "
            "but road access is completely cut off (0.2). 3 rescue boats are required to evacuate "
            "residents to the shelter center safely."
        ),
        "fallback_option": "Deploy 2 boats immediately and coordinate civilian craft.",
        "uncertainty": None,
    }


def test_initial_shelter_claim_is_valid(neg_state, tracer):
    """make_claim returns a valid Claim with agent='shelter'."""
    llm = StubLLM([_initial_shelter_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "shelter"
    assert claim.resources == {"boats": 3}
    assert claim.people_affected == 300
    assert claim.urgency == 7
    assert claim.time_window_hrs == 6.0
    assert len(claim.justification) > 0
    assert len(claim.fallback_option) > 0
    assert claim.uncertainty is None


def test_claim_enforces_shelter_agent(neg_state, tracer):
    """Even if the LLM returns an incorrect agent name, it is enforced to 'shelter'."""
    resp = _initial_shelter_claim_response()
    resp["agent"] = "medical"
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)
    assert claim.agent == "shelter"


def test_claim_traces_are_logged(neg_state, tracer):
    """make_claim logs gather_data, claim_request, tool calls, and final claim."""
    llm = StubLLM([_initial_shelter_claim_response()])
    make_claim(neg_state, llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "gather_data" in types
    assert "claim_request" in types
    assert "claim" in types
    # Tool calls produce tool_requested + tool_result pairs for both tools
    assert types.count("tool_requested") >= 2
    assert types.count("tool_result") >= 2


# ── Counter & Phased Fallback tests ──────────────────────────────────


def _counter_phased_claim_response() -> dict:
    """A realistic LLM response defending/revising after a counter with phased fallback."""
    return {
        "agent": "shelter",
        "resources": {"boats": 2},
        "people_affected": 300,
        "urgency": 8,
        "time_window_hrs": 8.0,
        "justification": (
            "Revised request to 2 boats based on mediator constraints. "
            "Evacuating 300 stranded residents in priority waves."
        ),
        "fallback_option": "Phased evacuation: 1 boat deployed over 6 hours for wave 1 (150 people), wave 2 thereafter.",
        "uncertainty": None,
    }


def test_phased_fallback_produced_under_counter(neg_state, tracer):
    """Under a mediator counter, a revised claim with phased fallback is returned."""
    counter = Counter(to_agent="shelter", question="Can you manage with fewer boats?")
    llm = StubLLM([_counter_phased_claim_response()])
    claim = make_claim(neg_state, llm, tracer, counter=counter)

    assert isinstance(claim, Claim)
    assert claim.agent == "shelter"
    assert claim.resources == {"boats": 2}
    assert "phase" in claim.fallback_option.lower()
    assert "6 hours" in claim.fallback_option.lower() or "wave" in claim.fallback_option.lower()


def test_phased_fallback_enforced_if_omitted_by_llm(neg_state, tracer):
    """If the LLM omits phased phrasing under a counter, agent ensures phased evacuation."""
    counter = Counter(to_agent="shelter", question="Can you operate with 1 boat?")
    resp = _counter_phased_claim_response()
    resp["fallback_option"] = "Use local volunteers"  # Doesn't specify phased
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer, counter=counter)

    assert "phased evacuation" in claim.fallback_option.lower()


# ── Water Rising Escalation tests ────────────────────────────────────


def test_escalation_on_event_context(neg_state, tracer):
    """When an event shows water rising faster, urgency is escalated."""
    neg_state.events.append("Flash flood alert: water rising faster than forecasted in Zone A.")
    # LLM initially returned urgency 6
    resp = _initial_shelter_claim_response()
    resp["urgency"] = 6
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.urgency >= 9


def test_escalation_on_mediator_counter_context(neg_state, tracer):
    """When a mediator counter mentions water rising faster, urgency is escalated."""
    counter = Counter(
        to_agent="shelter",
        question="Water is rising faster in Zone A, should we revise boat allocation?"
    )
    resp = _initial_shelter_claim_response()
    resp["urgency"] = 5
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer, counter=counter)

    assert claim.urgency >= 9


def test_no_escalation_when_water_not_rising(neg_state, tracer):
    """When no event or counter mentions water rising, urgency is preserved from LLM."""
    neg_state.events.append("Weather clear in Sector North.")
    resp = _initial_shelter_claim_response()
    resp["urgency"] = 7
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.urgency == 7


# ── Tool failure tests ──────────────────────────────────────────────


def _tool_failure_claim_response() -> dict:
    """LLM response when shelter tools fail – includes uncertainty."""
    return {
        "agent": "shelter",
        "resources": {"boats": 2},
        "people_affected": 250,
        "urgency": 8,
        "time_window_hrs": 4.0,
        "justification": "Tool telemetry unavailable; estimating from static zone baseline.",
        "fallback_option": "Phased community shuttle.",
        "uncertainty": 0.65,
    }


def test_tool_failure_sets_uncertainty(neg_state, tracer):
    """When tools fail, uncertainty is present on the resulting Claim."""
    set_failure("*")
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "shelter"
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0


def test_tool_failure_defaults_uncertainty_when_omitted(neg_state, tracer):
    """If tools fail and the LLM response omitted uncertainty, defaults to 0.5."""
    set_failure("shelter_capacity")
    resp = _tool_failure_claim_response()
    resp["uncertainty"] = None
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.uncertainty == 0.5


def test_claim_schema_valid_on_full_tool_failure(neg_state, tracer):
    """Full tool outage still produces a strictly valid Pydantic Claim."""
    set_failure("*")
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    validated = Claim.model_validate(claim.model_dump())
    assert validated.agent == "shelter"
    assert validated.resources == {"boats": 2}


# ── Estimate tests ──────────────────────────────────────────────────


def _estimate_response() -> dict:
    """A realistic LLM response for a shelter estimate query."""
    return {
        "agent": "shelter",
        "question": "How many shelter beds are available?",
        "value": 350.0,
        "confidence": 0.9,
        "rationale": "Relief Centers have 350 available capacity across monitored zones.",
    }


def test_estimate_returns_valid_schema(neg_state, tracer):
    """estimate() returns a valid Estimate model with agent='shelter'."""
    llm = StubLLM([_estimate_response()])
    result = estimate(neg_state, "How many shelter beds are available?", llm, tracer)

    assert isinstance(result, Estimate)
    assert result.agent == "shelter"
    assert result.value == 350.0
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.rationale) > 0


def test_estimate_enforces_shelter_agent(neg_state, tracer):
    """estimate() overrides incorrect agent names to 'shelter'."""
    resp = _estimate_response()
    resp["agent"] = "logistics"
    llm = StubLLM([resp])
    result = estimate(neg_state, "How many shelter beds are available?", llm, tracer)
    assert result.agent == "shelter"


def test_estimate_traces_are_logged(neg_state, tracer):
    """estimate() logs estimate_request and estimate in the tracer."""
    llm = StubLLM([_estimate_response()])
    estimate(neg_state, "How many shelter beds are available?", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "estimate_request" in types
    assert "estimate" in types
