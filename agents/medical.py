"""Medical agent – advocates exclusively for medical needs."""

from pathlib import Path
from typing import Any

from agents._common import gather_tool_telemetry, handle_tool_failure
from lib.llm import StubLLM
from lib.schemas import Claim, Counter, Estimate, NegotiationState
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "medical.md"
_SYSTEM_PROMPT: str | None = None


def _system_prompt() -> str:
    """Load the system prompt from disk, cached after first read."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _gather_tool_data(
    state: NegotiationState, tracer: Tracer
) -> tuple[dict[str, Any], bool, list[str]]:
    """Call hospital_capacity and casualty_estimate via shared telemetry gatherer."""
    return gather_tool_telemetry(
        state.scenario, ["hospital_capacity", "casualty_estimate"], tracer
    )


def _build_user_message(
    state: NegotiationState,
    tool_data: dict[str, Any],
    tool_failed: bool,
    counter: Counter | None = None,
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
            f"critical_patients={z.critical_patients}, road_access={z.road_access}"
        )

    # Tool results
    if "hospital_capacity" in tool_data:
        parts.append(f"Hospital data: {tool_data['hospital_capacity']}")
    if "hospital_capacity_error" in tool_data:
        parts.append(
            f"WARNING – hospital_capacity tool failed: {tool_data['hospital_capacity_error']}"
        )

    if "casualty_estimate" in tool_data:
        parts.append(f"Casualty data: {tool_data['casualty_estimate']}")
    if "casualty_estimate_error" in tool_data:
        parts.append(
            f"WARNING – casualty_estimate tool failed: {tool_data['casualty_estimate_error']}"
        )

    if tool_failed:
        parts.append(
            "One or more tools failed. Set uncertainty between 0.3 and 0.8 and "
            "note the data gap in your justification."
        )

    # Counter / challenge context
    if counter:
        parts.append(
            f"The mediator challenges you: \"{counter.question}\". "
            "Defend with evidence or revise if the evidence supports it."
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
        "Submit your Claim as JSON. Advocate assertively for medical needs."
    )
    return "\n".join(parts)


def make_claim(
    state: NegotiationState,
    llm: Any,
    tracer: Tracer,
    counter: Counter | None = None,
) -> Claim:
    """Generate a medical resource claim backed by tool data and LLM reasoning.

    Args:
        state: Current negotiation state.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.
        counter: Optional Counter from the mediator requesting revision.

    Returns:
        A validated Claim with agent="medical".
    """
    tracer.log("medical", "tools", "gather_data", {"counter": counter.model_dump() if counter else None})

    tool_data, tool_failed, failed_tools = _gather_tool_data(state, tracer)

    user_msg = _build_user_message(state, tool_data, tool_failed, counter)
    system_msg = _system_prompt()

    tracer.log("medical", "llm", "claim_request", {"has_counter": counter is not None, "tool_failed": tool_failed})

    claim = llm.complete_json(system_msg, user_msg, Claim)

    # Enforce agent identity – the LLM must not claim as another agent
    if claim.agent != "medical":
        claim = claim.model_copy(update={"agent": "medical"})

    # Handle tool failure: state uncertainty explicitly and offer fallback_estimate
    claim = handle_tool_failure(claim, "medical", state.scenario, tool_failed, failed_tools)

    tracer.log("medical", "mediator", "claim", claim.model_dump())
    return claim


def estimate(
    state: NegotiationState,
    question: str,
    llm: Any,
    tracer: Tracer,
) -> Estimate:
    """Provide a medical estimate when asked by the mediator or a peer agent.

    Args:
        state: Current negotiation state.
        question: The question to answer.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.

    Returns:
        A validated Estimate with agent="medical".
    """
    tracer.log("medical", "llm", "estimate_request", {"question": question})

    # Gather fresh tool data for the estimate
    tool_data, tool_failed, _ = _gather_tool_data(state, tracer)

    system_msg = _system_prompt()
    user_parts = [
        f"A peer or the mediator asks: \"{question}\"",
        f"Disaster type: {state.scenario.disaster_type}",
        f"Available resources: {state.scenario.resources}",
    ]
    if "casualty_estimate" in tool_data:
        user_parts.append(f"Casualty data: {tool_data['casualty_estimate']}")
    if "hospital_capacity" in tool_data:
        user_parts.append(f"Hospital data: {tool_data['hospital_capacity']}")
    if tool_failed:
        user_parts.append("WARNING: some tool data unavailable.")
    user_parts.append(
        "Respond with JSON matching the Estimate schema: "
        '{"agent":"medical","question":"...","value":<float>,'
        '"confidence":<0-1>,"rationale":"..."}'
    )

    result = llm.complete_json(system_msg, "\n".join(user_parts), Estimate)

    if result.agent != "medical":
        result = result.model_copy(update={"agent": "medical"})

    tracer.log("medical", "mediator", "estimate", result.model_dump())
    return result
