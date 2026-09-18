"""
Tests for station-interface recovery around Wi-Fi setup mode.

After an access-point session the CYW43 driver leaves the station without a
default route, so every exit from setup mode must cycle the station before
rejoining, and the poll loop must cycle it after persistent fetch failures.
"""
import types
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


def _make_fake_time(step_ms=100):
    """Fake time module whose ticks_ms() advances by step_ms each call."""
    import time as _real_time
    tick_val = [0]

    def fake_ticks_ms():
        tick_val[0] += step_ms
        return tick_val[0]

    mod = types.ModuleType("time")
    mod.ticks_ms   = fake_ticks_ms
    mod.ticks_diff = lambda a, b: a - b
    mod.sleep      = lambda x: None
    mod.time       = _real_time.time
    mod.gmtime     = _real_time.gmtime
    return mod


class _CallLog:
    """Records the order of named calls across patched functions."""

    def __init__(self):
        self.calls = []

    def spy(self, name, return_value=None):
        def _fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return return_value
        return _fn

    def names(self):
        return [c[0] for c in self.calls]


class _WlanRecovering:
    """WLAN stub whose active() calls are logged into a shared _CallLog."""

    def __init__(self, log, connected=True):
        self._log = log
        self._active = True
        self._connected = connected

    def active(self, val=None):
        if val is None:
            return self._active
        self._log.calls.append(("wlan.active", (val,), {}))
        self._active = val

    def isconnected(self):
        return self._connected

    def connect(self, ssid=None, password=None):
        self._log.calls.append(("wlan.connect", (ssid, password), {}))
        self._connected = True

    def disconnect(self):
        self._connected = False

    def ifconfig(self):
        return ("192.168.1.100", "255.255.255.0", "192.168.1.1", "8.8.8.8")


class _PatchMixin:
    """Save/restore main module attributes patched by a test."""

    def setUp(self):
        self._saved = {}
        self.log = _CallLog()
        self._patch("time", _make_fake_time())
        self._patch("wlan", _WlanRecovering(self.log))
        self._patch("feed_watchdog", lambda: None)
        self._saved_settings = dict(main._settings)

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(main, name, val)
        main._settings.clear()
        main._settings.update(self._saved_settings)

    def _patch(self, name, value):
        if name not in self._saved:
            self._saved[name] = getattr(main, name)
        setattr(main, name, value)


class TestResetStaInterface(_PatchMixin, unittest.TestCase):

    def test_cycles_station_off_then_on(self):
        main.reset_sta_interface()
        self.assertEqual(
            [c for c in self.log.calls if c[0] == "wlan.active"],
            [("wlan.active", (False,), {}), ("wlan.active", (True,), {})],
        )


class TestReconnectAfterSetup(_PatchMixin, unittest.TestCase):

    def test_resets_station_then_connects_with_saved_credentials(self):
        main._settings["wifi_ssid"] = "HomeNet"
        main._settings["wifi_password"] = "hunter2"
        self._patch("reset_sta_interface", self.log.spy("reset"))
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))

        result = main.reconnect_after_setup()

        self.assertTrue(result)
        self.assertEqual(self.log.names(), ["reset", "connect"])
        _, args, kwargs = self.log.calls[1]
        self.assertEqual(args[:2], ("HomeNet", "hunter2"))
        self.assertEqual(kwargs.get("timeout"), main._SETUP_JOIN_TIMEOUT_S)

    def test_no_ssid_does_nothing(self):
        main._settings["wifi_ssid"] = ""
        self._patch("reset_sta_interface", self.log.spy("reset"))
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))

        self.assertFalse(main.reconnect_after_setup())
        self.assertEqual(self.log.names(), [])

    def test_failed_join_returns_false(self):
        main._settings["wifi_ssid"] = "HomeNet"
        self._patch("reset_sta_interface", self.log.spy("reset"))
        self._patch("connect_wifi", self.log.spy("connect", None))

        self.assertFalse(main.reconnect_after_setup())


class TestRunWifiSetupExitPaths(_PatchMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        main._settings["wifi_ssid"] = "HomeNet"
        main._settings["wifi_password"] = "hunter2"
        self._patch("ap_ssid", lambda: "dexcom-pico-0000")
        self._patch("generate_ap_password", lambda: "abcdefgh")
        self._patch("start_ap", self.log.spy("start_ap", object()))
        self._patch("stop_ap", self.log.spy("stop_ap"))
        self._patch("scan_networks", lambda: [])
        self._patch("wait_buttons_released", lambda: None)
        self._patch("draw_status", lambda *a, **k: None)
        self._patch("commit_settings", lambda: True)
        self._patch("gc", types.SimpleNamespace(collect=lambda: None))

    def test_cancel_stops_ap_then_reconnects(self):
        self._patch("serve_setup", lambda *a, **k: None)
        self._patch("reconnect_after_setup", self.log.spy("reconnect", True))

        self.assertFalse(main.run_wifi_setup())
        self.assertEqual(self.log.names(), ["start_ap", "stop_ap", "reconnect"])

    def test_submit_resets_station_before_join(self):
        form = {
            "wifi_ssid": "NewNet",
            "wifi_password": "newpass",
            "dexcom_account_id": "acct",
            "dexcom_password": "pw",
            "dexcom_region": "ous",
        }
        self._patch("serve_setup", lambda *a, **k: form)
        self._patch("reset_sta_interface", self.log.spy("reset"))
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))
        self._patch("dexcom_login", lambda *a, **k: "session-id")

        self.assertTrue(main.run_wifi_setup())
        self.assertEqual(self.log.names(), ["start_ap", "stop_ap", "reset", "connect"])
        _, args, _ = self.log.calls[3]
        self.assertEqual(args[:2], ("NewNet", "newpass"))
        self.assertEqual(main._session[0], "session-id")


class TestRecoverAfterFetchFailures(_PatchMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._patch("reset_sta_interface", self.log.spy("reset"))
        self._patch("rejoin_wifi", self.log.spy("rejoin", True))

    def test_below_threshold_only_counts(self):
        limit = main._FETCH_FAILS_BEFORE_STA_RESET
        for n in range(1, limit):
            self.assertEqual(main.recover_after_fetch_failures(n), n)
        self.assertEqual(self.log.names(), [])

    def test_at_threshold_resets_and_restarts_count(self):
        limit = main._FETCH_FAILS_BEFORE_STA_RESET
        self.assertEqual(main.recover_after_fetch_failures(limit), 0)
        self.assertEqual(self.log.names(), ["reset", "rejoin"])

    def test_poll_loop_style_sequence_resets_once_per_run_of_failures(self):
        limit = main._FETCH_FAILS_BEFORE_STA_RESET
        failures = 0
        for _ in range(limit * 2):
            failures = main.recover_after_fetch_failures(failures + 1)
        self.assertEqual(self.log.names().count("reset"), 2)
        self.assertEqual(failures, 0)


if __name__ == "__main__":
    unittest.main()
