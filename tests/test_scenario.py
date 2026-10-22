"""Tests for agents/scenario.py – ScenarioAgent event injector, patch application, and nonsense rejection."""

import pytest

from lib.llm import StubLLM
from lib.schemas import NegotiationState, Scenario
from lib.tracing import Tracer
from agents.scenario import (
    PRESET_EVENTS,
    ScenarioPatch,
    inject_event,
    _apply_patch,
    _diff_scenarios,
    _derive_affected_agents,
    _patch_is_empty,
)


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


def test_affected_agents_derived_from_diff(neg_state, tracer):
    """inject_event derives affected_agents from actual field changes, not LLM guess."""
    llm = StubLLM([_bridge_collapse_patch()])
    new_state = inject_event(neg_state, "Bridge to Z1 collapses", llm, tracer)

    # road_access change -> logistics, medical, shelter all derived
    assert "logistics" in new_state.affected_agents
    assert "medical" in new_state.affected_agents
    assert "shelter" in new_state.affected_agents
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
    # Resource change -> all agents affected
    assert "logistics" in new_state.affected_agents
    assert "medical" in new_state.affected_agents
    assert "shelter" in new_state.affected_agents


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
    # Derived from diff: stranded change -> shelter; road_access change -> logistics, medical, shelter
    assert "shelter" in new_state.affected_agents


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
    """Valid event logs event_eval_request and event_applied payload."""
    llm = StubLLM([_bridge_collapse_patch()])
    inject_event(neg_state, "Bridge to Z1 collapses", llm, tracer)

    types = [e["type"] for e in tracer.events]
    assert "event_eval_request" in types
    assert "event_applied" in types
    event_entry = next(e for e in tracer.events if e["type"] == "event_applied")
    assert event_entry["payload"]["text"] == "Bridge to Z1 collapses"
    assert "diff" in event_entry["payload"]
    assert len(event_entry["payload"]["diff"]["changed_fields"]) > 0


# ── Empty patch retry & no-op detection ──────────────────────────────


def _empty_patch() -> dict:
    """A valid patch with all empty mutation fields – the bug scenario."""
    return {
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


def test_empty_patch_retry_then_invalid(neg_state, tracer):
    """Empty patch -> retry -> still empty -> is_valid=false, no-op warning."""
    # First call returns empty patch, second call also returns empty patch
    llm = StubLLM([_empty_patch(), _empty_patch()])
    new_state = inject_event(neg_state, "Road access is blocked.", llm, tracer)

    # Must reject as no-op
    assert new_state.affected_agents == []
    assert new_state.scenario == neg_state.scenario

    # event_rejected with warning should be in trace
    rejected = [e for e in tracer.events if e["type"] == "event_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["is_valid"] is False
    assert rejected[0]["payload"]["reason"] == "event had no effect on the scenario"
    assert rejected[0]["payload"].get("warning") is True


def test_ineffective_patch_retry_then_invalid(neg_state, tracer):
    """Patch has fields matching current state (diff empty) -> retried -> still empty -> is_valid=false."""
    # Current road_access for Zone A is 0.5. Patch specifies 0.5 (no actual change).
    no_change_patch = {
        "is_valid": True,
        "summary": "Zone A road access remains 0.5.",
        "affected_agents": ["logistics"],
        "road_access": {"Zone A": 0.5},
        "roads": {},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {},
    }
    llm = StubLLM([no_change_patch, no_change_patch])
    new_state = inject_event(neg_state, "Zone A roads checked, no change", llm, tracer)

    assert new_state.affected_agents == []
    assert new_state.scenario == neg_state.scenario

    rejected = [e for e in tracer.events if e["type"] == "event_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["is_valid"] is False
    assert rejected[0]["payload"]["reason"] == "event had no effect on the scenario"
    assert rejected[0]["payload"].get("warning") is True


def test_empty_patch_retry_succeeds_on_second_try(neg_state, tracer):
    """Empty patch -> retry -> concrete patch -> patch applied."""
    concrete_patch = {
        "is_valid": True,
        "summary": "Road blocked to Zone A, all ground access severed.",
        "affected_agents": ["logistics"],
        "road_access": {"Zone A": 0.0},
        "roads": {"Zone A": "blocked"},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"logistics": 8},
    }
    # First call: empty patch (triggers retry), second call: concrete patch
    llm = StubLLM([_empty_patch(), concrete_patch])
    new_state = inject_event(neg_state, "Road access is blocked.", llm, tracer)

    # Patch was applied
    zone_a = next(z for z in new_state.scenario.zones if z.name == "Zone A")
    assert zone_a.road_access == 0.0
    assert len(new_state.affected_agents) > 0

    # event_applied should be in trace
    applied = [e for e in tracer.events if e["type"] == "event_applied"]
    assert len(applied) == 1


# ── Vague road event applies to all zones ────────────────────────────


def test_vague_road_event_applies_to_all_zones(neg_state, tracer):
    """When a road event names no zone, the LLM should apply it to all zones.
    We verify the patch is applied to every zone."""
    all_zones_patch = {
        "is_valid": True,
        "summary": "Road access blocked to all zones by landslide.",
        "affected_agents": ["logistics", "shelter", "medical"],
        "road_access": {"Zone A": 0.0, "Zone B": 0.0},
        "roads": {"Zone A": "blocked", "Zone B": "blocked"},
        "zone_casualties": {},
        "zone_stranded": {},
        "resource_loss": {},
        "hospital_capacity": {},
        "urgency_hints": {"logistics": 10},
    }
    llm = StubLLM([all_zones_patch])
    new_state = inject_event(neg_state, "Massive landslide blocks all roads", llm, tracer)

    for z in new_state.scenario.zones:
        assert z.road_access == 0.0
        assert new_state.scenario.roads[z.name] == "blocked"

    # All agents affected because road_access changed
    assert "logistics" in new_state.affected_agents
    assert "medical" in new_state.affected_agents
    assert "shelter" in new_state.affected_agents


# ── affected_agents derived from changed fields ─────────────────────


def test_affected_agents_from_casualties_change():
    """When only casualties change, medical is derived as affected."""
    from lib.schemas import ScenarioDiff
    diff = ScenarioDiff(changed_fields={
        "critical_patients:Zone B": {"before": 12, "after": 20},
    })
    agents = _derive_affected_agents(diff)
    assert "medical" in agents
    assert "logistics" not in agents  # no road change


def test_affected_agents_from_stranded_change():
    """When only stranded changes, shelter is derived as affected."""
    from lib.schemas import ScenarioDiff
    diff = ScenarioDiff(changed_fields={
        "stranded:Zone A": {"before": 300, "after": 600},
    })
    agents = _derive_affected_agents(diff)
    assert "shelter" in agents


def test_affected_agents_from_road_change():
    """When road_access changes, logistics + medical + shelter are all derived."""
    from lib.schemas import ScenarioDiff
    diff = ScenarioDiff(changed_fields={
        "road_access:Zone A": {"before": 0.5, "after": 0.0},
        "roads": {"before": {"Zone A": "blocked"}, "after": {"Zone A": "blocked"}},
    })
    agents = _derive_affected_agents(diff)
    assert "logistics" in agents
    assert "medical" in agents
    assert "shelter" in agents


# ── Every preset yields a non-empty diff ─────────────────────────────


def _preset_patch_for(key: str, scenario: Scenario) -> dict:
    """Return a realistic LLM patch for each preset event key."""
    patches = {
        "bridge_collapse": {
            "is_valid": True,
            "summary": "Bridge to Zone A collapsed, severing all ground road access.",
            "affected_agents": ["logistics", "shelter"],
            "road_access": {"Zone A": 0.0},
            "roads": {"Zone A": "blocked"},
            "zone_casualties": {},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {"logistics": 9},
        },
        "hospital_capacity": {
            "is_valid": True,
            "summary": "Regional Hospital reached full capacity, ICU beds exhausted. "
                       "Critical patients in Zone B can no longer be admitted; count rises to 15.",
            "affected_agents": ["medical"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {"Zone B": 15},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {"Regional Hospital": "full"},
            "urgency_hints": {"medical": 10},
        },
        "water_rising_fast": {
            "is_valid": True,
            "summary": "Water in Zone A rises sharply, doubling stranded residents from 300 to 600.",
            "affected_agents": ["shelter"],
            "road_access": {"Zone A": 0.2},
            "roads": {"Zone A": "flooded"},
            "zone_casualties": {},
            "zone_stranded": {"Zone A": 600},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {"shelter": 10},
        },
        "new_casualties": {
            "is_valid": True,
            "summary": "Eight new critical patients arrive in Zone B, total now 20.",
            "affected_agents": ["medical"],
            "road_access": {},
            "roads": {},
            "zone_casualties": {"Zone B": 20},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {"medical": 9},
        },
        "resource_loss": {
            "is_valid": True,
            "summary": "1 boat lost in warehouse flood.",
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
            "summary": "Landslide blocks primary access road to Zone B.",
            "affected_agents": ["logistics"],
            "road_access": {"Zone B": 0.0},
            "roads": {"Zone B": "blocked"},
            "zone_casualties": {},
            "zone_stranded": {},
            "resource_loss": {},
            "hospital_capacity": {},
            "urgency_hints": {"logistics": 9},
        },
    }
    return patches[key]


@pytest.mark.parametrize("key", list(PRESET_EVENTS.keys()))
def test_preset_event_produces_non_empty_diff(key, sample_scenario, tracer):
    """Every preset event, given a realistic LLM response, produces a non-empty diff."""
    state = NegotiationState(scenario=sample_scenario, round=1)
    patch_dict = _preset_patch_for(key, sample_scenario)
    llm = StubLLM([patch_dict])

    new_state = inject_event(state, PRESET_EVENTS[key], llm, tracer)

    # The event must have been applied (not rejected)
    applied = [e for e in tracer.events if e["type"] == "event_applied"]
    assert len(applied) == 1, f"Preset '{key}' did not produce an event_applied trace"

    # The diff must be non-empty
    diff_payload = applied[0]["payload"]["diff"]
    assert len(diff_payload["changed_fields"]) > 0, f"Preset '{key}' produced an empty diff"

    # affected_agents must be non-empty
    assert len(new_state.affected_agents) > 0, f"Preset '{key}' had no affected agents"


# ── Diff & utility unit tests ───────────────────────────────────────


def test_diff_detects_road_access_change(sample_scenario):
    """_diff_scenarios detects zone road_access changes."""
    before = sample_scenario
    after = before.model_copy(deep=True)
    after.zones[0].road_access = 0.0

    diff = _diff_scenarios(before, after)
    assert not diff.is_empty
    assert "road_access:Zone A" in diff.changed_fields


def test_diff_detects_resource_change(sample_scenario):
    """_diff_scenarios detects resource changes."""
    before = sample_scenario
    after = before.model_copy(deep=True)
    after.resources["boats"] = 2

    diff = _diff_scenarios(before, after)
    assert not diff.is_empty
    assert "resources" in diff.changed_fields


def test_patch_is_empty_true():
    """_patch_is_empty returns True for a patch with all empty mutation fields."""
    p = ScenarioPatch(
        is_valid=True, summary="test", affected_agents=["logistics"],
        urgency_hints={"logistics": 8},
    )
    assert _patch_is_empty(p)


def test_patch_is_empty_false():
    """_patch_is_empty returns False when any mutation field is populated."""
    p = ScenarioPatch(
        is_valid=True, summary="test", affected_agents=["logistics"],
        road_access={"Zone A": 0.0},
    )
    assert not _patch_is_empty(p)
