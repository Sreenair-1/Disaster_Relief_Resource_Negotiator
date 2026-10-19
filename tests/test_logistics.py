"""Tests for agents/logistics.py – LogisticsAgent claim, blocked-road handling, estimate fallback, and tool failure."""

import pytest

from lib.llm import StubLLM
from lib.schemas import Claim, Counter, Estimate, NegotiationState, Scenario
from lib.tracing import Tracer
from agents.logistics import make_claim, estimate
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


def _initial_logistics_claim_response() -> dict:
    """A realistic LLM response for an initial logistics claim."""
    return {
        "agent": "logistics",
        "resources": {"trucks": 2, "boats": 1},
        "people_affected": 200,
        "urgency": 8,
        "time_window_hrs": 6.0,
        "justification": (
            "Delivering emergency drinking water and food rations to 200 cut-off residents. "
            "Route alert: Zone A access is cut (road_access 0.2), requiring 1 boat for transit; "
            "passable sectors can be reached with 2 trucks."
        ),
        "fallback_option": "Stage rations at Sector North hub using 1 truck.",
        "uncertainty": None,
    }


def test_initial_logistics_claim_is_valid(neg_state, tracer):
    """make_claim returns a valid Claim with agent='logistics'."""
    llm = StubLLM([_initial_logistics_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "logistics"
    assert claim.resources == {"trucks": 2, "boats": 1}
    assert claim.people_affected == 200
    assert claim.urgency == 8
    assert claim.time_window_hrs == 6.0
    assert len(claim.justification) > 0
    assert len(claim.fallback_option) > 0
    assert claim.uncertainty is None


def test_claim_enforces_logistics_agent(neg_state, tracer):
    """Even if the LLM returns an incorrect agent name, it is enforced to 'logistics'."""
    resp = _initial_logistics_claim_response()
    resp["agent"] = "medical"
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)
    assert claim.agent == "logistics"


def test_claim_traces_are_logged(neg_state, tracer):
    """make_claim logs gather_data, claim_request, tool calls, and final claim."""
    llm = StubLLM([_initial_logistics_claim_response()])
    make_claim(neg_state, llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "gather_data" in types
    assert "claim_request" in types
    assert "claim" in types
    # 3 tools: road_status, vehicle_inventory, stock_levels -> 3 pairs
    assert types.count("tool_requested") >= 3
    assert types.count("tool_result") >= 3


# ── Blocked-road handling tests ──────────────────────────────────────


def test_blocked_road_handling_in_justification(neg_state, tracer):
    """When routes are cut (e.g. road_access < 0.5 or blocked/flooded), justification flags them for the Mediator."""
    zone_b = next(z for z in neg_state.scenario.zones if z.name == "Zone B")
    assert zone_b.road_access < 0.5

    # If the LLM response forgot to mention cut routes, the agent automatically flags them
    resp = {
        "agent": "logistics",
        "resources": {"trucks": 1, "boats": 1},
        "people_affected": 150,
        "urgency": 7,
        "time_window_hrs": 5.0,
        "justification": "General supply distribution of water.",
        "fallback_option": "Reduce delivery payload.",
        "uncertainty": None,
    }
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert "Zone B" in claim.justification or "Zone A" in claim.justification
    assert "road access is cut" in claim.justification.lower() or "note for mediator" in claim.justification.lower()


def test_counter_revision_logistics(neg_state, tracer):
    """When responding to a mediator counter, revised claim is returned."""
    counter = Counter(to_agent="logistics", question="Can you prioritize water and drop 1 truck?")
    revised = {
        "agent": "logistics",
        "resources": {"trucks": 1, "boats": 1},
        "people_affected": 200,
        "urgency": 8,
        "time_window_hrs": 6.0,
        "justification": "Revised per counter: dropping 1 truck to conserve fuel, keeping 1 boat for cut routes.",
        "fallback_option": "Civilian logistics escort.",
        "uncertainty": None,
    }
    llm = StubLLM([revised])
    claim = make_claim(neg_state, llm, tracer, counter=counter)

    assert claim.resources == {"trucks": 1, "boats": 1}
    assert "Revised" in claim.justification


# ── Peer Estimate & Route Feasibility tests ──────────────────────────


def _estimate_feasibility_response() -> dict:
    """A realistic LLM response for a peer route feasibility inquiry."""
    return {
        "agent": "logistics",
        "question": "Is the route to Zone A passable for medical ambulances?",
        "value": 0.2,
        "confidence": 0.95,
        "rationale": "Zone A road_access is 0.2 with flood conditions; ground transport impassable, boat transit required.",
    }


def test_estimate_peer_route_feasibility_fallback(neg_state, tracer):
    """LogisticsAgent provides peer estimate on route feasibility when another agent asks."""
    llm = StubLLM([_estimate_feasibility_response()])
    est = estimate(
        neg_state,
        "Is the route to Zone A passable for medical ambulances?",
        llm,
        tracer,
    )

    assert isinstance(est, Estimate)
    assert est.agent == "logistics"
    assert est.value == 0.2
    assert est.confidence >= 0.9
    assert "impassable" in est.rationale or "boat" in est.rationale


def test_estimate_enforces_logistics_agent(neg_state, tracer):
    """estimate() overrides incorrect agent names to 'logistics'."""
    resp = _estimate_feasibility_response()
    resp["agent"] = "medical"
    llm = StubLLM([resp])
    est = estimate(neg_state, "Route status to Zone B?", llm, tracer)
    assert est.agent == "logistics"


def test_estimate_traces_are_logged(neg_state, tracer):
    """estimate() logs estimate_request and estimate in tracer."""
    llm = StubLLM([_estimate_feasibility_response()])
    estimate(neg_state, "Route feasibility to Zone A?", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "estimate_request" in types
    assert "estimate" in types


# ── Tool failure tests ──────────────────────────────────────────────


def _tool_failure_claim_response() -> dict:
    """LLM response when logistics tools fail – includes uncertainty."""
    return {
        "agent": "logistics",
        "resources": {"trucks": 1, "boats": 1},
        "people_affected": 150,
        "urgency": 7,
        "time_window_hrs": 6.0,
        "justification": "Telemetry offline; requesting standard baseline food and water supply transport.",
        "fallback_option": "Coordinate with local emergency cache.",
        "uncertainty": 0.6,
    }


def test_tool_failure_sets_uncertainty(neg_state, tracer):
    """When tools fail, uncertainty is set on the returned Claim."""
    set_failure("*")
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "logistics"
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0


def test_tool_failure_defaults_uncertainty_when_omitted(neg_state, tracer):
    """If tools fail and the LLM response omitted uncertainty, defaults to 0.5."""
    set_failure("road_status")
    resp = _tool_failure_claim_response()
    resp["uncertainty"] = None
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.uncertainty == 0.5


def test_partial_tool_failure_logistics(neg_state, tracer):
    """When only stock_levels fails, uncertainty is still handled."""
    set_failure("stock_levels")
    resp = _tool_failure_claim_response()
    resp["uncertainty"] = None
    llm = StubLLM([resp])
    claim = make_claim(neg_state, llm, tracer)

    assert claim.uncertainty == 0.5
    assert claim.agent == "logistics"


def test_claim_schema_valid_on_full_tool_failure(neg_state, tracer):
    """Full tool outage still produces a strictly valid Pydantic Claim."""
    set_failure("*")
    llm = StubLLM([_tool_failure_claim_response()])
    claim = make_claim(neg_state, llm, tracer)

    validated = Claim.model_validate(claim.model_dump())
    assert validated.agent == "logistics"
    assert validated.resources == {"trucks": 1, "boats": 1}
