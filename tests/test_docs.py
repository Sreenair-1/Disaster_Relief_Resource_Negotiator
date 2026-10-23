import json
import re
from pathlib import Path

import pytest

from lib.schemas import Plan, Scenario
from tools.registry import TOOLS
from scripts.gen_io_docs import generate


ROOT = Path(__file__).parents[1]
REGENERATION_REASON = (
    "Regenerate samples/trace_full_run.jsonl and samples/output_full_run.json "
    "with samples/input.json loaded, then rerun scripts/gen_io_docs.py."
)


def test_design_lists_every_registered_tool():
    design = (ROOT / "docs/DESIGN.md").read_text(encoding="utf-8")
    assert all(tool in design for tool in TOOLS)


def test_synopsis_and_design_name_all_five_agents():
    synopsis = (ROOT / "docs/SYNOPSIS.md").read_text(encoding="utf-8").casefold()
    design = (ROOT / "docs/DESIGN.md").read_text(encoding="utf-8").casefold()
    for name in ("medical", "shelter", "logistics", "mediator", "scenario"):
        assert name in synopsis
        assert name in design


def test_synopsis_has_exactly_three_paragraphs_before_scope_section():
    synopsis = (ROOT / "docs/SYNOPSIS.md").read_text(encoding="utf-8")
    body = synopsis.split("## Scope changes from CA2", 1)[0]
    body = re.sub(r"^# Synopsis\s*", "", body).strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    assert len(paragraphs) == 3


def test_committed_json_samples_validate_against_contracts():
    input_data = json.loads((ROOT / "samples/input.json").read_text(encoding="utf-8"))
    output_data = json.loads((ROOT / "samples/output_full_run.json").read_text(encoding="utf-8"))
    Scenario.model_validate(input_data)
    Plan.model_validate(output_data)


@pytest.mark.xfail(reason=REGENERATION_REASON)
def test_io_definitions_are_current_and_trace_matches_input():
    rendered, trace_matches = generate()
    assert (ROOT / "docs/IO_DEFINITIONS.md").read_text(encoding="utf-8") == rendered
    assert trace_matches


@pytest.mark.xfail(reason=REGENERATION_REASON)
def test_readme_checker_command_uses_existing_matching_artifacts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"python scripts/check_trace\.py ([^\n]+)", readme)
    assert match
    args = match.group(1).split()
    paths = [args[0], args[1], args[args.index("--scenario") + 1]]
    assert all((ROOT / path).is_file() for path in paths)
    _, trace_matches = generate()
    assert trace_matches
