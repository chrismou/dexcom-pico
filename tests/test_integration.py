"""
Integration tests for run_menu() and serve_setup() with stubbed hardware,
time, and socket modules.
"""
import sys
import types
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


# ---------------------------------------------------------------------------
# Shared fake-time factory
# ---------------------------------------------------------------------------

def _make_fake_time(step_ms=50):
    """Return a fake time module whose ticks_ms() advances by step_ms each call."""
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


# ---------------------------------------------------------------------------
# Fake socket infrastructure for serve_setup tests
# ---------------------------------------------------------------------------

class _FakeConn:
    """Simulates one accepted TCP connection with scripted recv data."""

    def __init__(self, recv_data):
        if isinstance(recv_data, str):
            recv_data = recv_data.encode("utf-8")
        self._buf    = bytearray(recv_data)
        self._pos    = 0
        self.written = bytearray()
        self.closed  = False

    def settimeout(self, t):
        pass

    def recv(self, n):
        chunk = bytes(self._buf[self._pos:self._pos + n])
        self._pos += len(chunk)
        if not chunk:
            raise OSError("no more data")
        return chunk

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.written.extend(data)

    def close(self):
        self.closed = True


class _FakeListener:
    """
    Simulates a server socket.  Pass a list of items for each accept() call:
      - a _FakeConn instance  -> returned as (conn, addr)
      - None                  -> raises OSError (accept timeout)
    After the list is exhausted every accept() raises OSError.
    """

    def __init__(self, connections):
        self._queue  = list(connections)
        self.closed  = False

    def setsockopt(self, *args):
        pass

    def bind(self, addr):
        pass

    def listen(self, backlog):
        pass

    def settimeout(self, t):
        pass

    def accept(self):
        if self._queue:
            item = self._queue.pop(0)
            if item is None:
                raise OSError(11, "timeout")
            return item, ("192.168.4.2", 12345)
        raise OSError(11, "timeout")

    def close(self):
        self.closed = True


def _make_fake_socket_module(listener):
    """Return a fake socket module that yields the given listener on socket()."""
    mod = types.ModuleType("socket")
    mod.SOL_SOCKET   = 1
    mod.SO_REUSEADDR = 2

    def getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, (host, port))]

    mod.getaddrinfo = getaddrinfo
    mod.socket      = lambda: listener
    return mod


# ---------------------------------------------------------------------------
# run_menu() integration test
# ---------------------------------------------------------------------------

class TestRunMenuIntegration(unittest.TestCase):
    """
    Drive run_menu() with stubbed time and scripted button sequences.
    Exercises: idle tick (UnboundLocalError fix), cursor move, edit-commit,
    and hold-X exit.
    """

    def setUp(self):
        # Ensure _settings has defaults
        main._settings.clear()
        main._settings.update(main.default_settings())
        main._settings_saved.clear()
        main._settings_saved.update(main._settings)

        # Patch time
        self._orig_time  = main.time
        main.time        = _make_fake_time(step_ms=50)

        # Patch hold threshold to 1 ms so a hold fires on the second tick with X held
        self._orig_hold  = main._MENU_HOLD_MS
        main._MENU_HOLD_MS = 1

        # The release wait at menu entry reads raw() and would consume the
        # scripted sequences below; it has its own test in TestMenuEntryRelease.
        self._orig_wait = main.wait_buttons_released
        main.wait_buttons_released = lambda *a, **k: None

    def tearDown(self):
        main.time          = self._orig_time
        main._MENU_HOLD_MS = self._orig_hold
        main.wait_buttons_released = self._orig_wait

    def _script_button(self, btn, reads=(), raws=()):
        """Replace a Button's read() and raw() with scripted sequences."""
        read_q = list(reads)
        raw_q  = list(raws)

        def _read():
            return read_q.pop(0) if read_q else False

        def _raw():
            return raw_q.pop(0) if raw_q else False

        btn.read = _read
        btn.raw  = _raw

    def test_run_menu_idle_cursor_commit_hold_exit(self):
        """
        Full run_menu() sequence:
          iter 0: all idle (no events) - previously crashed with UnboundLocalError
          iter 1: B pressed -> cursor moves to 1 ("units")
          iter 2: Y raw pressed -> select enters editing
          iter 3: A pressed, Y held -> step up mmol->mgdl (Y latched, no duplicate select)
          iter 4: Y released (no event)
          iter 5: Y raw pressed -> select commits edit (units changes, alerts reset)
          iter 6: X raw goes down (hold timer starts)
          iter 7: X raw still down -> hold fires -> "exit" -> closed
        """
        # Script A (up) and B (down) via read()
        # Script X and Y via raw()
        #
        # read() calls: once per iteration for a and b each
        # raw()  calls: once per iteration for x and y each
        #
        # Sequence (8 iters x 2 read + 2 raw per iter):
        a_reads = [False, False, False, True,  False, False, False, False]
        b_reads = [False, True,  False, False, False, False, False, False]
        y_raws  = [False, False, True,  True,  False, True,  False, False]
        x_raws  = [False, False, False, False, False, False, True,  True ]

        self._script_button(main.button_a, reads=a_reads)
        self._script_button(main.button_b, reads=b_reads)
        # Y uses raw() for latch; script it
        self._script_button(main.button_y, raws=y_raws)
        # X uses raw() for HoldDetector
        self._script_button(main.button_x, raws=x_raws)

        # run_menu() should return without raising
        try:
            main.run_menu(None)
        except SystemExit:
            # machine.reset() stub raises SystemExit - not expected here but safe
            pass

        # After hold-exit the menu is closed; verify units changed and alerts reset
        # (units was stepped from mmol to mgdl, thresholds reset to mgdl defaults)
        self.assertEqual(main._settings.get("units"), main._UNITS_MGDL)
        self.assertEqual(main._settings.get("alert_low"), 70)
        self.assertEqual(main._settings.get("alert_high"), 180)

    def test_run_menu_units_commit_without_step_preserves_custom_thresholds(self):
        """
        Navigate to "Units" (cursor 1), enter edit, commit with Y
        without stepping (unit unchanged), then hold-X to exit.
        Custom thresholds set beforehand must survive because apply_units_change
        returns a copy with thresholds intact when the unit is unchanged.
        """
        # Pre-set custom thresholds that differ from defaults
        main._settings["alert_low"]  = 5.5
        main._settings["alert_high"] = 12.0

        # Sequence (6 iters):
        # iter 0: all idle
        # iter 1: B pressed -> cursor moves to 1 ("units")
        # iter 2: Y raw pressed -> select enters editing
        # iter 3: Y raw pressed -> commit (no step, unit unchanged)
        # iter 4: X raw goes down (hold timer starts)
        # iter 5: X raw still down -> hold fires -> "exit" -> closed
        a_reads = [False, False, False, False, False, False]
        b_reads = [False, True,  False, False, False, False]
        y_raws  = [False, False, True,  True,  False, False]
        x_raws  = [False, False, False, False, True,  True ]

        self._script_button(main.button_a, reads=a_reads)
        self._script_button(main.button_b, reads=b_reads)
        self._script_button(main.button_y, raws=y_raws)
        self._script_button(main.button_x, raws=x_raws)

        try:
            main.run_menu(None)
        except SystemExit:
            pass

        # Unit must still be mmol (unchanged); custom thresholds must survive
        self.assertEqual(main._settings.get("units"), main._UNITS_MMOL)
        self.assertAlmostEqual(main._settings.get("alert_low"), 5.5)
        self.assertAlmostEqual(main._settings.get("alert_high"), 12.0)

    def test_run_menu_idle_tick_does_not_crash(self):
        """
        An idle tick with no button activity must not raise UnboundLocalError.
        After the idle tick, hold-X exits immediately.
        """
        # iter 0: all idle
        # iter 1: X raw down (hold starts)
        # iter 2: X raw still down (hold fires -> exit)
        self._script_button(main.button_a, reads=[])
        self._script_button(main.button_b, reads=[])
        self._script_button(main.button_y, raws=[])
        self._script_button(main.button_x, raws=[False, True, True])

        try:
            main.run_menu(None)
        except SystemExit:
            pass
        # If we get here without UnboundLocalError the fix is working


# ---------------------------------------------------------------------------
# serve_setup() socket-level tests
# ---------------------------------------------------------------------------

# Helpers to build HTTP request bytes
def _get_request(path="/"):
    return ("GET %s HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n" % path).encode()


def _post_request(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return (
        "POST /save HTTP/1.1\r\n"
        "Host: 192.168.4.1\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: %d\r\n\r\n" % len(body)
    ).encode() + body


def _valid_form_body():
    return (
        "ssid=HomeNet&ssid_other=&wifi_password=wifipass"
        "&dexcom_account_id=acct-uuid&dexcom_password=dexpass&dexcom_region=ous"
    )


class TestMenuEntryRelease(unittest.TestCase):
    """The X press that opens the menu must not be read as 'back' by the menu."""

    def setUp(self):
        main._settings.clear()
        main._settings.update(main.default_settings())
        self._orig_time = main.time
        self._orig_hold = main._MENU_HOLD_MS
        self._orig_raw  = (main.button_a.raw, main.button_b.raw, main.button_x.raw, main.button_y.raw)
        self._orig_read = (main.button_a.read, main.button_b.read)
        main.time = _make_fake_time(step_ms=50)
        main._MENU_HOLD_MS = 1
        main.button_a.read = lambda: False
        main.button_b.read = lambda: False
        main.button_a.raw  = lambda: False
        main.button_b.raw  = lambda: False
        main.button_y.raw  = lambda: False

    def tearDown(self):
        main.time = self._orig_time
        main._MENU_HOLD_MS = self._orig_hold
        (main.button_a.raw, main.button_b.raw, main.button_x.raw, main.button_y.raw) = self._orig_raw
        (main.button_a.read, main.button_b.read) = self._orig_read

    def test_held_x_at_entry_does_not_close_menu(self):
        # X is still held for the first sample, then released, then idle, then
        # a genuine hold (two consecutive True with the 1 ms threshold) exits.
        # Without the entry wait, the release would be a short press = "back"
        # and the menu would close after two ticks, leaving values unconsumed.
        x_raw_q = [True, False, False, False, True, True]
        main.button_x.raw = lambda: x_raw_q.pop(0) if x_raw_q else False

        main.run_menu(None)

        self.assertEqual(x_raw_q, [], "menu closed early on the opening press")

    def test_entry_wait_gives_up_on_stuck_button(self):
        main.button_x.raw = lambda: True
        main.wait_buttons_released(main._MENU_ENTRY_RELEASE_MS)   # must return
        # With the fake clock stepping 50 ms per call, the wait must have
        # advanced the clock past the timeout rather than spun forever.
        self.assertGreaterEqual(main.time.ticks_ms(), main._MENU_ENTRY_RELEASE_MS)


class TestServeSetup(unittest.TestCase):

    def setUp(self):
        main._settings.clear()
        main._settings.update(main.default_settings())
        main._settings_saved.clear()
        main._settings_saved.update(main._settings)

        self._orig_time       = main.time
        self._orig_socket     = main.socket
        self._orig_hold       = main._MENU_HOLD_MS
        main.time             = _make_fake_time(step_ms=50)
        main._MENU_HOLD_MS    = 1  # hold fires on 2nd consecutive raw=True tick

        # Keep X raw False by default (no hold cancel)
        self._orig_x_raw = main.button_x.raw
        main.button_x.raw = lambda: False

    def tearDown(self):
        main.time          = self._orig_time
        main.socket        = self._orig_socket
        main._MENU_HOLD_MS = self._orig_hold
        main.button_x.raw  = self._orig_x_raw

    def _install_listener(self, connections):
        listener = _FakeListener(connections)
        main.socket = _make_fake_socket_module(listener)
        return listener

    # ---- POST success ----

    def test_post_valid_form_returns_settings(self):
        """A valid POST /save causes serve_setup to return the new settings dict."""
        body = _valid_form_body().encode()
        conn = _FakeConn(_post_request(body))
        self._install_listener([conn])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result = main.serve_setup(ap_info, [], None)

        self.assertIsNotNone(result, "serve_setup should return a dict on valid POST")
        self.assertEqual(result.get("wifi_ssid"), "HomeNet")
        self.assertEqual(result.get("dexcom_region"), "ous")
        # Response to the browser should mention "Saved"
        self.assertIn(b"Saved", conn.written)

    def test_post_invalid_form_sends_error_page_and_continues(self):
        """
        A POST with missing dexcom_account_id sends a 200 page with an error
        and does NOT cause serve_setup to return (it loops back).
        On the next accept() timeout the hold-X fires and returns None.
        """
        bad_body = (
            "ssid=HomeNet&ssid_other=&wifi_password=wifipass"
            "&dexcom_account_id=&dexcom_password=dexpass&dexcom_region=ous"
        ).encode()
        bad_conn = _FakeConn(_post_request(bad_body))

        # After the bad POST, return None (timeout) and then hold-X cancels
        x_raw_seq = [False, False, True, True]  # hold fires after 2 Trues
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([bad_conn, None])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result = main.serve_setup(ap_info, [], None)

        # Hold-X cancels -> returns None
        self.assertIsNone(result)
        # The error page was sent to the bad connection
        written_str = bad_conn.written.decode("utf-8", errors="replace")
        self.assertIn("200", written_str)

    # ---- GET ----

    def test_get_serves_page_and_hold_x_cancels(self):
        """
        A GET request receives the setup page (200 OK).
        Afterwards hold-X causes serve_setup to return None.
        """
        conn = _FakeConn(_get_request("/"))

        # After GET: next accept() times out, then hold-X fires
        x_raw_seq = [False, False, True, True]
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([conn, None])

        networks = [("HomeNet", -55, True)]
        ap_info  = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result   = main.serve_setup(ap_info, networks, None)

        self.assertIsNone(result)
        written_str = conn.written.decode("utf-8", errors="replace")
        self.assertIn("200", written_str)
        self.assertIn("Dexcom Pico setup", written_str)

    def test_get_rescan_does_not_crash(self):
        """GET /?rescan=1 triggers scan_networks() which returns [] from the stub."""
        conn = _FakeConn(_get_request("/?rescan=1"))

        x_raw_seq = [False, False, True, True]
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([conn, None])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result  = main.serve_setup(ap_info, [], None)

        self.assertIsNone(result)
        written_str = conn.written.decode("utf-8", errors="replace")
        self.assertIn("200", written_str)

    # ---- 413 oversized header ----

    def test_oversized_header_sends_413(self):
        """
        A request whose header block exceeds _HTTP_MAX_HEADER_BYTES without a
        blank line triggers a 413 response.
        """
        # Build a request that is all header bytes, never has \\r\\n\\r\\n,
        # and exceeds _HTTP_MAX_HEADER_BYTES (2048 bytes).
        oversized = b"GET / HTTP/1.1\r\nX-Junk: " + b"A" * 3000 + b"\r\n"
        conn = _FakeConn(oversized)

        # After 413: accept() times out, then hold-X cancels
        x_raw_seq = [False, False, True, True]
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([conn, None])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result  = main.serve_setup(ap_info, [], None)

        self.assertIsNone(result)
        written_str = conn.written.decode("utf-8", errors="replace")
        self.assertIn("413", written_str)

    # ---- 413 oversized body ----

    def test_oversized_body_sends_413(self):
        """
        A POST whose Content-Length exceeds _HTTP_MAX_BODY_BYTES (1024)
        triggers a 413 response without reading the body.
        """
        huge_cl = main._HTTP_MAX_BODY_BYTES + 1
        request = (
            "POST /save HTTP/1.1\r\n"
            "Host: 192.168.4.1\r\n"
            "Content-Length: %d\r\n\r\n" % huge_cl
        ).encode() + b"x" * 10  # actual body content doesn't matter
        conn = _FakeConn(request)

        x_raw_seq = [False, False, True, True]
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([conn, None])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result  = main.serve_setup(ap_info, [], None)

        self.assertIsNone(result)
        written_str = conn.written.decode("utf-8", errors="replace")
        self.assertIn("413", written_str)

    # ---- cancel via hold-X with no connections ----

    def test_hold_x_cancels_immediately(self):
        """Hold-X before any connection causes serve_setup to return None."""
        # x_raw: immediately True, True → hold fires
        x_raw_seq = [True, True]
        x_idx = [0]
        def _x_raw():
            v = x_raw_seq[x_idx[0]] if x_idx[0] < len(x_raw_seq) else True
            x_idx[0] += 1
            return v
        main.button_x.raw = _x_raw

        self._install_listener([])

        ap_info = {"ssid": "dexcom-pico-test", "password": "TestPass1"}
        result  = main.serve_setup(ap_info, [], None)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
