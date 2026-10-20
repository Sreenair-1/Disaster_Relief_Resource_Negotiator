"""Tests for agents/scenario.py – ScenarioAgent event injector, patch application, and nonsense rejection."""

import pytest

from lib.llm import StubLLM
from lib.schemas import Claim, NegotiationState, Scenario
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


def test_affected_agents_correctly_set(sample_scenario, tracer):
    """inject_event derives affected_agents when road access changes and an agent claims that zone."""
    state = NegotiationState(
        scenario=sample_scenario,
        round=1,
        claims=[
            Claim(
                agent="shelter",
                resources={"boats": 2},
                people_affected=100,
                urgency=8,
                time_window_hrs=4.0,
                justification="Evacuation in Zone A",
                fallback_option="none",
            )
        ],
    )
    llm = StubLLM([_bridge_collapse_patch()])
    new_state = inject_event(state, "Bridge to Z1 collapses", llm, tracer)

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
    """Water rising event updates stranded count and road access, deriving shelter and logistics."""
    water_patch = {
        "is_valid": True,
        "summary": "Water in Zone A rising faster, increasing stranded count.",
        "affected_agents": ["shelter", "logistics"],
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
    assert set(new_state.affected_agents) == {"shelter", "logistics"}


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


def test_event_traces_are_logged(sample_scenario, tracer):
    """Valid event logs event_eval_request, event_applied, and event payload."""
    state = NegotiationState(
        scenario=sample_scenario,
        round=1,
        claims=[
            Claim(
                agent="shelter",
                resources={"boats": 2},
                people_affected=100,
                urgency=8,
                time_window_hrs=4.0,
                justification="Evacuation in Zone A",
                fallback_option="none",
            )
        ],
    )
    llm = StubLLM([_bridge_collapse_patch()])
    inject_event(state, "Bridge to Z1 collapses", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "event_eval_request" in types
    assert "event_applied" in types
    assert "event" in types

    applied_entry = next(e for e in tracer.events if e["type"] == "event_applied")
    assert applied_entry["payload"]["text"] == "Bridge to Z1 collapses"
    assert "diff" in applied_entry["payload"]
    assert "zones" in applied_entry["payload"]["diff"]

    event_entry = next(e for e in tracer.events if e["type"] == "event")
    assert event_entry["payload"]["text"] == "Bridge to Z1 collapses"
    assert event_entry["payload"]["affected_agents"] == ["logistics", "shelter"]


# ── Mandatory Defect Regression Tests ────────────────────────────────


def test_empty_patch_retries_and_becomes_invalid(neg_state, tracer):
    """empty patch -> retry -> is_valid=false with reason 'event had no effect on the scenario'."""
    empty_patch = {
        "is_valid": True,
        "summary": "Road access is blocked.",
        "affected_agents": ["logistics"],
        "road_access": {},
        "roads": {},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"logistics": 8},
    }
    # Initial call returns empty patch; retry call also returns empty patch
    llm = StubLLM([empty_patch, empty_patch])
    new_state = inject_event(neg_state, "Road access is blocked.", llm, tracer)

    # Must NOT report as successful replan
    assert new_state.scenario == neg_state.scenario
    assert new_state.affected_agents == []
    assert "Road access is blocked." not in new_state.events

    # Verify retry trace was emitted
    retry_events = [e for e in tracer.events if e["type"] == "event_retry_request"]
    assert len(retry_events) == 1

    # Verify rejection trace was emitted with specific reason
    rejected_events = [e for e in tracer.events if e["type"] == "event_rejected"]
    assert len(rejected_events) == 1
    assert rejected_events[0]["payload"]["reason"] == "event had no effect on the scenario"


def test_vague_road_event_applies_to_all_zones(neg_state, tracer):
    """vague road event applies to all zones when no specific zone is named."""
    vague_patch = {
        "is_valid": True,
        "summary": "Applied road access block across all plausible zones (Zone A, Zone B).",
        "affected_agents": ["logistics"],
        "road_access": {"Zone A": 0.0, "Zone B": 0.0},
        "roads": {"Zone A": "blocked", "Zone B": "blocked"},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"logistics": 9},
    }
    llm = StubLLM([vague_patch])
    new_state = inject_event(neg_state, "Road access is blocked.", llm, tracer)

    for z in new_state.scenario.zones:
        assert z.road_access == 0.0
        assert new_state.scenario.roads[z.name] == "blocked"

    applied_events = [e for e in tracer.events if e["type"] == "event_applied"]
    assert len(applied_events) == 1
    diff = applied_events[0]["payload"]["diff"]
    assert "Zone A" in diff["zones"]
    assert "Zone B" in diff["zones"]


def test_affected_agents_derives_from_changed_fields(sample_scenario, tracer):
    """affected_agents derives from changed fields (road/route->logistics+claimants, casualties->medical, stranded->shelter)."""
    state_with_claims = NegotiationState(
        scenario=sample_scenario,
        round=1,
        claims=[
            Claim(
                agent="shelter",
                people_affected=50,
                urgency=5,
                time_window_hrs=4.0,
                justification="Zone A rescue",
                fallback_option="none",
            ),
            Claim(
                agent="medical",
                people_affected=10,
                urgency=8,
                time_window_hrs=2.0,
                justification="Zone B clinic",
                fallback_option="none",
            ),
        ],
    )

    # 1. Casualties change only -> medical is affected (even if LLM guessed empty)
    cas_patch = {
        "is_valid": True,
        "summary": "Casualties surge in Zone B.",
        "affected_agents": [],
        "road_access": {},
        "roads": {},
        "zone_casualties": {"Zone B": 25},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {},
    }
    st1 = inject_event(state_with_claims, "Surge in Zone B", StubLLM([cas_patch]), tracer)
    assert st1.affected_agents == ["medical"]

    # 2. Road access change in Zone A -> logistics + shelter (claim references Zone A); medical not affected
    road_patch = {
        "is_valid": True,
        "summary": "Zone A road severed.",
        "affected_agents": ["medical"],  # Erroneous LLM guess should be corrected
        "road_access": {"Zone A": 0.0},
        "roads": {"Zone A": "blocked"},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {},
    }
    st2 = inject_event(state_with_claims, "Zone A road blocked", StubLLM([road_patch]), tracer)
    assert "logistics" in st2.affected_agents
    assert "shelter" in st2.affected_agents
    assert "medical" not in st2.affected_agents

    # 3. Stranded change in Zone A -> shelter
    stranded_patch = {
        "is_valid": True,
        "summary": "Water rises in Zone A.",
        "affected_agents": [],
        "road_access": {},
        "roads": {},
        "zone_casualties": {},
        "zone_stranded": {"Zone A": 600},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {},
    }
    st3 = inject_event(state_with_claims, "Water rises in Zone A", StubLLM([stranded_patch]), tracer)
    assert st3.affected_agents == ["shelter"]


def test_every_preset_yields_non_empty_diff(sample_scenario, tracer):
    """Every preset in PRESET_EVENTS names a zone and magnitude and yields a non-empty diff."""
    assert len(PRESET_EVENTS) >= 4

    preset_patches = {
        "bridge_collapse": {
            "is_valid": True,
            "summary": "Bridge to Zone A collapses, cutting road access.",
            "affected_agents": ["logistics"],
            "road_access": {"Zone A": 0.0},
            "roads": {"Zone A": "blocked"},
            "zone_casualties": {},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {},
        },
        "new_casualties": {
            "is_valid": True,
            "summary": "Eight new critical patients arrive in Zone B.",
            "affected_agents": ["medical"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {"Zone B": 20},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {},
        },
        "water_rising_fast": {
            "is_valid": True,
            "summary": "Water in Zone A rises sharply, doubling stranded residents.",
            "affected_agents": ["shelter"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {},
            "zone_stranded": {"Zone A": 600},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {},
        },
        "hospital_capacity": {
            "is_valid": True,
            "summary": "Regional Hospital in Zone A full; diverting 10 patients.",
            "affected_agents": ["medical"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {"Zone A": 10},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {"Regional Hospital": "full"},
            "urgency_hints": {},
        },
        "resource_loss": {
            "is_valid": True,
            "summary": "1 boat destroyed in Zone A depot.",
            "affected_agents": ["logistics"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {},
            "zone_stranded": {},
            "resource_loss": {"boats": 1},
            "hospital_capacity": {},
            "urgency_hints": {},
        },
        "road_blocked": {
            "is_valid": True,
            "summary": "Landslide in Zone B cuts road access to 0.0.",
            "affected_agents": ["logistics"],
            "road_access": {"Zone B": 0.0},
            "roads": {"Zone B": "blocked"},
            "zone_casualties": {},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {},
        },
    }

    for key, text in PRESET_EVENTS.items():
        # Check that preset names a zone
        assert any(z.name in text for z in sample_scenario.zones), f"Preset '{key}' does not name a zone: {text}"
        # Check that preset specifies a magnitude
        assert any(
            mag in text.lower() for mag in ["eight", "15", "10", "1", "all", "doubling", "0.0", "full", "sharply"]
        ), f"Preset '{key}' does not specify a magnitude: {text}"

        patch_data = preset_patches[key]
        state = NegotiationState(scenario=sample_scenario, round=1)
        new_state = inject_event(state, text, StubLLM([patch_data]), tracer)

        # Scenario must be mutated
        assert new_state.scenario != sample_scenario, f"Preset '{key}' produced no scenario change"
        assert len(new_state.affected_agents) > 0, f"Preset '{key}' had no affected agents"
        assert text in new_state.events

        # event_applied trace contains non-empty diff
        applied_events = [e for e in tracer.events if e["type"] == "event_applied"]
        assert len(applied_events) > 0
        diff = applied_events[-1]["payload"]["diff"]
        assert diff, f"Preset '{key}' yielded empty diff in event_applied trace"
