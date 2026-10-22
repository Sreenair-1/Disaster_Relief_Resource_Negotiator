from lib.scoring import over_subscribed, score_claim
from lib.schemas import Claim, Scenario


def test_score_is_deterministic(sample_scenario):
    claim = Claim(agent="medical", resources={"boats": 2}, people_affected=12,
                  urgency=10, time_window_hrs=2, justification="critical",
                  fallback_option="ambulance")
    assert score_claim(claim, sample_scenario) == 180


def test_blocked_unrelated_zone_does_not_zero_claim_score(sample_scenario):
    sample_scenario.zones[0].road_access = 0.0
    claim = Claim(agent="medical", resources={"boats": 1}, people_affected=12,
                  urgency=10, time_window_hrs=2, justification="critical patients",
                  fallback_option="transfer")

    assert score_claim(claim, sample_scenario) == 360


def test_unknown_zone_uses_people_weighted_access():
    scenario = Scenario.model_validate({
        "disaster_type": "flood",
        "zones": [
            {"name": "Large", "population": 900, "road_access": 0.0},
            {"name": "Small", "population": 100, "road_access": 1.0},
        ],
    })
    claim = Claim(agent="logistics", resources={"trucks": 1}, people_affected=100,
                  urgency=5, time_window_hrs=1, justification="Supply routes",
                  fallback_option="Use alternate routes")

    assert score_claim(claim, scenario) == 50


def test_over_subscription(sample_scenario):
    claims = [
        Claim(agent="a", resources={"boats": 2}, people_affected=1, urgency=1,
              time_window_hrs=1, justification="x", fallback_option="y"),
        Claim(agent="b", resources={"boats": 2}, people_affected=1, urgency=1,
              time_window_hrs=1, justification="x", fallback_option="y"),
    ]
    assert over_subscribed(claims, sample_scenario) == {"boats": 1}
