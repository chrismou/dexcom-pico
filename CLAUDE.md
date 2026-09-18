# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file MicroPython app (`src/main.py`) that runs **on-device** on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8. It authenticates directly against the Dexcom Share API and displays the latest glucose reading (mmol/L or mg/dL, user-selectable) with a trend arrow. There is no wrapper service and no Bearer token - the Pico logs in for a session id and polls with it.

## Commands

- **Syntax check (the only local validation available):**
  ```bash
  .venv/bin/python3 -m py_compile src/main.py
  ```
- **Run tests (CPython, hardware stubbed):**
  ```bash
  .venv/bin/python3 -m unittest discover -s tests -v
  ```
- **Deploy:** transfer `src/main.py` and optionally `src/secrets.py` to the Pico's filesystem (Thonny / `ampy` / `rshell` / `mpremote`) and reboot. The Pimoroni libs must already be flashed.

The `.venv` is CPython 3.10 used only for `py_compile` and tests; do not add CPython-only dependencies expecting them to exist on the Pico.

## Architecture

`src/main.py` is the whole application, structured as a linear state machine in `main()`:
crash-log WDT check → arm WDT → load settings → `ensure_wifi()` (retry loop, or setup mode if no SSID) → NTP sync (non-fatal) → poll loop (30 s interval, or immediate on A/B/Y press; X opens menu).

Key cross-cutting concepts that span the file:

- **Two-tier age computation** (`draw_reading`). Reading age drives the staleness gate and the "Last reading N mins ago" text. Preferred path: true sensor age from the reading's `ts_ms` (Dexcom `WT` field) via wall-clock, but **only when `_ntp_synced` is True**. Fallback path when NTP failed: monotonic age from `received_ms` (when the reading was received). Any change to age/staleness logic must keep both paths consistent - the same `age_ms` feeds both the displayed minutes and the staleness gate.

- **Staleness gate** (`_STALE_LIMIT_MS`, strict `>`). A reading older than 6 minutes (hard-coded constant, not configurable) blanks the value to `---` and hides the trend arrow, while keeping the age text visible. This is a deliberate at-a-glance BG-safety signal - buffered readings that arrive already-old show as stale immediately. Do not weaken it to "time since last fetch." All three staleness-dependent behaviours (`---` display, solid-red LED, "Previous:" corner) use `_STALE_LIMIT_MS`. There is no `stale_limit_ms()` function.

- **Epoch reconciliation** (`_PICO_EPOCH_OFFSET_S`). Detected at runtime from `time.gmtime(0)[0]`: some MicroPython ports use a 2000-01-01 epoch, others (Pimoroni build) use Unix epoch. A hard-coded offset double-counts ~946M seconds on Unix-epoch builds - keep the runtime detection.

- **Session management** (`_session` mutable cell + `fetch_latest`). Session id is in-memory only; a hardware reset clears it. On a failed fetch, `fetch_latest` does one silent re-login before returning None. Returning None never crashes the display - the caller falls back to the staleness rule.

- **Change detection** (poll loop). `received_ms` is only reset when a genuinely new reading arrives, compared by `ts_ms`; if `ts_ms` is None (parse failure), it falls back to comparing `mg_dl` so a repeated identical reading doesn't reset the receive clock and a parse failure never freezes the display.

- **Hang protection** (`_wdt` + `feed_watchdog`, `_REQUEST_TIMEOUT_S`, `rejoin_wifi`). An 8 s hardware watchdog is armed at the top of `main()` and cannot be disabled afterwards. Every blocking loop (Wi-Fi connect/rejoin, button waits, the poll loop, menu loop, setup HTTP accept loop, AP start wait) must call `feed_watchdog()`. All `requests.post` calls pass `timeout=_REQUEST_TIMEOUT_S`. Do not add an unbounded sleep, poll, or socket call without feeding the watchdog.

- **Settings** (`_settings`, `_settings_saved`, `_SETTINGS_SCHEMA`). Live settings are in `_settings` (mutated in place). `_settings_saved` holds the last persisted snapshot; `commit_settings()` saves only when `settings_changed()` is True. Credentials and region are read from `_settings`, never from `secrets.py` constants directly. `secrets.py` supplies defaults when a key is absent from `/settings.json`. After a first save, all schema keys are present in the file, so `secrets.py` has no further effect until the file is deleted.

- **Menu** (`run_menu`, `menu_reduce`, `HoldDetector`). `menu_reduce` is a pure function tested under CPython. The driver loop reads hardware, calls the reducer, applies commits, and calls `apply_settings()`. Hold X for 1.5 s exits the menu; short X press goes back. After the menu closes, a fetch is forced on the next poll tick.

- **Wi-Fi setup mode** (`run_wifi_setup`, `serve_setup`). The device raises a WPA2 AP and serves an HTML form. `wlan.scan()` is the only un-feedable blocking C call (1-3 s typically); the watchdog is fed immediately before and after. Do not move the scan inside the HTTP accept loop. STA and AP coexist during setup. Bringing the AP up makes it lwIP's default route in the CYW43 driver, and tearing it down leaves the STA with an address and link but no route to the internet, so **every** exit from setup (cancel, timeout, submit) must call `reset_sta_interface()` before rejoining (`reconnect_after_setup` / `connect_wifi`); do not rely on `rejoin_wifi`, which trusts `isconnected()`. The poll loop also calls `recover_after_fetch_failures`, which cycles the STA after `_FETCH_FAILS_BEFORE_STA_RESET` consecutive failed fetches. Setup mode cannot run concurrently with the poll loop.

- **Boot network reset** (`reset_sta_interface()` at the top of `main()` before `ensure_wifi`). A soft reboot (Thonny, PyCharm, Ctrl-D) does not reinitialise the CYW43 chip or lwIP, so the previous run's state, including a missing default route, survives. Keep the boot-time cycle; only a power cycle is otherwise equivalent.

- **Network error trace** (`_last_net_error`, `note_net_error`, `describe_net_error`). Network helpers still swallow exceptions and return None, but they record a short "stage: repr(e)" first, cleared by the next successful fetch. Device info shows it with an errno hint (113 = no route to host). When adding a network call, record its failure the same way rather than adding prints.

- **Crash log** (`write_crash_log`, `read_crash_log`, `_bump_wdt_resets`). Written on fatal errors and on WDT-reset detection at boot. `write_crash_log` is inside `try/except` in the fatal handler so it cannot mask the reboot. Direct write (no tmp+rename) is acceptable for the crash path; `read_crash_log` tolerates corruption.

## Conventions

- Glucose is stored as a raw mg/dL integer; `convert_mg_dl()` converts to the display unit at draw time, `format_glucose()` formats for display. mmol/L uses one decimal place; mg/dL shows as an integer. Out-of-range values (`> alert_high` or `< alert_low`, in the display unit) render red; there is no separate HIGH/LOW text state. Alert ranges and defaults are per-unit via `_ALERT_SPECS`; `setting_spec()` is the single source of truth for kind and range. Switching units calls `apply_units_change()` which resets both thresholds to the new unit's defaults.
- Network/parse helpers swallow exceptions and return `None` rather than raising; the UI layer interprets `None` as "unreachable" and applies staleness. Preserve this pattern.
- `secrets.py` is gitignored (keys: `WIFI_SSID`, `WIFI_PASSWORD`, `DEXCOM_ACCOUNT_ID`, `DEXCOM_PASSWORD`, `DEXCOM_REGION`). Never commit it; `secrets.example.py` is the template. The file is optional at runtime.
- `requirements.md` is the behavioural spec - treat it as the source of truth for intended on-device behaviour. Keep `CHANGELOG.md` (Keep-a-Changelog style, `Unreleased` section) updated for user-facing changes.
- Frontend text (HTML setup page, on-screen strings): use the plain keyboard hyphen `-` only, never Unicode dashes.

## MicroPython compatibility guards

- No f-strings with `=` specifiers or nested quotes
- No `str.format` on bytes
- No `dict` comprehension with walrus operator
- No `dataclasses` or `typing` imports (type hints in docstrings only)
- `json.dumps` without `indent`/`separators` keyword arguments
- `os.rename`/`os.remove`/`os.stat` only (no `os.path`, `pathlib`, etc.)
- `socket` (not `usocket`) with `socket.getaddrinfo`
- `uio.StringIO` via `try: import uio except ImportError: import io as uio`
- `ujson` via `try: import ujson as json except ImportError: import json`

## Test suite

Pure-logic functions (settings validation, HTTP parsing, form decoding, menu reducer, hold detection) are covered by `tests/`. `tests/hw_stubs.py` installs fake hardware modules into `sys.modules` **before** importing `main`. The stub `secrets` module must be pre-seeded before the import because CPython has a stdlib `secrets` module that would otherwise be imported. The `if __name__ == "__main__"` guard in `main.py` ensures importing it from tests runs nothing.
