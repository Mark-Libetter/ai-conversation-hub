from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "AIConversationHub"


def resource_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        return Path(bundled).resolve()
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    override = os.environ.get("CONVERSATION_HUB_DATA_DIR", "").strip()
    if override:
        target = Path(override).expanduser()
    elif getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            target = Path.home() / "Library" / "Application Support" / APP_NAME / "UserData"
        else:
            local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
            target = local_app_data / APP_NAME / "UserData"
    else:
        # Keep the existing source/stable-copy workflow backward compatible.
        target = resource_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir()
STATIC_DIR = RESOURCE_DIR / "static"
CONFIG_PATH = DATA_DIR / "sources.json"
NOTES_DB = DATA_DIR / "hub_notes.sqlite"
