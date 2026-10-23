from pathlib import Path

from streamlit.testing.v1 import AppTest

from lib.llm import StubLLM
from lib.schemas import Claim, MediatorAction, Plan


def test_app_renders_scenario_controls_and_invalid_json_message(monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.chdir(root)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=10).run()

    assert not app.exception
    assert any(button.label == "Run" for button in app.button)
    assert any("Scenario JSON is valid" in item.value for item in app.success)

    editor = next(area for area in app.text_area if area.label == "Scenario JSON")
    editor.set_value("{").run()
    assert any("Scenario JSON is invalid" in item.value for item in app.error)


def test_uploaded_scenario_replaces_editor_contents(monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.chdir(root)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=10).run()
    uploaded = (root / "samples" / "input.json").read_bytes()

    app.file_uploader[0].set_value(("uploaded-scenario.json", uploaded, "application/json")).run()

    editor = next(area for area in app.text_area if area.label == "Scenario JSON")
    assert '"Zone A"' in editor.value
    assert '"Zone B"' in editor.value
    assert any("Scenario JSON is valid" in item.value for item in app.success)


def test_runner_smoke_with_stub_llm(monkeypatch, sample_scenario):
    import graph.negotiation as graph
    from ui.runner import run_negotiation

    def stub_claim(agent):
        return lambda state, llm, tracer, counter=None: Claim(
            agent=agent, resources={}, people_affected=1, urgency=5,
            time_window_hrs=2, justification="stub", fallback_option="phase",
            uncertainty=0,
        )

    monkeypatch.setattr(graph, "CLAIMERS", {
        "medical": stub_claim("medical"), "shelter": stub_claim("shelter"),
        "logistics": stub_claim("logistics"),
    })
    monkeypatch.setattr(graph, "decide_next", lambda *args: MediatorAction.finalize)
    monkeypatch.setattr(graph, "mediator_finalize", lambda state, llm, tracer: Plan(
        allocation={claim.agent: {} for claim in state.claims}, rounds_used=state.round,
    ))

    result = run_negotiation(sample_scenario, llm=StubLLM())
    assert result["error"] is None
    assert result["plan"] is not None
    assert {claim["agent"] for claim in result["state"].model_dump()["claims"]} == {
        "medical", "shelter", "logistics",
    }


def test_runner_reports_missing_api_key(monkeypatch, sample_scenario):
    from ui.runner import run_negotiation

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = run_negotiation(sample_scenario)
    assert result["plan"] is None
    assert "No API key configured" in result["error"]
