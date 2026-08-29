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


def _int_in(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if lo <= value <= hi else default


def _sanitize(settings):
    """Clamp the editable numbers to sane ranges so a hand-edited or corrupted
    settings.json can never crash the bot on start."""
    settings["base_power"] = _int_in(settings.get("base_power"), DEFAULTS["base_power"], 0, 10)
    settings["target_crafts"] = _int_in(settings.get("target_crafts"), DEFAULTS["target_crafts"], 0, 10**6)
    stats = settings.get("stats") or {}
    settings["stats"] = {
        "successes": _int_in(stats.get("successes"), 0, 0, 10**9),
        "failures": _int_in(stats.get("failures"), 0, 0, 10**9),
    }
    return settings


def load(path=SETTINGS_PATH):
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULTS))
    return _sanitize(_deep_merge(DEFAULTS, data))


def save(settings, path=SETTINGS_PATH):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
