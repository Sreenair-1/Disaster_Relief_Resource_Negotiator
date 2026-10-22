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
    actions = iter([MediatorAction.challenge_agent, MediatorAction.challenge_agent,
                    MediatorAction.finalize])
    monkeypatch.setattr(graph, "decide_next", lambda *args: next(actions))
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(rounds_used=state.round))
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)])
    state = graph.run_negotiation(scenario, StubLLM(), tracer)
    assert state.round == 3
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
