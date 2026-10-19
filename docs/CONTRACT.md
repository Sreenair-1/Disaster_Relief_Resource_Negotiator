# Shared contract

## Ownership

| Owner | Files |
|---|---|
| A (Hari) | `lib/`, `data/scenario.json`, `samples/`, `app.py`, `ui/`, `graph/`, `agents/mediator.py`, `README.md`, `docs/SYNOPSIS.md`, `docs/DESIGN.md`, `docs/CONTRACT.md`, `requirements.txt`, `CLAUDE.md`, and tests for these |
| B (teammate) | `tools/*`, `agents/{medical,shelter,logistics,scenario}.py`, `agents/prompts/`, `data/scenarios/`, `requirements-b.txt`, `docs/parts/b-*.md`, and tests for these |

Nobody edits the other owner's files.

## Interfaces

- `Zone`, `Scenario`, `Claim`, `Counter`, `Estimate`, `MediatorAction`,
  `Plan`, and `NegotiationState` are Pydantic models in `lib/schemas.py`.
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
- `TOOLS` is a name-to-callable registry. Failure injection is controlled by
  `set_failure(name | None)` and queried by `is_failing(name)`.
