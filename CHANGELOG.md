# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- **Stale-reading staleness detection** — Glucose readings whose sensor timestamp exceeds 5 minutes are now displayed as `---` instead of showing a stale numeric value. The "Last reading N mins ago" text remains visible to indicate freshness at a glance.
- **True sensor-age basis** — Reading age is now computed from the Dexcom sensor's timestamp (WT field) rather than the device's receipt time. This correctly handles buffered readings that arrive aged.
- **NTP time synchronization at boot** — The Pico now syncs its real-time clock via NTP immediately after Wi-Fi connects (3 retries, 2-second gaps). This enables accurate wall-clock-based sensor-age calculation. If NTP fails, the app degrades gracefully to monotonic time with a brief "No NTP" status shown; this is non-fatal.

### Changed

- Age text now displays "Last reading N mins ago" (previously "Last updated...") to clarify that the timestamp is sensor-based, not device-receipt-based.
- Reading change detection now uses the reading's `ts_ms` (sensor timestamp) rather than an opaque ID field, improving consistency with the true-age logic.

### Technical Notes

- `ntptime` (built-in MicroPython module) is used for NTP sync; no new external dependencies.
- The RP2040's time epoch (2000-01-01 UTC) is reconciled with Unix epoch (1970-01-01 UTC) via a constant offset (946 684 800 seconds).
- Staleness threshold remains 5 minutes (300 000 ms, strict `>`); a reading at exactly 5 minutes old is not yet stale.
