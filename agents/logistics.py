"""Logistics agent – advocates exclusively for vehicles, supply distribution, and route feasibility."""

from pathlib import Path
from typing import Any

from lib.schemas import Claim, Counter, Estimate, NegotiationState
from lib.toolcall import call_tool
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "logistics.md"
_SYSTEM_PROMPT: str | None = None


def _system_prompt() -> str:
    """Load the system prompt from disk, cached after first read."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _gather_tool_data(
    state: NegotiationState, tracer: Tracer
) -> tuple[dict[str, Any], bool]:
    """Call road_status, vehicle_inventory, and stock_levels; return merged data and tool_failed flag."""
    data: dict[str, Any] = {}
    tool_failed = False

    road_res = call_tool("road_status", state.scenario, tracer)
    if road_res.ok:
        data["road_status"] = road_res.data
    else:
        tool_failed = True
        data["road_status_error"] = road_res.error

    veh_res = call_tool("vehicle_inventory", state.scenario, tracer)
    if veh_res.ok:
        data["vehicle_inventory"] = veh_res.data
    else:
        tool_failed = True
        data["vehicle_inventory_error"] = veh_res.error

    stock_res = call_tool("stock_levels", state.scenario, tracer)
    if stock_res.ok:
        data["stock_levels"] = stock_res.data
    else:
        tool_failed = True
        data["stock_levels_error"] = stock_res.error

    return data, tool_failed


def _get_blocked_routes(state: NegotiationState, tool_data: dict[str, Any]) -> list[str]:
    """Identify zones or routes where access is cut or impassable."""
    blocked: list[str] = []
    if "road_status" in tool_data:
        blocked.extend(tool_data["road_status"].get("impassable_zones", []))

    for z in state.scenario.zones:
        r_cond = str(state.scenario.roads.get(z.name, "")).lower()
        if (
            z.road_access < 0.5
            or r_cond in ("blocked", "flooded", "closed", "impassable")
        ) and z.name not in blocked:
            blocked.append(z.name)

    return blocked


def _build_user_message(
    state: NegotiationState,
    tool_data: dict[str, Any],
    tool_failed: bool,
    counter: Counter | None = None,
    blocked_routes: list[str] | None = None,
) -> str:
    """Compose the user-turn prompt sent to the LLM."""
    parts: list[str] = []

    # Scenario summary
    parts.append(f"Disaster type: {state.scenario.disaster_type}")
    parts.append(f"Available resources: {state.scenario.resources}")
    parts.append(f"Round: {state.round}")

    # Zone summaries
    for z in state.scenario.zones:
        parts.append(
            f"Zone '{z.name}': population={z.population}, stranded={z.stranded}, "
            f"road_access={z.road_access}"
        )

    # Road status & Blocked routes
    if "road_status" in tool_data:
        parts.append(f"Road status telemetry: {tool_data['road_status']}")
    if "road_status_error" in tool_data:
        parts.append(f"WARNING – road_status tool failed: {tool_data['road_status_error']}")

    if blocked_routes:
        parts.append(
            f"CRITICAL ROUTE ALERT: Road access is cut in the following zones (road_access < 0.5): "
            f"{', '.join(blocked_routes)}. You MUST explicitly flag these cut routes in your "
            f"justification so the Mediator can adjust priority scores. Claim boats for flooded/cut routes, "
            f"and trucks where roads remain open."
        )

    # Vehicle inventory
    if "vehicle_inventory" in tool_data:
        parts.append(f"Vehicle inventory: {tool_data['vehicle_inventory']}")
    if "vehicle_inventory_error" in tool_data:
        parts.append(f"WARNING – vehicle_inventory tool failed: {tool_data['vehicle_inventory_error']}")

    # Stock levels (food, water, relief goods)
    if "stock_levels" in tool_data:
        parts.append(f"Stock levels (food, water, rations): {tool_data['stock_levels']}")
    if "stock_levels_error" in tool_data:
        parts.append(f"WARNING – stock_levels tool failed: {tool_data['stock_levels_error']}")

    if tool_failed:
        parts.append(
            "One or more tools failed. Set uncertainty between 0.3 and 0.8 and "
            "note the telemetry gap in your justification."
        )

    # Event context
    if state.events:
        parts.append(f"Active scenario events: {'; '.join(state.events)}")

    # Counter / challenge context
    if counter:
        parts.append(
            f"The mediator challenges you: \"{counter.question}\". "
            "Defend with evidence or revise your supply distribution plan."
        )

    # Existing claims in this round (for context)
    if state.claims:
        parts.append(f"Existing claims this round: {len(state.claims)}")
        for c in state.claims:
            parts.append(
                f"  {c.agent}: resources={c.resources}, urgency={c.urgency}, "
                f"people_affected={c.people_affected}"
            )

    parts.append(
        "Submit your Claim as JSON. Advocate assertively for vehicles, food/water distribution, "
        "and supply transit routes."
    )
    return "\n".join(parts)


def make_claim(
    state: NegotiationState,
    llm: Any,
    tracer: Tracer,
    counter: Counter | None = None,
) -> Claim:
    """Generate a logistics claim backed by road status, vehicle inventory, and stock telemetry.

    Args:
        state: Current negotiation state.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.
        counter: Optional Counter from the mediator requesting revision.

    Returns:
        A validated Claim with agent="logistics".
    """
    tracer.log(
        "logistics",
        "tools",
        "gather_data",
        {
            "counter": counter.model_dump() if counter else None,
            "events": state.events,
        },
    )

    tool_data, tool_failed = _gather_tool_data(state, tracer)
    blocked_routes = _get_blocked_routes(state, tool_data)

    user_msg = _build_user_message(state, tool_data, tool_failed, counter, blocked_routes)
    system_msg = _system_prompt()

    tracer.log(
        "logistics",
        "llm",
        "claim_request",
        {
            "has_counter": counter is not None,
            "tool_failed": tool_failed,
            "blocked_routes": blocked_routes,
        },
    )

    claim = llm.complete_json(system_msg, user_msg, Claim)

    # Enforce agent identity
    if claim.agent != "logistics":
        claim = claim.model_copy(update={"agent": "logistics"})

    # If tools failed and the LLM didn't set uncertainty, apply a default
    if tool_failed and claim.uncertainty is None:
        claim = claim.model_copy(update={"uncertainty": 0.5})

    # Ensure cut/blocked routes are explicitly noted in justification for Mediator scoring
    if blocked_routes and not any(r.lower() in claim.justification.lower() for r in blocked_routes):
        flag_note = f" Note for Mediator: road access is cut in {', '.join(blocked_routes)} (road_access < 0.5)."
        claim = claim.model_copy(update={"justification": claim.justification + flag_note})

    tracer.log("logistics", "mediator", "claim", claim.model_dump())
    return claim


def estimate(
    state: NegotiationState,
    question: str,
    llm: Any,
    tracer: Tracer,
) -> Estimate:
    """Provide a logistics estimate (e.g. route feasibility fallback when peer tools fail).

    Args:
        state: Current negotiation state.
        question: The question to answer (e.g. route feasibility, transport availability).
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.

    Returns:
        A validated Estimate with agent="logistics".
    """
    tracer.log("logistics", "llm", "estimate_request", {"question": question})

    tool_data, tool_failed = _gather_tool_data(state, tracer)
    blocked_routes = _get_blocked_routes(state, tool_data)

    system_msg = _system_prompt()
    user_parts = [
        f"A peer agent or the mediator asks: \"{question}\"",
        f"Disaster type: {state.scenario.disaster_type}",
        f"Available resources: {state.scenario.resources}",
    ]
    if "road_status" in tool_data:
        user_parts.append(f"Road status telemetry: {tool_data['road_status']}")
    if "vehicle_inventory" in tool_data:
        user_parts.append(f"Vehicle inventory: {tool_data['vehicle_inventory']}")
    if "stock_levels" in tool_data:
        user_parts.append(f"Stock levels: {tool_data['stock_levels']}")
    if blocked_routes:
        user_parts.append(f"Cut/impassable routes detected: {', '.join(blocked_routes)}")
    if tool_failed:
        user_parts.append("WARNING: Some logistics telemetry tools are degraded.")

    user_parts.append(
        "If this is a peer route feasibility inquiry, provide an objective route feasibility "
        "score (0.0 to 1.0) with confidence and rationale. "
        "Respond with JSON matching the Estimate schema: "
        '{"agent":"logistics","question":"...","value":<float>,'
        '"confidence":<0-1>,"rationale":"..."}'
    )

    result = llm.complete_json(system_msg, "\n".join(user_parts), Estimate)

    if result.agent != "logistics":
        result = result.model_copy(update={"agent": "logistics"})

    tracer.log("logistics", "mediator", "estimate", result.model_dump())
    return result
