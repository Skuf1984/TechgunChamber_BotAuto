"""Persistent GUI/runtime settings (settings.json), kept separate from the
calibration config.json so recalibration never clobbers user preferences."""

import json
import os

import paths

BASE_DIR = paths.DATA_DIR
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

DEFAULTS = {
    "language": "ru",
    "base_power": 3,
    "target_crafts": 0,          # 0 = unlimited
    "stop_on_fail": True,
    "hotkeys": {
        "toggle_control": "F8",
        "pause": "F9",
        "quit": "F10",
    },
    "stats": {
        "successes": 0,
        "failures": 0,
    },
}


def _deep_merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=SETTINGS_PATH):
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULTS))
    return _deep_merge(DEFAULTS, data)


def save(settings, path=SETTINGS_PATH):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
