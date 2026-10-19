# Disaster Relief Resource Negotiator

Foundation for a five-agent flood-relief resource allocator: Medical, Shelter,
Logistics, Mediator, and Scenario. The project uses typed Pydantic contracts,
deterministic scoring, JSONL tracing, and an injectable hosted-LLM client.

The Streamlit and LangGraph orchestration surfaces are planned. Read
[docs/CONTRACT.md](docs/CONTRACT.md) before changing an interface.

## Development

```powershell
python -m pytest
```

Copy `.env.example` to `.env` and provide hosted-LLM credentials only through
environment variables when enabling the LLM client.
