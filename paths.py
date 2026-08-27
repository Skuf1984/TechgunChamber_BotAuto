"""Where the app keeps its data (config, settings, templates, logs).

When frozen by PyInstaller this resolves to the folder containing the .exe so the
user's calibration and stats persist next to it; in source it's the project dir."""

import os
import sys


def data_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = data_dir()
