from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parents[1]


def _app(monkeypatch):
    monkeypatch.chdir(ROOT)
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()


def test_empty_state_welcomes_user_and_has_steps(monkeypatch):
    app = _app(monkeypatch)
    assert not app.exception
    assert any("Welcome to the relief planning control room" in item.value for item in app.subheader)
    assert any(button.label == "Run the example scenario" for button in app.button)
    assert any(tab.label == "Plan" for tab in app.tabs)
    assert any(tab.label == "Negotiation" for tab in app.tabs)
    assert any(tab.label == "What-if events" for tab in app.tabs)
    assert any(tab.label == "Raw trace" for tab in app.tabs)


def test_sample_run_is_read_only_and_renders_all_views(monkeypatch):
    app = _app(monkeypatch)
    next(button for button in app.button if button.label == "View a recorded sample run").click().run()
    assert not app.exception
    assert any("Recorded sample, not live" in item.value for item in app.info)
    assert app.session_state["display_only"] is True
    assert len(app.session_state["trace_events"]) > 0
    assert any("not available" in item.value.lower() or "not included" in item.value.lower()
               for item in app.info)
    assert any(tab.label == "Plan" for tab in app.tabs)


def test_event_builder_is_disabled_until_a_live_run(monkeypatch):
    app = _app(monkeypatch)
    event_type = next(widget for widget in app.selectbox if widget.label == "Event type")
    event_button = next(button for button in app.button if button.label == "Inject event")
    failure_toggle = next(widget for widget in app.toggle if widget.label == "Simulate tool failure")
    assert event_type.disabled
    assert event_button.disabled
    assert failure_toggle.disabled


def test_negotiation_timeline_has_filter_and_tool_toggle(monkeypatch):
    app = _app(monkeypatch)
    next(button for button in app.button if button.label == "View a recorded sample run").click().run()
    assert not app.exception
    assert any(widget.label == "Show teams" for widget in app.multiselect)
    show_tools = next(widget for widget in app.toggle if widget.label == "Show tool calls")
    assert show_tools.value is False
    assert any("rounds" in item.value and "tool failures" in item.value for item in app.caption)


def test_run_is_disabled_for_invalid_scenario_json(monkeypatch):
    app = _app(monkeypatch)
    next(area for area in app.text_area if area.label == "Scenario JSON").set_value("{").run()
    run_button = next(button for button in app.button if button.label == "Run")
    assert run_button.disabled is True
    assert any("Scenario JSON is invalid" in item.value for item in app.error)


from agents.scenario import PRESET_EVENTS
from app import _compose_event


def test_event_builder_uses_exact_known_presets(sample_scenario):
    assert _compose_event("Road or bridge collapse", "Zone A", 8, "", sample_scenario) == PRESET_EVENTS["bridge_collapse"]
    assert _compose_event("New critical patients", "Zone B", 8, "", sample_scenario) == PRESET_EVENTS["new_casualties"]
    assert _compose_event("Rising water", "Zone A", 100, "", sample_scenario) == PRESET_EVENTS["water_rising_fast"]
    assert _compose_event("Hospital full", "Zone A", 8, "", sample_scenario) == PRESET_EVENTS["hospital_capacity"]
