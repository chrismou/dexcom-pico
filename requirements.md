This is a MicroPython project running on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8 attached.

The software should work as follows:

When starting up the Pico should attempt to connect to the WiFi using the credentials saved in secrets.py. It should show
feedback on the screen to let the user know it's connected, and gracefully handle if it can't connect within a reasonable
amount of time. On failure it shows "Wi-Fi failed" with a "Retrying in Ns" countdown (5 s) and then retries automatically,
indefinitely, so an unattended device recovers on its own once the network is back. Pressing any button skips the
countdown and retries immediately.

Once connected, it should authenticate directly with the Dexcom Share API using the credentials stored in secrets.py
(`DEXCOM_ACCOUNT_ID`, `DEXCOM_PASSWORD`, `DEXCOM_REGION`). No wrapper URL or Bearer token is used. The Pico obtains a
session id via the Dexcom Share login endpoint and uses it to poll for glucose readings.

The session id is maintained in-memory across polls. If a fetch fails (indicating the session may have expired), the Pico
will attempt one silent re-login before applying the staleness rule. A hardware reset clears the session; no persistent
storage is used.

Glucose readings are always displayed in mmol/L (one decimal place). The `unit` field is always `"mmol/L"`.

The Dexcom API should be polled every 30 seconds. Change detection uses the reading's timestamp (`ts_ms`); if the
timestamp is None (e.g. the field was unparseable) the reading is always treated as new to avoid freezing the display.

RGB LED alert indicator:

The Pimoroni Display Pack 2.8's onboard RGB LED (GP26/27/28) provides a glanceable status indicator:
- **Off** (default) when the reading is current and in range (4–14 mmol/L)
- **Flashing red** (~0.5 s on, 0.5 s off, brightness 80/255) when the reading value is out of range (> 14 or < 4 mmol/L)
- **Solid red** when the reading is stale (> 6 minutes old)
- **Precedence rule**: Stale (solid red) takes priority over out-of-range (flashing red), which takes priority over off

Scenarios for the data:

1. A reading is returned (value is set) and is not stale:
   - Display the mmol/L value in large text on the left half of the screen. Underneath in small text display "mmol/L",
     and above in small text display "Last reading N mins ago" (N = minutes since the reading's sensor timestamp).
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
   - The value text is white unless the value is above 14 or below 4, in which case it is red.
   - Out-of-range sentinel values from Dexcom (Value=400 → 22.2 mmol/L, Value=40 → 2.2 mmol/L) are shown as numeric
     values in red — there is no separate HIGH/LOW text state.

2. No reading is returned (API unreachable, login failure, empty list, or network error), or the reading is stale:
   - Continue displaying the existing reading as long as the time since the last successful fetch is less than 6 minutes.
   - If more than 6 minutes have passed since the last successful reading (stale state), update the display to show `---` in the main value area, hide the trend arrow, and display `"Previous: X.X"` in the bottom-right corner showing the last known glucose value (one decimal, mmol/L). The "Last reading N mins ago" text remains visible to indicate staleness.

If any of the 4 buttons are pressed, the API should be called immediately and the display updated as described above.

Resilience:

- Every Dexcom request uses a bounded socket timeout (5 s per operation) so a stalled connection is treated as a failed
  fetch rather than blocking the display loop.
- Before each poll the Wi-Fi link is checked; if it has dropped, a bounded rejoin (5 s) is attempted without replacing
  the reading on screen. If the rejoin fails the poll is skipped and the staleness rule applies as normal.
- A hardware watchdog (8 s) is armed at startup and fed on every loop tick and between network steps. If the device
  hangs for any reason it reboots rather than freezing with the last frame on screen.
- An unhandled error shows an "Error" screen for 3 s and then reboots the device. The device must never sit on a
  frozen frame indefinitely.

secrets.py keys (gitignored, never committed):
  WIFI_SSID, WIFI_PASSWORD
  DEXCOM_ACCOUNT_ID   — Dexcom Share account UUID
  DEXCOM_PASSWORD     — Dexcom Share password
  DEXCOM_REGION       — "ous" (outside US), "us", or "jp"
