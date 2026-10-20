# Shared contract

## Project areas

| Area | Files |
|---|---|
| Core contracts and orchestration | `lib/`, `graph/`, `agents/mediator.py`, `app.py`, `ui/`, `data/scenario.json`, `samples/`, and their tests |
| Specialist agents and tools | `agents/{medical,shelter,logistics,scenario}.py`, `agents/_common.py`, `agents/prompts/`, `tools/`, `data/scenarios/`, and their tests |
| Integrated documentation and environment | `README.md`, `docs/`, `requirements.txt`, `CLAUDE.md` |

The parallel ownership split is complete. The integrated project uses one
consolidated requirements file and the documentation in `README.md`,
`docs/SYNOPSIS.md`, and `docs/DESIGN.md`.

## Interfaces

- `Zone`, `Scenario`, `Claim`, `Counter`, `Estimate`, `MediatorAction`,
  `Plan`, and `NegotiationState` are Pydantic models in `lib/schemas.py`;
  `ScenarioPatch` is defined in `agents/scenario.py`.
- `score_claim(claim, scenario) -> float` is deterministic; `over_subscribed`
  returns resource deficits.
- `Tracer.log(sender, receiver, type, payload) -> dict` stores memory and JSONL.
- `LLMClient.complete_json(system, user, schema) -> schema` uses environment
  credentials, retries, and repairs malformed JSON. `StubLLM` queues responses.
- `call_tool(name, scenario, tracer, **kw) -> ToolResult` logs calls and catches
  `ToolError`.
- Agent signatures:
  `make_claim(state, llm, tracer, counter=None) -> Claim`,
  `estimate(state, question, llm, tracer) -> Estimate`,
  `inject_event(state, text, llm, tracer) -> NegotiationState`,
  `decide_next(state, llm, tracer) -> MediatorAction`, and
  `finalize(state, llm, tracer) -> Plan`.
- Graph entry points are
  `run_negotiation(scenario, llm, tracer) -> NegotiationState` and
  `replan(state, event_text, llm, tracer) -> NegotiationState`.
- `TOOLS` is a name-to-callable registry. Failure injection is controlled by
  `set_failure(name | None)` and queried by `is_failing(name)`.
