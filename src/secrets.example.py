WIFI_SSID = ""
WIFI_PASSWORD = ""

# Dexcom Share credentials
# DEXCOM_ACCOUNT_ID: your Dexcom Share account UUID (found in the Dexcom app)
DEXCOM_ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"
DEXCOM_PASSWORD = ""

# Region: "ous" (Rest of the world, default), "us" (United States), or "jp" (Japan)
DEXCOM_REGION = "ous"

# NOTE: secrets.py is now optional. The on-device Wi-Fi setup page (hold X at boot
# after a Wi-Fi failure, or open the Settings menu and choose "Wi-Fi setup") lets
# you configure all credentials from a phone browser without editing this file.
# If both secrets.py and /settings.json exist, the saved settings file wins for
# every key. Editing secrets.py after a first save has no effect until /settings.json
# is deleted (e.g. with Thonny or mpremote).
