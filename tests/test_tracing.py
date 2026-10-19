import json


def test_tracer_writes_memory_and_jsonl(tracer):
    event = tracer.log("a", "b", "test", {"x": 1})
    assert tracer.events == [event]
    assert json.loads(tracer.path.read_text())["payload"] == {"x": 1}
