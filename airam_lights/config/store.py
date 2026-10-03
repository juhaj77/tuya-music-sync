"""JSON persistence for AppConfig.

Config lives in the user's own settings folder by default (not inside the
repo) - AppData on Windows, ~/.config on Linux, see default_config_dir() -
so it survives updates and keeps local Tuya keys out of the project
directory. The file is plain, indented JSON - human-readable and editable
by hand if needed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .schema import AppConfig

logger = logging.getLogger("airam_lights.config")

APP_DIR_NAME = "AiramMusicLights"


def default_config_dir() -> Path:
    """The app's settings folder (config.json, logs, optionally tinytuya.json):
    %APPDATA%\\AiramMusicLights on Windows; elsewhere the XDG location,
    $XDG_CONFIG_HOME/AiramMusicLights - normally ~/.config/AiramMusicLights."""
    if sys.platform != "win32":
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / APP_DIR_NAME
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / "config.json"


class ConfigStore:
    """Loads/saves AppConfig to disk, with a defensive backup-on-write.

    Never logs local_key or other secret values - only device names/ids/ips.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or default_config_path()

    def load(self) -> AppConfig:
        if not self.path.exists():
            logger.info("No config file at %s, starting with defaults", self.path)
            cfg = AppConfig.with_defaults()
            self.save(cfg)
            return cfg
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg = AppConfig.from_dict(raw)
            logger.info(
                "Loaded config from %s (%d devices, %d groups)",
                self.path,
                len(cfg.devices),
                len(cfg.groups),
            )
            return cfg
        except Exception:
            logger.exception("Failed to load config from %s, falling back to defaults", self.path)
            backup = self.path.with_suffix(".broken.json")
            try:
                shutil.copy2(self.path, backup)
                logger.warning("Backed up unreadable config to %s", backup)
            except OSError:
                pass
            return AppConfig.with_defaults()

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
                f.write("\n")
            # Atomic-ish replace so a crash mid-write never corrupts the real file.
            os.replace(tmp_path, self.path)
            logger.debug("Saved config to %s", self.path)
        except Exception:
            logger.exception("Failed to save config to %s", self.path)
            raise
