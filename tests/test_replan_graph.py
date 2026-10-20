from lib.schemas import Claim, NegotiationState, Plan, Scenario, Zone


def _claim(agent, value):
    return Claim(agent=agent, resources={"boats": value}, people_affected=1, urgency=5,
                 time_window_hrs=2, justification="test", fallback_option="phase")


def test_replan_requeries_only_affected_agent_and_updates_allocation(monkeypatch, tracer):
    import graph.replan as graph

    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)], resources={"boats": 4})
    original = NegotiationState(scenario=scenario, claims=[_claim("medical", 1), _claim("shelter", 1)])
    calls = []

    def inject(state, text, llm, tracer):
        return state.model_copy(deep=True, update={"affected_agents": ["medical"],
                                                   "scenario": state.scenario.model_copy(update={"resources": {"boats": 2}})})

    monkeypatch.setattr(graph, "inject_event", inject)
    monkeypatch.setattr(graph, "CLAIMERS", {"medical": lambda *args: (calls.append("medical") or _claim("medical", 2)),
                                              "shelter": lambda *args: (calls.append("shelter") or _claim("shelter", 9))})
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(
        allocation={claim.agent: {"boats": min(claim.resources["boats"], state.scenario.resources["boats"])}
                    for claim in state.claims}, replans=state.replans))
    updated = graph.replan(original, "resource event", object(), tracer)
    assert calls == ["medical"]
    assert updated.plan.allocation["medical"]["boats"] == 2
    assert updated.replans == 1
