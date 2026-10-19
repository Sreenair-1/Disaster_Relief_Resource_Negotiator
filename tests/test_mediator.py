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
    assert plan.allocation == {"medical": {"boats": 2}, "shelter": {"boats": 1}}
    assert sum(item.get("boats", 0) for item in plan.allocation.values()) <= sample_scenario.resources["boats"]
    assert plan.rounds_used == 2
    assert plan.justifications["medical"]
    assert plan.disputes_log
    assert tracer.events[-1]["type"] == "plan"
