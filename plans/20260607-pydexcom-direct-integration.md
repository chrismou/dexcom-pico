# Technical Design Doc: pydexcom Direct Integration
**Date:** 2026-06-07  
**Branch:** feature/pydexcom  
**Author:** Lead System Architect

---

## 1. Overview

Replace the custom wrapper endpoint (`https://dexcom.mou.me/api/latest/egv`) with a
direct MicroPython implementation of the Dexcom Share HTTP API, porting the relevant
wire-level flow from the CPython `pydexcom` library. The Pico will authenticate with
Dexcom Share, maintain a session id, fetch the latest glucose reading, and render it
on the Pimoroni Display Pack 2.8 using the existing display/layout code unchanged.

---

## 2. Affected Files

| File | Change type | Summary |
|---|---|---|
| `src/main.py` | Modify | Replace `fetch_latest()` with Dexcom client functions; update `main()` state; update import from `secrets`; update `draw_reading()` for mmol/L-only flow; bridge trend string format |
| `src/secrets.py` | Modify (local, untracked) | Replace `API_URL`/`API_TOKEN` with `DEXCOM_ACCOUNT_ID`, `DEXCOM_PASSWORD`, `DEXCOM_REGION` |
| `src/secrets.example.py` | Modify | Mirror the new keys with safe placeholder values |
| `requirements.md` | Modify | Update behaviour spec to describe direct Dexcom authentication, mmol/L display, and new failure model |

No new files are created; no CPython dependencies are introduced. All logic lives in
`src/main.py`.

---

## 3. secrets.py Changes

### 3.1 src/secrets.example.py — new content

```python
WIFI_SSID = ""
WIFI_PASSWORD = ""

# Dexcom Share credentials
# DEXCOM_ACCOUNT_ID: your Dexcom Share account UUID (found in the Dexcom app)
DEXCOM_ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"
DEXCOM_PASSWORD = ""

# Region: "ous" (outside US, default), "us", or "jp"
DEXCOM_REGION = "ous"
```

### 3.2 src/secrets.py — coder should set (file is gitignored, never commit)

```python
WIFI_SSID = "<actual ssid>"
WIFI_PASSWORD = "<actual password>"

DEXCOM_ACCOUNT_ID = "<your-dexcom-account-uuid>"   # real value lives only in untracked secrets.py
DEXCOM_PASSWORD = ""   # user fills this in on the device

DEXCOM_REGION = "ous"
```

The password field is intentionally left blank for the user to fill before flashing.

---

## 4. src/main.py — Logic Changes

### 4.1 Import line

**Remove:**
```python
from secrets import WIFI_SSID, WIFI_PASSWORD, API_URL, API_TOKEN
```
**Replace with:**
```python
from secrets import WIFI_SSID, WIFI_PASSWORD, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD, DEXCOM_REGION
```

---

### 4.2 Dexcom Client Constants (add near top, after colour definitions)

```python
# ----- Dexcom Share API constants -----
_DEXCOM_BASE_URLS = {
    "us":  "https://share2.dexcom.com/ShareWebServices/Services/",
    "ous": "https://shareous1.dexcom.com/ShareWebServices/Services/",
    "jp":  "https://share.dexcom.jp/ShareWebServices/Services/",
}
_DEXCOM_APP_ID_DEFAULT = "d89443d2-327c-4a6f-89e5-496bbb0317db"
_DEXCOM_APP_ID_JP      = "d8665ade-9673-4e27-9ff6-92db4ce13d13"
_DEXCOM_NULL_SESSION   = "00000000-0000-0000-0000-000000000000"
_DEXCOM_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "dexcom-pico/1.0",
}
_MMOL_FACTOR = 0.0555

# Trend string (PascalCase from API) -> canonical lowercase for draw_trend()
_TREND_MAP = {
    "DoubleUp":        "doubleUp",
    "SingleUp":        "singleUp",
    "FortyFiveUp":     "fortyFiveUp",
    "Flat":            "flat",
    "FortyFiveDown":   "fortyFiveDown",
    "SingleDown":      "singleDown",
    "DoubleDown":      "doubleDown",
    # The following produce no arrow (draw_trend falls through to else: return)
    "None":            None,
    "NotComputable":   None,
    "RateOutOfRange":  None,
}
```

**Why a module-level dict rather than inline logic:** keeps the mapping easy to audit,
avoids repeated string comparisons, and is cheap on MicroPython.

---

### 4.3 Region / URL helper

```python
def _dexcom_base(region):
    return _DEXCOM_BASE_URLS.get(region, _DEXCOM_BASE_URLS["ous"])

def _dexcom_app_id(region):
    return _DEXCOM_APP_ID_JP if region == "jp" else _DEXCOM_APP_ID_DEFAULT
```

---

### 4.4 dexcom_login(region, account_id, password) -> session_id or None

**Endpoint:** `General/LoginPublisherAccountById`  
**Method:** POST  
**Body:** `{"accountId": account_id, "password": password, "applicationId": app_id}`  
**Response:** bare JSON string (the UUID, with surrounding quotes), e.g. `"\"3f2a...\""`.

```python
def dexcom_login(region, account_id, password):
    url = _dexcom_base(region) + "General/LoginPublisherAccountById"
    body = json.dumps({
        "accountId": account_id,
        "password": password,
        "applicationId": _dexcom_app_id(region),
    })
    try:
        resp = requests.post(url, data=body, headers=_DEXCOM_HEADERS)
        if resp is None or resp.status_code != 200:
            try: resp.close()
            except Exception: pass
            return None
        session_id = resp.json()   # ujson decodes the bare string literal
        try: resp.close()
        except Exception: pass
        if not session_id or session_id == _DEXCOM_NULL_SESSION:
            return None
        return session_id
    except Exception:
        return None
```

**Note on `resp.json()` for a bare string:** `ujson.loads('"uuid-here"')` returns the
Python string `"uuid-here"`, so `requests.resp.json()` will work directly.

---

### 4.5 dexcom_fetch_latest(region, session_id) -> raw list or None

**Endpoint:** `Publisher/ReadPublisherLatestGlucoseValues`  
**Method:** POST  
**Query params:** `sessionId`, `minutes=1440`, `maxCount=1`  
**Body:** `{}`

```python
def dexcom_fetch_latest(region, session_id):
    base = _dexcom_base(region)
    url = (base
           + "Publisher/ReadPublisherLatestGlucoseValues"
           + "?sessionId=" + session_id
           + "&minutes=1440"
           + "&maxCount=1")
    try:
        resp = requests.post(url, data="{}", headers=_DEXCOM_HEADERS)
        if resp is None or resp.status_code != 200:
            try: resp.close()
            except Exception: pass
            return None
        readings = resp.json()   # list of dicts
        try: resp.close()
        except Exception: pass
        return readings
    except Exception:
        return None
```

**Session-expired detection:** Dexcom returns HTTP 500 (or a non-200 status) when the
session_id is invalid. The caller (see §4.7) treats any non-200 from this call as a
potential session expiry and triggers one re-login.

---

### 4.6 parse_dexcom_reading(raw_list) -> reading dict or None

Accepts the raw JSON list from `dexcom_fetch_latest`. Returns a normalised reading
dict that the existing `draw_reading()` can consume, or `None` if the list is empty or
malformed.

```python
def parse_dexcom_reading(raw_list):
    if not raw_list:            # empty list -> no current reading
        return None
    try:
        r = raw_list[0]         # most-recent reading
        mg_dl = int(r["Value"])
        mmol  = round(mg_dl * _MMOL_FACTOR, 1)
        trend_raw = r.get("Trend", "")
        trend = _TREND_MAP.get(trend_raw, None)   # None -> no arrow
        # Timestamp: parse WT field  "Date(1587431782000-0400)"
        # Extract the leading integer (ms since epoch); ignore tz offset.
        wt_str = r.get("WT") or r.get("DT") or ""
        ts_ms = _parse_dexcom_timestamp(wt_str)
        return {
            "value":       mmol,
            "unit":        "mmol/L",
            "trend":       trend,
            "ts_ms":       ts_ms,   # epoch ms from reading (used for change detection)
        }
    except Exception:
        return None
```

---

### 4.7 _parse_dexcom_timestamp(wt_str) -> int or None

The `WT`/`DT`/`ST` fields contain strings of the form `Date(1587431782000-0400)`.
We only need the millisecond integer for change-detection (comparing successive
readings). The timezone offset is irrelevant because `received_ms` (used for "Updated
N mins ago") is set at the moment the Pico receives the data using
`time.ticks_ms()`, not from the reading's own timestamp.

```python
def _parse_dexcom_timestamp(wt_str):
    # wt_str e.g. "Date(1587431782000-0400)" or "Date(1587431782000+0000)"
    try:
        start = wt_str.index("(") + 1
        end   = wt_str.index(")")
        inner = wt_str[start:end]   # "1587431782000-0400" or "1587431782000+0000"
        # Split on sign after position 0 to handle negative-epoch edge cases
        for sep in ("+", "-"):
            idx = inner.find(sep, 1)   # skip potential leading '-' of epoch
            if idx != -1:
                return int(inner[:idx])
        return int(inner)             # no offset present
    except Exception:
        return None
```

**Design choice:** The extracted epoch-ms value (`ts_ms`) is used **only** as a change
identity token (replacing the old `data.get("id")`). Two consecutive calls that return
the same `ts_ms` are the same reading. The "Updated N mins ago" label still uses
`received_ms` (device ticks at fetch time), which is the same behaviour as before.

---

### 4.8 fetch_latest() — new implementation (replaces old function entirely)

This is the sole entry-point called from the main loop. It owns the session lifecycle:
one lazy login on startup, one transparent re-login on apparent session expiry.

The function operates on module-level mutable state for the session id because
MicroPython does not have convenient class closures on constrained RAM. Use a
single-element list as a mutable cell:

```python
_session = [None]   # _session[0] holds the current session id string or None
```

```python
def fetch_latest():
    """
    Returns a parsed reading dict {value, unit, trend, ts_ms} on success,
    or None on any failure (caller treats None as "API unreachable").
    """
    # --- Step 1: ensure we have a session ---
    if _session[0] is None:
        _session[0] = dexcom_login(DEXCOM_REGION, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD)
        if _session[0] is None:
            return None   # login failed; caller uses staleness logic

    # --- Step 2: fetch ---
    raw = dexcom_fetch_latest(DEXCOM_REGION, _session[0])

    # --- Step 3: session expiry recovery ---
    if raw is None:
        # Attempt one re-login; clear session so next call re-logs if this fails too
        _session[0] = None
        _session[0] = dexcom_login(DEXCOM_REGION, DEXCOM_ACCOUNT_ID, DEXCOM_PASSWORD)
        if _session[0] is None:
            return None   # re-auth failed; caller applies 5-min stale rule
        raw = dexcom_fetch_latest(DEXCOM_REGION, _session[0])
        if raw is None:
            return None

    # --- Step 4: parse ---
    return parse_dexcom_reading(raw)
```

**Behaviour contract:**
- Happy path: returns parsed reading dict.
- Session expired mid-poll: one silent re-login; last reading stays on screen during
  the re-auth HTTP round-trips (typically < 2 s); returns fresh reading or None.
- Login failure: returns None immediately; main loop applies the existing 5-minute
  stale rule.
- Empty reading list: `parse_dexcom_reading` returns None; treated as "API
  unreachable" by main loop.

---

### 4.9 main() state changes

**Remove:**
```python
last_id = None
```

**Replace with:**
```python
last_ts_ms = None   # epoch-ms of last displayed reading; used for change detection
```

**Remove from `last_state`:**
```python
"status": None,
```

**Updated `last_state` initial value:**
```python
last_state = {
    "value":       None,
    "unit":        "mmol/L",
    "trend":       None,
    "received_ms": None,
}
```

**Update reading ingestion block (inside `if data is not None:`):**

```python
data = fetch_latest()
if data is not None:
    ts = data.get("ts_ms")
    if ts != last_ts_ms:
        last_ts_ms = ts
        last_state = {
            "value":       data.get("value"),
            "unit":        "mmol/L",
            "trend":       data.get("trend"),
            "received_ms": time.ticks_ms(),
        }
    draw_reading(last_state)
else:
    # API unreachable path (unchanged from current code)
    ...
```

---

### 4.10 draw_reading() — HIGH/LOW status removal

The current `draw_reading()` contains a branch for `status == "high"` / `"low"`:

```python
# CURRENT (lines 199-204):
else:
    if status is not None:
        s = str(status).lower()
        if s == "high" or s == "low":
            show_text = status
        else:
            show_text = "---"
    else:
        show_text = "---"
```

The Dexcom Share `ReadPublisherLatestGlucoseValues` endpoint returns a numeric `Value`
field when a reading exists; there is no explicit HIGH/LOW status string in the raw
response. The Share API signals out-of-range by returning `Value = 400` (HIGH) or
`Value = 40` (LOW) with the threshold's sentinel value. Since we convert to mmol/L and
display the numeric value, these will naturally appear as `22.2` and `2.2`
respectively (and will be coloured RED by the existing `> 14 or < 4` rule).

**Remove the `status` key entirely from `last_state` and from `draw_reading()`:**

The simplified `draw_reading()` value-or-blank logic becomes:

```python
show_text = "---"
color = WHITE
if val is not None:
    show_text = str(val)
    try:
        numeric = float(val)
        if numeric > 14 or numeric < 4:
            color = RED
    except Exception:
        pass
```

Remove the `status` parameter read (`status = state.get("status")`) and the
`else: if status is not None:` branch entirely.

---

### 4.11 draw_trend() — no changes to arrow drawing

`draw_trend()` already handles lowercase canonical names and falls through to `return`
(no arrow) for anything not in its explicit list. The `_TREND_MAP` dict (§4.4) maps
PascalCase API strings to the matching lowercase keys already used by `draw_trend()`,
or to `None`. When `trend` is `None`, `draw_trend(None, ...)` reaches the `else:
return` branch — no arrow drawn. No changes to the drawing logic itself are needed.

The debug label `draw_bottom_right(trend, WHITE, scale=1)` at line 141 will display
`None` as the string `"None"` if trend is Python `None`. The coder should guard this:

```python
if trend is not None:
    draw_bottom_right(trend, WHITE, scale=1)
```

---

## 5. Exact Request Construction Reference

### 5.1 Login

```
POST https://shareous1.dexcom.com/ShareWebServices/Services/General/LoginPublisherAccountById
Headers:
  Accept: application/json
  Content-Type: application/json
  User-Agent: dexcom-pico/1.0
Body (JSON):
  {"accountId":"<uuid>","password":"<pw>","applicationId":"d89443d2-327c-4a6f-89e5-496bbb0317db"}
Response: 200 OK, body = "<session-uuid>"  (a bare JSON string with quotes)
```

### 5.2 Readings

```
POST https://shareous1.dexcom.com/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues?sessionId=<sid>&minutes=1440&maxCount=1
Headers: (same as login)
Body: {}
Response: 200 OK, body = [{...}]  or  []
```

### 5.3 URL construction rule

Base URL has a trailing slash. Endpoint path has NO leading slash. Concatenation:
`base_url + "General/LoginPublisherAccountById"` — no double-slash risk.

---

## 6. Data Flow Mapping

```
Raw Dexcom reading dict
  r["Value"]    int (mg/dL)
    -> mmol  = round(int(r["Value"]) * 0.0555, 1)      # e.g. 5.4
    -> stored as last_state["value"]                    # float
    -> show_text = str(mmol)                            # "5.4"
    -> colour: RED if mmol > 14 or mmol < 4, else WHITE

  r["Trend"]    PascalCase string
    -> _TREND_MAP[r["Trend"]]  -> lowercase or None     # e.g. "flat"
    -> stored as last_state["trend"]
    -> draw_trend(trend, ...)                           # None -> no arrow

  r["WT"]       "Date(ms±offset)"
    -> _parse_dexcom_timestamp(r["WT"]) -> int ms
    -> stored as last_state["ts_ms"] (change token only)
    -> "Updated N mins ago" uses device ticks (received_ms), NOT ts_ms
```

---

## 7. Failure Handling Matrix

| Scenario | Detection | Behaviour |
|---|---|---|
| No Wi-Fi at startup | `connect_wifi` returns None | `ensure_wifi()` loop — unchanged |
| Wi-Fi drops mid-run | `urequests.post` raises exception | `dexcom_fetch_latest` returns None; `fetch_latest` returns None; stale rule applies |
| Login fails (wrong password / network) | `dexcom_login` returns None | `fetch_latest` returns None; main loop applies 5-min stale rule |
| Session expired mid-poll | `dexcom_fetch_latest` returns None (non-200 from Dexcom) | One silent re-login attempt; last reading stays on screen; then: fresh reading OR None->stale rule |
| Re-login also fails | second `dexcom_login` returns None | `fetch_latest` returns None; stale rule; if > 5 min, blanks to `---` |
| Empty readings list from Dexcom | `raw_list == []` | `parse_dexcom_reading` returns None; treated as "API unreachable" |
| Malformed reading dict (missing key) | `int(r["Value"])` raises KeyError | `parse_dexcom_reading` catches Exception, returns None; stale rule |
| `WT` field absent or malformed | `_parse_dexcom_timestamp` returns None | `ts_ms = None`; change detection: `None != last_ts_ms` is True on first call, then `None == None` won't update unnecessarily — coder should handle: treat `ts_ms=None` as "always show latest" (always update state when ts_ms is None) |
| Network timeout (urequests hangs) | `urequests` raises OSError after TCP timeout | Exception caught in `dexcom_fetch_latest`/`dexcom_login`; returns None; stale rule |
| Value > 14 or < 4 (out-of-range) | `numeric > 14 or numeric < 4` in `draw_reading` | Display shows numeric mmol/L in RED — same rule as before |

**ts_ms=None special case:** if `_parse_dexcom_timestamp` cannot parse the field,
`ts_ms` will be `None`. The coder should implement the change-detection check as:

```python
if ts is None or ts != last_ts_ms:
    # treat as new reading to ensure display always updates
```

---

## 8. requirements.md Updates

The spec should be revised to reflect:

1. **Authentication:** The Pico now authenticates directly with the Dexcom Share API
   using `DEXCOM_ACCOUNT_ID`, `DEXCOM_PASSWORD`, and `DEXCOM_REGION` from `secrets.py`.
   No Bearer token or wrapper URL is needed.

2. **Units:** Only mmol/L is displayed (one decimal place). The previous `unit` field
   from the wrapper is gone; the unit is always `"mmol/L"`.

3. **Polling behaviour:** Unchanged — 30s interval, manual refresh on any button.

4. **HIGH/LOW status:** No longer a separate text state. Out-of-range values (Dexcom
   sentinel `Value=400` → `22.2 mmol/L`, `Value=40` → `2.2 mmol/L`) are shown as
   numeric values in RED.

5. **Trend values:** The trend identifiers are the same visual semantics; they now
   originate as PascalCase strings from the Dexcom API and are normalised internally.

6. **API unreachable / 5-min stale rule:** Behaviour identical to the existing spec
   (scenario 3), covering: login failure, re-auth failure, network timeout, empty
   readings list.

7. **secrets.py keys:** Remove `API_URL`, `API_TOKEN`; add `DEXCOM_ACCOUNT_ID`,
   `DEXCOM_PASSWORD`, `DEXCOM_REGION`.

---

## 9. Verification Approach

### 9.1 Static / off-device checks (can be done without hardware)

1. **Syntax / byte-compilation:** On the host `.venv`, run:
   ```bash
   python3 -m py_compile src/main.py
   ```
   MicroPython's parser is close enough to CPython 3.x for syntactic validity. This
   catches typos, indentation errors, missing colons.

2. **Import smoke test:** Confirm the new `secrets` import line matches the keys
   present in `src/secrets.example.py` — no key should be referenced in `main.py` that
   is not defined in the example.

3. **Trend map completeness review:** Manually verify that every key in `_TREND_MAP`
   maps either to a string that appears as a branch label in `draw_trend()` or to
   `None`. Cross-reference against the nine trend strings specified in the brief.

4. **URL construction review:** Print/trace the constructed URLs for login and readings
   for `region="ous"` and confirm no double-slash, no missing path segment, correct
   query-string format.

5. **Timestamp parser logic review:** Trace `_parse_dexcom_timestamp("Date(1587431782000-0400)")`,
   `_parse_dexcom_timestamp("Date(1587431782000+0000)")`, and
   `_parse_dexcom_timestamp("")` mentally or in a CPython REPL to confirm correct
   integer extraction and graceful fallback.

6. **mmol conversion spot-check:** Verify `round(97 * 0.0555, 1) == 5.4` and
   `round(400 * 0.0555, 1) == 22.2` in a Python REPL.

### 9.2 On-device checks (require Pico 2 W + Display Pack)

1. **Wi-Fi connect/retry:** Power on without Wi-Fi available; confirm retry prompt and
   button-press behaviour is unchanged.

2. **Initial login + first reading:** After Wi-Fi connects, confirm:
   - "Starting / Fetching latest..." status screen appears briefly.
   - A reading appears: mmol/L value in large text, `"mmol/L"` label, trend arrow,
     "Updated 0 mins ago" (or N mins based on fetch time).

3. **30s poll + "Updated N mins ago":** Leave running for 2–3 minutes; confirm the
   minutes counter increments and display refreshes without re-login.

4. **Manual refresh:** Press any button; confirm the overlay "Checking for updates"
   appears briefly and display refreshes.

5. **Session expiry simulation:** Not easily triggered in normal operation. Acceptable
   to test by temporarily patching `_DEXCOM_NULL_SESSION` as the initial session id to
   force re-auth on first fetch, then confirming the re-login path completes and a
   reading appears.

6. **Stale / API unreachable:** Disable Wi-Fi on the router; confirm the existing
   reading stays on screen for up to 5 minutes then transitions to `---`.

7. **Out-of-range colour:** Manually inject a mmol value of `3.5` or `15.0` into a
   test state dict and call `draw_reading()` directly; confirm RED text.

8. **No arrow for None/NotComputable/RateOutOfRange trend:** Inject a test reading with
   `Trend="NotComputable"` and confirm no arrow is drawn.

---

## 10. Side Effects for QA

- The `status` key is entirely removed from `last_state`. Any test fixture or display
  test code that passes a state dict with `"status"` will silently ignore it (since
  `draw_reading` no longer calls `state.get("status")`). This is safe but worth noting.
- `draw_trend()` receives `None` for unrenderable trends (was previously receiving a
  non-matching string like `"unknown"`). The `else: return` branch handles both — no
  behavioural difference, but the `draw_bottom_right(trend, ...)` guard (§4.11) is
  required to avoid rendering the string `"None"` on-screen.
- Change detection now uses `ts_ms` (an integer epoch-ms from the reading) instead of
  a string UUID `id` from the wrapper. The semantics are equivalent: same ts_ms = same
  reading. The special case where `ts_ms is None` should be treated as "always update"
  to avoid freezing the display if timestamp parsing ever fails.
- The `received_ms` staleness logic is unchanged in semantics — it still uses
  `time.ticks_ms()` at the moment of successful fetch. No change to the 5-minute
  stale threshold.
- `urequests` on MicroPython does not support connection timeout parameters in the same
  way as CPython `requests`. If Dexcom's servers are slow, the loop may block longer
  than 30s on a fetch. This is the same risk as before with the wrapper. No mitigation
  is planned (it was not mitigated before).
- The `_session` module-level mutable cell (`_session = [None]`) persists across calls
  within a single firmware run. A hardware reset or power cycle resets it — no
  persistent storage is used. This is intentional.
