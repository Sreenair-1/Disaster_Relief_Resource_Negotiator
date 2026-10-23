import json
from pathlib import Path

from lib.schemas import Plan, Scenario
from scripts.check_trace import validate


def test_trace_checker_passes_complete_synthetic_run():
    root = Path(__file__).parents[1]
    scenario = Scenario.model_validate_json((root / "samples/input.json").read_text())
    events = [
        {"sender": "medical", "type": "claim"},
        {"sender": "shelter", "type": "claim"},
        {"sender": "logistics", "type": "claim"},
        {"sender": "mediator", "type": "decision", "payload": {"action": "challenge_agent"}},
        {"sender": "medical", "type": "claim_revision"},
        {"sender": "scenario", "type": "event_applied"},
        {"sender": "mediator", "type": "reassessment"},
        {"sender": "toolcall", "receiver": "inventory", "type": "tool_result", "payload": {"ok": False}},
        {"sender": "medical", "type": "fallback_estimate"},
        {"sender": "toolcall", "receiver": "hospital_capacity", "type": "tool_result", "payload": {"ok": True, "data": {"hospitals": [{"hospital": "Regional Hospital", "icu_available": None}]}}},
    ]
    plan = Plan(
        allocation={"medical": {"boats": 1}},
        justifications={"medical": "Score 100; requested 2, awarded 1; shortfall 1."},
    )
    assert all(ok for _, ok, _ in validate(events, plan, scenario))


def test_trace_checker_detects_overallocation_and_boilerplate():
    root = Path(__file__).parents[1]
    scenario = Scenario.model_validate_json((root / "samples/input.json").read_text())
    plan = Plan(
        allocation={"medical": {"boats": 4}},
        justifications={"medical": "Allocated according to deterministic priority."},
    )
    results = dict((name, ok) for name, ok, _ in validate([], plan, scenario))
    assert results["allocation within inventory"] is False
    assert results["numeric data-backed justifications"] is False
