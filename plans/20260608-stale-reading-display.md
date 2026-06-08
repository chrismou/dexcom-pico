# Technical Design Doc: Stale Reading Display

**Date:** 2026-06-08
**Branch:** feature/pydexcom
**Author:** Architect pass — implementation by coder agent

---

## 1. Problem Statement

The "Last reading N mins ago" age text is always computed from `received_ms` and is therefore always accurate. However, the numeric glucose value is only blanked to `---` in one of the three render paths (the API-unreachable `else` branch). On a successful fetch that returns the same unchanged reading, and on every periodic 30-second refresh, the raw value is drawn regardless of age. This means a reading older than 5 minutes still shows its numeric value in contradiction with the feature requirement.

---

## 2. Affected Files

| File | Lines of interest |
|---|---|
| `/home/mou/dev/hardware/dexcom-pico/src/main.py` | `minutes_since_ms` (201-207), `draw_reading` (210-243), main loop staleness block (439-456) |

No other files require changes.

---

## 3. Recommended Approach: Enforce Staleness Inside `draw_reading`

### Decision

Apply the staleness rule inside `draw_reading`, not at the call sites in the main loop.

### Rationale

- `draw_reading` already owns 100% of the display logic and already has the `received_ms` value through `state`. It calls `minutes_since_ms` (line 233) to produce the age text — so the age is computed there anyway.
- There are three call sites in the main loop (lines 438, 442-443 / 450, 452, 456). Patching each one is error-prone and creates duplication. A single guard inside `draw_reading` is a single source of truth and is automatically correct for any future call site.
- The existing approach in the `else` branch (lines 439-452) builds a throw-away `stale_state` dict copy with `value = None`. That pattern works but spreads the threshold constant across the loop body. Moving the rule into `draw_reading` removes the need for that copy entirely.
- Keeping changes minimal: only `draw_reading` needs a small addition; the loop's `else` branch can be simplified without changing its external behaviour.

---

## 4. Logic Change in `draw_reading`

### Threshold Precision

The constant `stale_limit_ms = 5 * 60 * 1000` (300 000 ms) exists in `main()`. The age check in `draw_reading` should use a module-level constant so the value is not buried inside a function scope.

Define at module level (near the other constants, before `draw_reading`):

```
_STALE_LIMIT_MS = 5 * 60 * 1000   # 300 000 ms
```

**Boundary at exactly 5 minutes:** The feature request says "over 5 minutes old". The existing loop check uses `age > stale_limit_ms` (strict greater-than), meaning a reading at exactly 300 000 ms is NOT stale. This matches the wording "over 5 minutes". The new code must use the same strict `>` so behaviour is identical to the current `else`-branch logic.

`minutes_since_ms` returns `d // 60000` (integer floor division). A return value of `5` therefore covers the range 300 000 ms to 359 999 ms — i.e. it spans both "exactly 5" and "a bit over 5". To stay consistent with the ms-level check (`age > stale_limit_ms`) the guard inside `draw_reading` should operate in milliseconds rather than re-using the already-floored `mins` integer.

### Change Inside `draw_reading`

After computing `val` (line 215) and before the `if val is not None` block (line 223), add an age check:

```
received_ms = state.get("received_ms")
if received_ms is not None:
    age_ms = time.ticks_diff(time.ticks_ms(), received_ms)
    if age_ms < 0:
        age_ms = 0
    if age_ms > _STALE_LIMIT_MS:
        val = None   # blank the value; unit and trend are preserved below
```

This overwrites the local `val` variable only. The rest of `draw_reading` is unchanged: `unit`, `trend`, `mins_txt`, and the trend arrow all still render from the original `state` keys. The existing `show_text = "---"` default (line 220) already handles `val is None`, so no further display code changes are needed.

**Time complexity note:** `time.ticks_diff` is used (not subtraction) to handle the MicroPython `ticks_ms` 30-bit rollover correctly — consistent with `minutes_since_ms` at line 204.

---

## 5. Simplification of the Main Loop `else` Branch

After the change to `draw_reading`, the `else` branch (lines 439-452) contains duplicate logic. It can be collapsed:

```python
else:
    # API unreachable; draw current state (draw_reading applies staleness)
    draw_reading(last_state)
```

The `stale_state` dict copy and the `age > stale_limit_ms` branch inside the loop are deleted. The `stale_limit_ms` variable in `main()` becomes unused and should also be removed.

The periodic refresh path (lines 453-456) requires no change — `draw_reading(last_state)` will now handle staleness itself.

---

## 6. Summary of All Changes

1. Add `_STALE_LIMIT_MS = 5 * 60 * 1000` as a module-level constant (replaces `stale_limit_ms` local variable in `main`).
2. In `draw_reading`, after reading `val` from state, add a 6-line ms-level age guard that sets `val = None` when `age_ms > _STALE_LIMIT_MS` and `received_ms` is not `None`.
3. In `main`, collapse the `else` branch to a single `draw_reading(last_state)` call, removing the `stale_state` copy and the in-loop `age > stale_limit_ms` check.
4. Remove the now-unused `stale_limit_ms` local variable from `main()`.

Net change: approximately +8 lines, -10 lines.

---

## 7. Edge Cases for QA

| Case | Expected behaviour | Notes |
|---|---|---|
| `received_ms` is `None` (app just booted, no reading yet) | Guard is skipped; `val` remains `None`; screen shows `---` with no age text | Already handled by existing `value is None` path and `mins_txt` conditional |
| Reading exactly 5 minutes old (300 000 ms) | Value shown (not stale) | `age_ms > _STALE_LIMIT_MS` is false at exactly 300 000 ms |
| Reading 5 min 1 ms old | Value blanked to `---` | First moment the condition is true |
| `ticks_ms` rollover | Handled correctly | `time.ticks_diff` already used in `minutes_since_ms`; same pattern used in the new guard |
| Successful fetch returns same unchanged reading (ts unchanged, loop does not update `received_ms`) | `received_ms` reflects the last time a NEW reading arrived; age continues to grow; stale blanking still applies at 5 min | Correct — `received_ms` is only set on line 436 when `ts != last_ts_ms` |
| Successful fetch returns a new reading | `received_ms` reset to `time.ticks_ms()` at that moment; age guard immediately returns false; value shown | No regression |
| Unit and trend while stale | Both still rendered from `state` dict unchanged | `val = None` override is local to `draw_reading`; `unit` and `trend` come from separate `state.get()` calls |
| `mins` integer vs. `age_ms` consistency | "Last reading 5 mins ago" text can appear at the same render cycle as `---` | This is correct and intentional: age text shows how old the reading is while the value is suppressed |

---

## 8. Out of Scope

- No changes to the Dexcom fetch logic, Wi-Fi handling, or any other rendering function.
- No new imports or dependencies.
- No changes to `secrets.example.py` or `requirements.md`.

---

## Addendum: Reading Age Based on Sensor Timestamp

**Date appended:** 2026-06-08

### A1. Problem Statement (Addendum)

The "Last reading N mins ago" text at the top of the display is currently driven by `received_ms`, a `ticks_ms()` value stamped at the moment the device received a response from the Dexcom Share API. This is wrong in two ways:

1. It shows device-uptime age from receipt, not true age from when the sensor measurement was taken.
2. Because Dexcom Share can buffer readings, a freshly received response may carry a reading that is already several minutes old. That gap is invisible both in the age text and in the 5-minute staleness rule — a reading that is actually 6 minutes old could arrive and be shown as 0 minutes old before the stale blank kicks in.

The fix is to compute reading age from the sensor's own `ts_ms` (Unix epoch ms, already parsed from the `WT` field) using real wall-clock time obtained via NTP. `ts_ms` and the 5-minute staleness gate must both use this single "true age" computation — one source of truth.

---

### A2. Additional Affected Files

The changes in the original plan sections 3-6 remain as specified. This addendum introduces the following additional scope.

| File | New or changed areas |
|---|---|
| `/home/mou/dev/hardware/dexcom-pico/src/main.py` | `ensure_wifi()`, module-level NTP state sentinel, `main()` state shape, `draw_reading()` age computation, `minutes_since_ms()` (new overload or replacement) |

No new files. `ntptime` is a built-in MicroPython module on the Pico W firmware; no `requirements.md` change.

---

### A3. Epoch Reconciliation (Critical)

`ts_ms` from Dexcom is Unix epoch milliseconds (origin: 1970-01-01 00:00:00 UTC).

On the RP2040 / Pico port of MicroPython, `ntptime.settime()` sets the hardware RTC and `time.time()` then returns seconds since **2000-01-01 00:00:00 UTC** — not 1970. The difference is exactly **946 684 800 seconds**.

To get "current Unix epoch ms" on the device after NTP sync:

```
_PICO_EPOCH_OFFSET_S = 946_684_800
current_unix_ms = (time.time() + _PICO_EPOCH_OFFSET_S) * 1000
```

This must be used any time the device computes age from `ts_ms`.

The offset value is a fixed calendar constant and does not need runtime detection. However, to make the assumption explicit and to guard against a firmware build that has already corrected its epoch, the implementation should assert or document this clearly in a comment at the point of use. Do not attempt runtime epoch sniffing — it is fragile and unnecessary.

---

### A4. NTP Sync Design

#### Where to sync

NTP should be attempted once, immediately after `ensure_wifi()` confirms a connection — before the first Dexcom fetch. This keeps the boot sequence linear and ensures the clock is set before any reading arrives.

`ensure_wifi()` currently calls `draw_status("Wi-Fi connected", ...)` and returns. The NTP attempt should happen in `main()` right after the `ensure_wifi()` call, not inside `ensure_wifi()` itself. This keeps networking concerns separated: `ensure_wifi()` owns link-layer connectivity only.

#### Module-level NTP sentinel

Add a module-level flag:

```
_ntp_synced = False
```

Set to `True` after a successful `ntptime.settime()` call. This flag is read by `draw_reading()` and the staleness check to choose between true-age and fallback-age.

#### NTP sync logic in `main()` (after `ensure_wifi()`)

```
import ntptime

_NTP_MAX_RETRIES = 3
_NTP_RETRY_DELAY_S = 2

for attempt in range(_NTP_MAX_RETRIES):
    try:
        ntptime.settime()
        _ntp_synced = True
        break
    except Exception:
        if attempt < _NTP_MAX_RETRIES - 1:
            time.sleep(_NTP_RETRY_DELAY_S)

if not _ntp_synced:
    draw_status("No NTP", sub="Age may be approx", ...)
    time.sleep(1)
```

Three attempts with 2-second waits between them. If all fail the boot continues in fallback mode; a transient status message warns the user but does not block the main loop.

#### No periodic NTP re-sync

Because the device will typically run for at most a few hours between reboots and `ticks_ms()` drift over that period is negligible for a 5-minute threshold, a single NTP sync at boot is sufficient. Do not add periodic re-sync.

---

### A5. State Shape Changes

The current `last_state` dict in `main()`:

```python
last_state = {
    "value":       None,
    "unit":        "mmol/L",
    "trend":       None,
    "received_ms": None,   # ticks_ms() at moment of receipt (monotonic, no wall-clock)
}
```

Add `ts_ms` to `last_state` so `draw_reading()` can compute true age without accessing a separate variable:

```python
last_state = {
    "value":       None,
    "unit":        "mmol/L",
    "trend":       None,
    "received_ms": None,   # kept as fallback when NTP not synced
    "ts_ms":       None,   # Unix epoch ms from sensor WT field; None until first reading
}
```

When a new reading is stored into `last_state` (the `ts != last_ts_ms` branch, currently line ~432), also copy `ts_ms`:

```python
last_state = {
    "value":       data.get("value"),
    "unit":        "mmol/L",
    "trend":       data.get("trend"),
    "received_ms": time.ticks_ms(),
    "ts_ms":       data.get("ts_ms"),   # new
}
```

`received_ms` is retained. It serves as the fallback age basis when `_ntp_synced` is `False`.

---

### A6. True-Age Helper

`minutes_since_ms(t_ms)` (line 201) uses `ticks_diff` against `ticks_ms()` and is correct for monotonic inputs. It must not be used with Unix-epoch inputs.

Add a new helper for wall-clock-based age:

```
def minutes_since_unix_ms(ts_unix_ms):
    """Return age in whole minutes for a Unix-epoch-ms timestamp.
    Returns None if ts_unix_ms is None.
    Relies on NTP having set the RTC; call only when _ntp_synced is True.
    """
    if ts_unix_ms is None:
        return None
    now_unix_ms = (time.time() + _PICO_EPOCH_OFFSET_S) * 1000
    diff_ms = now_unix_ms - ts_unix_ms
    if diff_ms < 0:
        diff_ms = 0
    return diff_ms // 60000
```

And a parallel helper for raw ms age (used in the staleness gate):

```
def age_unix_ms(ts_unix_ms):
    """Return age in ms for a Unix-epoch-ms timestamp, or None."""
    if ts_unix_ms is None:
        return None
    now_unix_ms = (time.time() + _PICO_EPOCH_OFFSET_S) * 1000
    diff_ms = now_unix_ms - ts_unix_ms
    return max(0, diff_ms)
```

These helpers are pure functions with no side effects. They do not check `_ntp_synced` themselves — the caller is responsible for choosing the right helper based on `_ntp_synced`.

---

### A7. Changes to `draw_reading()`

`draw_reading()` currently reads `state.get("received_ms")` in two places (once for the staleness guard added by the base plan, once for `minutes_since_ms`). Both must be updated to use true age when available.

Revised logic (pseudo-code; replaces the guard and age-text sections):

```
ts_ms     = state.get("ts_ms")
recv_ms   = state.get("received_ms")

if _ntp_synced and ts_ms is not None:
    age_ms  = age_unix_ms(ts_ms)          # wall-clock based
    mins    = age_ms // 60000
else:
    # Fallback: monotonic age from receipt time
    if recv_ms is not None:
        raw_diff = time.ticks_diff(time.ticks_ms(), recv_ms)
        age_ms   = max(0, raw_diff)
        mins     = age_ms // 60000
    else:
        age_ms = None
        mins   = None

# Staleness gate (unifies with base plan section 4)
if age_ms is not None and age_ms > _STALE_LIMIT_MS:
    val = None

# Age text
mins_txt = ("Last reading %d mins ago" % mins) if mins is not None else ""
```

Key points:

- The staleness gate from the base plan (section 4) is absorbed here. The base plan's guard used `received_ms`; this replaces it with `age_ms`, which is computed from `ts_ms` when the clock is synced and from `received_ms` otherwise. The `_STALE_LIMIT_MS` constant and the `val = None` overwrite are unchanged.
- `mins_txt` is now also driven by `age_ms`, so the age text and the stale gate are always consistent — the same `age_ms` value drives both.
- The fallback (`_ntp_synced` is `False`) reproduces the current behaviour exactly: monotonic ticks from receipt.

---

### A8. First-Load Sequence

The user explicitly asked about first-load handling. This section walks through the full boot sequence for both the happy path and degraded paths.

#### Boot sequence

```
power on
  -> ensure_wifi()          (blocks until connected or user retries)
  -> NTP sync (3 attempts)
  -> draw_status("Starting", "Fetching latest...")
  -> main loop begins; first fetch is triggered immediately (last_fetch = 0)
```

#### Case 1: NTP succeeds, first reading arrives

- `_ntp_synced = True`.
- First fetch returns a reading with `ts_ms` = some Unix epoch ms value.
- `last_state["ts_ms"]` is set; `last_state["received_ms"]` is also set.
- `draw_reading()` takes the `_ntp_synced` branch: computes `age_ms = age_unix_ms(ts_ms)`.
- Age text shows true age immediately, e.g. "Last reading 3 mins ago" even though the reading just arrived.
- Staleness gate uses the same `age_ms` — if the reading is already stale when it arrives (e.g. Dexcom buffered it for 7 minutes) `val` is set to `None` and `---` is shown immediately. This is correct behaviour.

#### Case 2: NTP fails, first reading arrives

- `_ntp_synced = False`.
- `last_state["ts_ms"]` is populated but ignored by `draw_reading()`.
- `draw_reading()` takes the fallback branch: `recv_ms` is the `ticks_ms()` at receipt.
- At the moment of first draw `age_ms` is approximately 0 ms — the reading was just received.
- Age text shows "Last reading 0 mins ago" (or 1 min depending on exact timing).
- Staleness gate fires after 5 monotonic minutes from receipt, not from sensor time. This is the existing degraded behaviour and is acceptable.
- A "No NTP" status was shown at boot; the user has been informed.

#### Case 3: Boot, no reading yet, first draw before any fetch completes

This cannot happen in normal flow because `draw_status("Starting...")` is shown synchronously before the loop, and the first `draw_reading()` call only happens after `fetch_latest()` returns. However `last_state` has all `None` values at this point, so:

- `ts_ms = None`, `recv_ms = None`.
- `age_ms = None`, `mins = None`.
- `mins_txt = ""` — no age text is displayed.
- `val = None` — display shows `---`.

This is safe and correct; no guard needed.

#### Case 4: NTP succeeds but `ts_ms` is `None` in the returned reading (parse failure)

- `_ntp_synced = True` but `state.get("ts_ms") is None`.
- `draw_reading()` falls into the `else` branch: uses `recv_ms` (monotonic fallback).
- Staleness and age text degrade gracefully to receipt-time basis.
- No crash; no blank screen.

---

### A9. Unified Staleness Rule

The base plan (section 4) moved the stale gate inside `draw_reading()` and used `received_ms` as the basis. This addendum replaces that basis with `age_ms` as computed in section A7. The `_STALE_LIMIT_MS` constant, the strict `>` comparison, and the `val = None` local overwrite are all unchanged. There is no duplication between the age text and the stale gate — both consume `age_ms` from the same computation.

The practical benefit: a Dexcom reading that was buffered for 6 minutes will be immediately shown as `---` rather than appearing valid for 5 minutes after receipt.

---

### A10. Summary of Additional Changes

Relative to the base plan, the following new changes are required:

1. Add `_PICO_EPOCH_OFFSET_S = 946_684_800` and `_ntp_synced = False` as module-level constants.
2. Add `import ntptime` (built-in; no new dependency).
3. Add `minutes_since_unix_ms()` and `age_unix_ms()` helper functions near `minutes_since_ms()`.
4. In `main()`, after `ensure_wifi()`, attempt NTP sync (3 retries, 2 s apart); set `_ntp_synced = True` on success, show brief status on failure.
5. Add `"ts_ms": None` to the initial `last_state` dict in `main()`.
6. When writing a new reading into `last_state` (the `ts != last_ts_ms` branch), also copy `data.get("ts_ms")` into `last_state["ts_ms"]`.
7. In `draw_reading()`, replace the `received_ms`-only age computation with the dual-path logic in section A7 (NTP path uses `ts_ms` + `age_unix_ms`; fallback path uses `received_ms` + `ticks_diff`). This subsumes the staleness guard from the base plan.

Net additional change estimate: approximately +35 lines, -3 lines (relative to the base plan's already-agreed diff).

---

### A11. Edge Cases for QA (Addendum)

| Case | Expected behaviour |
|---|---|
| NTP sync succeeds; reading `ts_ms` parses correctly | Age text and stale gate use true sensor age immediately from first draw |
| NTP sync succeeds; reading `ts_ms` is `None` (parse failure) | Falls back to `received_ms` monotonic age; no crash |
| NTP sync fails; any reading | Falls back to `received_ms` monotonic age; "No NTP" shown at boot |
| NTP succeeds; reading already 6 min old when received | Shown immediately as `---` with "Last reading 6 mins ago"; stale gate fires on first draw |
| NTP succeeds; reading exactly 5 min old when received | Value shown (not stale); `age_ms == 300000` is not `> _STALE_LIMIT_MS` |
| Clock drift between NTP syncs | Not a concern at the scale of a few hours; 5-minute threshold has wide natural tolerance |
| `time.time()` returns 0 before NTP (RTC default) | `_ntp_synced` is `False`; wall-clock path never taken; no erroneous age computed |
| `ts_ms` from a future timestamp (clock skew) | `age_unix_ms` clamps to 0; "Last reading 0 mins ago"; value shown; not stale |
| Reading unchanged across polls (same `ts_ms`); NTP synced | `last_state["ts_ms"]` unchanged; `age_unix_ms` grows each render cycle; stale gate fires at correct wall-clock age |
