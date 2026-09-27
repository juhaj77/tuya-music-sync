"""LAN discovery of Tuya devices via their UDP broadcast (ports 6666/6667/6668).

This finds IP + device id + protocol version for devices already paired to
your Wi-Fi network. It does NOT return the local_key - Tuya deliberately does
not broadcast that on the LAN. Local keys must be obtained once via the cloud
setup wizard (tools/setup_wizard.py) or entered manually if you already have
them from another tool.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("airam_lights.lamps")


@dataclass
class DiscoveredDevice:
    ip: str
    device_id: str
    version: str
    product_key: Optional[str] = None


def scan_network(timeout: float = 8.0) -> List[DiscoveredDevice]:
    """Best-effort LAN scan. Returns [] (and logs why) on any failure instead
    of raising, so a UI "Scan" button never crashes the app."""
    try:
        import tinytuya
    except ImportError:
        logger.error("tinytuya is not installed - cannot scan for devices")
        return []

    logger.info("Scanning LAN for Tuya devices (UDP broadcast, timeout=%.0fs)...", timeout)
    found = None
    try:
        # tinytuya's deviceScan signature has changed across versions; try the
        # common ones defensively rather than pinning to one exact signature.
        found = tinytuya.deviceScan(False, timeout)
    except TypeError:
        try:
            found = tinytuya.deviceScan(verbose=False)
        except Exception:
            logger.exception("LAN scan failed")
            return []
    except Exception:
        logger.exception("LAN scan failed")
        return []

    results: List[DiscoveredDevice] = []
    if isinstance(found, dict):
        for ip, info in found.items():
            if not isinstance(info, dict):
                continue
            dev_id = info.get("gwId") or info.get("id") or info.get("uuid") or ""
            version = str(info.get("version") or info.get("ver") or "3.3")
            results.append(
                DiscoveredDevice(
                    ip=ip,
                    device_id=dev_id,
                    version=version,
                    product_key=info.get("productKey"),
                )
            )
    logger.info("Scan found %d candidate device(s) on the LAN", len(results))
    return results


def find_device_address(device_id: str) -> Optional[DiscoveredDevice]:
    """Blocking (up to ~tinytuya's scan time): listens for one specific
    device's LAN broadcast and returns where it currently is, or None. Used
    to follow a lamp whose IP address changed (DHCP)."""
    try:
        import tinytuya

        info = tinytuya.find_device(dev_id=device_id)
    except Exception:
        logger.exception("Looking for device %s on the LAN failed", device_id)
        return None
    if not isinstance(info, dict) or not info.get("ip"):
        return None
    return DiscoveredDevice(ip=info["ip"], device_id=device_id, version=str(info.get("version") or "3.3"))
