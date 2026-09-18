"""
Tests for the network-error trace and the Device info rows.
"""
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


class TestNoteNetError(unittest.TestCase):

    def setUp(self):
        main._last_net_error[0] = None

    def tearDown(self):
        main._last_net_error[0] = None

    def test_records_stage_and_detail(self):
        main.note_net_error("login", repr(OSError(113)))
        self.assertEqual(main._last_net_error[0], "login: OSError(113)")

    def test_truncates_long_detail(self):
        main.note_net_error("fetch", "x" * 500)
        self.assertEqual(len(main._last_net_error[0]), 80)

    def test_successful_fetch_clears_error(self):
        main._last_net_error[0] = "fetch: OSError(110)"

        class _Resp:
            status_code = 200

            def json(self):
                return [{"Value": 100}]

            def close(self):
                pass

        saved = main.requests.post
        main.requests.post = lambda *a, **k: _Resp()
        try:
            self.assertEqual(main.dexcom_fetch_latest("ous", "sess"), [{"Value": 100}])
        finally:
            main.requests.post = saved
        self.assertIsNone(main._last_net_error[0])

    def test_http_error_is_recorded(self):
        class _Resp:
            status_code = 500

            def close(self):
                pass

        saved = main.requests.post
        main.requests.post = lambda *a, **k: _Resp()
        try:
            self.assertIsNone(main.dexcom_login("ous", "acct", "pw"))
        finally:
            main.requests.post = saved
        self.assertEqual(main._last_net_error[0], "login: HTTP 500")


class TestDescribeNetError(unittest.TestCase):

    def test_none_is_none_text(self):
        self.assertEqual(main.describe_net_error(None), "none")

    def test_known_errno_gets_hint(self):
        self.assertEqual(
            main.describe_net_error("login: OSError(113)"),
            "login: OSError(113) = No route to host",
        )

    def test_errno_in_bracket_form(self):
        self.assertTrue(main.describe_net_error("ntp: [Errno 110] ETIMEDOUT").endswith("Timed out"))

    def test_unknown_error_unchanged(self):
        self.assertEqual(main.describe_net_error("fetch: ValueError()"), "fetch: ValueError()")


class TestDeviceInfoLines(unittest.TestCase):

    def setUp(self):
        self._saved_settings = dict(main._settings)
        self._saved_wlan = main.wlan
        self._saved_read_crash = main.read_crash_log
        main._last_net_error[0] = None
        main.read_crash_log = lambda: {"crashes": 0, "wdt_resets": 0, "last_error": ""}

    def tearDown(self):
        main._settings.clear()
        main._settings.update(self._saved_settings)
        main.wlan = self._saved_wlan
        main.read_crash_log = self._saved_read_crash
        main._last_net_error[0] = None

    def _texts(self):
        return [t for t, _ in main._device_info_lines()]

    def test_connected_shows_ip_gateway_and_dns(self):
        main._settings["wifi_ssid"] = "HomeNet"
        main.wlan._connected = True
        texts = self._texts()
        self.assertIn("SSID: HomeNet", texts)
        self.assertIn("IP: 192.168.1.100", texts)
        self.assertIn("GW: 192.168.1.1", texts)
        self.assertIn("DNS: 8.8.8.8", texts)

    def test_disconnected_shows_not_connected(self):
        main.wlan._connected = False
        self.assertIn("IP: not connected", self._texts())

    def test_net_error_shown_with_hint(self):
        main._last_net_error[0] = "login: OSError(113)"
        texts = self._texts()
        self.assertTrue(any(t.startswith("Net: login: OSError(113) = No route") for t in texts))

    def test_crash_error_shown_when_no_net_error(self):
        main.read_crash_log = lambda: {"crashes": 1, "wdt_resets": 0, "last_error": "MemoryError"}
        texts = self._texts()
        self.assertIn("Last err: MemoryError", texts)
        self.assertFalse(any(t.startswith("Net:") for t in texts))


if __name__ == "__main__":
    unittest.main()
