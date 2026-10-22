from lib.schemas import Claim, MediatorAction, NegotiationState, Scenario, Zone


def test_claim_validation_and_state(sample_scenario):
    claim = Claim(agent="medical", people_affected=1, urgency=10, time_window_hrs=1,
                  justification="urgent", fallback_option="none")
    state = NegotiationState(scenario=sample_scenario, claims=[claim])
    assert state.claims[0].agent == "medical"
    assert MediatorAction.finalize.value == "finalize"


def test_legacy_hospital_names_become_unverified_operation_facilities():
    scenario = Scenario(disaster_type="flood", zones=[Zone(name="A", population=1)],
                        hospitals=["Old Hospital"])
    assert scenario.hospitals == ["Old Hospital"]
    hospital = scenario.operations.hospitals[0]
    assert hospital.name == "Old Hospital"
    assert hospital.icu_beds_free is None
    assert hospital.accepts_transfers is None
    assert hospital.travel_hours is None


def test_hospital_objects_are_accepted_in_operations():
    scenario = Scenario.model_validate({
        "disaster_type": "flood", "zones": [{"name": "A", "population": 1}],
        "operations": {"hospitals": [{"name": "Hospital", "icu_beds_free": 2,
                                         "accepts_transfers": True, "travel_hours": 1.5}]},
    })
    assert scenario.hospitals == ["Hospital"]
    assert scenario.operations.hospitals[0].icu_beds_free == 2
