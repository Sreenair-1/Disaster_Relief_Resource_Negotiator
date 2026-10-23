import json
from pathlib import Path

from ui.format import agent_style, describe_event, group_by_round, replan_diff, summarize_plan

ROOT = Path(__file__).parents[1]


def test_every_sample_trace_event_formats():
    events = [json.loads(line) for line in (ROOT / "samples/trace_full_run.jsonl").read_text(encoding="utf-8").splitlines() if line]
    described = [describe_event(event) for event in events]
    assert len(described) == len(events)
    assert all(item["headline"] and item["agent"] and "icon" in item for item in described)
    assert any("requested" in item["headline"] for item in described)
    assert any("Tool returned" in item["headline"] for item in described)


def test_unknown_events_and_agent_names_have_safe_fallbacks():
    item = describe_event({"sender": "new_agent", "type": "new_event", "payload": {}})
    assert item["headline"] == "New Agent recorded new event"
    assert item["icon"]
    assert agent_style("medical")["name"] == "Medical"


def test_group_events_by_round_carries_round_forward():
    events = [
        {"type": "claim", "payload": {}},
        {"type": "decision", "payload": {"round": 2}},
        {"type": "claim_revision", "payload": {"revision_round": 2}},
    ]
    grouped = group_by_round(events)
    assert len(grouped[1]) == 1
    assert len(grouped[2]) == 2


def test_plan_summary_supports_old_and_new_shapes():
    old = summarize_plan({"allocation": {}, "rounds_used": 2, "conflicts_resolved": []})
    assert old["status_label"] == "Available"
    assert old["kpis"]["critical_patients_covered_percent"] is None

    new = summarize_plan({
        "status": "constrained", "rounds_used": 3, "replans": 1,
        "conflicts_resolved": ["boats"], "life_safety_gap": True,
        "unresolved_shortfalls": [{"resource": "boats", "unmet_amount": 2}],
        "coverage": {"Zone A": {"critical_patients": {"demand": 10, "people_served": 6}}},
    })
    assert new["kpis"]["critical_patients_covered_percent"] == 60.0
    assert "boats short by 2" in new["headline"]
    assert "Critical patients remain uncovered" in new["headline"]


def test_identical_plan_diff_says_no_change():
    plan = {"allocation": {"medical": {"boats": 2}}}
    diff = replan_diff(plan, plan)
    assert diff["changed"] is False
    assert diff["headline"] == "No change to the allocation."


def test_replan_diff_includes_direction_and_reason():
    before = {"allocation": {"medical": {"boats": 2}}}
    after = {"allocation": {"medical": {"boats": 1}},
             "reassessments": [{"agent": "medical", "reason": "Road access was cut."}]}
    diff = replan_diff(before, after)
    assert diff["rows"][0]["arrow"] == "↓"
    assert diff["rows"][0]["reason"] == "Road access was cut."
