"""Tests for settings load/validate/save/serialise, step_value, alert helpers."""
import sys
import os
import json
import tempfile
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


class TestDefaultSettings(unittest.TestCase):

    def test_defaults_from_secrets_stub(self):
        defaults = main.default_settings()
        self.assertEqual(defaults["wifi_ssid"], "TestSSID")
        self.assertEqual(defaults["wifi_password"], "TestPass")
        self.assertEqual(defaults["dexcom_account_id"], "test-account-id")
        self.assertEqual(defaults["dexcom_password"], "test-dex-pass")
        self.assertEqual(defaults["dexcom_region"], "ous")

    def test_defaults_numeric(self):
        defaults = main.default_settings()
        self.assertAlmostEqual(defaults["backlight"], 0.5)
        self.assertEqual(defaults["units"], main._UNITS_MMOL)
        self.assertAlmostEqual(defaults["alert_low"], 4.0)
        self.assertAlmostEqual(defaults["alert_high"], 14.0)
        self.assertTrue(defaults["led_alerts"])

    def test_no_stale_minutes_key(self):
        defaults = main.default_settings()
        self.assertNotIn("stale_minutes", defaults)

    def test_stale_limit_ms_constant(self):
        # Hard-coded to 6 minutes; no longer a configurable setting.
        self.assertEqual(main._STALE_LIMIT_MS, 6 * 60 * 1000)

    def test_stale_limit_ms_no_function(self):
        self.assertFalse(hasattr(main, "stale_limit_ms"))


class TestValidateSettings(unittest.TestCase):

    def test_non_dict_returns_defaults(self):
        result = main.validate_settings("bad")
        defaults = main.default_settings()
        self.assertAlmostEqual(result["backlight"], defaults["backlight"])
        self.assertEqual(result["units"], defaults["units"])

    def test_none_returns_defaults(self):
        result = main.validate_settings(None)
        defaults = main.default_settings()
        self.assertEqual(result["backlight"], defaults["backlight"])

    def test_unknown_keys_dropped(self):
        result = main.validate_settings({"unknown_key": "value", "backlight": 0.7})
        self.assertNotIn("unknown_key", result)
        self.assertAlmostEqual(result["backlight"], 0.7, places=1)

    def test_invalid_region_becomes_default(self):
        result = main.validate_settings({"dexcom_region": "invalid"})
        self.assertEqual(result["dexcom_region"], "ous")

    def test_valid_regions(self):
        for region in ("us", "ous", "jp"):
            result = main.validate_settings({"dexcom_region": region})
            self.assertEqual(result["dexcom_region"], region)

    def test_valid_units(self):
        result = main.validate_settings({"units": main._UNITS_MGDL})
        self.assertEqual(result["units"], main._UNITS_MGDL)
        # Absent alert keys must take the resolved unit's defaults (70/180 as ints).
        self.assertEqual(result["alert_low"], 70)
        self.assertEqual(result["alert_high"], 180)
        self.assertIsInstance(result["alert_low"], int)
        self.assertIsInstance(result["alert_high"], int)

    def test_valid_units_mgdl_explicit_alerts_win(self):
        # Explicit alert values in raw override the unit-default seeds.
        result = main.validate_settings({"units": main._UNITS_MGDL, "alert_low": 80, "alert_high": 200})
        self.assertEqual(result["units"], main._UNITS_MGDL)
        self.assertEqual(result["alert_low"], 80)
        self.assertEqual(result["alert_high"], 200)

    def test_invalid_units_becomes_default(self):
        result = main.validate_settings({"units": "imperial"})
        self.assertEqual(result["units"], main._UNITS_MMOL)

    def test_alert_low_ge_alert_high_resets_both(self):
        result = main.validate_settings({"alert_low": 10.0, "alert_high": 8.0})
        defaults = main.default_settings()
        self.assertAlmostEqual(result["alert_low"], defaults["alert_low"])
        self.assertAlmostEqual(result["alert_high"], defaults["alert_high"])

    def test_alert_low_equal_alert_high_resets_both(self):
        result = main.validate_settings({"alert_low": 8.0, "alert_high": 8.0})
        defaults = main.default_settings()
        self.assertAlmostEqual(result["alert_low"], defaults["alert_low"])
        self.assertAlmostEqual(result["alert_high"], defaults["alert_high"])

    def test_alert_mgdl_units_uses_int_ranges(self):
        # mg/dL alert_low range is 40-180 step 5
        result = main.validate_settings({"units": main._UNITS_MGDL, "alert_low": 73, "alert_high": 200})
        self.assertEqual(result["units"], main._UNITS_MGDL)
        self.assertEqual(result["alert_low"], 75)   # snapped to step 5
        self.assertEqual(result["alert_high"], 200)

    def test_units_first_alert_pair_reset_uses_new_unit_defaults(self):
        # Supply invalid pair with mgdl units; both should reset to mgdl defaults
        result = main.validate_settings({"units": main._UNITS_MGDL, "alert_low": 200, "alert_high": 100})
        defs = main.alert_defaults(main._UNITS_MGDL)
        self.assertEqual(result["alert_low"], defs["alert_low"])
        self.assertEqual(result["alert_high"], defs["alert_high"])

    def test_stale_minutes_in_raw_is_ignored(self):
        # Old settings files may contain stale_minutes; it should be silently dropped
        result = main.validate_settings({"stale_minutes": 9, "backlight": 0.7})
        self.assertNotIn("stale_minutes", result)
        self.assertAlmostEqual(result["backlight"], 0.7, places=1)

    def test_float_clamp(self):
        result = main.validate_settings({"backlight": 99.9})
        self.assertAlmostEqual(result["backlight"], 1.0)
        result = main.validate_settings({"backlight": -1.0})
        self.assertAlmostEqual(result["backlight"], 0.1)

    def test_int_clamp_backlight_snapped(self):
        # alert_low has no stale_minutes to test, use a valid numeric field
        result = main.validate_settings({"backlight": 99.9})
        self.assertAlmostEqual(result["backlight"], 1.0)

    def test_bool_coercion_string(self):
        result = main.validate_settings({"led_alerts": "1"})
        self.assertTrue(result["led_alerts"])
        result = main.validate_settings({"led_alerts": "0"})
        self.assertFalse(result["led_alerts"])
        result = main.validate_settings({"led_alerts": "on"})
        self.assertTrue(result["led_alerts"])

    def test_string_truncation(self):
        result = main.validate_settings({"wifi_ssid": "A" * 100})
        self.assertEqual(len(result["wifi_ssid"]), 32)

    def test_numeric_coercion_from_string(self):
        result = main.validate_settings({"backlight": "0.8"})
        self.assertAlmostEqual(result["backlight"], 0.8, places=1)


class TestAlertHelpers(unittest.TestCase):

    def test_alert_spec_mmol_low(self):
        kind, (lo, hi, step), default = main.alert_spec("alert_low", main._UNITS_MMOL)
        self.assertEqual(kind, "float")
        self.assertAlmostEqual(lo, 2.0)
        self.assertAlmostEqual(hi, 10.0)
        self.assertAlmostEqual(step, 0.1)
        self.assertAlmostEqual(default, 4.0)

    def test_alert_spec_mmol_high(self):
        kind, (lo, hi, step), default = main.alert_spec("alert_high", main._UNITS_MMOL)
        self.assertEqual(kind, "float")
        self.assertAlmostEqual(default, 14.0)

    def test_alert_spec_mgdl_low(self):
        kind, (lo, hi, step), default = main.alert_spec("alert_low", main._UNITS_MGDL)
        self.assertEqual(kind, "int")
        self.assertEqual(lo, 40)
        self.assertEqual(hi, 180)
        self.assertEqual(step, 5)
        self.assertEqual(default, 70)

    def test_alert_spec_mgdl_high(self):
        kind, (lo, hi, step), default = main.alert_spec("alert_high", main._UNITS_MGDL)
        self.assertEqual(kind, "int")
        self.assertEqual(default, 180)

    def test_alert_defaults_mmol(self):
        defs = main.alert_defaults(main._UNITS_MMOL)
        self.assertAlmostEqual(defs["alert_low"], 4.0)
        self.assertAlmostEqual(defs["alert_high"], 14.0)

    def test_alert_defaults_mgdl(self):
        defs = main.alert_defaults(main._UNITS_MGDL)
        self.assertEqual(defs["alert_low"], 70)
        self.assertEqual(defs["alert_high"], 180)

    def test_setting_spec_backlight(self):
        settings = main.default_settings()
        kind, extra = main.setting_spec("backlight", settings)
        self.assertEqual(kind, "float")
        lo, hi, step = extra
        self.assertAlmostEqual(lo, 0.1)
        self.assertAlmostEqual(hi, 1.0)

    def test_setting_spec_alert_mmol(self):
        settings = main.default_settings()
        settings["units"] = main._UNITS_MMOL
        kind, (lo, hi, step) = main.setting_spec("alert_low", settings)
        self.assertEqual(kind, "float")
        self.assertAlmostEqual(lo, 2.0)

    def test_setting_spec_alert_mgdl(self):
        settings = main.default_settings()
        settings["units"] = main._UNITS_MGDL
        kind, (lo, hi, step) = main.setting_spec("alert_low", settings)
        self.assertEqual(kind, "int")
        self.assertEqual(lo, 40)

    def test_apply_units_change_resets_thresholds(self):
        settings = main.default_settings()
        settings["alert_low"]  = 5.0
        settings["alert_high"] = 10.0
        result = main.apply_units_change(settings, main._UNITS_MGDL)
        self.assertEqual(result["units"], main._UNITS_MGDL)
        self.assertEqual(result["alert_low"], 70)
        self.assertEqual(result["alert_high"], 180)

    def test_apply_units_change_same_unit_preserves_custom_thresholds(self):
        settings = main.default_settings()
        settings["alert_low"]  = 9.0
        settings["alert_high"] = 9.5
        result = main.apply_units_change(settings, main._UNITS_MMOL)
        self.assertAlmostEqual(result["alert_low"], 9.0)
        self.assertAlmostEqual(result["alert_high"], 9.5)

    def test_apply_units_change_does_not_mutate_input(self):
        settings = main.default_settings()
        settings["alert_low"]  = 5.0
        settings["alert_high"] = 10.0
        original_low  = settings["alert_low"]
        original_high = settings["alert_high"]
        main.apply_units_change(settings, main._UNITS_MGDL)
        # Input dict must not have been modified
        self.assertAlmostEqual(settings["alert_low"], original_low)
        self.assertAlmostEqual(settings["alert_high"], original_high)


class TestSettingsChanged(unittest.TestCase):

    def test_identical_returns_false(self):
        a = main.default_settings()
        b = main.default_settings()
        self.assertFalse(main.settings_changed(a, b))

    def test_different_str(self):
        a = main.default_settings()
        b = main.default_settings()
        b["wifi_ssid"] = "different"
        self.assertTrue(main.settings_changed(a, b))

    def test_float_tolerance(self):
        a = main.default_settings()
        b = main.default_settings()
        # 0.5 vs 0.500001 - within round(..., 2) tolerance
        b["backlight"] = 0.500001
        self.assertFalse(main.settings_changed(a, b))

    def test_float_different(self):
        a = main.default_settings()
        b = main.default_settings()
        b["backlight"] = 0.6
        self.assertTrue(main.settings_changed(a, b))


class TestSerialiseSettings(unittest.TestCase):

    def test_contains_exactly_schema_keys(self):
        settings = main.default_settings()
        s = main.serialise_settings(settings)
        parsed = json.loads(s)
        self.assertEqual(set(parsed.keys()), set(main._SETTINGS_SCHEMA.keys()))

    def test_round_trip(self):
        settings = main.default_settings()
        settings["backlight"] = 0.7
        s = main.serialise_settings(settings)
        parsed = json.loads(s)
        self.assertAlmostEqual(parsed["backlight"], 0.7, places=1)

    def test_no_stale_minutes_in_serialised(self):
        settings = main.default_settings()
        s = main.serialise_settings(settings)
        parsed = json.loads(s)
        self.assertNotIn("stale_minutes", parsed)


class TestLoadSaveSettings(unittest.TestCase):

    def test_missing_file_returns_defaults(self):
        result = main.load_settings("/nonexistent/path/settings.json")
        defaults = main.default_settings()
        self.assertAlmostEqual(result["backlight"], defaults["backlight"])

    def test_corrupt_file_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{{{")
            path = f.name
        try:
            result = main.load_settings(path)
            defaults = main.default_settings()
            self.assertAlmostEqual(result["backlight"], defaults["backlight"])
        finally:
            os.unlink(path)

    def test_wrong_type_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('"just a string"')
            path = f.name
        try:
            result = main.load_settings(path)
            defaults = main.default_settings()
            self.assertAlmostEqual(result["backlight"], defaults["backlight"])
        finally:
            os.unlink(path)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path     = os.path.join(d, "settings.json")
            tmp_path = os.path.join(d, "settings.json.tmp")
            settings = main.default_settings()
            settings["backlight"] = 0.8
            settings["units"]     = main._UNITS_MGDL
            ok = main.save_settings(settings, path=path, tmp_path=tmp_path)
            self.assertTrue(ok)
            loaded = main.load_settings(path)
            self.assertAlmostEqual(loaded["backlight"], 0.8, places=1)
            self.assertEqual(loaded["units"], main._UNITS_MGDL)

    def test_save_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path     = os.path.join(d, "settings.json")
            tmp_path = os.path.join(d, "settings.json.tmp")
            s1 = main.default_settings()
            s1["backlight"] = 0.3
            main.save_settings(s1, path=path, tmp_path=tmp_path)
            s2 = main.default_settings()
            s2["backlight"] = 0.9
            ok = main.save_settings(s2, path=path, tmp_path=tmp_path)
            self.assertTrue(ok)
            loaded = main.load_settings(path)
            self.assertAlmostEqual(loaded["backlight"], 0.9, places=1)

    def test_old_file_with_stale_minutes_loads_without_error(self):
        # Files from before R2 may contain stale_minutes; it is silently ignored
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"stale_minutes": 9, "backlight": 0.7}, f)
            loaded = main.load_settings(path)
            self.assertNotIn("stale_minutes", loaded)
            self.assertAlmostEqual(loaded["backlight"], 0.7, places=1)


class TestStepValue(unittest.TestCase):

    def test_bool_toggle(self):
        self.assertFalse(main.step_value("bool", True, +1, None))
        self.assertTrue(main.step_value("bool", False, +1, None))

    def test_choice_cycle_forward(self):
        choices = ("ous", "us", "jp")
        result = main.step_value("choice", "ous", +1, choices)
        self.assertEqual(result, "us")
        result = main.step_value("choice", "jp", +1, choices)
        self.assertEqual(result, "ous")  # wraps

    def test_choice_cycle_backward(self):
        choices = ("ous", "us", "jp")
        result = main.step_value("choice", "ous", -1, choices)
        self.assertEqual(result, "jp")  # wraps

    def test_float_step(self):
        result = main.step_value("float", 0.5, +1, (0.1, 1.0, 0.1))
        self.assertAlmostEqual(result, 0.6, places=1)

    def test_float_clamp_hi(self):
        result = main.step_value("float", 1.0, +1, (0.1, 1.0, 0.1))
        self.assertAlmostEqual(result, 1.0, places=1)

    def test_float_clamp_lo(self):
        result = main.step_value("float", 0.1, -1, (0.1, 1.0, 0.1))
        self.assertAlmostEqual(result, 0.1, places=1)

    def test_int_step(self):
        result = main.step_value("int", 70, +1, (40, 180, 5))
        self.assertEqual(result, 75)

    def test_int_clamp(self):
        result = main.step_value("int", 180, +1, (40, 180, 5))
        self.assertEqual(result, 180)
        result = main.step_value("int", 40, -1, (40, 180, 5))
        self.assertEqual(result, 40)

    def test_float_drift(self):
        # Repeated stepping should not drift beyond clamp due to floating-point accumulation
        val = 0.1
        for _ in range(10):
            val = main.step_value("float", val, +1, (0.1, 1.0, 0.1))
        self.assertAlmostEqual(val, 1.0, places=1)

    def test_unit_choice_cycle(self):
        choices = main._UNIT_CHOICES
        result = main.step_value("choice", main._UNITS_MMOL, +1, choices)
        self.assertEqual(result, main._UNITS_MGDL)
        result = main.step_value("choice", main._UNITS_MGDL, +1, choices)
        self.assertEqual(result, main._UNITS_MMOL)  # wraps


if __name__ == "__main__":
    unittest.main()
