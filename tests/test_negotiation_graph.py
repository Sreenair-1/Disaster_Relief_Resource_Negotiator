from lib.schemas import Claim, Counter, Estimate, MediatorAction, Plan, Scenario, Zone
from lib.llm import StubLLM


def _claim(agent, resources=None, uncertainty=None):
    return Claim(agent=agent, resources=resources or {}, people_affected=1, urgency=5,
                 time_window_hrs=2, justification="test", fallback_option="phase",
                 uncertainty=0.0 if uncertainty is None else uncertainty)


def test_graph_routes_challenge_and_stops_at_round_cap(monkeypatch, tracer):
    import graph.negotiation as graph

    monkeypatch.setattr(graph, "CLAIMERS", {a: (lambda s, l, t, counter=None, a=a: _claim(a))
                                              for a in ("medical", "shelter", "logistics")})
    calls = 0
    def challenge_once_per_cycle(state, llm, tracer):
        nonlocal calls
        calls += 1
        if state.round >= 3:
            return MediatorAction.finalize
        state.round_challenged_agents = ["medical"]
        state.counters.append(Counter(to_agent="medical", question="Respond",
                                      issued_round=state.round,
                                      counter_id=len(state.counters) + 1))
        return MediatorAction.challenge_agent

    monkeypatch.setattr(graph, "decide_next", challenge_once_per_cycle)
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(rounds_used=state.round))
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)])
    state = graph.run_negotiation(scenario, StubLLM(), tracer)
    assert state.round == 3
    assert calls == 3
    assert state.plan is not None


def test_graph_routes_fallback(monkeypatch, tracer):
    import graph.negotiation as graph

    monkeypatch.setattr(graph, "CLAIMERS", {a: (lambda s, l, t, counter=None, a=a: _claim(a))
                                              for a in ("medical", "shelter", "logistics")})
    def choose(state, llm, tracer):
        state.counters.append(Counter(to_agent="medical", question="Estimate tool-gap impact"))
        return MediatorAction.request_fallback

    monkeypatch.setattr(graph, "decide_next", choose)
    monkeypatch.setattr(graph, "ESTIMATORS", {"medical": lambda *args: Estimate(
        agent="medical", question="gap", value=1, confidence=0.5)})
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(rounds_used=state.round))
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)])
    state = graph.run_negotiation(scenario, StubLLM(), tracer)
    assert state.plan is not None
    assert any(event["type"] == "fallback_estimate" for event in tracer.events)


def test_claim_demand_partition_and_clamp_are_traced(monkeypatch, tracer):
    import graph.negotiation as graph

    def claim(agent):
        return lambda *args, **kwargs: _claim(agent, resources={}, uncertainty=0)

    monkeypatch.setattr(graph, "CLAIMERS", {
        agent: claim(agent) for agent in ("medical", "shelter", "logistics")
    })
    monkeypatch.setattr(graph, "decide_next", lambda *args: MediatorAction.finalize)
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan())
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)])
    graph.run_negotiation(scenario, StubLLM(), tracer)

    assert any(event["type"] == "demand_partition" for event in tracer.events)
    assert any(event["type"] == "claim_demand_clamped" for event in tracer.events)


def test_claim_fact_correction_is_traced(monkeypatch, tracer):
    import graph.negotiation as graph

    def false_capacity(agent):
        return lambda *args, **kwargs: Claim(
            agent=agent, resources={}, people_affected=1, urgency=5,
            time_window_hrs=2, justification="About 1,300 persons per boat in Zone A.",
            fallback_option="phase",
        )

    monkeypatch.setattr(graph, "CLAIMERS", {
        name: false_capacity(name) for name in ("medical", "shelter", "logistics")
    })
    monkeypatch.setattr(graph, "decide_next", lambda *args: MediatorAction.finalize)
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan())
    scenario = Scenario.model_validate({
        "disaster_type": "flood", "zones": [{"name": "Zone A", "population": 10}],
        "operations": {"boat_capacity_persons": 50},
    })
    state = graph.run_negotiation(scenario, StubLLM(), tracer)
    assert all(claim.contradicts_data for claim in state.claims)
    corrected = [event for event in tracer.events if event["type"] == "claim_corrected"]
    assert len(corrected) == 3
    assert "1,300 persons per boat" in corrected[0]["payload"]["before"]["justification"]
    assert "50 persons per boat" in corrected[0]["payload"]["after"]["justification"]


def test_claims_are_inventory_clamped_and_unknown_resources_are_reported(monkeypatch, tracer):
    import graph.negotiation as graph

    def overclaim(agent):
        return lambda *args, **kwargs: Claim(
            agent=agent, resources={"boats": 8, "food_rations": 99},
            people_affected=1, urgency=5, time_window_hrs=2,
            justification="need", fallback_option="phase",
        )
    monkeypatch.setattr(graph, "CLAIMERS", {
        name: overclaim(name) for name in ("medical", "shelter", "logistics")
    })
    monkeypatch.setattr(graph, "decide_next", lambda *args: MediatorAction.finalize)
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan())
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)],
                        resources={"boats": 2})
    state = graph.run_negotiation(scenario, StubLLM(), tracer)
    assert all(claim.resources == {"boats": 2} for claim in state.claims)
    assert len(state.non_inventory_requests) == 3
    assert len([event for event in tracer.events if event["type"] == "claim_clamped"]) == 3
