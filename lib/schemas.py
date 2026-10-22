from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Zone(BaseModel):
    name: str
    population: int = Field(ge=0)
    stranded: int = Field(default=0, ge=0)
    critical_patients: int = Field(default=0, ge=0)
    road_access: float = Field(default=1.0, ge=0, le=1)
    supply_dependent: int | None = Field(default=None, ge=0)


class Hospital(BaseModel):
    """Receiving-facility fields; None means the value is unverified."""

    name: str
    icu_beds_free: int | None = Field(default=None, ge=0)
    accepts_transfers: bool | None = None
    travel_hours: float | None = Field(default=None, gt=0)


class Operations(BaseModel):
    """Transport capacities and route times used by deterministic checks."""

    boat_capacity_persons: int | None = Field(default=None, gt=0)
    trip_hours_by_zone: dict[str, float] = Field(default_factory=dict)
    ambulance_capacity: int | None = Field(default=None, ge=0)
    hospitals: list[Hospital] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trip_times(self):
        if any(hours <= 0 for hours in self.trip_hours_by_zone.values()):
            raise ValueError("trip hours by zone must be positive")
        return self


class Scenario(BaseModel):
    disaster_type: str
    zones: list[Zone]
    resources: dict[str, int] = Field(default_factory=dict)
    hospitals: list[str] = Field(default_factory=list)
    roads: dict[str, Any] = Field(default_factory=dict)
    operations: Operations = Field(default_factory=Operations)

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
    agent: str
    resources: dict[str, int] = Field(default_factory=dict)
    people_affected: int = Field(ge=0)
    urgency: int = Field(ge=1, le=10)
    time_window_hrs: float = Field(gt=0)
    justification: str
    fallback_option: str
    uncertainty: float | None = Field(default=None, ge=0, le=1)


class Counter(BaseModel):
    to_agent: str
    question: str
    issued_round: int = Field(default=0, ge=0)
    claim_snapshot: Claim | None = None


class ClaimRevision(BaseModel):
    agent: str
    round: int = Field(ge=0)
    previous_claim: Claim | None = None
    current_claim: Claim
    changes: dict[str, Any] = Field(default_factory=dict)
    defended_unchanged: bool = False
    has_evidence: bool = False


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
    resource: str
    unmet_amount: int = Field(ge=0)
    agents: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    allocation: dict[str, dict[str, int]] = Field(default_factory=dict)
    justifications: dict[str, str] = Field(default_factory=dict)
    conflicts_resolved: list[str] = Field(default_factory=list)
    replans: int = 0
    rounds_used: int = 0
    disputes_log: list[str] = Field(default_factory=list)
    status: str = "resolved"
    unresolved_shortfalls: list[UnresolvedShortfall] = Field(default_factory=list)


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
