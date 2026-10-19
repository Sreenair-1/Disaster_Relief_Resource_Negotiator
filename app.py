"""Streamlit control room for a disaster relief negotiation."""

from pathlib import Path

import streamlit as st

from lib.schemas import Scenario
from ui.runner import inject_event, run_negotiation

DEFAULT_SCENARIO_PATH = Path(__file__).parent / "data" / "scenario.json"


def scenario_json() -> str:
    if "scenario_json" not in st.session_state:
        st.session_state.scenario_json = DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8")
    return st.session_state.scenario_json


def main() -> None:
    st.set_page_config(page_title="Relief Negotiator", layout="wide")
    st.title("Disaster Relief Resource Negotiator")
    st.caption("Scenario control room · negotiation runner is a replaceable placeholder")

    with st.sidebar:
        st.header("Scenario")
        uploaded = st.file_uploader("Load scenario JSON", type=["json"])
        if uploaded is not None:
            st.session_state.scenario_json = uploaded.getvalue().decode("utf-8")
        raw = st.text_area("Edit scenario JSON", value=scenario_json(), height=360, key="scenario_editor")
        st.session_state.scenario_json = raw
        valid_scenario = None
        try:
            valid_scenario = Scenario.model_validate_json(raw)
            st.success("Scenario is valid")
        except Exception as exc:
            st.error(f"Invalid scenario JSON or fields: {exc}")

        if st.button("Run", type="primary", disabled=valid_scenario is None):
            result = run_negotiation(valid_scenario)
            st.session_state.run_result = result
            st.session_state.trace_events = result["events"]

        st.divider()
        st.subheader("Inject an event")
        event_text = st.text_input("Event description", key="event_text")
        cols = st.columns(2)
        if cols[0].button("Road blocked"):
            event_text = "Road access is blocked."
        if cols[1].button("New patients"):
            event_text = "A new group of critical patients has been reported."
        if cols[0].button("Weather worsened"):
            event_text = "Weather conditions have worsened."
        if cols[1].button("Inject event", disabled=not event_text.strip()):
            if valid_scenario is not None:
                result = inject_event(valid_scenario, event_text.strip())
                st.session_state.trace_events = result["events"]
                st.session_state.run_result = result
                st.session_state.event_text = ""

        try:
            from tools.failure import set_failure

            fail = st.toggle("Simulate tool failure")
            set_failure("*" if fail else None)
        except ImportError:
            st.info("Tool failure injection is not available yet.")

        result = st.session_state.get("run_result")
        if result:
            st.download_button(
                "Download trace",
                data="\n".join(__import__("json").dumps(e, default=str) for e in result["events"]),
                file_name="negotiation-trace.jsonl",
                mime="application/x-ndjson",
            )

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Negotiation transcript")
        events = st.session_state.get("trace_events", [])
        if events:
            for event in events:
                with st.chat_message("assistant" if event.get("sender") in {"mediator", "system"} else "user"):
                    st.markdown(f"**{event.get('sender', 'system')} → {event.get('receiver', 'all')}** · {event.get('type', 'event')}")
                    st.json(event.get("payload", {}))
        else:
            st.info("Run a scenario to see tracer events here.")
    with right:
        st.subheader("Allocation")
        result = st.session_state.get("run_result")
        plan = result.get("plan") if result else None
        if plan and plan.get("allocation"):
            rows = []
            for agent, resources in plan["allocation"].items():
                for resource, amount in resources.items():
                    rows.append({"Agent": agent, "Resource": resource, "Allocated": amount,
                                 "Justification": plan.get("justifications", {}).get(agent, "")})
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("The placeholder runner does not produce an allocation yet.")


if __name__ == "__main__":
    main()
