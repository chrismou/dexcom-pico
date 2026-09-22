import time
import math
import machine
import network
import os
import socket
import gc

try:
    import ujson as json
except ImportError:
    import json

try:
    import uio
except ImportError:
    import io as uio

try:
    import ntptime
except ImportError:
    ntptime = None

try:
    import urequests as requests
except ImportError:
    requests = None

from picographics import PicoGraphics, DISPLAY_PICO_DISPLAY_2, PEN_P8
from pimoroni import Button, RGBLED

try:
    import qrcode as _qrcode_mod
except ImportError:
    _qrcode_mod = None

# ----- Optional secrets.py -----
try:
    import secrets as _secrets
except ImportError:
    _secrets = None


def _secret(name, default=""):
    """Return a value from secrets.py, or default if the module or attr is absent."""
    return getattr(_secrets, name, default) if _secrets is not None else default


# ----- Display setup -----
display = PicoGraphics(display=DISPLAY_PICO_DISPLAY_2, pen_type=PEN_P8)
display.set_backlight(0.5)
WIDTH, HEIGHT = display.get_bounds()

BLACK = display.create_pen(0, 0, 0)
WHITE = display.create_pen(255, 255, 255)
GREEN = display.create_pen(0, 200, 120)
CYAN = display.create_pen(0, 180, 255)
YELLOW = display.create_pen(255, 215, 0)
RED = display.create_pen(255, 60, 60)
GREY = display.create_pen(80, 80, 90)

try:
    display.set_font("bitmap8")
except Exception:
    pass  # fallback to default font

# ----- Dexcom Share API constants -----
_DEXCOM_BASE_URLS = {
    "us":  "https://share2.dexcom.com/ShareWebServices/Services/",
    "ous": "https://shareous1.dexcom.com/ShareWebServices/Services/",
    "jp":  "https://share.dexcom.jp/ShareWebServices/Services/",
}
_DEXCOM_APP_ID_DEFAULT = "d89443d2-327c-4a6f-89e5-496bbb0317db"
_DEXCOM_APP_ID_JP      = "d8665ade-9673-4e27-9ff6-92db4ce13d13"
_DEXCOM_NULL_SESSION   = "00000000-0000-0000-0000-000000000000"
_DEXCOM_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "dexcom-pico/1.0",
}
_MMOL_FACTOR = 0.0555


def unit_label(units):
    """Return the display label for the given units string (e.g. "mmol/L", "mg/dL")."""
    return _UNIT_LABELS.get(units, "mmol/L")


def convert_mg_dl(mg_dl, units):
    """
    Convert a raw mg/dL integer to the display value for the given units.
    Returns a float (mmol/L, 1 dp) or int (mg/dL). Returns None if mg_dl is None.
    """
    if mg_dl is None:
        return None
    if units == _UNITS_MGDL:
        return int(mg_dl)
    return round(mg_dl * _MMOL_FACTOR, 1)


def format_glucose(value, units):
    """
    Format a converted glucose value as a string for display.
    Returns "---" for None. mmol/L always shows 1 decimal place; mg/dL is an integer.
    """
    if value is None:
        return "---"
    if units == _UNITS_MGDL:
        return str(int(value))
    # Ensure exactly one decimal place even if value is already a float
    return "%.1f" % value


# Trend string (PascalCase from API) -> canonical lowercase for draw_trend()
_TREND_MAP = {
    "DoubleUp":        "doubleUp",
    "SingleUp":        "singleUp",
    "FortyFiveUp":     "fortyFiveUp",
    "Flat":            "flat",
    "FortyFiveDown":   "fortyFiveDown",
    "SingleDown":      "singleDown",
    "DoubleDown":      "doubleDown",
    # The following produce no arrow (draw_trend falls through to else: return)
    "None":            None,
    "NotComputable":   None,
    "RateOutOfRange":  None,
}

# Arrowhead barb length as a fraction of the shaft half-length. Long barbs so
# the direction reads from across the room.
_ARROW_HEAD_RATIO = 0.85

# Canonical trend names that draw_trend() can render as an arrow
_ARROW_TRENDS = tuple(t for t in _TREND_MAP.values() if t is not None)

# Small trend arrow drawn beside "Previous: X.X" while the reading is stale.
# Sized to sit on the 16 px scale-2 bitmap8 text row without enlarging it.
_PREV_ARROW_BOX_PX       = 22   # square box the arrow is centred in
_PREV_ARROW_SIZE_PX      = 9    # half-length of the arrow shaft
_PREV_ARROW_THICKNESS_PX = 3
_PREV_ARROW_HEAD_LEN_PX  = 8    # barb length, longer than the default ratio so
_PREV_ARROW_HEAD_WIDTH_PX = 3   # the direction reads from across the room
_PREV_ARROW_GAP_PX       = 6    # gap between the text and the arrow box

# Mutable cell holding the current Dexcom session id
_session = [None]   # _session[0] holds the current session id string or None

# Most recent network failure, for the Device info screen. Network helpers
# swallow exceptions by design, so this is the only trace of why a fetch
# or NTP sync failed. Cleared by the next successful fetch.
_last_net_error = [None]


def note_net_error(stage, detail):
    """Record a short 'stage: detail' description of the latest network failure."""
    try:
        _last_net_error[0] = (stage + ": " + str(detail))[:80]
    except Exception:
        _last_net_error[0] = stage

# ----- LED alert state -----
_led_mode = ["off"]   # mutable cell so draw_reading() can update it without global
_LED_FLASH_PERIOD_MS = 1000
_LED_RED_BRIGHTNESS  = 80

# ----- Staleness / epoch constants -----
# Hard-coded to 6 minutes to match the Dexcom Share polling cadence.
# Deliberately not configurable: any longer would hide a sensor gap.
_STALE_LIMIT_MS = 6 * 60 * 1000

_PICO_EPOCH_OFFSET_S = 946_684_800 if time.gmtime(0)[0] == 2000 else 0

# Set to True after a successful ntptime.settime() call at boot.
_ntp_synced = False

_NTP_MAX_RETRIES = 3
_NTP_RETRY_DELAY_S = 2

# ----- Resilience settings -----
_REQUEST_TIMEOUT_S = 5
_WDT_TIMEOUT_MS = 8000
_WIFI_REJOIN_WAIT_S = 5
_WIFI_RETRY_DELAY_S = 5

# Pause between deactivating and reactivating the station interface.
_STA_RESET_PAUSE_MS = 500

# Main-screen loop: sample the buttons every tick so quick taps are not missed,
# but only repaint the reading about once a second (it shows whole minutes).
_POLL_TICK_S        = 0.05
_REDRAW_INTERVAL_MS = 1000

# Longest the menu waits for the opening button press to be released before it
# starts sampling. A stuck button therefore degrades to the old behaviour.
_MENU_ENTRY_RELEASE_MS = 2000

# Consecutive failed fetches (with the link reporting up or down) before the
# station interface is cycled as a last resort to restore routing.
_FETCH_FAILS_BEFORE_STA_RESET = 3
_FATAL_ERROR_PAUSE_S = 3

# ----- Settings paths -----
_SETTINGS_PATH     = "/settings.json"
_SETTINGS_TMP_PATH = "/settings.json.tmp"
_CRASH_LOG_PATH    = "/crash.json"
_CRASH_TEXT_MAX    = 400

# ----- AP / HTTP server constants -----
_AP_SSID_PREFIX         = "dexcom-pico-"
_AP_IP                  = "192.168.4.1"
_AP_PASSWORD_LEN        = 8
_HTTP_PORT              = 80
_HTTP_ACCEPT_TIMEOUT_S  = 0.25
_HTTP_CLIENT_TIMEOUT_S  = 3
_HTTP_MAX_HEADER_BYTES  = 2048
_HTTP_MAX_BODY_BYTES    = 1024
_SETUP_IDLE_TIMEOUT_MS  = 10 * 60 * 1000

# ----- Menu constants -----
_MENU_HOLD_MS         = 1500
_MENU_IDLE_TIMEOUT_MS = 60000

# ----- Units / region labels -----
_UNITS_MMOL = "mmol"
_UNITS_MGDL = "mgdl"
_UNIT_CHOICES = (_UNITS_MMOL, _UNITS_MGDL)
_UNIT_LABELS = {_UNITS_MMOL: "mmol/L", _UNITS_MGDL: "mg/dL"}

# Each entry: (stored_value, display_label)
_REGION_CHOICES = (("ous", "Rest of the world"), ("us", "United States"), ("jp", "Japan"))
_REGION_LABELS  = dict(_REGION_CHOICES)

# Per-unit alert specs: {unit: {key: (kind, (lo, hi, step), default)}}
_ALERT_SPECS = {
    _UNITS_MMOL: {
        "alert_low":  ("float", (2.0, 10.0, 0.1), 4.0),
        "alert_high": ("float", (7.0, 25.0, 0.5), 14.0),
    },
    _UNITS_MGDL: {
        "alert_low":  ("int", (40, 180, 5),   70),
        "alert_high": ("int", (120, 450, 10), 180),
    },
}

# ----- Settings schema -----
# Each value: (kind, default_or_callable, extra)
# kind in ("str", "choice", "float", "int", "bool", "alert")
# "alert" kind: default and extra are resolved at runtime via _ALERT_SPECS + current units.
_SETTINGS_SCHEMA = {
    "wifi_ssid":        ("str",    lambda: _secret("WIFI_SSID"),                   32),
    "wifi_password":    ("str",    lambda: _secret("WIFI_PASSWORD"),               63),
    "dexcom_account_id":("str",    lambda: _secret("DEXCOM_ACCOUNT_ID"),           64),
    "dexcom_password":  ("str",    lambda: _secret("DEXCOM_PASSWORD"),             64),
    "dexcom_region":    ("choice", lambda: _secret("DEXCOM_REGION", "ous"),        tuple(k for k, _ in _REGION_CHOICES)),
    "units":            ("choice", _UNITS_MMOL,                                    _UNIT_CHOICES),
    "backlight":        ("float",  0.5,                                            (0.1, 1.0, 0.1)),
    "alert_low":        ("alert",  None,                                           None),
    "alert_high":       ("alert",  None,                                           None),
    "led_alerts":       ("bool",   True,                                           None),
}

# ----- Live settings dicts (mutated in place) -----
_settings = {}
_settings_saved = {}


def alert_spec(key, units):
    """
    Return (kind, (lo, hi, step), default) for an alert key and unit string.
    Falls back to mmol spec if units is unrecognised.
    """
    specs = _ALERT_SPECS.get(units, _ALERT_SPECS[_UNITS_MMOL])
    return specs[key]


def alert_defaults(units):
    """Return {"alert_low": default, "alert_high": default} for the given units."""
    lo_kind, _lo_range, lo_default = alert_spec("alert_low", units)   # noqa: F841
    hi_kind, _hi_range, hi_default = alert_spec("alert_high", units)  # noqa: F841
    return {"alert_low": lo_default, "alert_high": hi_default}


def setting_spec(key, settings):
    """
    Return (kind, (lo, hi, step)) for a schema key, resolving "alert" kind
    against the current units in settings.
    Raises KeyError if key is not in _SETTINGS_SCHEMA.
    """
    kind, _default, extra = _SETTINGS_SCHEMA[key]
    if kind == "alert":
        units = settings.get("units", _UNITS_MMOL)
        spec_kind, spec_range, _default = alert_spec(key, units)
        return spec_kind, spec_range
    return kind, extra


def apply_units_change(settings, new_units):
    """
    Return a copy of settings with units set to new_units.
    If new_units differs from the current units, alert_low and alert_high are
    reset to that unit's defaults. If the unit is unchanged, custom thresholds
    are preserved. The input dict is never mutated.
    """
    result = dict(settings)
    result["units"] = new_units
    if new_units != settings.get("units"):
        result.update(alert_defaults(new_units))
    return result


def default_settings():
    """Materialise defaults from the schema; callables are called at this point."""
    result = {}
    for key, spec in _SETTINGS_SCHEMA.items():
        kind, default, _extra = spec
        if kind == "alert":
            # Alert defaults depend on the units default; materialise after "units" is set.
            continue
        if callable(default):
            result[key] = default()
        else:
            result[key] = default
    # Now resolve alert defaults against the units that were just set.
    units = result.get("units", _UNITS_MMOL)
    result.update(alert_defaults(units))
    return result


def _snap_float(value, lo, hi, step):
    """Clamp value into [lo, hi] and snap to nearest step, rounded to 1 dp."""
    value = max(lo, min(hi, value))
    steps = round((value - lo) / step)
    snapped = lo + steps * step
    snapped = max(lo, min(hi, snapped))
    return round(snapped, 1)


def _snap_int(value, lo, hi, step):
    """Clamp value into [lo, hi] and snap to nearest step."""
    value = max(lo, min(hi, int(value)))
    steps = round((value - lo) / step)
    snapped = lo + steps * step
    return max(lo, min(hi, int(snapped)))


def _coerce_value(kind, extra, val):
    """
    Coerce val according to (kind, extra). Returns the coerced value.
    Raises on failure so the caller can keep the default.
    Does not handle "alert" kind - that is resolved before this call.
    """
    if kind == "str":
        max_len = extra if extra is not None else 256
        return str(val)[:max_len]
    elif kind == "choice":
        choices = extra
        if val in choices:
            return val
        raise ValueError("not a valid choice")
    elif kind == "float":
        lo, hi, step = extra
        val = float(val)
        return _snap_float(val, lo, hi, step)
    elif kind == "int":
        lo, hi, step = extra
        val = int(float(val)) if not isinstance(val, bool) else int(val)
        return _snap_int(val, lo, hi, step)
    elif kind == "bool":
        if isinstance(val, bool):
            return val
        elif val in ("1", "on", "true", "True", 1):
            return True
        elif val in ("0", "off", "false", "False", 0):
            return False
        else:
            return bool(val)
    raise ValueError("unknown kind")


def validate_settings(raw):
    """
    Validate and coerce a raw dict into a clean settings dict.
    Starts from defaults, then merges and validates each schema key found in raw.
    Processes "units" first so "alert" thresholds resolve against the right unit.
    Never raises; a non-dict raw returns defaults.
    """
    result = default_settings()
    if not isinstance(raw, dict):
        return result

    # --- Pass 1: validate "units" first so alert specs are correct ---
    if "units" in raw:
        try:
            coerced = _coerce_value("choice", _UNIT_CHOICES, raw["units"])
            result["units"] = coerced
        except Exception:
            pass
        # Re-seed alert defaults for the resolved unit so absent alert keys in
        # raw produce the correct defaults (e.g. {"units": "mgdl"} -> 70/180).
        # Pass 2 still lets explicit raw alert values override these seeds.
        result.update(alert_defaults(result.get("units", _UNITS_MMOL)))

    # --- Pass 2: all other keys ---
    for key, spec in _SETTINGS_SCHEMA.items():
        if key == "units" or key not in raw:
            continue
        kind, _default, extra = spec
        val = raw[key]
        try:
            if kind == "alert":
                units = result.get("units", _UNITS_MMOL)
                a_kind, a_range, _a_def = alert_spec(key, units)
                result[key] = _coerce_value(a_kind, a_range, val)
            else:
                result[key] = _coerce_value(kind, extra, val)
        except Exception:
            pass  # keep default on coercion failure

    # --- Enforce alert_low < alert_high (per-unit pair check) ---
    units = result.get("units", _UNITS_MMOL)
    if result["alert_low"] >= result["alert_high"]:
        result.update(alert_defaults(units))
    return result


def settings_changed(a, b):
    """Compare two settings dicts key-wise; floats compared after round(..., 2)."""
    for key in _SETTINGS_SCHEMA:
        va = a.get(key)
        vb = b.get(key)
        if isinstance(va, float) or isinstance(vb, float):
            if round(va or 0.0, 2) != round(vb or 0.0, 2):
                return True
        else:
            if va != vb:
                return True
    return False


def serialise_settings(settings):
    """Return JSON string of only the schema keys (no unknown/secret leakage)."""
    out = {}
    for key in _SETTINGS_SCHEMA:
        out[key] = settings.get(key)
    return json.dumps(out)


def load_settings(path=_SETTINGS_PATH):
    """
    Load settings from the given path. Returns validate_settings(parsed) on success,
    or default_settings() on any error (missing file, JSON error, wrong type).
    """
    try:
        with open(path) as f:
            raw = json.load(f)
        return validate_settings(raw)
    except Exception:
        return default_settings()


def save_settings(settings, path=_SETTINGS_PATH, tmp_path=_SETTINGS_TMP_PATH):
    """
    Atomically write settings to path via tmp_path + rename.
    Returns True on success, False on any exception.
    """
    try:
        data = serialise_settings(settings)
        with open(tmp_path, "w") as f:
            f.write(data)
            f.flush()
        try:
            os.rename(tmp_path, path)
        except OSError:
            try:
                os.remove(path)
            except Exception:
                pass
            os.rename(tmp_path, path)
        return True
    except Exception:
        return False


def apply_settings():
    """Apply side-effectful settings to hardware."""
    try:
        display.set_backlight(_settings["backlight"])
    except Exception:
        pass


def commit_settings():
    """
    Save settings to flash if they have changed since the last save.
    Clears the session whenever credentials or region changed, regardless of
    whether the save succeeded (in-memory values have already changed).
    Returns True if saved, False if unchanged or save failed.
    """
    if not settings_changed(_settings, _settings_saved):
        return False
    # Check if credentials/region changed before saving
    cred_keys = ("dexcom_account_id", "dexcom_password", "dexcom_region")
    creds_changed = any(_settings.get(k) != _settings_saved.get(k) for k in cred_keys)
    ok = save_settings(_settings)
    if ok:
        _settings_saved.update(_settings)
    # Always clear the session when creds changed, even if save failed,
    # because in-memory settings already reflect the new values.
    if creds_changed:
        _session[0] = None
    return ok


# ----- Crash log -----

def read_crash_log(path=_CRASH_LOG_PATH):
    """Read crash log from flash; returns zeros/empty on missing or corrupt file."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return {
            "crashes":      int(data.get("crashes", 0)),
            "wdt_resets":   int(data.get("wdt_resets", 0)),
            "last_error":   str(data.get("last_error", "")),
            "last_uptime_s":int(data.get("last_uptime_s", 0)),
        }
    except Exception:
        return {"crashes": 0, "wdt_resets": 0, "last_error": "", "last_uptime_s": 0}


def write_crash_log(exc, uptime_s, path=_CRASH_LOG_PATH):
    """Write crash info to flash. Wrapped in try/except so it cannot mask a reboot."""
    try:
        current = read_crash_log(path)
        buf = uio.StringIO()
        try:
            import sys as _sys
            _sys.print_exception(exc, buf)
        except Exception:
            buf.write(str(exc))
        err_text = buf.getvalue()[:_CRASH_TEXT_MAX]
        current["crashes"]      += 1
        current["last_error"]   = err_text
        current["last_uptime_s"] = uptime_s
        with open(path, "w") as f:
            f.write(json.dumps(current))
    except Exception:
        pass


def _bump_wdt_resets(path=_CRASH_LOG_PATH):
    """Increment wdt_resets counter in the crash log. Called at boot after WDT reset."""
    try:
        current = read_crash_log(path)
        current["wdt_resets"] += 1
        with open(path, "w") as f:
            f.write(json.dumps(current))
    except Exception:
        pass


# ----- Buttons -----
button_a = Button(12)
button_b = Button(13)
button_x = Button(14)
button_y = Button(15)

# ----- RGB LED -----
led = RGBLED(26, 27, 28)
led.set_rgb(0, 0, 0)

# ----- Watchdog -----
_wdt = [None]


def feed_watchdog():
    """Feed the hardware watchdog if it has been armed."""
    if _wdt[0] is not None:
        _wdt[0].feed()


# ----- Wi-Fi -----
wlan = network.WLAN(network.STA_IF)


def connect_wifi(ssid, password, timeout=20):
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        try:
            wlan.connect(ssid, password)
        except Exception:
            pass
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            feed_watchdog()
            if time.ticks_diff(time.ticks_ms(), t0) > timeout * 1000:
                return None
            draw_status("Connecting to Wi-Fi", sub="Press any button to cancel", dots=True)
            if any_button_pressed():
                return None
            time.sleep(0.1)
    return wlan.ifconfig()


def rejoin_wifi(timeout=_WIFI_REJOIN_WAIT_S):
    """Re-associate with the AP if the link has dropped since boot."""
    if wlan.isconnected():
        return True
    try:
        if not wlan.active():
            wlan.active(True)
        wlan.connect(_settings["wifi_ssid"], _settings["wifi_password"])
    except Exception:
        pass
    t0 = time.ticks_ms()
    while not wlan.isconnected():
        feed_watchdog()
        if time.ticks_diff(time.ticks_ms(), t0) > timeout * 1000:
            return False
        time.sleep(0.1)
    return True


def reset_sta_interface():
    """Cycle the station interface off and on.

    The CYW43 driver makes whichever interface was brought up most recently
    lwIP's default route. After an access-point session that default is gone,
    so the station keeps its address and link but cannot reach the internet.
    Reactivating the station re-runs the driver's network init, which restores
    the route. It also clears any stuck join state.
    """
    try:
        wlan.active(False)
    except Exception:
        pass
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < _STA_RESET_PAUSE_MS:
        feed_watchdog()
        time.sleep(0.1)
    try:
        wlan.active(True)
    except Exception:
        pass


def restart_device(msg):
    """Show msg briefly, then hard-reset the board.

    Once the access point has been up, only a hard reset reliably restores the
    station's default route on the CYW43 driver: cycling the interface with
    active(False)/active(True) was verified on hardware not to fix it. Every
    exit from setup mode therefore reboots, as does the poll loop when fetches
    keep failing with "no route to host" while the link reports up.
    """
    try:
        draw_status(msg, sub="Restarting...")
    except Exception:
        pass
    feed_watchdog()
    time.sleep(1)
    machine.reset()


# Marker file written before the post-submit reboot so the next boot knows to
# verify the new credentials and report failures back into setup mode.
_SETUP_VERIFY_MARKER = "/setup_verify"


def set_setup_verify_pending():
    """Flag that the next boot must verify freshly submitted credentials."""
    try:
        with open(_SETUP_VERIFY_MARKER, "w") as f:
            f.write("1")
    except Exception:
        pass


def take_setup_verify_pending():
    """Return True (once) if the previous boot submitted new credentials."""
    try:
        os.stat(_SETUP_VERIFY_MARKER)
    except Exception:
        return False
    try:
        os.remove(_SETUP_VERIFY_MARKER)
    except Exception:
        pass
    return True


def is_no_route_error(err):
    """True when a recorded network error is EHOSTUNREACH (MicroPython errno 113)."""
    return bool(err) and ("OSError(113" in err or "[Errno 113]" in err)


def recover_after_fetch_failures(failures):
    """Track consecutive failed fetches and recover when they persist.

    Called by the poll loop with the running count after each failed fetch.
    Once the count reaches _FETCH_FAILS_BEFORE_STA_RESET: if the link is up
    but the last error was "no route to host", the network stack is broken
    and only a reboot fixes it; otherwise the station is cycled and rejoined
    to clear a stuck join. Returns the updated count (0 after a recovery).
    """
    if failures < _FETCH_FAILS_BEFORE_STA_RESET:
        return failures
    try:
        link_up = wlan.isconnected()
    except Exception:
        link_up = False
    if link_up and is_no_route_error(_last_net_error[0]):
        restart_device("Network route lost")
        return 0   # unreachable on device; keeps tests and stubs sane
    reset_sta_interface()
    rejoin_wifi()
    return 0


def any_button_pressed():
    """Read all four buttons (no short-circuit so all edge states are consumed)."""
    a = button_a.read()
    b = button_b.read()
    x = button_x.read()
    y = button_y.read()
    return a or b or x or y


def wait_buttons_released(timeout_ms=None):
    """Spin until all four buttons are physically released.
    timeout_ms caps the wait (None = unbounded) so a stuck button cannot hang a screen.
    """
    t0 = time.ticks_ms()
    while True:
        feed_watchdog()
        if not button_a.raw() and not button_b.raw() and not button_x.raw() and not button_y.raw():
            break
        if timeout_ms is not None and time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
            break
        time.sleep(0.05)


# ----- Drawing helpers -----
def clear(bg_pen=BLACK):
    display.set_pen(bg_pen)
    display.clear()


def draw_text(text, x, y, color=WHITE, scale=2, wrap=WIDTH, font=None):
    display.set_pen(color)
    if font:
        try:
            display.set_font(font)
            if font == "sans":
                display.set_thickness(6)
        except Exception:
            pass
    display.text(text, x, y, wrap, scale)


def draw_status(msg, sub=None, dots=False):
    clear()
    draw_text("Dexcom Monitor", 8, 8, CYAN, scale=2)
    draw_text(msg + ("." * ((time.ticks_ms() // 300) % 4) if dots else ""), 8, 32, GREY, scale=2)
    if sub:
        draw_text(sub, 8, 56, GREY, scale=2)
    display.update()


def draw_bottom_right(msg, color=GREY, scale=1, y=None, right_margin=4):
    """Draw bitmap8 text right-aligned to the display edge minus right_margin px."""
    char_h = 8
    try:
        display.set_font("bitmap8")
        text_w = display.measure_text(msg, scale)
    except Exception:
        text_w = len(msg) * char_h * scale
    x = max(0, WIDTH - text_w - right_margin)
    if y is None:
        y = HEIGHT - (char_h * scale) - 4
    draw_text(msg, x, y, color=color, scale=scale, wrap=WIDTH, font="bitmap8")


def draw_thick_line(x1, y1, x2, y2, thickness):
    """
    Draw a solid line about `thickness` px wide using 1 px display.line calls.
    Copies are shifted along whichever axis is closer to perpendicular to the
    line, so diagonal strokes fill in rather than hatching.
    """
    ldx = x2 - x1
    ldy = y2 - y1
    length = math.sqrt(ldx * ldx + ldy * ldy)
    if length == 0:
        display.line(int(x1), int(y1), int(x2), int(y2))
        return
    if abs(ldx) >= abs(ldy):
        sx, sy = 0, 1
        step = abs(ldx) / length
    else:
        sx, sy = 1, 0
        step = abs(ldy) / length
    count = max(1, int(thickness / step + 0.5))
    for i in range(count):
        offset = i - count // 2
        display.line(
            int(x1) + sx * offset,
            int(y1) + sy * offset,
            int(x2) + sx * offset,
            int(y2) + sy * offset
        )


def draw_trend(trend, x0, y0, w, h, color=WHITE, size=None, thickness=5,
               head_len=None, head_width=None):
    """
    Draw the trend arrow centred in the box (x0, y0, w, h).
    size is the half-length of the arrow shaft in px (defaults to a sixth of
    the box's shorter side); thickness is the stroke width in px. head_len is
    how far each barb runs back from the tip along the shaft and head_width
    how far it spreads to the side; both default to a proportion of size.
    """
    display.set_pen(color)
    cx = x0 + w // 2
    cy = y0 + h // 2
    if size is None:
        size = min(w, h) // 6
    ah = head_len if head_len is not None else int(size * _ARROW_HEAD_RATIO)
    aw = head_width if head_width is not None else ah // 1.8
    # Double arrows sit one shaft-length apart, or wider if the heads would touch
    spacing = max(size * 1.0, 2 * aw + thickness + 2)

    def arrow(dx, dy, offset_x=0.0):
        center_x = cx + offset_x
        x1 = center_x - dx * size
        y1 = cy - dy * size
        x2 = center_x + dx * size
        y2 = cy + dy * size
        pdx, pdy = -dy, dx
        draw_thick_line(x1, y1, x2, y2, thickness)
        hx = x2
        hy = y2
        draw_thick_line(hx, hy, hx - dx * ah + pdx * aw, hy - dy * ah + pdy * aw, thickness)
        draw_thick_line(hx, hy, hx - dx * ah - pdx * aw, hy - dy * ah - pdy * aw, thickness)

    if trend == "doubleUp":
        arrow(0, -1, -spacing // 2)
        arrow(0, -1, spacing // 2)
    elif trend == "singleUp":
        arrow(0, -1)
    elif trend == "fortyFiveUp":
        arrow(1, -1)
    elif trend == "flat":
        arrow(1, 0)
    elif trend == "fortyFiveDown":
        arrow(1, 1)
    elif trend == "singleDown":
        arrow(0, 1)
    elif trend == "doubleDown":
        arrow(0, 1, -spacing // 2)
        arrow(0, 1, spacing // 2)
    else:
        return


def age_unix_ms(ts_unix_ms):
    """Return age in ms for a Unix-epoch-ms timestamp, or None."""
    if ts_unix_ms is None:
        return None
    now_unix_ms = (time.time() + _PICO_EPOCH_OFFSET_S) * 1000
    diff_ms = now_unix_ms - ts_unix_ms
    return max(0, diff_ms)


def draw_reading(state):
    """
    Render the main glucose reading screen.
    Converts the stored mg_dl value to the display unit at draw time.
    Uses _STALE_LIMIT_MS (hard-coded 6 min) for the staleness gate.
    """
    clear()
    left_w = WIDTH // 2
    units  = _settings.get("units", _UNITS_MMOL)
    mg_dl  = state.get("mg_dl")
    trend  = state.get("trend")

    ts_ms   = state.get("ts_ms")
    recv_ms = state.get("received_ms")

    if _ntp_synced and ts_ms is not None:
        age_ms = age_unix_ms(ts_ms)
        mins = (age_ms // 60000) if age_ms is not None else None
    else:
        if recv_ms is not None:
            raw_diff = time.ticks_diff(time.ticks_ms(), recv_ms)
            age_ms = max(0, raw_diff)
            mins = age_ms // 60000
        else:
            age_ms = None
            mins = None

    stale = (age_ms is not None and age_ms > _STALE_LIMIT_MS)
    if stale:
        mg_dl = None

    # Convert to display units at draw time
    display_val = convert_mg_dl(mg_dl, units)
    show_text   = format_glucose(display_val, units)
    ulabel      = unit_label(units)

    color = WHITE
    _out_of_range = False
    if display_val is not None:
        try:
            numeric = float(display_val)
            al = _settings.get("alert_low")
            ah = _settings.get("alert_high")
            if al is not None and ah is not None and (numeric < al or numeric > ah):
                color = RED
                _out_of_range = True
        except Exception:
            pass

    mins_txt = ("Last reading %d mins ago" % mins) if mins is not None else ""
    draw_text(mins_txt, 8, 8, WHITE, scale=2, font="bitmap8")
    draw_text(show_text, 8, HEIGHT // 2 - 0, color, scale=3, font="sans")
    draw_text(ulabel, 8, HEIGHT - 24, WHITE, scale=2, font="bitmap8")

    if display_val is not None:
        draw_trend(trend, left_w, 0, WIDTH - left_w, HEIGHT, WHITE)

    if stale:
        _led_mode[0] = "solid_red"
    elif _out_of_range:
        _led_mode[0] = "flash_red"
    else:
        _led_mode[0] = "off"

    if stale:
        prev_mg_dl = state.get("mg_dl")
        if prev_mg_dl is not None:
            prev_val = convert_mg_dl(prev_mg_dl, units)
            prev_txt = "Previous: " + format_glucose(prev_val, units)
            draw_previous_corner(prev_txt, trend)

    display.update()


def draw_previous_corner(prev_txt, trend):
    """
    Draw the stale-state "Previous: X.X" text in the bottom-right corner with
    a small trend arrow to its right, so the last known direction stays
    visible. The text keeps its scale-2 size; the arrow box is centred on
    the text row and the text shifts left to make room for it.
    """
    text_y = HEIGHT - 24
    if trend not in _ARROW_TRENDS:
        draw_bottom_right(prev_txt, WHITE, scale=2, y=text_y)
        return
    box = _PREV_ARROW_BOX_PX
    arrow_x = WIDTH - 4 - box
    arrow_y = text_y + 8 - box // 2
    draw_bottom_right(prev_txt, WHITE, scale=2, y=text_y,
                      right_margin=4 + box + _PREV_ARROW_GAP_PX)
    draw_trend(trend, arrow_x, arrow_y, box, box, WHITE,
               size=_PREV_ARROW_SIZE_PX, thickness=_PREV_ARROW_THICKNESS_PX,
               head_len=_PREV_ARROW_HEAD_LEN_PX, head_width=_PREV_ARROW_HEAD_WIDTH_PX)


def update_led():
    """Drive the RGB LED according to the current _led_mode."""
    if not _settings.get("led_alerts", True):
        led.set_rgb(0, 0, 0)
        return
    mode = _led_mode[0]
    if mode == "solid_red":
        led.set_rgb(_LED_RED_BRIGHTNESS, 0, 0)
    elif mode == "flash_red":
        phase = time.ticks_ms() % _LED_FLASH_PERIOD_MS
        if phase < _LED_FLASH_PERIOD_MS // 2:
            led.set_rgb(_LED_RED_BRIGHTNESS, 0, 0)
        else:
            led.set_rgb(0, 0, 0)
    else:
        led.set_rgb(0, 0, 0)


# ----- Dexcom client helpers -----
def _dexcom_base(region):
    return _DEXCOM_BASE_URLS.get(region, _DEXCOM_BASE_URLS["ous"])


def _dexcom_app_id(region):
    return _DEXCOM_APP_ID_JP if region == "jp" else _DEXCOM_APP_ID_DEFAULT


def _close_quietly(resp):
    try:
        resp.close()
    except Exception:
        pass


def dexcom_login(region, account_id, password):
    url = _dexcom_base(region) + "General/LoginPublisherAccountById"
    body = json.dumps({
        "accountId": account_id,
        "password": password,
        "applicationId": _dexcom_app_id(region),
    })
    resp = None
    try:
        resp = requests.post(url, data=body, headers=_DEXCOM_HEADERS, timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code != 200:
            note_net_error("login", "HTTP %d" % resp.status_code)
            return None
        session_id = resp.json()
        if not session_id or session_id == _DEXCOM_NULL_SESSION:
            note_net_error("login", "null session")
            return None
        return session_id
    except Exception as e:
        note_net_error("login", repr(e))
        return None
    finally:
        if resp is not None:
            _close_quietly(resp)


def dexcom_fetch_latest(region, session_id):
    base = _dexcom_base(region)
    url = (base
           + "Publisher/ReadPublisherLatestGlucoseValues"
           + "?sessionId=" + session_id
           + "&minutes=1440"
           + "&maxCount=1")
    resp = None
    try:
        resp = requests.post(url, data="{}", headers=_DEXCOM_HEADERS, timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code != 200:
            note_net_error("fetch", "HTTP %d" % resp.status_code)
            return None
        readings = resp.json()
        _last_net_error[0] = None   # a successful fetch clears the last failure
        return readings
    except Exception as e:
        note_net_error("fetch", repr(e))
        return None
    finally:
        if resp is not None:
            _close_quietly(resp)


def _parse_dexcom_timestamp(wt_str):
    try:
        start = wt_str.index("(") + 1
        end   = wt_str.index(")")
        inner = wt_str[start:end]
        for sep in ("+", "-"):
            idx = inner.find(sep, 1)
            if idx != -1:
                return int(inner[:idx])
        return int(inner)
    except Exception:
        return None


def parse_dexcom_reading(raw_list):
    """
    Parse the Dexcom Share API response and return a reading dict.
    Stores the raw mg/dL value; conversion to display units happens at draw time.
    Returns None on any error.
    """
    if not raw_list:
        return None
    try:
        r = raw_list[0]
        mg_dl = int(r["Value"])
        trend_raw = r.get("Trend", "")
        trend = _TREND_MAP.get(trend_raw, None)
        wt_str = r.get("WT") or r.get("DT") or ""
        ts_ms = _parse_dexcom_timestamp(wt_str)
        return {
            "mg_dl": mg_dl,
            "trend": trend,
            "ts_ms": ts_ms,
        }
    except Exception:
        return None


def fetch_latest():
    """
    Returns a parsed reading dict {mg_dl, trend, ts_ms} on success,
    or None on any failure.
    """
    account_id = _settings.get("dexcom_account_id", "")
    password   = _settings.get("dexcom_password", "")
    region     = _settings.get("dexcom_region", "ous")

    if not account_id or not password:
        draw_status("Config error", sub="Menu > Wi-Fi setup to set them")
        return None

    if _session[0] is None:
        feed_watchdog()
        _session[0] = dexcom_login(region, account_id, password)
        if _session[0] is None:
            return None

    feed_watchdog()
    raw = dexcom_fetch_latest(region, _session[0])

    if raw is None:
        _session[0] = None
        feed_watchdog()
        _session[0] = dexcom_login(region, account_id, password)
        if _session[0] is None:
            return None
        feed_watchdog()
        raw = dexcom_fetch_latest(region, _session[0])
        if raw is None:
            return None

    return parse_dexcom_reading(raw)


# ----- Pure HTTP/form helpers (testable) -----

def html_escape(s):
    """Escape a string for safe HTML insertion."""
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace('"', "&quot;")
    s = s.replace("'", "&#x27;")
    return s


def parse_query(qs):
    """
    Parse a URL query string into a dict. Values are percent-decoded.
    Repeated keys: last value wins.
    """
    result = {}
    if not qs:
        return result
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[_pct_decode(k)] = _pct_decode(v)
        elif pair:
            result[_pct_decode(pair)] = ""
    return result


def _pct_decode(s):
    """Decode percent-encoding and + as space. Handles multibyte UTF-8 sequences."""
    s = s.replace("+", " ")
    if "%" not in s:
        return s
    out = bytearray()
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                byte_val = int(s[i + 1:i + 3], 16)
                out.append(byte_val)
                i += 3
            except ValueError:
                out.extend(s[i].encode("utf-8"))
                i += 1
        else:
            out.extend(s[i].encode("utf-8"))
            i += 1
    try:
        return out.decode("utf-8")
    except Exception:
        return out.decode("latin-1")


def parse_form(body):
    """
    Parse application/x-www-form-urlencoded body.
    body may be bytes or str. Returns a dict; repeated keys: last value wins.
    """
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8")
        except Exception:
            body = body.decode("latin-1")
    return parse_query(body)


def parse_http_request(head):
    """
    Parse HTTP request headers from bytes up to (not including) \\r\\n\\r\\n.
    Returns (method, path, query_dict, headers_dict) or raises ValueError on malformed input.
    """
    if isinstance(head, (bytes, bytearray)):
        try:
            head = head.decode("utf-8")
        except Exception:
            head = head.decode("latin-1")
    lines = head.split("\r\n")
    if not lines:
        raise ValueError("empty request")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method = parts[0].upper()
    raw_path = parts[1]
    if "?" in raw_path:
        path, qs = raw_path.split("?", 1)
    else:
        path, qs = raw_path, ""
    query = parse_query(qs)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return method, path, query, headers


def dedupe_networks(raw_scan):
    """
    Post-process wlan.scan() output.
    raw_scan: list of tuples (ssid_bytes, bssid, channel, rssi, security, hidden).
    Returns list of (ssid_str, rssi, secure) sorted by rssi desc, capped at 20.
    """
    seen = {}
    for entry in raw_scan:
        try:
            ssid_bytes = entry[0]
            rssi       = int(entry[3])
            security   = int(entry[4])
            if isinstance(ssid_bytes, (bytes, bytearray)):
                try:
                    ssid = ssid_bytes.decode("utf-8")
                except Exception:
                    ssid = ssid_bytes.decode("latin-1")
            else:
                ssid = str(ssid_bytes)
            if not ssid.strip():
                continue  # skip empty/hidden
            if ssid not in seen or rssi > seen[ssid][0]:
                seen[ssid] = (rssi, security > 0)
        except Exception:
            continue
    networks = [(ssid, info[0], info[1]) for ssid, info in seen.items()]
    networks.sort(key=lambda x: x[1], reverse=True)
    return networks[:20]


def scan_networks():
    """Perform a Wi-Fi scan and return dedupe_networks output. Returns [] on failure."""
    try:
        feed_watchdog()
        # wlan.scan() is blocking C code, typically 1-3s.
        # The watchdog is fed immediately before/after; it cannot be fed mid-operation.
        raw = wlan.scan()
        feed_watchdog()
        return dedupe_networks(raw)
    except Exception:
        return []


def wifi_qr_payload(ssid, password):
    """
    Build the Wi-Fi QR code payload string.
    Escapes \\, ;, ,, : with a leading backslash in ssid and password.
    """
    def _esc(s):
        s = s.replace("\\", "\\\\")
        s = s.replace(";", "\\;")
        s = s.replace(",", "\\,")
        s = s.replace(":", "\\:")
        return s
    auth = "WPA" if password else "nopass"
    return "WIFI:T:%s;S:%s;P:%s;;" % (auth, _esc(ssid), _esc(password))


def render_setup_page(networks, settings, error=None):
    """
    Render the Wi-Fi/Dexcom setup HTML page.
    networks: list of (ssid, rssi, secure).
    settings: current settings dict for pre-filling fields.
    error: optional error string to display.
    Returns an HTML string.
    """
    saved_ssid = settings.get("wifi_ssid", "")
    saved_account = settings.get("dexcom_account_id", "")
    saved_region = settings.get("dexcom_region", "ous")

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html><head>")
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append("<title>Dexcom Pico setup</title>")
    parts.append("<style>")
    parts.append("body{font-family:sans-serif;max-width:480px;margin:0 auto;padding:16px}")
    parts.append("label{display:block;margin-top:12px;font-weight:bold}")
    parts.append("input,select{width:100%;padding:6px;box-sizing:border-box;margin-top:4px}")
    parts.append(".error{color:red;margin:8px 0}")
    parts.append("button{margin-top:16px;padding:10px 20px;font-size:1em}")
    parts.append("</style></head><body>")
    parts.append("<h1>Dexcom Pico setup</h1>")
    if error:
        parts.append('<p class="error">%s</p>' % html_escape(error))
    parts.append('<p><a href="/?rescan=1">Rescan networks</a></p>')
    parts.append('<form method="POST" action="/save">')

    # SSID select
    parts.append("<label>Wi-Fi network</label>")
    parts.append('<select name="ssid">')
    parts.append('<option value="">-- select network --</option>')
    for ssid, rssi, secure in networks:
        sel = ' selected' if ssid == saved_ssid else ''
        lock = " (secured)" if secure else ""
        parts.append('<option value="%s"%s>%s (%d dBm%s)</option>' % (
            html_escape(ssid), sel, html_escape(ssid), rssi, lock))
    parts.append("</select>")

    parts.append("<label>Other / hidden network</label>")
    parts.append('<input type="text" name="ssid_other" placeholder="SSID" maxlength="32">')

    parts.append("<label>Wi-Fi password</label>")
    parts.append('<input type="password" name="wifi_password" maxlength="63">')

    parts.append("<label>Dexcom account id</label>")
    parts.append('<input type="text" name="dexcom_account_id" value="%s" maxlength="64">' % html_escape(saved_account))

    parts.append("<label>Dexcom password</label>")
    parts.append('<input type="password" name="dexcom_password" maxlength="64">')

    parts.append("<label>Dexcom region</label>")
    parts.append('<select name="dexcom_region">')
    for region_val, region_label in _REGION_CHOICES:
        sel = ' selected' if region_val == saved_region else ''
        parts.append('<option value="%s"%s>%s</option>' % (region_val, sel, region_label))
    parts.append("</select>")

    parts.append('<button type="submit">Save and connect</button>')
    parts.append("</form>")
    parts.append("<p><small>Connect to the Pico's Wi-Fi AP, then open http://192.168.4.1 in your browser. HTTPS is not supported.</small></p>")
    parts.append("</body></html>")
    return "".join(parts)


def validate_setup_form(form, settings):
    """
    Validate the submitted setup form.
    Returns (new_settings_dict, None) on success or (None, error_msg) on failure.
    """
    ssid_other = form.get("ssid_other", "").strip()
    ssid = ssid_other if ssid_other else form.get("ssid", "").strip()
    if not ssid:
        return None, "Please select or enter a Wi-Fi network SSID"

    wifi_password = form.get("wifi_password", "")
    account_id = form.get("dexcom_account_id", "").strip()
    if not account_id:
        return None, "Dexcom account id is required"

    dexcom_password = form.get("dexcom_password", "")
    region = form.get("dexcom_region", "ous")
    if region not in _REGION_LABELS:
        return None, "Invalid Dexcom region"

    new = dict(settings)
    new["wifi_ssid"]          = ssid[:32]
    new["wifi_password"]      = wifi_password[:63]
    new["dexcom_account_id"]  = account_id[:64]
    new["dexcom_password"]    = dexcom_password[:64]
    new["dexcom_region"]      = region
    return new, None


def _send_response(conn, status, body, content_type="text/html; charset=utf-8"):
    """Send an HTTP/1.0 response in chunks, feeding the watchdog between them."""
    header = ("HTTP/1.0 %s\r\nContent-Type: %s\r\nConnection: close\r\n\r\n" % (status, content_type))
    try:
        conn.write(header.encode("utf-8"))
    except Exception:
        return
    if isinstance(body, str):
        body = body.encode("utf-8")
    offset = 0
    chunk = 512
    while offset < len(body):
        feed_watchdog()
        try:
            conn.write(body[offset:offset + chunk])
        except Exception:
            break
        offset += chunk


def ap_ssid():
    """Return the AP SSID using the last 4 hex chars of machine.unique_id()."""
    try:
        uid = machine.unique_id()
        suffix = "".join("%02x" % b for b in uid)[-4:]
        return _AP_SSID_PREFIX + suffix
    except Exception:
        return "dexcom-pico-setup"


def generate_ap_password():
    """Generate a random 8-char AP password from an unambiguous alphabet."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    raw = os.urandom(_AP_PASSWORD_LEN)
    return "".join(alphabet[b % len(alphabet)] for b in raw)


def start_ap(ssid, password):
    """Bring up WPA2 access point; STA stays active for scanning."""
    try:
        wlan.disconnect()
    except Exception:
        pass
    ap = network.WLAN(network.AP_IF)
    try:
        ap.config(ssid=ssid, key=password)
    except (TypeError, ValueError):
        try:
            ap.config(essid=ssid, password=password)
        except Exception:
            pass
    ap.active(True)
    t0 = time.ticks_ms()
    while not ap.active():
        feed_watchdog()
        if time.ticks_diff(time.ticks_ms(), t0) > 5000:
            break
        time.sleep(0.1)
    return ap


def stop_ap(ap):
    """Tear down the access point."""
    try:
        ap.active(False)
    except Exception:
        pass
    gc.collect()


def draw_setup_screen(ssid, password, phase):
    """Draw the Wi-Fi setup waiting screen."""
    clear()
    draw_text("Wi-Fi setup", 8, 8, CYAN, scale=2)
    draw_text("1. Join Wi-Fi:", 8, 32, WHITE, scale=2, font="bitmap8")
    draw_text(ssid, 8, 48, YELLOW, scale=2, font="bitmap8")
    draw_text("Password:", 8, 64, WHITE, scale=2, font="bitmap8")
    draw_text(password, 8, 80, YELLOW, scale=2, font="bitmap8")
    draw_text("2. Open:", 8, 100, WHITE, scale=2, font="bitmap8")
    draw_text("http://192.168.4.1", 8, 116, CYAN, scale=2, font="bitmap8")
    draw_text(phase, 8, 136, GREY, scale=2, font="bitmap8")
    draw_text("Hold X to cancel", 8, HEIGHT - 20, GREY, scale=1, font="bitmap8")

    # QR code on the right half (optional)
    if _qrcode_mod is not None:
        try:
            qr = _qrcode_mod.QRCode()
            qr.set_text(wifi_qr_payload(ssid, password))
            qr_size = qr.get_size()
            max_px = 110
            scale_qr = max(1, max_px // qr_size)
            ox = WIDTH // 2 + 8
            oy = 8
            # White background for quiet zone
            display.set_pen(WHITE)
            qr_px = qr_size * scale_qr
            display.rectangle(ox - 2, oy - 2, qr_px + 4, qr_px + 4)
            display.set_pen(BLACK)
            for y in range(qr_size):
                for x in range(qr_size):
                    if qr.get_module(x, y):
                        display.rectangle(ox + x * scale_qr, oy + y * scale_qr, scale_qr, scale_qr)
        except Exception:
            pass  # QR failure is non-fatal; text instructions always present

    display.update()


def serve_setup(ap_info, networks, status_msg):
    """
    Run the HTTP server for the setup page.
    Returns a new settings dict on successful form submit, or None on cancel/timeout.
    ap_info: dict with 'ssid' and 'password' keys.
    """
    listener = None
    try:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        addr = socket.getaddrinfo("0.0.0.0", _HTTP_PORT)[0][-1]
        listener.bind(addr)
        listener.listen(2)
        listener.settimeout(_HTTP_ACCEPT_TIMEOUT_S)

        hold_det = HoldDetector(_MENU_HOLD_MS)
        idle_start = time.ticks_ms()
        last_draw = 0
        phase = status_msg if status_msg else "Waiting for phone..."
        dot_count = [0]

        while True:
            feed_watchdog()
            update_led()

            # Inactivity timeout
            if time.ticks_diff(time.ticks_ms(), idle_start) > _SETUP_IDLE_TIMEOUT_MS:
                return None

            # X hold -> cancel
            ev = hold_det.update(button_x.raw(), time.ticks_ms())
            if ev == "hold":
                return None

            # Redraw at most once per second
            now_ms = time.ticks_ms()
            if time.ticks_diff(now_ms, last_draw) >= 1000:
                dot_count[0] = (dot_count[0] + 1) % 4
                dots = "." * dot_count[0]
                draw_setup_screen(
                    ap_info["ssid"],
                    ap_info["password"],
                    (phase + dots) if not status_msg else phase
                )
                last_draw = now_ms

            # Accept connection
            conn = None
            try:
                conn, _addr = listener.accept()
                idle_start = time.ticks_ms()  # reset idle timer on any connection
            except OSError:
                time.sleep(0.01)
                continue

            try:
                conn.settimeout(_HTTP_CLIENT_TIMEOUT_S)
                # Read headers
                head_buf = b""
                while b"\r\n\r\n" not in head_buf:
                    feed_watchdog()
                    try:
                        chunk = conn.recv(512)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    head_buf += chunk
                    if len(head_buf) > _HTTP_MAX_HEADER_BYTES:
                        _send_response(conn, "413 Request Too Large", "Too large")
                        break

                if b"\r\n\r\n" not in head_buf:
                    continue

                header_part, body_part = head_buf.split(b"\r\n\r\n", 1)

                try:
                    method, path, query, headers = parse_http_request(header_part)
                except ValueError:
                    _send_response(conn, "400 Bad Request", "Bad request")
                    continue

                # favicon -> 404
                if path == "/favicon.ico":
                    _send_response(conn, "404 Not Found", "")
                    continue

                if method == "GET":
                    if query.get("rescan") == "1":
                        networks = scan_networks()
                    page = render_setup_page(networks, _settings, status_msg if status_msg else None)
                    _send_response(conn, "200 OK", page)

                elif method == "POST" and path == "/save":
                    # Read remaining body
                    content_length = 0
                    try:
                        content_length = int(headers.get("content-length", "0"))
                    except Exception:
                        pass
                    if content_length > _HTTP_MAX_BODY_BYTES:
                        _send_response(conn, "413 Request Too Large", "Body too large")
                        continue
                    body = body_part
                    needed = content_length - len(body_part)
                    while needed > 0:
                        feed_watchdog()
                        try:
                            chunk = conn.recv(min(512, needed))
                        except OSError:
                            chunk = b""
                        if not chunk:
                            break
                        body += chunk
                        needed -= len(chunk)

                    form = parse_form(body)
                    new_settings, err = validate_setup_form(form, _settings)
                    if err:
                        page = render_setup_page(networks, _settings, err)
                        _send_response(conn, "200 OK", page)
                    else:
                        # Success: send redirect page, close, return new settings
                        ssid_used = new_settings.get("wifi_ssid", "")
                        success_page = (
                            "<!DOCTYPE html><html><head>"
                            '<meta name="viewport" content="width=device-width,initial-scale=1">'
                            "<title>Saved</title></head><body>"
                            "<h1>Saved</h1>"
                            "<p>Joining %s... watch the Pico's screen.</p>"
                            "</body></html>"
                        ) % html_escape(ssid_used)
                        _send_response(conn, "200 OK", success_page)
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                        return new_settings
                else:
                    _send_response(conn, "405 Method Not Allowed", "Not allowed")

            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    finally:
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass


def run_wifi_setup(status_msg=None):
    """
    Enter AP+HTTP setup mode. Never returns on device: every exit reboots.

    Cancel or timeout reboots so the station comes back with a working route.
    Submit persists the form, flags verification for the next boot, and reboots;
    ensure_wifi() then joins the new network and checks the Dexcom login, and
    re-enters setup with the reason if either fails.
    status_msg: optional error to show on the page (from a failed verification).
    """
    ap_ssid_str  = ap_ssid()
    ap_password  = generate_ap_password()
    ap_info      = {"ssid": ap_ssid_str, "password": ap_password}

    ap = start_ap(ap_ssid_str, ap_password)
    networks = scan_networks()

    result = serve_setup(ap_info, networks, status_msg)
    stop_ap(ap)
    if result is None:
        wait_buttons_released()
        restart_device("Leaving setup")
        return False   # unreachable on device

    _settings.update(result)
    commit_settings()
    set_setup_verify_pending()
    restart_device("Applying Wi-Fi settings")
    return True   # unreachable on device


# ----- Hold detector (pure, testable) -----

class HoldDetector:
    """
    Classify raw button state into 'press' (released before hold_ms),
    'hold' (held for hold_ms, reported once) or None.
    Pure: caller passes now_ms.
    """

    def __init__(self, hold_ms):
        self._hold_ms  = hold_ms
        self._down_at  = None
        self._fired    = False
        self._prev     = False

    def update(self, is_down, now_ms):
        """
        Call each tick with the current raw button state and monotonic ms.
        Returns 'press', 'hold', or None.
        """
        result = None
        if is_down and not self._prev:
            # Falling edge: button just pressed
            self._down_at = now_ms
            self._fired   = False
        elif is_down and self._prev:
            # Held: check for hold threshold
            if not self._fired and self._down_at is not None:
                if now_ms - self._down_at >= self._hold_ms:
                    self._fired = True
                    result = "hold"
        elif not is_down and self._prev:
            # Rising edge: button released
            if not self._fired and self._down_at is not None:
                result = "press"
            self._down_at = None
            self._fired   = False
        self._prev = is_down
        return result


# ----- Menu model (pure, testable) -----

_MENU_ITEMS = (
    ("backlight",      "Backlight"),
    ("units",          "Units"),
    ("alert_low",      "Alert low"),
    ("alert_high",     "Alert high"),
    ("dexcom_region",  "Region"),
    ("led_alerts",     "LED alerts"),
    ("action:wifi",    "Wi-Fi setup"),
    ("action:info",    "Device info"),
    ("action:restart", "Restart"),
)

_MENU_ITEM_COUNT = len(_MENU_ITEMS)


def format_setting(key, value):
    """Format a setting value for display in the menu."""
    if key == "backlight":
        return "%d%%" % int(round(value * 100))
    elif key == "units":
        return unit_label(value)
    elif key == "dexcom_region":
        return _REGION_LABELS.get(value, str(value))
    elif key == "led_alerts":
        return "On" if value else "Off"
    elif isinstance(value, float):
        return "%.1f" % value
    elif isinstance(value, bool):
        return "On" if value else "Off"
    else:
        return str(value)


def step_value(kind, value, delta, extra):
    """
    Step a setting value up or down by delta (+1 or -1).
    Returns the new value, clamped/snapped appropriately.
    kind: "float", "int", "bool", "choice"
    extra: (lo, hi, step) for numeric, tuple of choices for choice, None for bool
    """
    if kind == "bool":
        return not value
    elif kind == "choice":
        choices = extra
        try:
            idx = list(choices).index(value)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(choices)
        return choices[idx]
    elif kind == "float":
        lo, hi, step = extra
        new_val = round(value + delta * step, 10)  # avoid float drift
        return _snap_float(new_val, lo, hi, step)
    elif kind == "int":
        lo, hi, step = extra
        new_val = value + delta * step
        return _snap_int(new_val, lo, hi, step)
    return value


def menu_reduce(state, event, settings, schema=None):
    """
    Pure menu state reducer.
    state: dict with keys cursor, editing, edit_value, action, closed, commit
    event: one of "up", "down", "select", "back", "exit"
    Returns a new state dict.
    """
    if schema is None:
        schema = _SETTINGS_SCHEMA

    state = dict(state)
    state["action"] = None
    state["commit"] = None

    cursor  = state.get("cursor", 0)
    editing = state.get("editing", False)

    if editing:
        key, label = _MENU_ITEMS[cursor]
        kind, extra = setting_spec(key, settings)
        edit_val = state.get("edit_value")

        if event == "up":
            state["edit_value"] = step_value(kind, edit_val, +1, extra)
        elif event == "down":
            state["edit_value"] = step_value(kind, edit_val, -1, extra)
        elif event == "select":
            state["commit"]  = (key, edit_val)
            state["editing"] = False
        elif event == "back":
            state["editing"] = False
        elif event == "exit":
            state["editing"] = False
            state["closed"]  = True
    else:
        if event == "up":
            state["cursor"] = (cursor - 1) % _MENU_ITEM_COUNT
        elif event == "down":
            state["cursor"] = (cursor + 1) % _MENU_ITEM_COUNT
        elif event == "select":
            key, _label = _MENU_ITEMS[cursor]
            if key.startswith("action:"):
                state["action"] = key[len("action:"):]
            else:
                state["editing"]    = True
                state["edit_value"] = settings.get(key)
        elif event in ("back", "exit"):
            state["closed"] = True

    return state


# ----- Menu renderer -----

def draw_menu(state):
    """
    Render the on-device settings menu.
    Layout: rows start at y=32, 20px pitch; hint line at HEIGHT-16, scale=1.
    Values are right-aligned; the active edit value is drawn in yellow.
    """
    clear()
    draw_text("Settings", 8, 8, CYAN, scale=2)

    cursor  = state.get("cursor", 0)
    editing = state.get("editing", False)

    y     = 32
    pitch = 20
    for i, (key, label) in enumerate(_MENU_ITEMS):
        is_cursor = (i == cursor)
        row_color = WHITE if is_cursor else GREY
        prefix = ">" if is_cursor else " "
        label_text = prefix + " " + label

        if key.startswith("action:"):
            draw_text(label_text, 4, y, row_color, scale=2, font="bitmap8")
        else:
            if is_cursor and editing:
                edit_val  = state.get("edit_value")
                val_text  = format_setting(key, edit_val) if edit_val is not None else "?"
                val_color = YELLOW
            else:
                val       = _settings.get(key)
                val_text  = format_setting(key, val) if val is not None else ""
                val_color = row_color

            draw_text(label_text, 4, y, row_color, scale=2, font="bitmap8")
            # Right-align the value; fall back to scale=1 if label+value would overlap
            label_w = display.measure_text(label_text, scale=2)
            val_w   = display.measure_text(val_text, scale=2)
            if label_w + val_w + 12 > WIDTH:
                val_w1 = display.measure_text(val_text, scale=1)
                draw_text(val_text, WIDTH - val_w1 - 4, y + 4, val_color, scale=1, font="bitmap8")
            else:
                draw_text(val_text, WIDTH - val_w - 4, y, val_color, scale=2, font="bitmap8")

        y += pitch

    if editing:
        hint = "A/B adjust  Y save  X cancel"
    else:
        hint = "A/B move  Y select  X back  hold X exit"
    draw_text(hint, 4, HEIGHT - 16, GREY, scale=1, font="bitmap8")
    display.update()


# ----- Device info screen -----

# Plain-language hints for the errno values most likely to reach the display.
# Values are MicroPython's (Linux-style) errno numbers.
_ERRNO_HINTS = {
    "-2":  "DNS lookup failed",
    "104": "Connection reset",
    "110": "Timed out",
    "113": "No route to host",
    "115": "Connect in progress",
}


def describe_net_error(err):
    """Return err with a plain-language hint appended when it carries a known errno."""
    if not err:
        return "none"
    for code, hint in _ERRNO_HINTS.items():
        if "OSError(" + code + ")" in err or "[Errno " + code + "]" in err:
            return err + " = " + hint
    return err


def _device_info_lines():
    """Build the Device info rows as (text, colour) pairs; pure apart from reads."""
    crash = read_crash_log()
    lines = [("SSID: " + str(_settings.get("wifi_ssid", "?")), WHITE)]
    try:
        cfg = wlan.ifconfig() if wlan.isconnected() else None
    except Exception:
        cfg = None
    if cfg:
        lines.append(("IP: " + cfg[0], WHITE))
        lines.append(("GW: " + cfg[2], WHITE))
        lines.append(("DNS: " + cfg[3], WHITE))
    else:
        lines.append(("IP: not connected", WHITE))
    try:
        lines.append(("RSSI: %d dBm" % wlan.status("rssi"), WHITE))
    except Exception:
        lines.append(("RSSI: ?", WHITE))
    lines.append(("NTP: " + ("synced" if _ntp_synced else "not synced"), WHITE))
    try:
        lines.append(("Free: %d KB" % (gc.mem_free() // 1024), WHITE))
    except Exception:
        pass
    lines.append(("Crashes: %d  WDT: %d" % (crash["crashes"], crash["wdt_resets"]), WHITE))
    # One error line: the live network failure if any, else the crash log's.
    net_err = _last_net_error[0]
    if net_err:
        lines.append(("Net: " + describe_net_error(net_err), YELLOW))
    elif crash["last_error"]:
        lines.append(("Last err: " + crash["last_error"][:60].replace("\n", " "), YELLOW))
    return lines


def show_device_info():
    """Show device info screen at a readable scale; any button returns."""
    clear()
    draw_text("Device info", 8, 8, CYAN, scale=2)
    y = 32
    row_h = 16               # bitmap8 at scale 2
    chars_per_row = (WIDTH - 16) // 16
    for text, colour in _device_info_lines():
        # draw_text wraps on spaces at WIDTH; advance by the rows it will use.
        rows = max(1, (len(text) + chars_per_row - 1) // chars_per_row)
        if y + rows * row_h > HEIGHT - 24:
            break
        draw_text(text, 8, y, colour, scale=2, font="bitmap8")
        y += rows * row_h
    draw_text("Any button to return", 8, HEIGHT - 20, GREY, scale=1, font="bitmap8")
    display.update()

    while True:
        feed_watchdog()
        if any_button_pressed():
            break
        time.sleep(0.05)
    wait_buttons_released()


# ----- Menu driver -----

def run_menu(last_state):
    """
    Blocking menu loop. Exits when the menu is closed.
    last_state: last reading state dict (may be None before first reading).
    """
    state = {
        "cursor":     0,
        "editing":    False,
        "edit_value": None,
        "action":     None,
        "closed":     False,
        "commit":     None,
    }

    hold_det    = HoldDetector(_MENU_HOLD_MS)
    y_was_down  = False
    idle_start  = time.ticks_ms()
    toast_until = 0
    toast_msg   = ""

    draw_menu(state)

    # The X press that opened the menu is usually still held here. Sampling it
    # now would register its release as a short press, which at the top level
    # means "back" and closes the menu again immediately.
    wait_buttons_released(_MENU_ENTRY_RELEASE_MS)

    while not state["closed"]:
        feed_watchdog()
        update_led()
        time.sleep(0.05)

        now_ms = time.ticks_ms()

        # Inactivity timeout
        if time.ticks_diff(now_ms, idle_start) > _MENU_IDLE_TIMEOUT_MS:
            break

        # Live backlight preview while editing backlight
        if state["editing"] and _MENU_ITEMS[state["cursor"]][0] == "backlight":
            try:
                display.set_backlight(state["edit_value"])
            except Exception:
                pass

        events = []

        a = button_a.read()
        b = button_b.read()
        y_raw = button_y.raw()
        x_ev  = hold_det.update(button_x.raw(), now_ms)

        if a:
            events.append("up")
            idle_start = now_ms
        if b:
            events.append("down")
            idle_start = now_ms

        # Y: only on the first True until released (latch)
        if y_raw and not y_was_down:
            events.append("select")
            idle_start = now_ms
        y_was_down = y_raw

        if x_ev == "press":
            events.append("back")
            idle_start = now_ms
        elif x_ev == "hold":
            events.append("exit")
            idle_start = now_ms

        needs_redraw = bool(events)

        # Track whether we were previewing backlight before processing events,
        # so we can restore it if the edit is cancelled (avoids referencing the
        # loop variable `event` after the loop, which would be unbound on idle ticks).
        was_editing_backlight = (
            state["editing"]
            and _MENU_ITEMS[state["cursor"]][0] == "backlight"
        )

        for event in events:
            state = menu_reduce(state, event, _settings)

            if state.get("commit"):
                key, val = state["commit"]
                if key == "units":
                    # apply_units_change returns a copy; resets thresholds only when unit differs
                    candidate = apply_units_change(_settings, val)
                    validated = validate_settings(candidate)
                    _settings.update(validated)
                    apply_settings()
                    ok = commit_settings()
                    toast_msg   = "Saved" if ok else "Save failed"
                    toast_until = time.ticks_ms() + 1000
                else:
                    # Single-key change: validate to enforce alert pair constraint
                    candidate = dict(_settings)
                    candidate[key] = val
                    validated = validate_settings(candidate)
                    if validated[key] != val:
                        # The pair was reset; show error
                        toast_msg   = "Low must be < High"
                        toast_until = time.ticks_ms() + 1500
                        state["editing"] = False
                    else:
                        _settings[key] = validated[key]
                        apply_settings()
                        ok = commit_settings()
                        toast_msg   = "Saved" if ok else "Save failed"
                        toast_until = time.ticks_ms() + 1000

            if state.get("action") == "wifi":
                run_wifi_setup()
                # Try NTP if not synced after setup
                global _ntp_synced
                if not _ntp_synced and ntptime is not None:
                    try:
                        ntptime.settime()
                        _ntp_synced = True
                    except Exception:
                        pass
                state["action"] = None
                needs_redraw    = True

            elif state.get("action") == "info":
                show_device_info()
                state["action"] = None
                needs_redraw    = True

            elif state.get("action") == "restart":
                # Confirm screen
                clear()
                draw_text("Restart?", 8, 60, WHITE, scale=2)
                draw_text("Y to restart  X to cancel", 8, HEIGHT - 20, GREY, scale=1, font="bitmap8")
                display.update()
                confirmed = False
                while True:
                    feed_watchdog()
                    if button_y.read():
                        confirmed = True
                        break
                    if button_x.read():
                        break
                    time.sleep(0.05)
                if confirmed:
                    machine.reset()
                state["action"] = None
                needs_redraw    = True

        # Restore backlight when a backlight edit is cancelled (back/exit/idle-exit).
        # Uses the pre-loop flag so this is safe on idle ticks with no events.
        if was_editing_backlight and not state["editing"]:
            try:
                display.set_backlight(_settings.get("backlight", 0.5))
            except Exception:
                pass

        if needs_redraw or time.ticks_diff(now_ms, 0) % 500 < 50:
            draw_menu(state)
            if toast_msg and time.ticks_diff(time.ticks_ms(), toast_until) < 0:
                draw_text(toast_msg, 8, HEIGHT // 2, YELLOW, scale=2)
                display.update()

    wait_buttons_released()


# ----- Main boot sequence -----

def verify_dexcom_after_setup():
    """Check freshly submitted Dexcom credentials once Wi-Fi is up.

    On success the session is kept for the first poll. On failure setup mode
    is re-entered with the reason (which reboots on exit).
    """
    draw_status("Checking Dexcom login", dots=True)
    feed_watchdog()
    session = dexcom_login(
        _settings.get("dexcom_region"),
        _settings.get("dexcom_account_id"),
        _settings.get("dexcom_password"),
    )
    if session:
        _session[0] = session
        draw_status("Dexcom login OK", sub="Starting...")
        time.sleep(1)
        return True
    _session[0] = None
    run_wifi_setup("Wi-Fi joined, but Dexcom login failed - check account id, password and region")
    return False   # unreachable on device


def ensure_wifi():
    """Connect to Wi-Fi, entering setup mode if SSID is empty or retrying on failure.

    When the previous boot submitted the setup form, the join and the Dexcom
    login are verified here and any failure sends the user straight back to
    the setup page with the reason, instead of retrying forever.
    """
    verify_pending = take_setup_verify_pending()

    # If no SSID configured, go straight to setup (which reboots on exit)
    if not _settings.get("wifi_ssid"):
        run_wifi_setup()
        return False   # unreachable on device

    while True:
        draw_status("Wi-Fi", sub="Connecting...", dots=True)
        cfg = connect_wifi(_settings["wifi_ssid"], _settings["wifi_password"], timeout=20)
        if cfg:
            draw_status("Wi-Fi connected", sub=str(cfg[0]))
            time.sleep(0.5)
            if verify_pending:
                return verify_dexcom_after_setup()
            return True
        if verify_pending:
            run_wifi_setup("Could not join '%s' - check the password" % _settings["wifi_ssid"])
            return False   # unreachable on device
        t0 = time.ticks_ms()
        while True:
            feed_watchdog()
            elapsed_ms = time.ticks_diff(time.ticks_ms(), t0)
            if elapsed_ms >= _WIFI_RETRY_DELAY_S * 1000:
                break
            # X opens the settings menu during the countdown
            if button_x.read():
                run_menu(None)
                wait_buttons_released()
                break
            # A/B/Y still retry immediately
            if button_a.read() or button_b.read() or button_y.read():
                time.sleep(0.2)
                break
            remaining_s = _WIFI_RETRY_DELAY_S - elapsed_ms // 1000
            draw_status("Wi-Fi failed", sub="Retrying in %ds" % remaining_s)
            time.sleep(0.1)


def main():
    global _ntp_synced
    boot_ms = time.ticks_ms()

    # Check for watchdog reset before arming WDT
    try:
        if machine.reset_cause() == machine.WDT_RESET:
            _bump_wdt_resets()
    except Exception:
        pass

    # Arm the hardware watchdog
    _wdt[0] = machine.WDT(timeout=_WDT_TIMEOUT_MS)

    # Load settings from flash
    _settings.update(load_settings())
    _settings_saved.update(_settings)
    apply_settings()

    # A soft reboot (Thonny / PyCharm / Ctrl-D) keeps the CYW43 chip and the
    # lwIP stack exactly as the previous run left them, including a missing
    # default route after an access-point session. The station can only be
    # active this early after a soft reboot, so turn it into a hard reset.
    try:
        if wlan.active():
            restart_device("Soft reboot detected")
    except Exception:
        pass

    ensure_wifi()

    # NTP sync (non-fatal)
    for attempt in range(_NTP_MAX_RETRIES):
        feed_watchdog()
        try:
            if ntptime is not None:
                ntptime.settime()
            _ntp_synced = True
            break
        except Exception as e:
            note_net_error("ntp", repr(e))
            if attempt < _NTP_MAX_RETRIES - 1:
                time.sleep(_NTP_RETRY_DELAY_S)
    if not _ntp_synced:
        draw_status("No NTP", sub="Age may be approx")
        time.sleep(1)

    last_ts_ms = None
    last_state = {
        "mg_dl":       None,
        "trend":       None,
        "received_ms": None,
        "ts_ms":       None,
    }
    last_fetch = 0
    poll_ms = 30000
    fetch_failures = 0   # consecutive failed fetches, see recover_after_fetch_failures
    last_draw = 0        # ticks_ms of the last reading repaint, see _REDRAW_INTERVAL_MS

    draw_status("Starting", sub="Fetching latest...", dots=True)

    while True:
        feed_watchdog()
        now = time.ticks_ms()

        # Read all four buttons (no short-circuit so all edge states are consumed)
        a = button_a.read()
        b = button_b.read()
        x = button_x.read()
        y = button_y.read()

        if x:
            run_menu(last_state)
            wait_buttons_released()
            last_fetch = time.ticks_ms() - poll_ms  # force fetch on next tick
            continue

        button_pressed = a or b or y
        need_fetch = time.ticks_diff(now, last_fetch) >= poll_ms or button_pressed

        if need_fetch:
            last_fetch = now
            if button_pressed:
                draw_bottom_right("Checking for updates", WHITE, scale=1)
                display.update()
            if rejoin_wifi():
                data = fetch_latest()
            else:
                data = None
            # Safety net: persistent failures with a link that looks healthy
            # usually mean the default route is gone; cycle the station.
            if data is None:
                fetch_failures = recover_after_fetch_failures(fetch_failures + 1)
            else:
                fetch_failures = 0
            if data is not None:
                ts = data.get("ts_ms")
                is_first = last_state.get("mg_dl") is None
                if ts is not None:
                    is_new = is_first or (ts != last_ts_ms)
                else:
                    # Parse failure fallback: compare raw mg_dl to avoid freeze
                    is_new = is_first or (data.get("mg_dl") != last_state.get("mg_dl"))
                if is_new:
                    last_ts_ms = ts
                    last_state = {
                        "mg_dl":       data.get("mg_dl"),
                        "trend":       data.get("trend"),
                        "received_ms": time.ticks_ms(),
                        "ts_ms":       ts,
                    }
                draw_reading(last_state)
            else:
                draw_reading(last_state)
            last_draw = time.ticks_ms()
        elif (last_state.get("received_ms") is not None
              and time.ticks_diff(now, last_draw) >= _REDRAW_INTERVAL_MS):
            # Periodic refresh of the age text; throttled so the buttons are
            # sampled far more often than the screen is repainted.
            draw_reading(last_state)
            last_draw = now

        update_led()
        time.sleep(_POLL_TICK_S)


if __name__ == "__main__":
    _boot_ms = time.ticks_ms()
    try:
        main()
    except Exception as e:
        feed_watchdog()
        try:
            uptime_s = time.ticks_diff(time.ticks_ms(), _boot_ms) // 1000
            write_crash_log(e, uptime_s)
        except Exception:
            pass
        try:
            draw_status("Error", sub=str(e))
            time.sleep(_FATAL_ERROR_PAUSE_S)
        except Exception:
            pass
        machine.reset()
