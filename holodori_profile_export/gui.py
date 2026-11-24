from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from importlib.resources import files
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog

from . import __version__, hoshidori, settings, text
from .client import GameClient
from .sources import credential, steam, transfer

ctk.set_appearance_mode("dark")


def _set_macos_app_name(name: str) -> None:
    """When run as `python main.py` on macOS the menu bar says "Python"; rename it (needs pyobjc)."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # pyobjc-framework-Cocoa

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
    except Exception:
        pass


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _blend_titlebar(window, bg: str, fg: str) -> None:
    """Color the system title bar like the app so the window looks borderless, while keeping the
    real title bar (dragging, buttons, resizing, taskbar/Dock all keep working). Best effort:
    Windows 11 takes the exact color, Windows 10 only goes dark, macOS shows the app background
    through a transparent title bar, Linux is left to the desktop."""
    try:
        if sys.platform == "win32":
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            dwm = ctypes.windll.dwmapi

            def put(attr: int, value: int) -> int:
                v = ctypes.c_int(value)
                return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

            if put(20, 1) != 0:  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on early Windows 10 builds)
                put(19, 1)

            def colorref(color: str) -> int:
                r, g, b = _hex_rgb(color)
                return r | (g << 8) | (b << 16)

            put(35, colorref(bg))  # DWMWA_CAPTION_COLOR  (Windows 11; ignored on 10)
            put(36, colorref(fg))  # DWMWA_TEXT_COLOR
            put(34, colorref(bg))  # DWMWA_BORDER_COLOR
        elif sys.platform == "darwin":
            from AppKit import NSApp, NSAppearance, NSColor  # pyobjc-framework-Cocoa

            r, g, b = (v / 255 for v in _hex_rgb(bg))
            color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            for w in NSApp.windows():
                if not w.isVisible() or w.isSheet():
                    continue
                w.setAppearance_(dark)
                w.setTitlebarAppearsTransparent_(True)
                w.setTitleVisibility_(1)  # NSWindowTitleHidden: the sidebar already shows the name
                w.setBackgroundColor_(color)
    except Exception:
        pass


class C:
    """Colors: a Kobo Kanaeru theme (deep rain-navy with Kobo cyan)."""

    BG = "#0b151f"          # page
    PANEL = "#141f2c"       # cards / sidebar
    SOFT = "#111b28"        # tiles inside cards
    CONTROL = "#1a2735"     # inputs, ghost buttons
    LINE = "#293245"        # hairlines
    EDGE = "#24434f"        # teal-tinted card edge
    TEXT = "#e7eaf3"
    MUTED = "#a4aec3"
    FAINT = "#6f7a90"
    ACCENT = "#62d4e4"      # Kobo cyan
    ACCENT_HOVER = "#4cc2d4"
    ACCENT_SOFT = "#1d3542" # selected / active
    ACCENT_SEG = "#245b67"  # selected segment
    ON_ACCENT = "#0b151f"
    OK = "#f0757a"          # Kobo coral red (highlights, done), between pale pink and her artwork's red
    OK_SOFT = "#3b2328"     # faint coral tint (selected card, hovers)
    WHITE = "#f4f6fa"       # main buttons (Kobo's white jacket)
    WHITE_HOVER = "#dde3ec"
    WARN = "#f4c16f"
    WARN_SOFT = "#2c2416"
    ERR = "#ff5c6c"         # errors: a stronger red than the pale "done" red
    ERR_SOFT = "#3d1a22"
    # kept so older code paths still resolve
    SURFACE = PANEL
    SURFACE_HI = CONTROL
    BORDER = LINE
    BORDER_HI = "#3a4660"
    SIDEBAR = PANEL


# One font family everywhere: Inter if it's installed, otherwise the system's own UI font
# (whatever Tk uses for TkDefaultFont: SF on macOS, Segoe UI on Windows). Picked in App.__init__.
_FAMILY = "Segoe UI" if sys.platform == "win32" else "Helvetica" if sys.platform == "darwin" else "DejaVu Sans"


def _pick_fonts() -> None:
    global _FAMILY
    try:
        from tkinter import font as tkfont

        fams = set(tkfont.families())
        chosen = next((f for f in ("Inter", "Inter Variable", "Inter Display") if f in fams), None)
        if chosen is None:
            chosen = tkfont.nametofont("TkDefaultFont").actual("family")
        if chosen:
            _FAMILY = chosen
        # make widgets that use customtkinter's built-in default font match as well
        theme_font = ctk.ThemeManager.theme.get("CTkFont", {})
        for key in ("macOS", "Windows", "Linux"):
            if isinstance(theme_font.get(key), dict):
                theme_font[key]["family"] = _FAMILY
    except Exception:
        pass


def F(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_FAMILY, size=size, weight=weight)


def M(size: int = 11, weight: str = "bold") -> ctk.CTkFont:
    """Small caps labels (eyebrows, section headings): same family, just small and bold."""
    return ctk.CTkFont(family=_FAMILY, size=size, weight=weight)


_SOURCES = [
    ("steam", text.UI_SRC_STEAM, text.UI_SRC_STEAM_DESC, "▶"),
    ("transfer", text.UI_SRC_TRANSFER, text.UI_SRC_TRANSFER_DESC, "⇄"),
    ("credential", text.UI_SRC_CREDENTIAL, text.UI_SRC_CREDENTIAL_DESC, "\u2630"),  # ☰ three-bar icon
]
_STEPS = [text.UI_STEP_METHOD, text.UI_STEP_ACCOUNT, text.UI_STEP_EXPORT]


def _open_folder(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self) -> None:
        _set_macos_app_name(text.GUI_TITLE)  # must happen before the Tk window exists
        super().__init__(fg_color=C.BG)
        try:
            self.tk.call("tk", "appname", text.GUI_TITLE)
        except Exception:
            pass
        _pick_fonts()
        self.title(text.GUI_TITLE)
        self.geometry("1000x720")  # open at the minimum; _fit_window grows it only if the content needs more
        self.minsize(1000, 720)
        self._set_icon()
        self.after(250, self._set_icon)
        self.after(50, lambda: _blend_titlebar(self, C.BG, C.TEXT))
        self.after(400, lambda: _blend_titlebar(self, C.BG, C.TEXT))  # again once the window is on screen

        self.q: queue.Queue = queue.Queue()
        saved = settings.load()  # remembered from the last run (never holds a sign-in)
        self.source = "steam"
        self.region = saved.get("region") if saved.get("region") in ("us", "as", "jp") else "us"
        out = saved.get("out_dir")
        self.out_dir = out if isinstance(out, str) and os.path.isdir(out) else os.getcwd()
        self.hdb = ctk.BooleanVar(value=True)
        self.hsdr = ctk.BooleanVar(value=True)
        self.hsdr_base = ""
        sd = saved.get("steam_dir")
        self.steam_dir = sd if isinstance(sd, str) and os.path.isdir(sd) else ""  # empty = found automatically

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(
            self, width=226, corner_radius=18, fg_color=C.PANEL, border_width=1, border_color=C.EDGE
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(14, 0), pady=14)
        self.sidebar.grid_propagate(False)
        self.sidebar.pack_propagate(False)
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color=C.BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self._build_sidebar()
        self._step_source()
        self._fit_job = None
        self.bind("<Configure>", self._on_resize, add="+")

    # ------------------------------------------------------------ chrome

    def _set_icon(self) -> None:
        # The artwork is stored as WebP to keep the repo small; Pillow turns it into a Tk icon.
        try:
            from PIL import Image, ImageTk

            path = files("holodori_profile_export") / "assets" / "icon.webp"
            with Image.open(str(path)) as im:
                rgba = im.convert("RGBA")
                self._icon = ImageTk.PhotoImage(rgba.resize((256, 256), Image.LANCZOS))
                if sys.platform == "win32" and not getattr(self, "_ico_set", False):
                    # Windows' title bar and taskbar use an .ico. Setting one also stops customtkinter
                    # from putting its own logo there a moment after the window opens.
                    import tempfile

                    ico = os.path.join(tempfile.gettempdir(), "holodori-exporter-icon.ico")
                    rgba.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
                    self.iconbitmap(ico)
                    self._ico_set = True
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    def _logo_image(self, size: int):
        """The round app picture for the sidebar (needs Pillow); None if it can't be loaded.

        Resized once, straight from the 192px original to the exact pixel size it's drawn at, then
        lightly sharpened, so it doesn't go soft from being scaled twice.
        """
        try:
            from PIL import Image, ImageFilter

            try:
                scale = float(ctk.ScalingTracker.get_widget_scaling(self))
            except Exception:
                scale = 1.0
            px = max(1, round(size * scale))
            path = files("holodori_profile_export") / "assets" / "logo.webp"
            with Image.open(str(path)) as im:
                img = im.convert("RGBA").resize((px, px), Image.LANCZOS)
            img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=1))
            self._logo = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
            return self._logo
        except Exception:
            return None

    def _build_sidebar(self) -> None:
        sb = self.sidebar
        head = ctk.CTkFrame(sb, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(20, 0))
        logo = self._logo_image(34)
        if logo is not None:
            ctk.CTkLabel(head, text="", image=logo, width=34, height=34).pack(side="left")
        else:
            ctk.CTkLabel(head, text="\u2602", font=F(18, "bold"), text_color=C.ACCENT).pack(side="left")
        ctk.CTkLabel(head, text=text.UI_APP_NAME, font=F(15, "bold"), text_color=C.OK).pack(
            side="left", padx=(8, 0)
        )
        ctk.CTkLabel(
            sb, text=text.UI_APP_TAGLINE, font=F(12), text_color=C.MUTED,
            justify="left", anchor="w", wraplength=184,
        ).pack(fill="x", padx=18, pady=(8, 22))

        ctk.CTkLabel(sb, text=text.UI_NAV_SECTION, font=M(11), text_color=C.MUTED, anchor="w").pack(
            fill="x", padx=18, pady=(0, 6)
        )
        self._step_rows: list[tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]] = []
        for i, name in enumerate(_STEPS):
            row = ctk.CTkFrame(sb, fg_color="transparent", corner_radius=12, height=40)
            row.pack(fill="x", padx=10, pady=2)
            badge = ctk.CTkLabel(
                row, text=str(i + 1), width=22, height=22, corner_radius=11,
                font=F(11, "bold"), fg_color=C.CONTROL, text_color=C.MUTED,
            )
            badge.pack(side="left", padx=(10, 0), pady=9)
            lbl = ctk.CTkLabel(row, text=name, font=F(13), text_color=C.MUTED, anchor="w")
            lbl.pack(side="left", padx=(10, 0))
            self._step_rows.append((row, badge, lbl))

        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=18, pady=18)
        ctk.CTkFrame(foot, height=1, fg_color=C.LINE).pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            foot, text=text.UI_PRIVACY, font=F(11), text_color=C.FAINT,
            justify="left", anchor="w", wraplength=186,
        ).pack(fill="x")
        ctk.CTkLabel(foot, text=f"v{__version__}", font=M(10, "normal"), text_color=C.FAINT, anchor="w").pack(
            fill="x", pady=(8, 0)
        )
        ctk.CTkLabel(
            foot, text=text.UI_LAST_UPDATED, font=F(10), text_color=C.FAINT, justify="left", anchor="w",
        ).pack(fill="x", pady=(2, 0))

    def _set_step(self, active: int, done_upto: int | None = None) -> None:
        done_upto = active if done_upto is None else done_upto
        for i, (row, badge, lbl) in enumerate(self._step_rows):
            if i < done_upto:
                row.configure(fg_color="transparent")
                badge.configure(text="\u2713", fg_color=C.OK_SOFT, text_color=C.OK)
                lbl.configure(text_color=C.MUTED, font=F(13))
            elif i == active:
                row.configure(fg_color=C.ACCENT_SOFT)
                badge.configure(text=str(i + 1), fg_color=C.ACCENT, text_color=C.ON_ACCENT)
                lbl.configure(text_color=C.ACCENT, font=F(13, "bold"))
            else:
                row.configure(fg_color="transparent")
                badge.configure(text=str(i + 1), fg_color=C.CONTROL, text_color=C.MUTED)
                lbl.configure(text_color=C.MUTED, font=F(13))

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, corner_radius=18, fg_color=C.PANEL, border_width=1, border_color=C.EDGE, **kw)

    _BASE_MIN = (1000, 720)

    def _fit_window(self) -> None:
        """Never let the window be smaller than its content needs, so nothing gets cut off.
        Measured from the widgets themselves, so it follows text length and wrapping."""
        self._fit_job = None
        try:
            self.update_idletasks()
            need_w = max(self._BASE_MIN[0], self.winfo_reqwidth())
            need_h = max(self._BASE_MIN[1], self.winfo_reqheight())
            self.minsize(need_w, need_h)
            cur_w, cur_h = self.winfo_width(), self.winfo_height()
            if cur_w > 1 and (cur_w < need_w or cur_h < need_h):
                self.geometry(f"{max(cur_w, need_w)}x{max(cur_h, need_h)}")
        except Exception:
            pass

    def _on_resize(self, event) -> None:
        if event.widget is self and self._fit_job is None:
            self._fit_job = self.after(120, self._fit_window)  # after text re-wraps to the new width

    def _autowrap(self, label, pad: int) -> None:
        """Wrap a label's text to the current width of its container (minus pad), not a fixed width."""
        holder = label.master

        def on_configure(e):
            try:
                scale = float(ctk.ScalingTracker.get_widget_scaling(label))
            except Exception:
                scale = 1.0
            width = max(80, int(e.width / scale) - pad)
            if getattr(label, "_wrap_now", None) != width:
                label._wrap_now = width
                label.configure(wraplength=width)

        holder.bind("<Configure>", on_configure, add="+")

    def _screen(self, step: int, title: str, sub: str = "", *, done_upto: int | None = None, scroll: bool = False):
        """Clear the main area; return (body, footer). body sits inside a rounded content card."""
        for w in self.main.winfo_children():
            w.destroy()
        self.after_idle(self._fit_window)  # runs once this screen's widgets are all built
        self._set_step(step, done_upto)
        wrap = ctk.CTkFrame(self.main, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=(14, 14), pady=14)

        head = self._card(wrap)
        head.pack(fill="x")
        eyebrow = text.UI_EYEBROW.format(n=min(step + 1, len(_STEPS)), total=len(_STEPS), name=_STEPS[min(step, len(_STEPS) - 1)])
        ctk.CTkLabel(head, text=eyebrow.upper(), font=M(11), text_color=C.ACCENT, anchor="w").pack(
            fill="x", padx=22, pady=(14, 0)
        )
        ctk.CTkLabel(head, text=title, font=F(26, "bold"), text_color=C.TEXT, anchor="w").pack(fill="x", padx=22)
        sub_lbl = ctk.CTkLabel(
            head, text=sub or " ", font=F(13), text_color=C.MUTED, anchor="w", justify="left", wraplength=600,
        )
        sub_lbl.pack(fill="x", padx=22, pady=(0, 14))
        self._autowrap(sub_lbl, 48)

        footer = ctk.CTkFrame(wrap, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        content = self._card(wrap)
        content.pack(fill="both", expand=True, pady=(12, 0))
        if scroll:
            body = ctk.CTkScrollableFrame(
                content, fg_color="transparent", scrollbar_button_color=C.LINE,
                scrollbar_button_hover_color=C.EDGE,
            )
            body.pack(fill="both", expand=True, padx=12, pady=12)
        else:
            body = ctk.CTkFrame(content, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=20, pady=18)
        return body, footer

    # ------------------------------------------------------------ small widgets

    def _primary(self, parent, label: str, command, width: int = 150):
        return ctk.CTkButton(
            parent, text=label, command=command, width=width, height=42, corner_radius=12,
            font=F(13, "bold"), fg_color=C.WHITE, hover_color=C.WHITE_HOVER, text_color=C.ON_ACCENT,
        )

    def _ghost(self, parent, label: str, command, width: int = 110):
        return ctk.CTkButton(
            parent, text=label, command=command, width=width, height=42, corner_radius=12,
            font=F(13), fg_color=C.CONTROL, hover_color=C.ACCENT_SOFT, text_color=C.TEXT,
            border_width=1, border_color=C.LINE,
        )

    def _label(self, parent, label: str, *, pady=(0, 6)):
        ctk.CTkLabel(parent, text=label.upper(), font=M(11), text_color=C.ACCENT, anchor="w").pack(
            fill="x", pady=pady
        )

    def _entry(self, parent, placeholder: str = "", show: str = ""):
        return ctk.CTkEntry(
            parent, placeholder_text=placeholder, show=show, height=44, corner_radius=12,
            font=F(13), fg_color=C.CONTROL, border_color=C.LINE, border_width=1,
            text_color=C.TEXT, placeholder_text_color=C.FAINT,
        )

    def _notice(self, parent, message: str, kind: str = "info"):
        fg, bd, tx, icon = {
            "info": (C.ACCENT_SOFT, C.EDGE, C.ACCENT, "i"),
            "warn": (C.WARN_SOFT, C.WARN_SOFT, C.WARN, "!"),
            "err": (C.ERR_SOFT, C.ERR_SOFT, C.ERR, "×"),
            "ok": (C.OK_SOFT, C.OK_SOFT, C.OK, "✓"),
        }[kind]
        box = ctk.CTkFrame(parent, fg_color=fg, corner_radius=12, border_width=1, border_color=bd)
        ctk.CTkLabel(box, text=icon, font=F(13, "bold"), text_color=tx, width=18).pack(
            side="left", anchor="n", padx=(14, 0), pady=12
        )
        ctk.CTkLabel(
            box, text=message, font=F(12), text_color=C.TEXT if kind == "info" else tx,
            justify="left", anchor="w", wraplength=580,
        ).pack(side="left", fill="x", expand=True, padx=(8, 14), pady=12)
        return box

    # ------------------------------------------------------------ step 1: method + options

    def _step_source(self) -> None:
        body, footer = self._screen(0, text.UI_S1_TITLE, text.UI_S1_SUB)

        # sign-in method: three tiles side by side
        self._label(body, text.UI_LABEL_METHOD)
        tiles = ctk.CTkFrame(body, fg_color="transparent")
        tiles.pack(fill="x")
        self._cards: dict[str, ctk.CTkFrame] = {}
        for col, (key, title, desc, glyph) in enumerate(_SOURCES):
            tiles.grid_columnconfigure(col, weight=1, uniform="src")
            card = self._source_card(tiles, key, title, desc, glyph)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 0 if col == 2 else 5))
            self._cards[key] = card
        self._paint_cards()

        # region + save folder on one row
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(16, 0))
        row.grid_columnconfigure(1, weight=1)
        reg = ctk.CTkFrame(row, fg_color="transparent")
        reg.grid(row=0, column=0, sticky="nw", padx=(0, 18))
        self._label(reg, text.UI_LABEL_REGION)
        self._region = ctk.CTkSegmentedButton(
            reg, values=["US", "AS", "JP"], height=42, corner_radius=12, font=F(13, "bold"),
            fg_color=C.CONTROL, selected_color=C.ACCENT_SEG, selected_hover_color=C.EDGE,
            unselected_color=C.CONTROL, unselected_hover_color=C.ACCENT_SOFT, text_color=C.TEXT,
        )
        self._region.set(self.region.upper())
        self._region.pack(anchor="w")
        out = ctk.CTkFrame(row, fg_color="transparent")
        out.grid(row=0, column=1, sticky="new")
        self._label(out, text.UI_LABEL_SAVETO)
        orow = ctk.CTkFrame(out, fg_color="transparent")
        orow.pack(fill="x")
        self._out_entry = self._entry(orow)
        self._out_entry.insert(0, self.out_dir)
        self._out_entry.pack(side="left", fill="x", expand=True)
        self._ghost(orow, text.GUI_BTN_BROWSE, self._browse, width=92).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row, text=text.UI_REGION_HINT, font=F(11), text_color=C.FAINT, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        self._hsdr_panel(body)
        self._primary(footer, text.UI_BTN_CONTINUE + "  \u2192", self._from_source).pack(side="right")

    def _source_card(self, parent, key: str, title: str, desc: str, glyph: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=12, border_width=1, fg_color=C.SOFT, border_color=C.LINE)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        # macOS: the icon is drawn as vector shapes on a Tk canvas (exactly centered, Retina-sharp).
        # Elsewhere: a text glyph (Tk's canvas isn't anti-aliased on Windows/Linux).
        mac = sys.platform == "darwin"
        icon = ctk.CTkLabel(
            top, text="" if mac else glyph, width=32, height=32, corner_radius=10,
            font=F(14, "bold"), fg_color=C.CONTROL, text_color=C.ACCENT,
        )
        icon.pack(side="left")
        card._shape = self._icon_shape(icon, key) if mac else None  # type: ignore[attr-defined]
        t = ctk.CTkLabel(card, text=title, font=F(14, "bold"), text_color=C.TEXT, anchor="w")
        t.pack(fill="x", padx=14, pady=(10, 0))
        d = ctk.CTkLabel(card, text=desc, font=F(12), text_color=C.MUTED, anchor="nw", justify="left", wraplength=200)
        d.pack(fill="both", expand=True, padx=14, pady=(2, 12))
        self._autowrap(d, 32)
        card._icon = icon  # type: ignore[attr-defined]
        card._title = t  # type: ignore[attr-defined]
        for w in (card, top, icon, t, d):
            w.bind("<Button-1>", lambda _e, k=key: self._pick_source(k))
        return card

    def _icon_shape(self, holder, kind: str):
        """Draw a sign-in method icon (16x16) on a Tk canvas centered in its square. macOS only."""
        import tkinter as tk

        cv = tk.Canvas(holder, width=16, height=16, bg=C.CONTROL, highlightthickness=0, bd=0)
        cv.place(relx=0.5, rely=0.5, anchor="center")
        items = []  # (item, option to recolor)
        if kind == "steam":  # play triangle, nudged right so it looks centered
            items.append((cv.create_polygon(5, 2.5, 5, 13.5, 13.5, 8, outline=""), "fill"))
        elif kind == "transfer":  # two opposite arrows
            for y, x1, x2 in ((5, 2, 14), (11, 14, 2)):
                items.append((cv.create_line(x1, y, x2, y, width=1.6, arrow="last", arrowshape=(4, 5, 2.5)), "fill"))
        else:  # three bars
            for y in (4, 8, 12):
                items.append((cv.create_line(3, y, 13, y, width=1.8, capstyle="round"), "fill"))
        cv.bind("<Button-1>", lambda _e, k=kind: self._pick_source(k))
        return cv, items

    def _pick_source(self, key: str) -> None:
        self.source = key
        self._paint_cards()

    def _paint_cards(self) -> None:
        for key, card in self._cards.items():
            on = key == self.source
            # blue highlight like the rest of the app
            card.configure(fg_color=C.ACCENT_SOFT if on else C.SOFT, border_color=C.ACCENT if on else C.LINE)
            card._icon.configure(fg_color=C.EDGE if on else C.CONTROL, text_color=C.ACCENT)  # type: ignore[attr-defined]
            if card._shape:  # type: ignore[attr-defined]
                cv, items = card._shape  # type: ignore[attr-defined]
                cv.configure(bg=C.EDGE if on else C.CONTROL)
                for item, kind in items:
                    cv.itemconfigure(item, **{kind: C.ACCENT})
            card._title.configure(text_color=C.ACCENT if on else C.TEXT)  # type: ignore[attr-defined]

    def _make_toggle(self, parent, var, color: str, bg: str = C.SOFT):
        """On/off toggle in the target's color: a pill with a white circle inside it.

        macOS: drawn on a plain Tk canvas, which macOS renders smoothly and Retina-sharp with exact
        circles (customtkinter's own shapes there either clip a pixel or leave artifacts).
        Windows/Linux: two customtkinter frames, whose default drawing is exact and smooth there."""
        w, h, pad = 42, 24, 3
        d = h - 2 * pad

        def flip(_e=None):
            var.set(not bool(var.get()))
            self._paint_targets()

        if sys.platform == "darwin":
            import tkinter as tk

            cv = tk.Canvas(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
            # one thick line with round caps = the whole pill in a single shape (no seams between parts)
            track = [cv.create_line(h / 2, h / 2, w - h / 2, h / 2, width=h, capstyle="round")]
            knob = cv.create_oval(0, 0, d, d, fill="#ffffff", outline="")

            def repaint(*_):
                on = bool(var.get())
                for item in track:
                    cv.itemconfigure(item, fill=color if on else C.BORDER_HI)
                x = (w - pad - d) if on else pad
                cv.coords(knob, x, pad, x + d, pad + d)

            var.trace_add("write", repaint)  # also follows changes made elsewhere
            cv.bind("<Button-1>", flip)
            repaint()
            return cv

        pill = ctk.CTkFrame(parent, width=w, height=h, corner_radius=h // 2, fg_color=C.BORDER_HI, cursor="hand2")
        pill.pack_propagate(False)
        knob = ctk.CTkFrame(pill, width=d, height=d, corner_radius=d // 2, fg_color="#ffffff", cursor="hand2")

        def repaint(*_):
            on = bool(var.get())
            pill.configure(fg_color=color if on else C.BORDER_HI)
            knob.place(x=(w - pad - d) if on else pad, y=pad)

        var.trace_add("write", repaint)  # also follows changes made elsewhere
        for wdg in (pill, knob):
            wdg.bind("<Button-1>", flip)
        repaint()
        return pill

    def _target_tile(self, parent, var, title: str, desc: str, badge: str, color: str):
        tile = ctk.CTkFrame(parent, corner_radius=12, border_width=1, fg_color=C.SOFT, border_color=C.EDGE)
        top = ctk.CTkFrame(tile, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            top, text=badge, width=58, height=24, corner_radius=7, font=F(11, "bold"),
            fg_color=C.CONTROL, text_color=color,
        ).pack(side="left")
        ctk.CTkLabel(top, text=title, font=F(14, "bold"), text_color=C.TEXT, anchor="w").pack(
            side="left", padx=(10, 0)
        )
        self._make_toggle(top, var, color).pack(side="right")
        desc_lbl = ctk.CTkLabel(
            tile, text=desc, font=F(12), text_color=C.MUTED, anchor="nw", justify="left", wraplength=300,
        )
        desc_lbl.pack(fill="x", padx=14, pady=(8, 12))
        self._autowrap(desc_lbl, 32)
        return tile

    def _hsdr_panel(self, parent) -> None:
        self._label(parent, text.UI_LABEL_EXPORT_FOR, pady=(16, 6))
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="tgt")
        hdb = self._target_tile(grid, self.hdb, text.UI_TGT_HDB, text.UI_TGT_HDB_DESC, ".hdbest", C.ACCENT)
        hdb.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        hs = self._target_tile(grid, self.hsdr, text.UI_TGT_HSDR, text.UI_TGT_HSDR_DESC, ".json", C.OK)
        hs.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        base_row = ctk.CTkFrame(hs, fg_color="transparent")
        base_row.pack(fill="x", padx=14, pady=(0, 12))
        self._base_label = ctk.CTkLabel(base_row, text="", font=F(11), text_color=C.MUTED, anchor="w",
                                        justify="left", wraplength=170)
        self._base_label.pack(side="left", fill="x", expand=True)
        self._base_btn = ctk.CTkButton(
            base_row, text=text.UI_HSDR_BASE_BTN, command=self._pick_base, width=170, height=30, corner_radius=10,
            font=F(12), fg_color=C.CONTROL, hover_color=C.ACCENT_SOFT, text_color=C.TEXT,
            border_width=1, border_color=C.LINE,
        )
        self._base_btn.pack(side="right")
        self._target_err = ctk.CTkLabel(parent, text="", font=F(12), text_color=C.ERR, anchor="w")
        self._target_err.pack(fill="x", pady=(4, 0))
        self._paint_targets()

    def _paint_targets(self) -> None:
        self._paint_base(None)
        if self.hdb.get() or self.hsdr.get():
            self._target_err.configure(text="")

    def _paint_base(self, row) -> None:
        on = bool(self.hsdr.get())
        name = Path(self.hsdr_base).name if self.hsdr_base else ""
        self._base_label.configure(
            text=(text.UI_HSDR_BASE_SET.format(name=name) if name else text.UI_HSDR_BASE_NONE) if on else text.UI_HSDR_OFF_SHORT,
            text_color=C.MUTED if on else C.FAINT,
        )
        self._base_btn.configure(state="normal" if on else "disabled")

    def _pick_base(self) -> None:
        p = filedialog.askopenfilename(
            title=text.UI_HSDR_BASE_BTN, filetypes=[("HoshiDori export", "*.json"), ("All files", "*.*")]
        )
        if p:
            self.hsdr_base = p
            self._paint_base(None)

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self._out_entry.get() or os.getcwd())
        if d:
            self._out_entry.delete(0, "end")
            self._out_entry.insert(0, d)

    def _from_source(self) -> None:
        if not (self.hdb.get() or self.hsdr.get()):
            self._target_err.configure(text=text.UI_ERR_PICK_ONE)
            return
        self.region = (self._region.get() or "US").lower()
        self.out_dir = self._out_entry.get().strip() or os.getcwd()
        settings.save(region=self.region, out_dir=self.out_dir if os.path.isdir(self.out_dir) else None)
        self._step_details()

    # ------------------------------------------------------------ step 2: account details

    def _step_details(self) -> None:
        if self.source == "steam":
            body, footer = self._screen(1, text.UI_STEAM_TITLE, text.UI_STEAM_SUB)
            self._notice(body, text.UI_SIGNOUT_NOTE, "warn").pack(fill="x", pady=(0, 10))
            self._notice(body, text.UI_STEAM_NOTE, "info").pack(fill="x", pady=(0, 14))
            self._steam_folder_row(body)
            self._summary(body)
        elif self.source == "credential":
            body, footer = self._screen(1, text.UI_CRED_TITLE, text.UI_CRED_SUB)
            self._notice(body, text.UI_SIGNOUT_NOTE, "warn").pack(fill="x", pady=(0, 16))
            self._label(body, text.GUI_CRED_FIELD)
            self._cred = self._entry(body, text.UI_CRED_PLACEHOLDER, show="•")
            self._cred.pack(fill="x")
            self._show_toggle(body, self._cred)
            self._err = ctk.CTkLabel(body, text="", font=F(12), text_color=C.ERR, anchor="w")
            self._err.pack(fill="x", pady=(8, 0))
            self._summary(body)
        else:
            body, footer = self._screen(1, text.UI_TRANSFER_TITLE, text.UI_TRANSFER_SUB)
            self._notice(body, text.UI_SIGNOUT_NOTE, "warn").pack(fill="x", pady=(0, 16))
            self._label(body, text.GUI_TRANSFER_FIELD_ID)
            self._mig = self._entry(body, "ABCD1234")
            self._mig.pack(fill="x", pady=(0, 14))
            self._label(body, text.GUI_TRANSFER_FIELD_PW)
            self._pwd = self._entry(body, "•" * 8, show="•")
            self._pwd.pack(fill="x")
            self._show_toggle(body, self._pwd)
            self._err = ctk.CTkLabel(body, text="", font=F(12), text_color=C.ERR, anchor="w")
            self._err.pack(fill="x", pady=(8, 0))
        self._primary(footer, text.UI_BTN_START, self._run, width=170).pack(side="right")
        self._ghost(footer, "←  " + text.GUI_BTN_BACK, self._step_source).pack(side="left")

    def _show_toggle(self, parent, entry) -> None:
        var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            parent, text=text.UI_SHOW, variable=var, font=F(12), text_color=C.MUTED,
            checkbox_width=16, checkbox_height=16, corner_radius=4, border_width=1,
            fg_color=C.ACCENT, hover_color=C.ACCENT_HOVER, border_color=C.BORDER_HI, checkmark_color=C.ON_ACCENT,
            command=lambda: entry.configure(show="" if var.get() else "•"),
        ).pack(anchor="w", pady=(8, 0))

    def _steam_folder_row(self, parent) -> None:
        """Shows where the saved sign-in is read from, with a button to pick another folder."""
        self._label(parent, text.UI_STEAM_FOLDER)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        path_lbl = ctk.CTkLabel(row, text="", font=F(12), text_color=C.TEXT, anchor="w", justify="left", wraplength=480)
        path_lbl.pack(side="left", fill="x", expand=True)
        self._ghost(row, text.UI_STEAM_FOLDER_BTN, self._pick_steam_dir, width=110).pack(side="right", padx=(12, 0))
        hint = ctk.CTkLabel(parent, text="", font=F(11), anchor="w", justify="left", wraplength=560)
        hint.pack(fill="x", pady=(4, 10))
        self._autowrap(hint, 8)
        self._steam_lbls = (path_lbl, hint)
        self._paint_steam_dir()

    def _paint_steam_dir(self) -> None:
        path_lbl, hint = self._steam_lbls
        folder = steam.game_data_dir(self.steam_dir or None)
        path_lbl.configure(text=str(folder))
        if folder.is_dir():
            hint.configure(text=text.UI_STEAM_FOLDER_HINT, text_color=C.FAINT)
        else:
            hint.configure(text=text.UI_STEAM_FOLDER_MISSING, text_color=C.WARN)

    def _pick_steam_dir(self) -> None:
        start = steam.game_data_dir(self.steam_dir or None)
        d = filedialog.askdirectory(
            title=text.UI_STEAM_FOLDER, initialdir=str(start if start.is_dir() else Path.home()), mustexist=True
        )
        if d:
            self.steam_dir = d
            settings.save(steam_dir=d)
            self._paint_steam_dir()

    def _summary(self, parent) -> None:
        box = ctk.CTkFrame(parent, corner_radius=12, border_width=1, fg_color=C.SOFT, border_color=C.LINE)
        box.pack(fill="x", pady=(16, 0))
        rows = [
            (text.UI_SUM_REGION, self.region.upper()),
            (text.UI_SUM_SAVETO, self.out_dir),
            (text.UI_SUM_FILES, "  +  ".join(
                n for n, on in ((".hdbest", self.hdb.get()), (".hoshidori.json", self.hsdr.get())) if on
            )),
        ]
        for i, (k, v) in enumerate(rows):
            r = ctk.CTkFrame(box, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=(12 if i == 0 else 4, 12 if i == len(rows) - 1 else 0))
            ctk.CTkLabel(r, text=k, font=F(12), text_color=C.MUTED, width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=v, font=F(12), text_color=C.TEXT, anchor="w", justify="left", wraplength=420).pack(
                side="left", fill="x", expand=True
            )

    # ------------------------------------------------------------ step 3: run

    def _run(self) -> None:
        mig = pwd = cred = ""
        if self.source == "transfer":
            mig, pwd = self._mig.get().strip(), self._pwd.get()
            if len(mig) != 8:
                self._err.configure(text=text.ERR_BAD_LINK_ID)
                return
            if not 8 <= len(pwd) <= 16:
                self._err.configure(text=text.ERR_BAD_PASSWORD)
                return
        elif self.source == "credential":
            cred = self._cred.get().strip()
            if not cred:
                self._err.configure(text=text.UI_CRED_EMPTY)
                return

        body, footer = self._screen(2, text.UI_WORKING_TITLE, text.UI_WORKING_SUB, scroll=False)
        self._bar = ctk.CTkProgressBar(
            body, mode="indeterminate", height=6, corner_radius=3, fg_color=C.CONTROL, progress_color=C.ACCENT
        )
        self._bar.pack(fill="x")
        self._bar.start()
        self._status = ctk.CTkLabel(body, text=text.UI_STATUS_START, font=F(14, "bold"), text_color=C.TEXT, anchor="w")
        self._status.pack(fill="x", pady=(16, 10))
        self._log = ctk.CTkTextbox(
            body, height=220, corner_radius=12, border_width=1, border_color=C.LINE,
            fg_color=C.SOFT, text_color=C.MUTED, font=F(12),
        )
        self._log.pack(fill="both", expand=True)
        self._log.configure(state="disabled")

        threading.Thread(target=self._worker, args=(mig, pwd, cred), daemon=True).start()
        self.after(100, self._poll)

    def _worker(self, mig: str, pwd: str, cred: str) -> None:
        echo = lambda s: self.q.put(("log", s))  # noqa: E731
        try:
            with GameClient(self.region) as client:
                if self.source == "steam":
                    _, dest = steam.run(
                        client, self.out_dir, echo=echo, hdbest=bool(self.hdb.get()), data_dir=self.steam_dir or None
                    )
                elif self.source == "credential":
                    _, dest = credential.run(client, cred, self.out_dir, echo=echo, hdbest=bool(self.hdb.get()))
                else:
                    _, dest = transfer.run(client, mig, pwd, self.out_dir, echo=echo, hdbest=bool(self.hdb.get()))
                hsdr = None
                if self.hsdr.get():
                    _, hsdr = hoshidori.export(client, self.out_dir, self.hsdr_base or None, echo=echo)
            self.q.put(("done", {"dest": str(dest) if dest else None, "transfer": self.source == "transfer", "hsdr": hsdr}))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", str(e)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._status.configure(text=payload)
                    self._log.configure(state="normal")
                    self._log.insert("end", "› " + payload + "\n")
                    self._log.see("end")
                    self._log.configure(state="disabled")
                elif kind == "done":
                    self._show_done(payload)
                    return
                elif kind == "error":
                    self._show_error(payload)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ------------------------------------------------------------ results

    def _file_card(self, parent, badge: str, color: str, name: str, where: str, hint: str) -> None:
        card = ctk.CTkFrame(parent, corner_radius=12, border_width=1, fg_color=C.SOFT, border_color=C.LINE)
        card.pack(fill="x", pady=5)
        ctk.CTkLabel(
            card, text=badge, width=64, height=28, corner_radius=8, font=F(11, "bold"),
            fg_color=C.CONTROL, text_color=color,
        ).pack(side="left", padx=(14, 12), pady=14, anchor="n")
        txt = ctk.CTkFrame(card, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True, pady=12, padx=(0, 14))
        ctk.CTkLabel(txt, text=name, font=F(14, "bold"), text_color=C.TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(txt, text=where, font=F(11), text_color=C.FAINT, anchor="w", justify="left", wraplength=560).pack(fill="x")
        ctk.CTkLabel(txt, text=hint, font=F(12), text_color=C.MUTED, anchor="w", justify="left", wraplength=560).pack(
            fill="x", pady=(6, 0)
        )

    def _show_done(self, info: dict) -> None:
        body, footer = self._screen(2, text.UI_DONE_TITLE, text.UI_DONE_SUB, done_upto=3)
        scroll = body

        hsdr = info.get("hsdr")
        folder = self.out_dir
        if info.get("dest"):
            dest = Path(info["dest"])
            folder = str(dest.parent)
            self._file_card(scroll, ".hdbest", C.ACCENT, dest.name, folder, text.UI_DONE_HDB_HINT)
        if hsdr:
            hp = Path(hsdr["dest"])
            folder = str(hp.parent) if not info.get("dest") else folder
            self._file_card(
                scroll, ".json", C.OK, hp.name, str(hp.parent),
                text.UI_DONE_HSDR_HINT.format(owned=hsdr["owned"], total=hsdr["total"], ranks=hsdr["ranks"]),
            )
            if hsdr.get("boardsDest"):
                bp = Path(hsdr["boardsDest"])
                self._file_card(
                    scroll, ".json", C.OK, bp.name, str(bp.parent),
                    text.UI_DONE_BOARDS_HINT.format(holomems=hsdr["boards"]["holomems"], nodes=hsdr["boards"]["nodes"]),
                )
            if hsdr["uncertainLevels"]:
                self._notice(
                    scroll,
                    text.HSDR_UNCERTAIN.format(
                        n=len(hsdr["uncertainLevels"]),
                        cards=", ".join(u["card"] for u in hsdr["uncertainLevels"][:4])
                        + (f" +{len(hsdr['uncertainLevels']) - 4} more" if len(hsdr["uncertainLevels"]) > 4 else ""),
                    ),
                    "warn",
                ).pack(fill="x", pady=(8, 0))
            for w in hsdr["warnings"][:2]:
                self._notice(scroll, w, "warn").pack(fill="x", pady=(8, 0))
        self._notice(scroll, text.UI_DONE_SIGNOUT, "info").pack(fill="x", pady=(8, 0))

        self._primary(footer, text.GUI_BTN_CLOSE, self.destroy, width=130).pack(side="right")
        self._ghost(footer, text.UI_BTN_OPEN_FOLDER, lambda: _open_folder(folder), width=140).pack(
            side="right", padx=(0, 10)
        )
        self._ghost(footer, text.UI_BTN_AGAIN, self._step_source, width=130).pack(side="left")

    def _show_error(self, msg: str) -> None:
        body, footer = self._screen(2, text.UI_ERROR_TITLE, text.UI_ERROR_SUB, done_upto=2)
        self._notice(body, msg, "err").pack(fill="x")
        self._primary(footer, "←  " + text.UI_BTN_START_OVER, self._step_source, width=170).pack(side="right")


def run_gui() -> int:
    App().mainloop()
    return 0
