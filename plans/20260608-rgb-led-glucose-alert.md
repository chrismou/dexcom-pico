# Technical Design Doc — RGB LED Glucose Alert Indicator

**Date:** 2026-06-08
**Branch:** feature/pydexcom
**Affected file:** `src/main.py` (sole application file)

---

## Overview

The Pimoroni Display Pack 2.8 has an onboard RGB LED wired to GPIO 6 (R), 7 (G), 8 (B). It is never initialised, leaving it permanently white. This feature initialises it explicitly and makes it a glanceable glucose-alert indicator with three states:

| Priority | Condition | LED state |
|---|---|---|
| 1 (highest) | Reading age > 5 min (`_STALE_LIMIT_MS`) | Solid RED |
| 2 | Value out of range (`> 14 or < 4 mmol/L`) | Flashing RED |
| 3 (default) | In-range and fresh, or boot/status screen | OFF |

Stale takes precedence over out-of-range because once the value is blanked to `---` the numeric range is no longer knowable.

---

## Affected Files

- `src/main.py` — all changes confined here
- `requirements.md` — add LED behaviour section (documenter step)
- `CHANGELOG.md` — add entry under `## Unreleased / ### Added` (documenter step)

---

## New Constants (add immediately after the existing `_STALE_LIMIT_MS` block, around line 63)

```
_LED_FLASH_PERIOD_MS = 1000   # total flash cycle: 500 ms on, 500 ms off
_LED_RED_BRIGHTNESS  = 80     # 0-255; full 255 is uncomfortably bright on the bare LED
```

`_LED_FLASH_PERIOD_MS` drives the on/off split: phase = `ticks_ms() % _LED_FLASH_PERIOD_MS`; LED is on when `phase < _LED_FLASH_PERIOD_MS // 2`. 80/255 is a starting point — easily adjustable by the user without understanding the flash logic.

Rationale for 80: the bare LED on the Display Pack is eye-level in a dark room. 255 is distracting. 80 is clearly visible across a room without being intrusive. Expose it as a named constant so it can be tuned.

---

## RGBLED Initialisation (add after the `Button` block, lines 82–85)

Import and construct the LED immediately after the buttons, in the same "hardware init" zone:

```
from pimoroni import Button, RGBLED

led = RGBLED(6, 7, 8)
led.set_rgb(0, 0, 0)   # explicitly off at boot; overrides the hardware default white
```

The `set_rgb(0, 0, 0)` call at construction time fulfils the "off by default" requirement — the LED is off before `main()` is entered, so status/boot screens do not show a spurious colour.

Note: `from pimoroni import Button` is already present at line 7. The coder must extend that import rather than add a second `from pimoroni import` line, to keep MicroPython happy and the file tidy.

---

## LED State Cache (module level, near the `_session` cell)

Add two module-level variables to carry LED intent across loop ticks:

```
# LED state cache — written by draw_reading(), consumed by update_led() each tick.
# Possible values for _led_mode: "off", "solid_red", "flash_red"
_led_mode = ["off"]   # mutable cell so draw_reading() can update it without global
```

Using a mutable one-element list (matching the `_session` pattern already in the file) avoids a `global` declaration inside `draw_reading`.

---

## LED Update Helper (add as a module-level function, near the other small helpers)

```python
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
```

This is deliberately separate from `draw_reading` so the flash cadence is not tied to the sparse display-redraw cadence. The display is only redrawn on fetch/refresh; the LED must pulse smoothly every ~0.2 s.

---

## Changes to `draw_reading(state)` (lines 233–294)

At the end of `draw_reading`, after all display work is done and before `display.update()`, set `_led_mode[0]` according to the computed conditions. The conditions are already fully resolved at that point in the function:

1. `age_ms is not None and age_ms > _STALE_LIMIT_MS` — stale (line 266 gate has already fired, `val` is None here).
2. `val is not None` and the `numeric > 14 or numeric < 4` branch fired (line 277).

Insert the following block immediately before `display.update()` (after line 292, before line 294):

```python
    # --- LED state ---
    # Precedence: stale (solid red) > out-of-range (flash red) > off.
    # age_ms and val are already resolved by this point in the function.
    if age_ms is not None and age_ms > _STALE_LIMIT_MS:
        _led_mode[0] = "solid_red"
    elif color is RED:
        # color was set to RED only in the out-of-range branch above;
        # this is the same condition without re-evaluating.
        _led_mode[0] = "flash_red"
    else:
        _led_mode[0] = "off"
```

Note: `color` is the local variable already set in `draw_reading` (line 271 `color = WHITE`, reassigned to `RED` at line 278 when out-of-range). Checking `color is RED` re-uses the already-computed decision with no extra arithmetic. This works because `RED` is a module-level pen constant and identity comparison (`is`) is valid for small integers / cached objects on MicroPython. However, to be more explicit and robust, an alternative is a local boolean flag:

```python
    _out_of_range = False
    # (set _out_of_range = True inside the numeric > 14 or numeric < 4 branch)
```

Either approach is acceptable; the flag approach is slightly more readable and avoids relying on pen-object identity. The coder should choose the flag approach for clarity.

### Precise insertion point within `draw_reading`

The staleness gate sets `val = None` at line 267. The out-of-range test and `color = RED` assignment happen at lines 276–278. The LED block must come after line 278 (so `color` is fully resolved) and before `display.update()` at line 294. Insert after line 292 (`if val is not None: draw_trend(...)`).

---

## Changes to the Main Poll Loop (`main()`, lines 486–532)

Add a single call to `update_led()` unconditionally at the bottom of every loop iteration, just before the `time.sleep(0.2)`:

```python
        update_led()
        time.sleep(0.2)
```

This ensures:
- Flash cadence is smooth (called every ~0.2 s regardless of fetch/redraw schedule).
- LED state is driven even when the display is not redrawn (the idle path at line 529, and between fetch intervals when `need_fetch` is False and `received_ms` is None).

No other changes to the loop body are required.

---

## Precedence Logic — Detailed Reasoning

The three-level precedence is enforced by the `if / elif / else` in `draw_reading`:

1. **Stale check first.** `age_ms > _STALE_LIMIT_MS` fires before we have resolved whether val is in-range (val has already been blanked to None). If we checked out-of-range first, we would always fall through to `off` when stale, which is wrong.
2. **Out-of-range second.** Only reached when the reading is fresh (stale gate did not fire). The `color is RED` (or `_out_of_range` flag) check is the identical condition used for the display text colour, so display and LED always agree.
3. **Off as default.** Covers: fresh in-range reading, `val is None` with unknown age (e.g. `age_ms is None` — see edge cases below), and any path not covered above.

When `age_ms is None` (no NTP, no `received_ms`) the stale gate cannot fire. In this case we cannot know staleness, so the LED defaults to `off`. This is conservative: we do not falsely alarm, but we also do not suppress a genuine out-of-range alarm — the out-of-range check can still fire if `val` is not None and numeric is out of range.

---

## Edge Cases

### NTP not synced / monotonic fallback

Both the stale condition and the out-of-range condition still work in the fallback path:

- Stale gate: `age_ms` is derived from `ticks_diff(ticks_ms(), recv_ms)` when `_ntp_synced` is False and `recv_ms` is not None. This is already the existing fallback for the display; the LED condition reuses the same `age_ms` variable.
- Out-of-range: purely a function of `val`, independent of time. Unaffected.
- `age_ms is None` (NTP not synced AND no `received_ms`, i.e. before first reading): stale gate cannot fire; LED stays `off`. Correct.

### Boot and status screens (`draw_status`, `ensure_wifi`)

`draw_reading` is never called during boot/status screens, so `_led_mode[0]` remains `"off"` (its initialised value). `update_led()` is only in the poll loop body, which is only reached after `ensure_wifi()` and the NTP block complete. The LED is off throughout boot. This is the correct behaviour.

The one case to note: `draw_status("Starting", ...)` is called at line 484 just before the poll loop. At that point the LED is off and no `update_led()` has been called yet. LED stays off. Correct.

### API unreachable but last reading is still fresh

When `fetch_latest()` returns None, `draw_reading(last_state)` is still called with the cached `last_state` (line 526). `last_state` retains its `received_ms` / `ts_ms`, so `age_ms` is computed normally. If the cached reading is still within 5 minutes, LED behaviour is driven by the cached value's range, exactly as the display is. If the cache becomes stale (age > 5 min), LED transitions to solid red, matching the `---` display state. No special case is needed.

### `age_ms is None` with a non-None `val`

Possible when NTP not synced and `received_ms` is None (e.g. state dict was just initialised but first reading has not yet arrived — the initial `last_state` at line 474 has `value: None`, so `val` will also be None in `draw_reading`, and the out-of-range check is skipped). In practice val and received_ms are set together, but the LED default `off` is safe regardless.

### `val` is None but stale gate did not fire (`age_ms is None`)

`val` could be None in the initial state before any reading. `age_ms is None` so stale gate skips, `color` stays `WHITE` (not RED), so LED stays `off`. Correct.

### Session expiry / re-login during a stale window

`fetch_latest` returns None on both re-login attempts. `draw_reading(last_state)` is called with stale data. If old enough, solid red fires. The LED is not involved in the login recovery path at all.

---

## Off-by-Default Guarantee

Three layers ensure the LED is off when it should be:

1. `led.set_rgb(0, 0, 0)` at construction time (hardware init, before `main()`).
2. `_led_mode[0] = "off"` initial value — if `update_led()` is ever called before `draw_reading`, it calls `led.set_rgb(0, 0, 0)`.
3. The `else: led.set_rgb(0, 0, 0)` branch in `update_led()` explicitly sets off on every tick where mode is `"off"`, rather than simply not calling `set_rgb`. This guards against any future code path that sets the LED to a colour without going through this helper.

---

## Brightness Consideration

Full brightness (255, 0, 0) is very intense on the bare LED, particularly in a dark room or at night. A constant `_LED_RED_BRIGHTNESS = 80` is introduced. This is approximately 31 % of maximum and is clearly visible across a room in normal ambient light without being distracting.

The constant is separate from the display colour constants (which are pen IDs, not RGB tuples) and must not be confused with them. The coder should add a brief comment noting this distinction.

If the user later wants a different brightness, only this one constant needs changing.

---

## Verification

### Local (off-device)

```bash
.venv/bin/python3 -m py_compile src/main.py
```

Must exit 0 with no output. This catches syntax errors and import-level name errors.

### On-device manual checklist

1. **Boot LED off.** Flash and reboot. During Wi-Fi connect and "Starting..." status, verify LED is dark (not white as before).

2. **In-range fresh reading — LED off.** With a fresh in-range reading (e.g. 7.5 mmol/L), confirm LED stays off. Confirm the constant-white glow from before is gone.

3. **Out-of-range — flashing red.** With a fresh out-of-range reading (e.g. 3.5 or 15.0 mmol/L), confirm:
   - Value text on display is red.
   - LED flashes red at approximately 0.5 s on / 0.5 s off.
   - Flash is smooth (not stuttering with a ~30 s glitch when the poll fires).

4. **Stale — solid red.** Wait 5+ minutes without a new reading (or temporarily disconnect Wi-Fi to prevent fetches, then wait). Confirm:
   - Display shows `---`.
   - LED is solid red (not flashing).
   - LED is steady even as the loop continues.

5. **Recovery.** After stale → when a fresh in-range reading arrives:
   - LED turns off.
   - Display shows the value.

6. **Recovery from out-of-range.** After a flashing-red reading, when the next reading is in range:
   - LED stops flashing and turns off.

7. **Brightness subjective check.** Confirm `_LED_RED_BRIGHTNESS = 80` is visible but not eye-wateringly bright. Adjust constant if needed.

8. **NTP-not-synced path.** Manually break NTP (e.g. block port 123 or comment out `ntptime.settime()` for testing). Confirm the "No NTP" fallback still drives the LED correctly via monotonic age.

---

## Documentation Updates Required (documenter step)

### `requirements.md` — add a new section

Suggested wording (after the button-press section):

> **RGB LED alert indicator**
>
> The onboard RGB LED (GPIO 6/7/8) acts as a glanceable glucose alert:
> - **Off** when the current reading is in range (4–14 mmol/L) and fresh (< 5 min old), and during boot/status screens.
> - **Flashing red** (approx. 0.5 s on / 0.5 s off) when the reading is out of range (`> 14 or < 4 mmol/L`) and the reading is fresh.
> - **Solid red** when the reading is stale (> 5 minutes old). This takes precedence over the flashing state, since the numeric value is no longer known when stale.
>
> Brightness is controlled by `_LED_RED_BRIGHTNESS` (default 80/255).

### `CHANGELOG.md` — add under `## Unreleased / ### Added`

> - **RGB LED glucose alert** — the onboard LED (GPIO 6/7/8) now acts as a glanceable alert: off when in range and fresh, flashing red when out of range (< 4 or > 14 mmol/L), solid red when the reading is stale (> 5 min). Stale takes precedence. LED is explicitly off at boot and during status screens.

---

## Summary of All Edit Locations in `src/main.py`

| # | Location | What changes |
|---|---|---|
| 1 | Line 7 | Extend `from pimoroni import Button` to also import `RGBLED` |
| 2 | After line 21 (pen constants block) | Add `_LED_FLASH_PERIOD_MS` and `_LED_RED_BRIGHTNESS` constants (logically near the staleness constants at line 63 is also acceptable; pick the more readable grouping) |
| 3 | After line 85 (button init block) | Construct `led = RGBLED(6, 7, 8)` and call `led.set_rgb(0, 0, 0)` |
| 4 | Near the `_session` cell (~line 60) | Add `_led_mode = ["off"]` module-level cache |
| 5 | After `draw_reading` / before `draw_status` area | Add `update_led()` function |
| 6 | Inside `draw_reading`, before `display.update()` | Add LED mode assignment block (stale / out-of-range / off) using a local `_out_of_range` boolean flag set in the existing numeric test branch |
| 7 | Inside `main()` poll loop, just before `time.sleep(0.2)` | Add `update_led()` call |
