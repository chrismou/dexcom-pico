# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file MicroPython app (`src/main.py`) that runs **on-device** on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8. It authenticates directly against the Dexcom Share API and displays the latest glucose reading (mmol/L) with a trend arrow. There is no wrapper service and no Bearer token — the Pico logs in for a session id and polls with it.

## Commands

There is no test suite and no build step. The app only runs on the Pico hardware (it imports `picographics`, `pimoroni`, `network`, `ntptime`, `urequests`, `ujson` — none available off-device).

- **Syntax check (the only local validation available):**
  ```bash
  .venv/bin/python3 -m py_compile src/main.py
  ```
- **Deploy:** transfer `src/main.py` and `src/secrets.py` to the Pico's filesystem (Thonny / `ampy` / `rshell`) and reboot. The Pimoroni libs must already be flashed.

The `.venv` is CPython 3.10 used only for `py_compile`; do not add CPython-only dependencies expecting them to exist on the Pico.

## Architecture

`src/main.py` is the whole application, structured as a linear state machine in `main()`:
`ensure_wifi()` (retry loop) → NTP sync (non-fatal) → poll loop (30s interval, or immediate on any button press).

Key cross-cutting concepts that span the file:

- **Two-tier age computation** (`draw_reading`). Reading age drives the staleness gate and the "Last reading N mins ago" text. Preferred path: true sensor age from the reading's `ts_ms` (Dexcom `WT` field) via wall-clock, but **only when `_ntp_synced` is True**. Fallback path when NTP failed: monotonic age from `received_ms` (when the reading was received). Any change to age/staleness logic must keep both paths consistent — the same `age_ms` feeds both the displayed minutes and the `> 5 min` blanking.

- **Staleness gate** (`_STALE_LIMIT_MS`, strict `>`). A reading older than 5 minutes blanks the value to `---` and hides the trend arrow, while keeping the age text visible. This is a deliberate at-a-glance BG-safety signal — buffered readings that arrive already-old show as stale immediately. Do not weaken it to "time since last fetch."

- **Epoch reconciliation** (`_PICO_EPOCH_OFFSET_S`). Detected at runtime from `time.gmtime(0)[0]`: some MicroPython ports use a 2000-01-01 epoch, others (Pimoroni build) use Unix epoch. A hard-coded offset double-counts ~946M seconds on Unix-epoch builds — keep the runtime detection.

- **Session management** (`_session` mutable cell + `fetch_latest`). Session id is in-memory only; a hardware reset clears it. On a failed fetch, `fetch_latest` does one silent re-login before returning None. Returning None never crashes the display — the caller falls back to the staleness rule.

- **Change detection** (poll loop). `received_ms` is only reset when a genuinely new reading arrives, compared by `ts_ms`; if `ts_ms` is None (parse failure), it falls back to comparing the glucose value so a repeated identical reading doesn't reset the receive clock and a parse failure never freezes the display.

## Conventions

- Glucose is **always** mmol/L, one decimal (`_MMOL_FACTOR`, `round(..., 1)`). Out-of-range values (`> 14` or `< 4`) render red; there is no separate HIGH/LOW text state — sentinel values like 400/40 mg/dL just render as red numbers.
- Network/parse helpers swallow exceptions and return `None` rather than raising; the UI layer interprets `None` as "unreachable" and applies staleness. Preserve this pattern.
- `secrets.py` is gitignored (keys: `WIFI_SSID`, `WIFI_PASSWORD`, `DEXCOM_ACCOUNT_ID`, `DEXCOM_PASSWORD`, `DEXCOM_REGION`). Never commit it; `secrets.example.py` is the template.
- `requirements.md` is the behavioural spec — treat it as the source of truth for intended on-device behaviour. Keep `CHANGELOG.md` (Keep-a-Changelog style, `Unreleased` section) updated for user-facing changes.