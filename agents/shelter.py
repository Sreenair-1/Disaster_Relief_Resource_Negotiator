"""Shelter agent – advocates exclusively for evacuation, shelter capacity, and stranded people."""

from pathlib import Path
from typing import Any

from lib.schemas import Claim, Counter, Estimate, NegotiationState
from lib.toolcall import call_tool
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "shelter.md"
_SYSTEM_PROMPT: str | None = None

_WATER_RISING_PHRASES = (
    "water rising",
    "rising faster",
    "water is rising",
    "flood rising",
    "rapid rise",
    "rising water",
    "flood surge",
    "water level rising",
    "inundation",
)


def _system_prompt() -> str:
    """Load the system prompt from disk, cached after first read."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _is_water_rising_faster(state: NegotiationState, counter: Counter | None = None) -> bool:
    """Detect whether events or mediator counters signal that floodwaters are rising faster."""
    for event in state.events:
        ev_lower = event.lower()
        if any(phrase in ev_lower for phrase in _WATER_RISING_PHRASES):
            return True

    if counter:
        q_lower = counter.question.lower()
        if any(phrase in q_lower for phrase in _WATER_RISING_PHRASES):
            return True

    return False


def _gather_tool_data(
    state: NegotiationState, tracer: Tracer
) -> tuple[dict[str, Any], bool]:
    """Call shelter_capacity and zone_population, return merged data and tool_failed flag."""
    data: dict[str, Any] = {}
    tool_failed = False

    shelter_result = call_tool("shelter_capacity", state.scenario, tracer)
    if shelter_result.ok:
        data["shelter_capacity"] = shelter_result.data
    else:
        tool_failed = True
        data["shelter_capacity_error"] = shelter_result.error

    pop_result = call_tool("zone_population", state.scenario, tracer)
    if pop_result.ok:
        data["zone_population"] = pop_result.data
    else:
        tool_failed = True
        data["zone_population_error"] = pop_result.error

    return data, tool_failed


def _build_user_message(
    state: NegotiationState,
    tool_data: dict[str, Any],
    tool_failed: bool,
    counter: Counter | None = None,
    water_rising: bool = False,
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

    # Tool results
    if "shelter_capacity" in tool_data:
        parts.append(f"Shelter capacity data: {tool_data['shelter_capacity']}")
    if "shelter_capacity_error" in tool_data:
        parts.append(
            f"WARNING – shelter_capacity tool failed: {tool_data['shelter_capacity_error']}"
        )

    if "zone_population" in tool_data:
        parts.append(f"Demographics data: {tool_data['zone_population']}")
    if "zone_population_error" in tool_data:
        parts.append(
            f"WARNING – zone_population tool failed: {tool_data['zone_population_error']}"
        )

    if tool_failed:
        parts.append(
            "One or more tools failed. Set uncertainty between 0.3 and 0.8 and "
            "note the data gap in your justification."
        )

    # Event context
    if state.events:
        parts.append(f"Active scenario events: {'; '.join(state.events)}")

    # Water rising escalation alert
    if water_rising:
        parts.append(
            "CRITICAL ALERT: Floodwaters are rising faster! You MUST escalate your urgency "
            "(set urgency to 9 or 10, reduce time_window_hrs) to protect stranded people."
        )

    # Counter / challenge context
    if counter:
        parts.append(
            f"The mediator challenges you: \"{counter.question}\". "
            "Defend with evidence or revise if appropriate. Propose a phased evacuation "
            "as your fallback_option (e.g. 1 boat over 6 hours)."
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
        "Submit your Claim as JSON. Advocate assertively for evacuation, shelter, and boats."
    )
    return "\n".join(parts)


def make_claim(
    state: NegotiationState,
    llm: Any,
    tracer: Tracer,
    counter: Counter | None = None,
) -> Claim:
    """Generate a shelter/evacuation resource claim backed by tool data and LLM reasoning.

    Args:
        state: Current negotiation state.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.
        counter: Optional Counter from the mediator requesting revision.

    Returns:
        A validated Claim with agent="shelter".
    """
    tracer.log(
        "shelter",
        "tools",
        "gather_data",
        {
            "counter": counter.model_dump() if counter else None,
            "events": state.events,
        },
    )

    tool_data, tool_failed = _gather_tool_data(state, tracer)
    water_rising = _is_water_rising_faster(state, counter)

    user_msg = _build_user_message(state, tool_data, tool_failed, counter, water_rising)
    system_msg = _system_prompt()

    tracer.log(
        "shelter",
        "llm",
        "claim_request",
        {
            "has_counter": counter is not None,
            "tool_failed": tool_failed,
            "water_rising": water_rising,
        },
    )

    claim = llm.complete_json(system_msg, user_msg, Claim)

    # Enforce agent identity
    if claim.agent != "shelter":
        claim = claim.model_copy(update={"agent": "shelter"})

    # If tools failed and the LLM didn't set uncertainty, apply a default
    if tool_failed and claim.uncertainty is None:
        claim = claim.model_copy(update={"uncertainty": 0.5})

    # Escalate urgency if water is rising faster
    if water_rising and claim.urgency < 9:
        claim = claim.model_copy(update={"urgency": max(claim.urgency, 9)})

    # Propose phased evacuation under counter if not already specified
    if counter and not any(kw in claim.fallback_option.lower() for kw in ("phase", "staged", "waves")):
        claim = claim.model_copy(
            update={
                "fallback_option": (
                    f"Phased evacuation: {claim.fallback_option} (deploy primary resources in wave 1 over 6 hours, wave 2 thereafter)"
                    if claim.fallback_option
                    else "Phased evacuation: 1 boat deployed over 6 hours for wave 1, followed by wave 2."
                )
            }
        )

    tracer.log("shelter", "mediator", "claim", claim.model_dump())
    return claim


def estimate(
    state: NegotiationState,
    question: str,
    llm: Any,
    tracer: Tracer,
) -> Estimate:
    """Provide a shelter/evacuation estimate when asked by the mediator or a peer agent.

    Args:
        state: Current negotiation state.
        question: The question to answer.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.

    Returns:
        A validated Estimate with agent="shelter".
    """
    tracer.log("shelter", "llm", "estimate_request", {"question": question})

    tool_data, tool_failed = _gather_tool_data(state, tracer)

    system_msg = _system_prompt()
    user_parts = [
        f"A peer or the mediator asks: \"{question}\"",
        f"Disaster type: {state.scenario.disaster_type}",
        f"Available resources: {state.scenario.resources}",
    ]
    if "shelter_capacity" in tool_data:
        user_parts.append(f"Shelter data: {tool_data['shelter_capacity']}")
    if "zone_population" in tool_data:
        user_parts.append(f"Demographics data: {tool_data['zone_population']}")
    if tool_failed:
        user_parts.append("WARNING: some tool data unavailable.")
    user_parts.append(
        "Respond with JSON matching the Estimate schema: "
        '{"agent":"shelter","question":"...","value":<float>,'
        '"confidence":<0-1>,"rationale":"..."}'
    )

    result = llm.complete_json(system_msg, "\n".join(user_parts), Estimate)

    if result.agent != "shelter":
        result = result.model_copy(update={"agent": "shelter"})

    tracer.log("shelter", "mediator", "estimate", result.model_dump())
    return result
