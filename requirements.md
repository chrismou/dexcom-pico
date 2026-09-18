This is a MicroPython project running on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8 attached.

The software should work as follows:

When starting up the Pico should attempt to connect to the WiFi using the credentials saved in settings.json (or
secrets.py if settings.json is absent). If no SSID is configured the device goes straight into Wi-Fi setup mode. It
should show feedback on the screen to let the user know it's connected, and gracefully handle if it can't connect
within a reasonable amount of time. On failure it shows "Wi-Fi failed" with a "Retrying in Ns" countdown (5 s) and
then retries automatically, indefinitely, so an unattended device recovers on its own once the network is back. During
the countdown: pressing X opens the settings menu (from which Wi-Fi setup can be started); pressing A, B, or Y retries
immediately.

Once connected, it should authenticate directly with the Dexcom Share API using the credentials stored in
settings.json (or secrets.py as a fallback). No wrapper URL or Bearer token is used. The Pico obtains a session id
via the Dexcom Share login endpoint and uses it to poll for glucose readings.

The session id is maintained in-memory across polls. If a fetch fails (indicating the session may have expired), the
Pico will attempt one silent re-login before applying the staleness rule. A hardware reset clears the session.

Glucose readings are displayed in the unit selected by the user (mmol/L by default, or mg/dL). The raw mg/dL
integer from the API is stored in memory; conversion to the chosen unit happens at draw time. mmol/L is shown with
one decimal place; mg/dL is shown as an integer. The unit is user-selectable via the settings menu.

The Dexcom API should be polled every 30 seconds. Change detection uses the reading's timestamp (`ts_ms`); if the
timestamp is None (e.g. the field was unparseable) the reading is always treated as new to avoid freezing the display.

RGB LED alert indicator:

The Pimoroni Display Pack 2.8's onboard RGB LED (GP26/27/28) provides a glanceable status indicator:
- **Off** (default) when the reading is current and in range
- **Flashing red** (~0.5 s on, 0.5 s off, brightness 80/255) when the reading value is out of range
- **Solid red** when the reading is stale (older than 6 minutes - fixed, not configurable)
- **Precedence rule**: Stale (solid red) takes priority over out-of-range (flashing red), which takes priority over off
- LED alerts can be disabled via the settings menu ("LED alerts" toggle)

The out-of-range thresholds are configurable via the settings menu. Defaults and ranges are unit-dependent:
- mmol/L: low < 4.0 (range 2.0-10.0, step 0.1), high > 14.0 (range 7.0-25.0, step 0.5)
- mg/dL: low < 70 (range 40-180, step 5), high > 180 (range 120-450, step 10)
Switching units resets both thresholds to the new unit's defaults.

Scenarios for the data:

1. A reading is returned (value is set) and is not stale:
   - Display the glucose value in large text on the left half of the screen. Underneath in small text display the
     unit label ("mmol/L" or "mg/dL"), and above in small text display "Last reading N mins ago" (N = minutes
     since the reading's sensor timestamp).
   - On the right, show a graphic arrow depicting the trend. The bottom-right corner is blank during normal operation.
   Possible trend values and arrows:
       "doubleUp": 2 arrows pointing upwards
       "singleUp": 1 arrow pointing upwards
       "fortyFiveUp": 1 arrow pointing up at a 45-degree angle
       "flat": 1 arrow pointing right
       "fortyFiveDown": 1 arrow pointing down at a 135-degree angle
       "singleDown": 1 arrow pointing downwards
       "doubleDown": 2 arrows pointing downwards
     If the trend is anything other than the above, do not show any arrow.
   - The value text is white unless the value is above alert_high or below alert_low (in the display unit), in which
     case it is red.
   - Out-of-range sentinel values from Dexcom (Value=400 or Value=40) are shown as numeric values in red.

2. No reading is returned (API unreachable, login failure, empty list, or network error), or the reading is stale:
   - Continue displaying the existing reading as long as the age is under 6 minutes.
   - If the reading is stale (older than 6 minutes), update the display to show `---` in the main value area,
     hide the trend arrow, and display "Previous: X.X" (or "Previous: NNN" in mg/dL) in the bottom-right corner
     showing the last known glucose value. The "Last reading N mins ago" text remains visible.

Button interactions:

- **X (top-right)**: Opens the settings menu. Does not trigger a fetch.
- **A (top-left), B (bottom-left), Y (bottom-right)**: Trigger an immediate API refresh on the main screen.
- In the settings menu: A = up, B = down, Y = select/confirm, X = back, hold X = exit.

Settings menu (opened by pressing X):

The settings menu allows configuring:
- Backlight brightness (0.1 - 1.0, step 0.1)
- Units (mmol/L or mg/dL; switching resets alert thresholds to the new unit's defaults)
- Alert low threshold (ranges depend on current unit; see LED alert section above)
- Alert high threshold (ranges depend on current unit)
- Region (stored as ous/us/jp; displayed as "Rest of the world" / "United States" / "Japan")
- LED alerts on/off toggle
- Wi-Fi setup (launches AP setup mode)
- Device info (shows SSID, IP, RSSI, NTP status, free memory, crash counts)
- Restart (with confirmation)

The menu layout places values right-aligned in each row. The active edit value is highlighted in yellow.

The menu auto-closes after 60 seconds of inactivity. Settings are saved to /settings.json on each confirmed edit.

Wi-Fi setup mode:

The device can operate as a Wi-Fi access point to allow configuration via a phone browser:
- Raises a WPA2 access point (SSID: dexcom-pico-XXXX where XXXX is derived from the device's unique ID)
- Displays the AP SSID, a per-session random 8-character password, the setup URL (http://192.168.4.1),
  and a Wi-Fi QR code on the right half of the screen (when the Pimoroni qrcode module is available)
- Serves an HTML form listing scanned Wi-Fi networks, plus fields for Dexcom account id, password, and region
- On submit: saves settings, joins the selected network, and verifies Dexcom credentials with one login attempt
- On Dexcom login failure: reopens the setup page with an error message (Wi-Fi credentials retained)
- Hold X to cancel setup mode and return to the previous state
- Setup mode times out after 10 minutes without an HTTP request

Persistence:

Settings are stored in /settings.json on the LittleFS flash using atomic write (tmp file + rename).
Settings are only written on confirmed changes. A corrupt or missing file falls back to defaults (using
secrets.py credentials if present). secrets.py is optional when settings.json exists and contains all keys.

Crash log:

A crash log is maintained at /crash.json recording:
- Total crash count, watchdog reset count
- The last exception traceback (up to 400 characters)
- Uptime at time of last crash
The crash log is viewable from the Settings menu > Device info screen.

Resilience:

- Every Dexcom request uses a bounded socket timeout (5 s per operation) so a stalled connection is treated as a
  failed fetch rather than blocking the display loop.
- Before each poll the Wi-Fi link is checked; if it has dropped, a bounded rejoin (5 s) is attempted without
  replacing the reading on screen.
- A hardware watchdog (8 s) is armed at startup and fed on every loop tick and between network steps.
- An unhandled error writes a crash log entry, shows an "Error" screen for 3 s and then reboots the device.
- A detected watchdog reset at boot increments the wdt_resets counter in the crash log.

secrets.py keys (gitignored, now optional - the on-device setup page can replace this file):
  WIFI_SSID, WIFI_PASSWORD
  DEXCOM_ACCOUNT_ID   - Dexcom Share account UUID
  DEXCOM_PASSWORD     - Dexcom Share password
  DEXCOM_REGION       - "ous" (Rest of the world), "us" (United States), or "jp" (Japan)
