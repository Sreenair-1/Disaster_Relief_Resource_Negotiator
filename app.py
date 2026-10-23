"""Streamlit control room for a disaster relief negotiation."""

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from agents.scenario import PRESET_EVENTS
from lib.schemas import Scenario
from ui.format import agent_style, describe_event, replan_diff, summarize_plan
from ui.runner import inject_event, run_negotiation

DEFAULT_SCENARIO_PATH = Path(__file__).parent / "data" / "scenario.json"
SAMPLE_TRACE_PATH = Path(__file__).parent / "samples" / "trace_full_run.jsonl"
SAMPLE_PLAN_PATH = Path(__file__).parent / "samples" / "output_full_run.json"
RUNTIME_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def configure_llm_environment() -> None:
    """Load local .env values or Streamlit secrets without overriding process env."""
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)
    for key in RUNTIME_KEYS:
        if os.environ.get(key):
            continue
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ[key] = str(value)


def scenario_json() -> str:
    if "scenario_json" not in st.session_state:
        st.session_state.scenario_json = DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8")
    return st.session_state.scenario_json


def set_event_text(text: str) -> None:
    """Set the event description before Streamlit reruns the script."""
    st.session_state.event_text = text


def handle_inject_event() -> None:
    """Inject an event and safely clear the input through its callback."""
    event_text = st.session_state.get("event_text", "").strip()
    if not event_text:
        return
    try:
        Scenario.model_validate_json(st.session_state.get("scenario_json", ""))
    except Exception:
        st.session_state.event_error = "Please provide a valid scenario first."
        return

    previous = st.session_state.get("run_result") or {}
    previous_state = previous.get("state")
    if previous_state is None or st.session_state.get("display_only", False):
        st.session_state.event_error = "Run the scenario live before injecting an event."
        return
    try:
        result = inject_event(previous_state, event_text)
        result["events"] = [*previous.get("events", []), *result.get("events", [])]
        st.session_state.trace_events = result.get("events", [])
        st.session_state.run_result = result
        st.session_state.event_text = ""
        if "custom_event_text" in st.session_state:
            st.session_state.custom_event_text = ""
        st.session_state.event_error = ""
    except Exception as exc:
        st.session_state.event_error = _friendly_error(str(exc))


def _friendly_error(message: str) -> str:
    lowered = message.casefold()
    if "api key" in lowered or "openai_api_key" in lowered:
        return "No API key is set. Add OPENAI_API_KEY to the root .env file or Streamlit app secrets, then try again."
    if "tool" in lowered:
        return f"A relief data tool was unavailable. Check the tool settings and try again. ({message})"
    return f"The negotiation could not finish. Check the setup and try again. ({message})"


def _init_session() -> None:
    st.session_state.setdefault("scenario_editor", scenario_json())
    st.session_state.setdefault("run_result", None)
    st.session_state.setdefault("trace_events", [])
    st.session_state.setdefault("event_text", "")
    st.session_state.setdefault("event_error", "")
    st.session_state.setdefault("display_only", False)
    st.session_state.setdefault("failure_enabled", False)


def _load_recorded_sample() -> None:
    try:
        events = [json.loads(line) for line in SAMPLE_TRACE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        plan = json.loads(SAMPLE_PLAN_PATH.read_text(encoding="utf-8"))
        st.session_state.run_result = {
            "state": None, "plan": plan, "events": events,
            "error": None, "warnings": [], "before_plan": None,
        }
        st.session_state.trace_events = events
        st.session_state.display_only = True
        st.session_state.event_error = ""
    except (OSError, json.JSONDecodeError) as exc:
        st.session_state.event_error = f"The recorded sample could not be loaded: {exc}"


def _scenario_sidebar(scenario_raw: str) -> Scenario | None:
    st.sidebar.header("Scenario")
    try:
        scenario = Scenario.model_validate_json(scenario_raw)
    except Exception as exc:
        st.sidebar.error(f"Scenario JSON needs a correction: {exc}")
        scenario = None

    if scenario is not None:
        with st.sidebar.container(border=True):
            st.markdown(f"**{scenario.disaster_type.replace('_', ' ').title()}**")
            st.caption(f"{len(scenario.zones)} zones · resources available: " +
                       (", ".join(f"{amount} {name}" for name, amount in scenario.resources.items()) or "not listed"))
            rows = []
            for zone in scenario.zones:
                access = zone.road_access
                road = "Open" if access >= 0.75 else "Partial" if access > 0 else "Cut"
                rows.append({"Zone": zone.name, "Population": zone.population,
                             "Stranded": zone.stranded, "Critical": zone.critical_patients,
                             "Road access": f"{road} ({access:.0%})"})
            st.dataframe(rows, hide_index=True, width="stretch")

    with st.sidebar.expander("Advanced: edit scenario JSON", expanded=False):
        uploaded = st.file_uploader("Load scenario JSON", type=["json"], key="scenario_upload",
                                    help="Upload a scenario file to fill the editor below.")
        if uploaded is not None:
            text = uploaded.getvalue().decode("utf-8")
            if text != st.session_state.get("last_scenario_upload"):
                st.session_state.scenario_editor = text
                st.session_state.last_scenario_upload = text
        raw = st.text_area("Scenario JSON", height=320, key="scenario_editor",
                           help="Advanced: edit the complete scenario as JSON.")
        st.session_state.scenario_json = raw
        try:
            Scenario.model_validate_json(raw)
            st.success("Scenario JSON is valid")
        except Exception as exc:
            st.error(f"Scenario JSON is invalid: {exc}")
    return scenario


def _render_sidebar() -> tuple[Scenario | None, bool]:
    raw = st.session_state.scenario_editor
    scenario = _scenario_sidebar(raw)
    run_clicked = st.sidebar.button("Run", type="primary", icon=":material/play_arrow:",
                                    disabled=scenario is None,
                                    help="Ask the agents to review needs and allocate available resources.",
                                    key="run_scenario")
    with st.sidebar.expander("Export", expanded=False):
        result = st.session_state.get("run_result") or {}
        if result.get("plan"):
            st.download_button("Download final plan", data=json.dumps(result["plan"], indent=2, ensure_ascii=False),
                               file_name="output_full_run.json", mime="application/json",
                               help="Save the current allocation plan as JSON.")
        if result.get("events"):
            trace_data = "\n".join(json.dumps(event, default=str) for event in result["events"])
            st.download_button("Download trace", data=trace_data, file_name="trace_full_run.jsonl",
                               mime="application/x-ndjson", help="Save every agent and tool event as JSONL.")
    return scenario, run_clicked


def _run_scenario() -> None:
    try:
        scenario = Scenario.model_validate_json(st.session_state.get("scenario_editor", ""))
    except Exception as exc:
        st.session_state.event_error = f"Scenario JSON is invalid: {exc}"
        return
    with st.status("Agents are gathering data and negotiating. This can take a minute or two.",
                   expanded=True) as status:
        result = run_negotiation(scenario)
        st.session_state.run_result = result
        st.session_state.trace_events = result.get("events", [])
        st.session_state.display_only = False
        if result.get("error"):
            status.update(label="The run needs attention", state="error")
        else:
            status.update(label="Negotiation complete", state="complete")


def _render_welcome() -> tuple[bool, bool]:
    with st.container(border=True):
        st.subheader("Welcome to the relief planning control room")
        st.write("Three specialist teams review urgent medical care, shelter, and supply needs. The Mediator compares their requests with available resources and shows where needs remain unmet.")
        st.caption("Start with the example scenario, or open the advanced editor in the sidebar to use your own.")
        left, right = st.columns(2)
        run_clicked = left.button("Run the example scenario", type="primary", icon=":material/play_arrow:",
                                  help="Start a live negotiation with the current scenario.")
        sample_clicked = right.button("View a recorded sample run", icon=":material/visibility:",
                                       help="Open a saved run without calling an API. This view is read-only.")
    return run_clicked, sample_clicked


def _render_error_and_warnings(result: dict[str, Any]) -> None:
    if result.get("error"):
        st.error(result["error"])
        if "api key" in result["error"].casefold():
            st.info("Next step: add OPENAI_API_KEY to the root .env file, or add it to Streamlit app secrets when deployed.")
    for warning in result.get("warnings", []) or []:
        st.warning(f"Some relief data could not be checked: {warning}")
    if st.session_state.get("event_error"):
        st.warning(st.session_state.event_error)
    if st.session_state.get("display_only"):
        st.info("Recorded sample, not live. Event injection is disabled in this view.")


def _render_plan_tab(plan: dict[str, Any], before_plan: dict[str, Any] | None) -> None:
    summary = summarize_plan(plan)
    if summary["status"] == "resolved" and not plan.get("life_safety_gap", False):
        st.success(f"{summary['status_label']}: {summary['headline']}")
    elif summary["status"] == "constrained" or plan.get("life_safety_gap", False):
        st.warning(f"{summary['status_label']}: {summary['headline']}")
    else:
        st.info(summary["headline"])

    kpis = summary["kpis"]
    coverage = kpis["critical_patients_covered_percent"]
    with st.container(horizontal=True):
        st.metric("Negotiation rounds", kpis["rounds"], border=True)
        st.metric("Conflicts resolved", kpis["conflicts"], border=True)
        st.metric("Replans", kpis["replans"], border=True)
        st.metric("Critical patients covered", f"{coverage:.1f}%" if coverage is not None else "Not reported", border=True)

    if plan.get("life_safety_gap", False):
        st.error("There is a critical-patient coverage gap. Review the zone coverage and risks below.")

    allocation = plan.get("allocation") or {}
    st.subheader("Resource allocation by team")
    if not allocation:
        st.info("This plan does not include an agent allocation.")
    for agent, resources in allocation.items():
        style = agent_style(agent)
        explanation = (plan.get("justifications") or {}).get(agent, "No explanation was included in this plan.")
        with st.container(border=True):
            st.markdown(f"### {style['icon']} {style['name']}")
            st.write(", ".join(f"**{amount} {resource}**" for resource, amount in resources.items()) or "No resources allocated.")
            st.caption(explanation.split(".", 1)[0].strip() + ("." if "." in explanation else ""))
            with st.expander("Read the full explanation"):
                st.write(explanation)

    diff = replan_diff(before_plan, plan) if before_plan else None
    if diff:
        st.subheader("First run → after the event")
        if not diff["changed"]:
            st.info(diff["headline"])
        else:
            for row in diff["rows"]:
                if row["delta"] > 0:
                    st.success(f"{row['arrow']} {row['agent']}: {row['resource']} {row['before']} → {row['after']}. {row['reason']}")
                elif row["delta"] < 0:
                    st.warning(f"{row['arrow']} {row['agent']}: {row['resource']} {row['before']} → {row['after']}. {row['reason']}")
                else:
                    st.caption(f"{row['agent']}: {row['resource']} unchanged at {row['after']}. {row['reason']}")

    st.subheader("Assignments by zone")
    by_zone = plan.get("allocation_by_zone") or {}
    if by_zone:
        rows = [{"Zone": zone, **resources} for zone, resources in by_zone.items()]
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Zone-level assignments are not available in this saved plan.")

    st.subheader("People covered by zone")
    coverage_by_zone = plan.get("coverage") or {}
    if coverage_by_zone:
        for zone, tiers in coverage_by_zone.items():
            with st.container(border=True):
                st.markdown(f"**{zone}**")
                for tier, figures in tiers.items():
                    demand = int(figures.get("demand", 0) or 0)
                    served = int(figures.get("people_served", 0) or 0)
                    percent = float(figures.get("coverage_percent", 0) or 0)
                    st.progress(min(1.0, max(0.0, percent / 100)),
                                text=f"{tier.replace('_', ' ').title()}: {served} of {demand} people ({percent:.1f}%)")
    else:
        st.info("Coverage estimates are not included in this saved plan.")

    for risk in plan.get("risks") or []:
        st.warning(str(risk))
    for item in plan.get("unresolved_shortfalls") or []:
        if isinstance(item, dict):
            st.error(f"Resource shortfall: {item.get('resource', 'resource')} still short by {item.get('unmet_amount', 0)} units. Affected teams: {', '.join(item.get('agents') or []) or 'not specified'}.")
    for item in plan.get("non_inventory_requests") or []:
        if isinstance(item, dict):
            st.caption(f"Unlisted resource request: {item.get('agent', 'Team')} asked for {item.get('requested', 0)} {item.get('resource', 'resource')}; it was not included in available stock.")
    assumptions = (plan.get("feasibility_report") or {}).get("unverified_assumptions") or []
    with st.expander("Information that could not be verified"):
        if assumptions:
            for item in assumptions:
                st.write(f"- {item}")
        else:
            st.write("No unverified assumptions were listed in this plan.")
    with st.expander("How to read this plan"):
        st.write("**Score** is a consistent priority guide based on people at risk, urgency, time window, and access. **Priority tiers** put life-safety needs first. **Coverage** estimates how many people can be reached using recorded capacities and travel times. A **constrained** plan means requests still exceed resources; it is not a confirmation that every need is met. A **life-safety gap** means some critical patients are not covered by the verified plan.")


def _render_negotiation_tab(events: list[dict[str, Any]]) -> None:
    from ui.format import group_by_round

    rounds = group_by_round(events)
    challenges = sum(1 for event in events if event.get("type") == "decision" and
                     (event.get("payload") or {}).get("action") == "challenge_agent")
    revisions = sum(1 for event in events if event.get("type") == "claim_revision")
    failures = sum(1 for event in events if event.get("type") == "tool_result" and
                   (event.get("payload") or {}).get("ok") is False)
    result = st.session_state.get("run_result") or {}
    rounds_count = (result.get("plan") or {}).get("rounds_used") or max(rounds, default=0)
    st.caption(f"{rounds_count} rounds · {challenges} challenges · {revisions} revisions · {failures} tool failures")
    if not events:
        st.info("Run the scenario to see how the teams and Mediator reached a plan.")
        return

    agent_names = sorted({describe_event(event)["agent"] for event in events})
    selected_agents = st.multiselect("Show teams", agent_names, default=agent_names,
                                    help="Choose which teams and coordinators to show.")
    show_tools = st.toggle("Show tool calls", value=False,
                           help="Show each data lookup. Failures and recovery estimates are counted above.")
    with st.expander("Agent legend"):
        st.write(" · ".join(f"{agent_style(name)['icon']} {name}" for name in agent_names))

    hidden_tool_kinds = {"tool_requested", "tool_result"}
    for round_number, round_events in rounds.items():
        visible = []
        for event in round_events:
            summary = describe_event(event)
            if summary["agent"] not in selected_agents:
                continue
            if not show_tools and event.get("type") in hidden_tool_kinds:
                continue
            visible.append((event, summary))
        if not visible:
            continue
        st.subheader(f"Round {round_number}" if round_number else "Before the first round")
        for index, (event, summary) in enumerate(visible, start=1):
            st.markdown(f"{summary['icon']} **{summary['agent']}** — {summary['headline']}")
            if summary["detail"]:
                st.caption(summary["detail"])
            with st.expander(f"View event details ({event.get('type', 'event')})"):
                st.json(event)


def _number_word(number: int) -> str:
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
             6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
    return words.get(number, str(number))


def _compose_event(kind: str, zone: str, magnitude: int, custom_text: str,
                   scenario: Scenario | None) -> str:
    if kind == "Custom":
        return custom_text.strip()
    if kind == "Road or bridge collapse":
        if zone == "Zone A":
            return PRESET_EVENTS["bridge_collapse"]
        return f"Bridge to {zone} collapses, cutting all ground road access to {zone}."
    if kind == "New critical patients":
        if zone == "Zone B" and magnitude == 8:
            return PRESET_EVENTS["new_casualties"]
        return f"{_number_word(magnitude)} new critical patients arrive in {zone}."
    if kind == "Rising water":
        if zone == "Zone A" and magnitude == 100:
            return PRESET_EVENTS["water_rising_fast"]
        return f"Flood water rises in {zone}, increasing stranded residents by {magnitude}%."
    if kind == "Hospital full":
        hospitals = scenario.operations.hospitals if scenario else []
        name = hospitals[0].name if hospitals else (
            scenario.hospitals[0] if scenario and scenario.hospitals else "The receiving hospital"
        )
        if name == "Regional Hospital":
            return PRESET_EVENTS["hospital_capacity"]
        return f"{name} reaches full capacity; ICU beds exhausted."
    return ""


def _render_event_warnings(events: list[dict[str, Any]]) -> None:
    for event in events:
        payload = event.get("payload") or {}
        if event.get("type") == "event_rejected" and isinstance(payload, dict) and payload.get("warning"):
            st.warning(f"Event had no effect: {payload.get('reason', 'no changes were made')}")


def _render_event_tab(scenario: Scenario | None, result: dict[str, Any], events: list[dict[str, Any]]) -> None:
    live = bool(result.get("state") is not None and not st.session_state.get("display_only"))
    if not live:
        st.info("Run a live scenario first to enable event injection. Recorded samples are read-only.")

    try:
        from tools.failure import set_failure

        failure_enabled = st.toggle("Simulate tool failure", key="failure_enabled", disabled=not live,
                                    help="Makes data tools fail so you can see agents recover.")
        set_failure("*" if failure_enabled else None)
    except ImportError:
        st.info("Tool failure simulation is not available in this installation.")

    st.subheader("Build a what-if event")
    kinds = ["Road or bridge collapse", "New critical patients", "Rising water", "Hospital full", "Custom"]
    kind = st.selectbox("Event type", kinds, key="event_kind", disabled=not live,
                        help="Choose a clear change to test against this scenario.")
    zones = [zone.name for zone in scenario.zones] if scenario and scenario.zones else ["No zone available"]
    zone = st.selectbox("Affected zone", zones, key="event_zone", disabled=not live or kind == "Hospital full",
                        help="Choose the zone affected by the event.")
    magnitude = 8
    if kind == "New critical patients":
        magnitude = st.number_input("New critical patients", min_value=1, max_value=10000, value=8,
                                    step=1, disabled=not live, help="Number of additional critical patients.")
    elif kind == "Rising water":
        magnitude = st.number_input("Increase in stranded residents (%)", min_value=10, max_value=500,
                                    value=100, step=10, disabled=not live,
                                    help="How much the event increases stranded residents.")
    custom_text = ""
    if kind == "Custom":
        with st.expander("Write a custom event"):
            custom_text = st.text_area("Custom event text", key="custom_event_text", disabled=not live,
                                       help="Describe one concrete change and name its zone.")
    composed = _compose_event(kind, zone, int(magnitude), custom_text, scenario)
    st.session_state.event_text = composed
    st.text_area("Event text to apply", value=composed, disabled=True, height=80,
                 help="This is the unambiguous text that will be sent to the scenario agent.")
    st.button("Inject event", type="primary", icon=":material/send:",
              disabled=not live or not composed.strip(), on_click=handle_inject_event,
              help="Apply the event, refresh affected claims, and compare the plan.")

    if st.session_state.get("event_error"):
        st.warning(st.session_state.event_error)

    st.subheader("Event history")
    history = [event for event in events if event.get("type") in {"event_applied", "event", "event_rejected"}]
    if not history:
        st.caption("No events have been applied during this run.")
    for event in history:
        description = describe_event(event)
        with st.container(border=True):
            st.markdown(f"{description['icon']} **{description['headline']}**")
            if description["detail"]:
                st.caption(description["detail"])
    if history and live:
        affected = (result.get("state").affected_agents if result.get("state") else [])
        st.write("Teams asked to review their claims: " + (", ".join(agent_style(a)["name"] for a in affected) or "none reported"))
        before_plan = result.get("before_plan")
        after_plan = result.get("plan") or {}
        if before_plan:
            diff = replan_diff(before_plan, after_plan)
            st.write(diff["headline"])
            for row in diff["rows"]:
                st.caption(f"{row['arrow']} {row['agent']} · {row['resource']}: {row['before']} → {row['after']} — {row['reason']}")


def _render_raw_trace(events: list[dict[str, Any]]) -> None:
    if not events:
        st.info("No trace is available yet.")
        return
    page_size = 20
    page_count = max(1, (len(events) + page_size - 1) // page_size)
    page = st.number_input("Trace page", min_value=1, max_value=page_count, value=1,
                           step=1, help="The trace is split into pages of 20 events.")
    start = (int(page) - 1) * page_size
    for index, event in enumerate(events[start:start + page_size], start=start + 1):
        with st.expander(f"Event {index}: {event.get('type', 'unknown')} · {event.get('sender', 'system')}"):
            st.json(event)


def main() -> None:
    st.set_page_config(page_title="Relief Negotiator", page_icon="🌊", layout="wide")
    configure_llm_environment()
    _init_session()
    st.title("Disaster relief planning")
    st.write("See how specialist teams balance urgent medical, shelter, and supply needs against available resources.")
    with st.container(horizontal=True):
        for number, title in ((1, "Load and run"), (2, "Review the plan"), (3, "Try a what-if event")):
            with st.container(border=True):
                st.markdown(f"**{number}. {title}**")

    scenario, sidebar_run_clicked = _render_sidebar()
    result = st.session_state.get("run_result") or {}
    welcome_run_clicked = sample_clicked = False
    if not result:
        welcome_run_clicked, sample_clicked = _render_welcome()
    if sample_clicked:
        _load_recorded_sample()
    elif sidebar_run_clicked or welcome_run_clicked:
        _run_scenario()
    result = st.session_state.get("run_result") or {}
    _render_error_and_warnings(result)
    if st.session_state.get("display_only"):
        st.caption("Recorded sample, not live · read-only")
    _render_event_warnings(st.session_state.get("trace_events", []))

    plan_tab, negotiation_tab, events_tab, trace_tab = st.tabs(
        ["Plan", "Negotiation", "What-if events", "Raw trace"]
    )
    with plan_tab:
        plan = result.get("plan") or {}
        if plan:
            _render_plan_tab(plan, result.get("before_plan"))
        elif result.get("error"):
            st.info("A plan will appear here after the API key and scenario are ready.")
        else:
            st.info("Run the example scenario or open a recorded sample to review an allocation.")
    with negotiation_tab:
        _render_negotiation_tab(st.session_state.get("trace_events", []))
    with events_tab:
        _render_event_tab(scenario, result, st.session_state.get("trace_events", []))
    with trace_tab:
        _render_raw_trace(st.session_state.get("trace_events", []))


if __name__ == "__main__":
    main()
