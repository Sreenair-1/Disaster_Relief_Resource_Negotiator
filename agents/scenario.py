"""Scenario agent – dynamic environment event injector and scenario patcher."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from graph.replan import derive_affected_agents
from lib.schemas import NegotiationState, Scenario, Zone
from lib.tracing import Tracer

_PROMPT_PATH = Path(__file__).parent / "prompts" / "scenario.md"
_SYSTEM_PROMPT: str | None = None

PRESET_EVENTS: dict[str, str] = {
    "bridge_collapse": "Bridge to Zone A collapses, cutting all ground road access to Zone A.",
    "new_casualties": "Eight new critical patients arrive in Zone B.",
    "water_rising_fast": "Water in Zone A rises sharply, doubling the number of stranded residents.",
    "hospital_capacity": "Regional Hospital in Zone A reaches full capacity; 10 critical patients require diversion.",
    "resource_loss": "Warehouse flood in Zone A damages supply depot: 1 rescue boat destroyed.",
    "road_blocked": "Landslide in Zone B cuts all road access to Zone B to 0.0.",
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
        target_clean = target.strip().lower()
        if target_clean in ("all", "*", "every zone", "all zones", "roads", "all roads"):
            for z in new_scenario.zones:
                z.road_access = max(0.0, min(1.0, float(access)))
                if z.road_access < 0.5 and z.name not in patch.roads:
                    new_scenario.roads[z.name] = "blocked"
        else:
            z = _match_zone(new_scenario, target)
            if z:
                z.road_access = max(0.0, min(1.0, float(access)))
                if z.road_access < 0.5 and z.name not in patch.roads:
                    new_scenario.roads[z.name] = "blocked"

    # 2. Update roads dict
    for r_key, r_cond in patch.roads.items():
        r_clean = r_key.strip().lower()
        if r_clean in ("all", "*", "every zone", "all zones", "roads", "all roads"):
            for z in new_scenario.zones:
                new_scenario.roads[z.name] = str(r_cond)
        else:
            z = _match_zone(new_scenario, r_key)
            key = z.name if z else r_key
            new_scenario.roads[key] = str(r_cond)

    # 3. Update zone casualties
    for target, count in patch.zone_casualties.items():
        target_clean = target.strip().lower()
        if target_clean in ("all", "*", "every zone", "all zones"):
            for z in new_scenario.zones:
                z.critical_patients = max(0, int(count))
        else:
            z = _match_zone(new_scenario, target)
            if z:
                z.critical_patients = max(0, int(count))

    # 4. Update zone stranded
    for target, count in patch.zone_stranded.items():
        target_clean = target.strip().lower()
        if target_clean in ("all", "*", "every zone", "all zones"):
            for z in new_scenario.zones:
                z.stranded = max(0, int(count))
        else:
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

    # 6. Apply hospital capacity updates
    if patch.hospital_capacity:
        for hosp, status in patch.hospital_capacity.items():
            h_str = str(status).lower()
            if h_str in ("closed", "offline", "destroyed") and hosp in new_scenario.hospitals:
                new_scenario.hospitals.remove(hosp)

    return new_scenario


def diff_scenarios(before: Scenario, after: Scenario) -> dict[str, Any]:
    """Compute structured before/after diff of changed scenario fields."""
    diff: dict[str, Any] = {}

    # 1. Road conditions
    if before.roads != after.roads:
        road_diff: dict[str, Any] = {}
        all_road_keys = set(before.roads.keys()) | set(after.roads.keys())
        for rk in all_road_keys:
            b_val = before.roads.get(rk)
            a_val = after.roads.get(rk)
            if b_val != a_val:
                road_diff[rk] = {"before": b_val, "after": a_val}
        if road_diff:
            diff["roads"] = road_diff

    # 2. Resources
    if before.resources != after.resources:
        res_diff: dict[str, Any] = {}
        all_res_keys = set(before.resources.keys()) | set(after.resources.keys())
        for rk in all_res_keys:
            b_val = before.resources.get(rk)
            a_val = after.resources.get(rk)
            if b_val != a_val:
                res_diff[rk] = {"before": b_val, "after": a_val}
        if res_diff:
            diff["resources"] = res_diff

    # 3. Hospitals
    if before.hospitals != after.hospitals:
        diff["hospitals"] = {"before": list(before.hospitals), "after": list(after.hospitals)}

    # 4. Disaster type
    if before.disaster_type != after.disaster_type:
        diff["disaster_type"] = {"before": before.disaster_type, "after": after.disaster_type}

    # 5. Zones
    before_zones = {z.name: z for z in before.zones}
    zone_diffs: dict[str, Any] = {}
    for az in after.zones:
        bz = before_zones.get(az.name)
        if not bz:
            zone_diffs[az.name] = {"added": az.model_dump()}
            continue
        z_changes: dict[str, Any] = {}
        if bz.road_access != az.road_access:
            z_changes["road_access"] = {"before": bz.road_access, "after": az.road_access}
        if bz.critical_patients != az.critical_patients:
            z_changes["critical_patients"] = {"before": bz.critical_patients, "after": az.critical_patients}
        if bz.stranded != az.stranded:
            z_changes["stranded"] = {"before": bz.stranded, "after": az.stranded}
        if bz.population != az.population:
            z_changes["population"] = {"before": bz.population, "after": az.population}
        if z_changes:
            zone_diffs[az.name] = z_changes

    for bname, bz in before_zones.items():
        if not any(az.name == bname for az in after.zones):
            zone_diffs[bname] = {"removed": bz.model_dump()}

    if zone_diffs:
        diff["zones"] = zone_diffs

    return diff


def _build_user_message(state: NegotiationState, text: str) -> str:
    """Compose user prompt for LLM evaluation of incoming scenario event."""
    zones_summary = [
        f"Zone '{z.name}': population={z.population}, stranded={z.stranded}, "
        f"critical_patients={z.critical_patients}, road_access={z.road_access}"
        for z in state.scenario.zones
    ]
    zone_names = [z.name for z in state.scenario.zones]
    return (
        f"Disaster type: {state.scenario.disaster_type}\n"
        f"Resources: {state.scenario.resources}\n"
        f"Road conditions: {state.scenario.roads}\n"
        f"Hospitals: {state.scenario.hospitals}\n"
        f"Zones: {zones_summary}\n"
        f"Available zone names: {zone_names}\n"
        f"Incoming event text: \"{text}\"\n\n"
        "Evaluate this event against the CURRENT disaster context. If it is nonsense, humor, or unrelated, "
        "set is_valid=false and explain why in summary. Otherwise, set is_valid=true and populate affected_agents "
        "and scenario mutations.\n\n"
        "CRITICAL RULES:\n"
        "1. Resolve the event against the current scenario context. Output concrete numeric deltas, "
        "never empty maps for a physical event.\n"
        "2. If the text names no zone, apply it to every zone it plausibly affects (e.g. all available zones) "
        "and state this in the summary.\n"
        "3. Provide concrete mutations: road_access (0.0 to 1.0), zone_casualties, zone_stranded, "
        "resource_loss, or roads.\n"
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
        tracer.log(
            "scenario",
            "mediator",
            "event_rejected",
            {"text": text, "reason": "Empty event description."},
        )
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
    diff = diff_scenarios(state.scenario, new_scenario)

    # If nothing changed, retry once with an instruction to be specific
    if not diff:
        tracer.log(
            "scenario",
            "llm",
            "event_retry_request",
            {
                "text": cleaned,
                "reason": "Previous patch produced no scenario changes (empty diff).",
            },
        )
        retry_msg = (
            f"{user_msg}\n\n"
            "CRITICAL RETRY – BE SPECIFIC:\n"
            "Your previous evaluation produced no concrete state changes to the scenario (diff was empty). "
            "You MUST be specific and output concrete numeric deltas (e.g. road_access, zone_casualties, "
            "zone_stranded, roads, or resource_loss). If the event names no specific zone, apply it to every "
            "zone it plausibly affects. If this event genuinely cannot affect scenario state, set is_valid=false."
        )
        patch = llm.complete_json(system_msg, retry_msg, ScenarioPatch)

        if not patch.is_valid:
            reason = patch.summary if patch.summary else "event had no effect on the scenario"
            tracer.log(
                "scenario",
                "mediator",
                "event_rejected",
                {"text": cleaned, "reason": reason},
            )
            return state.model_copy(deep=True, update={"affected_agents": []})

        new_scenario = _apply_patch(state.scenario, patch)
        diff = diff_scenarios(state.scenario, new_scenario)

        if not diff:
            # Still unchanged: reject and never report no-op as successful replan
            patch.is_valid = False
            patch.summary = "event had no effect on the scenario"
            tracer.log(
                "scenario",
                "mediator",
                "event_rejected",
                {"text": cleaned, "reason": "event had no effect on the scenario"},
            )
            return state.model_copy(deep=True, update={"affected_agents": []})

    # Derive affected_agents from fields that actually changed
    affected_agents = derive_affected_agents(
        state.scenario,
        new_scenario,
        state.claims,
        patch.affected_agents,
    )

    # Trace event_applied with the before/after diff of changed fields
    tracer.log(
        "scenario",
        "mediator",
        "event_applied",
        {
            "text": cleaned,
            "diff": diff,
            "affected_agents": affected_agents,
        },
    )

    tracer.log(
        "scenario",
        "mediator",
        "event",
        {
            "text": cleaned,
            "patch": patch.model_dump(),
            "affected_agents": affected_agents,
            "diff": diff,
        },
    )

    return state.model_copy(
        deep=True,
        update={
            "scenario": new_scenario,
            "events": [*state.events, cleaned],
            "affected_agents": affected_agents,
        },
    )
