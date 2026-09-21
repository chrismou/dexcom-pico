"""
Tests for network recovery around Wi-Fi setup mode and soft reboots.

Once the access point has been up, only a hard reset restores the station's
default route on the CYW43 driver, so every exit from setup mode reboots and
the next boot verifies freshly submitted credentials. The poll loop reboots
when fetches keep failing with "no route to host" while the link is up.
"""
import os
import tempfile
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
        self._patch("draw_status", lambda *a, **k: None)
        self._saved_settings = dict(main._settings)
        main._last_net_error[0] = None
        main._session[0] = None

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(main, name, val)
        main._settings.clear()
        main._settings.update(self._saved_settings)
        main._last_net_error[0] = None
        main._session[0] = None

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


class TestRestartDevice(_PatchMixin, unittest.TestCase):

    def test_shows_message_then_hard_resets(self):
        self._patch("draw_status", self.log.spy("status"))
        with self.assertRaises(SystemExit):
            main.restart_device("Leaving setup")
        self.assertEqual(self.log.calls[0][1][0], "Leaving setup")


class TestSetupVerifyMarker(_PatchMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._patch("_SETUP_VERIFY_MARKER", os.path.join(self._tmp, "setup_verify"))

    def test_absent_by_default(self):
        self.assertFalse(main.take_setup_verify_pending())

    def test_set_then_take_is_one_shot(self):
        main.set_setup_verify_pending()
        self.assertTrue(main.take_setup_verify_pending())
        self.assertFalse(main.take_setup_verify_pending())


class TestIsNoRouteError(unittest.TestCase):

    def test_matches_errno_113(self):
        self.assertTrue(main.is_no_route_error("login: OSError(113)"))
        self.assertTrue(main.is_no_route_error("login: OSError(113,)"))
        self.assertTrue(main.is_no_route_error("ntp: [Errno 113] EHOSTUNREACH"))

    def test_other_errors_do_not_match(self):
        self.assertFalse(main.is_no_route_error(None))
        self.assertFalse(main.is_no_route_error("fetch: OSError(110)"))
        self.assertFalse(main.is_no_route_error("login: HTTP 500"))


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
        self._patch("commit_settings", self.log.spy("commit", True))
        self._patch("set_setup_verify_pending", self.log.spy("mark_verify"))

    def test_cancel_stops_ap_then_reboots(self):
        self._patch("serve_setup", lambda *a, **k: None)
        with self.assertRaises(SystemExit):
            main.run_wifi_setup()
        self.assertEqual(self.log.names(), ["start_ap", "stop_ap"])

    def test_submit_saves_marks_verify_then_reboots(self):
        form = {
            "wifi_ssid": "NewNet",
            "wifi_password": "newpass",
            "dexcom_account_id": "acct",
            "dexcom_password": "pw",
            "dexcom_region": "ous",
        }
        self._patch("serve_setup", lambda *a, **k: dict(form))
        with self.assertRaises(SystemExit):
            main.run_wifi_setup()
        self.assertEqual(self.log.names(), ["start_ap", "stop_ap", "commit", "mark_verify"])
        self.assertEqual(main._settings["wifi_ssid"], "NewNet")
        self.assertEqual(main._settings["dexcom_account_id"], "acct")

    def test_status_message_is_passed_to_page(self):
        seen = []
        self._patch("serve_setup", lambda ap, nets, msg: seen.append(msg))
        with self.assertRaises(SystemExit):
            main.run_wifi_setup("Could not join")
        self.assertEqual(seen, ["Could not join"])


class TestEnsureWifiAfterSubmit(_PatchMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        main._settings["wifi_ssid"] = "NewNet"
        main._settings["wifi_password"] = "newpass"
        main._settings["dexcom_region"] = "ous"
        main._settings["dexcom_account_id"] = "acct"
        main._settings["dexcom_password"] = "pw"
        self._patch("take_setup_verify_pending", lambda: True)
        self._patch("run_wifi_setup", self.log.spy("setup"))

    def test_join_and_login_ok_keeps_session(self):
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))
        self._patch("dexcom_login", self.log.spy("login", "session-id"))
        self.assertTrue(main.ensure_wifi())
        self.assertEqual(main._session[0], "session-id")
        self.assertEqual(self.log.names(), ["connect", "login"])

    def test_login_failure_reenters_setup_with_reason(self):
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))
        self._patch("dexcom_login", self.log.spy("login", None))
        self.assertFalse(main.ensure_wifi())
        self.assertIsNone(main._session[0])
        self.assertEqual(self.log.names()[-1], "setup")
        self.assertIn("Dexcom login failed", self.log.calls[-1][1][0])

    def test_join_failure_reenters_setup_with_password_hint(self):
        self._patch("connect_wifi", self.log.spy("connect", None))
        self.assertFalse(main.ensure_wifi())
        self.assertEqual(self.log.names(), ["connect", "setup"])
        self.assertIn("NewNet", self.log.calls[-1][1][0])
        self.assertIn("password", self.log.calls[-1][1][0])

    def test_no_pending_verification_skips_login(self):
        self._patch("take_setup_verify_pending", lambda: False)
        self._patch("connect_wifi", self.log.spy("connect", ("10.0.0.5",)))
        self._patch("dexcom_login", self.log.spy("login", "session-id"))
        self.assertTrue(main.ensure_wifi())
        self.assertEqual(self.log.names(), ["connect"])
        self.assertIsNone(main._session[0])


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

    def test_generic_failure_cycles_station_and_restarts_count(self):
        main._last_net_error[0] = "fetch: OSError(110)"
        self.assertEqual(main.recover_after_fetch_failures(main._FETCH_FAILS_BEFORE_STA_RESET), 0)
        self.assertEqual(self.log.names(), ["reset", "rejoin"])

    def test_no_route_with_link_up_reboots(self):
        main._last_net_error[0] = "login: OSError(113,)"
        with self.assertRaises(SystemExit):
            main.recover_after_fetch_failures(main._FETCH_FAILS_BEFORE_STA_RESET)
        self.assertEqual(self.log.names(), [])

    def test_no_route_with_link_down_cycles_instead(self):
        main._last_net_error[0] = "login: OSError(113)"
        main.wlan._connected = False
        self.assertEqual(main.recover_after_fetch_failures(main._FETCH_FAILS_BEFORE_STA_RESET), 0)
        self.assertEqual(self.log.names(), ["reset", "rejoin"])


if __name__ == "__main__":
    unittest.main()
