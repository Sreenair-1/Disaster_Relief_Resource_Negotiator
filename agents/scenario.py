"""Scenario agent – dynamic environment event injector and scenario patcher."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lib.schemas import NegotiationState, Scenario, Zone
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "scenario.md"
_SYSTEM_PROMPT: str | None = None

PRESET_EVENTS: dict[str, str] = {
    "bridge_collapse": "Bridge to Zone A collapses, blocking all ground road access.",
    "hospital_capacity": "Regional Hospital reaches full capacity; ICU beds exhausted.",
    "water_rising_fast": "Water in Zone A rising faster than forecasted; flooding spreading.",
    "new_casualties": "Flash surge in Zone B: 15 additional critical casualties reported.",
    "resource_loss": "Warehouse flood damages supply depot: 1 boat lost and fuel contaminated.",
    "road_blocked": "Primary access road blocked by landslide.",
}


class ScenarioPatch(BaseModel):
    """Structured patch representation parsed from an environmental event."""

    is_valid: bool = Field(
        default=True,
        description="Whether the event is a plausible disaster event or nonsense/irrelevant.",
    )
    summary: str = Field(
        default="",
        description="Brief summary of event impact or explanation of why it was rejected.",
    )
    affected_agents: list[str] = Field(
        default_factory=list,
        description="List of agent domains affected: 'medical', 'shelter', 'logistics'.",
    )
    road_access: dict[str, float] = Field(
        default_factory=dict,
        description="Zone name or alias -> new road_access value (0.0 to 1.0).",
    )
    roads: dict[str, str] = Field(
        default_factory=dict,
        description="Zone or road name -> condition ('blocked', 'flooded', 'clear').",
    )
    zone_casualties: dict[str, int] = Field(
        default_factory=dict,
        description="Zone name -> updated critical patient count.",
    )
    zone_stranded: dict[str, int] = Field(
        default_factory=dict,
        description="Zone name -> updated stranded resident count.",
    )
    resource_loss: dict[str, int] = Field(
        default_factory=dict,
        description="Resource name -> count destroyed or deducted from inventory.",
    )
    hospital_capacity: dict[str, Any] = Field(
        default_factory=dict,
        description="Hospital updates (e.g. {'Regional Hospital': 'full'}).",
    )
    urgency_hints: dict[str, int] = Field(
        default_factory=dict,
        description="Agent name -> suggested urgency level (1-10).",
    )


def _system_prompt() -> str:
    """Load the system prompt from disk, cached after first read."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


def _match_zone(scenario: Scenario, target: str) -> Zone | None:
    """Fuzzy match a zone by name or common alias (e.g. 'Z1', 'Zone 1', 'Zone A')."""
    target_clean = target.strip().lower()

    # Exact case-insensitive match
    for z in scenario.zones:
        if z.name.strip().lower() == target_clean:
            return z

    # Alias matching: Z1 / Zone 1 -> first zone, Z2 / Zone 2 -> second zone
    if target_clean in ("z1", "zone 1", "1") and len(scenario.zones) >= 1:
        return scenario.zones[0]
    if target_clean in ("z2", "zone 2", "2") and len(scenario.zones) >= 2:
        return scenario.zones[1]

    # Substring match
    for z in scenario.zones:
        zn = z.name.strip().lower()
        if target_clean in zn or zn in target_clean:
            return z

    return None


def _apply_patch(scenario: Scenario, patch: ScenarioPatch) -> Scenario:
    """Apply validated patch to a deep COPY of the scenario."""
    new_scenario = scenario.model_copy(deep=True)

    # 1. Update road_access
    for target, access in patch.road_access.items():
        z = _match_zone(new_scenario, target)
        if z:
            z.road_access = max(0.0, min(1.0, float(access)))
            if z.road_access < 0.5 and z.name not in patch.roads:
                new_scenario.roads[z.name] = "blocked"

    # 2. Update roads dict
    for r_key, r_cond in patch.roads.items():
        z = _match_zone(new_scenario, r_key)
        key = z.name if z else r_key
        new_scenario.roads[key] = str(r_cond)

    # 3. Update zone casualties
    for target, count in patch.zone_casualties.items():
        z = _match_zone(new_scenario, target)
        if z:
            z.critical_patients = max(0, int(count))

    # 4. Update zone stranded
    for target, count in patch.zone_stranded.items():
        z = _match_zone(new_scenario, target)
        if z:
            z.stranded = max(0, int(count))

    # 5. Apply resource loss
    for res_name, loss_count in patch.resource_loss.items():
        matched_key = None
        for k in new_scenario.resources.keys():
            kl = k.lower()
            rl = res_name.lower()
            if kl == rl or kl == f"{rl}s" or f"{kl}s" == rl:
                matched_key = k
                break
        res_key = matched_key or res_name
        current = new_scenario.resources.get(res_key, 0)
        new_scenario.resources[res_key] = max(0, current - abs(int(loss_count)))

    return new_scenario


def _build_user_message(state: NegotiationState, text: str) -> str:
    """Compose user prompt for LLM evaluation of incoming scenario event."""
    zones_summary = [
        f"Zone '{z.name}': population={z.population}, stranded={z.stranded}, "
        f"critical_patients={z.critical_patients}, road_access={z.road_access}"
        for z in state.scenario.zones
    ]
    return (
        f"Disaster type: {state.scenario.disaster_type}\n"
        f"Resources: {state.scenario.resources}\n"
        f"Road conditions: {state.scenario.roads}\n"
        f"Hospitals: {state.scenario.hospitals}\n"
        f"Zones: {zones_summary}\n"
        f"Incoming event text: \"{text}\"\n\n"
        "Evaluate this event against the disaster context. If it is nonsense, humor, or unrelated, "
        "set is_valid=false. Otherwise, set is_valid=true and populate affected_agents and scenario mutations. "
        "Return valid JSON matching ScenarioPatch."
    )


def inject_event(
    state: NegotiationState,
    text: str,
    llm: Any,
    tracer: Tracer,
) -> NegotiationState:
    """Convert an event text into a structured patch applied to a copy of the scenario.

    Args:
        state: Current negotiation state.
        text: Raw event string from UI button or free-text entry.
        llm: LLM client (LLMClient or StubLLM).
        tracer: Tracer for JSONL logging.

    Returns:
        A new NegotiationState with updated scenario, events, and affected_agents.
    """
    cleaned = text.strip()
    if not cleaned:
        tracer.log("scenario", "mediator", "event_rejected", {"text": text, "reason": "Empty event description."})
        return state.model_copy(deep=True, update={"affected_agents": []})

    tracer.log("scenario", "llm", "event_eval_request", {"text": cleaned})

    system_msg = _system_prompt()
    user_msg = _build_user_message(state, cleaned)

    patch = llm.complete_json(system_msg, user_msg, ScenarioPatch)

    if not patch.is_valid:
        tracer.log(
            "scenario",
            "mediator",
            "event_rejected",
            {"text": cleaned, "reason": patch.summary},
        )
        return state.model_copy(deep=True, update={"affected_agents": []})

    # Apply patch to a deep COPY of the scenario
    new_scenario = _apply_patch(state.scenario, patch)

    # Set affected agents and append event to state
    new_state = state.model_copy(
        deep=True,
        update={
            "scenario": new_scenario,
            "events": [*state.events, cleaned],
            "affected_agents": patch.affected_agents,
        },
    )

    tracer.log(
        "scenario",
        "mediator",
        "event",
        {
            "text": cleaned,
            "patch": patch.model_dump(),
            "affected_agents": patch.affected_agents,
        },
    )
    return new_state
