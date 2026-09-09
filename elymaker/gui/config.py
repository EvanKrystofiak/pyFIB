"""Small persisted config for elymaker (JSON under the user's home dir).

Currently holds the SmartFIB "drop" location: the file path SmartFIB loads a
pattern from. "Send to SmartFIB" writes the current pattern there so SmartFIB
(or pyFIB) can load and mill it -- elymaker never triggers milling itself.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".elymaker")
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")

_DEFAULTS: Dict[str, Any] = {
    "smartfib_drop_path": "",   # e.g. r"D:\SmartFIB\ApiLayout.ely" or a share
}


def config_path() -> str:
    return _CONFIG_PATH


def load_config() -> Dict[str, Any]:
    cfg = dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_CONFIG_PATH, "w") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


def get(key: str, default: Any = None) -> Any:
    return load_config().get(key, default if default is not None else _DEFAULTS.get(key))


def set(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
