import json
from pathlib import Path

import pytest

from lib.llm import StubLLM
from lib.schemas import Scenario
from lib.tracing import Tracer


@pytest.fixture
def sample_scenario():
    return Scenario.model_validate_json(
        Path(__file__).parents[1].joinpath("data", "scenario.json").read_text()
    )


@pytest.fixture
def stub_llm():
    return StubLLM()


@pytest.fixture
def tracer(tmp_path):
    return Tracer(tmp_path / "trace.jsonl")
