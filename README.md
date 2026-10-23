# Disaster Relief Resource Negotiator

Five agents coordinate a flood-relief response: **Medical**, **Shelter**,
**Logistics**, **Mediator**, and **Scenario**. The specialist agents make
typed resource claims, Mediator resolves trade-offs under inventory limits,
and Scenario injects changing conditions and replans affected claims. See
[docs/SYNOPSIS.md](docs/SYNOPSIS.md) for the project summary and
[docs/DESIGN.md](docs/DESIGN.md) for architecture, tools, schemas, and samples.

## Setup

Develop and run with Python 3.14.7. Create a fresh virtual environment and
install the pinned dependencies:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `OPENAI_API_KEY`, or provide those values
in the process environment. The app reads local `.env` settings and Streamlit
secrets; `python-dotenv` loads `.env` values without overriding values already
in the process environment. The example is configured for Groq's OpenAI
compatible API with `openai/gpt-oss-120b`; for OpenAI, set `OPENAI_MODEL` to an
available model and leave `OPENAI_BASE_URL` blank. Never commit a real key.
Without one, the UI shows a setup message. The graph can also be called with
`StubLLM` for deterministic development tests.

Operational capacities and demo population splits are explicit assumptions,
not verified field data; see [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

## Run

```powershell
streamlit run app.py
```

Load the seed scenario from `data/scenario.json` or a scenario from
`data/scenarios/`, edit it if needed, and select **Run**. The control room shows
the colored agent transcript,
tool/counter badges, allocation, conflicts, negotiation rounds, and JSONL trace.
Inject an event after a successful first run to see the before/after allocation
comparison and the reasons for updated awards.

When requests exceed stock, the Mediator records each claimant's response to a
counter before early finalization. If the three-round safety cap is reached,
the plan is marked `constrained` and reports unresolved resource shortfalls.
One round means all challenged agents have responded; the round count advances
once for the complete cycle, not once per challenge, with a hard cap of three.
Allocation ranks severity-weighted, time-sensitive claims, respects known
feasibility caps, and splits stock proportionally only within tied priority
tiers. Severity weights are documented model assumptions.
Medical support is marked unverified when ICU or receiving-facility data is
unknown; infeasible road or unavailable-boat fallback options are rejected.
Capacity tools report only scenario-provided values; unknown capacity remains
null and route verification requires the planning inputs to be known.
Claims are checked against known capacities and inventory; non-inventory asks
are reported separately from unresolved stock shortfalls.
Allocation follows life-safety tiers and reports agent totals, zone assignments,
per-zone coverage, any critical-patient gap, and inventory that no zone can use.
Resolved conflicts list only deficits eliminated by validated claim changes;
remaining stock gaps appear separately as shortfalls.
The Wayanad operations block is explicitly a demo assumption ledger, not local
fleet data; IFRC early-action guidance informs urgency but provides no vehicle
capacities or zone travel times.
The final plan includes a feasibility report, and its explanations are checked
against the numeric scores, ranks, requests, and awards before display.
Preset events apply concrete scenario diffs, including hospital ICU and
transfer-capacity changes. Replans that remain oversubscribed run one short
challenge and log each affected agent's before/after reassessment reason.

## Demo script

1. Load `data/scenarios/wayanad_monsoon_flood.json` and select **Run**. Review
   the first allocation and the three specialist claims.
2. Inject the preset event `Bridge to Meppadi Valley collapses, cutting all
   ground road access to Meppadi Valley.` Review the replan allocation.
3. Enable **Simulate tool failure** and inject `Eight new critical patients
   arrive in Mananthavady Riverine.` Confirm the tool failure and estimate
   feedback appear in the transcript.
4. Disable **Simulate tool failure**. Download **Download trace** as
   `trace_full_run.jsonl` and **Download final plan** as
   `output_full_run.json`, saving both into `samples/`.

## Test

```powershell
python -m pytest
```

## Deploy on Streamlit Community Cloud

Create an app from this repository with `app.py` as the entrypoint. In
**Advanced settings**, choose Python 3.14 when it is available in the version
selector; Cloud currently defaults to Python 3.12 and documents support for
released versions still receiving security updates. Add `OPENAI_API_KEY` via
the app's Secrets settings, and set `OPENAI_BASE_URL` and `OPENAI_MODEL` there
when using Groq. Never add credentials to the repository. See the official
[deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
and [Python version guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/upgrade-python).

## Trace self-check and Wayanad regeneration

After downloading a run, validate it against the matching scenario:

```powershell
python scripts/check_trace.py samples/trace_full_run.jsonl samples/output_full_run.json --scenario data/scenarios/wayanad_monsoon_flood.json
```

To regenerate the full Wayanad artifacts, load the Wayanad scenario and click
**Run**. Inject `Bridge to Meppadi Valley collapses, cutting all ground road
access to Meppadi Valley.`. Enable **Simulate tool failure**, inject
`Eight new critical patients arrive in Mananthavady Riverine.`, then disable
failure. Download the JSONL as `samples/trace_full_run.jsonl` and the final
plan as `samples/output_full_run.json`. Run the checker command above; it
prints each check and exits nonzero if any invariant fails.

### UI presentation

The Streamlit control room opens with a short explanation and offers either a
live run or a read-only recorded sample. The sidebar summarizes the scenario
and keeps upload and JSON editing under **Advanced: edit scenario JSON**. The
main view separates the plan, negotiation timeline, what-if events, and raw
trace into tabs. Plan summaries tolerate older files that omit optional
fields, and show team allocations, zone coverage, risks, and replan changes.
The Negotiation tab uses plain-language event descriptions; Raw trace keeps the
original JSON for review. When no API key is available, the app shows where to
configure one instead of a traceback.
