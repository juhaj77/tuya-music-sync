"""Fetch lamps' current local_key values from the Tuya cloud.

A bulb's local_key changes whenever it is re-paired (reset and added again
in Smart Life / Airam SmartHome). The app then can't talk to it locally
any more, while the phone app keeps working through the cloud. The one-time
setup wizard already saved Tuya IoT Platform API credentials in
`tinytuya.json`; this reuses them to look up the fresh key per device.

Deliberately queries each device individually (`/v1.0/devices/{id}`) rather
than tinytuya's `getdevices()`: the latter resolves the account through the
`apiDeviceID` stored at wizard time, and fails with "permission deny" as soon
as that particular device is no longer on the account - while per-device
lookups keep working.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..config.store import default_config_dir

logger = logging.getLogger("airam_lights.lamps")

CREDENTIALS_FILE = "tinytuya.json"


def credential_paths() -> List[Path]:
    """Where the wizard's tinytuya.json may be: the working directory (the
    wizard writes it there), next to a packaged exe, the source checkout,
    and the app's own settings folder."""
    paths = [Path.cwd() / CREDENTIALS_FILE]
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / CREDENTIALS_FILE)
    paths.append(Path(__file__).resolve().parents[2] / CREDENTIALS_FILE)
    paths.append(default_config_dir() / CREDENTIALS_FILE)
    return paths


def load_credentials() -> Optional[dict]:
    for path in credential_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("apiKey") and data.get("apiSecret") and data.get("apiRegion"):
            return data
    return None


def fetch_local_keys(device_ids: Iterable[str]) -> Dict[str, str]:
    """{device_id: local_key} for every id the cloud knows. Raises
    RuntimeError with a readable reason if the cloud can't be used at all."""
    creds = load_credentials()
    if creds is None:
        raise RuntimeError(
            f"no Tuya cloud credentials found ({CREDENTIALS_FILE} from the setup wizard) - run the setup "
            "wizard once, or copy tinytuya.json next to the app"
        )
    try:
        import tinytuya
    except ImportError as e:
        raise RuntimeError("tinytuya is not installed") from e

    cloud = tinytuya.Cloud(apiRegion=creds["apiRegion"], apiKey=creds["apiKey"], apiSecret=creds["apiSecret"])
    if getattr(cloud, "error", None):
        raise RuntimeError(f"Tuya cloud login failed: {cloud.error}")

    keys: Dict[str, str] = {}
    for device_id in device_ids:
        try:
            response = cloud.cloudrequest(f"/v1.0/devices/{device_id}")
        except Exception as e:  # network errors etc. - skip this device, keep the rest
            logger.warning("Tuya cloud lookup failed for %s: %s", device_id, e)
            continue
        result = response.get("result") if isinstance(response, dict) else None
        key = result.get("local_key") if isinstance(result, dict) else None
        if key:
            keys[device_id] = key
        else:
            msg = response.get("msg") if isinstance(response, dict) else response
            logger.warning("Tuya cloud returned no local_key for %s: %s", device_id, msg)
    return keys
