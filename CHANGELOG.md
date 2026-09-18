# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- **Stale-reading staleness detection** - Glucose readings whose sensor timestamp exceeds 6 minutes are displayed as `---`. The "Last reading N mins ago" text remains visible to indicate freshness at a glance.
- **True sensor-age basis** - Reading age is computed from the Dexcom sensor's timestamp (WT field) rather than device receipt time. This correctly handles buffered readings that arrive aged.
- **NTP time synchronization at boot** - The Pico syncs its real-time clock via NTP immediately after Wi-Fi connects (3 retries, 2-second gaps). This enables accurate wall-clock-based sensor-age calculation. If NTP fails, the app degrades gracefully to monotonic time; this is non-fatal.
- **Onboard RGB LED glucose alerts** - The Pimoroni Display Pack 2.8's RGB LED now provides visual feedback: flashes red when the value is out of range, shows solid red when the reading is stale (older than 6 minutes), and remains off otherwise. Precedence: stale > out-of-range > off.
- **Last-known reading on stale** - When a reading becomes stale, the bottom-right corner displays "Previous: X.X" (or "Previous: NNN" in mg/dL) showing the last known glucose value before the device went offline.
- **MIT License** - The project is now released under the MIT License.
- **On-device settings menu** - Press X to open a settings menu with configurable backlight, units, alert thresholds, Dexcom region, LED alerts toggle, Wi-Fi setup, device info, and restart. Menu auto-closes after 60 seconds of inactivity. Settings are saved to `/settings.json` on each confirmed edit.
- **Blood sugar unit selection** - "Units" menu item switches between mmol/L (default) and mg/dL. Switching units automatically resets alert thresholds to the target unit's defaults (mmol/L: low 4.0, high 14.0; mg/dL: low 70, high 180). The raw mg/dL value is stored in memory; conversion to the chosen unit happens at draw time.
- **Per-unit alert thresholds** - mmol/L: low 2.0-10.0 step 0.1, high 7.0-25.0 step 0.5. mg/dL: low 40-180 step 5, high 120-450 step 10. The pair is always validated together (low must be < high).
- **Phone Wi-Fi and Dexcom setup** - The device raises a WPA2 access point and serves a browser form to configure Wi-Fi network, Wi-Fi password, Dexcom account id, password, and region. Scanned networks are listed with RSSI. On submit the device saves and restarts; the next boot joins the selected network and verifies the Dexcom credentials with one login attempt, and reopens the page with the reason if either fails.
- **Wi-Fi QR code** - When the Pimoroni `qrcode` module is present, the setup screen shows a WPA2 Wi-Fi QR code on the right half for quick phone join. Gracefully absent if the module is not installed.
- **Persistent settings in `/settings.json`** - Atomic write (tmp + rename) to LittleFS flash. Settings survive reboots; a corrupt or missing file falls back to defaults. Only written on confirmed changes.
- **`secrets.py` now optional** - If `/settings.json` exists with all keys, `secrets.py` is not needed. The file is still supported as a credential fallback for keys absent from the settings file.
- **Crash log at `/crash.json`** - Records crash count, watchdog reset count, last exception traceback (up to 400 chars). Written on fatal errors; watchdog resets are detected at boot and increment `wdt_resets`. Viewable from Settings > Device info.
- **Device info screen** - Accessible from the settings menu. Shows SSID, IP address, gateway, DNS server, RSSI, NTP sync status, free memory, crash count, watchdog reset count, and the most recent network failure (login, fetch or NTP) with a plain-language hint for common errno values, e.g. "OSError(113) = No route to host". All rows are drawn at the readable scale-2 size.
- **LED alerts toggle** - "LED alerts" on/off toggle in the settings menu disables the RGB LED indicator without changing thresholds.
- **CPython unit test suite** - `tests/` directory with `unittest` tests for settings validation, glucose helpers, HTTP parsing, form decoding, menu state machine, and hold detection. Run with `.venv/bin/python3 -m unittest discover -s tests`.

### Changed

- **Age text now displays "Last reading N mins ago"** (previously "Last updated...") to clarify that the timestamp is sensor-based, not device-receipt-based.
- **Reading change detection now uses the reading's timestamp** (`ts_ms`) rather than an opaque ID field, improving consistency with the true-age logic.
- **Bottom-right corner of the glucose display** no longer shows the trend-direction text label by default. The trend arrow remains unchanged.
- **X button no longer triggers a fetch** on the main screen - it now opens the settings menu. A, B, and Y still force an immediate refresh.
- **All four buttons are read every tick** (no short-circuit) so X edge state is never missed.
- **Main-screen buttons sampled every 50 ms** (was 200 ms, with a full repaint every tick), so quick taps are no longer missed. The reading is now repainted about once a second instead; the staleness gate still uses the true age, only the paint is throttled.
- **Menu waits for the opening press to be released** (up to 2 s) before it starts sampling, so the release of the X press that opened it can no longer register as "back" and close the menu immediately.
- **Stale threshold** is hard-coded at 6 minutes (not configurable). All three staleness-dependent behaviours (`---` display, solid-red LED, "Previous:" corner) use this value.
- **Alert thresholds** are configurable via the settings menu and are unit-dependent (mmol/L: 4.0/14.0; mg/dL: 70/180 defaults).
- **Dexcom region labels** in the settings menu and setup page now show full names: "Rest of the world", "United States", "Japan" (stored values remain `ous`/`us`/`jp`).
- **Configuration error screen** now shows "Menu > Wi-Fi setup to set them" (was "Set Dexcom creds in secrets.py").
- **`rejoin_wifi` and `ensure_wifi`** now read credentials from `_settings` instead of `secrets.py` constants.
- **Backlight** is configured from `_settings["backlight"]` (default 0.5, configurable in menu). Live preview during editing.

### Fixed

- **Silent freeze after a network stall** - Dexcom requests were made without a socket timeout; now all requests use a 5-second per-operation timeout.
- **Hardware watchdog** - An 8-second `machine.WDT` is armed at startup and fed on every loop tick and between each network step. Any remaining hang now reboots the device.
- **Automatic Wi-Fi retry at boot** - A failed startup connection now shows "Wi-Fi failed" with a "Retrying in Ns" countdown and retries automatically every 5 seconds until it connects.
- **Wi-Fi reconnect** - The poll loop now checks the link before each fetch and attempts a bounded rejoin if it has dropped, keeping the current reading and staleness state on screen.
- **Reboot on fatal error** - An unhandled exception now shows the error screen for 3 seconds and then resets the board, rather than exiting to the REPL.
- **Socket leak on malformed responses** - HTTP responses are now always closed, including when JSON decoding fails, so a run of bad responses can no longer exhaust the socket pool.
- **No readings after leaving Wi-Fi setup** - Raising the setup access point makes it the default network route in the CYW43 driver, and tearing it down left the station connected with an address but unable to reach the internet, so every fetch failed until a power cycle. Cycling the station interface was verified on hardware not to restore the route, so every exit from setup mode (cancel, timeout or submit) now restarts the device, and submitted credentials are verified on the next boot. As a safety net the poll loop restarts the device after 3 consecutive failed fetches with "no route to host" while the link is up, and cycles the station for other persistent failures. A soft reboot from Thonny or PyCharm keeps the Wi-Fi chip and network stack exactly as the previous run left them, including the broken route, so it is converted into a hard reset at boot.

### Technical Notes

- Settings and crash log use absolute root paths (`/settings.json`, `/crash.json`) regardless of where `main.py` lives.
- The RP2350 watchdog cannot be disabled once armed. Stopping `main.py` from Thonny will reboot within ~8 seconds.
- `wlan.scan()` is the only un-feedable blocking call (blocking C code, typically 1-3 s). The watchdog is fed immediately before and after; if firmware makes this slower than ~7 s the device may reboot during setup.
- STA and AP interfaces coexist during setup (needed for scanning), but STA is disconnected from the home network before the AP is raised. Bringing the AP up replaces the station as lwIP's default route and `wlan.active(False)`/`active(True)` does not restore it (verified on hardware), so every exit from setup calls `machine.reset()`; a connected link with an address is not proof that routing works. A `/setup_verify` marker file carries the "verify new credentials" request across the reboot.
- Epoch reconciliation (`_PICO_EPOCH_OFFSET_S`) is detected at runtime from `time.gmtime(0)[0]`; do not use a hard-coded offset.
- Glucose values are stored internally as raw mg/dL integers; unit conversion (`_MMOL_FACTOR`) and formatting happen at draw time only.
- A legacy `stale_minutes` key in `settings.json` is ignored and removed on the next save.
