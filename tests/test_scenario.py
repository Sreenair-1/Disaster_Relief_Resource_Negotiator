"""Tests for agents/scenario.py – ScenarioAgent event injector, patch application, and nonsense rejection."""

import pytest

from lib.llm import StubLLM
from lib.schemas import NegotiationState, Scenario
from lib.tracing import Tracer
from agents.scenario import PRESET_EVENTS, ScenarioPatch, inject_event


@pytest.fixture
def neg_state(sample_scenario) -> NegotiationState:
    """Minimal negotiation state wrapping the sample scenario."""
    return NegotiationState(scenario=sample_scenario, round=1)


# ── Preset Events tests ──────────────────────────────────────────────


def test_preset_events_structure():
    """PRESET_EVENTS is a dict mapping string names to descriptive event texts."""
    assert isinstance(PRESET_EVENTS, dict)
    assert len(PRESET_EVENTS) >= 4
    for key, val in PRESET_EVENTS.items():
        assert isinstance(key, str) and len(key) > 0
        assert isinstance(val, str) and len(val) > 0
    assert "bridge_collapse" in PRESET_EVENTS
    assert "water_rising_fast" in PRESET_EVENTS
    assert "hospital_capacity" in PRESET_EVENTS


# ── Patch Application & Copy Immutability tests ──────────────────────


def _bridge_collapse_patch() -> dict:
    return {
        "is_valid": True,
        "summary": "Bridge to Zone A has collapsed, severing ground transport.",
        "affected_agents": ["logistics", "shelter"],
        "road_access": {"Zone A": 0.0},
        "roads": {"Zone A": "blocked"},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"shelter": 9, "logistics": 8},
    }


def test_patch_applied_correctly_to_copy(neg_state, tracer):
    """Event patch modifies scenario on a COPY; original scenario remains unchanged."""
    initial_access = neg_state.scenario.zones[0].road_access
    assert initial_access == 0.5  # Zone A initial road_access

    llm = StubLLM([_bridge_collapse_patch()])
    new_state = inject_event(neg_state, "Bridge to Z1 collapses", llm, tracer)

    # New state scenario has updated road_access
    zone_a_new = next(z for z in new_state.scenario.zones if z.name == "Zone A")
    assert zone_a_new.road_access == 0.0
    assert new_state.scenario.roads["Zone A"] == "blocked"

    # Original scenario must be UNCHANGED (copy immutability)
    zone_a_orig = next(z for z in neg_state.scenario.zones if z.name == "Zone A")
    assert zone_a_orig.road_access == 0.5


def test_affected_agents_correctly_set(neg_state, tracer):
    """inject_event correctly sets state.affected_agents from the patch."""
    llm = StubLLM([_bridge_collapse_patch()])
    new_state = inject_event(neg_state, "Bridge to Z1 collapses", llm, tracer)

    assert new_state.affected_agents == ["logistics", "shelter"]
    assert "Bridge to Z1 collapses" in new_state.events


def test_resource_loss_patch_application(neg_state, tracer):
    """Resource loss decrements the target resource count in scenario copy."""
    initial_boats = neg_state.scenario.resources.get("boats", 3)
    loss_patch = {
        "is_valid": True,
        "summary": "1 rescue boat lost to flood debris.",
        "affected_agents": ["logistics", "shelter", "medical"],
        "road_access": {},
        "roads": {},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {"boats": 1},
        "hospital_capacity": {},
        "urgency_hints": {},
    }
    llm = StubLLM([loss_patch])
    new_state = inject_event(neg_state, "Rescue boat damaged and sunk", llm, tracer)

    assert new_state.scenario.resources["boats"] == initial_boats - 1
    assert neg_state.scenario.resources["boats"] == initial_boats  # original untouched
    assert set(new_state.affected_agents) == {"logistics", "shelter", "medical"}


def test_water_rising_and_stranded_patch(neg_state, tracer):
    """Water rising event updates stranded count and road access."""
    water_patch = {
        "is_valid": True,
        "summary": "Water in Zone A rising faster, increasing stranded count.",
        "affected_agents": ["shelter", "medical"],
        "road_access": {"Zone A": 0.1},
        "roads": {"Zone A": "flooded"},
        "zone_casualties": {},
        "zone_stranded": {"Zone A": 450},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"shelter": 10},
    }
    llm = StubLLM([water_patch])
    new_state = inject_event(neg_state, "Water in Zone A rising faster", llm, tracer)

    zone_a = next(z for z in new_state.scenario.zones if z.name == "Zone A")
    assert zone_a.stranded == 450
    assert zone_a.road_access == 0.1
    assert new_state.scenario.roads["Zone A"] == "flooded"
    assert new_state.affected_agents == ["shelter", "medical"]


# ── Nonsense & Invalid Event tests ──────────────────────────────────


def _nonsense_patch() -> dict:
    return {
        "is_valid": False,
        "summary": "Unrelated event: alien landing is not relevant to flood disaster relief.",
        "affected_agents": [],
        "road_access": {},
        "roads": {},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {},
    }


def test_invalid_event_rejected_gracefully(neg_state, tracer):
    """Nonsense events are rejected; scenario unchanged, affected_agents set to empty."""
    llm = StubLLM([_nonsense_patch()])
    new_state = inject_event(neg_state, "Aliens have landed in Antarctica", llm, tracer)

    # Scenario unchanged
    assert new_state.scenario.zones == neg_state.scenario.zones
    assert new_state.scenario.resources == neg_state.scenario.resources
    # affected_agents is empty
    assert new_state.affected_agents == []

    # event_rejected was traced
    rejected_events = [e for e in tracer.events if e["type"] == "event_rejected"]
    assert len(rejected_events) == 1
    assert "Aliens have landed" in rejected_events[0]["payload"]["text"]


def test_empty_string_rejected_gracefully(neg_state, tracer):
    """Empty or whitespace-only events are rejected without error."""
    llm = StubLLM([])  # No response queued; should not be called
    new_state = inject_event(neg_state, "   ", llm, tracer)

    assert new_state.affected_agents == []
    rejected = [e for e in tracer.events if e["type"] == "event_rejected"]
    assert len(rejected) == 1


# ── Tracing tests ────────────────────────────────────────────────────


def test_event_traces_are_logged(neg_state, tracer):
    """Valid event logs event_eval_request and event payload."""
    llm = StubLLM([_bridge_collapse_patch()])
    inject_event(neg_state, "Bridge to Z1 collapses", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "event_eval_request" in types
    assert "event" in types
    event_entry = next(e for e in tracer.events if e["type"] == "event")
    assert event_entry["payload"]["text"] == "Bridge to Z1 collapses"
    assert event_entry["payload"]["affected_agents"] == ["logistics", "shelter"]
