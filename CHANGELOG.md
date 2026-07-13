# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- **Stale-reading staleness detection** — Glucose readings whose sensor timestamp exceeds 6 minutes are now displayed as `---` instead of showing a stale numeric value. The "Last reading N mins ago" text remains visible to indicate freshness at a glance.
- **True sensor-age basis** — Reading age is now computed from the Dexcom sensor's timestamp (WT field) rather than the device's receipt time. This correctly handles buffered readings that arrive aged.
- **NTP time synchronization at boot** — The Pico now syncs its real-time clock via NTP immediately after Wi-Fi connects (3 retries, 2-second gaps). This enables accurate wall-clock-based sensor-age calculation. If NTP fails, the app degrades gracefully to monotonic time with a brief "No NTP" status shown; this is non-fatal.
- **Onboard RGB LED glucose alerts** — The Pimoroni Display Pack 2.8's RGB LED (GP26/27/28) now provides visual feedback for glucose status: flashes red (~0.5 s on/off, 80/255 brightness) when the value is out of range (> 14 or < 4 mmol/L), shows solid red when the reading is stale (> 6 minutes old), and remains off otherwise. Precedence: stale > out-of-range > off.
- **Last-known reading on stale** — When a reading becomes stale (> 6 minutes old), the bottom-right corner now displays `"Previous: X.X"` showing the last known glucose value (mmol/L) before the device went offline. This provides context for how close the final reading was to in-range boundaries.
- **MIT License** — The project is now released under the MIT License (`LICENSE`).

### Changed

- Age text now displays "Last reading N mins ago" (previously "Last updated...") to clarify that the timestamp is sensor-based, not device-receipt-based.
- Reading change detection now uses the reading's `ts_ms` (sensor timestamp) rather than an opaque ID field, improving consistency with the true-age logic.
- Bottom-right corner of the glucose display no longer shows the trend-direction text label by default. The trend arrow (right-half of screen) remains unchanged.

### Technical Notes

- `ntptime` (built-in MicroPython module) is used for NTP sync; no new external dependencies.
- The RP2040's time epoch (2000-01-01 UTC) is reconciled with Unix epoch (1970-01-01 UTC) via a constant offset (946 684 800 seconds).
- Staleness threshold is 6 minutes (360 000 ms, strict `>`); a reading at exactly 6 minutes old is not yet stale.
