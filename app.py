"""Streamlit control room for a disaster relief negotiation."""

from pathlib import Path
import os

import streamlit as st
from dotenv import load_dotenv

from lib.schemas import Scenario
from ui.runner import inject_event, run_negotiation

DEFAULT_SCENARIO_PATH = Path(__file__).parent / "data" / "scenario.json"
RUNTIME_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def configure_llm_environment() -> None:
    """Load local .env values or Streamlit secrets without overriding process env."""
    env_file = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_file, override=False)

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
    """Inject an event and safely clear the input through a callback."""
    event_text = st.session_state.get("event_text", "").strip()

    if not event_text:
        return

    try:
        raw = st.session_state.get("scenario_json", "")
        valid_scenario = Scenario.model_validate_json(raw)
    except Exception:
        st.session_state.event_error = "Please provide a valid scenario first."
        return

    previous = st.session_state.get("run_result") or {}
    previous_state = previous.get("state")

    if previous_state is None:
        st.session_state.event_error = (
            "Run the scenario successfully before injecting an event."
        )
        return

    try:
        result = inject_event(previous_state, event_text)
        result["events"] = [
            *previous.get("events", []),
            *result["events"],
        ]
        st.session_state.trace_events = result["events"]
        st.session_state.run_result = result

        # Safe here: this function executes as a widget callback.
        st.session_state.event_text = ""

    except Exception as exc:
        st.session_state.event_error = f"Event injection failed: {exc}"


def main() -> None:
    configure_llm_environment()
    st.set_page_config(page_title="Relief Negotiator", layout="wide")
    st.title("Disaster Relief Resource Negotiator")
    st.caption("Scenario control room · multi-agent negotiation and event replanning")

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

        st.text_input(
            "Event description",
            key="event_text",
        )

        cols = st.columns(2)

        cols[0].button(
            "Road blocked",
            on_click=set_event_text,
            args=("Bridge to Zone A collapses, cutting all ground road access to Zone A.",),
        )

        cols[1].button(
            "New patients",
            on_click=set_event_text,
            args=("Eight new critical patients arrive in Zone B.",),
        )

        cols[0].button(
            "Water rising",
            on_click=set_event_text,
            args=("Water in Zone A rises sharply, doubling the number of stranded residents.",),
        )

        cols[1].button(
            "Inject event",
            type="primary",
            disabled=not st.session_state.event_text.strip(),
            on_click=handle_inject_event,
        )
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
        result = st.session_state.get("run_result") or {}
        if result.get("error"):
            st.error(result["error"])
        for warning in result.get("warnings", []):
            st.warning(f"Some relief data was unavailable: {warning}")
        for ev in result.get("events", []):
            if (ev.get("type") == "event_rejected"
                    and isinstance(ev.get("payload"), dict)
                    and ev["payload"].get("warning")):
                st.warning(f"⚠️ Event had no effect: {ev['payload'].get('reason', 'no-op')}")
        if st.session_state.get("event_error"):
            st.error(st.session_state.pop("event_error"))
        events = st.session_state.get("trace_events", [])
        if events:
            for event in events:
                sender = event.get("sender", "system")
                colors = {"medical": "red", "shelter": "green", "logistics": "blue",
                          "mediator": "violet", "scenario": "orange", "toolcall": "gray"}
                with st.chat_message("assistant" if sender in {"mediator", "system"} else "user"):
                    st.markdown(f":{colors.get(sender, 'gray')}[**{sender}**] → **{event.get('receiver', 'all')}** · {event.get('type', 'event')}")
                    payload = event.get("payload", {})
                    badges = []
                    if event.get("type") in {"tool_requested", "tool_result"}:
                        badges.append("🛠️ Tool call")
                    if isinstance(payload, dict) and payload.get("counter"):
                        badges.append("✉️ Counter request")
                    if badges:
                        st.caption(" · ".join(badges))
                    st.json(payload)
        else:
            st.info("Run a scenario to see tracer events here.")
    with right:
        st.subheader("Allocation")
        result = st.session_state.get("run_result")
        plan = result.get("plan") if result else None
        if plan and plan.get("allocation"):
            before = result.get("before_plan")
            if before and before.get("allocation"):
                st.markdown("**First run → replan**")
                before_alloc, after_alloc = before["allocation"], plan["allocation"]
                compare_rows = []
                for agent in sorted(set(before_alloc) | set(after_alloc)):
                    resources = set(before_alloc.get(agent, {})) | set(after_alloc.get(agent, {}))
                    for resource in sorted(resources):
                        old = before_alloc.get(agent, {}).get(resource, 0)
                        new = after_alloc.get(agent, {}).get(resource, 0)
                        compare_rows.append({"Agent": agent, "Resource": resource, "Before": old,
                                             "After": new, "Change": new - old,
                                             "Reason": plan.get("justifications", {}).get(agent, "Updated after scenario event.")})
                st.dataframe(compare_rows, use_container_width=True, hide_index=True)
            counts = st.columns(3)
            counts[0].metric("Rounds", plan.get("rounds_used", 0))
            counts[1].metric("Conflicts", len(plan.get("conflicts_resolved", [])))
            counts[2].metric("Replans", plan.get("replans", 0))
            rows = []
            for agent, resources in plan["allocation"].items():
                for resource, amount in resources.items():
                    rows.append({"Agent": agent, "Resource": resource, "Allocated": amount,
                                 "Justification": plan.get("justifications", {}).get(agent, "")})
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            if not result or not result.get("error"):
                st.info("Run a scenario to see its allocation.")


if __name__ == "__main__":
    main()
