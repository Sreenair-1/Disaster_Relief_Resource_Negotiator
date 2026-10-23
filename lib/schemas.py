from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Zone(BaseModel):
    name: str = Field(description="Zone name used by roads and per-zone operations data.")
    population: int = Field(ge=0, description="Total people in the zone.")
    stranded: int = Field(default=0, ge=0, description="Residents needing evacuation, including any critical patients counted here.")
    critical_patients: int = Field(default=0, ge=0, description="People needing urgent medical support.")
    road_access: float = Field(default=1.0, ge=0, le=1, description="Ground-road access factor from 0 (none) to 1 (full).")
    supply_dependent: int | None = Field(default=None, ge=0, description="People assigned to the supply-demand group; null infers the remaining population.")


class Hospital(BaseModel):
    """Receiving-facility fields; None means the value is unverified."""

    name: str = Field(description="Receiving facility name.")
    icu_beds_free: int | None = Field(default=None, ge=0, description="Verified free ICU beds, or null when unknown.")
    accepts_transfers: bool | None = Field(default=None, description="Whether the facility accepts transfers, or null when unknown.")
    travel_hours: float | None = Field(default=None, gt=0, description="Travel time to the facility in hours, or null when unknown.")


class Operations(BaseModel):
    """Transport capacities and route times used by deterministic checks."""

    boat_capacity_persons: int | None = Field(default=None, gt=0, description="People carried per boat per trip, or unknown.")
    trip_hours_by_zone: dict[str, float] = Field(default_factory=dict, description="Boat round-trip hours keyed by zone name.")
    ambulance_capacity: int | None = Field(default=None, ge=0, description="Legacy ambulance count/capacity input; null when unknown.")
    ambulance_availability: int | None = Field(default=None, ge=0, description="Available ambulances, or null when unknown.")
    truck_capacity_persons: int | None = Field(default=None, gt=0, description="People carried per truck trip, or unknown.")
    food_packs_per_person_day: float | None = Field(default=None, gt=0, description="Configured food packs per person per day.")
    water_liters_per_person_day: float | None = Field(default=None, gt=0, description="Configured water litres per person per day.")
    supply_horizon_days: float | None = Field(default=None, gt=0, description="Supply planning horizon in days.")
    truck_payload_packs: int | None = Field(default=None, gt=0, description="Food packs carried per truck trip.")
    truck_payload_liters: int | None = Field(default=None, gt=0, description="Water litres carried per truck trip.")
    boat_payload_packs: int | None = Field(default=None, gt=0, description="Food packs carried per boat trip.")
    boat_payload_liters: int | None = Field(default=None, gt=0, description="Water litres carried per boat trip.")
    truck_trip_hours_by_zone: dict[str, float] = Field(default_factory=dict, description="Truck round-trip hours keyed by zone; falls back to boat trip times where absent.")
    hospitals: list[Hospital] = Field(default_factory=list, description="Receiving facilities and their explicitly known capacities.")

    @model_validator(mode="after")
    def validate_trip_times(self):
        if any(hours <= 0 for hours in (*self.trip_hours_by_zone.values(), *self.truck_trip_hours_by_zone.values())):
            raise ValueError("trip hours by zone must be positive")
        return self


class Scenario(BaseModel):
    disaster_type: str = Field(description="Disaster category, such as flood.")
    zones: list[Zone] = Field(description="Affected zones and their population/access inputs.")
    resources: dict[str, int] = Field(default_factory=dict, description="Available resource inventory by resource name.")
    hospitals: list[str] = Field(default_factory=list, description="Receiving facility names; legacy input normalized with operations data.")
    roads: dict[str, Any] = Field(default_factory=dict, description="Road status keyed by zone name.")
    operations: Operations = Field(default_factory=Operations, description="Optional transport, facility, and supply assumptions.")

    @model_validator(mode="before")
    @classmethod
    def normalize_hospitals(cls, value):
        """Accept legacy name lists and normalize facility objects."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        operations = dict(data.get("operations") or {})
        operation_hospitals = operations.get("hospitals")
        legacy_hospitals = data.get("hospitals")

        if legacy_hospitals is not None:
            if legacy_hospitals and isinstance(legacy_hospitals[0], dict):
                operation_hospitals = legacy_hospitals
                data["hospitals"] = [item.get("name", "") for item in legacy_hospitals]
            else:
                operation_hospitals = operation_hospitals or [
                    {"name": name} for name in legacy_hospitals
                ]
        elif operation_hospitals:
            data["hospitals"] = [
                item if isinstance(item, str) else item.get("name", "")
                for item in operation_hospitals
            ]

        operations["hospitals"] = operation_hospitals or []
        data["operations"] = operations
        data.setdefault("hospitals", [])
        return data


class Claim(BaseModel):
    agent: str = Field(description="Name of the specialist making this claim.")
    resources: dict[str, int] = Field(default_factory=dict, description="Requested quantities by resource name.")
    people_affected: int = Field(ge=0, description="People represented by this claim.")
    urgency: int = Field(ge=1, le=10, description="Urgency rating from 1 to 10.")
    time_window_hrs: float = Field(gt=0, description="Response window in hours.")
    justification: str = Field(description="Evidence and reason for the requested resources.")
    fallback_option: str = Field(description="Alternative action if the request cannot be met.")
    uncertainty: float | None = Field(default=None, ge=0, le=1, description="Uncertainty from 0 to 1, or null when not stated.")
    unverified_support: bool = Field(default=False, description="Whether required support data is unverified or insufficient.")
    contradicts_data: bool = Field(default=False, description="Whether claim facts contradicted known scenario data.")


class Counter(BaseModel):
    to_agent: str
    question: str
    issued_round: int = Field(default=0, ge=0)
    claim_snapshot: Claim | None = None
    counter_id: int = Field(default=0, ge=0)


class ClaimRevision(BaseModel):
    agent: str
    round: int = Field(ge=0)
    previous_claim: Claim | None = None
    current_claim: Claim
    changes: dict[str, Any] = Field(default_factory=dict)
    defended_unchanged: bool = False
    has_evidence: bool = False
    meaningful_revision: bool = False
    counter_id: int | None = None


class Estimate(BaseModel):
    agent: str
    question: str
    value: float
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class MediatorAction(str, Enum):
    challenge_agent = "challenge_agent"
    request_fallback = "request_fallback"
    ask_peer_estimate = "ask_peer_estimate"
    accept_revision = "accept_revision"
    finalize = "finalize"


class UnresolvedShortfall(BaseModel):
    resource: str = Field(description="Resource with a remaining unmet request.")
    unmet_amount: int = Field(ge=0, description="Quantity that remains unmet.")
    agents: list[str] = Field(default_factory=list, description="Claimants contributing to the shortfall.")


class Plan(BaseModel):
    allocation: dict[str, dict[str, int]] = Field(default_factory=dict, description="Awarded resource quantities keyed by agent then resource.")
    justifications: dict[str, str] = Field(default_factory=dict, description="Mediator explanation keyed by agent.")
    conflicts_resolved: list[str] = Field(default_factory=list, description="Previously oversubscribed resources resolved by validated claim changes.")
    replans: int = Field(default=0, description="Number of accepted event-driven replans.")
    rounds_used: int = Field(default=0, description="Completed negotiation challenge cycles used.")
    disputes_log: list[str] = Field(default_factory=list, description="Claim reductions and Mediator stock caps recorded once per claimant.")
    status: str = Field(default="resolved", description="Plan status, such as resolved or constrained.")
    unresolved_shortfalls: list[UnresolvedShortfall] = Field(default_factory=list, description="Resource requests still unmet after allocation.")
    risks: list[str] = Field(default_factory=list, description="Unverified support and delivery risks.")
    feasibility_report: dict[str, Any] = Field(default_factory=dict, description="Computed allocation checks, demand totals, assumptions, and shortfalls.")
    reassessments: list[dict[str, Any]] = Field(default_factory=list, description="Before/after claim and award summaries for affected agents.")
    non_inventory_requests: list[dict[str, Any]] = Field(default_factory=list, description="Requested resources absent from scenario inventory.")
    allocation_by_zone: dict[str, dict[str, int]] = Field(default_factory=dict, description="Awarded resource quantities keyed by zone then resource.")
    coverage: dict[str, Any] = Field(default_factory=dict, description="Population demand and service coverage by zone.")
    life_safety_gap: bool = Field(default=False, description="Whether any critical patients remain without modeled transport coverage.")
    unassigned_resources: dict[str, int] = Field(default_factory=dict, description="Inventory remaining after allocation.")
    reasoning_source: str = Field(default="llm", description="Whether plan justifications came from the LLM or deterministic fallback.")
    supply_coverage: dict[str, Any] = Field(default_factory=dict, description="Configured food and water need and deliverable amount by zone.")
    undeliverable_stock: dict[str, int] = Field(default_factory=dict, description="Remaining food/water stock exceeding modeled delivery capacity.")


class ScenarioDiff(BaseModel):
    """Before/after diff of scenario fields changed by an event patch."""

    changed_fields: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="field_name -> {'before': ..., 'after': ...} for each changed field.",
    )

    @property
    def is_empty(self) -> bool:
        return len(self.changed_fields) == 0


class NegotiationState(BaseModel):
    scenario: Scenario
    round: int = 0
    claims: list[Claim] = Field(default_factory=list)
    counters: list[Counter] = Field(default_factory=list)
    estimates: list[Estimate] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    replans: int = 0
    affected_agents: list[str] = Field(default_factory=list)
    claim_revisions: list[ClaimRevision] = Field(default_factory=list)
    non_inventory_requests: list[dict[str, Any]] = Field(default_factory=list)
    round_challenged_agents: list[str] = Field(default_factory=list)
    round_responded_agents: list[str] = Field(default_factory=list)
