"""Tests for menu state machine, HoldDetector, and format_setting."""
import sys
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


def _initial_state():
    return {
        "cursor":     0,
        "editing":    False,
        "edit_value": None,
        "action":     None,
        "closed":     False,
        "commit":     None,
    }


def _settings():
    s = main.default_settings()
    return s


class TestMenuItems(unittest.TestCase):

    def test_no_stale_minutes_item(self):
        keys = [k for k, _ in main._MENU_ITEMS]
        self.assertNotIn("stale_minutes", keys)

    def test_units_item_at_cursor_1(self):
        self.assertEqual(main._MENU_ITEMS[1][0], "units")

    def test_alert_low_at_cursor_2(self):
        self.assertEqual(main._MENU_ITEMS[2][0], "alert_low")

    def test_alert_high_at_cursor_3(self):
        self.assertEqual(main._MENU_ITEMS[3][0], "alert_high")

    def test_region_label_is_region(self):
        region_item = next(item for item in main._MENU_ITEMS if item[0] == "dexcom_region")
        self.assertEqual(region_item[1], "Region")

    def test_alert_low_label(self):
        low_item = next(item for item in main._MENU_ITEMS if item[0] == "alert_low")
        self.assertEqual(low_item[1], "Alert low")

    def test_alert_high_label(self):
        high_item = next(item for item in main._MENU_ITEMS if item[0] == "alert_high")
        self.assertEqual(high_item[1], "Alert high")


class TestMenuCursor(unittest.TestCase):

    def test_down_moves_cursor(self):
        state = _initial_state()
        state = main.menu_reduce(state, "down", _settings())
        self.assertEqual(state["cursor"], 1)

    def test_up_moves_cursor(self):
        state = _initial_state()
        state["cursor"] = 2
        state = main.menu_reduce(state, "up", _settings())
        self.assertEqual(state["cursor"], 1)

    def test_up_wraps_at_top(self):
        state = _initial_state()
        state["cursor"] = 0
        state = main.menu_reduce(state, "up", _settings())
        self.assertEqual(state["cursor"], main._MENU_ITEM_COUNT - 1)

    def test_down_wraps_at_bottom(self):
        state = _initial_state()
        state["cursor"] = main._MENU_ITEM_COUNT - 1
        state = main.menu_reduce(state, "down", _settings())
        self.assertEqual(state["cursor"], 0)


class TestMenuSelect(unittest.TestCase):

    def test_select_enters_editing(self):
        # cursor 0 = backlight (a settings key)
        state = _initial_state()
        state["cursor"] = 0
        state = main.menu_reduce(state, "select", _settings())
        self.assertTrue(state["editing"])
        self.assertIsNotNone(state["edit_value"])

    def test_select_on_action_sets_action(self):
        # Find "action:wifi" item index
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "action:wifi")
        state = _initial_state()
        state["cursor"] = idx
        state = main.menu_reduce(state, "select", _settings())
        self.assertEqual(state["action"], "wifi")
        self.assertFalse(state["editing"])


class TestMenuEditing(unittest.TestCase):

    def _enter_edit(self, cursor=0):
        state = _initial_state()
        state["cursor"] = cursor
        state = main.menu_reduce(state, "select", _settings())
        return state

    def test_up_steps_value(self):
        state = self._enter_edit(0)  # backlight
        old_val = state["edit_value"]
        state = main.menu_reduce(state, "up", _settings())
        self.assertNotAlmostEqual(state["edit_value"], old_val, places=5)

    def test_down_steps_value(self):
        state = self._enter_edit(0)  # backlight
        # Move up first to have room to go down
        state = main.menu_reduce(state, "up", _settings())
        up_val = state["edit_value"]
        state = main.menu_reduce(state, "down", _settings())
        self.assertLess(state["edit_value"], up_val + 0.001)

    def test_select_commits(self):
        state = self._enter_edit(0)  # backlight
        state = main.menu_reduce(state, "up", _settings())
        state = main.menu_reduce(state, "select", _settings())
        self.assertFalse(state["editing"])
        self.assertIsNotNone(state["commit"])
        key, val = state["commit"]
        self.assertEqual(key, "backlight")

    def test_back_discards_edit(self):
        state = self._enter_edit(0)
        state["edit_value"] = 99.9  # set to something crazy
        state = main.menu_reduce(state, "back", _settings())
        self.assertFalse(state["editing"])
        self.assertIsNone(state["commit"])

    def test_exit_from_editing_discards_and_closes(self):
        state = self._enter_edit(0)
        state = main.menu_reduce(state, "exit", _settings())
        self.assertFalse(state["editing"])
        self.assertTrue(state["closed"])
        self.assertIsNone(state["commit"])

    def test_bool_toggle(self):
        # led_alerts is a bool
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "led_alerts")
        state = _initial_state()
        state["cursor"] = idx
        s = _settings()
        s["led_alerts"] = True
        state = main.menu_reduce(state, "select", s)
        self.assertTrue(state["editing"])
        # up toggles
        state = main.menu_reduce(state, "up", s)
        self.assertFalse(state["edit_value"])

    def test_choice_cycles(self):
        # dexcom_region is a choice
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "dexcom_region")
        state = _initial_state()
        state["cursor"] = idx
        s = _settings()
        s["dexcom_region"] = "ous"
        state = main.menu_reduce(state, "select", s)
        state = main.menu_reduce(state, "up", s)
        self.assertIn(state["edit_value"], ("us", "jp", "ous"))
        self.assertNotEqual(state["edit_value"], "ous")

    def test_units_choice_cycles(self):
        # units is a choice between mmol and mgdl
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "units")
        state = _initial_state()
        state["cursor"] = idx
        s = _settings()
        s["units"] = main._UNITS_MMOL
        state = main.menu_reduce(state, "select", s)
        state = main.menu_reduce(state, "up", s)
        self.assertEqual(state["edit_value"], main._UNITS_MGDL)

    def test_alert_low_mmol_steps_float(self):
        # With mmol units, alert_low uses float steps of 0.1
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "alert_low")
        state = _initial_state()
        state["cursor"] = idx
        s = _settings()
        s["units"] = main._UNITS_MMOL
        s["alert_low"] = 4.0
        state = main.menu_reduce(state, "select", s)
        state = main.menu_reduce(state, "up", s)
        self.assertIsInstance(state["edit_value"], float)
        self.assertAlmostEqual(state["edit_value"], 4.1, places=1)

    def test_alert_low_mgdl_steps_int(self):
        # With mgdl units, alert_low uses int steps of 5
        idx = next(i for i, item in enumerate(main._MENU_ITEMS) if item[0] == "alert_low")
        state = _initial_state()
        state["cursor"] = idx
        s = _settings()
        s["units"] = main._UNITS_MGDL
        s["alert_low"] = 70
        state = main.menu_reduce(state, "select", s)
        state = main.menu_reduce(state, "up", s)
        self.assertIsInstance(state["edit_value"], int)
        self.assertEqual(state["edit_value"], 75)


class TestMenuBackClose(unittest.TestCase):

    def test_back_closes_menu(self):
        state = _initial_state()
        state = main.menu_reduce(state, "back", _settings())
        self.assertTrue(state["closed"])

    def test_exit_closes_menu(self):
        state = _initial_state()
        state = main.menu_reduce(state, "exit", _settings())
        self.assertTrue(state["closed"])


class TestHoldDetector(unittest.TestCase):

    def test_press_on_release_before_hold(self):
        hd = main.HoldDetector(1500)
        # Press at t=0
        self.assertIsNone(hd.update(True, 0))
        # Still held at t=500 (under threshold)
        self.assertIsNone(hd.update(True, 500))
        # Released at t=800 (before 1500ms)
        result = hd.update(False, 800)
        self.assertEqual(result, "press")

    def test_hold_fires_at_threshold(self):
        hd = main.HoldDetector(1500)
        hd.update(True, 0)
        hd.update(True, 1000)
        result = hd.update(True, 1500)
        self.assertEqual(result, "hold")

    def test_hold_fires_only_once(self):
        hd = main.HoldDetector(1500)
        hd.update(True, 0)
        hd.update(True, 1500)  # fires hold
        # Continued holding: should not fire again
        result = hd.update(True, 2000)
        self.assertIsNone(result)

    def test_no_event_when_not_pressed(self):
        hd = main.HoldDetector(1500)
        result = hd.update(False, 0)
        self.assertIsNone(result)

    def test_press_not_reported_after_hold(self):
        hd = main.HoldDetector(1500)
        hd.update(True, 0)
        hd.update(True, 1500)  # hold fires
        result = hd.update(False, 2000)  # release: no press reported
        self.assertIsNone(result)


class TestFormatSetting(unittest.TestCase):

    def test_backlight_as_percent(self):
        self.assertEqual(main.format_setting("backlight", 0.5), "50%")
        self.assertEqual(main.format_setting("backlight", 1.0), "100%")
        self.assertEqual(main.format_setting("backlight", 0.1), "10%")

    def test_region_label_full_name(self):
        self.assertEqual(main.format_setting("dexcom_region", "ous"), "Rest of the world")
        self.assertEqual(main.format_setting("dexcom_region", "us"), "United States")
        self.assertEqual(main.format_setting("dexcom_region", "jp"), "Japan")

    def test_units_label(self):
        self.assertEqual(main.format_setting("units", main._UNITS_MMOL), "mmol/L")
        self.assertEqual(main.format_setting("units", main._UNITS_MGDL), "mg/dL")

    def test_bool_on_off(self):
        self.assertEqual(main.format_setting("led_alerts", True), "On")
        self.assertEqual(main.format_setting("led_alerts", False), "Off")

    def test_float_one_decimal(self):
        self.assertEqual(main.format_setting("alert_low", 4.0), "4.0")
        self.assertEqual(main.format_setting("alert_high", 14.5), "14.5")

    def test_int_as_string(self):
        # int values (e.g. mgdl alert_low) render as plain integers
        self.assertEqual(main.format_setting("alert_low", 70), "70")


class TestDrawMenuNoOverlap(unittest.TestCase):
    """
    Verify that for every non-action menu row, in both cursor-on and editing
    states, the value text does not overlap the label text.

    hw_stubs.measure_text returns len*8*scale (conservative). Long-value rows
    (e.g. "Region"/"Rest of the world") trigger the scale-1 fallback and are
    drawn at y+4. The assertion covers every row regardless of y: value start x
    must be >= label end x (computed from the drawn scale of each element).
    """

    def setUp(self):
        main._settings.clear()
        main._settings.update(main.default_settings())
        self._text_calls = []
        self._orig_text = main.display.text
        calls = self._text_calls

        def _spy_text(text, x, y, wrap=main.WIDTH, scale=1):
            calls.append((text, x, y, scale))

        main.display.text = _spy_text

    def tearDown(self):
        main.display.text = self._orig_text

    def _check_row(self, row_index, editing, cursor=None):
        """
        Call draw_menu with the given cursor position (defaults to row_index) and
        assert that the label and value for row_index do not overlap horizontally.
        Returns the value's drawn scale so callers can verify which tier fired.
        """
        self._text_calls.clear()

        key, _label = main._MENU_ITEMS[row_index]
        if key.startswith("action:"):
            return None  # action rows have no value; skip

        if cursor is None:
            cursor = row_index

        is_cursor_on = (cursor == row_index)
        edit_val = main._settings.get(key) if (editing and is_cursor_on) else None
        state = {
            "cursor":     cursor,
            "editing":    editing and is_cursor_on,
            "edit_value": edit_val,
            "commit":     None,
        }
        main.draw_menu(state)

        pitch = 20
        row_y = 32 + row_index * pitch

        # Label is always drawn at x=4. In the current 2-tier layout the label
        # is always scale 2 at y=row_y; search both y and y+4 for safety.
        label_draws = [
            (text, x, y, sc) for text, x, y, sc in self._text_calls
            if y in (row_y, row_y + 4) and x == 4
        ]
        # Value is right-aligned (x != 4) at row_y or row_y+4.
        value_draws = [
            (text, x, y, sc) for text, x, y, sc in self._text_calls
            if y in (row_y, row_y + 4) and x != 4
        ]

        self.assertEqual(len(label_draws), 1,
                         "Expected one label draw for row %d editing=%s cursor=%d" % (
                             row_index, editing, cursor))
        self.assertEqual(len(value_draws), 1,
                         "Expected one value draw for row %d editing=%s cursor=%d" % (
                             row_index, editing, cursor))

        _label_text, label_x, _label_y, label_scale = label_draws[0]
        _val_text, val_x, _val_y, val_scale = value_draws[0]

        # Always assert horizontal non-overlap using each element's actual scale.
        label_end_x = label_x + len(_label_text) * 8 * label_scale
        self.assertGreaterEqual(
            val_x, label_end_x,
            "Row %d (%s) editing=%s cursor=%d: value x=%d < label end x=%d "
            "(label=%r val=%r)" % (
                row_index, key, editing, cursor, val_x, label_end_x,
                _label_text, _val_text,
            )
        )
        return val_scale

    def test_no_overlap_all_rows_not_editing(self):
        for i in range(len(main._MENU_ITEMS)):
            self._check_row(i, editing=False)

    def test_no_overlap_all_rows_editing(self):
        for i in range(len(main._MENU_ITEMS)):
            self._check_row(i, editing=True)

    def test_region_row_exercises_fallback_and_no_overlap(self):
        """
        "Region"/"Rest of the world" is long enough to trigger the scale-1
        fallback. Verify the fallback fires (val_scale == 1) and that the value
        still starts to the right of the label - cursor on, cursor off, both
        editing states.
        """
        region_idx = next(
            i for i, (k, _) in enumerate(main._MENU_ITEMS) if k == "dexcom_region"
        )
        other_idx = (region_idx + 1) % len(main._MENU_ITEMS)

        for editing in (False, True):
            # cursor on
            val_scale = self._check_row(region_idx, editing=editing, cursor=region_idx)
            self.assertEqual(val_scale, 1,
                             "Region row cursor-on editing=%s: expected scale-1 fallback" % editing)
            # cursor off (different cursor position)
            val_scale = self._check_row(region_idx, editing=False, cursor=other_idx)
            self.assertEqual(val_scale, 1,
                             "Region row cursor-off: expected scale-1 fallback")

    def test_units_row_no_overlap_both_unit_values(self):
        """
        The renamed 'Units' label is short enough that no fallback is needed.
        Check cursor-on/off, editing/not-editing, and both unit values (mmol/mgdl).
        """
        units_idx = next(
            i for i, (k, _) in enumerate(main._MENU_ITEMS) if k == "units"
        )
        other_idx = (units_idx + 1) % len(main._MENU_ITEMS)

        for unit_val in (main._UNITS_MMOL, main._UNITS_MGDL):
            main._settings["units"] = unit_val
            for editing in (False, True):
                # cursor on
                val_scale = self._check_row(units_idx, editing=editing, cursor=units_idx)
                self.assertEqual(val_scale, 2,
                                 "Units row cursor-on unit=%s editing=%s: expected scale-2" % (
                                     unit_val, editing))
                # cursor off
                val_scale = self._check_row(units_idx, editing=False, cursor=other_idx)
                self.assertEqual(val_scale, 2,
                                 "Units row cursor-off unit=%s: expected scale-2" % unit_val)


if __name__ == "__main__":
    unittest.main()
