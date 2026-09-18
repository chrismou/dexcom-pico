# Dexcom Monitor for Raspberry Pi Pico

A real-time Dexcom glucose monitor running on a Raspberry Pi Pico 2 W with a Pimoroni Display Pack 2.8. The Pico authenticates directly with the Dexcom Share API to display current blood glucose readings and trend arrows without requiring a wrapper service.

## Hardware

- **Microcontroller**: Raspberry Pi Pico 2 W
- **Display**: Pimoroni Display Pack 2.8 (320x240, resistive touchscreen)
- **Connectivity**: Wi-Fi via Pico W's on-board module

## Features

- Direct authentication with Dexcom Share API (no wrapper service required)
- Real-time glucose readings displayed in mmol/L or mg/dL (user-selectable)
- Graphical trend arrows (up, down, flat, 45-degree angles, double arrows)
- Configurable out-of-range alert thresholds with unit-aware ranges (mmol/L: low 4.0, high 14.0; mg/dL: low 70, high 180) displayed in red
- Automatic session management with silent re-login on expiry
- Manual refresh via A/B/Y button press
- Staleness detection: readings older than 6 minutes display as `---`
- Automatic NTP time sync at boot for accurate sensor-age calculation; fallback to monotonic time if NTP unavailable
- Onboard RGB LED glucose alerts: flashes red when out of range, solid red when data is stale, off otherwise
- On-device settings menu (X button) with configurable units, thresholds, backlight, region, and LED alerts
- Phone-based Wi-Fi and Dexcom setup via a built-in access point and browser form
- Persistent settings in `/settings.json`; `secrets.py` is now optional
- Crash log at `/crash.json` viewable from the Settings > Device info screen

## Required MicroPython Libraries

The following libraries must be installed on the Pico:

- `picographics` - Display rendering (Pimoroni)
- `pimoroni` - Button input handling (Pimoroni)
- `urequests` - HTTP requests for API communication
- `ujson` - JSON parsing

Optional:

- `qrcode` - Wi-Fi QR code display on the setup screen (Pimoroni)

These can be installed via Thonny or uploaded manually to the Pico's filesystem.

## Setup

### Option A: Phone-based setup (recommended)

1. Flash the Pico with the latest Pimoroni MicroPython firmware and upload `src/main.py`.
2. Reboot. With no saved credentials the device immediately enters Wi-Fi setup mode.
3. On your phone, join the `dexcom-pico-XXXX` Wi-Fi network shown on the Pico's screen using the displayed password.
4. Open `http://192.168.4.1` in your browser (HTTPS is not supported).
5. Select your home Wi-Fi network, enter the password and your Dexcom credentials, then tap **Save and connect**.
6. The Pico joins your network, verifies the Dexcom login, and begins polling.

**Note:** Your phone may show a "no internet" warning when connected to the Pico's AP - stay connected and open the URL manually.

### Option B: Manual file setup

1. Clone this repository and copy the secrets file template:

```bash
cp src/secrets.example.py src/secrets.py
```

2. Edit `src/secrets.py` and fill in:

- **`WIFI_SSID`** and **`WIFI_PASSWORD`**: Your Wi-Fi network credentials
- **`DEXCOM_ACCOUNT_ID`**: Your Dexcom Share account UUID
- **`DEXCOM_PASSWORD`**: Your Dexcom Share password
- **`DEXCOM_REGION`**: Region code (`"ous"` Rest of the world, `"us"` United States, `"jp"` Japan)

3. Transfer `src/main.py` and `src/secrets.py` to the Pico and reboot.

#### Finding Your Dexcom Account ID

Your account ID is a UUID visible in the Dexcom app. It looks like `00000000-0000-0000-0000-000000000000`.

#### Enabling Dexcom Share

- Dexcom Share must be enabled on your account
- You must have added at least one follower account

### Flashing the Pico

1. Connect your Pico 2 W in bootloader mode (hold BOOTSEL while plugging in)
2. Download the latest Pimoroni MicroPython `.uf2` for the Pico 2 W from [github.com/pimoroni/pimoroni-pico](https://github.com/pimoroni/pimoroni-pico/releases)
3. Drag the `.uf2` onto the Pico's mass-storage device
4. Transfer `src/main.py` (and optionally `src/secrets.py`) to the Pico's root filesystem

You can use:
- **Thonny IDE** - drag-and-drop files to the Pico's filesystem
- **ampy** - `ampy --port /dev/ttyACM0 put src/main.py /main.py`
- **rshell** - interactive remote shell
- **mpremote** - `mpremote cp src/main.py :/main.py`

**Note:** Settings are stored at `/settings.json` and the crash log at `/crash.json` on the Pico's root filesystem, regardless of where `main.py` lives.

## On-Device Behavior

### Display States

#### Glucose Reading Available

- **Left half**: "Last reading N mins ago", large bold value in the selected unit (red if out of range), unit label
- **Right half**: Trend arrow (up/down/flat/diagonal, double arrows for rapid trends)

**Staleness**: When the sensor timestamp exceeds 6 minutes, the value is replaced with `---` and the bottom-right shows "Previous: X.X" (or "Previous: NNN" in mg/dL).

#### Wi-Fi Failed

Shows "Wi-Fi failed" with a "Retrying in Ns" countdown. During the countdown:
- **X**: Open settings menu (where Wi-Fi setup can be started)
- **A / B / Y**: Retry immediately

#### Configuration Error

If Dexcom account id or password is blank, a "Config error" screen appears with the instruction to open the settings menu.

### Settings Menu (X button)

Press X on the main screen to open the settings menu:

| Item | Description |
|---|---|
| Backlight | Display brightness 10-100% |
| Units | mmol/L or mg/dL (switching resets alert thresholds) |
| Alert low | Low threshold (range depends on current unit) |
| Alert high | High threshold (range depends on current unit) |
| Region | Rest of the world / United States / Japan |
| LED alerts | On / Off |
| Wi-Fi setup | Launch the phone setup page |
| Device info | Shows SSID, IP, RSSI, memory, crash counts |
| Restart | Reboot with confirmation |

**Controls in the menu**: A = up, B = down, Y = select/confirm, X = back, hold X = exit. The menu closes automatically after 60 seconds of inactivity.

### Wi-Fi Setup Mode

Accessible from the menu ("Wi-Fi setup") or automatically on boot when no SSID is configured.

The Pico raises a WPA2 access point:
- **SSID**: `dexcom-pico-XXXX` (where XXXX is derived from the device's unique ID)
- **Password**: Randomly generated 8-character password, shown on screen
- **URL**: `http://192.168.4.1`
- A Wi-Fi QR code is shown on the right side of the screen if the `qrcode` library is installed

On the form, select your network (or enter a hidden SSID), enter the Wi-Fi password, your Dexcom account id, Dexcom password, and region. Submit to save. The Pico restarts, joins the network and checks the Dexcom login; if either fails, the setup page reopens with the reason and your Wi-Fi credentials are retained. Hold X to cancel, which also restarts the Pico. The restart is deliberate: once the setup access point has been up, only a full reset restores normal internet access on the Pico's Wi-Fi chip.

Setup mode times out after 10 minutes without a browser request.

### Button Interactions

| Button | Main screen | Settings menu |
|---|---|---|
| A (top-left) | Trigger immediate fetch | Move up / increase value |
| B (bottom-left) | Trigger immediate fetch | Move down / decrease value |
| X (top-right) | Open settings menu | Back / hold to exit |
| Y (bottom-right) | Trigger immediate fetch | Select / confirm |

### Polling Interval

The API is polled every 30 seconds. A/B/Y bypass this interval.

## Persistence and secrets.py

Settings are saved to `/settings.json` on the Pico's flash after each confirmed change in the menu or after a successful setup. If `/settings.json` exists and contains a key, that value takes precedence over `secrets.py`. After a first save all keys are present in the file, so editing `secrets.py` afterwards has no effect until `/settings.json` is deleted.

To reset to defaults, delete `/settings.json` using Thonny or mpremote.

## Troubleshooting

### "Wi-Fi failed" on Startup

- If no SSID is saved: the device enters Wi-Fi setup mode automatically
- Otherwise: check that your SSID and password are correct; the Pico retries every 5 seconds
- Press X during the countdown to open the settings menu and use "Wi-Fi setup" to reconfigure

### "Config error" Message

- Press X to open the settings menu and choose "Wi-Fi setup" to enter credentials via phone browser
- Or edit `src/secrets.py`, upload it to the Pico, and reboot

### Blank Display or No Updates

- Verify Dexcom Share is enabled and at least one follower is added
- Check your Wi-Fi and Dexcom credentials via the settings menu > Wi-Fi setup
- Ensure MicroPython libraries (picographics, pimoroni, urequests, ujson) are installed
- Press A/B/Y to manually trigger a refresh

### Session Expired

The Pico handles Dexcom session expiry automatically with a silent re-login.

### Checking the Crash Log

Open Settings menu > Device info to see crash and watchdog reset counts and the last error. To read the full `/crash.json` file, use Thonny or `mpremote cat /crash.json`.

## Development

### Local Testing

A CPython test suite runs without hardware:

```bash
.venv/bin/python3 -m unittest discover -s tests -v
```

### Syntax Check

```bash
.venv/bin/python3 -m py_compile src/main.py
```

The `.venv` is CPython 3.10; do not add CPython-only dependencies expecting them to exist on the Pico.

### Modifying

1. Edit `src/main.py`
2. Run the syntax check and tests locally
3. Transfer the file to the Pico and reboot

The app is a single-file MicroPython state machine:
- Wi-Fi connection with retry loop (or Wi-Fi setup on first boot)
- Settings loaded from `/settings.json` (or `secrets.py` defaults)
- NTP sync -> poll loop (30-second interval, manual refresh on A/B/Y)
- Settings menu on X
- 8-second hardware watchdog throughout

## License

Released under the [MIT License](LICENSE).
