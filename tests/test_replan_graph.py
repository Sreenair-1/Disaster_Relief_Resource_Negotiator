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
    assert calls == ["medical", "medical"]  # affected agent also answers the challenge
    assert updated.plan.allocation["medical"]["boats"] == 2
    assert updated.replans == 1
    assert any(event["type"] == "reassessment" for event in tracer.events)


def test_replan_skips_when_event_rejected(monkeypatch, tracer):
    """When inject_event rejects the event (empty affected_agents), replan skips re-claiming."""
    import graph.replan as graph

    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)], resources={"boats": 3})
    original = NegotiationState(scenario=scenario, claims=[_claim("medical", 1)])
    calls = []

    def inject_reject(state, text, llm, tracer):
        return state.model_copy(deep=True, update={"affected_agents": []})

    monkeypatch.setattr(graph, "inject_event", inject_reject)
    monkeypatch.setattr(graph, "CLAIMERS", {"medical": lambda *args: (calls.append("medical") or _claim("medical", 2))})

    updated = graph.replan(original, "nonsense event", object(), tracer)
    # No agents should have been re-queried
    assert calls == []
    # Replans should NOT increment for a rejected event
    assert updated.replans == 0


def test_replan_road_change_requires_referencing_agents_to_reclaim(monkeypatch, tracer):
    """Every agent whose claim references a zone with changed road access is required to re-claim."""
    import graph.replan as graph

    scenario = Scenario(
        disaster_type="flood",
        zones=[
            Zone(name="Zone A", population=500, stranded=300, road_access=0.5),
            Zone(name="Zone B", population=120, stranded=12, critical_patients=12, road_access=0.5),
        ],
        resources={"boats": 4},
    )
    original = NegotiationState(
        scenario=scenario,
        claims=[
            Claim(agent="medical", resources={"boats": 1}, people_affected=10, urgency=5,
                  time_window_hrs=2, justification="Zone A critical patients", fallback_option="phase"),
            Claim(agent="shelter", resources={"boats": 1}, people_affected=10, urgency=5,
                  time_window_hrs=2, justification="Zone B evacuees", fallback_option="phase"),
        ],
    )
    calls = []

    def inject(state, text, llm, tracer):
        sc = state.scenario.model_copy(deep=True)
        sc.zones[0].road_access = 0.0
        return state.model_copy(deep=True, update={
            "scenario": sc,
            "affected_agents": ["logistics"],
        })

    monkeypatch.setattr(graph, "inject_event", inject)
    monkeypatch.setattr(graph, "CLAIMERS", {
        "medical": lambda *args: (calls.append("medical") or original.claims[0]),
        "shelter": lambda *args: (calls.append("shelter") or original.claims[1]),
        "logistics": lambda *args: (calls.append("logistics") or Claim(agent="logistics", resources={"boats": 1}, people_affected=5, urgency=5, time_window_hrs=2, justification="", fallback_option="")),
    })
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(
        allocation={"medical": {"boats": 1}, "shelter": {"boats": 1}, "logistics": {"boats": 1}},
        replans=state.replans,
    ))

    updated = graph.replan(original, "Bridge to Zone A collapses", object(), tracer)

    # Medical referenced Zone A, so it must be extended and re-claim
    assert "medical" in calls
    assert "logistics" in calls
    # Shelter referenced Zone B (unchanged road access), so it must NOT re-claim
    assert "shelter" not in calls
    assert "medical" in updated.affected_agents
    assert "shelter" not in updated.affected_agents

