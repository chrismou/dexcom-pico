# Technical Design Doc: On-Device Settings Menu, Phone Wi-Fi/Dexcom Setup Page, Persistent Settings and Crash Log

**Date:** 2026-09-17
**Branch:** `feature/settings` (branched from `main` at `2870244`; PR targets `main`)
**PR:** https://github.com/chrismou/dexcom-pico/pull/5
**Scope:** `src/main.py` (single-file app, new sections), new `tests/` suite, `.gitignore`, `src/secrets.example.py`, `requirements.md`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`
**Status:** Revision 1 and Revision 2 are both implemented, QA'd and reviewed (188 tests) and committed on `feature/settings`; see the PR above. Line numbers in Revision 2 referred to the pre-Revision-2 working-tree `src/main.py` (1950 lines) and are historical.

---

## Revision 2: post-review changes (outstanding)

### R2.0 Summary of the six requests

1. Menu rows overlap the button-help text: shrink the rows so the hint is fully readable.
2. New menu item "Units" (mmol/L | mg/dL) directly above "Alert low"; it drives the unit label, the displayed value (mg/dL = raw integer, no decimal) and everything derived from the value.
3. Switching units resets the alert thresholds to that unit's defaults (mmol/L: 4.0 / 14.0, mg/dL: 70 / 180); menu labels become "Alert low" / "Alert high" with no unit; mg/dL ranges/steps defined below; red colouring, LED alerts, "Previous:" text and the setup page all respect the unit.
4. Remove "Stale after" and the `stale_minutes` setting; hard-code 6 minutes again; old `settings.json` files containing the key load cleanly.
5. Region shown with human-readable labels (Japan / United States / Rest of the world) in the menu and the phone page; stored values stay `ous`/`us`/`jp`.
6. Defaults: region `ous`, units mmol/L, alert low 4.0, alert high 14.0.

### R2.1 Affected files (working tree)

| File | Change |
|---|---|
| `src/main.py` | Schema (`units` added, `stale_minutes` removed), units-aware validation/stepping, `_STALE_LIMIT_MS` restored, reading state stores raw mg/dL, `draw_reading` converts at draw time, menu items/labels/layout, region labels shared with the setup page |
| `tests/test_settings.py` | Replace every `stale_minutes` assertion; add units/threshold/migration tests |
| `tests/test_menu.py` | Region/units `format_setting` expectations; units item position; int-threshold stepping |
| `tests/test_integration.py` | `test_run_menu_idle_cursor_commit_hold_exit` now edits "Units" at cursor 1; assert the threshold reset |
| `tests/test_http.py` | Region option labels on the page |
| `tests/test_glucose.py` (new) | Conversion, formatting, unit label, threshold specs/defaults, `apply_units_change`, `parse_dexcom_reading` returning `mg_dl` |
| `requirements.md`, `README.md`, `CHANGELOG.md`, `CLAUDE.md` | See R2.9 |

### R2.2 Settings schema, validation and migration

**Constants (new, in the "Settings schema" section, lines 143-160):**

```python
_UNITS_MMOL = "mmol"
_UNITS_MGDL = "mgdl"
_UNIT_CHOICES = (_UNITS_MMOL, _UNITS_MGDL)          # stored values
_UNIT_LABELS  = {_UNITS_MMOL: "mmol/L", _UNITS_MGDL: "mg/dL"}

_REGION_CHOICES = (                                 # (stored value, label) - order is the menu/page order
    ("ous", "Rest of the world"),
    ("us",  "United States"),
    ("jp",  "Japan"),
)
_REGION_LABELS = dict(_REGION_CHOICES)

# Alert threshold specs per unit: key -> (kind, (lo, hi, step), default)
_ALERT_SPECS = {
    _UNITS_MMOL: {
        "alert_low":  ("float", (2.0, 10.0, 0.1), 4.0),
        "alert_high": ("float", (7.0, 25.0, 0.5), 14.0),
    },
    _UNITS_MGDL: {
        "alert_low":  ("int", (40, 180, 5), 70),
        "alert_high": ("int", (120, 450, 10), 180),
    },
}
```

mg/dL ranges are the mmol ranges scaled and rounded to sensible clinical steps (2.0-10.0 mmol/L is 36-180 mg/dL; 7.0-25.0 is 126-450). Steps of 5 and 10 mg/dL keep holding A/B usable (28 and 33 steps end to end).

**Schema (`_SETTINGS_SCHEMA`, lines 146-157):**

- Remove `"stale_minutes"`.
- Add `"units": ("choice", _UNITS_MMOL, _UNIT_CHOICES)` positioned before `alert_low`.
- Change `alert_low` / `alert_high` entries to a new kind `"alert"` with `extra=None`; their real kind/range/default are resolved per unit via a helper. `dexcom_region` uses `tuple(v for v, _ in _REGION_CHOICES)` for its choices; default stays `_secret("DEXCOM_REGION", "ous")`.

**New pure helpers (next to the schema):**

- `alert_spec(key, units) -> (kind, extra, default)` - looks up `_ALERT_SPECS[units][key]`, falling back to mmol when `units` is unknown.
- `alert_defaults(units) -> (low, high)`.
- `setting_spec(key, settings) -> (kind, extra)` - the one place that turns a schema entry into a concrete (kind, extra) pair; for `"alert"` it uses `alert_spec(key, settings.get("units", _UNITS_MMOL))`, otherwise the schema tuple. `menu_reduce` and `validate_settings` both use it so the menu and the validator can never disagree.
- `apply_units_change(settings, new_units) -> dict` - returns a copy with `units` set; if `new_units` differs from `settings["units"]`, `alert_low`/`alert_high` are replaced by `alert_defaults(new_units)` (reset, not converted, as the user's wording implies; documented in requirements.md). Unchanged units returns an unchanged copy.

**`default_settings()` (line 164):** for kind `"alert"` the default comes from `alert_defaults(_UNITS_MMOL)`, so defaults are units=mmol, 4.0/14.0, region `ous` (request 6).

**`validate_settings(raw)` (lines 193-248), new order:**

1. Process `units` first (choice; invalid or absent -> mmol).
2. Process every other key as today, except kind `"alert"`: resolve `(kind, extra, default)` via `alert_spec(key, result["units"])`, then coerce with `_snap_float` or `_snap_int` exactly as the float/int branches do. Absent alert keys take the resolved default for the resolved units (not the mmol default) so `{"units": "mgdl"}` alone yields 70/180.
3. Unknown keys (including a legacy `stale_minutes`) are ignored because the loop iterates `_SETTINGS_SCHEMA` - this is the migration path; `serialise_settings` writes only schema keys, so the next save drops the legacy key from the file. No explicit migration code is needed, but tests must pin this behaviour (R2.8).
4. `alert_low >= alert_high` resets both to `alert_defaults(result["units"])` (line 245 currently uses `default_settings()`, which would wrongly put mmol values into an mg/dL profile).

**`step_value` (lines 1461-1486):** unchanged in signature; callers now pass the resolved kind/extra from `setting_spec`. Int stepping on the mg/dL thresholds uses the existing `"int"` branch.

**`stale_limit_ms()` (lines 339-341):** delete. Restore `_STALE_LIMIT_MS = 6 * 60 * 1000` in the "Staleness / epoch constants" block (around line 104) with the original comment that 6 min matches the Dexcom Share cadence, and use it at the three sites in `draw_reading` (lines 607-608, 632, 639).

### R2.3 Glucose units: reading state, conversion and display

The reading is stored **in mg/dL** and converted at draw time, so a units change takes effect on the next redraw without a refetch and change detection is unit-independent.

- `parse_dexcom_reading` (lines 740-758): return `{"mg_dl": int, "trend": ..., "ts_ms": ...}`; drop the `"value"` (mmol) and `"unit"` keys. `_MMOL_FACTOR` stays (used by the converter).
- New pure helpers (next to `_MMOL_FACTOR`):
  - `unit_label(units) -> str` - `_UNIT_LABELS.get(units, "mmol/L")`.
  - `convert_mg_dl(mg_dl, units)` - `None` -> `None`; mmol -> `round(mg_dl * _MMOL_FACTOR, 1)` (float, unchanged maths); mgdl -> `int(mg_dl)`.
  - `format_glucose(value, units) -> str` - mmol `"%.1f"`, mgdl `"%d"`; `None` -> `"---"`.
- `main()` state (lines 1866-1872 and 1915-1921): replace `"value"`/`"unit"` with `"mg_dl"`; `is_first = last_state.get("mg_dl") is None` (line 1909); the `ts_ms is None` fallback compares `data.get("mg_dl")` (line 1913). The CLAUDE.md "change detection" note is updated accordingly (R2.9).
- `draw_reading(state)` (lines 585-646):
  - `units = _settings.get("units", _UNITS_MMOL)`; `val = convert_mg_dl(state.get("mg_dl"), units)`; `unit = unit_label(units)`.
  - Staleness gate: `_STALE_LIMIT_MS` (constant) at all three sites.
  - Out-of-range: compare `val` against `_settings["alert_low"]` / `_settings["alert_high"]` (both already in the selected unit's scale, guaranteed by `apply_units_change` + `validate_settings`). Default fallbacks in the `.get()` calls become `alert_defaults(units)`.
  - Value text: `format_glucose(val, units)` (so mg/dL renders `101`, never `101.0`); unit label bottom-left from `unit_label`.
  - "Previous:" corner (line 642): `"Previous: " + format_glucose(convert_mg_dl(state["mg_dl"], units), units)`.
  - LED precedence unchanged; `_out_of_range` is derived from the unit-consistent comparison, so `update_led` needs no change.
- `fetch_latest` and the Dexcom client are untouched (the API is mg/dL already).
- Sentinel values: Dexcom 400/40 render as `400`/`40` in mg/dL and `22.2`/`2.2` in mmol/L, red in both.

### R2.4 Menu items, labels and layout

**`_MENU_ITEMS` (lines 1430-1440):**

```python
_MENU_ITEMS = (
    ("backlight",      "Backlight"),
    ("units",          "Units"),
    ("alert_low",      "Alert low"),
    ("alert_high",     "Alert high"),
    ("dexcom_region",  "Region"),
    ("led_alerts",     "LED alerts"),
    ("action:wifi",    "Wi-Fi setup"),
    ("action:info",    "Device info"),
    ("action:restart", "Restart"),
)
```

Still 9 rows. The region row label is shortened to "Region" because `"Dexcom region"` plus `"Rest of the world"` cannot fit on a 320 px row at scale 2 (bitmap8 averages ~6 px per character at scale 1, so ~12 px at scale 2: 15 + 17 characters is ~384 px). The units label was originally "Blood sugar units" but was shortened to "Units" at the user's request to avoid row overflow (the original label plus "mmol/L" at scale 2 spanned ~300 px, leaving no margin).

**`format_setting(key, value)` (lines 1445-1458):** `units` -> `unit_label(value)`; `dexcom_region` -> `_REGION_LABELS.get(value, str(value).upper())`; alert values are type-driven as today (`float` -> `"%.1f"`, `int` -> `str`), which is correct because mmol thresholds are floats and mg/dL thresholds are ints after validation. Remove the `stale_minutes` case (there is none, it fell through to `str`).

**`menu_reduce` (lines 1489-1540):** replace `kind, _default, extra = schema[key]` with `kind, extra = setting_spec(key, settings)` (pass `schema` through if the parameter is kept for tests). Everything else unchanged.

**`run_menu` commit path (lines 1706-1723):** replace the `candidate[key] = val` line with:

```python
if key == "units":
    candidate = apply_units_change(_settings, val)       # resets thresholds on change
else:
    candidate = dict(_settings); candidate[key] = val
validated = validate_settings(candidate)
if validated[key] != val: toast "Low must be < High"     # unchanged
else:
    _settings.update(validated)                           # was: _settings[key] = validated[key]
    apply_settings(); commit_settings(); toast
```

`_settings.update(validated)` is what carries the reset thresholds into the live dict when units change; for other keys it is a no-op beyond `key` because the rest of `validated` equals the already-valid live values.

**`draw_menu` (lines 1543-1580), layout fix (request 1) and long-value handling:**

- Header "Settings" at y=8 scale 2 (unchanged).
- Rows start at `y = 32`, `pitch = 20` (was 36 / 22). Nine rows now end at y = 32 + 8*20 + 16 = 208.
- Hint drawn at `y = HEIGHT - 16` (224), scale 1 (8 px tall, ends 232). Clearance between the last row and the hint is 16 px; nothing overlaps.
- Row content changes from a single concatenated string to two draws: `prefix + " " + label` left-aligned at x=4, and the value **right-aligned** at `WIDTH - 4` using `display.measure_text(value, 2)` (same technique as `draw_bottom_right`, with the `len * 8 * scale` fallback). No `": "` separator and no `< value >` brackets any more.
- Editing indicator: the value of the row being edited is drawn in `YELLOW` (cursor row otherwise WHITE, other rows GREY). The hint text "A/B adjust  Y save  X cancel" already tells the user they are editing. This removes the 4 extra characters that made the region row overflow while editing.
- Overflow guard: if `measure(label_text) + measure(value) + 12 > WIDTH`, draw the value at scale 1 instead (vertically offset by 4 px to centre it in the row). This is a deterministic fallback so no combination of label and value can wrap onto the next row (`draw_text` wraps at `WIDTH`, which is what would corrupt the layout).
- Toast (line 1785) stays at `HEIGHT // 2`.

**`show_device_info`** unchanged.

### R2.5 Setup page region labels (request 5)

`render_setup_page` (lines 1004-1009): iterate `_REGION_CHOICES` instead of the inline tuple, so the dropdown reads "Rest of the world / United States / Japan" with values `ous/us/jp`. `validate_setup_form` (line 1037) checks membership in `_REGION_LABELS` instead of the literal tuple. The units setting is menu-only and is not added to the page (decision R2-1).

### R2.6 Defaults (request 6)

Already satisfied by R2.2: `default_settings()` yields `dexcom_region="ous"` (unless `secrets.py` overrides it), `units="mmol"`, `alert_low=4.0`, `alert_high=14.0`. `secrets.example.py` keeps `DEXCOM_REGION = "ous"` and its comment gains the label ("rest of the world").

### R2.7 Potential side effects for QA (revision 2)

1. **Units switch resets thresholds**: change units mmol/L -> mg/dL in the menu; Alert low/high must read 70/180 immediately, the main screen must show an integer value with "mg/dL" bottom-left, and the red/LED rule must use 70/180. Switch back: 4.0/14.0, one-decimal value, "mmol/L". Re-selecting the same unit (Y without stepping) must not reset custom thresholds.
2. **Persisted mg/dL profile survives reboot** with int thresholds; `settings.json` should contain `"units": "mgdl", "alert_low": 70, "alert_high": 180`.
3. **Legacy file**: a `settings.json` containing `stale_minutes` boots without error, the key is ignored, and it disappears from the file after the next confirmed edit. Staleness is 6 min regardless of the legacy value.
4. **Hand-edited inconsistent file** (`units: mgdl` with `alert_low: 4.0`): values are clamped into the mg/dL range (4.0 -> 40) and, if low >= high results, reset to 70/180. Not a crash.
5. **Stale readings**: `---`, solid-red LED and "Previous: 101" (mg/dL) or "Previous: 5.6" (mmol/L) all trigger at > 6 min in both age paths (NTP and monotonic).
6. **Menu layout**: on device, confirm the hint line is fully visible below the ninth row, "Rest of the world" and "Units"/"mmol/L" rows do not wrap, and the edited value turns yellow. Check the region row while editing (longest case).
7. **Setup page**: dropdown shows the three labels; submitting each stores the short code; Dexcom login still uses the stored code for the base URL.
8. **Change detection** now keys on `mg_dl` in the parse-failure fallback; a repeated identical reading must still not reset `received_ms`.
9. **Integer formatting**: mg/dL must never show `.0`; mmol must always show one decimal (`5.0`, not `5`).
10. **Existing tests**: every `stale_minutes` reference is removed; the run_menu integration test's cursor-1 item is now units, so its final assertion changes (R2.8).

### R2.8 Tests (revision 2)

- `tests/test_settings.py`: replace `stale_minutes` assertions (defaults, non-dict/None/missing/corrupt/wrong-type, int clamp, snapped, save/load round-trip, overwrite) with `backlight`/`alert_high`/`units` equivalents; add:
  - `units` default mmol; invalid units -> mmol; `{"units": "mgdl"}` alone -> 70/180 ints; `{"units": "mmol"}` -> 4.0/14.0 floats.
  - mg/dL clamp and snap (`alert_low: 33` -> 40, `alert_high: 999` -> 450, `alert_low: 72` -> 70), low >= high in mg/dL resets to 70/180 (not 4.0/14.0).
  - Migration: `{"stale_minutes": 9, "backlight": 0.7}` loads with no `stale_minutes` key and backlight 0.7; a saved file from such a dict does not contain `stale_minutes`; `_STALE_LIMIT_MS == 360000`; `hasattr(main, "stale_limit_ms")` is False.
  - `apply_units_change`: change resets both thresholds to the target defaults; same units leaves custom thresholds untouched; input dict is not mutated.
  - `setting_spec("alert_low", {"units": "mgdl"})` returns `("int", (40, 180, 5))`; unknown units fall back to mmol.
- `tests/test_glucose.py` (new): `convert_mg_dl` (100 -> 5.6 mmol, 100 -> 100 int, None -> None), `format_glucose` (`5.0` stays `"5.0"`, `101` -> `"101"`, None -> `"---"`), `unit_label`, `_REGION_LABELS` contents and order of `_REGION_CHOICES`, `parse_dexcom_reading` returns `mg_dl` int and no `value`/`unit` keys, sentinel 400/40 conversions.
- `tests/test_menu.py`: `format_setting("dexcom_region", "ous") == "Rest of the world"` (and us/jp); `format_setting("units", "mgdl") == "mg/dL"`; `_MENU_ITEMS[1][0] == "units"` and `_MENU_ITEMS[2][0] == "alert_low"`; no item has key `stale_minutes`; labels for alert items contain no unit; stepping `alert_low` via `menu_reduce` with `settings["units"] = "mgdl"` and thresholds 70/180 gives 75 (int); with mmol gives 4.1; remove `test_int_as_string`'s `stale_minutes` usage (use a literal int through `format_setting("alert_low", 70)`).
- `tests/test_integration.py::test_run_menu_idle_cursor_commit_hold_exit`: cursor 1 is now "Units"; the scripted "up" steps mmol -> mgdl and Y commits; assert `_settings["units"] == "mgdl"`, `alert_low == 70`, `alert_high == 180`, and that `_settings_saved` matches (commit happened). Add a second integration case that commits units without stepping and asserts custom thresholds survive.
- `tests/test_http.py::TestRenderSetupPage`: page contains `>Rest of the world<`, `>United States<`, `>Japan<` and `value="ous"` etc.
- `tests/hw_stubs.py`: no change expected (no new hardware calls; `measure_text` already stubbed - confirm it returns an int so the right-align maths works).
- Run: `.venv/bin/python3 -m unittest discover -s tests -v` and `.venv/bin/python3 -m py_compile src/main.py`.

### R2.9 Documentation edits

- **CLAUDE.md**: line 7 "displays the latest glucose reading (mmol/L)" -> "(mmol/L or mg/dL, selectable in the menu)". Line 32 staleness bullet: back to `_STALE_LIMIT_MS` (6 min, hard-coded to match the Dexcom Share cadence; deliberately not configurable), still strict `>`, still the three call sites. Line 52 convention: "Glucose arrives as mg/dL and is stored as `mg_dl` in the reading state; conversion happens only in `draw_reading` via `convert_mg_dl`/`format_glucose` based on `_settings["units"]`. Alert thresholds are stored in the selected unit's scale and are reset to that unit's defaults by `apply_units_change` when units change; never compare thresholds across units." Change-detection bullet: `ts_ms`, falling back to `mg_dl`. Settings bullet: mention `setting_spec`/`_ALERT_SPECS` as the single source for per-unit ranges and that legacy keys are dropped on load.
- **requirements.md**: line 20 -> units are selectable (default mmol/L one decimal; mg/dL integer); line 30 stale wording back to "older than 6 minutes" (fixed, matches Dexcom cadence); lines 34/51 thresholds "in the selected unit"; the menu list (lines 68-76): remove stale item, add "Units (mmol/L / mg/dL, default mmol/L); switching resets the alert thresholds to that unit's defaults", alert low/high lines list both unit ranges, region line shows the labels; setup-mode bullet: region dropdown labels; note that the stored region values remain `ous/us/jp`.
- **README.md**: features list (lines 14, 16, 19), display-state text (102, 105), menu table (124-127: remove Stale row, add Units row, unit-free alert labels with both ranges, Region row "Rest of the world / United States / Japan"), setup form paragraph (145) mentions the region names, `DEXCOM_REGION` line (68) adds the labels.
- **CHANGELOG.md** (`Unreleased`): remove the "Configurable stale threshold" Added entry (line 18) and the Changed entry (line 25); adjust the menu entry (line 9) to list "units" and drop "stale threshold"; add Added entries "Units menu item (mmol/L or mg/dL) selectable in the settings menu; mg/dL shows the raw Dexcom integer" and "Region names shown in full (Rest of the world / United States / Japan) in the menu and setup page"; adjust the alert-threshold entry (17/26) to say thresholds are in the selected unit and reset to 4.0/14.0 or 70/180 on a unit change; add a Technical Notes line that a legacy `stale_minutes` key in `settings.json` is ignored and removed on the next save. Nothing in `Unreleased` has shipped, so entries are rewritten rather than appended as fixes.
- **src/secrets.example.py**: region comment lists the labels.

### R2.10 Assumptions (revision 2)

1. Switching units **resets** thresholds to the target unit's defaults rather than converting them (the user's wording "the alert low and alert high defaults change to 70 and 180" plus "switching resets the thresholds"). Custom thresholds are therefore lost on a unit switch; documented in requirements.md and README.
2. mg/dL threshold ranges/steps: low 40-180 step 5 (default 70), high 120-450 step 10 (default 180); mmol/L ranges unchanged.
3. Stored unit values are `"mmol"` / `"mgdl"`; the labels `mmol/L` / `mg/dL` are display-only. Stored region values are unchanged (`ous`/`us`/`jp`) as requested.
4. The menu row label for region is "Region" (not "Dexcom region") purely to fit "Rest of the world" on one row; the setup page keeps "Dexcom region" as its field label.
5. Editing is indicated by drawing the value in yellow instead of `< value >` brackets, to avoid overflow on long values. The hint text already distinguishes the editing state.
6. The reading state stores raw `mg_dl` so a unit change is reflected on the next redraw without a fetch; this also changes the internal state keys (`value`/`unit` removed), which only `main()`, `draw_reading` and `parse_dexcom_reading` use.
7. Units are not on the phone setup page (menu-only), confirmed by the user (R2-1 in R2.12).

### R2.11 Open Questions (revision 2)

None outstanding.

### R2.12 Resolved Decisions (revision 2)

- **R2-1 (resolved 2026-09-18): units are menu-only.** The phone setup page does not offer a "Units" choice. `render_setup_page` and `validate_setup_form` change only for the region labels described in R2.5; the units/threshold-reset rule lives solely in the menu commit path (R2.4).

---

## Revision 1 (implemented on `feature/settings`)

The sections below are the original design as implemented. Where Revision 2 changes something (stale threshold, alert labels/ranges, menu layout, region labels, reading state keys), Revision 2 takes precedence.

---

## Summary

Four related features, all layered on top of the resilience work already at HEAD (request timeout, 8 s watchdog, Wi-Fi rejoin, boot retry, reboot-on-fatal, socket close fix):

1. **Settings menu** on the Display Pack 2.8's four buttons (X open/back, hold X exit, A up, B down, Y select). Numeric/choice/toggle items are edited in place. While the menu is open the poll loop is suspended, no button triggers a fetch, and the LED alert keeps running.
2. **Wi-Fi setup mode** ("Wi-Fi setup" menu item, and reachable from the boot "Wi-Fi failed" screen): the Pico raises a WPA2 access point, shows SSID/password/URL (plus a Wi-Fi QR code when the Pimoroni `qrcode` module is present), and serves one HTML form from a raw-socket HTTP server listing scanned networks, plus Dexcom account id / password / region. Submit writes settings, tears the AP down, joins the chosen network with on-screen feedback, verifies the Dexcom credentials with one login attempt, and on either failure reopens the page with the error.
3. **Persistence** in `/settings.json` on the LittleFS flash: atomic write (tmp file + `os.rename`), only when a value changes; missing/corrupt file falls back to defaults, with `secrets.py` supplying the default credentials. `secrets.py` becomes optional.
4. **Crash log** `/crash.json`: written only on the fatal-error path (and once after a watchdog reset is detected at boot), containing a crash counter, watchdog-reset counter and the last exception text.

All pure logic (settings merge/validate/serialise, HTTP request and form parsing, HTML escaping, menu state transitions, X-button hold detection) is written as hardware-free functions so it runs under CPython with the hardware modules stubbed, and a `unittest` suite is added under `tests/`.

---

## Affected Files

| File | Change type |
|---|---|
| `src/main.py` | Major: new settings/persistence, menu, AP + HTTP setup server, crash log; poll loop button handling reworked; thresholds/backlight/credentials read from settings |
| `tests/__init__.py`, `tests/hw_stubs.py`, `tests/test_settings.py`, `tests/test_http.py`, `tests/test_menu.py` | New: CPython unit tests with stubbed hardware modules |
| `.gitignore` | Add `src/settings.json`, `src/crash.json` (and root-level `settings.json` / `crash.json`, in case they are pulled off the device into the repo root) |
| `src/secrets.example.py` | Comment: the on-device setup page can replace hand-editing this file; file is now optional |
| `requirements.md` | Spec: menu, setup mode, persistence, crash log, configurable thresholds, `secrets.py` optional |
| `README.md` | Setup section (phone setup as the primary path), on-device behaviour (menu, setup mode), troubleshooting, testing, library list (`qrcode` optional) |
| `CHANGELOG.md` | `Unreleased`: Added entries for each feature, Changed for button behaviour / thresholds now configurable |
| `CLAUDE.md` | Commands (test suite now exists), architecture notes for settings, menu, setup server, crash log; fix the "5 min" wording to "6 min default, configurable" |

No new runtime dependencies. `qrcode` is an optional import guarded by `try/except ImportError`. Tests use the stdlib `unittest` (no change to `.venv`; decision 6).

---

## Current State (what the design builds on)

Relevant facts from `src/main.py` at HEAD (`2870244`):

- Line 9: `from secrets import WIFI_SSID, WIFI_PASSWORD, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD, DEXCOM_REGION` (hard import; a missing file crashes at import and reboot-loops via the fatal handler).
- Line 13: `display.set_backlight(0.5)` hard-coded at import.
- Line 71: `_STALE_LIMIT_MS = 6 * 60 * 1000`, used at lines 346, 379, 387.
- Lines 358: out-of-range test `numeric > 14 or numeric < 4` hard-coded.
- Lines 111-114: `Button(12/13/14/15)`; line 226 `any_button_pressed()` uses `.read()` with short-circuit `or` (so later buttons are not read when an earlier one is pressed).
- Line 170 `rejoin_wifi` and lines 521-549 `fetch_latest` use the `secrets` constants directly.
- Line 522: config-error text says "Set Dexcom creds in secrets.py".
- Lines 559-582 `ensure_wifi()`: any button skips the retry countdown.
- Lines 623-677 poll loop: `button_pressed = any_button_pressed()` forces a fetch on any press.
- Lines 680-694 fatal handler: shows error 3 s then `machine.reset()`.
- Pimoroni `Button.read()` returns True on the press edge and then auto-repeats every 200 ms while held (faster after 1 s); `Button.raw()` gives the instantaneous level. This matters for hold detection and for not re-triggering actions while a button is held.

---

## Design

### 1. Settings model and persistence

#### 1.1 Constants (new section "----- Settings -----", placed after the resilience constants, before the button setup)

```python
_SETTINGS_PATH = "/settings.json"
_SETTINGS_TMP_PATH = "/settings.json.tmp"
_CRASH_LOG_PATH = "/crash.json"
_CRASH_TEXT_MAX = 400          # bytes of exception text kept
```

Absolute root paths are used deliberately so behaviour does not depend on whether `main.py` was deployed to `/` or `/src/`.

#### 1.2 Optional `secrets.py`

Replace the hard import with:

```python
try:
    import secrets as _secrets
except ImportError:
    _secrets = None

def _secret(name, default=""):
    return getattr(_secrets, name, default) if _secrets is not None else default
```

The five module constants `WIFI_SSID` etc. are removed; all readers go through `_settings` (below).

#### 1.3 Schema

A single module-level `_SETTINGS_SCHEMA` dict describing each key, used by validation, the menu, and the setup form:

| key | type | default | constraints | edited where |
|---|---|---|---|---|
| `wifi_ssid` | str | `_secret("WIFI_SSID")` | max 32 chars | setup page |
| `wifi_password` | str | `_secret("WIFI_PASSWORD")` | max 63 chars, may be empty (open network) | setup page |
| `dexcom_account_id` | str | `_secret("DEXCOM_ACCOUNT_ID")` | stripped, max 64 | setup page |
| `dexcom_password` | str | `_secret("DEXCOM_PASSWORD")` | max 64 | setup page |
| `dexcom_region` | choice | `_secret("DEXCOM_REGION", "ous")` | one of `us`, `ous`, `jp`; invalid -> `ous` | menu + setup page |
| `backlight` | float | `0.5` | 0.1 - 1.0 step 0.1 | menu |
| `stale_minutes` | int | `6` | 2 - 15 step 1 | menu |
| `alert_low` | float | `4.0` | 2.0 - 10.0 step 0.1, must be `< alert_high` | menu |
| `alert_high` | float | `14.0` | 7.0 - 25.0 step 0.5, must be `> alert_low` | menu |
| `led_alerts` | bool | `True` | toggle | menu |

Suggested representation (kept simple for MicroPython, no classes needed):

```python
_SETTINGS_SCHEMA = {
    # key: (kind, default, extra)   kind in "str", "choice", "float", "int", "bool"
    "wifi_ssid":         ("str",    lambda: _secret("WIFI_SSID"), 32),
    ...
    "dexcom_region":     ("choice", lambda: _secret("DEXCOM_REGION", "ous"), ("ous", "us", "jp")),
    "backlight":         ("float",  0.5, (0.1, 1.0, 0.1)),
    "stale_minutes":     ("int",    6,   (2, 15, 1)),
    "alert_low":         ("float",  4.0, (2.0, 10.0, 0.1)),
    "alert_high":        ("float",  14.0, (7.0, 25.0, 0.5)),
    "led_alerts":        ("bool",   True, None),
}
```

Defaults that depend on `secrets.py` are callables so `default_settings()` evaluates them at call time (tests can swap `_secrets`).

#### 1.4 Pure functions (testable)

- `default_settings() -> dict` - materialises defaults from the schema.
- `validate_settings(raw: dict) -> dict` - starts from `default_settings()`, then for each schema key present in `raw`: coerce to the kind (`int`/`float` from numbers or numeric strings, `bool` from bool/`"1"`/`"0"`/`"on"`), clamp numeric values into range and snap to the step (round to 1 dp for floats), replace an invalid choice with the default, truncate strings, drop unknown keys, and finally enforce `alert_low < alert_high` (if violated, reset **both** to defaults so the pair is always coherent). Never raises; a non-dict `raw` returns defaults.
- `settings_changed(a: dict, b: dict) -> bool` - key-wise comparison (floats compared after `round(…, 2)`).
- `serialise_settings(settings: dict) -> str` - `json.dumps` of only the schema keys (stable, no `secrets.py` values leak beyond what was explicitly saved: see 1.6).

Note on `_secret` precedence: a key **absent** from `settings.json` falls back to the `secrets.py`/built-in default; a key **present** wins even when empty (an empty `wifi_password` is a legitimate open-network value). Therefore the file is always written with the full schema key set (after the first save every key is "present"), which is the simplest way to make "what the user saved is what runs" true.

#### 1.5 File I/O

- `load_settings(path=_SETTINGS_PATH) -> dict` - `open`/`json.load` inside `try/except Exception`; any failure (missing file, JSON error, wrong type) returns `default_settings()`. Result always passes through `validate_settings`.
- `save_settings(settings: dict, path=_SETTINGS_PATH, tmp_path=_SETTINGS_TMP_PATH) -> bool` - writes `serialise_settings(...)` to `tmp_path`, `f.flush()`, closes, then `os.rename(tmp_path, path)`. LittleFS `rename` replaces an existing target atomically; as a defensive fallback, if `os.rename` raises `OSError` (FAT-based builds return EEXIST), `os.remove(path)` then retry the rename once. Returns False on any exception (callers show "Save failed" but keep running). Never writes if `settings_changed(current_saved, new)` is False: the caller compares against `_settings_saved` (the last dict known to be on flash) before calling.
- Module state: `_settings = {}` (live values) and `_settings_saved = {}` (last persisted snapshot). Both are plain dicts mutated in place (matches the existing mutable-cell convention). `main()` does `_settings.update(load_settings()); _settings_saved.update(_settings)`, then `apply_settings()`.
- `apply_settings()` - side-effect hook: `display.set_backlight(_settings["backlight"])`. Called after load and after every confirmed edit.
- `commit_settings() -> bool` - if changed vs `_settings_saved`, `save_settings`, then update `_settings_saved`; if credentials or region changed, clear `_session[0]`.

#### 1.6 Readers that switch from constants to `_settings`

| Location | Change |
|---|---|
| line 13 `display.set_backlight(0.5)` | keep as boot default; `apply_settings()` overrides once settings are loaded |
| `rejoin_wifi` (line 170) | `wlan.connect(_settings["wifi_ssid"], _settings["wifi_password"])` |
| `ensure_wifi` (line 562) | `connect_wifi(_settings["wifi_ssid"], _settings["wifi_password"], timeout=20)` |
| `fetch_latest` (lines 521-549) | `_settings["dexcom_account_id"]`, `_settings["dexcom_password"]`, `_settings["dexcom_region"]` |
| `fetch_latest` config-error text | `"Menu > Wi-Fi setup to set them"` (X opens the menu) |
| `_STALE_LIMIT_MS` (lines 346, 379, 387) | replace with `stale_limit_ms()` returning `_settings["stale_minutes"] * 60000`; keep `_STALE_LIMIT_MS` name out of the code (docs updated) |
| `draw_reading` out-of-range (line 358) | `numeric > _settings["alert_high"] or numeric < _settings["alert_low"]` |
| `update_led` | if `not _settings["led_alerts"]`: force off (LED mode is still computed, so re-enabling takes effect on the next tick) |

### 2. Button handling and the menu

#### 2.1 Button semantics

- `A` (GP12, top-left) = up / increase, `B` (GP13, bottom-left) = down / decrease, `X` (GP14, top-right) = open menu / back / hold to exit, `Y` (GP15, bottom-right) = select / confirm.
- Poll loop (replace line 626): read **all four** buttons every tick into locals (no short-circuit) so edge state stays current:
  ```python
  a, b, x, y = button_a.read(), button_b.read(), button_x.read(), button_y.read()
  if x:
      run_menu(last_state)          # blocking; returns when the menu is closed
      last_fetch = time.ticks_ms() - poll_ms   # force a fetch on the next tick
      continue
  button_pressed = a or b or y
  ```
  A, B, Y keep their existing "refresh now" meaning on the main screen. `any_button_pressed()` stays for `connect_wifi`/`ensure_wifi` cancel paths but is changed to read all four buttons (no short-circuit).
- Hold detection is implemented once as a pure helper so the same logic serves the menu and setup mode:
  ```python
  class HoldDetector:
      """Classify raw X-button state into 'press' (released before hold_ms),
      'hold' (held for hold_ms, reported once) or None. Pure: caller passes now_ms."""
      def __init__(self, hold_ms): ...
      def update(self, is_down: bool, now_ms: int) -> str | None
  ```
  `_MENU_HOLD_MS = 1500`.
- After `run_menu()` / setup returns, `wait_buttons_released()` spins (feeding the watchdog, `time.sleep(0.05)`) until all four `.raw()` are False, so the auto-repeat of a still-held button cannot re-open the menu or trigger a fetch.

#### 2.2 Menu model (pure, testable)

```python
_MENU_ITEMS = (
    # (key or action, label)
    ("backlight",      "Backlight"),
    ("stale_minutes",  "Stale after (min)"),
    ("alert_low",      "Alert low (mmol/L)"),
    ("alert_high",     "Alert high (mmol/L)"),
    ("dexcom_region",  "Dexcom region"),
    ("led_alerts",     "LED alerts"),
    ("action:wifi",    "Wi-Fi setup"),
    ("action:info",    "Device info"),
    ("action:restart", "Restart"),
)
```

State dict: `{"cursor": int, "editing": bool, "edit_value": Any, "action": None | str, "closed": bool}`.

`menu_reduce(state, event, settings, schema=_SETTINGS_SCHEMA) -> state` with `event in ("up", "down", "select", "back", "exit")`:

- Not editing: `up`/`down` move the cursor with wrap-around; `select` on a settings key enters editing with `edit_value = settings[key]`; `select` on an action sets `state["action"]`; `back` or `exit` sets `closed`.
- Editing: `up`/`down` step the value (`step_value(kind, value, delta, extra)` clamps and snaps; `choice` cycles; `bool` toggles); `select` sets `state["commit"] = (key, edit_value)` and leaves editing; `back` discards; `exit` discards and closes.
- The reducer never touches `_settings` or hardware; the driver loop applies `commit`.

`step_value` is also pure and covered by tests (clamping at both ends, float rounding drift such as `0.1 + 0.1 + 0.1`).

#### 2.3 Menu driver (`run_menu(last_state)`)

Blocking loop, exits when `state["closed"]`:

1. Each tick: `feed_watchdog()`, `update_led()` (LED alerts stay live while the menu is open), `time.sleep(0.05)`.
2. Input: `A.read()` -> `"up"`, `B.read()` -> `"down"` (auto-repeat gives fast stepping when held), `Y.read()` -> `"select"` only on the first True until released (track a `y_latched` flag from `.raw()`), X via `HoldDetector` -> `"press"` maps to `"back"`, `"hold"` maps to `"exit"`.
3. On `commit`: validate via `validate_settings({**_settings, key: value})` (this is where `alert_low < alert_high` is enforced; if the pair is rejected, show "Low must be < High" for 1.5 s and keep the old value), update `_settings`, `apply_settings()`, `commit_settings()`; show "Saved" / "Save failed" toast for ~1 s.
4. While editing `backlight`, apply the candidate value live (`display.set_backlight(edit_value)`) so the user sees the effect; `back` restores the saved value.
5. Actions: `wifi` -> `run_wifi_setup()` then redraw the menu; `info` -> `show_device_info()` (SSID, IP, RSSI via `wlan.status('rssi')` in try/except, `_ntp_synced`, `gc.mem_free()`, crash/WDT counters and last error from `read_crash_log()`; any button returns); `restart` -> confirm screen ("Y to restart, X to cancel"), then `machine.reset()`.
6. Inactivity timeout `_MENU_IDLE_TIMEOUT_MS = 60000`: closes the menu automatically so the glucose display is never hidden indefinitely (decision 2). Any button press resets the timer; an in-progress edit is discarded on timeout (same as `exit`).
7. On exit: `wait_buttons_released()`, then the caller forces a fetch and redraw.

#### 2.4 Menu rendering (`draw_menu(state)`)

320x240, `bitmap8` scale 2 (16 px glyphs), header "Settings" at y=8 in CYAN, footer hint at y=HEIGHT-20 in GREY ("A/B move  Y select  X back  hold X exit" or, when editing, "A/B adjust  Y save  X cancel"). Items from y=36 with 22 px pitch: all 9 items fit without scrolling; the row under the cursor is drawn with a `>` marker and WHITE, others GREY. Values are right-aligned via `display.measure_text`. While editing, the row shows `< value >`. Values are formatted by `format_setting(key, value)` (pure): `backlight` as `%d%%`, floats one decimal, bool as `On`/`Off`, region upper-cased.

### 3. Wi-Fi setup mode

#### 3.1 Entry points

- Menu item "Wi-Fi setup".
- Boot: in `ensure_wifi()`, if `_settings["wifi_ssid"]` is empty, go straight into `run_wifi_setup()` (nothing to connect to). During the "Wi-Fi failed / Retrying in Ns" countdown, X opens the settings menu (from which setup can be started); A/B/Y keep the existing "retry now" behaviour (decision 1). No automatic entry into AP mode after repeated failures: an unattended device must keep retrying its saved network through a router outage.
- Setup mode itself times out after `_SETUP_IDLE_TIMEOUT_MS` (10 min) with no HTTP request received (decision 2); on timeout it behaves exactly like a hold-X cancel (AP down, return False, caller resumes its previous loop).
- After setup joins successfully from the boot path, `ensure_wifi` returns and NTP sync proceeds as today. From the menu path, if `_ntp_synced` is still False, one `ntptime.settime()` attempt is made (in `try/except`).

#### 3.2 Access point

Constants:

```python
_AP_SSID_PREFIX = "dexcom-pico-"      # + last 4 hex chars of machine.unique_id()
_AP_IP = "192.168.4.1"                 # rp2 port default AP address
_AP_PASSWORD_LEN = 8
_HTTP_PORT = 80
_HTTP_ACCEPT_TIMEOUT_S = 0.25
_HTTP_CLIENT_TIMEOUT_S = 3
_HTTP_MAX_HEADER_BYTES = 2048
_HTTP_MAX_BODY_BYTES = 1024
_SETUP_JOIN_TIMEOUT_S = 20
_SETUP_IDLE_TIMEOUT_MS = 10 * 60 * 1000   # leave setup mode automatically
```

`generate_ap_password()` - 8 chars from an unambiguous alphabet (no `0/O/1/l/I`) using `os.urandom`; generated once per setup session and shown on screen (decision 5). Pure enough to test given an injected byte source. `ap_ssid()` returns `_AP_SSID_PREFIX + last 4 hex chars of machine.unique_id()` (decision 5), wrapped in `try/except` falling back to `dexcom-pico-setup` if `unique_id` is unavailable.

`start_ap(ssid, password)` - `ap = network.WLAN(network.AP_IF)`; `ap.config(ssid=ssid, key=password)` with a fallback to `ap.config(essid=ssid, password=password)` on `TypeError`/`ValueError` (older rp2 builds); `ap.active(True)`; wait (watchdog-fed, max 5 s) for `ap.active()`. STA stays active for scanning; `wlan.disconnect()` is called first so the home network is released.

`stop_ap(ap)` - `ap.active(False)`, then `gc.collect()`.

#### 3.3 Scanning

`scan_networks() -> list[(ssid, rssi, secure)]` - `feed_watchdog()` immediately before and after `wlan.scan()` (the call is blocking C code that normally takes 1-3 s, well under the 8 s budget; this is the one place the watchdog cannot be fed mid-operation, so note it in CLAUDE.md). Wrapped in `try/except` returning `[]`. The list is post-processed by the pure `dedupe_networks(raw_scan)`: drop empty/hidden SSIDs, keep the strongest RSSI per SSID, sort by RSSI descending, cap at 20. Scanned once when setup starts and again on `GET /?rescan=1`.

#### 3.4 HTTP server (raw sockets, no libraries)

`serve_setup(ap_info, networks, status_msg) -> dict | None`:

1. `s = socket.socket(); s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1); s.bind(getaddrinfo("0.0.0.0", 80)[0][-1]); s.listen(2); s.settimeout(_HTTP_ACCEPT_TIMEOUT_S)`.
2. Loop: `feed_watchdog()`, `update_led()`, poll X via `HoldDetector` (`hold` -> cancel setup, return `None`; a short press is ignored here to avoid accidental cancels), redraw the setup screen at most once per second (it shows the password and the "waiting for phone" dots), check the idle timeout, then `conn, addr = s.accept()` inside `try/except OSError` (timeout -> continue).
3. Per connection (`try/finally conn.close()`): `conn.settimeout(_HTTP_CLIENT_TIMEOUT_S)`; read until `b"\r\n\r\n"` or `_HTTP_MAX_HEADER_BYTES` (feeding the watchdog between `recv(512)` calls); `parse_http_request(head: bytes) -> (method, path, query: dict, headers: dict)`; if `Content-Length` > `_HTTP_MAX_BODY_BYTES` respond 413; otherwise read the remaining body bytes (bounded); `parse_form(body) -> dict`.
4. Routing:
   - `GET` any path except `/favicon.ico` -> 200 with the page (`/favicon.ico` -> 404, empty body, so browsers do not get a full page twice). `rescan=1` in the query triggers `scan_networks()` first.
   - `POST /save` -> `validate_setup_form(form, settings) -> (settings_or_None, error_msg)`: SSID comes from `ssid` (select) unless `ssid_other` is non-empty; region must be valid; account id required non-empty. On error: 200 with the page re-rendered with the error and the entered non-secret fields. On success: 200 with a short "Saving, joining <ssid>... watch the Pico's screen" page, close the connection, close the listener, return the new settings dict.
   - Anything else -> 405.
5. Responses are `HTTP/1.0`, `Connection: close`, `Content-Type: text/html; charset=utf-8`, sent with `conn.write` in <= 512-byte chunks with `feed_watchdog()` between chunks.

Pure, tested helpers: `parse_http_request`, `parse_query`, `parse_form` (application/x-www-form-urlencoded: `+` -> space, `%XX` decoded to bytes then UTF-8-decoded with `errors` fallback so a bad sequence cannot raise), `html_escape`, `render_setup_page(networks, settings, error=None) -> str`, `validate_setup_form`.

#### 3.5 The page

Single small page (target < 4 KB, inline CSS of a few rules, no JS required):

- Heading "Dexcom Pico setup", optional red error line.
- Form `POST /save`: `<select name=ssid>` of scanned networks (label `SSID (-62 dBm)`; the currently saved SSID pre-selected), text `ssid_other` ("Other / hidden network"), password `wifi_password`, text `dexcom_account_id` (pre-filled), password `dexcom_password`, `<select name=dexcom_region>` (pre-selected), submit "Save and connect". A "Rescan networks" link to `/?rescan=1`.
- Passwords are never echoed back into the page. All dynamic strings go through `html_escape`.
- `<meta name=viewport content="width=device-width,initial-scale=1">` for phones.
- Use the plain hyphen `-` in any dashes in the page text (global frontend rule).

#### 3.6 Submit flow (`run_wifi_setup() -> bool`)

```
generate password -> start_ap -> scan -> loop:
    result = serve_setup(...)
    if result is None: stop_ap; wait_buttons_released; return False        (cancelled/idle)
    _settings.update(result); commit_settings()                            (write first, as agreed)
    stop_ap (close listener first)
    draw_status("Joining <ssid>", dots) ; cfg = connect_wifi(ssid, pw, timeout=_SETUP_JOIN_TIMEOUT_S)
    if not cfg:
        draw_status("Wi-Fi failed", "Reopening setup...") ; sleep 2 ; start_ap again ;
        status_msg = "Could not join '<ssid>' - check the password" ; loop
    draw_status("Wi-Fi connected", ip) ; sleep 1
    # Dexcom credential verification (decision 3)
    draw_status("Checking Dexcom login", dots) ; feed_watchdog()
    session = dexcom_login(region, account_id, password)
    if session:
        _session[0] = session                       (reuse it; no second login on the first poll)
        draw_status("Dexcom login OK", "Starting...") ; sleep 1.5 ; return True
    else:
        _session[0] = None
        draw_status("Dexcom login failed", "Reopening setup...") ; sleep 2
        wlan.disconnect() ; start_ap again ;
        status_msg = "Wi-Fi joined, but Dexcom login failed - check account id, password and region" ; loop
```

`connect_wifi` already cancels on any button press and feeds the watchdog; in this flow a cancel simply counts as a failed join. Between AP sessions `gc.collect()` is called to keep heap fragmentation down (the page string and scan list are the largest allocations in the app).

Dexcom verification details:

- Uses the existing `dexcom_login` (5 s request timeout, swallows exceptions, returns `None` on any failure), so it cannot hang or raise; the watchdog is fed immediately before the call. `None` covers wrong credentials, wrong region, DNS failure and a network without internet access alike; the on-page error text therefore names all three fields rather than diagnosing which one is wrong.
- The Wi-Fi credentials that just worked are kept in `_settings` (already persisted) when the AP is reopened, and the page pre-selects that SSID, so the user only needs to re-enter the Wi-Fi password and correct the Dexcom fields.
- The check runs once per submit; there is no retry loop, so a transient outage costs the user one resubmit.
- From the boot path (`ensure_wifi`), verification happens before NTP sync; `dexcom_login` does not depend on the RTC, so ordering is safe.

#### 3.7 Setup screen (`draw_setup_screen(ssid, password, phase)`)

Left column: "Wi-Fi setup", "1. Join Wi-Fi:", SSID, "Password:", password, "2. Open:", `http://192.168.4.1`, and a phase line ("Waiting for phone..." / "Saving..."). Footer: "Hold X to cancel". Right half: QR code if `qrcode` imported (`qrcode.QRCode()`, `set_text("WIFI:T:WPA;S:<ssid>;P:<pw>;;")`, `get_size()`, `get_module(x, y)` drawn as filled rectangles at the largest integer scale that fits ~110 px, on a white quiet zone). If the import failed or any QR call raises, the right half stays blank; the text instructions are always sufficient. The QR payload escaping (`\`, `;`, `,`, `:` prefixed with `\`) is a pure helper `wifi_qr_payload(ssid, password)` with a test.

### 4. Crash log

`/crash.json` shape: `{"crashes": int, "wdt_resets": int, "last_error": str, "last_uptime_s": int}`.

- `read_crash_log() -> dict` - tolerant (missing/corrupt -> zeros and empty string).
- `write_crash_log(exc, uptime_s)` - called in the `except` branch of the top-level handler **before** `draw_status("Error", ...)`, wrapped in its own `try/except` so logging can never mask the reboot. Exception text is `sys.print_exception(e, buf)` into a `uio.StringIO`, truncated to `_CRASH_TEXT_MAX`. Written directly (not via tmp+rename: this path must be short and it is acceptable for a crash log to be corrupt in the worst case; `read_crash_log` tolerates that).
- Watchdog resets leave no exception, so at the top of `main()` (before arming the WDT): `if machine.reset_cause() == machine.WDT_RESET: bump wdt_resets` (in `try/except`; `WDT_RESET` may not exist on some builds). This is the one write that happens at boot, and only after a watchdog reset.
- Wear: writes happen only on fatal errors and watchdog resets, never on normal boots or polls; LittleFS wear-levels across the 4 MB flash. Settings writes happen only on confirmed changes.
- Surface: "Device info" menu screen shows the counters and the first ~2 lines of `last_error`; the file can also be pulled with Thonny/`mpremote`.

### 5. Poll loop and boot changes (summary of edits in `main()`)

- Before arming the WDT: crash-log watchdog-reset bump.
- After arming: `_settings.update(load_settings()); _settings_saved.update(_settings); apply_settings()`.
- `ensure_wifi()`: empty SSID -> `run_wifi_setup()` loop until it returns True; during the countdown, X -> `run_menu(None)` (menu works before a reading exists; `last_state` may be `None`).
- Poll loop button handling per 2.1; after the menu closes, force a fetch on the next tick (`last_fetch = now - poll_ms`).
- Everything else (age computation, staleness gate, change detection, rejoin, LED precedence) unchanged apart from reading thresholds from `_settings`.

### 6. Tests (`tests/`)

- `tests/hw_stubs.py`: `install()` inserts fake modules into `sys.modules` **before** `main` is imported: `machine` (`WDT`, `reset`, `reset_cause`, `WDT_RESET`, `unique_id`), `network` (`WLAN` with `STA_IF`/`AP_IF`, no-op methods, `scan()` returning a canned list), `ntptime`, `urequests` (`post` raises), `ujson` (alias to `json`), `uio` (alias to `io`), `picographics` (`PicoGraphics` with `get_bounds() -> (320, 240)`, `create_pen`, `set_pen`, `clear`, `text`, `line`, `rectangle`, `update`, `set_font`, `set_thickness`, `measure_text`, `set_backlight`; `DISPLAY_PICO_DISPLAY_2`, `PEN_P8`), `pimoroni` (`Button` with `read`/`raw` scripted from a list, `RGBLED`), and a stub `secrets` module (this **must** be pre-seeded because CPython has a stdlib `secrets` module that would otherwise be imported). It then adds `src/` to `sys.path` and imports `main`. The `if __name__ == "__main__"` guard means importing runs nothing.
- `tests/test_settings.py`: defaults from secrets stub; validate coerces/clamps/snaps; unknown keys dropped; invalid region -> `ous`; `alert_low >= alert_high` resets the pair; corrupt/missing file -> defaults (using `tempfile` paths passed to `load_settings`/`save_settings`); save writes tmp then renames and produces a file `load_settings` reads back equal; `settings_changed` float tolerance; `step_value` bounds and float drift; `serialise_settings` includes exactly the schema keys.
- `tests/test_http.py`: `parse_http_request` for GET with query, POST with `Content-Length`, malformed request line, header case-insensitivity; `parse_form` for `+`, `%XX`, UTF-8 multibyte, bad percent sequences, repeated keys (last wins); `html_escape`; `render_setup_page` contains escaped SSIDs and never contains the password values; `validate_setup_form` for select vs other SSID, missing account id, invalid region; `dedupe_networks` ordering/dedupe/cap; `wifi_qr_payload` escaping.
- `tests/test_menu.py`: cursor wrap; select enters edit; up/down step; select commits; back discards; exit from editing discards and closes; action items set `action`; `HoldDetector` press vs hold timing (pure `now_ms` input); `format_setting`.
- Run with `.venv/bin/python3 -m unittest discover -s tests -v` (documented in CLAUDE.md and README). `py_compile` of `src/main.py` remains the on-device syntax check.
- MicroPython compatibility guard rails for the coder: no f-strings with `=` specifiers or nested quotes, no `str.format` on bytes, no `dict` comprehension with walrus, no `dataclasses`, no `typing` imports (type hints in docstrings only), `json.dumps` without `indent`/`separators`, `os.rename`/`os.remove`/`os.stat` only, `socket` (not `usocket`) with `getaddrinfo`, `uio.StringIO` via `try: import uio except ImportError: import io as uio`.

---

## Potential Side Effects for QA

1. **Button semantics changed on the main screen.** X no longer triggers a fetch; it opens the menu. A/B/Y still force a fetch. Verify no fetch happens on X press, and that a fetch happens promptly after the menu closes (the "Checking for updates" overlay is not shown for that automatic fetch).
2. **Held buttons.** Pimoroni `Button.read()` auto-repeats. Verify: holding X to exit the menu does not immediately reopen it; holding A/B while editing steps quickly but stops at the clamp; holding Y commits once.
3. **Menu hides the reading.** While the menu is open the reading is not visible and the poll loop is paused; the reading's age keeps accruing so on exit the staleness gate may fire immediately. The LED must keep flashing/solid while the menu is open (LED precedence unchanged). Confirm the idle timeout closes the menu.
4. **Watchdog.** Every new loop (menu, info screen, restart confirm, AP start wait, HTTP accept loop, per-connection recv/send, join wait, `wait_buttons_released`) feeds the watchdog; `wlan.scan()` is fed immediately before/after. QA should try: leaving the phone form open for minutes, submitting a huge body (413), connecting and sending nothing (client timeout 3 s), rapid reconnects, and confirm no watchdog reboot (check `wdt_resets` in Device info afterwards).
5. **Setup mode disconnects from the home network** (`wlan.disconnect()` before AP start). Cancelling setup from the menu path must reconnect via the existing `rejoin_wifi` in the poll loop (bounded 5 s); verify the reading returns without a reboot.
6. **Credential/region change from the menu clears the Dexcom session** so the next fetch re-logs in; a wrong region set from the menu still only manifests as staleness (the menu does not verify). The setup page path **does** verify: after a successful Wi-Fi join, one `dexcom_login` runs and a failure reopens the page with a "Dexcom login failed" error while keeping the working Wi-Fi credentials. QA: submit correct Wi-Fi + wrong Dexcom password, confirm the page reopens with that error and the SSID pre-selected; then correct it and confirm the first poll after setup uses the verified session (no second login request).
7. **Backlight preview**: cancelling an edit must restore the saved level; a saved 0.1 must still be readable (range floor is deliberately not 0).
8. **Thresholds now configurable.** With `alert_low`/`alert_high` changed, the red-value rule and LED flash rule must move together (both read `_settings`). `stale_minutes` changes must affect the `---` gate, the solid-red LED and the "Previous:" corner consistently (all three call `stale_limit_ms()`), in both the NTP and monotonic age paths.
9. **Persistence.** Power-cycle after changing a setting: value must survive. Corrupt `/settings.json` by hand: device must boot with defaults (and `secrets.py` credentials) rather than crash-loop. Delete `secrets.py` entirely: device must boot into setup mode, not error-reboot.
10. **`secrets.py` present + `settings.json` present**: settings file wins for every key (after a first save all keys are present). Editing `secrets.py` afterwards has no effect until `settings.json` is deleted, which the README must state.
11. **Phone behaviour.** Android/iOS will report "no internet" on the setup AP and may prompt to switch networks; the on-screen/README instructions must tell the user to stay connected. Both `http://192.168.4.1` and `http://192.168.4.1/anything` must serve the page. HTTPS will not work (document).
12. **Multiple submissions.** Submitting twice quickly: the second connection is refused/ignored because the listener is closed after the first successful save; the phone shows a connection error, which is acceptable.
13. **Fatal-error path** now does file I/O before the error screen; verify the reboot still happens within the watchdog period even if the write fails (the write is inside `try/except` and the watchdog is fed first).
14. **Memory.** The page string, scan list and QR object are the largest allocations; watch `gc.mem_free()` in Device info before/after a setup session for leaks (listener and connections must always be closed in `finally`).
15. **`rejoin_wifi` while the AP is active** cannot happen (the poll loop is not running during setup), but if a future change moves the server to an async model this assumption breaks; noted in CLAUDE.md.

---

## Assumptions

1. The resilience changes described in the task as "uncommitted" are already committed at HEAD (`2870244`); the working tree was clean. The plan builds on HEAD.
2. Extra menu items "LED alerts" (toggle), "Device info" and "Restart" are included because they are cheap, give the "toggle" example the design mentions, and provide the on-device way to read the crash log (decision 4). No "Reset settings to defaults" item; deleting `/settings.json` with Thonny/`mpremote` is the documented way to reset.
3. Numeric ranges/steps (backlight 0.1-1.0/0.1, stale 2-15 min/1, low 2.0-10.0/0.1, high 7.0-25.0/0.5) are my choices; they are constants in one schema table and trivially changed.
4. Settings are saved on each confirmed edit (Y), not batched on menu exit, so a power cut mid-session loses at most the item being edited.
5. On submit, settings are persisted **before** the join attempt (as stated in the agreed design), so a reboot during the join still keeps the new credentials even if they turn out to be wrong; the user can re-enter setup from the boot Wi-Fi-failed screen.
6. The setup AP password is random per setup session (8 chars, shown on screen and in the QR). The AP SSID is `dexcom-pico-XXXX` with a suffix derived from `machine.unique_id()` (decision 5).
7. No captive-portal DNS is implemented; the user types `http://192.168.4.1` (or scans the QR to join, then types the URL).
8. The setup page verifies the Dexcom credentials with a single `dexcom_login` after the Wi-Fi join (decision 3); a failure reopens the page with an error. The menu's region edit is not verified (it only clears the session).
9. A `tests/` directory is a new base folder; it is required by the task ("tests added accordingly") and uses stdlib `unittest` so no dependency changes are needed (decision 6).
10. CLAUDE.md's "> 5 min" wording is a doc drift (code and requirements say 6 min); it will be corrected to "6 min default, configurable" as part of the docs update.
11. The idle timeout for the menu is 60 s and for setup mode 10 min; both return to the glucose display (decision 2).
12. `secrets.example.py` stays as the template for people who prefer file-based configuration; it gains a comment that the setup page is the alternative. `secrets.py` is optional at runtime.
13. The crash log records exception text via `sys.print_exception` (MicroPython) so the traceback location is captured, truncated to 400 bytes.

## Open Questions

None outstanding. The seven questions raised in the first draft were answered by the user ("use proposed for all") and are recorded below; the design sections above already reflect them.

## Resolved Decisions

1. **Reaching setup at boot:** an empty saved SSID goes straight into setup mode; otherwise X during the "Wi-Fi failed / Retrying in Ns" countdown opens the settings menu (A/B/Y still retry immediately). No automatic AP mode after N failures.
2. **Timeouts:** the menu auto-closes after 60 s of inactivity; setup mode times out after 10 min without a request. Both return to whatever loop invoked them.
3. **Dexcom verification in setup:** after a successful Wi-Fi join, one `dexcom_login` attempt is made; the result is shown on screen and a failure reopens the setup page with a "Dexcom login failed" error (Wi-Fi credentials retained). A successful login's session id is kept for the first poll.
4. **Menu items:** "LED alerts" toggle, "Device info" and "Restart" are included as listed. No "Reset settings to defaults" item.
5. **Setup AP:** random 8-character per-session password shown on screen (and in the QR); SSID is `dexcom-pico-XXXX` with a suffix from `machine.unique_id()`.
6. **Tests:** stdlib `unittest`, no dependency changes.
7. **Dexcom region:** editable from both the menu (in-place choice, clears the session on commit) and the phone setup page (verified by the login attempt).

## Non-Obvious Side Effects

- **`secrets` name clash under CPython.** The stdlib has a `secrets` module; the test stub must pre-seed `sys.modules["secrets"]` before importing `main`, otherwise the app imports the wrong module and every credential default becomes empty. On the device there is no clash.
- **`any_button_pressed()` short-circuit.** Today, if A is pressed, B/X/Y are not `.read()` so their edge state is not updated; reading all four every tick changes the timing slightly (a press on two buttons in the same tick used to count once, now still counts once but both edges are consumed). Harmless, but the poll loop must read all four so X edges are never missed.
- **`Button.read()` state carries across loops.** The main loop, menu loop, setup loop and `connect_wifi` all call `.read()` on the same objects; a press consumed in one loop is invisible to the next. `wait_buttons_released()` on every transition is what keeps behaviour predictable.
- **Backlight applied at import.** `display.set_backlight(0.5)` still runs at import; `apply_settings()` overrides it a few hundred ms later inside `main()`. Not visible in practice, but do not move settings loading to import time to "fix" it: file I/O at import would run when tests import the module.
- **Region change invalidates the session.** `_session[0]` is tied to the region's base URL and app id; `commit_settings()` must clear it or the next fetch hits the wrong server with the old id and returns None until the silent re-login, which then uses the new region anyway. Clearing it just removes one failed request.
- **Age keeps accruing while the menu/setup is open.** No change to the staleness logic, but the first redraw after closing may flip to `---` with solid red. That is correct behaviour (the reading really is that old).
- **STA/AP coexistence.** The CYW43 driver allows STA active while the AP is up (needed for `wlan.scan()`), but the STA must be disconnected from the home network first, and after the AP is stopped the STA is reconnected explicitly via `connect_wifi`; do not rely on `rejoin_wifi` inside setup.
- **`wlan.scan()` blocks in C.** It is the only un-feedable blocking call; it normally completes in 1-3 s. If a future firmware makes it slower than ~7 s the watchdog would reboot the device during setup, which is why the scan is done once up front rather than per page load.
- **HTTP/1.0 with `Connection: close`** avoids keep-alive handling; browsers will open a new connection per request, which the 2-deep backlog and 250 ms accept timeout handle fine.
- **Percent-decoding must not raise.** Phone browsers send UTF-8 SSIDs percent-encoded; decode to bytes first, then `.decode("utf-8")` with a fallback, otherwise an emoji SSID would 500 the page.
- **Fatal handler ordering.** `write_crash_log` runs before `draw_status("Error")`; both are inside `try/except`, and `feed_watchdog()` precedes them, so the 3 s error pause plus file write still fits in the 8 s budget. Do not add anything slow to this path.
- **Deploy path.** The settings and crash files use absolute root paths regardless of where `main.py` lives; README deploy notes must say `settings.json` appears at `/` on the Pico.
- **Existing docs mention "secrets.py" in user-facing strings** (config error screen, README troubleshooting); all such references need updating so the on-device guidance matches the new flow.
