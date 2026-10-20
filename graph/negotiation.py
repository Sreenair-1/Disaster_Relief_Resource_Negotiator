"""Bounded multi-agent negotiation graph."""

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agents.logistics import estimate as logistics_estimate, make_claim as logistics_claim
from agents.medical import estimate as medical_estimate, make_claim as medical_claim
from agents.mediator import decide_next, finalize as mediator_finalize
from agents.shelter import estimate as shelter_estimate, make_claim as shelter_claim
from lib.schemas import Counter, MediatorAction, NegotiationState

CLAIMERS = {"medical": medical_claim, "shelter": shelter_claim, "logistics": logistics_claim}
ESTIMATORS = {"medical": medical_estimate, "shelter": shelter_estimate, "logistics": logistics_estimate}


class _GraphState(TypedDict):
    state: NegotiationState
    llm: Any
    tracer: Any
    action: MediatorAction | None
    next_agent: str | None


def _counter_for(state: NegotiationState, agent: str):
    return next((counter for counter in reversed(state.counters) if counter.to_agent == agent), None)


def _collect_claims(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    state.claims = []
    for agent, make_claim in CLAIMERS.items():
        state.claims.append(make_claim(state, ctx["llm"], ctx["tracer"]))
    state.round = 1
    return {**ctx, "state": state}


def _mediate(ctx: _GraphState) -> _GraphState:
    state = ctx["state"]
    action = decide_next(state, ctx["llm"], ctx["tracer"])
    uncertain = [claim for claim in state.claims if claim.uncertainty is None or claim.uncertainty > 0]
    if action == MediatorAction.accept_revision and uncertain and state.round < 3:
        action = MediatorAction.ask_peer_estimate
        target = max(uncertain, key=lambda claim: claim.uncertainty or 1).agent
        state.counters.append(Counter(to_agent=target, question="Please provide an independent estimate to address claim uncertainty."))
    target = state.counters[-1].to_agent if state.counters else None
    return {**ctx, "action": action, "next_agent": target}


def _challenge(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    agent = ctx["next_agent"]
    if agent in CLAIMERS:
        index = next((i for i, claim in enumerate(state.claims) if claim.agent == agent), None)
        revision = CLAIMERS[agent](state, ctx["llm"], ctx["tracer"], _counter_for(state, agent))
        if index is None:
            state.claims.append(revision)
        else:
            state.claims[index] = revision
    state.round += 1
    return {**ctx, "state": state, "action": None}


def _fallback(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    counter = state.counters[-1] if state.counters else None
    target = ctx["next_agent"]
    if target in ESTIMATORS and counter:
        estimate = ESTIMATORS[target](state, counter.question, ctx["llm"], ctx["tracer"])
        ctx["tracer"].log("mediator", target, "fallback_estimate", estimate.model_dump())
    state.round += 1
    return {**ctx, "state": state, "action": None}


def _finish(ctx: _GraphState) -> _GraphState:
    state = ctx["state"].model_copy(deep=True)
    state.plan = mediator_finalize(state, ctx["llm"], ctx["tracer"])
    return {**ctx, "state": state}


def _route(ctx: _GraphState) -> str:
    if ctx["state"].round >= 3 or ctx["action"] == MediatorAction.finalize:
        return "finalize"
    if ctx["action"] == MediatorAction.challenge_agent:
        return "challenge"
    if ctx["action"] in (MediatorAction.request_fallback, MediatorAction.ask_peer_estimate):
        return "fallback"
    return "finalize"


def _build_graph():
    graph = StateGraph(_GraphState)
    graph.add_node("collect_claims", _collect_claims)
    graph.add_node("mediator_decide", _mediate)
    graph.add_node("challenge", _challenge)
    graph.add_node("fallback", _fallback)
    graph.add_node("finalize", _finish)
    graph.add_edge(START, "collect_claims")
    graph.add_edge("collect_claims", "mediator_decide")
    graph.add_conditional_edges("mediator_decide", _route, {
        "challenge": "challenge", "fallback": "fallback", "finalize": "finalize",
    })
    graph.add_edge("challenge", "mediator_decide")
    graph.add_edge("fallback", "mediator_decide")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_negotiation(scenario, llm, tracer) -> NegotiationState:
    """Run claims, bounded mediator decisions, and final allocation."""
    return _build_graph().invoke({
        "state": NegotiationState(scenario=scenario), "llm": llm, "tracer": tracer,
        "action": None, "next_agent": None,
    })["state"]
