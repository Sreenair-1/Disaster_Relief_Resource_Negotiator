"""Tests for multi-agent negotiation, estimate feedback, claim revisions, and mediator early finalization."""

import pytest

from agents import mediator as mediator_agent
from agents import medical as medical_agent
from agents import shelter as shelter_agent
from graph.negotiation import compute_claim_diff, run_negotiation
from lib.llm import StubLLM
from lib.schemas import (
    Claim,
    Counter,
    Estimate,
    MediatorAction,
    NegotiationState,
    Plan,
    Scenario,
    Zone,
)
from lib.tracing import Tracer


@pytest.fixture
def oversubscribed_state(sample_scenario) -> NegotiationState:
    """State where 3 boats are available but 5 are claimed."""
    return NegotiationState(
        scenario=sample_scenario,  # boats: 3
        round=1,
        claims=[
            Claim(
                agent="medical",
                resources={"boats": 2},
                people_affected=12,
                urgency=9,
                time_window_hrs=1.0,
                justification="Critical patients in Zone B",
                fallback_option="1 boat with double trips",
            ),
            Claim(
                agent="shelter",
                resources={"boats": 2},
                people_affected=300,
                urgency=8,
                time_window_hrs=3.0,
                justification="Evacuate stranded residents in Zone A",
                fallback_option="Phased evacuation: 1 boat",
            ),
            Claim(
                agent="logistics",
                resources={"boats": 1},
                people_affected=50,
                urgency=6,
                time_window_hrs=6.0,
                justification="Deliver food rations",
                fallback_option="Trucks where road permits",
            ),
        ],
    )


@pytest.fixture
def non_oversubscribed_state(sample_scenario) -> NegotiationState:
    """State where 10 boats are available and 3 are claimed."""
    sc = sample_scenario.model_copy(deep=True)
    sc.resources = {"boats": 10}
    return NegotiationState(
        scenario=sc,
        round=1,
        claims=[
            Claim(
                agent="medical",
                resources={"boats": 1},
                people_affected=12,
                urgency=9,
                time_window_hrs=1.0,
                justification="Critical patients",
                fallback_option="none",
            ),
            Claim(
                agent="shelter",
                resources={"boats": 1},
                people_affected=50,
                urgency=7,
                time_window_hrs=4.0,
                justification="Evacuation",
                fallback_option="none",
            ),
            Claim(
                agent="logistics",
                resources={"boats": 1},
                people_affected=20,
                urgency=5,
                time_window_hrs=6.0,
                justification="Supplies",
                fallback_option="none",
            ),
        ],
    )


# ── 1. oversubscription -> challenge_agent -> revised claim stored ───


def test_oversubscription_challenges_agent_and_stores_revised_claim(oversubscribed_state, tracer):
    """When oversubscribed, Mediator challenges agent, target re-claims, and revised claim is stored with claim_revision trace."""
    mediator_stub = {
        "action": "challenge_agent",
        "target_agent": "shelter",
        "counter_question": "Boats are in deficit. Can you reduce to 1 boat?",
        "rationale": "Shelter has flexible fallback for phased evacuation.",
    }
    shelter_revised_stub = {
        "agent": "shelter",
        "resources": {"boats": 1},
        "people_affected": 300,
        "urgency": 8,
        "time_window_hrs": 3.0,
        "justification": "Revised down to 1 boat using staged evacuation waves.",
        "fallback_option": "1 boat staged wave 1",
    }
    mediator_final_stub = {
        "action": "finalize",
        "target_agent": None,
        "counter_question": "",
        "rationale": "Resource balance achieved.",
    }

    llm = StubLLM([mediator_stub, shelter_revised_stub, mediator_final_stub])
    final_state = run_negotiation(oversubscribed_state, llm, tracer, max_rounds=3)

    # Revised shelter claim is stored in state.claims
    shelter_claim = next(c for c in final_state.claims if c.agent == "shelter")
    assert shelter_claim.resources["boats"] == 1
    assert "Revised down" in shelter_claim.justification

    # Verify claim_revision trace event was logged
    revision_traces = [e for e in tracer.events if e["type"] == "claim_revision"]
    assert len(revision_traces) == 1
    rev_payload = revision_traces[0]["payload"]
    assert rev_payload["agent"] == "shelter"
    assert rev_payload["changes"]["resources"]["boats"]["before"] == 2
    assert rev_payload["changes"]["resources"]["boats"]["after"] == 1
    assert rev_payload["defended"] is False
    assert "boats: 2 -> 1" in rev_payload["summary"]


# ── 2. estimates appear in the next context ──────────────────────────


def test_estimates_stored_and_appear_in_next_context(sample_scenario, tracer):
    """Estimates stored in state.estimates feed back into Mediator and agent revision contexts."""
    est = Estimate(
        agent="logistics",
        question="Is Zone A road passable for evacuation?",
        value=0.2,
        confidence=0.85,
        rationale="Debris blocking primary transit lane.",
    )
    state = NegotiationState(
        scenario=sample_scenario,
        round=1,
        claims=[
            Claim(
                agent="shelter",
                resources={"boats": 2},
                people_affected=100,
                urgency=8,
                time_window_hrs=3.0,
                justification="Zone A evacuation",
                fallback_option="none",
            )
        ],
        estimates=[est],
    )

    # 1. Mediator context receives stored estimates
    mediator_prompt = mediator_agent._build_user_message(state)
    assert "Stored estimates" in mediator_prompt
    assert "Is Zone A road passable" in mediator_prompt
    assert "Debris blocking primary transit lane" in mediator_prompt
    assert "0.2" in mediator_prompt

    # 2. Need agent revision prompt receives stored estimates
    counter = Counter(to_agent="shelter", question="Can you revise with road feedback?")
    shelter_prompt = shelter_agent._build_user_message(state, {}, False, counter=counter)
    assert "Stored estimates" in shelter_prompt
    assert "Is Zone A road passable" in shelter_prompt
    assert "Debris blocking primary transit lane" in shelter_prompt

    # 3. Medical agent revision prompt receives stored estimates
    med_prompt = medical_agent._build_user_message(state, {}, False, counter=counter)
    assert "Stored estimates" in med_prompt
    assert "Is Zone A road passable" in med_prompt


# ── 3. no oversubscription -> finalize before round 3 ────────────────


def test_no_oversubscription_finalizes_before_round_3(non_oversubscribed_state, tracer):
    """When no resources are oversubscribed, Mediator immediately finalizes at round 1 without entering further rounds."""
    llm = StubLLM([])  # Deterministic logic recognizes no oversubscription without LLM call
    final_state = run_negotiation(non_oversubscribed_state, llm, tracer, max_rounds=3)

    assert final_state.round < 3
    assert final_state.plan is not None
    assert isinstance(final_state.plan, Plan)
    assert final_state.plan.allocation["medical"]["boats"] == 1
    assert final_state.plan.allocation["shelter"]["boats"] == 1
    assert final_state.plan.allocation["logistics"]["boats"] == 1

    # Verify decision trace was logged with finalize
    decision_traces = [e for e in tracer.events if e["type"] == "decision"]
    assert len(decision_traces) == 1
    assert decision_traces[0]["payload"]["action"] == "finalize"


# ── 4. round cap still forces finalize ────────────────────────────────


def test_round_cap_still_forces_finalize(oversubscribed_state, tracer):
    """When claims remain oversubscribed, round cap at 3 rounds still forces finalization as safety net."""
    # Set state round to 3
    oversubscribed_state.round = 3
    llm = StubLLM([])
    action = mediator_agent.decide_next(oversubscribed_state, llm, tracer)
    assert action == MediatorAction.finalize

    # Run negotiation loop respects round cap
    final_state = run_negotiation(oversubscribed_state, llm, tracer, max_rounds=3)
    assert final_state.plan is not None

    status_traces = [e for e in tracer.events if e["type"] == "status"]
    assert any("Round cap reached" in e.get("payload", {}).get("message", "") for e in status_traces)


# ── 5. action path differs between oversubscribed and non-oversubscribed ─


def test_action_path_differs_oversubscribed_vs_non_oversubscribed(sample_scenario, tracer):
    """Action path differs: non-oversubscribed goes directly to finalize, while oversubscribed executes challenge_agent."""
    # Path A: Non-oversubscribed
    ample_sc = sample_scenario.model_copy(deep=True)
    ample_sc.resources = {"boats": 10}
    state_a = NegotiationState(
        scenario=ample_sc,
        round=1,
        claims=[
            Claim(agent="medical", resources={"boats": 1}, people_affected=10, urgency=5, time_window_hrs=2.0, justification="", fallback_option=""),
            Claim(agent="shelter", resources={"boats": 1}, people_affected=10, urgency=5, time_window_hrs=2.0, justification="", fallback_option=""),
        ],
    )
    tracer_a = Tracer(tracer.path)
    tracer_a.events = []
    run_negotiation(state_a, StubLLM([]), tracer_a, max_rounds=3)
    actions_a = [e["payload"].get("action") for e in tracer_a.events if e["type"] == "decision"]

    # Path B: Oversubscribed
    scarce_sc = sample_scenario.model_copy(deep=True)
    scarce_sc.resources = {"boats": 2}
    state_b = NegotiationState(
        scenario=scarce_sc,
        round=1,
        claims=[
            Claim(agent="medical", resources={"boats": 2}, people_affected=10, urgency=5, time_window_hrs=2.0, justification="", fallback_option=""),
            Claim(agent="shelter", resources={"boats": 2}, people_affected=10, urgency=5, time_window_hrs=2.0, justification="", fallback_option=""),
        ],
    )
    b_decision = {"action": "challenge_agent", "target_agent": "medical", "counter_question": "Reduce boats"}
    b_revised = {"agent": "medical", "resources": {"boats": 0}, "people_affected": 10, "urgency": 5, "time_window_hrs": 2.0, "justification": "Use field clinic", "fallback_option": "clinic"}
    b_finalize = {"action": "finalize"}

    tracer_b = Tracer(tracer.path)
    tracer_b.events = []
    run_negotiation(state_b, StubLLM([b_decision, b_revised, b_finalize]), tracer_b, max_rounds=3)
    actions_b = [e["payload"].get("action") for e in tracer_b.events if e["type"] == "decision"]

    assert actions_a == ["finalize"]
    assert "challenge_agent" in actions_b
    assert actions_a != actions_b
