from lib.schemas import Claim, MediatorAction, NegotiationState


def test_claim_validation_and_state(sample_scenario):
    claim = Claim(agent="medical", people_affected=1, urgency=10, time_window_hrs=1,
                  justification="urgent", fallback_option="none")
    state = NegotiationState(scenario=sample_scenario, claims=[claim])
    assert state.claims[0].agent == "medical"
    assert MediatorAction.finalize.value == "finalize"
