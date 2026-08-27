"""Thread-safe wrapper that runs the Watchdog in a background thread and feeds a
GUI-friendly event queue. The GUI polls the queue; it never touches the bot thread."""

import logging
import queue
import threading

import chamber_bot
import settings as settings_mod


class _QueueLogHandler(logging.Handler):
    """Forwards bot log records into the engine event queue for the GUI."""

    def __init__(self, emit):
        super().__init__()
        self._emit = emit

    def emit(self, record):
        try:
            self._emit("log", {"level": record.levelname, "message": self.format(record)})
        except Exception:  # noqa: BLE001
            pass


class BotEngine:
    def __init__(self):
        self.events = queue.Queue()
        self.watchdog = None
        self.thread = None
        self.settings = settings_mod.load()
        self.last_error = None
        self._log_handler = None
        self._start_baseline = {"successes": 0, "failures": 0}

    def session_stats(self):
        """Crafts completed since Start was pressed (as opposed to the lifetime total)."""
        if self.watchdog is None:
            return {"successes": 0, "failures": 0}
        return {
            "successes": self.watchdog.successes - self._start_baseline["successes"],
            "failures": self.watchdog.failures - self._start_baseline["failures"],
        }

    # -- lifecycle -----------------------------------------------------------
    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, control_enabled):
        if self.is_running():
            return False
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
        except SystemExit as exc:
            self.events.put(("error", {"message": str(exc)}))
            return False

        s = self.settings
        cfg["power"]["default_level"] = int(s["base_power"])

        try:
            watchdog = chamber_bot.Watchdog(
                cfg,
                control_enabled,
                csv_enabled=True,
                hotkeys=s["hotkeys"],
                target_crafts=int(s["target_crafts"]),
                stop_on_fail=bool(s["stop_on_fail"]),
                emit=self._emit,
            )
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", {"message": str(exc)}))
            return False

        watchdog.successes = int(s["stats"]["successes"])
        watchdog.failures = int(s["stats"]["failures"])
        self._start_baseline = {"successes": watchdog.successes, "failures": watchdog.failures}
        self.watchdog = watchdog

        self._log_handler = _QueueLogHandler(self._emit)
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        chamber_bot.log.addHandler(self._log_handler)

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _run(self):
        try:
            self.watchdog.run()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.events.put(("error", {"message": str(exc)}))
        finally:
            try:
                self.watchdog.close()
            except Exception:  # noqa: BLE001
                pass
            if getattr(self, "_log_handler", None) is not None:
                chamber_bot.log.removeHandler(self._log_handler)
                self._log_handler = None
            self.events.put(("engine_stopped", {
                "reason": self.watchdog.stop_reason or "stopped",
                "successes": self.watchdog.successes,
                "failures": self.watchdog.failures,
            }))

    def stop(self):
        if self.watchdog is not None:
            self.watchdog.request_stop("user_stop")

    # -- controls ------------------------------------------------------------
    def toggle_pause(self):
        if self.watchdog is not None:
            self.watchdog.paused = not self.watchdog.paused
            self.events.put(("paused", {"paused": self.watchdog.paused}))

    def set_control(self, enabled):
        if self.watchdog is not None:
            self.watchdog.controller.enabled = bool(enabled)
            self.events.put(("control_toggled", {"enabled": bool(enabled)}))

    def set_target(self, target):
        self.settings["target_crafts"] = int(target)
        settings_mod.save(self.settings)
        if self.watchdog is not None:
            self.watchdog.target_crafts = int(target)

    def set_stop_on_fail(self, enabled):
        self.settings["stop_on_fail"] = bool(enabled)
        settings_mod.save(self.settings)
        if self.watchdog is not None:
            self.watchdog.stop_on_fail = bool(enabled)

    def set_base_power(self, level):
        self.settings["base_power"] = int(level)
        settings_mod.save(self.settings)
        cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
        cfg["power"]["default_level"] = int(level)
        chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
        if self.watchdog is not None:
            self.watchdog.regulator.default_level = int(level)

    def set_hotkey(self, role, key):
        self.settings["hotkeys"][role] = key.upper()
        settings_mod.save(self.settings)
        if self.watchdog is not None:
            self.watchdog.hotkeys[role] = key.upper()

    def reset_stats(self):
        self.settings["stats"] = {"successes": 0, "failures": 0}
        settings_mod.save(self.settings)
        if self.watchdog is not None:
            self.watchdog.successes = 0
            self.watchdog.failures = 0
        self.events.put(("stats_reset", {}))

    # -- events --------------------------------------------------------------
    def _emit(self, event, data):
        data = dict(data or {})
        if event == "craft_success":
            self.settings["stats"]["successes"] = int(data.get("successes", 0))
            settings_mod.save(self.settings)
        elif event == "craft_fail":
            self.settings["stats"]["failures"] = int(data.get("failures", 0))
            settings_mod.save(self.settings)
        self.events.put((event, data))

    def drain(self):
        """Return all pending events as a list of (event, data)."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out
