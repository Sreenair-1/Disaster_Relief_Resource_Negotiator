"""Tests for mediator decide_next, negotiation graph flow, and estimate feedback."""

import pytest

from agents.mediator import decide_next
from lib.llm import StubLLM
from lib.schemas import (
    Claim, Counter, Estimate, MediatorAction,
    NegotiationState, Plan, Scenario, Zone,
)


class MemoryTracer:
    def __init__(self):
        self.events = []

    def log(self, sender, receiver, type, payload):
        event = {"sender": sender, "receiver": receiver, "type": type, "payload": payload}
        self.events.append(event)
        return event


def _claim(agent, boats=1, urgency=5, uncertainty=0.0, people=10, window=4):
    return Claim(
        agent=agent, resources={"boats": boats}, people_affected=people,
        urgency=urgency, time_window_hrs=window,
        justification=f"{agent} need", fallback_option="use fewer boats",
        uncertainty=uncertainty,
    )


def _oversubscribed_state():
    """3 boats total, 2+2 requested → oversubscribed."""
    scenario = Scenario(
        disaster_type="flood",
        zones=[Zone(name="Zone A", population=500, stranded=300,
                    critical_patients=20, road_access=0.5)],
        resources={"boats": 3},
    )
    return NegotiationState(
        scenario=scenario,
        round=1,
        claims=[_claim("medical", boats=2), _claim("shelter", boats=2)],
    )


def _balanced_state():
    """3 boats total, 1+1 requested → NOT oversubscribed."""
    scenario = Scenario(
        disaster_type="flood",
        zones=[Zone(name="Zone A", population=500, stranded=300,
                    critical_patients=20, road_access=0.5)],
        resources={"boats": 3},
    )
    return NegotiationState(
        scenario=scenario,
        round=1,
        claims=[_claim("medical", boats=1), _claim("shelter", boats=1)],
    )


# ── Oversubscription → challenge_agent → revised claim stored ────────


def test_oversubscription_challenge_revised_claim_stored(monkeypatch):
    """When oversubscribed, mediator challenges; the graph stores the revised claim."""
    import graph.negotiation as graph

    tracer = MemoryTracer()
    state = _oversubscribed_state()
    state.claims[0].resources["boats"] = 0

    # LLM returns challenge_agent targeting shelter
    llm = StubLLM([
        {"action": "challenge_agent", "to_agent": "shelter",
         "question": "Can you reduce boats?", "reasoning": "Oversubscribed."},
    ])

    action = decide_next(state, llm, tracer)
    assert action == MediatorAction.challenge_agent
    assert state.counters[-1].to_agent == "shelter"

    # Execute graph _challenge with revised claim
    revised_shelter = _claim("shelter", boats=1, urgency=7)
    monkeypatch.setattr(graph, "CLAIMERS", {
        "shelter": lambda *args, **kw: revised_shelter,
    })
    ctx = {
        "state": state,
        "llm": StubLLM([]),
        "tracer": tracer,
        "action": MediatorAction.challenge_agent,
        "next_agent": "shelter",
    }
    result = graph._challenge(ctx)
    new_state = result["state"]

    # Verify revised claim is stored in state.claims
    shelter_claim = next(c for c in new_state.claims if c.agent == "shelter")
    assert shelter_claim.resources["boats"] == 1
    assert shelter_claim.urgency == 7

    # Verify claim_revision trace was emitted
    rev_traces = [e for e in tracer.events if e["type"] == "claim_revision"]
    assert len(rev_traces) == 1
    assert rev_traces[0]["payload"]["agent"] == "shelter"
    assert rev_traces[0]["payload"]["changes"]["resources"]["before"] == {"boats": 2}
    assert rev_traces[0]["payload"]["changes"]["resources"]["after"] == {"boats": 1}


# ── Estimates appear in the next context ──────────────────────────────


def test_estimates_included_in_mediator_and_agent_context():
    """Collected estimates appear in the context passed to decide_next and agent revision prompt."""
    from agents import logistics as logistics_agent, medical as medical_agent, shelter as shelter_agent

    tracer = MemoryTracer()
    state = _oversubscribed_state()
    state.estimates.append(Estimate(
        agent="logistics", question="Road access?", value=0.3,
        confidence=0.8, rationale="Road is partially blocked.",
    ))

    llm = StubLLM([
        {"action": "challenge_agent", "to_agent": "shelter",
         "question": "Reduce boats?", "reasoning": "Still oversubscribed."},
    ])
    decide_next(state, llm, tracer)

    # 1. Mediator decision context
    decision_event = next(e for e in tracer.events if e["type"] == "decision")
    assert decision_event is not None
    assert len(state.estimates) == 1
    assert state.estimates[0].agent == "logistics"

    # 2. Targeted agent revision context receives stored estimates
    counter = Counter(to_agent="shelter", question="Can you reduce boats?")
    shelter_msg = shelter_agent._build_user_message(state, {}, False, counter=counter)
    assert "Stored estimates" in shelter_msg
    assert "Road access?" in shelter_msg
    assert "Road is partially blocked." in shelter_msg

    med_msg = medical_agent._build_user_message(state, {}, False, counter=counter)
    assert "Stored estimates" in med_msg

    log_msg = logistics_agent._build_user_message(state, {}, False, counter=counter)
    assert "Stored estimates" in log_msg


# ── No oversubscription → finalize before round 3 ────────────────────


def test_no_oversubscription_finalize_before_round_3():
    """When demand fits inventory, mediator should finalize early (before round 3)."""
    tracer = MemoryTracer()
    state = _balanced_state()
    assert state.round == 1  # Not at round cap

    llm = StubLLM([
        {"action": "finalize", "reasoning": "Demand fits inventory, no oversubscription."},
    ])

    action = decide_next(state, llm, tracer)
    assert action == MediatorAction.finalize

    # Verify round is less than 3
    assert state.round < 3


# ── Round cap still forces finalize ──────────────────────────────────


def test_round_cap_forces_finalize():
    """At round 3, decide_next forces finalize without calling the LLM."""
    tracer = MemoryTracer()
    state = _oversubscribed_state()
    state.round = 6

    # No LLM responses needed — the round cap should force finalize
    llm = StubLLM([])

    action = decide_next(state, llm, tracer)
    assert action == MediatorAction.finalize

    decision_event = next(e for e in tracer.events if e["type"] == "decision")
    assert decision_event["payload"]["reasoning"] == "Round cap reached."


# ── Action path differs: oversubscribed vs. non-oversubscribed ───────


def test_action_path_differs_oversubscribed_vs_balanced():
    """The mediator takes different actions for oversubscribed vs. balanced states."""
    tracer_over = MemoryTracer()
    tracer_balanced = MemoryTracer()

    state_over = _oversubscribed_state()
    state_balanced = _balanced_state()

    # Oversubscribed → challenge_agent
    llm_over = StubLLM([
        {"action": "challenge_agent", "to_agent": "shelter",
         "question": "Reduce boats?", "reasoning": "Demand exceeds stock."},
    ])
    action_over = decide_next(state_over, llm_over, tracer_over)

    # Balanced → finalize
    llm_balanced = StubLLM([
        {"action": "finalize", "reasoning": "No oversubscription."},
    ])
    action_balanced = decide_next(state_balanced, llm_balanced, tracer_balanced)

    assert action_over == MediatorAction.challenge_agent
    assert action_balanced == MediatorAction.finalize
    assert action_over != action_balanced


# ── Claim revision trace ─────────────────────────────────────────────


def test_challenge_produces_claim_revision_trace(monkeypatch):
    """After a challenge, the graph traces the revised claim with a change summary."""
    import graph.negotiation as graph

    tracer = MemoryTracer()

    old_claim = _claim("medical", boats=2, urgency=9)
    revised_claim = _claim("medical", boats=1, urgency=8)

    state = _oversubscribed_state()
    state.claims = [old_claim, _claim("shelter", boats=2)]
    state.counters.append(Counter(to_agent="medical", question="Justify 2 boats."))

    ctx = {
        "state": state,
        "llm": StubLLM([]),
        "tracer": tracer,
        "action": MediatorAction.challenge_agent,
        "next_agent": "medical",
    }

    # Monkeypatch CLAIMERS to return the revised claim
    monkeypatch.setattr(graph, "CLAIMERS", {
        "medical": lambda *args, **kw: revised_claim,
        "shelter": lambda *args, **kw: _claim("shelter"),
    })

    result = graph._challenge(ctx)

    # Check revision trace
    revision_events = [e for e in tracer.events if e["type"] == "claim_revision"]
    assert len(revision_events) == 1
    rev = revision_events[0]
    assert rev["payload"]["agent"] == "medical"
    assert rev["payload"]["defended_unchanged"] is False
    assert "resources" in rev["payload"]["changes"]
    assert rev["payload"]["changes"]["resources"]["before"] == {"boats": 2}
    assert rev["payload"]["changes"]["resources"]["after"] == {"boats": 1}
    assert "urgency" in rev["payload"]["changes"]


def test_challenge_defended_unchanged_is_traced(monkeypatch):
    """An agent may defend unchanged; the trace marks defended_unchanged=True."""
    import graph.negotiation as graph

    tracer = MemoryTracer()

    same_claim = _claim("medical", boats=2, urgency=9)

    state = _oversubscribed_state()
    state.claims = [same_claim, _claim("shelter", boats=2)]
    state.counters.append(Counter(to_agent="medical", question="Justify 2 boats."))

    ctx = {
        "state": state,
        "llm": StubLLM([]),
        "tracer": tracer,
        "action": MediatorAction.challenge_agent,
        "next_agent": "medical",
    }

    # Monkeypatch CLAIMERS to return the exact same claim (defended unchanged)
    monkeypatch.setattr(graph, "CLAIMERS", {
        "medical": lambda *args, **kw: _claim("medical", boats=2, urgency=9),
        "shelter": lambda *args, **kw: _claim("shelter"),
    })

    graph._challenge(ctx)

    revision_events = [e for e in tracer.events if e["type"] == "claim_revision"]
    assert len(revision_events) == 1
    assert revision_events[0]["payload"]["defended_unchanged"] is True
    assert revision_events[0]["payload"]["changes"] == {}


# ── Fallback stores estimate in state ─────────────────────────────────


def test_fallback_stores_estimate_in_state(monkeypatch):
    """When a fallback/estimate is requested, the estimate is stored in state.estimates."""
    import graph.negotiation as graph

    tracer = MemoryTracer()
    state = _oversubscribed_state()
    state.counters.append(Counter(to_agent="medical", question="Estimate Zone B access."))

    est = Estimate(agent="medical", question="Zone B access", value=0.3,
                   confidence=0.8, rationale="Road partially blocked.")

    monkeypatch.setattr(graph, "ESTIMATORS", {
        "medical": lambda *args: est,
    })

    ctx = {
        "state": state,
        "llm": StubLLM([]),
        "tracer": tracer,
        "action": MediatorAction.request_fallback,
        "next_agent": "medical",
    }

    result = graph._fallback(ctx)
    new_state = result["state"]

    assert len(new_state.estimates) == 1
    assert new_state.estimates[0].agent == "medical"
    assert new_state.estimates[0].value == 0.3

    # Also check trace
    fb_events = [e for e in tracer.events if e["type"] == "fallback_estimate"]
    assert len(fb_events) == 1
