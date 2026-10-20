from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Zone(BaseModel):
    name: str
    population: int = Field(ge=0)
    stranded: int = Field(default=0, ge=0)
    critical_patients: int = Field(default=0, ge=0)
    road_access: float = Field(default=1.0, ge=0, le=1)


class Scenario(BaseModel):
    disaster_type: str
    zones: list[Zone]
    resources: dict[str, int] = Field(default_factory=dict)
    hospitals: list[str] = Field(default_factory=list)
    roads: dict[str, Any] = Field(default_factory=dict)


class ScenarioDiff(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


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


class Plan(BaseModel):
    allocation: dict[str, dict[str, int]] = Field(default_factory=dict)
    justifications: dict[str, str] = Field(default_factory=dict)
    conflicts_resolved: list[str] = Field(default_factory=list)
    replans: int = 0
    rounds_used: int = 0
    disputes_log: list[str] = Field(default_factory=list)


class NegotiationState(BaseModel):
    scenario: Scenario
    round: int = 0
    claims: list[Claim] = Field(default_factory=list)
    counters: list[Counter] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    replans: int = 0
    affected_agents: list[str] = Field(default_factory=list)
