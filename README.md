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
counter before early finalization. If the six-round safety cap is reached,
the plan is marked `constrained` and reports unresolved resource shortfalls.
Allocation ranks severity-weighted, time-sensitive claims, respects known
feasibility caps, and splits stock proportionally only within tied priority
tiers. Severity weights are documented model assumptions.
Medical support is marked unverified when ICU or receiving-facility data is
unknown; infeasible road or unavailable-boat fallback options are rejected.
Capacity tools report only scenario-provided values; unknown capacity remains
null and route verification requires the planning inputs to be known.
The Wayanad operations block is explicitly a demo assumption ledger, not local
fleet data; IFRC early-action guidance informs urgency but provides no vehicle
capacities or zone travel times.
The final plan includes a feasibility report, and its explanations are checked
against the numeric scores, ranks, requests, and awards before display.
Preset events apply concrete scenario diffs, including hospital ICU and
transfer-capacity changes. Replans that remain oversubscribed run one short
challenge and log each affected agent's before/after reassessment reason.

## Demo script

1. Load the seed `data/scenario.json` (with Zone A and Zone B) and select
   **Run**. Review the first allocation and the three specialist claims.
2. Enter the preset event
   `Bridge to Zone A collapses, cutting all ground road access to Zone A.` and
   select **Inject event**. Review the new scenario facts, affected-agent
   refresh, and first-run versus replan allocation comparison.
3. Enter the hospital preset exactly as listed in `agents.scenario.PRESET_EVENTS`:
   `Regional Hospital reaches full capacity; ICU beds exhausted.` Select
   **Inject event** and confirm the hospital diff marks Medical as affected.
4. Enable **Simulate tool failure** in the sidebar, then enter the preset event
   `Eight new critical patients arrive in Zone B.` and select **Inject event**.
   The transcript records failed tool calls; agents continue with uncertainty and
   fallback estimates, and a warning panel shows unavailable tool data.
5. Disable **Simulate tool failure**. Download **Download trace** as
   `trace_full_run.jsonl` and **Download final plan** as
   `output_full_run.json`, saving both into `samples/`. The preset event texts come from
   `agents.scenario.PRESET_EVENTS` (`bridge_collapse` and `new_casualties`).

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
