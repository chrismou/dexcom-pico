import time
import network
import urequests as requests
import ujson as json
from picographics import PicoGraphics, DISPLAY_PICO_DISPLAY_2, PEN_P8
from pimoroni import Button
from secrets import WIFI_SSID, WIFI_PASSWORD, API_URL, API_TOKEN

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

# ----- Buttons -----
# Pico Display/Pico Display 2.8 buttons are typically on GPIOs A=12, B=13, X=14, Y=15
button_a = Button(12)
button_b = Button(13)
button_x = Button(14)
button_y = Button(15)

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


def minutes_since_ms(t_ms):
    if t_ms is None:
        return None
    d = time.ticks_diff(time.ticks_ms(), t_ms)
    if d < 0:
        d = 0
    return d // 60000


def draw_reading(state):
    # state: dict with keys: value (or None), unit, trend, status, received_ms
    clear()
    # left area
    left_w = WIDTH // 2
    val = state.get("value")
    unit = state.get("unit") or ""
    trend = state.get("trend")
    status = state.get("status")

    # Determine value text and color
    show_text = "---"
    color = WHITE

    if val is not None:
        show_text = str(val)
        try:
            numeric = float(val)
        except Exception:
            numeric = None
        if numeric is not None:
            if numeric > 14 or numeric < 4:
                color = RED
    else:
        if status is not None:
            s = str(status).lower()
            if s == "high" or s == "low":
                show_text = status
            else:
                show_text = "---"
        else:
            show_text = "---"

    # Minutes since update (based on receive time on device)
    mins = minutes_since_ms(state.get("received_ms"))
    mins_txt = ("%d mins" % mins + ' ago') if mins is not None else ""

    # Draw texts
    draw_text(mins_txt, 8, 8, WHITE, scale=2, font="bitmap8")  # Small font for time
    draw_text(show_text, 8, HEIGHT // 2 - 0, color, scale=3, font="sans")  # Larger bold font for value
    draw_text(unit, 8, HEIGHT - 24, WHITE, scale=2, font="bitmap8")  # Small font for unit

    # right area trend
    draw_trend(trend, left_w, 0, WIDTH - left_w, HEIGHT, WHITE)

    display.update()


# ----- Networking -----
def fetch_latest():
    try:
        headers = {"Authorization": "Bearer " + API_TOKEN}
        resp = requests.get(API_URL, headers=headers)
        if resp is None:
            return None
        if resp.status_code != 200:
            try:
                resp.close()
            except Exception:
                pass
            return None
        data = resp.json()
        try:
            resp.close()
        except Exception:
            pass
        return data
    except Exception:
        return None


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
    ensure_wifi()
    last_id = None
    last_state = {
        "value": None,
        "unit": None,
        "trend": None,
        "status": None,
        "received_ms": None,
    }
    last_fetch = 0
    poll_ms = 30000
    stale_limit_ms = 5 * 60 * 1000

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
                data_id = data.get("id")
                if data_id != last_id:
                    last_id = data_id
                    last_state = {
                        "value": data.get("value"),
                        "unit": data.get("unit"),
                        "trend": data.get("trend"),
                        "status": data.get("status"),
                        "received_ms": time.ticks_ms(),
                    }
                    draw_reading(last_state)
                else:
                    # same value, just update minutes display
                    draw_reading(last_state)
            else:
                # API unreachable; decide based on staleness
                if last_state.get("received_ms") is None:
                    # nothing to show
                    draw_reading(last_state)
                else:
                    age = time.ticks_diff(time.ticks_ms(), last_state.get("received_ms"))
                    if age > stale_limit_ms:
                        # set to --- but keep unit/trend/status
                        stale_state = dict(last_state)
                        stale_state["value"] = None
                        stale_state["status"] = None
                        draw_reading(stale_state)
                    else:
                        draw_reading(last_state)
        else:
            # periodic refresh of minutes display
            if last_state.get("received_ms") is not None:
                draw_reading(last_state)

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
