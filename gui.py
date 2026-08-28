"""Modern tabbed GUI for the Reaction Chamber bot with RU/EN switching.

Run:  python gui.py
The bot runs in a background thread (engine.BotEngine); the GUI only polls an event
queue, so tkinter is never touched from the bot thread.
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import calibrate_digits
import chamber_bot
import digit
import engine as engine_mod
import flags as flags_mod
import i18n
import mouse
import notify
import tray as tray_mod
import vision
import window

APP_TITLE = "Reaction Chamber"

# palette
BG = "#0d0f14"
CARD = "#161923"
CARD2 = "#1e2230"
LINE = "#2a3040"
ACCENT = "#6366f1"
ACCENT_H = "#7a7df5"
ACCENT_SOFT = "#24274a"
GREEN = "#10b981"
RED = "#ef4444"
AMBER = "#f59e0b"
TEXT = "#e9ebf2"
MUTED = "#8b93a7"

HEAD = "Bahnschrift"
BODY = "Segoe UI"
MONO = "Cascadia Mono"

KEYSYM_MAP = {
    "Return": "ENTER", "Escape": "ESC", "space": "SPACE", "Tab": "TAB",
    "BackSpace": "BACKSPACE", "Delete": "DELETE", "Insert": "INSERT",
    "Home": "HOME", "End": "END", "Prior": "PAGEUP", "Next": "PAGEDOWN",
    "Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT",
    "Shift_L": "SHIFT", "Shift_R": "SHIFT", "Control_L": "CTRL", "Control_R": "CTRL",
    "Alt_L": "ALT", "Alt_R": "ALT", "Caps_Lock": "CAPSLOCK",
}
HOTKEY_ROLES = [("toggle_control", "hk_toggle"), ("pause", "hk_pause"), ("quit", "hk_quit")]


def hf(size, weight="bold"):
    return ctk.CTkFont(family=HEAD, size=size, weight=weight)


def bf(size, weight="normal"):
    return ctk.CTkFont(family=BODY, size=size, weight=weight)


def keysym_to_name(keysym):
    if keysym in KEYSYM_MAP:
        return KEYSYM_MAP[keysym]
    if len(keysym) == 1:
        return keysym.upper()
    if keysym.startswith("F") and keysym[1:].isdigit():
        return keysym.upper()
    return None


class ToolTip:
    def __init__(self, widget, text_provider):
        self.widget = widget
        self.provider = text_provider
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event):
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw, text=self.provider(), background="#232838", foreground="#e9ebf2",
            font=(BODY, 10), padx=11, pady=7, relief="solid", bd=1,
            justify="left", wraplength=280,
        ).pack()

    def _hide(self, _event):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def _dbg(msg):
    try:
        import os as _o

        _o.makedirs(chamber_bot.LOG_DIR, exist_ok=True)
        with open(_o.path.join(chamber_bot.LOG_DIR, "editor_debug.log"), "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:  # noqa: BLE001
        pass


class RoiEditor:
    """Interactive editor: drag the zone rectangles and +/- points on a snapshot of
    the chamber panel, then save the adjusted positions back to config.json."""

    SCALE = 2
    HANDLE = 7
    RECT_ROIS = ["power_scale", "power_marker", "luck_bar", "fail_bar", "digit"]
    POINTS = ["power_plus", "power_minus"]
    COLORS = {
        "power_scale": "#22c55e", "power_marker": "#06b6d4", "luck_bar": "#3b82f6",
        "fail_bar": "#f97316", "digit": "#d946ef",
        "power_plus": "#eab308", "power_minus": "#eab308",
    }
    LABELS = {
        "power_scale": "1 scale", "power_marker": "2 marker", "luck_bar": "3 luck",
        "fail_bar": "4 fail", "digit": "5 digit",
        "power_plus": "+", "power_minus": "-",
    }

    def __init__(self, master, tr):
        self.tr = tr
        self.master = master
        self.items = {}
        self._drag = None
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            left, top, self.panel_w, self.panel_h = vision.anchor_rect(
                window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
            with vision.ScreenCapture() as capture:
                self.panel = capture.grab(left, top, self.panel_w, self.panel_h)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        if vision.is_blank(self.panel):
            messagebox.showerror(APP_TITLE, tr("n_no_grey"))
            return
        self.cfg = cfg
        self._build()

    def _build(self):
        from PIL import Image, ImageTk

        # pick a scale so the editor stays a reasonable size; an oversized window
        # gets clipped by the OS and stops receiving mouse events properly
        try:
            sw = self.master.winfo_screenwidth()
            sh = self.master.winfo_screenheight()
        except Exception:  # noqa: BLE001
            sw, sh = 1280, 800
        max_w = min(sw - 40, 1150)
        max_h = min(sh - 140, 760)
        S = 2
        while S > 1 and (self.panel_w * S > max_w or self.panel_h * S > max_h):
            S -= 1
        self._scale = S
        W, H = self.panel_w * S, self.panel_h * S
        # trim the bottom of the editor: keep only up to the fail bar (zone 4)
        # plus a small margin, so the player-inventory part isn't shown
        fb = self.cfg["rois"].get("fail_bar")
        if fb:
            crop_h_panel = int((fb[1] + fb[3]) * self.panel_h) + 20
            crop_h_panel = min(crop_h_panel, self.panel_h)
        else:
            crop_h_panel = self.panel_h
        Hc = crop_h_panel * S
        _dbg(f"BUILD screen={sw}x{sh} max={max_w}x{max_h} chosen_scale={S} crop_h={crop_h_panel}")
        self.win = ctk.CTkToplevel(self.master)
        self.win.title(self.tr("editor_title"))
        self.win.configure(fg_color="#10131a")
        # place beside the main window so they don't overlap and fight for events
        try:
            mx = self.master.winfo_rootx()
            my = self.master.winfo_rooty()
            mw = self.master.winfo_width()
            sw = self.master.winfo_screenwidth()
            px = mx + mw + 16
            if px + self.panel_w * S + 40 > sw:
                px = max(0, mx - self.panel_w * S - 16)
            self.win.geometry(f"+{max(0, px)}+{max(0, my)}")
        except Exception:  # noqa: BLE001
            pass

        bar = tk.Frame(self.win, bg="#10131a")
        bar.pack(fill="x", padx=8, pady=6)
        tk.Label(bar, text=self.tr("editor_hint"), bg="#10131a", fg="#9aa4b8",
                 font=(BODY, 10), anchor="w", justify="left").pack(side="left", fill="x", expand=True)

        self.canvas = tk.Canvas(self.win, width=W, height=Hc, bg="#000",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=8, pady=(0, 8))

        cropped_panel = self.panel[:crop_h_panel, :]
        img = Image.fromarray(cropped_panel.astype("uint8")).resize((W, Hc), Image.NEAREST)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        _dbg(f"BUILD panel={self.panel_w}x{self.panel_h} canvas={W}x{Hc} "
             f"topmost={self.win.attributes('-topmost')}")

        for name in self.RECT_ROIS:
            x, y, w, h = self.cfg["rois"][name]
            self._add_rect(name, x * self.panel_w * S, y * self.panel_h * S,
                           (x + w) * self.panel_w * S, (y + h) * self.panel_h * S)
        for name in self.POINTS:
            px, py = self.cfg["points"][name]
            self._add_point(name, px * self.panel_w * S, py * self.panel_h * S)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<ButtonPress-1>", lambda e: _dbg(f"WINPRESS ({e.x},{e.y})"), add="+")
        self.canvas.bind("<Enter>", lambda e: _dbg("CANVAS ENTER"), add="+")

        btns = tk.Frame(self.win, bg="#10131a")
        btns.pack(fill="x", padx=8, pady=(0, 10))
        tk.Button(btns, text=self.tr("editor_save"), bg=ACCENT, fg="white",
                  activebackground=ACCENT_H, relief="flat", padx=16, pady=6,
                  font=(BODY, 11, "bold"), command=self._save).pack(side="left")
        tk.Button(btns, text=self.tr("editor_cancel"), bg="#2a2f3a", fg="#cbd2e0",
                  activebackground="#3a4050", relief="flat", padx=16, pady=6,
                  font=(BODY, 11), command=self.win.destroy).pack(side="left", padx=8)

        self.win.update_idletasks()
        self.win.lift()
        self.win.focus_force()

    def _add_rect(self, name, x0, y0, x1, y1):
        c = self.COLORS[name]
        rid = self.canvas.create_rectangle(x0, y0, x1, y1, outline=c, width=2)
        hid = self.canvas.create_rectangle(x1 - self.HANDLE, y1 - self.HANDLE, x1 + self.HANDLE,
                                           y1 + self.HANDLE, fill=c, outline="white", width=1)
        lid = self.canvas.create_text(x0 + 4, y0 + 4, anchor="nw", text=self.LABELS[name],
                                      fill=c, font=(BODY, 10, "bold"))
        self.items[name] = {"kind": "rect", "coords": [x0, y0, x1, y1],
                            "rect": rid, "handle": hid, "label": lid}

    def _add_point(self, name, cx, cy):
        c = self.COLORS[name]
        r = 9
        ids = [
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=2),
            self.canvas.create_line(cx - r, cy, cx + r, cy, fill=c, width=1),
            self.canvas.create_line(cx, cy - r, cx, cy + r, fill=c, width=1),
            self.canvas.create_text(cx + r + 3, cy, anchor="w", text=self.LABELS[name],
                                    fill=c, font=(BODY, 10, "bold")),
        ]
        self.items[name] = {"kind": "point", "coords": [cx, cy], "ids": ids}

    def _hit(self, x, y):
        for name, it in self.items.items():
            if it["kind"] == "rect":
                x0, y0, x1, y1 = it["coords"]
                if abs(x - x1) <= self.HANDLE + 3 and abs(y - y1) <= self.HANDLE + 3:
                    return name, "resize"
                if x0 <= x <= x1 and y0 <= y <= y1:
                    return name, "move"
            else:
                cx, cy = it["coords"]
                if (x - cx) ** 2 + (y - cy) ** 2 <= 15 ** 2:
                    return name, "move"
        return None, None

    def _press(self, e):
        name, mode = self._hit(e.x, e.y)
        _dbg(f"PRESS at ({e.x},{e.y}) -> hit={name} mode={mode}")
        if name:
            self._drag = (name, mode, e.x, e.y, list(self.items[name]["coords"]))

    def _motion(self, e):
        if not self._drag:
            return
        _dbg(f"MOTION at ({e.x},{e.y})")
        name, mode, sx, sy, start = self._drag
        dx, dy = e.x - sx, e.y - sy
        it = self.items[name]
        if it["kind"] == "rect":
            x0, y0, x1, y1 = start
            if mode == "move":
                nx0, ny0, nx1, ny1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
            else:
                nx0, ny0 = x0, y0
                nx1, ny1 = max(x0 + 8, x1 + dx), max(y0 + 8, y1 + dy)
            it["coords"] = [nx0, ny0, nx1, ny1]
            self.canvas.coords(it["rect"], nx0, ny0, nx1, ny1)
            self.canvas.coords(it["handle"], nx1 - self.HANDLE, ny1 - self.HANDLE,
                               nx1 + self.HANDLE, ny1 + self.HANDLE)
            self.canvas.coords(it["label"], nx0 + 4, ny0 + 4)
        else:
            cx, cy = start
            nx, ny = cx + dx, cy + dy
            it["coords"] = [nx, ny]
            r = 9
            self.canvas.coords(it["ids"][0], nx - r, ny - r, nx + r, ny + r)
            self.canvas.coords(it["ids"][1], nx - r, ny, nx + r, ny)
            self.canvas.coords(it["ids"][2], nx, ny - r, nx, ny + r)
            self.canvas.coords(it["ids"][3], nx + r + 3, ny)

    def _release(self, _e):
        self._drag = None

    def _save(self):
        S = self._scale
        for name in self.RECT_ROIS:
            x0, y0, x1, y1 = self.items[name]["coords"]
            self.cfg["rois"][name] = [
                round(x0 / (self.panel_w * S), 4), round(y0 / (self.panel_h * S), 4),
                round((x1 - x0) / (self.panel_w * S), 4), round((y1 - y0) / (self.panel_h * S), 4),
            ]
        for name in self.POINTS:
            cx, cy = self.items[name]["coords"]
            self.cfg["points"][name] = [round(cx / (self.panel_w * S), 4),
                                        round(cy / (self.panel_h * S), 4)]
        chamber_bot.save_config(self.cfg, chamber_bot.DEFAULT_CONFIG)
        self.win.destroy()
        messagebox.showinfo(APP_TITLE, self.tr("editor_saved"))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        chamber_bot.setup_logging_gui()
        self.engine = engine_mod.BotEngine()
        self.settings = self.engine.settings
        self.lang = self.settings.get("language", "ru")
        self.tray = tray_mod.TrayManager(self)

        self._rebind_role = None
        self._rebind_after = None
        self._calib_stop = threading.Event()
        self._notif_after = None
        self._log_visible = False
        self._tray_hint_shown = False
        self._rebuilding = False

        self._overlay_active = False
        self._overlay_win = None
        self._overlay_canvas = None
        self._overlay_job = None
        self._overlay_hotkey = None

        self.title(APP_TITLE)
        self.geometry("760x700")
        self.minsize(680, 620)
        self.configure(fg_color=BG)
        ctk.set_appearance_mode("dark")
        try:
            import os as _os

            import paths as _paths

            _ico = _os.path.join(_paths.DATA_DIR, "app_icon.ico")
            if _os.path.exists(_ico):
                self.iconbitmap(_ico)
        except Exception:  # noqa: BLE001
            pass

        self._flag_ru = ctk.CTkImage(light_image=flags_mod.make_ru_flag(), size=(30, 20))
        self._flag_us = ctk.CTkImage(light_image=flags_mod.make_us_flag(), size=(30, 20))

        self._build()
        self._refresh_settings_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ---------------------------------------------------------------- helpers
    def tr(self, key, **kw):
        return i18n.get(self.lang, key, **kw)

    def _glow(self, widget, color=ACCENT, width=2):
        """Soft hover glow: show a coloured border ring while the mouse is over."""
        def enter(_e):
            try:
                widget.configure(border_color=color, border_width=width)
            except Exception:  # noqa: BLE001
                pass

        def leave(_e):
            try:
                widget.configure(border_width=0)
            except Exception:  # noqa: BLE001
                pass

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def _card(self, parent):
        f = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        f.pack(fill="x", pady=6)
        self._glow(f, LINE, 1)
        return f

    def _card_title(self, parent, text):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(head, text=text, text_color=TEXT, font=hf(16)).pack(side="left")
        return head

    def _muted(self, parent, text, **kw):
        return ctk.CTkLabel(parent, text=text, text_color=MUTED, font=bf(12), **kw)

    # ------------------------------------------------------------------ build
    def _build(self):
        self._build_header()
        self.banner = ctk.CTkLabel(self, text="", text_color="#0d0f14", fg_color=ACCENT, corner_radius=12,
                                   font=hf(13), padx=14, pady=9, anchor="w")
        self._build_tabs()
        self._build_log()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        header.pack(fill="x", padx=16, pady=(16, 6))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text=self.tr("app_title"), text_color=TEXT, font=hf(22)).grid(
            row=0, column=0, padx=18, pady=(14, 0), sticky="w")
        ctk.CTkLabel(header, text=self.tr("app_subtitle"), text_color=MUTED, font=bf(12)).grid(
            row=1, column=0, padx=18, pady=(0, 14), sticky="w")

        self.status_pill = ctk.CTkLabel(header, text="  " + self.tr("status_stopped") + "  ",
                                        text_color="#0d0f14", fg_color="#3a3f4d", corner_radius=14, font=hf(13))
        self.status_pill.grid(row=0, column=2, rowspan=2, padx=6, pady=14, sticky="e")

        other_flag = self._flag_us if self.lang == "ru" else self._flag_ru
        self.btn_lang = ctk.CTkButton(header, text="", image=other_flag, width=40, height=28,
                                      fg_color=CARD2, hover_color=LINE, command=self._toggle_lang)
        self.btn_lang.grid(row=0, column=3, rowspan=2, padx=6, pady=14, sticky="e")
        ToolTip(self.btn_lang, lambda: self.tr("lang_tip"))
        self._glow(self.btn_lang, ACCENT)

        ctk.CTkButton(header, text="\u2014", width=40, height=28, fg_color=CARD2, hover_color=LINE,
                      text_color=MUTED, command=self._hide_to_tray).grid(
            row=0, column=4, rowspan=2, padx=(0, 16), pady=14, sticky="e")

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, fg_color=CARD, corner_radius=16, segmented_button_fg_color=CARD2,
                                   segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_H,
                                   segmented_button_unselected_color=CARD2, segmented_button_unselected_hover_color=LINE,
                                   text_color=MUTED)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=6)
        try:
            self.tabs._segmented_button.configure(font=hf(14))
        except Exception:  # noqa: BLE001
            pass

        home = self.tabs.add(self.tr("tab_home"))
        settings = self.tabs.add(self.tr("tab_settings"))
        tools = self.tabs.add(self.tr("tab_tools"))

        self._build_home(home)
        self._build_settings_tab(settings)
        self._build_tools_tab(tools)

    # ------------------------------------------------------------- home tab --
    def _build_home(self, parent):
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x", pady=(8, 2))
        for i in range(6):
            grid.grid_columnconfigure(i, weight=1, uniform="st")
        self.stat = {}
        keys = [("power", "stat_power"), ("marker", "stat_marker"), ("luck", "stat_luck"),
                ("fail", "stat_fail"), ("session", "stat_session"), ("total", "stat_total")]
        for i, (key, tkey) in enumerate(keys):
            card = ctk.CTkFrame(grid, fg_color=CARD2, corner_radius=14)
            card.grid(row=0, column=i, padx=4, pady=2, sticky="nsew")
            self._glow(card, ACCENT_SOFT, 2)
            ctk.CTkLabel(card, text=self.tr(tkey), text_color=MUTED, font=bf(11)).pack(pady=(10, 0))
            val = ctk.CTkLabel(card, text="--", text_color=TEXT, font=hf(22))
            val.pack(pady=(0, 2))
            sub = ctk.CTkLabel(card, text="", text_color=MUTED, font=bf(10))
            sub.pack(pady=(0, 8))
            self.stat[key] = (val, sub)

        # run card
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", pady=8)
        self._glow(card, LINE, 1)
        self._card_title(card, self.tr("run_title"))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(8, 8))
        row.grid_columnconfigure((0, 1, 2), weight=1, uniform="ctl")
        self.btn_start = ctk.CTkButton(row, text=self.tr("btn_start"), height=46, fg_color=ACCENT,
                                       hover_color=ACCENT_H, font=hf(16), command=self._start)
        self.btn_start.grid(row=0, column=0, padx=5, sticky="ew")
        self._glow(self.btn_start, ACCENT_H)
        self.btn_pause = ctk.CTkButton(row, text=self.tr("btn_pause"), height=46, fg_color=CARD2,
                                       hover_color=LINE, font=hf(16), state="disabled", command=self._pause)
        self.btn_pause.grid(row=0, column=1, padx=5, sticky="ew")
        self._glow(self.btn_pause, ACCENT)
        self.btn_stop = ctk.CTkButton(row, text=self.tr("btn_stop"), height=46, fg_color=RED,
                                      hover_color="#c73636", font=hf(16), state="disabled", command=self._stop)
        self.btn_stop.grid(row=0, column=2, padx=5, sticky="ew")
        self._glow(self.btn_stop, RED)

        swrow = ctk.CTkFrame(card, fg_color="transparent")
        swrow.pack(fill="x", padx=18, pady=(2, 14))
        self.sw_control = ctk.CTkSwitch(swrow, text=self.tr("sw_control"), text_color=TEXT, font=bf(13),
                                        progress_color=ACCENT, command=self._on_control_chk)
        self.sw_control.select()
        self.sw_control.pack(side="left")
        ToolTip(self.sw_control, lambda: self.tr("tip_control"))

    # --------------------------------------------------------- settings tab --
    def _build_settings_tab(self, parent):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", pady=8)
        self._glow(card, LINE, 1)
        self._card_title(card, self.tr("settings_title"))
        bodyf = ctk.CTkFrame(card, fg_color="transparent")
        bodyf.pack(fill="x", padx=18, pady=(8, 14))
        bodyf.grid_columnconfigure(1, weight=1)

        self._muted(bodyf, self.tr("lbl_base")).grid(row=0, column=0, pady=6, sticky="w")
        self.entry_base = ctk.CTkEntry(bodyf, width=80, fg_color=CARD2, border_color=LINE, text_color=TEXT,
                                       font=bf(14))
        self.entry_base.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ctk.CTkButton(bodyf, text=self.tr("btn_apply"), width=90, fg_color=CARD2, hover_color=LINE,
                      font=bf(13), command=self._apply_base).grid(row=0, column=2, padx=4, pady=6)
        ToolTip(self.entry_base, lambda: self.tr("tip_base"))
        self._glow(self.entry_base, ACCENT)

        self._muted(bodyf, self.tr("lbl_target")).grid(row=1, column=0, pady=6, sticky="w")
        self.entry_target = ctk.CTkEntry(bodyf, width=80, fg_color=CARD2, border_color=LINE, text_color=TEXT,
                                         font=bf(14))
        self.entry_target.grid(row=1, column=1, padx=8, pady=6, sticky="w")
        ctk.CTkButton(bodyf, text=self.tr("btn_apply"), width=90, fg_color=CARD2, hover_color=LINE,
                      font=bf(13), command=self._apply_target).grid(row=1, column=2, padx=4, pady=6)
        ToolTip(self.entry_target, lambda: self.tr("tip_target"))
        self._glow(self.entry_target, ACCENT)

        self.sw_stopfail = ctk.CTkSwitch(bodyf, text=self.tr("sw_stopfail"), text_color=TEXT, font=bf(13),
                                         progress_color=RED, command=self._on_stopfail_chk)
        self.sw_stopfail.grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky="w")
        ToolTip(self.sw_stopfail, lambda: self.tr("tip_stopfail"))

        # hotkeys
        hkcard = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        hkcard.pack(fill="x", pady=8)
        self._glow(hkcard, LINE, 1)
        head = self._card_title(hkcard, self.tr("hotkeys_title"))
        self._muted(head, self.tr("tip_hotkeys")).pack(side="left", padx=10)
        bodyh = ctk.CTkFrame(hkcard, fg_color="transparent")
        bodyh.pack(fill="x", padx=18, pady=(8, 14))
        bodyh.grid_columnconfigure(1, weight=1)
        self._hk_labels, self._hk_buttons = {}, {}
        for i, (role, label_key) in enumerate(HOTKEY_ROLES):
            self._muted(bodyh, self.tr(label_key)).grid(row=i, column=0, pady=5, sticky="w")
            lab = ctk.CTkLabel(bodyh, text="", text_color=ACCENT, font=hf(13), fg_color=CARD2,
                               corner_radius=8, padx=14, pady=4)
            lab.grid(row=i, column=1, padx=8, pady=5, sticky="w")
            self._glow(lab, ACCENT)
            btn = ctk.CTkButton(bodyh, text=self.tr("btn_rebind"), width=110, fg_color=CARD2, hover_color=LINE,
                                font=bf(13), command=lambda r=role: self._rebind(r))
            btn.grid(row=i, column=2, padx=4, pady=5)
            self._glow(btn, ACCENT)
            self._hk_labels[role] = lab
            self._hk_buttons[role] = btn

    # ------------------------------------------------------------ tools tab --
    def _build_tools_tab(self, parent):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", pady=8)
        self._glow(card, LINE, 1)
        self._card_title(card, self.tr("tools_title"))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(8, 4))
        row.grid_columnconfigure((0, 1, 2), weight=1, uniform="tool")
        self.btn_find = ctk.CTkButton(row, text=self.tr("btn_find"), height=56, fg_color=CARD2,
                                      hover_color=LINE, font=bf(13), command=self._find_anchor)
        self.btn_find.grid(row=0, column=0, padx=5, sticky="ew")
        self._glow(self.btn_find, ACCENT)
        ToolTip(self.btn_find, lambda: self.tr("tip_find"))
        self.btn_calib = ctk.CTkButton(row, text=self.tr("btn_calib"), height=56, fg_color=CARD2,
                                       hover_color=LINE, font=bf(13), command=self._calibrate)
        self.btn_calib.grid(row=0, column=1, padx=5, sticky="ew")
        self._glow(self.btn_calib, ACCENT)
        ToolTip(self.btn_calib, lambda: self.tr("tip_calib"))
        self.btn_rstats = ctk.CTkButton(row, text=self.tr("btn_reset_stats"), height=56, fg_color=CARD2,
                                        hover_color=LINE, font=bf(13), command=self._reset_stats)
        self.btn_rstats.grid(row=0, column=2, padx=5, sticky="ew")
        self._glow(self.btn_rstats, RED)

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=18, pady=(4, 14))
        row2.grid_columnconfigure((0, 1), weight=1, uniform="tool2")
        self.btn_calibbtn = ctk.CTkButton(row2, text=self.tr("btn_calib_buttons"), height=52,
                                          fg_color=CARD2, hover_color=LINE, font=bf(13),
                                          command=self._calibrate_buttons)
        self.btn_calibbtn.grid(row=0, column=0, padx=5, sticky="ew")
        self._glow(self.btn_calibbtn, ACCENT)
        ToolTip(self.btn_calibbtn, lambda: self.tr("tip_calib_buttons"))
        self.btn_verify = ctk.CTkButton(row2, text=self.tr("btn_verify"), height=52,
                                        fg_color=CARD2, hover_color=LINE, font=bf(13),
                                        command=self._verify_calibration)
        self.btn_verify.grid(row=0, column=1, padx=5, sticky="ew")
        self._glow(self.btn_verify, AMBER)
        ToolTip(self.btn_verify, lambda: self.tr("tip_verify"))

        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", padx=18, pady=(4, 14))
        row3.grid_columnconfigure((0, 1, 2), weight=1, uniform="tool3")
        self.btn_editor = ctk.CTkButton(row3, text=self.tr("btn_editor"), height=52,
                                        fg_color=CARD2, hover_color=LINE, font=bf(13),
                                        command=self._open_editor)
        self.btn_editor.grid(row=0, column=0, padx=5, sticky="ew")
        self._glow(self.btn_editor, GREEN)
        ToolTip(self.btn_editor, lambda: self.tr("tip_editor"))
        self.btn_template = ctk.CTkButton(row3, text=self.tr("btn_template"), height=52,
                                          fg_color=CARD2, hover_color=LINE, font=bf(13),
                                          command=self._save_template)
        self.btn_template.grid(row=0, column=1, padx=5, sticky="ew")
        self._glow(self.btn_template, GREEN)
        ToolTip(self.btn_template, lambda: self.tr("tip_template"))
        self.btn_overlay = ctk.CTkButton(row3, text=self.tr("btn_overlay"), height=52,
                                         fg_color=CARD2, hover_color=LINE, font=bf(13),
                                         command=self._toggle_overlay)
        self.btn_overlay.grid(row=0, column=2, padx=5, sticky="ew")
        self._glow(self.btn_overlay, AMBER)
        ToolTip(self.btn_overlay, lambda: self.tr("tip_overlay"))

    # ------------------------------------------------------------------ log --
    def _build_log(self):
        self.btn_log = ctk.CTkButton(self, text=self.tr("show_log") + "  \u25be", fg_color=CARD,
                                     hover_color=CARD2, text_color=MUTED, anchor="w", font=bf(13),
                                     command=self._toggle_log)
        self.btn_log.pack(fill="x", padx=16, pady=(2, 0))
        self._glow(self.btn_log, LINE, 1)
        self.log_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        self.log_box = ctk.CTkTextbox(self.log_card, fg_color="transparent", text_color="#c9cfdb",
                                      font=ctk.CTkFont(family=MONO, size=12), height=170)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=10)

    def _toggle_log(self):
        if self._log_visible:
            self.log_card.pack_forget()
            self.btn_log.configure(text=self.tr("show_log") + "  \u25be")
            self._log_visible = False
        else:
            self.log_card.pack(fill="both", padx=16, pady=(6, 12))
            self.btn_log.configure(text=self.tr("hide_log") + "  \u25b4")
            self._log_visible = True

    # --------------------------------------------------------------- language
    def _toggle_lang(self):
        self.lang = "en" if self.lang == "ru" else "ru"
        self.settings["language"] = self.lang
        import settings as settings_mod

        settings_mod.save(self.settings)
        self._rebuild_ui()

    def _rebuild_ui(self):
        self._rebuilding = True
        self._finish_rebind()
        for child in self.winfo_children():
            child.destroy()
        self._build()
        self._refresh_settings_ui()
        if self.engine.is_running():
            self._set_running_ui(True)
        self._rebuilding = False

    # --------------------------------------------------------------- settings
    def _refresh_settings_ui(self):
        s = self.settings
        self.entry_base.delete(0, "end")
        self.entry_base.insert(0, str(s["base_power"]))
        self.entry_target.delete(0, "end")
        self.entry_target.insert(0, str(s["target_crafts"]))
        if s["stop_on_fail"]:
            self.sw_stopfail.select()
        else:
            self.sw_stopfail.deselect()
        for role, lab in self._hk_labels.items():
            lab.configure(text=s["hotkeys"].get(role, "-"))
        self._update_stat_counters()

    def _update_stat_counters(self):
        s = self.engine.session_stats()
        t = self.settings["stats"]
        self.stat["session"][0].configure(text=f"{s['successes']}")
        self.stat["session"][1].configure(text=f"{s['failures']} {self.tr('failed')}")
        self.stat["total"][0].configure(text=f"{t['successes']}")
        self.stat["total"][1].configure(text=f"{t['failures']} {self.tr('failed')}")

    def _apply_base(self):
        try:
            level = int(self.entry_base.get())
        except ValueError:
            self._banner_show(self.tr("n_need_int"), RED)
            return
        self.engine.set_base_power(level)
        self._banner_show(self.tr("n_base_set", v=level), GREEN)

    def _apply_target(self):
        try:
            target = int(self.entry_target.get())
        except ValueError:
            self._banner_show(self.tr("n_need_int"), RED)
            return
        self.engine.set_target(target)
        self._banner_show(self.tr("n_target_set", v=target or self.tr("n_unlimited")), GREEN)

    def _on_stopfail_chk(self):
        self.engine.set_stop_on_fail(self.sw_stopfail.get() == 1)

    def _on_control_chk(self):
        self.engine.set_control(self.sw_control.get() == 1)

    def _reset_stats(self):
        if messagebox.askyesno(APP_TITLE, self.tr("reset_confirm")):
            self.engine.reset_stats()
            self.settings = self.engine.settings
            self._refresh_settings_ui()
            self._banner_show(self.tr("n_stats_reset"), GREEN)

    # ---------------------------------------------------------------- hotkeys
    def _rebind(self, role):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        self._rebind_role = role
        self._hk_buttons[role].configure(text=self.tr("btn_press_key"))
        self.bind_all("<Key>", self._on_rebind_key)
        if self._rebind_after:
            self.after_cancel(self._rebind_after)
        self._rebind_after = self.after(10000, self._rebind_cancel)

    def _on_rebind_key(self, event):
        if not self._rebind_role:
            return
        name = keysym_to_name(event.keysym)
        role = self._rebind_role
        self._finish_rebind()
        if not name:
            return
        for r, k in self.settings["hotkeys"].items():
            if r != role and k.upper() == name:
                self._banner_show(self.tr("n_bind_used", k=name, r=r), AMBER)
                return
        self.engine.set_hotkey(role, name)
        self.settings = self.engine.settings
        self._hk_labels[role].configure(text=name)
        self._banner_show(self.tr("n_bound", r=role, k=name), GREEN)

    def _rebind_cancel(self):
        if self._rebind_role:
            self._finish_rebind()
            self._refresh_settings_ui()

    def _finish_rebind(self):
        if self._rebind_role in self._hk_buttons:
            self._hk_buttons[self._rebind_role].configure(text=self.tr("btn_rebind"))
        self._rebind_role = None
        try:
            self.unbind_all("<Key>")
        except tk.TclError:
            pass

    # --------------------------------------------------------------- controls
    def _start(self):
        control = self.sw_control.get() == 1
        if not self.engine.start(control):
            self._banner_show(self.tr("n_start_failed"), RED)
            return
        self._set_running_ui(True)
        self.status_pill.configure(text="  RUNNING  ", fg_color=GREEN)
        self._notify(APP_TITLE, self.tr("n_started", v=self.tr("n_on") if control else self.tr("n_off")), "success")

    def _stop(self):
        self.engine.stop()

    def _pause(self):
        self.engine.toggle_pause()

    def toggle_run(self):
        if self.engine.is_running():
            self._stop()
        else:
            self._start()

    def _set_running_ui(self, running):
        if running:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.btn_pause.configure(state="normal")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_pause.configure(state="disabled", text=self.tr("btn_pause"))
            self.status_pill.configure(text="  " + self.tr("status_stopped") + "  ", fg_color="#3a3f4d")

    # ------------------------------------------------------------------ tools
    def _find_anchor(self):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        threading.Thread(target=self._find_anchor_worker, daemon=True).start()

    def _wait_corner(self, prompt_key, timeout=30):
        """Ask the user to hover a corner and press SPACE. Returns (x, y) screen
        coords, or None on timeout. Waits for SPACE to be released afterwards so a
        held key can't immediately trigger the next corner."""
        self.engine.events.put(("banner", {"text": self.tr(prompt_key), "color": AMBER}))
        keys = mouse.HotkeyEdge("SPACE")
        deadline = time.time() + timeout
        pos = None
        while time.time() < deadline:
            if keys.pressed("SPACE"):
                pos = window.cursor_pos()
                break
            time.sleep(0.03)
        if pos is None:
            return None
        # wait until SPACE is released so the next corner needs a fresh press
        release_deadline = time.time() + 5
        while time.time() < release_deadline and mouse.key_down("SPACE"):
            time.sleep(0.03)
        return pos

    def _find_anchor_worker(self):
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            left, top, width, height = window.client_rect_on_screen(hwnd)

            # Try the saved panel template first: it re-finds the exact panel box
            # (at any window size / GUI scale) without any corner prompts.
            try:
                import panel_match

                template = panel_match.load_template()
                if template is not None:
                    with vision.ScreenCapture() as capture:
                        frame = capture.grab(left, top, width, height)
                    found = panel_match.find_panel(frame, template)
                    if found is not None:
                        x, y, w, h = found
                        cfg["anchor"] = {"x": x, "y": y, "w": w, "h": h}
                        chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
                        self.engine.events.put(("log", {"level": "INFO", "message": f"anchor set from template: {cfg['anchor']}"}))
                        self.engine.events.put(("banner", {"text": self.tr("found_by_template", x=x, y=y), "color": GREEN}))
                        return
            except Exception as exc:  # noqa: BLE001
                self.engine.events.put(("log", {"level": "WARNING", "message": f"template match failed: {exc}"}))

            tl = self._wait_corner("anchor_tl")
            if tl is None:
                self.engine.events.put(("banner", {"text": self.tr("anchor_timeout"), "color": RED}))
                return
            br = self._wait_corner("anchor_br")
            if br is None:
                self.engine.events.put(("banner", {"text": self.tr("anchor_timeout"), "color": RED}))
                return

            x0, y0 = min(tl[0], br[0]), min(tl[1], br[1])
            x1, y1 = max(tl[0], br[0]), max(tl[1], br[1])
            ax, ay = x0 - left, y0 - top
            aw, ah = x1 - x0, y1 - y0
            if aw < 50 or ah < 50:
                self.engine.events.put(("banner", {"text": "zone too small", "color": RED}))
                return
            cfg["anchor"] = {"x": ax, "y": ay, "w": aw, "h": ah}
            chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
            self.engine.events.put(("log", {"level": "INFO", "message": f"anchor set: {cfg['anchor']}"}))
            self.engine.events.put(("banner", {"text": self.tr("n_anchor_done"), "color": GREEN}))
        except Exception as exc:  # noqa: BLE001
            self.engine.events.put(("error", {"message": str(exc)}))

    def _save_template(self):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        threading.Thread(target=self._save_template_worker, daemon=True).start()

    def _save_template_worker(self):
        """Capture the panel at the current anchor and store it as the reference
        template that 'Find window' and auto re-anchor align to."""
        try:
            import panel_match

            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            left, top, panel_w, panel_h = vision.anchor_rect(
                window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
            with vision.ScreenCapture() as capture:
                panel = capture.grab(left, top, panel_w, panel_h)
            if vision.is_blank(panel) or not chamber_bot.gui_is_open(panel, cfg):
                self.engine.events.put(("banner", {"text": self.tr("n_no_grey"), "color": RED}))
                return
            meta = panel_match.save_template(panel, cfg["rois"], cfg["points"])
            self.engine.events.put(("log", {"level": "INFO",
                                            "message": f"panel template saved: {meta['w']}x{meta['h']}"}))
            self.engine.events.put(("banner", {"text": self.tr("template_saved"), "color": GREEN}))
        except Exception as exc:  # noqa: BLE001
            self.engine.events.put(("error", {"message": str(exc)}))

    def _calibrate(self):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title(self.tr("calib_title"))
        dlg.geometry("420x210")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG)
        dlg.transient(self)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text=self.tr("calib_desc"), text_color=TEXT, justify="left", font=bf(13)).pack(
            padx=16, pady=(16, 8))
        entry = ctk.CTkEntry(dlg, width=90, fg_color=CARD2, border_color=LINE, text_color=TEXT, font=bf(15))
        entry.pack(pady=6)
        entry.insert(0, "1")

        def begin():
            try:
                start_value = int(entry.get())
            except ValueError:
                self._banner_show(self.tr("calib_bad_start"), RED)
                return
            dlg.destroy()
            self._start_calibration(start_value)

        ctk.CTkButton(dlg, text=self.tr("calib_start"), fg_color=ACCENT, hover_color=ACCENT_H,
                      font=hf(14), command=begin).pack(pady=14)

    def _start_calibration(self, start_value):
        self.btn_calib.configure(state="disabled", text="...")
        self._calib_stop.clear()
        # hide our window so it can't cover the chamber +/- buttons while clicking
        self.withdraw()
        notify.toast(APP_TITLE, self.tr("calib_hidden"))
        threading.Thread(target=self._calibration_worker, args=(start_value,), daemon=True).start()

    def _calibration_worker(self, start_value):
        q = self.engine.events
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            templates, final_value = calibrate_digits.run_calibration(
                cfg, hwnd, start_value,
                progress_cb=lambda m: q.put(("log", {"level": "INFO", "message": f"calib: {m}"})),
                should_stop=self._calib_stop.is_set,
            )
            rect = vision.anchor_rect(window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
            with vision.ScreenCapture() as capture:
                panel = capture.grab(*rect)
            value, conf = digit.read_number(panel, templates)
            q.put(("calib_done", {"value": value, "conf": conf, "count": len(templates)}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", {"message": f"calibration failed: {exc}"}))
            q.put(("calib_done", {"value": None, "conf": 0.0, "count": 0}))

    def _on_calib_done(self, data):
        self.deiconify()
        self.btn_calib.configure(state="normal", text=self.tr("btn_calib"))
        if data.get("value") is None:
            self._banner_show(self.tr("calib_reread_fail"), AMBER)
            return
        ok = messagebox.askyesno(APP_TITLE, self.tr("calib_done", c=data.get("count", 0),
                                                    v=data["value"], cf=data["conf"]))
        if not ok:
            self._banner_show(self.tr("calib_again"), AMBER)

    # ------------------------------------------------- +/- button calibration --
    def _close_existing_editor(self):
        ed = getattr(self, "_roi_editor", None)
        if ed is not None:
            try:
                ed.win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._roi_editor = None

    def _auto_open_editor(self):
        try:
            self._close_existing_editor()
            self._roi_editor = RoiEditor(self, self.tr)
            _dbg("AUTOOPEN ok")
        except Exception as exc:  # noqa: BLE001
            _dbg(f"AUTOOPEN ERROR {exc}")

    def _open_editor(self):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        # close any previous editor first so zones don't stack up
        self._close_existing_editor()
        # keep a reference so the editor (and its mouse bindings) isn't GC'd
        self._roi_editor = RoiEditor(self, self.tr)

    def _calibrate_buttons(self):
        if self.engine.is_running():
            self._banner_show(self.tr("n_stop_before"), AMBER)
            return
        self.btn_calibbtn.configure(state="disabled", text="...")
        self.withdraw()
        notify.toast(APP_TITLE, self.tr("calib_hidden"))
        threading.Thread(target=self._calib_buttons_worker, daemon=True).start()

    def _calib_buttons_worker(self):
        q = self.engine.events
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            window.focus(hwnd)
            keys = mouse.HotkeyEdge("SPACE")
            results = {}
            steps = [("power_plus", self.tr("calibbtn_plus")), ("power_minus", self.tr("calibbtn_minus"))]
            for name, prompt in steps:
                q.put(("banner", {"text": prompt, "color": ACCENT}))
                deadline = time.time() + 30
                hit = False
                while time.time() < deadline:
                    if keys.pressed("SPACE"):
                        hit = True
                        break
                    time.sleep(0.05)
                if not hit:
                    q.put(("banner", {"text": self.tr("calibbtn_timeout"), "color": RED}))
                    return
                sx, sy = window.cursor_pos()
                left, top, _w, _h = window.client_rect_on_screen(hwnd)
                ax, ay, aw, ah = chamber_bot.anchor_tuple(cfg)
                if aw <= 0 or ah <= 0:
                    q.put(("banner", {"text": self.tr("n_no_grey"), "color": RED}))
                    return
                px, py = sx - (left + ax), sy - (top + ay)
                results[name] = [round(px / aw, 4), round(py / ah, 4)]
                q.put(("log", {"level": "INFO",
                               "message": f"button {name} -> panel({px},{py}) norm {results[name]}"}))
                time.sleep(0.3)
            cfg["points"].update(results)
            chamber_bot.save_config(cfg, chamber_bot.DEFAULT_CONFIG)
            q.put(("banner", {"text": self.tr("calibbtn_done"), "color": GREEN}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", {"message": str(exc)}))
        finally:
            q.put(("calib_btn_done", {}))

    # --------------------------------------------------- calibration preview --
    def _zones_for(self, cfg, panel_w, panel_h):
        """Zone rectangles in panel pixels, shared by 'Verify calibration' and the
        persistent overlay. Returns a list of (rect, description_key, is_point)."""
        rois = cfg["rois"]
        pts = cfg["points"]

        def roi_px(roi):
            x, y, w, h = roi
            return (int(x * panel_w), int(y * panel_h), int((x + w) * panel_w), int((y + h) * panel_h))

        zones = [
            (roi_px(rois["power_scale"]), "zone_power_scale", False),
            (roi_px(rois["power_marker"]), "zone_power_marker", False),
            (roi_px(rois["luck_bar"]), "zone_luck", False),
            (roi_px(rois["fail_bar"]), "zone_fail", False),
        ]
        # digit zone comes from the saved config (ROI editor); fall back to default
        digit_roi = rois.get("digit")
        if digit_roi and len(digit_roi) == 4:
            zones.append((roi_px(digit_roi), "zone_digit", False))
        else:
            x0, y0, x1, y1 = digit.DIGIT_ROI
            rw, rh = digit.DIGIT_ROI_REF
            sx, sy = panel_w / rw, panel_h / rh
            zones.append(((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)), "zone_digit", False))
        for name, key in (("power_plus", "zone_plus"), ("power_minus", "zone_minus")):
            px, py = int(pts[name][0] * panel_w), int(pts[name][1] * panel_h)
            zones.append(((px - 10, py - 10, px + 10, py + 10), key, True))
        return zones

    def _verify_calibration(self):
        from PIL import Image, ImageDraw, ImageFont, ImageTk

        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            left, top, panel_w, panel_h = vision.anchor_rect(
                window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
            with vision.ScreenCapture() as capture:
                panel = capture.grab(left, top, panel_w, panel_h)
        except Exception as exc:  # noqa: BLE001
            self._banner_show(f"{exc}", RED)
            return
        if vision.is_blank(panel):
            self._banner_show(self.tr("n_no_grey"), RED)
            return

        zones = self._zones_for(cfg, panel_w, panel_h)

        img = Image.fromarray(panel.astype("uint8"))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        legend_lines = []
        for i, (rect, key, is_point) in enumerate(zones, start=1):
            rx0, ry0, rx1, ry1 = rect
            color = (255, 200, 0) if is_point else (255, 40, 40)
            draw.rectangle([rx0, ry0, rx1, ry1], outline=color, width=2)
            draw.text((rx0 + 3, max(0, ry0 + 2)), str(i), fill=color, font=font)
            legend_lines.append(f"{i}  -  {self.tr(key)}")

        try:
            import os as _os

            _os.makedirs(chamber_bot.LOG_DIR, exist_ok=True)
            img.save(_os.path.join(chamber_bot.LOG_DIR, "verify_preview.png"))
            for i, (rect, _key, _is_point) in enumerate(zones, start=1):
                cx0, cy0, cx1, cy1 = rect
                cx0, cy0 = max(0, cx0), max(0, cy0)
                cx1, cy1 = min(panel_w, cx1), min(panel_h, cy1)
                if cx1 > cx0 and cy1 > cy0:
                    Image.fromarray(panel[cy0:cy1, cx0:cx1].astype("uint8")).save(
                        _os.path.join(chamber_bot.LOG_DIR, f"verify_zone_{i}.png"))
        except Exception:  # noqa: BLE001
            pass

        photo = ImageTk.PhotoImage(img)
        prev = tk.Toplevel(self)
        prev.title(self.tr("verify_title"))
        prev.overrideredirect(True)
        prev.geometry(f"{panel_w}x{panel_h}+{left}+{top}")
        prev.attributes("-topmost", True)
        lbl = tk.Label(prev, image=photo)
        lbl.image = photo
        lbl.pack()

        legend = ctk.CTkToplevel(self)
        legend.title(self.tr("verify_title"))
        legend.geometry("430x360")
        legend.configure(fg_color=BG)
        legend.attributes("-topmost", True)
        legend.transient(self)
        ctk.CTkLabel(legend, text=self.tr("verify_hint"), text_color=MUTED, justify="left",
                     font=bf(12)).pack(padx=14, pady=(12, 6), anchor="w")
        box = ctk.CTkTextbox(legend, fg_color=CARD, text_color=TEXT, font=bf(13))
        box.pack(fill="both", expand=True, padx=14, pady=6)
        box.insert("1.0", "\n".join(legend_lines))
        box.configure(state="disabled")

        def close_all():
            try:
                prev.destroy()
            except tk.TclError:
                pass
            try:
                legend.destroy()
            except tk.TclError:
                pass

        ctk.CTkButton(legend, text=self.tr("verify_close"), fg_color=ACCENT, hover_color=ACCENT_H,
                      command=close_all).pack(pady=(2, 12))
        self.after(20000, close_all)

    # --------------------------------------------------- persistent zone overlay
    OVERLAY_KEY = "#010203"  # colour-keyed pixels are invisible AND click-through

    def _toggle_overlay(self):
        if self._overlay_active:
            self._overlay_stop()
            self._banner_show(self.tr("overlay_off"), MUTED)
        else:
            self._overlay_start()

    def _overlay_start(self):
        try:
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=self.OVERLAY_KEY)
            win.attributes("-transparentcolor", self.OVERLAY_KEY)
            canvas = tk.Canvas(win, bg=self.OVERLAY_KEY, highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
        except tk.TclError as exc:
            self._banner_show(str(exc), RED)
            return
        self._overlay_win = win
        self._overlay_canvas = canvas
        self._overlay_photo = None
        self._overlay_active = True
        self._overlay_hotkey = mouse.HotkeyEdge("F7")
        self._banner_show(self.tr("overlay_on"), GREEN)
        self._overlay_tick()

    def _overlay_stop(self):
        self._overlay_active = False
        if self._overlay_job is not None:
            try:
                self.after_cancel(self._overlay_job)
            except Exception:  # noqa: BLE001
                pass
            self._overlay_job = None
        if self._overlay_win is not None:
            try:
                self._overlay_win.destroy()
            except tk.TclError:
                pass
            self._overlay_win = None
        self._overlay_canvas = None

    def _overlay_tick(self):
        self._overlay_job = None
        if not self._overlay_active:
            return
        if self._overlay_hotkey is not None and self._overlay_hotkey.pressed("F7"):
            self._overlay_stop()
            self._banner_show(self.tr("overlay_off"), MUTED)
            return
        try:
            cfg = chamber_bot.load_config(chamber_bot.DEFAULT_CONFIG)
            hwnd = window.find_window(cfg["window_title"])
            if not window.is_minimized(hwnd):
                left, top, panel_w, panel_h = vision.anchor_rect(
                    window.client_rect_on_screen(hwnd), chamber_bot.anchor_tuple(cfg))
                self._overlay_draw(cfg, left, top, panel_w, panel_h)
        except Exception:  # noqa: BLE001 - the overlay must never crash the app
            pass
        if self._overlay_active:
            self._overlay_job = self.after(700, self._overlay_tick)

    def _overlay_draw(self, cfg, left, top, panel_w, panel_h):
        from PIL import Image, ImageDraw, ImageFont, ImageTk

        win, canvas = self._overlay_win, self._overlay_canvas
        if win is None or canvas is None or panel_w < 40 or panel_h < 40:
            return
        win.geometry(f"{panel_w}x{panel_h}+{left}+{top}")
        canvas.configure(width=panel_w, height=panel_h)

        img = Image.new("RGB", (panel_w, panel_h), self.OVERLAY_KEY)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        for i, (rect, _key, is_point) in enumerate(self._zones_for(cfg, panel_w, panel_h), start=1):
            rx0, ry0, rx1, ry1 = rect
            if is_point:
                # corner brackets only: the button centre must stay click-through
                cx, cy = (rx0 + rx1) // 2, (ry0 + ry1) // 2
                arm = 6
                for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    bx, by = cx + sx * 10, cy + sy * 10
                    draw.line([(bx, by), (bx - sx * arm, by)], fill=(255, 200, 0), width=2)
                    draw.line([(bx, by), (bx, by - sy * arm)], fill=(255, 200, 0), width=2)
            else:
                draw.rectangle([rx0, ry0, rx1, ry1], outline=(255, 40, 40), width=2)
                draw.text((rx0 + 3, max(0, ry0 + 2)), str(i), fill=(255, 40, 40), font=font)
        self._overlay_photo = ImageTk.PhotoImage(img)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=self._overlay_photo)

    # --------------------------------------------------------------- tray/etc
    def _hide_to_tray(self):
        self.withdraw()
        self.tray.start()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            notify.toast(APP_TITLE, self.tr("tray_hint"))

    def restore_from_tray(self):
        self.deiconify()
        self.lift()

    def quit_from_tray(self):
        self._really_exit()

    def _really_exit(self):
        if self.engine.is_running():
            self.engine.stop()
        self.tray.stop()
        self.destroy()

    def _on_close(self):
        self._hide_to_tray()

    # -------------------------------------------------------------- notify/log
    def _banner_show(self, text, color=ACCENT):
        self.banner.configure(text=text, fg_color=color)
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", padx=16, pady=(0, 4), before=self.tabs)
        if self._notif_after:
            self.after_cancel(self._notif_after)
        self._notif_after = self.after(4500, self._banner_hide)

    def _banner_hide(self):
        self.banner.pack_forget()

    def _log(self, message):
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")

    def _notify(self, title, message, kind="info"):
        self._banner_show(message, {"success": GREEN, "error": RED, "warn": AMBER}.get(kind, ACCENT))
        notify.toast(title, message)
        notify.beep(kind)

    # ------------------------------------------------------------------- poll
    def _poll(self):
        if not self._rebuilding:
            for event, data in self.engine.drain():
                self._handle(event, data)
        else:
            self.engine.drain()
        self.after(100, self._poll)

    def _handle(self, event, data):
        if event == "state":
            status = data.get("status", "?")
            if data.get("paused"):
                self.status_pill.configure(text="  PAUSED  ", fg_color=AMBER)
            else:
                pill_color = "#3a3f4d" if status in ("WAIT", "IDLE") else GREEN
                self.status_pill.configure(text=f"  {status}  ", fg_color=pill_color)
            self.stat["power"][0].configure(text=f"{data.get('power_fill', 0) * 100:.0f}%")
            self.stat["power"][1].configure(text=data.get("power_color", ""))
            marker = data.get("marker")
            self.stat["marker"][0].configure(text=f"{marker * 100:.0f}%" if marker and marker >= 0 else "--")
            self.stat["luck"][0].configure(text=f"{data.get('luck_fill', 0) * 100:.0f}%")
            self.stat["fail"][0].configure(text=f"{data.get('fail_fill', 0) * 100:.0f}%")
            self.settings["stats"]["successes"] = data.get("successes", self.settings["stats"]["successes"])
            self.settings["stats"]["failures"] = data.get("failures", self.settings["stats"]["failures"])
            self._update_stat_counters()
        elif event == "log":
            self._log(f"{data.get('level', '')} {data.get('message', '')}".strip())
        elif event == "banner":
            self._banner_show(data.get("text", ""), data.get("color", ACCENT))
        elif event == "error":
            self._log(f"ERROR {data.get('message', '')}")
            self._notify(APP_TITLE, data.get("message", "error"), "error")
        elif event == "craft_success":
            self._update_stat_counters()
            self._notify(APP_TITLE, f"{self.tr('n_craft_success')} ({data.get('successes', 0)})", "success")
        elif event == "craft_fail":
            self._update_stat_counters()
            self._notify(APP_TITLE, self.tr("n_craft_fail"), "error")
        elif event == "paused":
            self.btn_pause.configure(text=self.tr("btn_resume") if data.get("paused") else self.tr("btn_pause"))
        elif event == "waiting_input":
            self._notify(APP_TITLE, self.tr("n_add_ingredients"), "warn")
        elif event in ("stopped", "engine_stopped"):
            self._set_running_ui(False)
            reason = data.get("reason", "stopped")
            self.settings = self.engine.settings
            self._update_stat_counters()
            if reason == "target_reached":
                self._notify(APP_TITLE, self.tr("n_target_reached", v=data.get("successes", 0)), "success")
            elif reason == "craft_failed":
                self._notify(APP_TITLE, self.tr("n_craft_failed_stop"), "error")
            else:
                self._banner_show(f"Stopped ({reason})", "#3a3f4d")
        elif event == "calib_done":
            self._on_calib_done(data)
        elif event == "calib_btn_done":
            self.deiconify()
            self.btn_calibbtn.configure(state="normal", text=self.tr("btn_calib_buttons"))
        elif event == "reanchored":
            self._banner_show(
                self.tr("reanchored", x=data.get("x", 0), y=data.get("y", 0)), GREEN)


def main():
    app = App()
    if "--open-editor" in sys.argv:
        app.after(2000, app._auto_open_editor)
    app.mainloop()


if __name__ == "__main__":
    main()
