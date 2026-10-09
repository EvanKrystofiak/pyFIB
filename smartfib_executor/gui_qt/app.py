"""pyFIB Qt GUI -- Step 1 scaffold.

Proves the shell for the re-skin: the shared theme, a left nav that switches a
QStackedWidget of pages, and a Log page fed live by a subprocess worker (the same
model the tkinter GUI uses -- it shells out to the CLI). Later phases fill the
Capture / Positions / Mill / Tools pages with real controls; the engine and CLI are
untouched.

Runs only where qtpy/PyQt5 is installed. It can be smoke-tested headless with
QT_QPA_PLATFORM=offscreen (see tests/), which is why a Qt GUI is more verifiable than
the tkinter one.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .theme import apply_theme, scale_up
from .session import new_session_positions_path
from .worker import SubprocessWorker, build_argv, build_module_argv
from .pages import ArmBar, CapturePage, PositionsPage, MillPage, SetupPage, GisdepTab, ElymakerTab
from .tools_page import ToolsPage

APP_TITLE = "pyFIB"
NAV = [
    ("setup", "Setup"),
    ("gisdep", "GISDEP"),
    ("elymaker", "ELYMaker"),
    ("capture", "Capture"),
    ("positions", "Positions"),
    ("mill", "Rough Mill"),      # v0.50.0: was "Mill" (key kept: same page, same tests)
    ("prepolish", "Prepolish"),  # v0.51.0: own pattern; aligns to the rough after image
    ("polish", "Polish"),        # v0.51.0: own pattern; aligns to the prepolish image
    ("tools", "Tools"),          # v0.44.0: report, SEM autofocus, FIB autofocus
    ("log", "Log"),
]


class _Placeholder(QWidget):
    """A stand-in page used until a real page is built (Phase 2+)."""

    def __init__(self, title: str, note: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        h = QLabel(title)
        h.setObjectName("pageTitle")
        f = h.font(); f.setPointSize(20); f.setBold(True); h.setFont(f)
        lay.addWidget(h)
        if note:
            sub = QLabel(note)
            sub.setWordWrap(True)
            sub.setStyleSheet("color: palette(mid);")
            lay.addWidget(sub)
        lay.addStretch(1)


class LogPage(QWidget):
    """Live output pane fed by the subprocess worker."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        title = QLabel("Log")
        f = title.font(); f.setPointSize(20); f.setBold(True); title.setFont(f)
        lay.addWidget(title)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.view.clear)
        row.addStretch(1)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)

    def append(self, text: str) -> None:
        self.view.appendPlainText(text)


class MainWindow(QMainWindow):
    def __init__(self, positions_file: Optional[str] = None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 900)
        self._worker: Optional[SubprocessWorker] = None
        self._preload_positions = positions_file

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Shared top bar: ARM toggle + positions-file path (visible on every page).
        self.arm_bar = ArmBar(on_change=self._on_arm_change,
                              on_new_session=self._on_new_session)
        outer.addWidget(self.arm_bar)

        content = QWidget()
        outer.addWidget(content, 1)
        root = QHBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- left nav ---
        nav = QWidget()
        nav.setObjectName("navBar")
        nav.setFixedWidth(210)
        nav_lay = QVBoxLayout(nav)
        nav_lay.setContentsMargins(10, 14, 10, 14)
        nav_lay.setSpacing(6)
        brand = QLabel(APP_TITLE)
        bf = brand.font(); bf.setPointSize(20); bf.setBold(True); brand.setFont(bf)
        nav_lay.addWidget(brand)
        nav_lay.addSpacing(10)

        self._nav_buttons: Dict[str, QPushButton] = {}
        self._pages: Dict[str, QWidget] = {}
        self.stack = QStackedWidget()

        for key, label in NAV:
            btn = QPushButton(label)
            btn.setObjectName("toolButton")   # shared theme styles this
            btn.setCheckable(True)
            btn.setMinimumHeight(44)
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            nav_lay.addWidget(btn)
            self._nav_buttons[key] = btn

        nav_lay.addStretch(1)

        # --- pages ---
        self.log_page = LogPage()
        self.capture_page = CapturePage(self)
        self.positions_page = PositionsPage(self)
        self.mill_page = MillPage(self, title="Rough Mill", stage="rough")
        self.prepolish_page = MillPage(self, title="Prepolish", stage="prepolish")
        self.polish_page = MillPage(self, title="Polish", stage="polish")
        self.setup_page = SetupPage(self)
        self.gisdep_page = GisdepTab(self)
        self.elymaker_page = ElymakerTab(self)
        self.tools_page = ToolsPage(self)
        self._pages["setup"] = self.setup_page
        self._pages["gisdep"] = self.gisdep_page
        self._pages["elymaker"] = self.elymaker_page
        self._pages["capture"] = self.capture_page
        self._pages["positions"] = self.positions_page
        self._pages["mill"] = self.mill_page
        self._pages["prepolish"] = self.prepolish_page
        self._pages["polish"] = self.polish_page
        self._pages["tools"] = self.tools_page
        self._pages["log"] = self.log_page
        for key, _ in NAV:
            self.stack.addWidget(self._pages[key])

        root.addWidget(nav)
        root.addWidget(self.stack, 1)

        # If the launcher passed an explicit positions file, load it; otherwise start
        # in a FRESH session (a unique timestamped positions dir, created on the first
        # capture). Load Previous... in the top bar opens an old one.
        if self._preload_positions:
            self.arm_bar.pos_edit.setText(str(self._preload_positions))
        else:
            self.arm_bar.pos_edit.setText(str(new_session_positions_path()))
        self.statusBar().showMessage("Ready")
        self.show_page("setup")

        # A tiny self-check button on the Tools placeholder is overkill for step 1;
        # instead, a menu action proves the subprocess plumbing end to end.
        act = self.menuBar().addMenu("&Run")
        a = act.addAction("Self-check: run 'list-positions --help'")
        a.triggered.connect(self._self_check)

    # --- navigation ---
    def show_page(self, key: str) -> None:
        if key not in self._pages:
            return
        self.stack.setCurrentWidget(self._pages[key])
        for k, btn in self._nav_buttons.items():
            btn.setChecked(k == key)
        if key == "positions" and self.positions_file():
            self.positions_page.reload()
        if key in ("mill", "prepolish", "polish") and self.positions_file():
            self._pages[key].reload()
        if key == "tools":
            self.tools_page.on_show()
        if key == "gisdep":
            self.gisdep_page.ensure_built()
        if key == "elymaker":
            self.elymaker_page.ensure_built()
        if key == "setup" and self.positions_file():
            self.setup_page.reload_positions()

    def mill_pages(self):
        """The three milling stages, in order (v0.50.0)."""
        return [self.mill_page, self.prepolish_page, self.polish_page]

    # --- shared state the pages read ---
    def is_armed(self) -> bool:
        return self.arm_bar.is_armed()

    def positions_file(self) -> str:
        return self.arm_bar.positions_file()

    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    def confirm(self, message: str) -> bool:
        from qtpy.QtWidgets import QMessageBox
        return QMessageBox.question(
            self, "Confirm", message,
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _on_arm_change(self, armed: bool) -> None:
        self.status("ARMED — instrument commands will execute for real."
                    if armed else "Dry-run — instrument commands print the plan only.")
        # Keep the embedded gisdep tab's arm state in lock-step with pyFIB's toggle.
        if getattr(self, "gisdep_page", None) is not None:
            self.gisdep_page.sync_arm()

    def _on_new_session(self) -> None:
        # Fresh session: clear stale table/dropdown data (the new file has no records
        # until the first capture writes to it).
        try:
            self.positions_page.reload()
            for p in self.mill_pages():
                p.reload()
            self.setup_page.reload_positions()
        except Exception:
            pass
        self.status("New session started.")

    # --- subprocess plumbing (the model kept from tkinter) ---
    def run_argv(self, argv: List[str], *, input_text: Optional[str] = None,
                 on_line=None, on_done=None, show_log: bool = True) -> bool:
        """Launch any argv on the worker, streaming stdout to the Log page.

        ``on_line`` also receives each line (pages use it for live status parsing);
        ``on_done`` gets the return code. This is the shared core for run_cli /
        run_module -- the subprocess model, unchanged. show_log=False (v0.51.0, the
        milling tabs' manual buttons) keeps the current page on screen; the output still
        goes to the Log page.
        """
        if self._worker is not None and self._worker.isRunning():
            self.statusBar().showMessage("A command is already running")
            return False
        if show_log:
            self.show_page("log")
        self._worker = SubprocessWorker(argv, input_text=input_text)
        self._worker.line.connect(self.log_page.append)
        if on_line is not None:
            self._worker.line.connect(on_line)
        self._worker.failed.connect(lambda m: self.log_page.append(f"[UI ERROR] {m}"))
        self._worker.finished_rc.connect(self._on_rc)
        if on_done is not None:
            self._worker.finished_rc.connect(on_done)
        self.statusBar().showMessage("Running…")
        self._worker.start()
        return True

    def run_cli(self, subcmd_args: List[str], *, input_text: Optional[str] = None,
                on_line=None, on_done=None, show_log: bool = True) -> bool:
        """Launch a pyFIB CLI subcommand (python -u -m smartfib_executor.cli ...)."""
        return self.run_argv(build_argv(subcmd_args), input_text=input_text,
                             on_line=on_line, on_done=on_done, show_log=show_log)

    def run_module(self, module: str, args: List[str], *,
                   input_text: Optional[str] = None, on_line=None, on_done=None) -> bool:
        """Launch a standalone pyFIB module (e.g. the coincidence tool)."""
        return self.run_argv(build_module_argv(module, args), input_text=input_text,
                             on_line=on_line, on_done=on_done)

    def _on_rc(self, rc: int) -> None:
        self.log_page.append(f"[exit {rc}]")
        self.statusBar().showMessage("Done" if rc == 0 else f"Exited with code {rc}")

    def _self_check(self) -> None:
        # Harmless: prints the CLI help. Proves argv build + streaming + exit handling.
        self.run_cli(["list-positions", "--help"])


def main(argv=None, positions_file=None) -> int:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    apply_theme(app)
    scale_up(app)
    win = MainWindow(positions_file=positions_file)
    win.show()
    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
