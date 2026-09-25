from interdictor.jam import JammerController

CFG = {
    "profiles": [
        {
            "mode_name": "JAM_GNSS_L1",
            "description": "test",
            "centre_frequency": 1575420000.0,
            "bandwidth": 4000000.0,
            "tx_power_dbm": 30.0,
        },
        {
            "mode_name": "JAM_UAV_CONTROL",
            "description": "test",
            "centre_frequency": 2437000000.0,
            "bandwidth": 20000000.0,
            "tx_power_dbm": 30.0,
        },
    ],
}


def test_starts_in_default_mode_not_jamming():
    controller = JammerController(CFG, default_mode="STANDBY")
    assert controller.mode == "STANDBY"
    assert controller.is_jamming() is False
    assert controller.active_profile is None


def test_switch_to_known_mode_engages_jamming():
    controller = JammerController(CFG, default_mode="STANDBY")
    assert controller.switch_to("JAM_GNSS_L1") is True
    assert controller.mode == "JAM_GNSS_L1"
    assert controller.is_jamming() is True
    assert controller.active_profile.centre_frequency == 1575420000.0


def test_switch_to_unknown_mode_is_rejected_and_leaves_mode_unchanged():
    controller = JammerController(CFG, default_mode="STANDBY")
    controller.switch_to("JAM_UAV_CONTROL")
    assert controller.switch_to("JAM_NONEXISTENT") is False
    assert controller.mode == "JAM_UAV_CONTROL"


def test_revert_to_default_stops_jamming():
    controller = JammerController(CFG, default_mode="STANDBY")
    controller.switch_to("JAM_GNSS_L1")
    controller.revert_to_default()
    assert controller.mode == "STANDBY"
    assert controller.is_jamming() is False


def test_known_modes_includes_default_and_all_profiles():
    controller = JammerController(CFG, default_mode="STANDBY")
    assert controller.known_modes() == {"STANDBY", "JAM_GNSS_L1", "JAM_UAV_CONTROL"}
