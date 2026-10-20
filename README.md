# Disaster Relief Resource Negotiator

Foundation for a five-agent flood-relief resource allocator: Medical, Shelter,
Logistics, Mediator, and Scenario. The project uses typed Pydantic contracts,
deterministic scoring, JSONL tracing, and an injectable hosted-LLM client.

The Streamlit control room runs the LangGraph negotiation and can replan after
scenario events. It shows the agent transcript, allocation, conflicts, rounds,
and replan comparisons. Read [docs/CONTRACT.md](docs/CONTRACT.md) before
changing an interface.

## Development

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

The scenario editor accepts JSON matching `lib.schemas.Scenario`. Use Run to
start negotiation, then inject an event to update affected claims and compare
the new allocation with the first run. The sidebar can download the current
JSONL trace. Live runs need `OPENAI_API_KEY`; unset it to see the guided missing
key message. Development can call `ui.runner.run_negotiation(scenario, llm=StubLLM(...))`
to exercise the graph without hosted credentials.

Run the project tests with:

```powershell
python -m pytest
```

Copy `.env.example` to `.env` and provide hosted-LLM credentials only through
environment variables when enabling the LLM client.
