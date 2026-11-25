import unittest

from custom_components.peak_guard.mode_manager import BatteryModeManager


class BatteryModeManagerTests(unittest.TestCase):
    def select(self, manager, **overrides):
        parameters = {
            "interval_id": 1,
            "discharge_required": False,
            "discharge_active": False,
            "battery_soc": 90.0,
            "grid_charge_soc_limit": 70.0,
            "max_charge_soc": 90.0,
            "solar_surplus_w": 0.0,
            "available_peak_headroom_w": 0.0,
        }
        parameters.update(overrides)
        return manager.select_mode(**parameters)

    def test_soc_above_maximum_selects_self_consumption(self):
        manager = BatteryModeManager()

        mode, reason = self.select(manager, battery_soc=90.1)

        self.assertEqual(mode, "self_consumption")
        self.assertEqual(reason, "max_charge_soc_reached")

    def test_soc_at_maximum_selects_self_consumption(self):
        manager = BatteryModeManager()

        mode, reason = self.select(manager, battery_soc=90.0)

        self.assertEqual(mode, "self_consumption")
        self.assertEqual(reason, "max_charge_soc_reached")

    def test_peak_protection_takes_priority_above_maximum(self):
        manager = BatteryModeManager()

        mode, reason = self.select(
            manager,
            battery_soc=90.1,
            discharge_required=True,
            discharge_active=True,
        )

        self.assertEqual(mode, "discharge")
        self.assertEqual(reason, "peak_protection")

    def test_solar_charge_stops_when_surplus_is_too_low(self):
        manager = BatteryModeManager()
        self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=300.0,
        )

        mode, _ = self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=100.0,
        )

        self.assertNotEqual(mode, "solar_charge")
        self.assertFalse(manager.solar_charge_latched)

    def test_solar_charge_latch_survives_dip_below_start_threshold(self):
        manager = BatteryModeManager()
        self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=300.0,
        )

        mode, reason = self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=150.0,
        )

        self.assertEqual(mode, "solar_charge")
        self.assertEqual(reason, "solar_charge_hysteresis")

    def test_solar_surplus_hysteresis_configures_stop_threshold(self):
        manager = BatteryModeManager()
        self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=300.0,
            solar_surplus_hysteresis_w=50.0,
        )

        mode, _ = self.select(
            manager,
            battery_soc=50.0,
            solar_surplus_w=149.0,
            solar_surplus_hysteresis_w=50.0,
        )

        self.assertNotEqual(mode, "solar_charge")
        self.assertFalse(manager.solar_charge_latched)

    def test_solar_surplus_filter_smooths_a_single_dip(self):
        manager = BatteryModeManager()

        initial = manager.filter_solar_surplus(
            300.0,
            now_ts=0.0,
            filter_time_s=10.0,
        )
        after_dip = manager.filter_solar_surplus(
            0.0,
            now_ts=5.0,
            filter_time_s=10.0,
        )

        self.assertEqual(initial, 300.0)
        self.assertAlmostEqual(after_dip, 200.0)

    def test_native_mode_is_retained_for_brief_interval_boundary_gap(self):
        manager = BatteryModeManager()
        self.select(
            manager,
            interval_id=1,
            battery_soc=50.0,
            solar_surplus_w=300.0,
        )

        self.assertTrue(
            manager.should_retain_native_mode(
                sample_timestamp=1799.0,
                now_timestamp=1804.0,
            )
        )
        self.assertFalse(
            manager.should_retain_native_mode(
                sample_timestamp=1799.0,
                now_timestamp=1810.0,
            )
        )

    def test_restored_default_mode_migrates_to_self_consumption(self):
        manager = BatteryModeManager()

        manager.restore_state({"mode": "default"})

        self.assertEqual(manager.mode, "self_consumption")


if __name__ == "__main__":
    unittest.main()
