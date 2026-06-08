# Dexcom Monitor for Raspberry Pi Pico

A real-time Dexcom glucose monitor running on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8. The Pico authenticates directly with the Dexcom Share API to display current blood glucose readings and trend arrows without requiring a wrapper service.

## Hardware

- **Microcontroller**: Raspberry Pi Pico 2 W
- **Display**: Pimoroni Display Pack 2.8 (320x240, resistive touchscreen)
- **Connectivity**: Wi-Fi via Pico W's on-board module

## Features

- Direct authentication with Dexcom Share API (no wrapper service required)
- Real-time glucose readings displayed in mmol/L (one decimal place)
- Graphical trend arrows (up, down, flat, 45-degree angles, double arrows)
- Out-of-range values displayed in red (> 14 or < 4 mmol/L)
- Automatic session management with silent re-login on expiry
- Manual refresh via button press on display
- True sensor-age staleness detection: readings older than 6 minutes (based on sensor timestamp) display as `---` to highlight stale data at a glance
- Automatic NTP time sync at boot for accurate sensor-age calculation; graceful fallback to monotonic time if NTP unavailable
- Onboard RGB LED glucose alerts: flashes red when glucose is out of range (> 14 or < 4 mmol/L), shows solid red when data is stale (> 6 min old), off otherwise
- Configuration error screen if credentials are missing

## Required MicroPython Libraries

The following libraries must be installed on the Pico:

- `picographics` — Display rendering (Pimoroni)
- `pimoroni` — Button input handling (Pimoroni)
- `urequests` — HTTP requests for API communication
- `ujson` — JSON parsing

These can be installed via Thonny or uploaded manually to the Pico's filesystem.

## Setup

### 1. Clone and Prepare

Clone this repository and copy the secrets file template:

```bash
cp src/secrets.example.py src/secrets.py
```

### 2. Configure Credentials

Edit `src/secrets.py` and fill in:

- **`WIFI_SSID`** and **`WIFI_PASSWORD`**: Your Wi-Fi network credentials
- **`DEXCOM_ACCOUNT_ID`**: Your Dexcom Share account UUID (see below)
- **`DEXCOM_PASSWORD`**: Your Dexcom Share password
- **`DEXCOM_REGION`**: Region code:
  - `"ous"` — Outside US (default)
  - `"us"` — United States
  - `"jp"` — Japan

#### Finding Your Dexcom Account ID

Your account ID is a UUID visible in the Dexcom app or can be obtained from Dexcom support. It is typically a string like `00000000-0000-0000-0000-000000000000` (filled with your actual ID).

#### Enabling Dexcom Share

Ensure that:
- Dexcom Share is enabled on your account
- You have added at least one follower account (Dexcom's API requires this)

### 3. Flash the Pico

1. Connect your Pico 2 W to your computer in bootloader mode (hold BOOTSEL while plugging in)
2. Download the latest MicroPython `.uf2` firmware for the Pico from [micropython.org](https://micropython.org/download/rp2-pico2-w/)
3. Drag the `.uf2` file onto the Pico's mass-storage device
4. Once the Pico reboots, transfer the following files to it:
   - `src/main.py` → `/main.py` (or `/src/main.py` depending on your setup)
   - `src/secrets.py` → `/secrets.py` (or `/src/secrets.py`)
   - The Pimoroni library files (picographics, pimoroni, etc.)

You can use tools like:
- **Thonny IDE** — drag-and-drop files to the Pico's filesystem
- **ampy** (Adafruit MicroPython tool) — command-line file transfer
- **rshell** — interactive remote shell for the Pico

### 4. Run

Once the files are in place, reboot the Pico. The device will:
1. Attempt to connect to Wi-Fi
2. Sync time via NTP (to enable accurate sensor-age calculation)
3. Authenticate with Dexcom
4. Begin polling for readings every 30 seconds

**Note on NTP sync:** The Pico attempts to set its real-time clock from an NTP server at startup (3 attempts with 2-second gaps). This is used to compute the age of glucose readings based on their sensor timestamp. If NTP fails, the app continues in a fallback mode that uses monotonic time from when the reading was received. A brief "No NTP" message is shown on the display if synchronization fails; this is not fatal and does not prevent the app from running.

## On-Device Behavior

### Display States

#### 1. Glucose Reading Available

When a current reading is received:

- **Left half of screen**:
  - "Last reading N mins ago" — age of the glucose measurement based on the sensor's timestamp (not device-receive time)
  - Large bold value in mmol/L (white text, or red if > 14 or < 4)
  - Small "mmol/L" unit label
- **Right half**:
  - Trend arrow showing rate and direction of change:
    - ↑↑ = doubleUp (two upward arrows)
    - ↑ = singleUp (single upward arrow)
    - ↗ = fortyFiveUp (45-degree upward arrow)
    - → = flat (rightward arrow)
    - ↘ = fortyFiveDown (45-degree downward arrow)
    - ↓ = singleDown (single downward arrow)
    - ↓↓ = doubleDown (two downward arrows)
  - No arrow for unknown/unmeasurable trends

**Staleness indicator:** If the reading's sensor timestamp is older than 6 minutes, the glucose value is replaced with `---`. The "Last reading N mins ago" text remains visible, allowing you to see at a glance whether data is fresh or stale. This is particularly useful when Dexcom Share buffers readings — a buffered reading that is already old when received will be immediately shown as stale rather than appearing current.

#### 2. No Reading Available (Stale Data)

If the API is unreachable or returns no data, the Pico continues to display the last successful reading. However, if the last reading's sensor timestamp is older than 6 minutes, the glucose value is replaced with `---` while the "Last reading N mins ago" text remains visible. This gives you a clear visual indicator of data freshness.

#### 3. Configuration Error

If `DEXCOM_ACCOUNT_ID` or `DEXCOM_PASSWORD` is blank:

- A configuration error message appears on the screen
- The Pico will not attempt to fetch data until the secrets file is corrected and the Pico is rebooted

#### 4. Wi-Fi Connection

On startup, the Pico attempts to connect to Wi-Fi. If it fails:

- A retry prompt is displayed
- Press any button on the display to retry
- If connection times out after 20 seconds per attempt, the Pico stops execution

### Button Interactions

Press any of the four buttons on the display to trigger an immediate API refresh. A transient "Checking for updates" message briefly appears while fetching.

### Polling Interval

The API is polled every 30 seconds. Manual button presses bypass this interval.

## Troubleshooting

### "Wi-Fi failed" on Startup

- Verify SSID and password in `secrets.py` are correct
- Ensure the Wi-Fi network is within range
- Press a button to retry connection

### "Config error" Message

- Check that `DEXCOM_ACCOUNT_ID` and `DEXCOM_PASSWORD` are set (not empty) in `secrets.py`
- Reboot the Pico after correcting the file

### Blank Display or No Updates

- Verify Dexcom Share is enabled on your account and at least one follower is added
- Check that your Wi-Fi credentials are correct
- Ensure MicroPython libraries (picographics, pimoroni, urequests, ujson) are installed
- Press a button to manually trigger a refresh and check for error messages

### Session Expired

The Pico automatically handles Dexcom session expiry by attempting a silent re-login. If re-login fails, the staleness rule (6 minutes) applies. No manual action is needed.

## Development

This is a MicroPython project. To modify:

1. Edit `src/main.py`
2. Transfer the updated file to the Pico
3. Reboot or run manually via Thonny's REPL

The code uses a simple state machine:
- Wi-Fi connection with retry loop
- Session-based API authentication (lazy login on startup, re-login on expiry)
- Poll-or-stale display logic (30-second interval, 6-minute stale threshold)
- Button-driven manual refresh

## License

This project is provided as-is.
