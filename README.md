# Disaster Relief Resource Negotiator

Foundation for a five-agent flood-relief resource allocator: Medical, Shelter,
Logistics, Mediator, and Scenario. The project uses typed Pydantic contracts,
deterministic scoring, JSONL tracing, and an injectable hosted-LLM client.

The Streamlit control room is available; its runner remains a placeholder until
the negotiation graph is connected. Read [docs/CONTRACT.md](docs/CONTRACT.md)
before changing an interface.

## Development

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

The scenario editor accepts JSON matching `lib.schemas.Scenario`. Use Run to
open a trace session, inject a scenario event from the sidebar, and download
the current JSONL trace. The transcript and allocation panels will populate as
the graph runner is connected.

Run the project tests with:

```powershell
python -m pytest
```

Copy `.env.example` to `.env` and provide hosted-LLM credentials only through
environment variables when enabling the LLM client.
