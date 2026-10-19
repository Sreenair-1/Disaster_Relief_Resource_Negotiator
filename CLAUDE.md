# AI assistant instructions

- Project summary: a typed foundation for a five-agent flood-relief resource
  allocator (Medical, Shelter, Logistics, Mediator, Scenario).
- Stack: Python 3.14.7, Pydantic contracts, deterministic Python scoring,
  JSONL tracing, and a hosted LLM API; LangGraph and Streamlit are planned.
- Read `docs/CONTRACT.md` first.
- Ownership is exclusive: A (Hari) owns `lib/`, `data/scenario.json`,
  `samples/`, `app.py`, `ui/`, `graph/`, `agents/mediator.py`, `README.md`,
  `docs/SYNOPSIS.md`, `docs/DESIGN.md`, `docs/CONTRACT.md`, `requirements.txt`,
  `CLAUDE.md`, and tests for those. B owns `tools/*`,
  `agents/{medical,shelter,logistics,scenario}.py`, `agents/prompts/`,
  `data/scenarios/`, `requirements-b.txt`, `docs/parts/b-*.md`, and tests for
  those. Nobody edits the other's files.
- Add a package to `requirements.txt` only in the commit that first needs it.
- Update docs in the same commit: A updates `DESIGN.md`/`README.md`; B updates
  `docs/parts/b-<module>.md` and includes a Mermaid snippet.
- Exactly one commit per prompt, using the message given by the user.
- Run `pytest` before committing.
- Never commit secrets. Never hand-edit traces.
