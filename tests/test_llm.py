from lib.llm import StubLLM
from lib.schemas import Claim


def test_stub_repairs_json_string():
    llm = StubLLM(['{"agent":"x","people_affected":2,"urgency":5,"time_window_hrs":1,"justification":"j","fallback_option":"f"}'])
    claim = llm.complete_json("system", "user", Claim)
    assert claim.people_affected == 2
