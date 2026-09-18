"""
Hardware stubs for CPython unit tests.
Call install() before importing main to inject fake hardware modules.
"""
import sys
import os
import types
import time as _time


class _WDT:
    def feed(self):
        pass


class _WLAN:
    STA_IF = 0
    AP_IF  = 1

    def __init__(self, iface=0):
        self._iface    = iface
        self._active   = False
        self._connected = False
        self._config   = {}
        self._scan_result = [
            (b"TestNet", b"\x00" * 6, 6, -55, 4, 0),
            (b"Other",   b"\x00" * 6, 1, -70, 0, 0),
        ]

    def active(self, val=None):
        if val is None:
            return self._active
        self._active = val

    def isconnected(self):
        return self._connected

    def connect(self, ssid=None, password=None):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def scan(self):
        return self._scan_result

    def config(self, **kwargs):
        self._config.update(kwargs)

    def ifconfig(self):
        return ("192.168.1.100", "255.255.255.0", "192.168.1.1", "8.8.8.8")

    def status(self, key=None):
        if key == "rssi":
            return -60
        return None


class _Button:
    def __init__(self, pin):
        self._pin    = pin
        self._reads  = []
        self._raw_val = False

    def read(self):
        if self._reads:
            return self._reads.pop(0)
        return False

    def raw(self):
        return self._raw_val

    def _set_raw(self, val):
        self._raw_val = val


class _RGBLED:
    def __init__(self, r, g, b):
        self.last = (0, 0, 0)

    def set_rgb(self, r, g, b):
        self.last = (r, g, b)


class _PicoGraphics:
    DISPLAY_PICO_DISPLAY_2 = 1
    PEN_P8 = 2

    def __init__(self, display=None, pen_type=None):
        self._backlight = 0.5

    def get_bounds(self):
        return (320, 240)

    def create_pen(self, r, g, b):
        return (r, g, b)

    def set_pen(self, pen):
        pass

    def clear(self):
        pass

    def text(self, text, x, y, wrap, scale):
        pass

    def line(self, x1, y1, x2, y2):
        pass

    def rectangle(self, x, y, w, h):
        pass

    def update(self):
        pass

    def set_font(self, font):
        pass

    def set_thickness(self, t):
        pass

    def measure_text(self, text, scale=1):
        return len(text) * 8 * scale

    def set_backlight(self, val):
        self._backlight = val


def _make_network_module():
    mod = types.ModuleType("network")
    mod.WLAN    = _WLAN
    mod.STA_IF  = 0
    mod.AP_IF   = 1
    return mod


def _make_machine_module():
    mod = types.ModuleType("machine")
    mod.WDT          = _WDT
    mod.WDT_RESET    = 3
    mod.PWRON_RESET  = 1
    mod._reset_cause = 1  # not WDT by default

    def reset_cause():
        return mod._reset_cause

    def unique_id():
        return b"\xde\xad\xbe\xef"

    def reset():
        raise SystemExit("machine.reset() called")

    mod.reset_cause = reset_cause
    mod.unique_id   = unique_id
    mod.reset       = reset
    return mod


def _make_picographics_module():
    mod = types.ModuleType("picographics")
    mod.PicoGraphics          = _PicoGraphics
    mod.DISPLAY_PICO_DISPLAY_2 = 1
    mod.PEN_P8                = 2
    return mod


def _make_pimoroni_module():
    mod = types.ModuleType("pimoroni")
    mod.Button = _Button
    mod.RGBLED = _RGBLED
    return mod


def _make_secrets_stub():
    """Stub secrets module (pre-seeded to avoid importing CPython's stdlib secrets)."""
    mod = types.ModuleType("secrets")
    mod.WIFI_SSID          = "TestSSID"
    mod.WIFI_PASSWORD      = "TestPass"
    mod.DEXCOM_ACCOUNT_ID  = "test-account-id"
    mod.DEXCOM_PASSWORD    = "test-dex-pass"
    mod.DEXCOM_REGION      = "ous"
    return mod


def _make_ntptime_module():
    mod = types.ModuleType("ntptime")

    def settime():
        pass

    mod.settime = settime
    return mod


def _make_urequests_module():
    mod = types.ModuleType("urequests")

    def post(*args, **kwargs):
        raise OSError("stub: no network")

    mod.post = post
    return mod


class _StringIO:
    def __init__(self):
        self._buf = []

    def write(self, s):
        self._buf.append(s)

    def getvalue(self):
        return "".join(self._buf)


def _make_uio_module():
    mod = types.ModuleType("uio")
    mod.StringIO = _StringIO
    return mod


def install():
    """
    Install all hardware stubs into sys.modules and add src/ to sys.path.
    Must be called before importing main.
    """
    # Must stub 'secrets' before main.py is imported so CPython's stdlib
    # secrets module is not imported instead of our stub.
    sys.modules["secrets"]     = _make_secrets_stub()
    sys.modules["network"]     = _make_network_module()
    sys.modules["machine"]     = _make_machine_module()
    sys.modules["picographics"] = _make_picographics_module()
    sys.modules["pimoroni"]    = _make_pimoroni_module()
    sys.modules["ntptime"]     = _make_ntptime_module()
    sys.modules["urequests"]   = _make_urequests_module()
    sys.modules["ujson"]       = __import__("json")
    sys.modules["uio"]         = _make_uio_module()
    # qrcode is optional; leave it absent so the QR path is skipped in tests

    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
