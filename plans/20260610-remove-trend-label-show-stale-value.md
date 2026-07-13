# Technical Design Doc: Remove Trend Label, Show Stale Value in Bottom-Right

**Date:** 2026-06-10
**Branch:** `feature/led`
**Scope:** `src/main.py` — two targeted edits

---

## Summary

The bottom-right corner currently shows the trend string label (e.g. `"singleUp"`) when a reading is present. The desired behaviour is:

- **Default state:** bottom-right is blank.
- **Stale state:** bottom-right shows `"Last: X.X"` (the last known glucose value before blanking), so a glanceable stale indicator is preserved without cluttering the normal view.

---

## Affected Files

| File | Change type |
|---|---|
| `src/main.py` | Logic change (two edits) |
| `requirements.md` | Spec update — document new bottom-right behaviour |
| `CHANGELOG.md` | User-facing entry in `Unreleased` section |

---

## Logic Changes

### Change 1 — Remove the trend label from `draw_trend()` (line 207–208)

**Location:** `draw_trend()` function, lines 207–208.

Current code:
```python
if trend is not None:
    draw_bottom_right(trend, WHITE, scale=1)
```

Remove these two lines entirely. The trend arrow drawing that follows (the `if trend == "doubleUp":` block etc.) is untouched.

### Change 2 — Show last known glucose value in bottom-right when stale (inside `draw_reading()`)

**Location:** `draw_reading()`, after the LED state block (currently line 319, before `display.update()`).

The staleness condition is already evaluated at line 279 and reused at line 312. The original (pre-blanking) glucose value lives in `state.get("value")` — `val` is set to `None` at line 280 to blank the main display, but the state dict is not mutated.

Insert the following logic after the LED state block (lines 312–317) and before `display.update()`:

```python
# Bottom-right: blank by default; show last known reading when stale.
if age_ms is not None and age_ms > _STALE_LIMIT_MS:
    last_val = state.get("value")
    if last_val is not None:
        draw_bottom_right("Last: %s" % last_val, GREY, scale=1)
```

Key decisions captured here:

- **Colour:** GREY (`GREY` constant already used for secondary text). The value is stale so a muted colour is appropriate; it avoids the RED/WHITE contrast used for live readings.
- **Label text:** `"Last: X.X"` is compact enough for the bottom-right area. `draw_bottom_right` right-aligns by character count so short strings are safe.
- **No-reading guard:** `last_val is not None` ensures the corner stays blank when the device has never received a reading (cold boot with no connectivity).
- **Unit omission:** Including the unit (`mmol/L`) would overflow the corner area at scale=1 given the 8px-per-char approximation for a ~20-char string. The glucose value alone (`"Last: 7.8"` = 9 chars = 72px) fits comfortably within a 320px-wide display.

---

## Potential Side Effects for QA

1. **Normal / fresh reading:** Bottom-right must be completely blank. Verify no residual trend label text appears. (The trend arrow in the right half should still draw normally.)

2. **Stale reading (age > 6 min):** Bottom-right must show `"Last: X.X"` in grey. The main value area must show `---`, the trend arrow must be hidden, and the LED must be solid red — all existing stale behaviours unchanged.

3. **Stale with no prior reading (None value):** Bottom-right must remain blank. This is the cold-boot / never-received-a-reading path.

4. **NTP fallback path:** Staleness is determined from `received_ms` monotonic age when `_ntp_synced` is False. The new bottom-right condition uses the same `age_ms` variable (already resolved from either path before the gate) — no separate handling needed, but verify the stale label appears correctly under the NTP-failure path.

5. **`draw_trend()` called with `trend=None`:** The removed two lines were guarded by `if trend is not None:`, so their removal cannot affect the `trend is None` path. Confirm no regression when no trend is available.

6. **Right-alignment overflow:** `draw_bottom_right` estimates `8 * scale` pixels per character. `"Last: 14.0"` is 10 chars = 80px — well within margin. No overflow risk for expected glucose values (1–3 decimal digits).

---

## Out of Scope

- The trend arrow drawing logic is untouched.
- The `draw_reading()` age/staleness computation is untouched.
- No changes to `_STALE_LIMIT_MS`, LED behaviour, or the `---` blanking logic.
- `secrets.py` / `secrets.example.py` are unaffected.