from lib.scoring import over_subscribed, score_claim
from lib.schemas import Claim


def test_score_is_deterministic(sample_scenario):
    claim = Claim(agent="medical", resources={"boats": 2}, people_affected=12,
                  urgency=10, time_window_hrs=2, justification="critical",
                  fallback_option="ambulance")
    assert score_claim(claim, sample_scenario) == 18


def test_over_subscription(sample_scenario):
    claims = [
        Claim(agent="a", resources={"boats": 2}, people_affected=1, urgency=1,
              time_window_hrs=1, justification="x", fallback_option="y"),
        Claim(agent="b", resources={"boats": 2}, people_affected=1, urgency=1,
              time_window_hrs=1, justification="x", fallback_option="y"),
    ]
    assert over_subscribed(claims, sample_scenario) == {"boats": 1}
