"""Validate a negotiation trace and output plan against the scenario facts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.schemas import Plan, Scenario

CORE_AGENTS = {"medical", "shelter", "logistics"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(event, dict) or "type" not in event:
            raise ValueError(f"line {line_number} is not a trace event")
        events.append(event)
    return events


def validate(events: list[dict[str, Any]], plan: Plan, scenario: Scenario) -> list[tuple[str, bool, str]]:
    kinds = [str(e.get("type", "")).casefold() for e in events]
    senders = {str(e.get("sender", "")).casefold() for e in events}
    challenged = any(k in {"decision", "counter"} and
                     (e.get("payload") or {}).get("action") == "challenge_agent"
                     for k, e in zip(kinds, events))
    revised = "claim_revision" in kinds
    applied = any(k in {"event_applied", "scenario_event_applied"} for k in kinds)
    reassessed = "reassessment" in kinds
    failure_index = next((i for i, e in enumerate(events)
                          if e.get("type") == "tool_result" and
                          (e.get("payload") or {}).get("ok") is False), None)
    recovery = failure_index is not None and any(
        e.get("type") in {"fallback_estimate", "peer_estimate", "estimate"}
        for e in events[failure_index + 1:]
    )
    agent_count = len(CORE_AGENTS & senders)
    checks: list[tuple[str, bool, str]] = [
        ("at least four distinct agents", len(senders - {"toolcall", "feasibility", "system", ""}) >= 4,
         f"observed {len(senders - {'toolcall', 'feasibility', 'system', ''})}"),
        ("challenge and claim revision", challenged and revised, f"challenge={challenged}, revision={revised}"),
        ("event applied", applied, f"event_applied={applied}"),
        ("replan reassessment", reassessed, f"reassessment={reassessed}"),
        ("tool failure followed by recovery estimate", recovery, f"failure={failure_index is not None}, recovery={recovery}"),
    ]

    # Ensure a tool only asserts hospital capacities that the scenario verifies.
    hospital_known = {h.name: h for h in scenario.operations.hospitals}
    contradictions = []
    for event in events:
        if event.get("type") != "tool_result" or event.get("receiver") != "hospital_capacity":
            continue
        payload = (event.get("payload") or {}).get("data") or {}
        for item in payload.get("hospitals", []):
            name = item.get("hospital") or item.get("name")
            fact = hospital_known.get(name)
            if fact is None:
                contradictions.append(f"unlisted hospital {name!r}")
            elif fact.icu_beds_free is None and item.get("icu_available") is not None:
                contradictions.append(f"unverified ICU capacity asserted for {name}")
            elif fact.icu_beds_free is not None and item.get("icu_available") != fact.icu_beds_free:
                contradictions.append(f"ICU capacity mismatch for {name}")
    checks.append(("tool results match scenario hospital facts", not contradictions,
                   "; ".join(contradictions) if contradictions else "no unsupported hospital facts"))

    used: dict[str, int] = {}
    for awards in plan.allocation.values():
        for resource, amount in awards.items():
            used[resource] = used.get(resource, 0) + amount
    overspend = [f"{r}: {n}>{scenario.resources.get(r, 0)}" for r, n in used.items()
                 if n > scenario.resources.get(r, 0)]
    checks.append(("allocation within inventory", not overspend,
                   "; ".join(overspend) if overspend else "all awarded totals are within stock"))

    weak_justifications = []
    for agent in plan.allocation:
        note = plan.justifications.get(agent, "")
        lowered = note.casefold()
        if (not note.strip() or "deterministic priority" in lowered or
                not re.search(r"\bscore\D{0,12}\d", lowered) or
                not re.search(r"\brequest(?:ed)?\D{0,20}\d", lowered) or
                not re.search(r"\baward(?:ed)?\D{0,20}\d", lowered)):
            weak_justifications.append(agent)
    checks.append(("numeric data-backed justifications", not weak_justifications,
                   f"missing or incomplete for {', '.join(weak_justifications)}" if weak_justifications else "all allocated agents cited score, request, and award"))

    unknown_shortfalls = [item.resource for item in plan.unresolved_shortfalls
                          if item.resource not in scenario.resources]
    checks.append(("shortfalls use inventory resources", not unknown_shortfalls,
                   f"unknown resources: {', '.join(unknown_shortfalls)}" if unknown_shortfalls else "all shortfalls use listed resources"))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--scenario", type=Path, required=True)
    args = parser.parse_args()
    try:
        events = load_jsonl(args.trace)
        plan = Plan.model_validate_json(args.plan.read_text(encoding="utf-8"))
        scenario = Scenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL input validation: {exc}")
        return 2
    checks = validate(events, plan, scenario)
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    print(f"Result: {sum(ok for _, ok, _ in checks)}/{len(checks)} checks passed")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
