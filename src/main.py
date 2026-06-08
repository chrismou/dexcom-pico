import time
import network
import ntptime
import urequests as requests
import ujson as json
from picographics import PicoGraphics, DISPLAY_PICO_DISPLAY_2, PEN_P8
from pimoroni import Button, RGBLED
from secrets import WIFI_SSID, WIFI_PASSWORD, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD, DEXCOM_REGION

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

# Mutable cell holding the current Dexcom session id
_session = [None]   # _session[0] holds the current session id string or None

# ----- LED alert state -----
# LED state cache — written by draw_reading(), consumed by update_led() each tick.
# Possible values for _led_mode: "off", "solid_red", "flash_red"
_led_mode = ["off"]   # mutable cell so draw_reading() can update it without global
_LED_FLASH_PERIOD_MS = 1000        # total flash cycle: 500 ms on, 500 ms off
_LED_RED_BRIGHTNESS  = 80          # 0-255; note: these are raw RGB values, not pen IDs

# ----- Staleness / epoch constants -----
_STALE_LIMIT_MS = 6 * 60 * 1000   # 360 000 ms — reading older than this shows "---"

# Dexcom ts_ms values use the Unix epoch (1970-01-01 UTC). MicroPython ports
# differ: some use a 2000-01-01 epoch for time.time(), others (e.g. the Pimoroni
# Pico build) already use the Unix epoch. Detect the base at runtime via the year
# of gmtime(0) so the age maths is correct on any firmware — a hard-coded offset
# double-counts on Unix-epoch builds (showing ~946 684 800 s of bogus age).
_PICO_EPOCH_OFFSET_S = 946_684_800 if time.gmtime(0)[0] == 2000 else 0

# Set to True after a successful ntptime.settime() call at boot.
# When False, draw_reading() falls back to monotonic ticks from received_ms.
_ntp_synced = False

# NTP retry settings used in main() boot sequence.
_NTP_MAX_RETRIES = 3
_NTP_RETRY_DELAY_S = 2

# ----- Buttons -----
# Pico Display/Pico Display 2.8 buttons are typically on GPIOs A=12, B=13, X=14, Y=15
button_a = Button(12)
button_b = Button(13)
button_x = Button(14)
button_y = Button(15)

# ----- RGB LED -----
# Display Pack 2.8" wires the onboard RGB LED to GP26/27/28 (the smaller 1.14"/2.0"
# packs use GP6/7/8 — wrong pins leave the LED uninitialised and floating white).
led = RGBLED(26, 27, 28)
led.set_rgb(0, 0, 0)   # explicitly off at boot; overrides the hardware default white

# ----- Wi-Fi -----
def connect_wifi(ssid, password, timeout=20):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        try:
            wlan.connect(ssid, password)
        except Exception:
            pass
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout * 1000:
                return None
            draw_status("Connecting to Wi-Fi", sub="Press any button to cancel", dots=True)
            if any_button_pressed():
                return None
            time.sleep(0.1)
    return wlan.ifconfig()

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
            pass  # fallback if font not available
    display.text(text, x, y, wrap, scale)


def draw_status(msg, sub=None, dots=False):
    clear()
    draw_text("Dexcom Monitor", 8, 8, CYAN, scale=2)
    draw_text(msg + ("." * ((time.ticks_ms() // 300) % 4) if dots else ""), 8, 32, GREY, scale=2)
    if sub:
        draw_text(sub, 8, 56, GREY, scale=2)
    display.update()

# Small overlay helper for bottom-right messages

def draw_bottom_right(msg, color=GREY, scale=1):
    # Approximate right alignment assuming 8px per character for bitmap8 font
    char_w = 8
    text_w = len(msg) * char_w * scale
    x = max(0, WIDTH - text_w - 4)
    y = HEIGHT - (char_w * scale) - 4
    draw_text(msg, x, y, color=color, scale=scale, wrap=WIDTH, font="bitmap8")

# ---- Helpers ----
def any_button_pressed():
    return button_a.read() or button_b.read() or button_x.read() or button_y.read()


def draw_trend(trend, x0, y0, w, h, color=WHITE):
    # Draw arrows according to trend on the right half
    display.set_pen(color)
    cx = x0 + w // 2
    cy = y0 + h // 2
    size = min(w, h) // 6

    def arrow(dx, dy, offset_x=0.0):
        # offset_x shifts the arrow horizontally
        center_x = cx + offset_x
        x1 = center_x - dx * size
        y1 = cy - dy * size
        x2 = center_x + dx * size
        y2 = cy + dy * size

        # Draw thicker shaft by drawing multiple parallel lines
        thickness = 5  # Adjust this value for thicker/thinner shaft
        pdx, pdy = -dy, dx  # perpendicular vector for thickness
        for offset in range(-thickness // 2, thickness // 2 + 1):
            display.line(
                int(x1 + pdx * offset),
                int(y1 + pdy * offset),
                int(x2 + pdx * offset),
                int(y2 + pdy * offset)
            )

        # arrow head (bigger)
        hx = x2
        hy = y2
        ah = size // 1.8  # Increased from size // 3 to make head bigger
        aw = ah // 1.8  # Width of arrow head

        # Draw thicker arrow head lines
        for offset in range(-thickness // 2, thickness // 2 + 1):
            display.line(
                int(hx + pdx * offset),
                int(hy + pdy * offset),
                int(hx - dx * ah + pdx * aw + pdx * offset),
                int(hy - dy * ah + pdy * aw + pdy * offset)
            )
            display.line(
                int(hx + pdx * offset),
                int(hy + pdy * offset),
                int(hx - dx * ah - pdx * aw + pdx * offset),
                int(hy - dy * ah - pdy * aw + pdy * offset)
            )

    if trend is not None:
        draw_bottom_right(trend, WHITE, scale=1)

    if trend == "doubleUp":
        spacing = size * 1.0  # Adjust spacing between arrows as needed
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
        spacing = size * 1.0  # Adjust spacing between arrows as needed
        arrow(0, 1, -spacing // 2)
        arrow(0, 1, spacing // 2)
    else:
        # do nothing for unknown trend
        return


def age_unix_ms(ts_unix_ms):
    """Return age in ms for a Unix-epoch-ms timestamp, or None.
    Relies on NTP having set the RTC; call only when _ntp_synced is True.
    Note: time.time() has ~1s resolution, so age has up to ±1s jitter —
    acceptable for a 5-minute CGM cadence.
    """
    if ts_unix_ms is None:
        return None
    now_unix_ms = (time.time() + _PICO_EPOCH_OFFSET_S) * 1000
    diff_ms = now_unix_ms - ts_unix_ms
    return max(0, diff_ms)


def draw_reading(state):
    # state: dict with keys: value (or None), unit, trend, received_ms, ts_ms
    clear()
    # left area
    left_w = WIDTH // 2
    val = state.get("value")
    unit = state.get("unit") or ""
    trend = state.get("trend")

    # --- Age computation (dual-path) ---
    # Prefer true sensor age via wall-clock when NTP has synced and ts_ms is available.
    # Fall back to monotonic age from receipt time otherwise.
    ts_ms = state.get("ts_ms")
    recv_ms = state.get("received_ms")

    if _ntp_synced and ts_ms is not None:
        age_ms = age_unix_ms(ts_ms)       # wall-clock based true age
        # age_unix_ms returns None only when ts_unix_ms is None, which is
        # guarded by the outer condition — but guard symmetrically to be safe.
        mins = (age_ms // 60000) if age_ms is not None else None
    else:
        # Fallback: monotonic age from receipt time
        if recv_ms is not None:
            raw_diff = time.ticks_diff(time.ticks_ms(), recv_ms)
            age_ms = max(0, raw_diff)
            mins = age_ms // 60000
        else:
            age_ms = None
            mins = None

    # --- Staleness gate ---
    # If the reading is over 6 minutes old, blank the value.
    # Uses the same age_ms computed above so age text and staleness gate are consistent.
    if age_ms is not None and age_ms > _STALE_LIMIT_MS:
        val = None   # blank the value; unit and trend are preserved below

    # Determine value text and color
    show_text = "---"
    color = WHITE

    _out_of_range = False
    if val is not None:
        show_text = str(val)
        try:
            numeric = float(val)
            if numeric > 14 or numeric < 4:
                color = RED
                _out_of_range = True
        except Exception:
            pass

    # Age text
    mins_txt = ("Last reading %d mins ago" % mins) if mins is not None else ""
    # Draw texts
    draw_text(mins_txt, 8, 8, WHITE, scale=2, font="bitmap8")  # Small font for time
    draw_text(show_text, 8, HEIGHT // 2 - 0, color, scale=3, font="sans")  # Larger bold font for value
    draw_text(unit, 8, HEIGHT - 24, WHITE, scale=2, font="bitmap8")  # Small font for unit

    # right area trend — hide the arrow (and its label) when the value is
    # blanked to "---" (stale or no reading), since the trend is meaningless then.
    if val is not None:
        draw_trend(trend, left_w, 0, WIDTH - left_w, HEIGHT, WHITE)

    # --- LED state ---
    # Precedence: stale (solid red) > out-of-range (flash red) > off.
    # age_ms and _out_of_range are fully resolved by this point.
    if age_ms is not None and age_ms > _STALE_LIMIT_MS:
        _led_mode[0] = "solid_red"
    elif _out_of_range:
        _led_mode[0] = "flash_red"
    else:
        _led_mode[0] = "off"

    display.update()


def update_led():
    """Drive the RGB LED according to the current _led_mode.
    Called every main-loop tick (~0.2 s) so flash cadence is smooth
    regardless of how infrequently draw_reading() is called.
    """
    mode = _led_mode[0]
    if mode == "solid_red":
        led.set_rgb(_LED_RED_BRIGHTNESS, 0, 0)
    elif mode == "flash_red":
        phase = time.ticks_ms() % _LED_FLASH_PERIOD_MS
        if phase < _LED_FLASH_PERIOD_MS // 2:
            led.set_rgb(_LED_RED_BRIGHTNESS, 0, 0)
        else:
            led.set_rgb(0, 0, 0)
    else:  # "off" or any unrecognised value
        led.set_rgb(0, 0, 0)


# ----- Dexcom client helpers -----
def _dexcom_base(region):
    return _DEXCOM_BASE_URLS.get(region, _DEXCOM_BASE_URLS["ous"])


def _dexcom_app_id(region):
    return _DEXCOM_APP_ID_JP if region == "jp" else _DEXCOM_APP_ID_DEFAULT


def dexcom_login(region, account_id, password):
    url = _dexcom_base(region) + "General/LoginPublisherAccountById"
    body = json.dumps({
        "accountId": account_id,
        "password": password,
        "applicationId": _dexcom_app_id(region),
    })
    try:
        resp = requests.post(url, data=body, headers=_DEXCOM_HEADERS)
        if resp is None or resp.status_code != 200:
            try:
                resp.close()
            except Exception:
                pass
            return None
        session_id = resp.json()   # ujson decodes the bare string literal
        try:
            resp.close()
        except Exception:
            pass
        if not session_id or session_id == _DEXCOM_NULL_SESSION:
            return None
        return session_id
    except Exception:
        return None


def dexcom_fetch_latest(region, session_id):
    base = _dexcom_base(region)
    url = (base
           + "Publisher/ReadPublisherLatestGlucoseValues"
           + "?sessionId=" + session_id
           + "&minutes=1440"
           + "&maxCount=1")
    try:
        resp = requests.post(url, data="{}", headers=_DEXCOM_HEADERS)
        if resp is None or resp.status_code != 200:
            try:
                resp.close()
            except Exception:
                pass
            return None
        readings = resp.json()   # list of dicts
        try:
            resp.close()
        except Exception:
            pass
        return readings
    except Exception:
        return None


def _parse_dexcom_timestamp(wt_str):
    # wt_str e.g. "Date(1587431782000-0400)" or "Date(1587431782000+0000)"
    try:
        start = wt_str.index("(") + 1
        end   = wt_str.index(")")
        inner = wt_str[start:end]   # "1587431782000-0400" or "1587431782000+0000"
        # Split on sign after position 0 to handle negative-epoch edge cases
        for sep in ("+", "-"):
            idx = inner.find(sep, 1)   # skip potential leading '-' of epoch
            if idx != -1:
                return int(inner[:idx])
        return int(inner)             # no offset present
    except Exception:
        return None


def parse_dexcom_reading(raw_list):
    if not raw_list:            # empty list -> no current reading
        return None
    try:
        r = raw_list[0]         # most-recent reading
        mg_dl = int(r["Value"])
        mmol  = round(mg_dl * _MMOL_FACTOR, 1)
        trend_raw = r.get("Trend", "")
        trend = _TREND_MAP.get(trend_raw, None)   # None -> no arrow
        # Timestamp: parse WT field  "Date(1587431782000-0400)"
        # Extract the leading integer (ms since epoch); ignore tz offset.
        wt_str = r.get("WT") or r.get("DT") or ""
        ts_ms = _parse_dexcom_timestamp(wt_str)
        return {
            "value":       mmol,
            "unit":        "mmol/L",
            "trend":       trend,
            "ts_ms":       ts_ms,   # epoch ms from reading (used for change detection)
        }
    except Exception:
        return None


# ----- Networking -----
def fetch_latest():
    """
    Returns a parsed reading dict {value, unit, trend, ts_ms} on success,
    or None on any failure (caller treats None as "API unreachable").
    """
    # --- Step 0: misconfiguration guard ---
    if not DEXCOM_ACCOUNT_ID or not DEXCOM_PASSWORD:
        draw_status("Config error", sub="Set Dexcom creds in secrets.py")
        return None

    # --- Step 1: ensure we have a session ---
    if _session[0] is None:
        _session[0] = dexcom_login(DEXCOM_REGION, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD)
        if _session[0] is None:
            return None   # login failed; caller uses staleness logic

    # --- Step 2: fetch ---
    raw = dexcom_fetch_latest(DEXCOM_REGION, _session[0])

    # --- Step 3: session expiry recovery ---
    if raw is None:
        # Attempt one re-login; clear session so next call re-logs if this fails too
        _session[0] = None
        _session[0] = dexcom_login(DEXCOM_REGION, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD)
        if _session[0] is None:
            return None   # re-auth failed; caller applies 6-min stale rule
        raw = dexcom_fetch_latest(DEXCOM_REGION, _session[0])
        if raw is None:
            return None

    # --- Step 4: parse ---
    return parse_dexcom_reading(raw)


# ----- Main loop -----

def ensure_wifi():
    while True:
        draw_status("Wi-Fi", sub="Connecting...", dots=True)
        cfg = connect_wifi(WIFI_SSID, WIFI_PASSWORD, timeout=20)
        if cfg:
            draw_status("Wi-Fi connected", sub=str(cfg[0]))
            time.sleep(0.5)
            return True
        # failed
        draw_status("Wi-Fi failed", sub="Press any button to retry")
        # wait for button
        while True:
            if any_button_pressed():
                # drain
                time.sleep(0.2)
                break
            time.sleep(0.05)


def main():
    global _ntp_synced

    ensure_wifi()

    # ----- NTP sync (non-fatal) -----
    # Attempt to set the RTC from NTP so that true sensor age can be computed.
    # Three attempts with 2-second gaps; boot continues in fallback mode on failure.
    for attempt in range(_NTP_MAX_RETRIES):
        try:
            ntptime.settime()
            _ntp_synced = True
            break
        except Exception:
            if attempt < _NTP_MAX_RETRIES - 1:
                time.sleep(_NTP_RETRY_DELAY_S)
    if not _ntp_synced:
        draw_status("No NTP", sub="Age may be approx")
        time.sleep(1)

    last_ts_ms = None   # epoch-ms of last displayed reading; used for change detection
    last_state = {
        "value":       None,
        "unit":        "mmol/L",
        "trend":       None,
        "received_ms": None,   # kept as fallback when NTP not synced
        "ts_ms":       None,   # Unix epoch ms from sensor WT field; None until first reading
    }
    last_fetch = 0
    poll_ms = 30000

    draw_status("Starting", sub="Fetching latest...", dots=True)

    while True:
        now = time.ticks_ms()
        button_pressed = any_button_pressed()
        need_fetch = time.ticks_diff(now, last_fetch) >= poll_ms or button_pressed
        if need_fetch:
            last_fetch = now
            if button_pressed:
                # Show transient overlay while fetching due to manual refresh
                draw_bottom_right("Checking for updates", WHITE, scale=1)
                display.update()
            data = fetch_latest()
            if data is not None:
                ts = data.get("ts_ms")
                # Determine whether this is a genuinely new reading so we only
                # reset received_ms when the data has actually changed.
                #
                # (a) First reading ever: last_state["value"] is None.
                # (b) ts_ms available on both sides: compare timestamps.
                # (c) ts_ms is None (parse failure): fall back to comparing the
                #     glucose value so a repeated identical reading does NOT
                #     reset the receive clock, while a changed value does.
                is_first = last_state.get("value") is None
                if ts is not None:
                    is_new = is_first or (ts != last_ts_ms)
                else:
                    # ts unavailable; treat as new only when value has changed
                    # (or it is the very first reading).
                    is_new = is_first or (data.get("value") != last_state.get("value"))
                if is_new:
                    last_ts_ms = ts
                    last_state = {
                        "value":       data.get("value"),
                        "unit":        "mmol/L",
                        "trend":       data.get("trend"),
                        "received_ms": time.ticks_ms(),
                        "ts_ms":       ts,
                    }
                draw_reading(last_state)
            else:
                # API unreachable; draw current state (draw_reading applies staleness)
                draw_reading(last_state)
        else:
            # periodic refresh of minutes display
            if last_state.get("received_ms") is not None:
                draw_reading(last_state)

        update_led()
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # On fatal error, display message
        try:
            draw_status("Error", sub=str(e))
            time.sleep(3)
        except Exception:
            pass
