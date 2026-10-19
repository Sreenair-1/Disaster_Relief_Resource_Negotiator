from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_app_renders_scenario_controls_and_invalid_json_message(monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.chdir(root)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=10).run()

    assert not app.exception
    assert any(button.label == "Run" for button in app.button)
    assert any("Scenario is valid" in item.value for item in app.success)

    editor = next(area for area in app.text_area if area.label == "Edit scenario JSON")
    editor.set_value("{").run()
    assert any("Invalid scenario JSON or fields" in item.value for item in app.error)
