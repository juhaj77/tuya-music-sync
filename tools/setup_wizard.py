"""One-time setup helper: obtain each bulb's local_key from the Tuya cloud
via tinytuya's built-in wizard, then import the results into this app's
config file (%APPDATA%\\AiramMusicLights\\config.json on Windows,
~/.config/AiramMusicLights/config.json on Linux).

The local_key is required for local LAN control but is deliberately not
broadcast on the network for security reasons - Tuya's own tooling always
gets it via one authenticated cloud API call, done once here, offline from
the actual music visualizer. After this step, no cloud access is needed to
run the app.

Prerequisites (see README.md "Obtaining local keys" for the full walkthrough):
  1. A free Tuya IoT Platform developer account: https://iot.tuya.com
  2. A Cloud Development project there, with the "IoT Core" and
     "Authorization" APIs subscribed.
  3. Your Airam SmartHome app account linked to that project via
     Cloud -> Devices -> "Link Tuya App Account" (scan the QR code with the
     Airam SmartHome app). This works because Airam SmartHome, like many
     white-label smart-home apps, runs on Tuya's shared cloud platform - see
     DEVICE_NOTES.md for what is confirmed vs. assumed about this step.

This script just launches `python -m tinytuya wizard`, which is interactive
and will prompt for your Access ID / Access Secret / region / account UID.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airam_lights.config.schema import DeviceConfig  # noqa: E402
from airam_lights.config.store import ConfigStore  # noqa: E402


def main() -> None:
    print(__doc__)
    input("Press Enter to launch the tinytuya setup wizard (Ctrl+C to cancel)...")

    if getattr(sys, "frozen", False):
        # Packaged .exe build: there's no separate Python interpreter to run
        # `-m tinytuya` with (sys.executable is this exe itself), so run the
        # wizard in-process instead.
        from tinytuya import wizard
        try:
            wizard.wizard()
        except Exception as e:
            print(f"\nWizard failed: {e}")
            return
    else:
        result = subprocess.run([sys.executable, "-m", "tinytuya", "wizard"])
        if result.returncode != 0:
            print("\nWizard exited with a non-zero status - see the output above for details.")
            return

    devices_json = Path("devices.json")
    if not devices_json.exists():
        print("\ndevices.json was not created by the wizard - nothing to import.")
        return

    with open(devices_json, "r", encoding="utf-8") as f:
        raw_devices = json.load(f)

    store = ConfigStore()
    config = store.load()
    by_id = {d.id: d for d in config.devices}
    added = 0
    updated = 0
    for entry in raw_devices:
        dev_id = entry.get("id") or entry.get("uuid")
        if not dev_id:
            continue
        # tinytuya's devices.json uses the key "version" (not "ver") for the
        # local protocol version it detected during its own local poll step -
        # this was previously read as "ver" here, which silently defaulted
        # every device to "3.3" even when the bulb actually speaks 3.4,
        # breaking local decryption ("Unexpected Payload from Device").
        version = str(entry.get("version") or entry.get("ver") or "3.3")
        ip = entry.get("ip", "")
        local_key = entry.get("key", "")
        name = entry.get("name", dev_id)

        existing = by_id.get(dev_id)
        if existing is None:
            new_dev = DeviceConfig(id=dev_id, name=name, ip=ip, local_key=local_key, version=version)
            config.devices.append(new_dev)
            by_id[dev_id] = new_dev
            added += 1
        else:
            # Upsert: always refresh ip/local_key/version from the latest wizard
            # run (IP can change via DHCP, local_key changes on re-pairing, and
            # the version may only become known after this run's local poll).
            changed = (existing.ip, existing.local_key, existing.version) != (ip, local_key, version)
            existing.ip = ip or existing.ip
            existing.local_key = local_key or existing.local_key
            existing.version = version
            if changed:
                updated += 1

    store.save(config)
    print(f"\nAdded {added} new device(s), updated {updated} existing device(s) in {store.path}")
    print("Local keys are now stored only in that config file (never printed or logged).")
    print("Open the app and check the Devices tab - rename lamps, select which participate, and Test Connection.")


if __name__ == "__main__":
    main()
    if getattr(sys, "frozen", False):
        # Double-clicked .exe: keep the console window open so the result can be read.
        input("\nPress Enter to close...")
