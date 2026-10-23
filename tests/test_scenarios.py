"""Tests for realistic scenario files and tool-failure handling across all three need agents."""

from pathlib import Path
import pytest

from lib.llm import StubLLM
from lib.schemas import Claim, NegotiationState, Scenario
from lib.tracing import Tracer
from agents.medical import make_claim as medical_make_claim, estimate as medical_estimate
from agents.shelter import make_claim as shelter_make_claim, estimate as shelter_estimate
from agents.logistics import make_claim as logistics_make_claim, estimate as logistics_estimate
from tools.failure import reset_failure, set_failure

SCENARIOS_DIR = Path(__file__).parents[1] / "data" / "scenarios"
DATA_DIR = Path(__file__).parents[1] / "data"


@pytest.fixture(autouse=True)
def clean_failure():
    """Reset failure injection around every test."""
    reset_failure()
    yield
    reset_failure()


# ── Scenario Schema Validation Tests ─────────────────────────────────


def test_assumptions_document_exists():
    """data/scenarios/ASSUMPTIONS.md exists and documents district profiles and ratios."""
    assumptions_file = SCENARIOS_DIR / "ASSUMPTIONS.md"
    assert assumptions_file.exists()
    content = assumptions_file.read_text(encoding="utf-8")
    assert len(content) > 200
    assert "Cachar" in content or "cachar" in content
    assert "Wayanad" in content or "wayanad" in content


def test_all_scenarios_validate_against_schema():
    """All JSON files in data/scenarios/ and data/ validate strictly against lib.schemas.Scenario."""
    scenario_files = list(SCENARIOS_DIR.glob("*.json"))
    scenario_files.append(DATA_DIR / "scenario.json")

    assert len(scenario_files) >= 3, "Expected at least 2 in data/scenarios/ plus baseline scenario.json"

    for path in scenario_files:
        raw = path.read_text(encoding="utf-8")
        sc = Scenario.model_validate_json(raw)
        assert isinstance(sc, Scenario)
        assert len(sc.disaster_type) > 0
        assert len(sc.zones) >= 2
        for z in sc.zones:
            assert z.population >= 0
            assert z.stranded >= 0
            assert z.critical_patients >= 0
            assert 0.0 <= z.road_access <= 1.0
        assert len(sc.resources) > 0
        assert len(sc.hospitals) > 0
        assert isinstance(sc.roads, dict)


def test_cachar_scenario_metrics():
    """Cachar district scenario conforms to Assam riverine flood profile."""
    path = SCENARIOS_DIR / "cachar_monsoon_flood.json"
    sc = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    assert sc.disaster_type == "monsoon_flood"
    zone_names = [z.name for z in sc.zones]
    assert "Silchar Urban" in zone_names
    assert "Katigorah Border Plain" in zone_names
    assert sc.resources["boats"] >= 10


def test_wayanad_scenario_metrics():
    """Wayanad district scenario conforms to Western Ghats monsoon profile."""
    path = SCENARIOS_DIR / "wayanad_monsoon_flood.json"
    sc = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    assert sc.disaster_type == "monsoon_flood"
    zone_names = [z.name for z in sc.zones]
    assert "Meppadi Valley" in zone_names
    assert "Sulthan Bathery Lowland" in zone_names
    assert sc.resources["trucks"] >= 10
    assert sc.operations.boat_capacity_persons == 10
    assert sc.operations.trip_hours_by_zone["Mananthavady Riverine"] == 3
    assert sc.operations.truck_capacity_persons == 20
    assert sc.operations.hospitals[0].icu_beds_free is None


# ── Tool-Failure Path Tests for MedicalAgent ─────────────────────────


def _basic_claim_stub(agent: str) -> dict:
    return {
        "agent": agent,
        "resources": {"boats": 2},
        "people_affected": 50,
        "urgency": 8,
        "time_window_hrs": 4.0,
        "justification": f"{agent.capitalize()} priority request.",
        "fallback_option": "Local community support.",
        "uncertainty": None,
    }


def test_medical_agent_tool_failure_states_uncertainty_and_fallback(sample_scenario, tracer):
    """When tools fail, MedicalAgent states uncertainty and offers a fallback_estimate."""
    set_failure("hospital_capacity")
    state = NegotiationState(scenario=sample_scenario, round=1)
    llm = StubLLM([_basic_claim_stub("medical")])

    claim = medical_make_claim(state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "medical"
    # 1. Uncertainty is explicitly set
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0
    # 2. Stated explicitly in justification
    assert any(w in claim.justification.lower() for w in ("uncertain", "unreliable", "telemetry", "data gap", "failure"))
    # 3. Offers a fallback_estimate in fallback_option
    assert "fallback estimate" in claim.fallback_option.lower()
    assert "critical patients" in claim.fallback_option.lower()


def test_medical_estimate_under_tool_failure(sample_scenario, tracer):
    """MedicalAgent estimate() succeeds under tool failure."""
    set_failure("*")
    state = NegotiationState(scenario=sample_scenario, round=1)
    stub_resp = {
        "agent": "medical",
        "question": "How many ICU beds needed?",
        "value": 12.0,
        "confidence": 0.5,
        "rationale": "Tool offline; using baseline critical patients count.",
    }
    llm = StubLLM([stub_resp])
    est = medical_estimate(state, "How many ICU beds needed?", llm, tracer)
    assert est.agent == "medical"
    assert est.value == 12.0


# ── Tool-Failure Path Tests for ShelterAgent ─────────────────────────


def test_shelter_agent_tool_failure_states_uncertainty_and_fallback(sample_scenario, tracer):
    """When tools fail, ShelterAgent states uncertainty and offers a fallback_estimate."""
    set_failure("shelter_capacity")
    state = NegotiationState(scenario=sample_scenario, round=1)
    llm = StubLLM([_basic_claim_stub("shelter")])

    claim = shelter_make_claim(state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "shelter"
    # 1. Uncertainty explicitly set
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0
    # 2. Stated in justification
    assert any(w in claim.justification.lower() for w in ("uncertain", "unreliable", "telemetry", "data gap", "failure"))
    # 3. Offers a fallback_estimate
    assert "fallback estimate" in claim.fallback_option.lower()
    assert "stranded residents" in claim.fallback_option.lower()


def test_shelter_estimate_under_tool_failure(sample_scenario, tracer):
    """ShelterAgent estimate() succeeds under tool failure."""
    set_failure("*")
    state = NegotiationState(scenario=sample_scenario, round=1)
    stub_resp = {
        "agent": "shelter",
        "question": "How many shelter beds available?",
        "value": 200.0,
        "confidence": 0.45,
        "rationale": "Telemetry down; estimated from historical baseline.",
    }
    llm = StubLLM([stub_resp])
    est = shelter_estimate(state, "How many shelter beds available?", llm, tracer)
    assert est.agent == "shelter"
    assert est.confidence == 0.45


# ── Tool-Failure Path Tests for LogisticsAgent ───────────────────────


def test_logistics_agent_tool_failure_states_uncertainty_and_fallback(sample_scenario, tracer):
    """When tools fail, LogisticsAgent states uncertainty and offers a fallback_estimate."""
    set_failure("road_status")
    state = NegotiationState(scenario=sample_scenario, round=1)
    llm = StubLLM([_basic_claim_stub("logistics")])

    claim = logistics_make_claim(state, llm, tracer)

    assert isinstance(claim, Claim)
    assert claim.agent == "logistics"
    # 1. Uncertainty explicitly set
    assert claim.uncertainty is not None
    assert 0.0 < claim.uncertainty <= 1.0
    # 2. Stated in justification
    assert any(w in claim.justification.lower() for w in ("uncertain", "unreliable", "telemetry", "data gap", "failure"))
    # 3. Offers a fallback_estimate
    assert "fallback estimate" in claim.fallback_option.lower()
    assert "payload" in claim.fallback_option.lower() or "batches" in claim.fallback_option.lower()


def test_logistics_estimate_under_tool_failure(sample_scenario, tracer):
    """LogisticsAgent estimate() provides route feasibility fallback even when tools fail."""
    set_failure("*")
    state = NegotiationState(scenario=sample_scenario, round=1)
    stub_resp = {
        "agent": "logistics",
        "question": "Is Zone A road passable?",
        "value": 0.3,
        "confidence": 0.5,
        "rationale": "Tool offline; using zone static road_access coefficient.",
    }
    llm = StubLLM([stub_resp])
    est = logistics_estimate(state, "Is Zone A road passable?", llm, tracer)
    assert est.agent == "logistics"
    assert est.value == 0.3


# ── Multi-Agent Claims on Realistic Scenarios ────────────────────────


def test_agents_execute_on_cachar_scenario(tracer):
    """All 3 need agents generate valid claims on the Cachar monsoon scenario."""
    sc = Scenario.model_validate_json(
        (SCENARIOS_DIR / "cachar_monsoon_flood.json").read_text(encoding="utf-8")
    )
    state = NegotiationState(scenario=sc, round=1)

    med_claim = medical_make_claim(state, StubLLM([_basic_claim_stub("medical")]), tracer)
    shelter_claim = shelter_make_claim(state, StubLLM([_basic_claim_stub("shelter")]), tracer)
    log_claim = logistics_make_claim(state, StubLLM([_basic_claim_stub("logistics")]), tracer)

    assert med_claim.agent == "medical"
    assert shelter_claim.agent == "shelter"
    assert log_claim.agent == "logistics"
