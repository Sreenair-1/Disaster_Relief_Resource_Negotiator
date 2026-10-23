"""Plain-language formatting helpers for trace events and allocation plans."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

_AGENT_STYLES = {
    "medical": ("🩺", "Medical"),
    "shelter": ("🏠", "Shelter"),
    "logistics": ("🚚", "Logistics"),
    "mediator": ("⚖️", "Mediator"),
    "scenario": ("🌊", "Scenario"),
    "toolcall": ("🛠️", "Tools"),
    "tools": ("🛠️", "Tools"),
    "system": ("ℹ️", "System"),
}
_RESOURCE_LABELS = {"boats": "boats", "trucks": "trucks", "ambulances": "ambulances"}


def agent_style(agent: str | None) -> dict[str, str]:
    """Return a stable icon and display name for a known or unknown sender."""
    key = str(agent or "system").casefold()
    icon, name = _AGENT_STYLES.get(key, ("ℹ️", str(agent or "System").replace("_", " ").title()))
    return {"icon": icon, "name": name}


def describe_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Turn one tracer record into a concise human-readable event description."""
    sender = str(event.get("sender", "system"))
    recipient = str(event.get("receiver", ""))
    kind = str(event.get("type", "unknown"))
    payload = event.get("payload")
    data = payload if isinstance(payload, Mapping) else {}
    style = agent_style(sender)
    agent = style["name"]
    round_number = data.get("round", data.get("revision_round", data.get("issued_round")))
    details = ""

    if kind == "claim_request":
        headline = f"{agent} gathered information for a resource request"
        details = "Some information may be uncertain." if data.get("tool_failed") else ""
    elif kind == "gather_data":
        headline = f"{agent} requested current relief information"
    elif kind == "claim":
        resources = data.get("resources", {})
        request = _resource_phrase(resources)
        urgency = data.get("urgency")
        headline = f"{agent} requested {request or 'no listed resources'}"
        if urgency is not None:
            headline += f" (urgency {urgency}/10)"
        details = str(data.get("justification", ""))
    elif kind == "decision":
        action = data.get("action", "made a decision")
        counter = data.get("counter")
        if action in {"challenge_agent", "request_fallback", "ask_peer_estimate"} and isinstance(counter, Mapping):
            target = agent_style(counter.get("to_agent"))["name"]
            question = str(counter.get("question", ""))
            labels = {"challenge_agent": "challenged", "request_fallback": "asked for a fallback from",
                      "ask_peer_estimate": "requested an independent estimate from"}
            headline = f"Mediator {labels.get(action, 'contacted')} {target}"
            details = question
        else:
            headline = f"Mediator chose {str(action).replace('_', ' ')}"
            details = str(data.get("reasoning", ""))
    elif kind == "claim_revision":
        revised = data.get("revised_claim", {})
        changes = data.get("changes", {})
        resource_change = changes.get("resources", {}) if isinstance(changes, Mapping) else {}
        before = resource_change.get("before", {}) if isinstance(resource_change, Mapping) else {}
        after = resource_change.get("after", {}) if isinstance(resource_change, Mapping) else {}
        resource = next(iter(set(before) | set(after)), None) if isinstance(before, Mapping) and isinstance(after, Mapping) else None
        if resource:
            headline = f"{agent} revised {resource}: {before.get(resource, 0)} → {after.get(resource, 0)}"
        else:
            headline = f"{agent} responded to the Mediator"
        details = str(data.get("justification", revised.get("justification", ""))) if isinstance(revised, Mapping) else ""
    elif kind in {"fallback_estimate", "estimate"}:
        headline = f"{agent} provided an estimate"
        details = str(data.get("rationale", ""))
    elif kind == "estimate_request":
        headline = f"{agent} requested a fallback estimate"
        details = str(data.get("question", ""))
    elif kind == "event_eval_request":
        headline = "Scenario checked a proposed event"
        details = str(data.get("text", ""))
    elif kind in {"event", "event_applied"}:
        headline = f"Scenario event applied: {data.get('summary', data.get('text', 'conditions changed'))}"
        details = _changed_fields(data.get("diff") or data.get("changed_fields") or {})
    elif kind == "event_rejected":
        headline = "Scenario event was not applied"
        details = str(data.get("reason", data.get("text", "The event had no effect.")))
    elif kind == "tool_requested":
        headline = f"Tools checked {recipient.replace('_', ' ')}"
    elif kind == "tool_result":
        if data.get("ok", True) is False:
            headline = f"Tool failed: {recipient.replace('_', ' ')}"
            details = "An agent flagged the missing information as uncertain."
        else:
            headline = f"Tool returned information: {recipient.replace('_', ' ')}"
            details = _short_tool_summary(data.get("data", {}))
    elif kind == "plan":
        headline = "Mediator completed the allocation plan"
        details = f"Status: {data.get('status', 'not specified')}"
    elif kind == "scenario_loaded":
        headline = "Scenario loaded and ready to review"
    elif kind == "error":
        headline = "The run could not be completed"
        details = str(data.get("message", "Please review the setup and try again."))
    elif kind == "reassessment":
        headline = f"Mediator reassessed {agent_style(data.get('agent', recipient))[ 'name' ]}'s allocation"
        details = str(data.get("reason", ""))
    else:
        headline = f"{agent} recorded {kind.replace('_', ' ')}"
        details = str(data.get("message", ""))

    return {"icon": style["icon"], "agent": agent, "headline": headline,
            "detail": details, "kind": kind, "round": round_number}


def group_by_round(events: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    """Group events in order, carrying the most recent explicit round forward."""
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    current_round = 1
    for event in events:
        data = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        explicit = data.get("round", data.get("revision_round", data.get("issued_round")))
        if isinstance(explicit, int) and explicit > 0:
            current_round = explicit
        grouped[current_round].append(event)
    return dict(grouped)


def summarize_plan(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarize a plan, tolerating optional fields from older saved plans."""
    plan = plan if isinstance(plan, Mapping) else {}
    status = str(plan.get("status") or "available")
    shortfalls = plan.get("unresolved_shortfalls") or []
    gap = bool(plan.get("life_safety_gap", False))
    if status == "constrained":
        items = [f"{item.get('resource', 'resource')} short by {item.get('unmet_amount', 0)}"
                 for item in shortfalls if isinstance(item, Mapping)]
        headline = "Constrained: " + ("; ".join(items) if items else "some requests remain unmet")
    elif status == "resolved":
        headline = "Plan ready: requests were compared against available resources."
    else:
        headline = "Allocation plan is ready to review."
    if gap:
        headline += " Critical patients remain uncovered by the verified transport capacity."

    coverage = plan.get("coverage") or {}
    total_demand = total_served = 0
    for zone_data in coverage.values() if isinstance(coverage, Mapping) else []:
        critical = zone_data.get("critical_patients", {}) if isinstance(zone_data, Mapping) else {}
        if isinstance(critical, Mapping):
            total_demand += int(critical.get("demand", 0) or 0)
            total_served += int(critical.get("people_served", 0) or 0)
    pct: float | None = round(100 * total_served / total_demand, 1) if total_demand else None
    return {"status": status, "status_label": status.replace("_", " ").title(),
            "headline": headline, "kpis": {
                "rounds": int(plan.get("rounds_used", 0) or 0),
                "conflicts": len(plan.get("conflicts_resolved") or []),
                "replans": int(plan.get("replans", 0) or 0),
                "critical_patients_covered_percent": pct,
            }}


def replan_diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compare agent awards before and after a replan, including direction arrows."""
    before = before if isinstance(before, Mapping) else {}
    after = after if isinstance(after, Mapping) else {}
    old_allocation = before.get("allocation") or {}
    new_allocation = after.get("allocation") or {}
    rows = []
    for agent in sorted(set(old_allocation) | set(new_allocation)):
        old_resources, new_resources = old_allocation.get(agent, {}), new_allocation.get(agent, {})
        for resource in sorted(set(old_resources) | set(new_resources)):
            old, new = int(old_resources.get(resource, 0) or 0), int(new_resources.get(resource, 0) or 0)
            delta = new - old
            rows.append({"agent": agent_style(agent)["name"], "resource": resource,
                         "before": old, "after": new, "delta": delta,
                         "arrow": "↑" if delta > 0 else "↓" if delta < 0 else "→",
                         "reason": _replan_reason(after, agent)})
    changed = any(row["delta"] for row in rows)
    return {"changed": changed, "headline": "Allocation changed after the event." if changed else "No change to the allocation.",
            "rows": rows}


def _resource_phrase(resources: Any) -> str:
    if not isinstance(resources, Mapping):
        return ""
    return " and ".join(f"{amount} {_RESOURCE_LABELS.get(name, name.replace('_', ' '))}"
                        for name, amount in resources.items() if amount)


def _changed_fields(diff: Any) -> str:
    if not isinstance(diff, Mapping):
        return ""
    phrases = []
    for field, change in diff.items():
        if isinstance(change, Mapping) and "before" in change and "after" in change:
            label = field.replace("_", " ")
            phrases.append(f"{label} {change['before']} → {change['after']}")
    return "; ".join(phrases)


def _short_tool_summary(data: Any) -> str:
    if not isinstance(data, Mapping):
        return ""
    for key in ("total_critical_patients", "total_stranded", "total_population", "count"):
        if key in data:
            label = key.replace("total_", "").replace("_", " ")
            return f"Reported {data[key]} {label}."
    return ""


def _replan_reason(plan: Mapping[str, Any], agent: str) -> str:
    reassessments = plan.get("reassessments") or []
    for item in reassessments:
        if isinstance(item, Mapping) and item.get("agent") == agent and item.get("reason"):
            return str(item["reason"])
    return ""
