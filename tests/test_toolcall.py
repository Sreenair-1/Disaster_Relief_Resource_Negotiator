from lib.toolcall import call_tool


def test_unknown_tool_is_reported(sample_scenario, tracer):
    result = call_tool("missing", sample_scenario, tracer)
    assert not result.ok
    assert "Unknown tool" in result.error
