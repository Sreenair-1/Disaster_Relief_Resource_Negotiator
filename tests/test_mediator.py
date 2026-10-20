from agents.mediator import decide_next, finalize
from lib.llm import StubLLM
from lib.schemas import Claim, MediatorAction, NegotiationState, Plan


class MemoryTracer:
    def __init__(self):
        self.events = []

    def log(self, sender, receiver, type, payload):
        event = {"sender": sender, "receiver": receiver, "type": type, "payload": payload}
        self.events.append(event)
        return event


def _claim(agent, resources, affected, urgency, window, uncertainty=0.1):
    return Claim(agent=agent, resources=resources, people_affected=affected,
                 urgency=urgency, time_window_hrs=window,
                 justification=f"{agent} need", fallback_option="use fewer boats",
                 uncertainty=uncertainty)


def _state(sample_scenario, round=0):
    return NegotiationState(
        scenario=sample_scenario,
        round=round,
        claims=[_claim("medical", {"boats": 2}, 12, 10, 2),
                _claim("shelter", {"boats": 2}, 300, 8, 6)],
    )


def test_decision_varies_with_state_and_emits_counter(sample_scenario):
    tracer = MemoryTracer()
    first, second = _state(sample_scenario), _state(sample_scenario, round=2)
    llm = StubLLM([
        {"action": "challenge_agent", "to_agent": "medical", "question": "Justify two boats.", "reasoning": "Uncertain."},
        {"action": "request_fallback", "to_agent": "shelter", "question": "Can you use one boat?", "reasoning": "Demand exceeds stock."},
    ])

    assert decide_next(first, llm, tracer) == MediatorAction.challenge_agent
    assert first.counters[-1].to_agent == "medical"
    assert decide_next(second, llm, tracer) == MediatorAction.request_fallback
    assert second.counters[-1].question == "Can you use one boat?"
    assert [event["type"] for event in tracer.events].count("decision") == 2


def test_round_three_forces_finalize_without_llm(sample_scenario):
    tracer = MemoryTracer()
    state = _state(sample_scenario, round=3)
    assert decide_next(state, StubLLM(), tracer) == MediatorAction.finalize
    assert tracer.events[-1]["payload"]["round"] == 3


def test_peer_estimate_decision_emits_targeted_counter(sample_scenario):
    tracer = MemoryTracer()
    state = _state(sample_scenario)
    llm = StubLLM([{
        "action": "ask_peer_estimate", "to_agent": "logistics",
        "question": "Estimate access to Zone B.", "reasoning": "Medical telemetry is uncertain.",
    }])

    assert decide_next(state, llm, tracer) == MediatorAction.ask_peer_estimate
    assert state.counters[-1].to_agent == "logistics"
    assert state.counters[-1].question == "Estimate access to Zone B."


def test_finalize_caps_inventory_and_seed_split(sample_scenario):
    tracer = MemoryTracer()
    state = _state(sample_scenario, round=2)
    llm = StubLLM([{
        "justifications": {"medical": "Critical patients have the highest urgency.",
                           "shelter": "One boat supports evacuation."},
        "conflicts_resolved": ["Medical priority resolved the boat shortage."],
        "disputes_log": [],
    }])

    plan = finalize(state, llm, tracer)

    assert isinstance(plan, Plan)
    assert plan.allocation == {"medical": {"boats": 1}, "shelter": {"boats": 2}}
    assert sum(item.get("boats", 0) for item in plan.allocation.values()) <= sample_scenario.resources["boats"]
    assert plan.rounds_used == 2
    assert plan.justifications["medical"]
    assert plan.disputes_log
    assert tracer.events[-1]["type"] == "plan"


def test_tied_score_uses_urgency_before_agent_name(sample_scenario):
    sample_scenario.resources["boats"] = 1
    state = NegotiationState(
        scenario=sample_scenario,
        claims=[
            _claim("alpha", {"boats": 1}, 100, 5, 5, uncertainty=0),
            _claim("zeta", {"boats": 1}, 50, 10, 5, uncertainty=0),
        ],
    )
    llm = StubLLM([{"justifications": {}, "conflicts_resolved": [], "disputes_log": []}])

    plan = finalize(state, llm, MemoryTracer())

    assert plan.allocation["zeta"] == {"boats": 1}
    assert plan.allocation["alpha"] == {}


def test_seed_three_boats_gives_medical_a_boat():
    from lib.schemas import Scenario

    scenario = Scenario.model_validate({
        "disaster_type": "flood",
        "zones": [{"name": "Zone A", "population": 1000, "stranded": 312,
                   "critical_patients": 12, "road_access": 1.0}],
        "resources": {"boats": 3},
    })
    state = NegotiationState(
        scenario=scenario,
        claims=[
            _claim("medical", {"boats": 3}, 12, 10, 2, uncertainty=0),
            _claim("shelter", {"boats": 3}, 312, 9, 6, uncertainty=0),
            _claim("logistics", {"boats": 3}, 312, 9, 12, uncertainty=0),
        ],
    )
    llm = StubLLM([{"justifications": {}, "conflicts_resolved": [], "disputes_log": []}])

    plan = finalize(state, llm, MemoryTracer())

    assert plan.allocation["medical"]["boats"] >= 1
    assert sum(award.get("boats", 0) for award in plan.allocation.values()) <= 3


def test_allocation_respects_inventory_and_each_request(sample_scenario):
    sample_scenario.resources.update({"boats": 3, "trucks": 2})
    state = NegotiationState(
        scenario=sample_scenario,
        claims=[
            _claim("medical", {"boats": 10, "trucks": 1}, 12, 10, 2, uncertainty=0),
            _claim("shelter", {"boats": 5, "trucks": 5}, 300, 8, 6, uncertainty=0),
            _claim("logistics", {"trucks": 5}, 300, 7, 12, uncertainty=0),
        ],
    )
    llm = StubLLM([{"justifications": {}, "conflicts_resolved": [], "disputes_log": []}])

    plan = finalize(state, llm, MemoryTracer())

    for resource, inventory in sample_scenario.resources.items():
        assert sum(award.get(resource, 0) for award in plan.allocation.values()) <= inventory
        for claim in state.claims:
            assert plan.allocation[claim.agent].get(resource, 0) <= claim.resources.get(resource, 0)


def test_changing_road_access_changes_allocation(sample_scenario):
    from lib.schemas import Zone

    sample_scenario.resources["boats"] = 1
    sample_scenario.zones = [
        Zone(name="Medical Zone", population=100, critical_patients=20, road_access=1.0),
        Zone(name="Logistics Zone", population=100, road_access=0.5),
    ]
    claims = [
        _claim("medical", {"boats": 1}, 20, 8, 4, uncertainty=0),
        _claim("logistics", {"boats": 1}, 20, 8, 4, uncertainty=0),
    ]
    claims[0].justification = "Patients in Medical Zone need transport."
    claims[1].justification = "Supplies for Logistics Zone need transport."
    before = NegotiationState(scenario=sample_scenario, claims=claims)
    changed_scenario = sample_scenario.model_copy(deep=True)
    changed_scenario.zones[0].road_access = 0.05
    after = NegotiationState(scenario=changed_scenario, claims=claims)

    def llm_for():
        return StubLLM([{"justifications": {}, "conflicts_resolved": [], "disputes_log": []}])

    before_plan = finalize(before, llm_for(), MemoryTracer())
    after_plan = finalize(after, llm_for(), MemoryTracer())

    assert before_plan.allocation["medical"] == {"boats": 1}
    assert after_plan.allocation["logistics"] == {"boats": 1}
