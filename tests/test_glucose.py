"""
Tests for glucose unit helpers and parse_dexcom_reading.

Covers: convert_mg_dl, format_glucose, unit_label, _REGION_CHOICES/_REGION_LABELS,
and parse_dexcom_reading returning mg_dl (not value/unit).
"""
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


class TestConvertMgDl(unittest.TestCase):

    def test_mmol_conversion(self):
        # 100 * 0.0555 = 5.55, round to 1dp -> 5.5 (Python banker's rounding)
        result = main.convert_mg_dl(100, main._UNITS_MMOL)
        self.assertAlmostEqual(result, 5.5, places=1)

    def test_mgdl_passthrough(self):
        result = main.convert_mg_dl(100, main._UNITS_MGDL)
        self.assertEqual(result, 100)
        self.assertIsInstance(result, int)

    def test_none_returns_none(self):
        self.assertIsNone(main.convert_mg_dl(None, main._UNITS_MMOL))
        self.assertIsNone(main.convert_mg_dl(None, main._UNITS_MGDL))

    def test_sentinel_400_mmol(self):
        # 400 mg/dL * 0.0555 = 22.2 mmol/L
        result = main.convert_mg_dl(400, main._UNITS_MMOL)
        self.assertAlmostEqual(result, 22.2, places=1)

    def test_sentinel_40_mmol(self):
        # 40 mg/dL * 0.0555 = 2.2 mmol/L
        result = main.convert_mg_dl(40, main._UNITS_MMOL)
        self.assertAlmostEqual(result, 2.2, places=1)

    def test_sentinel_400_mgdl(self):
        result = main.convert_mg_dl(400, main._UNITS_MGDL)
        self.assertEqual(result, 400)

    def test_sentinel_40_mgdl(self):
        result = main.convert_mg_dl(40, main._UNITS_MGDL)
        self.assertEqual(result, 40)

    def test_mmol_result_is_float(self):
        result = main.convert_mg_dl(90, main._UNITS_MMOL)
        self.assertIsInstance(result, float)


class TestFormatGlucose(unittest.TestCase):

    def test_none_returns_dashes(self):
        self.assertEqual(main.format_glucose(None, main._UNITS_MMOL), "---")
        self.assertEqual(main.format_glucose(None, main._UNITS_MGDL), "---")

    def test_mmol_one_decimal(self):
        self.assertEqual(main.format_glucose(5.0, main._UNITS_MMOL), "5.0")
        self.assertEqual(main.format_glucose(14.5, main._UNITS_MMOL), "14.5")

    def test_mmol_always_one_decimal(self):
        # Even a whole number mmol value shows exactly one decimal place
        self.assertEqual(main.format_glucose(7.0, main._UNITS_MMOL), "7.0")

    def test_mgdl_integer_string(self):
        self.assertEqual(main.format_glucose(101, main._UNITS_MGDL), "101")
        self.assertEqual(main.format_glucose(70, main._UNITS_MGDL), "70")

    def test_mgdl_no_decimal(self):
        # mg/dL must never show a decimal point
        result = main.format_glucose(100, main._UNITS_MGDL)
        self.assertNotIn(".", result)


class TestUnitLabel(unittest.TestCase):

    def test_mmol_label(self):
        self.assertEqual(main.unit_label(main._UNITS_MMOL), "mmol/L")

    def test_mgdl_label(self):
        self.assertEqual(main.unit_label(main._UNITS_MGDL), "mg/dL")

    def test_unknown_falls_back_to_mmol(self):
        self.assertEqual(main.unit_label("unknown"), "mmol/L")

    def test_labels_use_plain_hyphen(self):
        # Plain keyboard hyphen only - no Unicode minus or en/em dashes
        for label in main._UNIT_LABELS.values():
            if "-" in label:
                self.assertIn("-", label)  # standard hyphen (U+002D) is fine
            # Reject Unicode minus / en dash / em dash
            self.assertNotIn("−", label)
            self.assertNotIn("–", label)
            self.assertNotIn("—", label)


class TestRegionLabels(unittest.TestCase):

    def test_region_choices_contains_three(self):
        self.assertEqual(len(main._REGION_CHOICES), 3)

    def test_region_choices_values(self):
        values = [v for v, _ in main._REGION_CHOICES]
        self.assertIn("ous", values)
        self.assertIn("us", values)
        self.assertIn("jp", values)

    def test_region_choices_labels(self):
        labels = [l for _, l in main._REGION_CHOICES]
        self.assertIn("Rest of the world", labels)
        self.assertIn("United States", labels)
        self.assertIn("Japan", labels)

    def test_region_choices_order(self):
        # "ous" is first (the default)
        self.assertEqual(main._REGION_CHOICES[0][0], "ous")

    def test_region_labels_dict(self):
        self.assertEqual(main._REGION_LABELS["ous"], "Rest of the world")
        self.assertEqual(main._REGION_LABELS["us"], "United States")
        self.assertEqual(main._REGION_LABELS["jp"], "Japan")

    def test_no_outside_us_label(self):
        for _, label in main._REGION_CHOICES:
            self.assertNotEqual(label, "Outside US")


class TestParseDexcomReading(unittest.TestCase):

    def _make_raw(self, value=180, trend="Flat", wt="Date(1700000000000+0000)"):
        return [{"Value": value, "Trend": trend, "WT": wt}]

    def test_returns_mg_dl(self):
        result = main.parse_dexcom_reading(self._make_raw(180))
        self.assertIsNotNone(result)
        self.assertEqual(result["mg_dl"], 180)

    def test_no_value_key(self):
        result = main.parse_dexcom_reading(self._make_raw(180))
        self.assertNotIn("value", result)

    def test_no_unit_key(self):
        result = main.parse_dexcom_reading(self._make_raw(180))
        self.assertNotIn("unit", result)

    def test_mg_dl_is_int(self):
        result = main.parse_dexcom_reading(self._make_raw(100))
        self.assertIsInstance(result["mg_dl"], int)

    def test_trend_mapped(self):
        result = main.parse_dexcom_reading(self._make_raw(180, trend="SingleUp"))
        self.assertEqual(result["trend"], "singleUp")

    def test_unknown_trend_is_none(self):
        result = main.parse_dexcom_reading(self._make_raw(180, trend="Bogus"))
        self.assertIsNone(result["trend"])

    def test_ts_ms_parsed(self):
        result = main.parse_dexcom_reading(self._make_raw())
        self.assertEqual(result["ts_ms"], 1700000000000)

    def test_empty_list_returns_none(self):
        self.assertIsNone(main.parse_dexcom_reading([]))

    def test_none_returns_none(self):
        self.assertIsNone(main.parse_dexcom_reading(None))

    def test_sentinel_400_stored_raw(self):
        # Sentinel high (400 mg/dL) is stored as-is, not pre-converted
        result = main.parse_dexcom_reading(self._make_raw(400))
        self.assertEqual(result["mg_dl"], 400)

    def test_sentinel_40_stored_raw(self):
        result = main.parse_dexcom_reading(self._make_raw(40))
        self.assertEqual(result["mg_dl"], 40)


if __name__ == "__main__":
    unittest.main()
