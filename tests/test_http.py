"""Tests for HTTP parsing, form handling, HTML escaping, and setup page helpers."""
import sys
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


class TestHtmlEscape(unittest.TestCase):

    def test_ampersand(self):
        self.assertEqual(main.html_escape("a&b"), "a&amp;b")

    def test_lt_gt(self):
        self.assertIn("&lt;", main.html_escape("<script>"))
        self.assertIn("&gt;", main.html_escape("<script>"))

    def test_quotes(self):
        self.assertIn("&quot;", main.html_escape('"'))
        self.assertIn("&#x27;", main.html_escape("'"))

    def test_plain(self):
        self.assertEqual(main.html_escape("hello world"), "hello world")


class TestParseQuery(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(main.parse_query(""), {})

    def test_simple(self):
        result = main.parse_query("a=1&b=2")
        self.assertEqual(result["a"], "1")
        self.assertEqual(result["b"], "2")

    def test_plus_as_space(self):
        result = main.parse_query("a=hello+world")
        self.assertEqual(result["a"], "hello world")

    def test_percent_decode(self):
        result = main.parse_query("a=hello%20world")
        self.assertEqual(result["a"], "hello world")

    def test_repeated_keys_last_wins(self):
        result = main.parse_query("a=1&a=2")
        self.assertEqual(result["a"], "2")


class TestParseForm(unittest.TestCase):

    def test_bytes_input(self):
        result = main.parse_form(b"ssid=MyNet&wifi_password=pass123")
        self.assertEqual(result["ssid"], "MyNet")
        self.assertEqual(result["wifi_password"], "pass123")

    def test_plus_space(self):
        result = main.parse_form("a=hello+world")
        self.assertEqual(result["a"], "hello world")

    def test_percent_hex(self):
        result = main.parse_form("a=%41%42%43")
        self.assertEqual(result["a"], "ABC")

    def test_utf8_multibyte(self):
        # "cafe" with accent: %C3%A9
        result = main.parse_form("a=caf%C3%A9")
        self.assertIn("caf", result["a"])

    def test_bad_percent_sequence(self):
        # Bad sequence: should not raise
        result = main.parse_form("a=%ZZ")
        self.assertIn("a", result)  # key exists, value may have the literal %

    def test_repeated_keys_last_wins(self):
        result = main.parse_form("k=1&k=2")
        self.assertEqual(result["k"], "2")


class TestParseHttpRequest(unittest.TestCase):

    def test_get_with_query(self):
        head = b"GET /?rescan=1 HTTP/1.1\r\nHost: 192.168.4.1\r\n"
        method, path, query, headers = main.parse_http_request(head)
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/")
        self.assertEqual(query["rescan"], "1")

    def test_post(self):
        head = b"POST /save HTTP/1.1\r\nContent-Length: 42\r\n"
        method, path, query, headers = main.parse_http_request(head)
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/save")
        self.assertEqual(headers["content-length"], "42")

    def test_header_case_insensitive(self):
        head = b"GET / HTTP/1.1\r\nContent-Type: text/html\r\n"
        _m, _p, _q, headers = main.parse_http_request(head)
        self.assertIn("content-type", headers)

    def test_malformed_request_line(self):
        with self.assertRaises(ValueError):
            main.parse_http_request(b"BADREQUEST\r\n")

    def test_no_query(self):
        head = b"GET /index HTTP/1.1\r\n"
        method, path, query, headers = main.parse_http_request(head)
        self.assertEqual(path, "/index")
        self.assertEqual(query, {})


class TestDedupeNetworks(unittest.TestCase):

    def _raw(self, ssid, rssi, security=4):
        if isinstance(ssid, str):
            ssid = ssid.encode("utf-8")
        return (ssid, b"\x00" * 6, 6, rssi, security, 0)

    def test_deduplication_keeps_strongest(self):
        raw = [
            self._raw("Net", -80),
            self._raw("Net", -60),
            self._raw("Net", -75),
        ]
        result = main.dedupe_networks(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], -60)

    def test_sorted_by_rssi_descending(self):
        raw = [
            self._raw("Weak", -90),
            self._raw("Strong", -40),
            self._raw("Medium", -60),
        ]
        result = main.dedupe_networks(raw)
        rssies = [r[1] for r in result]
        self.assertEqual(rssies, sorted(rssies, reverse=True))

    def test_empty_ssid_skipped(self):
        raw = [self._raw("", -50), self._raw("   ", -55), self._raw("Real", -60)]
        result = main.dedupe_networks(raw)
        names = [r[0] for r in result]
        self.assertNotIn("", names)
        self.assertIn("Real", names)

    def test_capped_at_20(self):
        raw = [self._raw("Net%d" % i, -50 - i) for i in range(30)]
        result = main.dedupe_networks(raw)
        self.assertLessEqual(len(result), 20)

    def test_secure_flag(self):
        raw = [self._raw("Open", -60, security=0), self._raw("Secured", -55, security=4)]
        result = main.dedupe_networks(raw)
        by_ssid = {r[0]: r[2] for r in result}
        self.assertFalse(by_ssid["Open"])
        self.assertTrue(by_ssid["Secured"])


class TestWifiQrPayload(unittest.TestCase):

    def test_basic(self):
        result = main.wifi_qr_payload("MyNet", "pass")
        self.assertIn("WIFI:T:WPA;S:MyNet;P:pass;;", result)

    def test_open_network(self):
        result = main.wifi_qr_payload("OpenNet", "")
        self.assertIn("T:nopass", result)

    def test_escapes_semicolon(self):
        result = main.wifi_qr_payload("Net;Work", "p;ss")
        self.assertIn("\\;", result)

    def test_escapes_backslash(self):
        result = main.wifi_qr_payload("Net\\Work", "pass")
        self.assertIn("\\\\", result)

    def test_escapes_colon(self):
        result = main.wifi_qr_payload("Net:Work", "p:ss")
        self.assertIn("\\:", result)

    def test_escapes_comma(self):
        result = main.wifi_qr_payload("Net,Work", "p,ss")
        self.assertIn("\\,", result)


class TestRenderSetupPage(unittest.TestCase):

    def _nets(self):
        return [("HomeNet", -55, True), ("Other", -70, False)]

    def test_contains_ssids(self):
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings)
        self.assertIn("HomeNet", page)
        self.assertIn("Other", page)

    def test_escapes_ssid(self):
        nets = [('<script>', -60, False)]
        settings = main.default_settings()
        page = main.render_setup_page(nets, settings)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_never_contains_password(self):
        settings = main.default_settings()
        settings["wifi_password"]    = "super_secret_wifi"
        settings["dexcom_password"]  = "super_secret_dex"
        page = main.render_setup_page(self._nets(), settings)
        self.assertNotIn("super_secret_wifi", page)
        self.assertNotIn("super_secret_dex", page)

    def test_shows_error(self):
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings, error="Test error message")
        self.assertIn("Test error message", page)

    def test_dexcom_account_prefilled(self):
        settings = main.default_settings()
        settings["dexcom_account_id"] = "my-account-uuid"
        page = main.render_setup_page(self._nets(), settings)
        self.assertIn("my-account-uuid", page)

    def test_viewport_meta(self):
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings)
        self.assertIn("viewport", page)

    def test_region_option_labels(self):
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings)
        # All three region options must appear with their full label text
        self.assertIn("Rest of the world", page)
        self.assertIn("United States", page)
        self.assertIn("Japan", page)

    def test_region_option_values(self):
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings)
        # Each region option tag must carry the stored value
        self.assertIn('value="ous"', page)
        self.assertIn('value="us"', page)
        self.assertIn('value="jp"', page)

    def test_no_outside_us_label(self):
        # The old "Outside US" label must no longer appear
        settings = main.default_settings()
        page = main.render_setup_page(self._nets(), settings)
        self.assertNotIn("Outside US", page)


class TestValidateSetupForm(unittest.TestCase):

    def _base_form(self):
        return {
            "ssid":              "HomeNet",
            "ssid_other":        "",
            "wifi_password":     "wifipass",
            "dexcom_account_id": "acct-uuid",
            "dexcom_password":   "dexpass",
            "dexcom_region":     "ous",
        }

    def test_valid_form(self):
        new_s, err = main.validate_setup_form(self._base_form(), main.default_settings())
        self.assertIsNone(err)
        self.assertEqual(new_s["wifi_ssid"], "HomeNet")

    def test_ssid_other_takes_precedence(self):
        form = self._base_form()
        form["ssid_other"] = "HiddenNet"
        new_s, err = main.validate_setup_form(form, main.default_settings())
        self.assertIsNone(err)
        self.assertEqual(new_s["wifi_ssid"], "HiddenNet")

    def test_missing_ssid_gives_error(self):
        form = self._base_form()
        form["ssid"] = ""
        _new_s, err = main.validate_setup_form(form, main.default_settings())
        self.assertIsNotNone(err)

    def test_missing_account_id_gives_error(self):
        form = self._base_form()
        form["dexcom_account_id"] = ""
        _new_s, err = main.validate_setup_form(form, main.default_settings())
        self.assertIsNotNone(err)

    def test_invalid_region_gives_error(self):
        form = self._base_form()
        form["dexcom_region"] = "invalid"
        _new_s, err = main.validate_setup_form(form, main.default_settings())
        self.assertIsNotNone(err)

    def test_valid_regions(self):
        for region in ("us", "ous", "jp"):
            form = self._base_form()
            form["dexcom_region"] = region
            new_s, err = main.validate_setup_form(form, main.default_settings())
            self.assertIsNone(err)
            self.assertEqual(new_s["dexcom_region"], region)


if __name__ == "__main__":
    unittest.main()
