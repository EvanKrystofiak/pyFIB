"""Tkinter UI wrapper for smartfib_executor.

Goal:
- Provide a SerialFIB-like (simple) desktop UI to:
  * capture named positions (stage + objective potential + BMP)
  * mill a SmartFIB .ely pattern at multiple positions
  * browse captured positions and preview/open BMP images

Design choice:
- The UI launches the existing CLI commands in a subprocess
  (sys.executable -m smartfib_executor ...). This keeps behavior consistent
  with your tested command lines.

Windows-only (SmartSEM Remote API is ActiveX/COM).
"""

from __future__ import annotations

import os
import re
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
from tkinter import font as tkfont

from .batch import natural_position_sort
from .positions import PositionDatabase

try:
    from PIL import Image, ImageTk  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageTk = None  # type: ignore


# Best-effort import of the public project version.
try:  # pragma: no cover
    from pyfib import __version__ as PYFIB_VERSION  # type: ignore
except Exception:  # pragma: no cover
    PYFIB_VERSION = "dev"


# ---- UI theme (pyFIB_UI: black / gold + subtle section color blocks) ----

# Palette: shared with elymaker / gisdep (fib_dark_theme). Teal accent on a dark
# scope-room slate. Constant NAMES are kept so the rest of the GUI is unchanged;
# only the values move. To retune, keep these in sync with fib_dark_theme.py.
COLOR_BG = "#232833"          # window background            (theme BG)
COLOR_TEXT = "#e7ebf2"        # main text                    (theme TEXT)
COLOR_MUTED = "#9aa3b2"       # muted text                   (theme TEXT_DIM)
COLOR_ACCENT = "#17c3b2"      # teal accent                  (theme ACCENT)
COLOR_ACCENT_HI = "#3ad9c7"   # brighter teal (hover/pressed)(theme ACCENT_Hi)
COLOR_ACCENT_FG = "#06231f"   # text drawn on the accent     (theme ACCENT_FG)
COLOR_SELECT = "#123b37"      # selection: dark teal
COLOR_DANGER = "#e0574a"      # stop button                  (theme DANGER)
COLOR_DANGER_HI = "#c8483d"   # stop hover

# Section blocks: same blue-grey family (subtle depth, no clashing hues)
COLOR_PROJECT_BOX = "#1c212b"  # panel base                  (theme BG_ALT)
COLOR_NAV_BOX = "#1c212b"

# Active tab content
COLOR_CONTENT_BOX = "#1c212b"

# Shared surfaces
COLOR_PANEL = "#1c212b"        # generic panel background     (theme BG_ALT)
COLOR_PANEL_2 = "#2b313d"      # header background            (theme BG_RAISE)
COLOR_FIELD = "#1c212b"        # entry/tree background        (theme BG_ALT)
COLOR_BORDER = "#3a4150"       # subtle borders               (theme BORDER)

# Checkbox glyphs used in the Milling Queue table (Treeview)
CHECK_ON = "☑"
CHECK_OFF = "☐"
# Inner surfaces (depth inside the Project / Navigation / Tab boxes)
COLOR_PROJECT_INNER = "#2b313d"  # raised surface            (theme BG_RAISE)
COLOR_NAV_INNER = "#2b313d"
COLOR_CONTENT_INNER = "#2b313d"
COLOR_CONTENT_CARD = "#2b313d"   # card background within the active tab

# Neutral button colors (theme button base)
COLOR_BTN_NEUTRAL = "#1c212b"
COLOR_BTN_NEUTRAL_HI = "#2b313d"

# Safety (ARM) panel colors: neutral when off (accent outline), red when armed
COLOR_ARM_OFF = "#1c212b"  # neutral panel base
COLOR_ARM_ON = "#5a1a14"   # dark red (armed)

# Rounded button defaults
BTN_RADIUS = 14
BTN_HEIGHT = 34
BTN_HEIGHT_NAV = 40
BTN_HEIGHT_ARM = 46


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _hex_to_rgb(hex_color: str):
    """#RRGGBB -> (r,g,b) ints."""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        return 0, 0, 0
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp(r, 0, 255):02x}{_clamp(g, 0, 255):02x}{_clamp(b, 0, 255):02x}"


def _blend(c1: str, c2: str, t: float) -> str:
    """Blend c1->c2 with t in [0,1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return _rgb_to_hex(r, g, b)


def _attach_vertical_gradient(parent: tk.Widget, top: str, bottom: str, *, steps: int = 64) -> tk.Canvas:
    """Attach a non-interactive vertical gradient background behind existing children.

    Implementation notes:
    - Uses a Canvas placed behind other widgets.
    - Draws a small number of rectangles ("steps") for a subtle gradient.
    - Best-effort: never raises if drawing fails.
    """

    cv = tk.Canvas(parent, highlightthickness=0, bd=0)
    cv.place(x=0, y=0, relwidth=1, relheight=1)
    try:
        cv.lower()  # keep behind child widgets
    except Exception:
        pass

    def redraw(_evt=None):
        try:
            w = int(cv.winfo_width())
            h = int(cv.winfo_height())
            if w <= 2 or h <= 2:
                return
            cv.delete("_grad")
            for i in range(max(2, int(steps))):
                y1 = int(i * h / steps)
                y2 = int((i + 1) * h / steps)
                t = float(i) / float(max(1, steps - 1))
                col = _blend(top, bottom, t)
                cv.create_rectangle(0, y1, w, y2, outline="", fill=col, tags="_grad")
        except Exception:
            return

    cv.bind("<Configure>", redraw)
    redraw()
    return cv


def _rounded_rect_points(x1: int, y1: int, x2: int, y2: int, r: int):
    """Return polygon points for a rounded rectangle."""
    w = max(0, x2 - x1)
    h = max(0, y2 - y1)
    r = _clamp(r, 0, min(w // 2, h // 2))
    # Points go clockwise; smooth=True makes rounded corners.
    return [
        x1 + r,
        y1,
        x2 - r,
        y1,
        x2,
        y1,
        x2,
        y1 + r,
        x2,
        y2 - r,
        x2,
        y2,
        x2 - r,
        y2,
        x1 + r,
        y2,
        x1,
        y2,
        x1,
        y2 - r,
        x1,
        y1 + r,
        x1,
        y1,
    ]


class RoundedButton(tk.Canvas):
    """A rounded-corner button implemented on a Canvas.

    ttk buttons cannot be reliably rounded across themes. This widget draws
    its own rounded rectangle and supports hover/pressed states.
    """

    def __init__(
        self,
        master,
        *,
        text: str,
        command,
        width: int = 140,
        height: int = BTN_HEIGHT,
        radius: int = BTN_RADIUS,
        fill: str = COLOR_BTN_NEUTRAL,
        hover_fill: Optional[str] = None,
        press_fill: Optional[str] = None,
        outline: str = "",
        outline_width: int = 0,
        fg: str = COLOR_TEXT,
        font=("Segoe UI", 10, "bold"),
        image=None,
        image_pad: int = 10,
        icon_box_width: int = 34,
        icon_text_gap: int = 8,
        text_anchor: str = "center",
        canvas_bg: Optional[str] = None,
    ):
        bg = canvas_bg
        if bg is None:
            try:
                bg = str(master.cget("background"))
            except Exception:
                bg = COLOR_BG

        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=bg,
        )
        self.configure(cursor="hand2")

        self._text = str(text)
        self._command = command
        self._radius = int(radius)
        self._fill = str(fill)
        self._hover_fill = str(hover_fill) if hover_fill is not None else str(fill)
        if press_fill is not None:
            self._press_fill = str(press_fill)
        elif hover_fill is not None:
            self._press_fill = str(hover_fill)
        else:
            self._press_fill = str(fill)
        self._outline = str(outline)
        self._outline_width = int(outline_width)
        self._fg = str(fg)
        self._font = font
        self._image = image
        self._image_pad = int(image_pad)
        self._icon_box_width = int(icon_box_width)
        self._icon_text_gap = int(icon_text_gap)
        self._text_anchor = str(text_anchor)

        self._hover = False
        self._pressed = False
        self._enabled = True

        self.bind("<Enter>", lambda _e: self._on_enter())
        self.bind("<Leave>", lambda _e: self._on_leave())
        self.bind("<ButtonPress-1>", lambda _e: self._on_press())
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._redraw())

        self._redraw()

    def set_text(self, text: str) -> None:
        self._text = str(text)
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._redraw()

    def set_colors(
        self,
        *,
        fill: Optional[str] = None,
        hover_fill: Optional[str] = None,
        press_fill: Optional[str] = None,
        fg: Optional[str] = None,
        outline: Optional[str] = None,
        outline_width: Optional[int] = None,
    ) -> None:
        if fill is not None:
            self._fill = str(fill)
        if hover_fill is not None:
            self._hover_fill = str(hover_fill)
        if press_fill is not None:
            self._press_fill = str(press_fill)
        if fg is not None:
            self._fg = str(fg)
        if outline is not None:
            self._outline = str(outline)
        if outline_width is not None:
            self._outline_width = int(outline_width)
        self._redraw()

    def _current_fill(self) -> str:
        if not self._enabled:
            return COLOR_BORDER
        if self._pressed:
            return self._press_fill
        if self._hover:
            return self._hover_fill
        return self._fill

    def _redraw(self) -> None:
        self.delete("all")
        w = max(2, int(self.winfo_width()))
        h = max(2, int(self.winfo_height()))
        pts = _rounded_rect_points(1, 1, w - 2, h - 2, self._radius)
        fill = self._current_fill()

        self.create_polygon(
            pts,
            smooth=True,
            fill=fill,
            outline=self._outline,
            width=self._outline_width,
        )

        if self._image is not None:
            # Keep icon + text alignment consistent across buttons.
            # We draw a fixed-width "icon column" and center the icon inside it,
            # so the text always starts at the same x position even if icon sizes differ.
            pad = self._image_pad
            box_w = max(24, int(self._icon_box_width))

            icon_x = pad + (box_w // 2)
            icon_y = h // 2
            try:
                self.create_image(icon_x, icon_y, image=self._image, anchor="center")
            except Exception:
                # Fallback to the old behavior if a Tk build is picky
                self.create_image(pad + 2, icon_y, image=self._image, anchor="w")

            text_x = pad + box_w + max(4, int(self._icon_text_gap))
            self.create_text(text_x, h // 2, text=self._text, fill=self._fg, font=self._font, anchor="w")
        else:
            if self._text_anchor == "w":
                self.create_text(12, h // 2, text=self._text, fill=self._fg, font=self._font, anchor="w")
            else:
                self.create_text(w // 2, h // 2, text=self._text, fill=self._fg, font=self._font, anchor="center")

    def _on_enter(self) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, event) -> None:
        if not self._enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        if not was_pressed:
            return
        try:
            x, y = event.x, event.y
            if 0 <= x <= self.winfo_width() and 0 <= y <= self.winfo_height():
                if callable(self._command):
                    self._command()
        except Exception:
            pass


class ArmToggle(tk.Frame):
    """Large, readable global ARM toggle with rounded corners."""

    def __init__(
        self,
        master,
        *,
        variable: tk.BooleanVar,
        bg: str,
        on_text: str = "ARMED",
        off_text: str = "DISARMED (DRY-RUN)",
    ):
        super().__init__(master, bg=bg)
        self._var = variable
        self._on_text = on_text
        self._off_text = off_text

        self._btn = RoundedButton(
            self,
            text=self._off_text,
            command=self._toggle,
            width=1,
            height=BTN_HEIGHT_ARM,
            radius=18,
            fill=COLOR_ARM_OFF,
            hover_fill="#2b313d",
            press_fill="#343b48",
            outline=COLOR_ACCENT,
            outline_width=1,
            fg=COLOR_TEXT,
            font=("Segoe UI", 13, "bold"),
            canvas_bg=bg,
        )
        self._btn.pack(fill=tk.X)

        try:
            self._var.trace_add("write", lambda *_: self._sync())
        except Exception:
            self._var.trace("w", lambda *_: self._sync())
        self._sync()

    def _toggle(self) -> None:
        try:
            self._var.set(not bool(self._var.get()))
        except Exception:
            pass

    def _sync(self) -> None:
        armed = bool(self._var.get())
        if armed:
            self._btn.set_text(self._on_text)
            self._btn.set_colors(
                fill=COLOR_ACCENT,
                hover_fill=COLOR_ACCENT_HI,
                press_fill=COLOR_ACCENT_HI,
                fg=COLOR_ACCENT_FG,
                outline=COLOR_ACCENT,
                outline_width=1,
            )
        else:
            self._btn.set_text(self._off_text)
            self._btn.set_colors(
                fill=COLOR_ARM_OFF,
                hover_fill="#2b313d",
                press_fill="#343b48",
                fg=COLOR_TEXT,
                outline=COLOR_ACCENT,
                outline_width=1,
            )


def _is_windows() -> bool:

    return os.name == "nt"


def _open_in_default_app(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if _is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    # Best-effort for non-Windows (UI isn't intended there, but don't crash)
    import webbrowser

    webbrowser.open(path.as_uri())


def _project_root() -> Path:
    # smartfib_executor/gui.py -> smartfib_executor -> project root
    return Path(__file__).resolve().parent.parent


@dataclass
class RunResult:
    returncode: int
    stdout: str


class SubprocessWorker:
    """Runs a command in the background and streams output lines."""

    def __init__(self, on_line, on_done):
        self._on_line = on_line
        self._on_done = on_done
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stop_requested = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop_requested = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self, cmd: List[str], cwd: Optional[Path] = None,
            input_text: Optional[str] = None) -> None:
        if self.is_running():
            raise RuntimeError("A command is already running")

        self._stop_requested = False

        def _target():
            out_buf: List[str] = []
            try:
                self._on_line(f"\n$ {' '.join(cmd)}\n")
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd) if cwd else None,
                    stdin=(subprocess.PIPE if input_text is not None else None),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert self._proc.stdout is not None
                if input_text is not None and self._proc.stdin is not None:
                    try:
                        self._proc.stdin.write(input_text)
                        self._proc.stdin.flush()
                        self._proc.stdin.close()
                    except Exception:
                        pass
                for line in self._proc.stdout:
                    out_buf.append(line)
                    self._on_line(line)
                    if self._stop_requested:
                        break
                rc = self._proc.wait()
                self._on_done(RunResult(returncode=rc, stdout="".join(out_buf)))
            except Exception as e:
                self._on_line(f"[UI ERROR] {e}\n")
                self._on_done(RunResult(returncode=1, stdout="".join(out_buf)))

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()


class SmartFIBExecutorUI(tk.Tk):
    def __init__(self, *, positions_file: Optional[str] = None, ely_file: Optional[str] = None, timeout_s: int = 3600):
        super().__init__()
        self._apply_dark_theme()
        self.title(f"pyFIB_UI v{PYFIB_VERSION}")
        self.geometry("1200x760")

        # State
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._worker = SubprocessWorker(self._enqueue_log, self._on_cmd_done)
        self._last_cmd_kind: Optional[str] = None
        # Coincidence tool image state (SEM + FIB overview shown side by side).
        self._coinc_img_dir: Optional[str] = None
        self._coinc_sem_path: Optional[str] = None
        self._coinc_fib_path: Optional[str] = None
        self._coinc_sem_tk = None
        self._coinc_fib_tk = None
        self._current_image_tk = None  # keep reference

        # Variables
        # SAFETY: never adopt a stale positions.json automatically. When the caller does
        # not provide an explicit file, start a FRESH timestamped session file. An old file
        # can only enter the session via an explicit "Load Previous..." action.
        self.var_positions_file = tk.StringVar(value=positions_file or str(self._new_session_positions_path()))
        # The file the session launched with (fresh or explicit). Used to detect when a
        # foreign (previous-session) file is later loaded, so we can warn once about the
        # stale coordinates it contains.
        self._launch_session_file: str = self.var_positions_file.get().strip()
        self._warned_stale_paths: set = set()
        self.var_ely_file = tk.StringVar(value=ely_file or "")
        self.var_timeout = tk.StringVar(value=str(timeout_s))
        self.var_done_confirm = tk.StringVar(value="30")
        self.var_wait = tk.StringVar(value="auto")
        self.var_connect = tk.StringVar(value="remoting")
        self.var_machine = tk.StringVar(value="")

        # Global ARM: single safety switch for both capture + milling
        self.var_arm_global = tk.BooleanVar(value=False)

        self.var_capture_name = tk.StringVar(value="position_1")
        self.var_capture_mag = tk.StringVar(value="2000")

        self.var_continue_on_error = tk.BooleanVar(value=False)
        self.var_move_method = tk.StringVar(value="movestage")
        self.var_stage_timeout = tk.StringVar(value="120")

        # Milling queue UI state
        self._queue_item_by_name = {}  # name -> tree item id
        self._queue_done = 0
        self._queue_total = 0
        self._queue_running_name: Optional[str] = None
        self._active_mill_names: List[str] = []

        # Live readout
        self.var_live_current = tk.StringVar(value="(idle)")
        self.var_live_wait = tk.StringVar(value="")
        self.var_live_event = tk.StringVar(value="")

        # Build UI
        self._build_layout()

        # Load positions at launch.
        # - Fresh session: the timestamped file does not exist yet, so this starts EMPTY (safe).
        # - Explicit --positions-file that exists: that file is loaded (deliberate opt-in).
        self._db: Optional[PositionDatabase] = None
        self._load_positions(silent=True)

        # Start log pump
        self.after(100, self._drain_log_queue)

    def _apply_dark_theme(self) -> None:
        """Apply a modern-ish black/gold theme (pyFIB_UI) using ttk styles.

        Notes:
        - Uses the 'clam' theme when available because it supports custom colors.
        - Keeps dependencies minimal (pure tkinter/ttk).
        """

        # Root background (covers any empty areas)
        try:
            self.configure(background=COLOR_BG)
        except Exception:
            pass

        # Default font
        #
        # IMPORTANT: do *not* use a raw Tcl font string like "Segoe UI 10".
        # Tk interprets it as a list ("Segoe", "UI", "10") and can crash with:
        #   TclError: expected integer but got "UI"
        # on some systems / Tk builds.
        #
        # Instead, configure Tk's named fonts safely.
        try:
            import tkinter.font as tkfont

            for fname in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                try:
                    f = tkfont.nametofont(fname)
                    f.configure(family="Segoe UI", size=10)
                except Exception:
                    pass
        except Exception:
            pass

        style = ttk.Style(self)

        def cfg(style_name: str, **kwargs) -> None:
            """Best-effort style.configure wrapper (ttk options vary by platform/theme)."""
            try:
                style.configure(style_name, **kwargs)
            except Exception:
                pass

        def mp(style_name: str, **kwargs) -> None:
            """Best-effort style.map wrapper."""
            try:
                style.map(style_name, **kwargs)
            except Exception:
                pass
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        # Base
        cfg("TFrame", background=COLOR_PANEL)
        cfg("TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT)
        cfg("TLabelframe", background=COLOR_PANEL, foreground=COLOR_TEXT)
        cfg(
            "TLabelframe.Label",
            background=COLOR_PANEL,
            foreground=COLOR_ACCENT,
            font=("Segoe UI", 10, "bold"),
        )


        # Layout-specific frames/labels
        cfg("Root.TFrame", background=COLOR_BG)
        cfg("Header.TFrame", background=COLOR_PANEL_2)
        cfg("Header.TLabel", background=COLOR_PANEL_2, foreground=COLOR_TEXT)
        cfg("HeaderTitle.TLabel", background=COLOR_PANEL_2, foreground=COLOR_ACCENT, font=("Segoe UI", 20, "bold"))
        cfg("HeaderSub.TLabel", background=COLOR_PANEL_2, foreground=COLOR_MUTED, font=("Segoe UI", 10))

        cfg("Nav.TFrame", background=COLOR_NAV_INNER)
        cfg("NavHeader.TLabel", background=COLOR_NAV_INNER, foreground=COLOR_ACCENT, font=("Segoe UI", 10, "bold"))
        cfg("NavHint.TLabel", background=COLOR_NAV_INNER, foreground=COLOR_MUTED, font=("Segoe UI", 9))

        cfg("Card.TFrame", background=COLOR_PROJECT_INNER)
        cfg("CardTitle.TLabel", background=COLOR_PROJECT_INNER, foreground=COLOR_ACCENT, font=("Segoe UI", 11, "bold"))
        cfg("Card.TLabel", background=COLOR_PROJECT_INNER, foreground=COLOR_TEXT)

        cfg("Page.TFrame", background=COLOR_CONTENT_INNER)

        # Content cards (inside active tab)
        cfg("Content.TLabelframe", background=COLOR_CONTENT_CARD, foreground=COLOR_TEXT)
        cfg("Content.TLabelframe.Label", background=COLOR_CONTENT_CARD, foreground=COLOR_ACCENT, font=("Segoe UI", 10, "bold"))
        cfg("Content.TFrame", background=COLOR_CONTENT_CARD)
        cfg("Content.TLabel", background=COLOR_CONTENT_CARD, foreground=COLOR_TEXT)
        cfg("ContentMuted.TLabel", background=COLOR_CONTENT_CARD, foreground=COLOR_MUTED)


        # Navigation checkbutton (global ARM lives in the nav safety box)
        cfg("Nav.TCheckbutton", background=COLOR_NAV_INNER, foreground=COLOR_TEXT)
        mp(
            "Nav.TCheckbutton",
            foreground=[("disabled", COLOR_MUTED)],
            background=[("active", COLOR_NAV_INNER)],
        )

        # Accent button (gold)
        cfg(
            "Accent.TButton",
            background=COLOR_ACCENT,
            foreground=COLOR_ACCENT_FG,
            padding=(12, 7),
            borderwidth=0,
        )
        mp(
            "Accent.TButton",
            background=[("active", COLOR_ACCENT_HI), ("pressed", COLOR_ACCENT_HI), ("disabled", COLOR_BORDER)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        # Navigation buttons
        cfg(
            "Nav.TButton",
            background=COLOR_NAV_BOX,
            foreground=COLOR_TEXT,
            padding=(12, 10),
            borderwidth=0,
            anchor="w",
        )
        mp(
            "Nav.TButton",
            background=[("active", COLOR_BORDER), ("pressed", COLOR_BORDER), ("disabled", COLOR_NAV_INNER)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        cfg(
            "NavActive.TButton",
            background=COLOR_ACCENT,
            foreground=COLOR_ACCENT_FG,
            padding=(12, 10),
            borderwidth=0,
            anchor="w",
        )
        mp(
            "NavActive.TButton",
            background=[("active", COLOR_ACCENT_HI), ("pressed", COLOR_ACCENT_HI), ("disabled", COLOR_BORDER)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        # Buttons
        cfg(
            "TButton",
            background=COLOR_ACCENT,
            foreground=COLOR_ACCENT_FG,
            padding=(12, 7),
            borderwidth=0,
        )
        mp(
            "TButton",
            background=[("active", COLOR_ACCENT_HI), ("pressed", COLOR_ACCENT_HI), ("disabled", COLOR_BORDER)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        cfg(
            "Danger.TButton",
            background=COLOR_DANGER,
            foreground="white",
            padding=(12, 7),
            borderwidth=0,
        )
        mp(
            "Danger.TButton",
            background=[("active", COLOR_DANGER_HI), ("pressed", COLOR_DANGER_HI), ("disabled", COLOR_BORDER)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        # Inputs
        cfg(
            "TEntry",
            fieldbackground=COLOR_FIELD,
            background=COLOR_FIELD,
            foreground=COLOR_TEXT,
        )
        mp(
            "TEntry",
            fieldbackground=[("disabled", COLOR_BG)],
            foreground=[("disabled", COLOR_MUTED)],
        )

        cfg(
            "TCombobox",
            fieldbackground=COLOR_FIELD,
            background=COLOR_FIELD,
            foreground=COLOR_TEXT,
        )
        mp(
            "TCombobox",
            fieldbackground=[("readonly", COLOR_FIELD)],
            foreground=[("readonly", COLOR_TEXT)],
        )

        cfg("TCheckbutton", background=COLOR_PANEL, foreground=COLOR_TEXT)
        mp(
            "TCheckbutton",
            foreground=[("disabled", COLOR_MUTED)],
            background=[("active", COLOR_PANEL)],
        )

        # Notebook
        cfg("TNotebook", background=COLOR_BG, borderwidth=0)
        cfg("TNotebook.Tab", background=COLOR_BG, foreground=COLOR_TEXT, padding=(14, 9))
        mp(
            "TNotebook.Tab",
            background=[("selected", COLOR_PANEL), ("active", COLOR_BORDER)],
            foreground=[("selected", "white")],
        )

        # Treeview
        cfg(
            "Treeview",
            background=COLOR_FIELD,
            fieldbackground=COLOR_FIELD,
            foreground=COLOR_TEXT,
            rowheight=24,
        )
        mp(
            "Treeview",
            background=[("selected", COLOR_SELECT)],
            foreground=[("selected", "white")],
        )
        cfg(
            "Treeview.Heading",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            relief="flat",
        )
        mp("Treeview.Heading", background=[("active", COLOR_BORDER)])

        # Scrollbars (best-effort)
        cfg(
            "Vertical.TScrollbar",
            background=COLOR_PANEL,
            troughcolor=COLOR_BG,
            bordercolor=COLOR_BG,
            arrowcolor=COLOR_TEXT,
        )
        cfg(
            "Horizontal.TScrollbar",
            background=COLOR_PANEL,
            troughcolor=COLOR_BG,
            bordercolor=COLOR_BG,
            arrowcolor=COLOR_TEXT,
        )

    # ---------------- UI layout ----------------

    def _build_layout(self) -> None:
        """Build a more modern layout:

        - Large header (app name)
        - Left-side navigation
        - Stacked pages (Capture / Positions / Mill / Log)
        """

        # ---- header
        outer = ttk.Frame(self, style="Root.TFrame")
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        # Load icons (best effort)
        self._icons = self._load_icons()

        header = ttk.Frame(outer, style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        header.columnconfigure(2, weight=1)

        # App icon (optional)
        if self._icons.get("app") is not None:
            try:
                self.iconphoto(True, self._icons["app"])  # type: ignore[arg-type]
            except Exception:
                pass
            ttk.Label(header, image=self._icons["app"], style="Header.TLabel").grid(
                row=0, column=0, rowspan=2, padx=(16, 10), pady=14, sticky="w"
            )
        else:
            ttk.Label(header, text="◆", style="HeaderTitle.TLabel").grid(
                row=0, column=0, rowspan=2, padx=(16, 10), pady=14, sticky="w"
            )

        ttk.Label(header, text=f"pyFIB_UI v{PYFIB_VERSION}", style="HeaderTitle.TLabel").grid(
            row=0, column=1, columnspan=2, sticky="w", pady=(14, 0)
        )
        ttk.Label(
            header,
            text="position capture + SmartFIB pattern execution for Zeiss Crossbeam",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(2, 14))

        # Copyright (same font/color as the subtitle)
        ttk.Label(
            header,
            text="Copyright Evan Krystofiak 2026",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=2, sticky="e", padx=(0, 18), pady=(2, 14))

        # ---- configuration card (Project box)
        project_box = tk.Frame(
            outer,
            bg=COLOR_PROJECT_BOX,
            highlightbackground=COLOR_ACCENT,
            highlightthickness=1,
            bd=0,
        )
        # Subtle gradient for the Project box background
        _attach_vertical_gradient(
            project_box,
            _blend(COLOR_PROJECT_BOX, "#FFFFFF", 0.05),
            _blend(COLOR_PROJECT_BOX, "#000000", 0.10),
            steps=72,
        )
        project_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 10))
        project_box.columnconfigure(0, weight=1)

        cfg_card = ttk.Frame(project_box, style="Card.TFrame")
        cfg_card.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        cfg_card.columnconfigure(0, weight=1)

        top = ttk.Frame(cfg_card, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Project", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))

        # Paths
        self._add_path_row(
            top,
            label="Positions JSON",
            var=self.var_positions_file,
            browse_cmd=self._browse_positions,
            filetypes=[("JSON", "*.json"), ("All", "*")],
            row=1,
        )
        self._add_path_row(
            top,
            label="Pattern (.ely)",
            var=self.var_ely_file,
            browse_cmd=self._browse_ely,
            filetypes=[("ELayout", "*.ely"), ("All", "*")],
            row=2,
        )

        # Connection + wait row
        row2 = ttk.Frame(top, style="Card.TFrame")
        row2.grid(row=3, column=0, columnspan=4, sticky="ew", padx=6, pady=(10, 0))
        row2.columnconfigure(9, weight=1)

        ttk.Label(row2, text="Connect:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            row2,
            textvariable=self.var_connect,
            values=["remoting", "local"],
            width=10,
            state="readonly",
        ).grid(row=0, column=1, padx=(6, 18), sticky="w")

        ttk.Label(row2, text="Machine (local):", style="Card.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(row2, textvariable=self.var_machine, width=28).grid(row=0, column=3, padx=(6, 18), sticky="w")

        ttk.Label(row2, text="Wait:", style="Card.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            row2,
            textvariable=self.var_wait,
            values=["auto", "mode_change", "blanked", "sleep", "none"],
            width=12,
            state="readonly",
        ).grid(row=0, column=5, padx=(6, 18), sticky="w")

        ttk.Label(row2, text="Timeout (s):", style="Card.TLabel").grid(row=0, column=6, sticky="w")
        ttk.Entry(row2, textvariable=self.var_timeout, width=10).grid(row=0, column=7, padx=(6, 12), sticky="w")

        RoundedButton(row2, text="Reload Positions", command=self._load_positions, width=170, height=BTN_HEIGHT, radius=12, fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI, fg=COLOR_ACCENT_FG, canvas_bg=COLOR_PROJECT_INNER).grid(
            row=0, column=8, padx=(10, 0)
        )

        # ---- body: left nav + pages
        body = ttk.Frame(outer, style="Root.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Navigation box (colored)
        nav_box = tk.Frame(
            body,
            bg=COLOR_NAV_BOX,
            highlightbackground=COLOR_ACCENT,
            highlightthickness=1,
            bd=0,
        )
        # Subtle gradient for the Navigation box background
        _attach_vertical_gradient(
            nav_box,
            _blend(COLOR_NAV_BOX, "#FFFFFF", 0.04),
            _blend(COLOR_NAV_BOX, "#000000", 0.12),
            steps=72,
        )
        nav_box.grid(row=0, column=0, sticky="nsw", padx=(12, 8), pady=(0, 12))

        nav = ttk.Frame(nav_box, style="Nav.TFrame", width=230)
        nav.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        nav.pack_propagate(False)

        # Content box (colored)
        content_box = tk.Frame(
            body,
            bg=COLOR_CONTENT_BOX,
            highlightbackground=COLOR_ACCENT,
            highlightthickness=1,
            bd=0,
        )
        # Subtle gradient for the active Tab/Content box background
        _attach_vertical_gradient(
            content_box,
            _blend(COLOR_CONTENT_BOX, "#FFFFFF", 0.04),
            _blend(COLOR_CONTENT_BOX, "#000000", 0.12),
            steps=72,
        )
        content_box.grid(row=0, column=1, sticky="nsew", padx=(8, 12), pady=(0, 12))
        content_box.columnconfigure(0, weight=1)
        content_box.rowconfigure(0, weight=1)

        content = ttk.Frame(content_box, style="Page.TFrame")
        content.grid(row=0, column=0, sticky="nsew", padx=3, pady=3)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        # Pages (reuse existing tab builders)
        self.tab_capture = ttk.Frame(content, style="Page.TFrame")
        self.tab_positions = ttk.Frame(content, style="Page.TFrame")
        self.tab_mill = ttk.Frame(content, style="Page.TFrame")
        self.tab_tools = ttk.Frame(content, style="Page.TFrame")
        self.tab_log = ttk.Frame(content, style="Page.TFrame")

        for f in (self.tab_capture, self.tab_positions, self.tab_mill,
                  self.tab_tools, self.tab_log):
            f.grid(row=0, column=0, sticky="nsew")

        self._build_capture_tab()
        self._build_positions_tab()
        self._build_mill_tab()
        self._build_tools_tab()
        self._build_log_tab()

        # Nav buttons
        ttk.Label(nav, text="Navigation", style="NavHeader.TLabel").pack(anchor="w", padx=14, pady=(14, 10))

        self._nav_buttons = {}

        def add_nav(key: str, text_label: str, icon_key: str):
            img = self._icons.get(icon_key)
            btn = RoundedButton(
                nav,
                text=text_label,
                command=lambda k=key: self._show_page(k),
                width=210,
                height=BTN_HEIGHT_NAV,
                radius=16,
                fill=COLOR_NAV_INNER,
                hover_fill=COLOR_BORDER,
                press_fill=COLOR_BORDER,
                fg=COLOR_TEXT,
                font=("Segoe UI", 11, "bold"),
                image=img,
                image_pad=14,
                icon_box_width=34,
                icon_text_gap=10,
                text_anchor="w",
                canvas_bg=COLOR_NAV_INNER,
            )
            btn.pack(fill=tk.X, padx=12, pady=6)
            self._nav_buttons[key] = btn

        add_nav("capture", "Capture", "capture")
        add_nav("positions", "Positions", "positions")
        add_nav("mill", "Mill", "mill")
        add_nav("tools", "Tools", "tools")
        add_nav("log", "Log", "log")

        # Bottom safety box (global ARM lives here)
        safety = ttk.Frame(nav, style="Nav.TFrame")
        safety.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)
        ttk.Separator(safety, orient="horizontal").pack(fill=tk.X, pady=(0, 10))

        ttk.Label(safety, text="Safety", style="NavHeader.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(
            safety,
            text="UI is DRY-RUN by default.\nEnable ARM to control the instrument.",
            style="NavHint.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # Large global ARM toggle
        arm_outer = tk.Frame(
            safety,
            bg=COLOR_ARM_OFF,
            highlightbackground=COLOR_ACCENT,
            highlightthickness=1,
            bd=0,
        )
        arm_outer.pack(fill=tk.X)

        arm_inner = tk.Frame(arm_outer, bg=COLOR_ARM_OFF)
        arm_inner.pack(fill=tk.X, padx=6, pady=6)

        ArmToggle(arm_inner, variable=self.var_arm_global, bg=COLOR_ARM_OFF).pack(fill=tk.X)

        # Default page
        self._show_page("capture")

    def _show_page(self, key: str) -> None:
        mapping = {
            "capture": self.tab_capture,
            "positions": self.tab_positions,
            "mill": self.tab_mill,
            "tools": self.tab_tools,
            "log": self.tab_log,
        }
        frame = mapping.get(key)
        if frame is None:
            return
        frame.tkraise()

        # Update nav button styles (rounded canvas buttons)
        for k, btn in getattr(self, "_nav_buttons", {}).items():
            try:
                if k == key:
                    btn.set_colors(
                        fill=COLOR_ACCENT,
                        hover_fill=COLOR_ACCENT_HI,
                        press_fill=COLOR_ACCENT_HI,
                        fg=COLOR_ACCENT_FG,
                        outline=COLOR_ACCENT,
                        outline_width=1,
                    )
                else:
                    btn.set_colors(
                        fill=COLOR_NAV_INNER,
                        hover_fill=COLOR_BORDER,
                        press_fill=COLOR_BORDER,
                        fg=COLOR_TEXT,
                        outline="",
                        outline_width=0,
                    )
            except Exception:
                pass

    def _load_icons(self) -> dict:
        """Load PNG icons from smartfib_executor/assets (best effort)."""
        icons = {"app": None, "capture": None, "positions": None, "mill": None,
                 "tools": None, "log": None}
        try:
            assets_dir = Path(__file__).resolve().parent / "assets"
            if not assets_dir.exists():
                return icons

            def load_png(name: str):
                p = assets_dir / name
                if not p.exists():
                    return None
                try:
                    return tk.PhotoImage(file=str(p))
                except Exception:
                    return None

            icons["app"] = load_png("app.png")
            icons["capture"] = load_png("capture.png")
            icons["positions"] = load_png("positions.png")
            icons["mill"] = load_png("mill.png")
            icons["tools"] = load_png("tools.png")
            icons["log"] = load_png("log.png")
        except Exception:
            return icons
        return icons

    def _add_path_row(self, parent, *, label: str, var: tk.StringVar, browse_cmd, filetypes, row: int) -> None:
        ttk.Label(parent, text=label + ":", style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=6, pady=6)
        ent = ttk.Entry(parent, textvariable=var)
        ent.grid(row=row, column=1, sticky="ew", padx=6, pady=6)
        parent.columnconfigure(1, weight=1)
        RoundedButton(parent, text="Browse", command=browse_cmd, width=92, height=BTN_HEIGHT, radius=12, fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI, fg=COLOR_ACCENT_FG, canvas_bg=COLOR_PROJECT_INNER).grid(row=row, column=2, padx=6, pady=6)

    # ---------------- Capture tab ----------------

    def _build_capture_tab(self) -> None:
        frm = ttk.Frame(self.tab_capture, style="Page.TFrame")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        box = ttk.LabelFrame(frm, text="Capture Position", style="Content.TLabelframe")
        box.pack(fill=tk.X)

        r = ttk.Frame(box, style="Content.TFrame")
        r.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(r, text="Name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(r, textvariable=self.var_capture_name, width=20).grid(row=0, column=1, padx=(6, 18), sticky="w")

        ttk.Label(r, text="FIB Mag:").grid(row=0, column=2, sticky="w")
        ttk.Entry(r, textvariable=self.var_capture_mag, width=10).grid(row=0, column=3, padx=(6, 18), sticky="w")

        ttk.Label(r, text="ARM is global (left Safety box).", foreground=COLOR_MUTED).grid(row=0, column=4, padx=(0, 18), sticky="w")

        RoundedButton(r, text="Capture", command=self._on_capture, width=120, height=BTN_HEIGHT, radius=12, fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI, fg=COLOR_ACCENT_FG, canvas_bg=COLOR_CONTENT_CARD).grid(row=0, column=5, padx=6)
        RoundedButton(r, text="Stop", command=self._on_stop, width=110, height=BTN_HEIGHT, radius=12, fill=COLOR_DANGER, hover_fill=COLOR_DANGER_HI, press_fill=COLOR_DANGER_HI, fg="white", canvas_bg=COLOR_CONTENT_CARD).grid(row=0, column=6, padx=6)

        info = ttk.LabelFrame(frm, text="Notes", style="Content.TLabelframe")
        info.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        txt = (
            "This runs the same command you already tested, e.g.\n"
            "  python -m pyfib capture-position position_1 --arm --positions-file C:\\temp\\positions.json\n\n"
            "Default is DRY-RUN unless you enable ARM (global Safety switch).\n"
            "Capture requests FIB imaging (DP_FIB_IMAGING=1 and/or CMD_FIB_MODE_FIB) so the saved BMP is a FIB image.\n"
            "Captured images are stored next to the positions file (<name>.bmp by default)."
        )
        ttk.Label(info, text=txt, justify="left").pack(anchor="w", padx=10, pady=10)

    # ---------------- Positions tab ----------------

    def _build_positions_tab(self) -> None:
        frm = ttk.Frame(self.tab_positions, style="Page.TFrame")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Session management (safety): start fresh, or deliberately load a previous file.
        session_box = ttk.LabelFrame(frm, text="Session", style="Content.TLabelframe")
        session_box.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        session_inner = ttk.Frame(session_box, style="Content.TFrame")
        session_inner.pack(fill=tk.X, padx=10, pady=10)
        RoundedButton(
            session_inner, text="New Session", command=self._new_session,
            width=140, height=BTN_HEIGHT, radius=12,
            fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI,
            fg=COLOR_ACCENT_FG, canvas_bg=COLOR_CONTENT_CARD,
        ).pack(side=tk.LEFT)
        RoundedButton(
            session_inner, text="Load Previous…", command=self._load_previous_positions,
            width=160, height=BTN_HEIGHT, radius=12,
            fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI,
            fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.lbl_session = ttk.Label(session_inner, text="", style="ContentMuted.TLabel")
        self.lbl_session.pack(side=tk.LEFT, padx=(14, 0))

        # Split: left list, right details
        paned = ttk.Panedwindow(frm, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, style="Page.TFrame")
        right = ttk.Frame(paned, style="Page.TFrame")
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        # Left: tree
        # First column should be the position name (requested)
        cols = ("pos", "obj_v", "x", "y", "z", "r", "m")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")

        self.tree.heading("pos", text="Position")
        self.tree.heading("obj_v", text="Obj (V)")
        self.tree.heading("x", text="X (m)")
        self.tree.heading("y", text="Y (m)")
        self.tree.heading("z", text="Z (m)")
        self.tree.heading("r", text="R (deg)")
        self.tree.heading("m", text="M (m)")

        self.tree.column("pos", width=140, anchor="w")
        self.tree.column("obj_v", width=90, anchor="e")
        for c, w in zip(("x", "y", "z", "r", "m"), [120, 120, 120, 90, 120]):
            self.tree.column(c, width=w, anchor="e")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select_position)

        # Right: details + image
        details = ttk.LabelFrame(right, text="Selected Position", style="Content.TLabelframe")
        details.pack(fill=tk.X)

        self.lbl_details = ttk.Label(details, text="(select a position)", justify="left")
        self.lbl_details.pack(anchor="w", padx=10, pady=10)

        btns = ttk.Frame(details, style="Content.TFrame")
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        RoundedButton(btns, text="Open Image", command=self._open_selected_image, width=120, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(side=tk.LEFT)
        RoundedButton(btns, text="Refresh", command=self._load_positions, width=110, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(side=tk.LEFT, padx=(8, 0))

        img_box = ttk.LabelFrame(right, text="BMP Preview", style="Content.TLabelframe")
        img_box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.img_label = ttk.Label(img_box, text="(no image)")
        self.img_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        if Image is None:
            self.img_label.configure(
                text="Pillow is not installed, so the UI cannot preview BMP files.\n\nRun:  pip install pillow\n\nYou can still use 'Open Image' to view in the default viewer."
            )

    # ---------------- Mill tab ----------------

    def _build_mill_tab(self) -> None:
        frm = ttk.Frame(self.tab_mill, style="Page.TFrame")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ---- Setup / options
        setup = ttk.LabelFrame(frm, text="Milling Setup", style="Content.TLabelframe")
        setup.pack(fill=tk.X)

        r1 = ttk.Frame(setup, style="Content.TFrame")
        r1.pack(fill=tk.X, padx=10, pady=10)
        r1.columnconfigure(9, weight=1)

        ttk.Label(r1, text="ARM is global (left Safety box).", foreground=COLOR_MUTED).grid(row=0, column=0, padx=(0, 18), sticky="w")

        ttk.Checkbutton(r1, text="Continue on error", variable=self.var_continue_on_error).grid(row=0, column=1, padx=(0, 18), sticky="w")

        ttk.Label(r1, text="Move method:").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            r1,
            textvariable=self.var_move_method,
            values=["movestage", "ap_goto", "none"],
            width=12,
            state="readonly",
        ).grid(row=0, column=3, padx=(6, 18), sticky="w")

        ttk.Label(r1, text="Stage timeout (s):").grid(row=0, column=4, sticky="w")
        ttk.Entry(r1, textvariable=self.var_stage_timeout, width=10).grid(row=0, column=5, padx=(6, 18), sticky="w")

        ttk.Label(r1, text="Wait:").grid(row=0, column=6, sticky="w")
        ttk.Combobox(
            r1,
            textvariable=self.var_wait,
            values=["auto", "mode_change", "blanked", "sleep", "none"],
            width=12,
            state="readonly",
        ).grid(row=0, column=7, padx=(6, 18), sticky="w")

        ttk.Label(r1, text="Timeout (s):").grid(row=0, column=8, sticky="w")
        ttk.Entry(r1, textvariable=self.var_timeout, width=10).grid(row=0, column=9, padx=(6, 0), sticky="w")

        # Confirm-done window (seconds)
        r2 = ttk.Frame(setup, style="Content.TFrame")
        r2.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Label(r2, text="Confirm done (s):").grid(row=0, column=0, sticky="w")
        ttk.Entry(r2, textvariable=self.var_done_confirm, width=10).grid(row=0, column=1, padx=(6, 18), sticky="w")
        ttk.Label(r2, text="Require completion to remain stable before moving to next position.", foreground=COLOR_MUTED).grid(row=0, column=2, sticky="w")

        # ---- Queue / selection + progress
        queue_box = ttk.LabelFrame(frm, text="Milling Queue (check positions to mill)", style="Content.TLabelframe")
        queue_box.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        queue_box.columnconfigure(0, weight=1)
        queue_box.rowconfigure(0, weight=1)

        # Left: queue table
        cols = ("sel", "pos", "status", "af", "refine", "obj_v", "x", "y", "z", "r", "m")
        self.queue_tree = ttk.Treeview(queue_box, columns=cols, show="headings", selectmode="browse")

        self.queue_tree.heading("sel", text="✓")
        self.queue_tree.heading("pos", text="Position")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("af", text="Autofocus")
        self.queue_tree.heading("refine", text="Refine")
        self.queue_tree.heading("obj_v", text="Obj (V)")
        self.queue_tree.heading("x", text="X (m)")
        self.queue_tree.heading("y", text="Y (m)")
        self.queue_tree.heading("z", text="Z (m)")
        self.queue_tree.heading("r", text="R (deg)")
        self.queue_tree.heading("m", text="M (m)")

        self.queue_tree.column("sel", width=42, anchor="center")
        self.queue_tree.column("pos", width=140, anchor="w")
        self.queue_tree.column("status", width=110, anchor="w")
        self.queue_tree.column("af", width=80, anchor="center")
        self.queue_tree.column("refine", width=70, anchor="center")
        for c, w in zip(("obj_v", "x", "y", "z", "r", "m"), (90, 120, 120, 120, 90, 120)):
            self.queue_tree.column(c, width=w, anchor="e")

        q_vsb = ttk.Scrollbar(queue_box, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=q_vsb.set)

        self.queue_tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        q_vsb.grid(row=0, column=1, sticky="ns", pady=10)

        # Click-to-toggle checkbox in first column
        self.queue_tree.bind("<Button-1>", self._on_queue_tree_click)

        # Right: controls
        controls = ttk.Frame(queue_box, style="Content.TFrame")
        controls.grid(row=0, column=2, sticky="ns", padx=10, pady=10)

        RoundedButton(controls, text="Select All", command=self._queue_select_all, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 8))
        RoundedButton(controls, text="Select None", command=self._queue_select_none, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 8))
        RoundedButton(controls, text="Invert", command=self._queue_select_invert, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 14))

        RoundedButton(controls, text="Move Up", command=self._queue_move_up, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 8))
        RoundedButton(controls, text="Move Down", command=self._queue_move_down, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 14))

        RoundedButton(controls, text="Open Image", command=self._open_queue_selected_image, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 8))
        RoundedButton(controls, text="Refresh", command=self._load_positions, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI, press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 14))

        RoundedButton(controls, text="Start Milling", command=self._on_mill_queue, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI, fg=COLOR_ACCENT_FG, canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X, pady=(0, 8))
        RoundedButton(controls, text="Stop", command=self._on_stop, width=200, height=BTN_HEIGHT, radius=12, fill=COLOR_DANGER, hover_fill=COLOR_DANGER_HI, press_fill=COLOR_DANGER_HI, fg="white", canvas_bg=COLOR_CONTENT_CARD).pack(fill=tk.X)

        # Progress + queue summary
        prog = ttk.Frame(queue_box, style="Content.TFrame")
        prog.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))
        prog.columnconfigure(0, weight=1)

        self.var_queue_progress = tk.StringVar(value="Queue: 0/0 completed")
        ttk.Label(prog, textvariable=self.var_queue_progress).grid(row=0, column=0, sticky="w")
        self.queue_progress = ttk.Progressbar(prog, mode="determinate")
        self.queue_progress.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        # ---- Live readout
        live = ttk.LabelFrame(frm, text="Live Readout", style="Content.TLabelframe")
        live.pack(fill=tk.X, pady=(12, 0))
        live.columnconfigure(1, weight=1)

        ttk.Label(live, text="Current:").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Label(live, textvariable=self.var_live_current).grid(row=0, column=1, sticky="w", padx=10, pady=(8, 2))

        ttk.Label(live, text="Wait:").grid(row=1, column=0, sticky="w", padx=10, pady=(2, 2))
        ttk.Label(live, textvariable=self.var_live_wait).grid(row=1, column=1, sticky="w", padx=10, pady=(2, 2))

        ttk.Label(live, text="Event:").grid(row=2, column=0, sticky="w", padx=10, pady=(2, 8))
        ttk.Label(live, textvariable=self.var_live_event).grid(row=2, column=1, sticky="w", padx=10, pady=(2, 8))

    # ---------------- Tools tab ----------------

    def _build_tools_tab(self) -> None:
        frm = ttk.Frame(self.tab_tools, style="Page.TFrame")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Tools", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))

        # --- Coincidence tool -------------------------------------------------
        coinc = ttk.Frame(frm, style="Card.TFrame")
        coinc.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(coinc, text="SEM working distance / FIB-SEM coincidence",
                  style="CardTitle.TLabel").pack(anchor="w", padx=10, pady=(10, 2))
        ttk.Label(
            coinc,
            text=("Autofocuses the SEM (coarse+fine, confirmed idle), reads the working "
                  "distance, and recommends a Z move to the 5 mm coincidence point. "
                  "It moves Z itself only for corrections \u2264 0.5 mm; larger ones are "
                  "recommended for you to make by hand. Unarmed runs are SIMULATED."),
            style="Card.TLabel", wraplength=720, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        crow = ttk.Frame(coinc, style="Card.TFrame")
        crow.pack(anchor="w", padx=10, pady=(0, 10))
        RoundedButton(
            crow, text="Measure Working Distance", command=self._on_coincidence_measure,
            width=220, height=BTN_HEIGHT, radius=12,
            fill=COLOR_ACCENT, hover_fill=COLOR_ACCENT_HI, press_fill=COLOR_ACCENT_HI,
            fg=COLOR_ACCENT_FG, canvas_bg=COLOR_CONTENT_CARD,
        ).grid(row=0, column=0, padx=(0, 8))
        RoundedButton(
            crow, text="Measure & Adjust Z (\u2264 0.5 mm)",
            command=self._on_coincidence_adjust,
            width=240, height=BTN_HEIGHT, radius=12,
            fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI,
            press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, outline=COLOR_ACCENT,
            canvas_bg=COLOR_CONTENT_CARD,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Label(coinc, text="Requires the SEM beam on. Coincidence at 5 mm WD is tilt-independent. "
                              "Output and images appear below and in the Log tab.", style="Card.TLabel").pack(
            anchor="w", padx=10, pady=(0, 10))

        # SEM + FIB overview images, shown side by side after an armed adjust.
        imgrow = ttk.Frame(coinc, style="Card.TFrame")
        imgrow.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        imgrow.columnconfigure(0, weight=1)
        imgrow.columnconfigure(1, weight=1)
        sem_box = ttk.Frame(imgrow, style="Card.TFrame")
        sem_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        fib_box = ttk.Frame(imgrow, style="Card.TFrame")
        fib_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(sem_box, text="SEM", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(fib_box, text="FIB", style="CardTitle.TLabel").pack(anchor="w")
        self.coinc_sem_label = tk.Label(sem_box, text="(no SEM image yet)",
                                        bg=COLOR_FIELD, fg=COLOR_MUTED,
                                        width=44, height=16)
        self.coinc_sem_label.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.coinc_fib_label = tk.Label(fib_box, text="(no FIB image yet)",
                                        bg=COLOR_FIELD, fg=COLOR_MUTED,
                                        width=44, height=16)
        self.coinc_fib_label.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # --- External tools (not functional yet) ------------------------------
        ext = ttk.Frame(frm, style="Card.TFrame")
        ext.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(ext, text="External tools", style="CardTitle.TLabel").pack(
            anchor="w", padx=10, pady=(10, 2))
        ttk.Label(
            ext,
            text=("Launch the sibling pyFIB-family tools. These will be integrated as "
                  "they mature; the buttons are placeholders for now."),
            style="Card.TLabel", wraplength=720, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        erow = ttk.Frame(ext, style="Card.TFrame")
        erow.pack(anchor="w", padx=10, pady=(0, 12))
        RoundedButton(
            erow, text="Load ELY Maker",
            command=lambda: self._on_launch_external("ELY Maker"),
            width=170, height=BTN_HEIGHT, radius=12,
            fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI,
            press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD,
        ).grid(row=0, column=0, padx=(0, 8))
        RoundedButton(
            erow, text="Load GISDEP",
            command=lambda: self._on_launch_external("GISDEP"),
            width=150, height=BTN_HEIGHT, radius=12,
            fill=COLOR_BTN_NEUTRAL, hover_fill=COLOR_BTN_NEUTRAL_HI,
            press_fill=COLOR_BTN_NEUTRAL_HI, fg=COLOR_TEXT, canvas_bg=COLOR_CONTENT_CARD,
        ).grid(row=0, column=1, padx=(0, 8))

    # ---------------- Tools handlers ----------------

    def _coincidence_cmd(self, *, dry_run: bool) -> List[str]:
        armed = bool(self.var_arm_global.get())
        cmd = [sys.executable, "-u", "-m", "smartfib_executor.coincidence"]
        if not armed:
            cmd.append("--simulate")     # unarmed -> safe offline demo
        if dry_run:
            cmd.append("--dry-run")      # measure + recommend only; never moves Z
        else:
            # Adjust flow: capture SEM+FIB overview images into a fresh dir.
            import tempfile
            self._coinc_img_dir = tempfile.mkdtemp(prefix="pyfib_coinc_")
            self._coinc_sem_path = None
            self._coinc_fib_path = None
            cmd += ["--image-dir", self._coinc_img_dir]
        return cmd

    def _on_coincidence_measure(self) -> None:
        if self._worker.is_running():
            messagebox.showwarning("Busy", "A command is already running.")
            return
        self._last_cmd_kind = "coincidence"
        self._show_page("log")
        # Measure never moves the stage, so no ARM confirmation is needed.
        self._worker.run(self._coincidence_cmd(dry_run=True))

    def _on_coincidence_adjust(self) -> None:
        if self._worker.is_running():
            messagebox.showwarning("Busy", "A command is already running.")
            return
        if bool(self.var_arm_global.get()):
            msg = (
                "Measure the SEM working distance and, if the required correction is "
                "\u2264 0.5 mm, MOVE the stage Z toward the 5 mm coincidence point.\n\n"
                "Safety limits enforced in code: move \u2264 0.5 mm, Z kept within "
                "[20, 40] mm, WD sanity 3\u20138 mm, tilt = 0, SEM beam on. Larger "
                "corrections are only reported, not moved.\n\n"
                "Proceed with the ARMED adjustment?"
            )
            if not messagebox.askyesno("Confirm ARMED Z adjustment", msg):
                return
        self._last_cmd_kind = "coincidence"
        self._show_page("log")
        # Feed "yes" so the tool's own confirmation prompt is satisfied by the dialog
        # above (unarmed runs are simulated and harmless).
        self._worker.run(self._coincidence_cmd(dry_run=False), input_text="yes\n")

    def _on_launch_external(self, name: str) -> None:
        messagebox.showinfo(
            name,
            f"{name} integration is not available yet.\n\n"
            f"This button is a placeholder; {name} will launch from here once it is "
            f"integrated into pyFIB.",
        )

    # ---------------- Log tab ----------------

    def _build_log_tab(self) -> None:
        frm = ttk.Frame(self.tab_log, style="Page.TFrame")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.txt_log = tk.Text(
            frm,
            wrap="word",
            background=COLOR_FIELD,
            foreground=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(frm, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Start with some help text
        self._enqueue_log(
            "SmartFIB Executor UI\n\n"
            "- Use 'Capture' to create/update entries in your positions.json\n"
            "- Use 'Mill' to run the same .ely at multiple positions\n"
            "- Use 'Positions' tab to inspect stage/objective data and images\n\n"
        )

    # ---------------- Actions ----------------

    def _browse_positions(self) -> None:
        p = filedialog.asksaveasfilename(
            title="Select positions JSON",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*")],
        )
        if p:
            self.var_positions_file.set(p)
            self._load_positions()

    def _browse_ely(self) -> None:
        p = filedialog.askopenfilename(
            title="Select pattern (.ely)",
            filetypes=[("ELayout", "*.ely"), ("All", "*")],
        )
        if p:
            self.var_ely_file.set(p)

    def _confirm_armed(self, action_name: str) -> bool:
        msg = (
            f"You are about to run an ARMED instrument action: {action_name}.\n\n"
            "This can move the stage and/or start milling.\n\n"
            "Proceed?"
        )
        return messagebox.askyesno("Confirm ARMED action", msg)

    def _on_capture(self) -> None:
        if self._worker.is_running():
            messagebox.showwarning("Busy", "A command is already running.")
            return

        name = self.var_capture_name.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Enter a position name (e.g. position_1).")
            return

        pos_file = self._positions_path()
        if pos_file is None:
            return

        try:
            mag = float(self.var_capture_mag.get().strip())
        except Exception:
            messagebox.showerror("Bad magnification", "FIB Mag must be a number (e.g. 2000).")
            return

        armed = bool(self.var_arm_global.get())
        if armed and not self._confirm_armed(f"Capture {name}"):
            return

        cmd = [
            sys.executable,
            "-m",
            "pyfib",
            "capture-position",
            name,
            "--positions-file",
            str(pos_file),
            "--fib-mag",
            f"{mag}",
        ]
        cmd += self._connection_args()
        if armed:
            cmd.append("--arm")

        self._last_cmd_kind = "capture"
        self._worker.run(cmd, cwd=_project_root())

    def _on_mill_queue(self) -> None:
        """Start milling using the checked positions in the queue table."""

        names = self._queue_selected_names()
        if not names:
            messagebox.showerror("No positions selected", "Check one or more positions in the Milling Queue.")
            return

        self._start_milling_names(names)

    def _start_milling_names(self, names: List[str]) -> None:
        if self._worker.is_running():
            messagebox.showwarning("Busy", "A command is already running.")
            return

        pos_file = self._positions_path()
        if pos_file is None:
            return

        ely = self.var_ely_file.get().strip()
        if not ely:
            messagebox.showerror("Missing pattern", "Select a .ely pattern file.")
            return

        ely_path = Path(ely).expanduser()
        if not ely_path.exists():
            messagebox.showerror("Pattern not found", f".ely file not found: {ely_path}")
            return

        armed = bool(self.var_arm_global.get())
        if armed and not self._confirm_armed("Mill positions"):
            return

        # Prepare queue/progress UI
        self._queue_prepare_run(names)

        # Timeout / wait
        wait = self.var_wait.get().strip() or "auto"
        timeout_raw = self.var_timeout.get().strip() or "3600"
        try:
            timeout = int(float(timeout_raw))
        except Exception:
            messagebox.showerror("Bad timeout", "Timeout must be a number (seconds). Use 0 for no timeout.")
            return

        # Confirm-done stability window
        done_raw = self.var_done_confirm.get().strip() or "30"
        try:
            done_confirm = float(done_raw)
        except Exception:
            messagebox.showerror("Bad confirm done", "Confirm done must be a number (seconds). Use 0 to disable.")
            return
        if done_confirm < 0:
            messagebox.showerror("Bad confirm done", "Confirm done must be >= 0 seconds.")
            return

        # Stage timeout
        try:
            stage_timeout = float(self.var_stage_timeout.get().strip() or "120")
        except Exception:
            messagebox.showerror("Bad stage timeout", "Stage timeout must be a number (seconds).")
            return

        move_method = self.var_move_method.get().strip() or "movestage"

        cmd = [
            sys.executable,
            "-u",
            "-m",
            "pyfib",
            "mill-positions",
            str(ely_path),
        ]
        cmd += names
        cmd += [
            "--positions-file",
            str(pos_file),
            "--wait",
            wait,
            "--timeout",
            str(timeout),
            "--done-confirm-s",
            str(done_confirm),
            "--move-method",
            move_method,
            "--stage-timeout-s",
            str(stage_timeout),
            "--no-mode-mill",
        ]

        # Per-position autofocus / refine (only names that are also being milled).
        af_names = [n for n in self._queue_flagged_names(3) if n in names]
        rf_names = [n for n in self._queue_flagged_names(4) if n in names]
        if af_names:
            cmd += ["--autofocus", ",".join(af_names)]
        if rf_names:
            cmd += ["--refine", ",".join(rf_names)]

        cmd += self._connection_args()

        if self.var_continue_on_error.get():
            cmd.append("--continue-on-error")

        if armed:
            cmd.append("--arm")

        self._last_cmd_kind = "mill"
        self._worker.run(cmd, cwd=_project_root())

    def _on_stop(self) -> None:
        if self._worker.is_running():
            self._worker.stop()
            self._enqueue_log("\n[UI] Stop requested.\n")

    # ---------------- Milling Queue helpers ----------------

    def _queue_selected_names(self) -> List[str]:
        """Return checked position names in the current displayed order."""
        if not hasattr(self, "queue_tree"):
            return []
        names: List[str] = []
        for item in self.queue_tree.get_children(""):
            vals = list(self.queue_tree.item(item, "values"))
            if len(vals) < 3:
                continue
            if str(vals[0]) == CHECK_ON:
                names.append(str(vals[1]))
        return names

    def _queue_flagged_names(self, col_idx: int) -> List[str]:
        """Checked position names for an extra checkbox column (af=3, refine=4)."""
        if not hasattr(self, "queue_tree"):
            return []
        out: List[str] = []
        for item in self.queue_tree.get_children(""):
            vals = list(self.queue_tree.item(item, "values"))
            if len(vals) <= col_idx:
                continue
            if str(vals[col_idx]) == CHECK_ON:
                out.append(str(vals[1]))
        return out

    def _queue_set_checked(self, *, checked: Optional[bool] = None, invert: bool = False) -> None:
        if not hasattr(self, "queue_tree"):
            return
        for item in self.queue_tree.get_children(""):
            vals = list(self.queue_tree.item(item, "values"))
            if not vals:
                continue
            cur = str(vals[0])
            cur_checked = (cur == CHECK_ON)
            new_checked = cur_checked
            if invert:
                new_checked = not cur_checked
            elif checked is not None:
                new_checked = bool(checked)
            vals[0] = CHECK_ON if new_checked else CHECK_OFF
            self.queue_tree.item(item, values=vals)

    def _queue_select_all(self) -> None:
        self._queue_set_checked(checked=True)

    def _queue_select_none(self) -> None:
        self._queue_set_checked(checked=False)

    def _queue_select_invert(self) -> None:
        self._queue_set_checked(invert=True)

    def _queue_move_up(self) -> None:
        if not hasattr(self, "queue_tree"):
            return
        sel = self.queue_tree.selection()
        if not sel:
            return
        item = sel[0]
        children = list(self.queue_tree.get_children(""))
        try:
            idx = children.index(item)
        except ValueError:
            return
        if idx <= 0:
            return
        self.queue_tree.move(item, "", idx - 1)

    def _queue_move_down(self) -> None:
        if not hasattr(self, "queue_tree"):
            return
        sel = self.queue_tree.selection()
        if not sel:
            return
        item = sel[0]
        children = list(self.queue_tree.get_children(""))
        try:
            idx = children.index(item)
        except ValueError:
            return
        if idx >= len(children) - 1:
            return
        self.queue_tree.move(item, "", idx + 1)

    def _on_queue_tree_click(self, event) -> None:
        """Toggle the checkbox when clicking the first column in the queue tree."""
        if not hasattr(self, "queue_tree"):
            return
        # Only toggle on cell clicks
        region = self.queue_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.queue_tree.identify_column(event.x)  # '#1' is first
        # sel=#1, af=#4, refine=#5 are the toggleable checkbox columns.
        col_to_idx = {"#1": 0, "#4": 3, "#5": 4}
        if col not in col_to_idx:
            return
        item = self.queue_tree.identify_row(event.y)
        if not item:
            return
        vals = list(self.queue_tree.item(item, "values"))
        if not vals:
            return
        idx = col_to_idx[col]
        while len(vals) <= idx:
            vals.append(CHECK_OFF)
        vals[idx] = CHECK_OFF if str(vals[idx]) == CHECK_ON else CHECK_ON
        self.queue_tree.item(item, values=vals)
        # Prevent default behavior from also starting an edit/etc.
        return "break"

    def _open_queue_selected_image(self) -> None:
        if self._db is None:
            messagebox.showinfo("No positions", "Load a positions.json file first.")
            return
        if not hasattr(self, "queue_tree"):
            return
        sel = self.queue_tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a row in the Milling Queue.")
            return
        item = sel[0]
        vals = list(self.queue_tree.item(item, "values"))
        if len(vals) < 2:
            return
        name = str(vals[1])
        try:
            rec = self._db.get(name)
        except Exception:
            return
        if not rec.image_bmp:
            messagebox.showinfo("No image", f"No BMP recorded for {name}.")
            return
        p = Path(rec.image_bmp)
        if not p.is_absolute():
            # interpret relative to the positions.json directory
            pos_file = self._positions_path()
            if pos_file is not None:
                p = (pos_file.parent / p).resolve()
        try:
            _open_in_default_app(p)
        except Exception as e:
            messagebox.showerror("Open image failed", str(e))

    def _queue_prepare_run(self, names: List[str]) -> None:
        """Initialize queue statuses + progress UI for a new milling run."""
        self._active_mill_names = list(names)
        self._queue_total = len(names)
        self._queue_done = 0
        self._queue_running_name = None

        if hasattr(self, "queue_progress"):
            try:
                self.queue_progress.configure(maximum=max(1, self._queue_total), value=0)
            except Exception:
                pass
        if hasattr(self, "var_queue_progress"):
            self.var_queue_progress.set(f"Queue: 0/{self._queue_total} completed")

        # Reset status column
        if hasattr(self, "queue_tree"):
            for item in self.queue_tree.get_children(""):
                vals = list(self.queue_tree.item(item, "values"))
                if len(vals) < 3:
                    continue
                pos = str(vals[1])
                if pos in names:
                    vals[2] = "Pending"
                else:
                    vals[2] = ""
                self.queue_tree.item(item, values=vals)

        self.var_live_current.set("(starting)")
        self.var_live_wait.set("")
        self.var_live_event.set("Starting milling queue...")

    def _queue_mark_status(self, name: str, status: str) -> None:
        if not hasattr(self, "queue_tree"):
            return
        # Find the row by scanning (stable even if user reorders)
        for item in self.queue_tree.get_children(""):
            vals = list(self.queue_tree.item(item, "values"))
            if len(vals) < 3:
                continue
            if str(vals[1]) == name:
                vals[2] = status
                self.queue_tree.item(item, values=vals)
                return

    # ---------------- Positions handling ----------------

    def _new_session_positions_path(self) -> Path:
        """Return a fresh, unique positions file path for a new session.

        Each session gets its own timestamped subfolder so that BMP grabs (which default
        to <positions-file folder>/images) stay grouped per session. The file itself is
        not created on disk until the first capture writes to it.
        """
        ts = time.strftime("%Y%m%d_%H%M%S")
        return _project_root() / "positions" / f"session_{ts}" / "positions.json"

    def _resolved_str(self, p: str) -> str:
        """Best-effort absolute/normalised string for comparing file paths."""
        try:
            return str(Path(p).expanduser().resolve())
        except Exception:
            return str(Path(p).expanduser().absolute())

    def _update_session_label(self) -> None:
        lbl = getattr(self, "lbl_session", None)
        if lbl is None:
            return
        p = self.var_positions_file.get().strip()
        n = len(self._db.positions) if (self._db and self._db.positions) else 0
        try:
            name = Path(p).name
        except Exception:
            name = p
        lbl.configure(text=f"Active file: {name}   ({n} position{'' if n == 1 else 's'})")

    def _new_session(self) -> None:
        """Start a brand-new, empty session positions file. Nothing is loaded."""
        new_path = self._new_session_positions_path()
        self.var_positions_file.set(str(new_path))
        self._db = PositionDatabase()
        self._refresh_tree()
        self._refresh_mill_queue()
        self._update_session_label()
        self._enqueue_log(f"[UI] Started new session. Positions file: {new_path}\n")

    def _load_previous_positions(self) -> None:
        """Deliberately load a positions file from a previous session (opt-in).

        Per the chosen behaviour, the session then operates on that file directly.
        A stale-coordinates warning is raised by _load_positions().
        """
        p = filedialog.askopenfilename(
            title="Load positions from a previous session",
            filetypes=[("JSON", "*.json"), ("All", "*")],
        )
        if not p:
            return
        self.var_positions_file.set(p)
        self._load_positions()  # not silent -> stale-coords warning fires inside

    def _positions_path(self) -> Optional[Path]:
        p = self.var_positions_file.get().strip()
        if not p:
            messagebox.showerror("Missing positions file", "Select a positions JSON file.")
            return None
        return Path(p).expanduser()

    def _load_positions(self, silent: bool = False) -> None:
        pos_file = self._positions_path()
        if pos_file is None:
            return

        if not pos_file.exists():
            self._db = PositionDatabase()
            self._refresh_tree()
            self._refresh_mill_queue()
            self._update_session_label()
            if not silent:
                self._enqueue_log(f"[UI] positions file does not exist yet: {pos_file}\n")
            return

        try:
            self._db = PositionDatabase.load(pos_file)
            self._refresh_tree()
            self._refresh_mill_queue()
            self._update_session_label()
            if not silent:
                self._enqueue_log(f"[UI] Loaded positions: {pos_file}\n")

            # SAFETY: warn (once per file) when the loaded file is NOT the file this
            # session launched with and it actually contains positions. Those coordinates
            # were captured against a previous sample / stage baseline, so moving or
            # milling to them risks a stage collision.
            if not silent and self._db and self._db.positions:
                cur = self._resolved_str(str(pos_file))
                if cur != self._resolved_str(self._launch_session_file) and cur not in self._warned_stale_paths:
                    self._warned_stale_paths.add(cur)
                    messagebox.showwarning(
                        "Positions loaded from a previous session",
                        "These positions were captured in a previous session.\n\n"
                        "Stage coordinates (X/Y/Z/M), rotation and eucentric height may not "
                        "match the current sample or stage baseline. Moving or milling to them "
                        "could cause a stage collision.\n\n"
                        "Verify the stage is safe and re-check eucentric height before milling.",
                    )
        except Exception as e:
            if not silent:
                messagebox.showerror("Failed to load positions", f"{e}")
            self._db = None
            self._refresh_tree()
            self._refresh_mill_queue()
            self._update_session_label()

    def _refresh_tree(self) -> None:
        # Clear
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        if not self._db or not self._db.positions:
            self.lbl_details.configure(text="(no positions loaded)")
            self.img_label.configure(text="(no image)")
            return

        names = natural_position_sort(list(self._db.positions.keys()))
        for name in names:
            rec = self._db.positions[name]
            st = rec.stage_at
            self.tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    name,
                    f"{rec.fib_objective_potential_v:.3f}",
                    f"{st.x_m:.3e}",
                    f"{st.y_m:.3e}",
                    f"{st.z_m:.3e}",
                    f"{st.r_deg:.3f}",
                    f"{st.m_m:.3e}",
                ),
            )

    def _refresh_mill_queue(self) -> None:
        """Populate/refresh the Milling Queue table from the loaded PositionDatabase.

        Preserves any existing check states and user ordering when possible.
        """
        if not hasattr(self, "queue_tree"):
            return

        # Preserve current state
        prev_checked = {}
        prev_af = {}
        prev_refine = {}
        prev_order: List[str] = []
        try:
            for item in self.queue_tree.get_children(""):
                vals = list(self.queue_tree.item(item, "values"))
                if len(vals) >= 2:
                    name = str(vals[1])
                    prev_order.append(name)
                    prev_checked[name] = (str(vals[0]) == CHECK_ON)
                    if len(vals) >= 5:
                        prev_af[name] = (str(vals[3]) == CHECK_ON)
                        prev_refine[name] = (str(vals[4]) == CHECK_ON)
        except Exception:
            prev_checked = {}
            prev_order = []

        # Clear
        for item in self.queue_tree.get_children(""):
            self.queue_tree.delete(item)

        self._queue_item_by_name = {}

        if not self._db or not self._db.positions:
            return

        names = natural_position_sort(list(self._db.positions.keys()))
        if prev_order:
            # Keep previous order as much as possible
            kept = [n for n in prev_order if n in self._db.positions]
            rest = [n for n in names if n not in kept]
            names = kept + rest

        for name in names:
            rec = self._db.positions[name]
            st = rec.stage_at
            checked = prev_checked.get(name, True)
            af_on = prev_af.get(name, False)
            rf_on = prev_refine.get(name, False)
            iid = self.queue_tree.insert(
                "",
                tk.END,
                values=(
                    CHECK_ON if checked else CHECK_OFF,
                    name,
                    "",
                    CHECK_ON if af_on else CHECK_OFF,
                    CHECK_ON if rf_on else CHECK_OFF,
                    f"{rec.fib_objective_potential_v:.3f}",
                    f"{st.x_m:.3e}",
                    f"{st.y_m:.3e}",
                    f"{st.z_m:.3e}",
                    f"{st.r_deg:.3f}",
                    f"{st.m_m:.3e}",
                ),
            )
            self._queue_item_by_name[name] = iid

    def _selected_position_name(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        return str(sel[0])

    def _on_select_position(self, _evt=None) -> None:
        name = self._selected_position_name()
        if not name or not self._db:
            return
        rec = self._db.positions.get(name)
        if not rec:
            return

        st = rec.stage_at
        lines = [
            f"Name: {rec.name}",
            f"Captured: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rec.captured_unix_s))}",
            f"FIB Mag: {rec.fib_magnification_x:.6g}",
            f"FIB Objective Potential: {rec.fib_objective_potential_v:.6f} V",
            "",
            f"Stage AT X: {st.x_m:.6e} m",
            f"Stage AT Y: {st.y_m:.6e} m",
            f"Stage AT Z: {st.z_m:.6e} m",
            f"Stage AT R: {st.r_deg:.6f} deg",
            f"Stage AT M: {st.m_m:.6e} m",
        ]
        if st.t_deg is not None:
            lines.append(f"Stage AT T: {st.t_deg:.6f} deg")

        img_path = self._resolve_image_path(rec.image_bmp)
        lines += ["", f"Image: {img_path if img_path else '(none)'}"]

        self.lbl_details.configure(text="\n".join(lines))
        self._show_image(img_path)

    def _resolve_image_path(self, image_field: Optional[str]) -> Optional[Path]:
        if not image_field:
            return None
        pos_file = self._positions_path()
        if pos_file is None:
            return None
        p = Path(image_field)
        if not p.is_absolute():
            p = pos_file.parent / p
        return p

    def _open_selected_image(self) -> None:
        if not self._db:
            return
        name = self._selected_position_name()
        if not name:
            messagebox.showinfo("No selection", "Select a position first.")
            return
        rec = self._db.positions.get(name)
        if not rec:
            return
        img_path = self._resolve_image_path(rec.image_bmp)
        if not img_path or not img_path.exists():
            messagebox.showerror("No image", "This position has no image, or the file is missing.")
            return
        try:
            _open_in_default_app(img_path)
        except Exception as e:
            messagebox.showerror("Failed to open", str(e))

    def _scaled_photo(self, path, box=(360, 300)):
        """Load an image path scaled to fit `box`, returning a PhotoImage or None."""
        try:
            if Image is None or ImageTk is None or not path:
                return None
            p = Path(path)
            if not p.exists():
                return None
            img = Image.open(p).convert("RGB")
            w, h = img.size
            scale = min(box[0] / w, box[1] / h, 1.0)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _show_coincidence_images(self) -> None:
        """Display the captured SEM + FIB overview images side by side."""
        sem_tk = self._scaled_photo(self._coinc_sem_path)
        fib_tk = self._scaled_photo(self._coinc_fib_path)
        self._coinc_sem_tk = sem_tk
        self._coinc_fib_tk = fib_tk
        if sem_tk is not None:
            self.coinc_sem_label.configure(image=sem_tk, text="")
        elif self._coinc_sem_path:
            self.coinc_sem_label.configure(
                image="", text=f"SEM image at:\n{self._coinc_sem_path}"
                + ("" if (Image and ImageTk) else "\n(install Pillow to preview)"))
        if fib_tk is not None:
            self.coinc_fib_label.configure(image=fib_tk, text="")
        elif self._coinc_fib_path:
            self.coinc_fib_label.configure(
                image="", text=f"FIB image at:\n{self._coinc_fib_path}"
                + ("" if (Image and ImageTk) else "\n(install Pillow to preview)"))
        # Bring the Tools tab forward so the operator sees the pair.
        try:
            self._show_page("tools")
        except Exception:
            pass

    def _show_image(self, path: Optional[Path]) -> None:
        if path is None or not path.exists():
            if Image is None:
                return
            self.img_label.configure(image="", text="(no image)")
            self._current_image_tk = None
            return

        if Image is None or ImageTk is None:
            # Pillow not installed
            self.img_label.configure(text=f"Image available at:\n{path}\n\nInstall Pillow to preview BMP files.")
            return

        try:
            img = Image.open(path)
            # Convert to RGB for Tk
            img = img.convert("RGB")

            # Fit to label size
            max_w = max(200, self.img_label.winfo_width() - 20)
            max_h = max(200, self.img_label.winfo_height() - 20)
            w, h = img.size
            scale = min(max_w / w, max_h / h, 1.0)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img2 = img.resize(new_size)

            tk_img = ImageTk.PhotoImage(img2)
            self._current_image_tk = tk_img
            self.img_label.configure(image=tk_img, text="")
        except Exception as e:
            self.img_label.configure(text=f"Failed to load image:\n{e}")
            self._current_image_tk = None

    # ---------------- Logging ----------------

    def _enqueue_log(self, line: str) -> None:
        self._log_queue.put(line)

    def _process_output_line(self, line: str) -> None:
        """Parse CLI output lines to drive queue progress + live readout.

        We intentionally keep this lightweight and pattern-based so it stays
        compatible with the CLI behavior you've already validated.
        """

        if self._last_cmd_kind == "coincidence":
            t = line.strip()
            if t.startswith("SEM_IMAGE:"):
                self._coinc_sem_path = t.split("SEM_IMAGE:", 1)[1].strip()
            elif t.startswith("FIB_IMAGE:"):
                self._coinc_fib_path = t.split("FIB_IMAGE:", 1)[1].strip()
            return

        if self._last_cmd_kind != "mill":
            return

        s = line.strip("\n")

        # [1/8] Milling at position_1
        m = re.search(r"\[(\d+)/(\d+)\]\s+Milling at\s+(\S+)", s)
        if m:
            idx = int(m.group(1))
            total = int(m.group(2))
            name = m.group(3)
            self._queue_total = total
            self._queue_running_name = name
            self.var_live_current.set(name)
            self.var_live_event.set(f"[{idx}/{total}] Milling started")
            self._queue_mark_status(name, "Running")
            return

        # [1/8] Completed milling at position_1
        m = re.search(r"\[(\d+)/(\d+)\]\s+Completed milling at\s+(\S+)", s)
        if m:
            idx = int(m.group(1))
            total = int(m.group(2))
            name = m.group(3)
            self._queue_total = total
            self._queue_done = max(self._queue_done, idx)
            self._queue_running_name = None
            self._queue_mark_status(name, "Done")
            self.var_live_event.set(f"[{idx}/{total}] Completed")

            # Progress bar
            if hasattr(self, "queue_progress"):
                try:
                    self.queue_progress.configure(maximum=max(1, total))
                    self.queue_progress["value"] = idx
                except Exception:
                    pass
            if hasattr(self, "var_queue_progress"):
                self.var_queue_progress.set(f"Queue: {idx}/{total} completed")
            return

        # Error at position_1: <msg>
        m = re.search(r"^Error at\s+(\S+):\s+(.*)$", s)
        if m:
            name = m.group(1)
            msg = m.group(2)
            self._queue_mark_status(name, "Error")
            self.var_live_event.set(f"Error at {name}: {msg}")
            return

        #   [wait]  123.4s elapsed | DP_FIB_MODE=2 | DP_FIB_BLANKED=No
        m = re.search(
            r"\[wait\]\s+([0-9.]+)s\s+elapsed\s+\|\s+DP_FIB_MODE=([^|]+)\|\s+DP_FIB_BLANKED=([^\s]+)",
            s,
        )
        if m:
            elapsed = m.group(1)
            mode = m.group(2).strip()
            blanked = m.group(3).strip()
            self.var_live_wait.set(f"{elapsed}s | DP_FIB_MODE={mode} | DP_FIB_BLANKED={blanked}")
            return

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                # Process for live readout / queue progress (main thread safe)
                try:
                    self._process_output_line(line)
                except Exception:
                    pass
                self.txt_log.insert(tk.END, line)
                self.txt_log.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _on_cmd_done(self, result: RunResult) -> None:
        self._enqueue_log(f"\n[UI] Command finished rc={result.returncode}\n")
        if self._last_cmd_kind == "mill":
            try:
                if result.returncode == 0:
                    self.var_live_event.set("Milling queue finished")
                else:
                    self.var_live_event.set(f"Milling queue stopped (rc={result.returncode})")
            except Exception:
                pass
        # Reload positions after capture/mill
        if self._last_cmd_kind in ("capture", "mill"):
            self._load_positions(silent=True)
        # Show the SEM + FIB overview images from a coincidence adjust.
        if self._last_cmd_kind == "coincidence":
            self._show_coincidence_images()

    def _connection_args(self) -> List[str]:
        # Pass through CLI connection options
        connect = (self.var_connect.get().strip() or "remoting").lower()
        args: List[str] = ["--connect", connect]
        if connect == "local":
            mach = self.var_machine.get().strip()
            if mach:
                args += ["--machine", mach]
        return args


def launch_gui(*, positions_file: Optional[str] = None, ely_file: Optional[str] = None, timeout_s: int = 3600) -> None:
    app = SmartFIBExecutorUI(positions_file=positions_file, ely_file=ely_file, timeout_s=timeout_s)
    app.mainloop()
