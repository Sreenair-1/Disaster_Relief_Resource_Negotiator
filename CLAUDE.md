# AI assistant instructions

- Project summary: a five-agent flood-relief resource allocator (Medical,
  Shelter, Logistics, Mediator, Scenario) with scenario event replanning.
- Stack: Python 3.14.7, pinned Pydantic contracts, deterministic Python
  scoring, JSONL tracing, hosted LLM API, LangGraph orchestration, and Streamlit.
- Read `docs/CONTRACT.md` first.
- Ownership is exclusive: A (Hari) owns `lib/`, `data/scenario.json`,
  `samples/`, `app.py`, `ui/`, `graph/`, `agents/mediator.py`, `README.md`,
  `docs/SYNOPSIS.md`, `docs/DESIGN.md`, `docs/CONTRACT.md`, `requirements.txt`,
  `CLAUDE.md`, and tests for those. B owns `tools/*`,
  `agents/{medical,shelter,logistics,scenario}.py`, `agents/prompts/`,
  `data/scenarios/`, and tests for those. Nobody edits the other's files.
  The integration commit consolidates the documentation and dependency files.
- Add a package to `requirements.txt` only in the commit that first needs it.
- Update the consolidated `docs/DESIGN.md` and `README.md` in the same commit.
- Exactly one commit per prompt, using the message given by the user.
- Run `pytest` before committing.
- Never commit secrets. Never hand-edit traces.
