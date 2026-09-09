"""Subprocess worker for the Qt GUI.

Same model pyFIB has always used: the GUI shells out to the existing CLI
(``python -u -m ...``) and streams the child's stdout line-by-line. Here that runs
on a ``QThread`` and forwards each line as a Qt signal, so the engine, the CLI, and
all the safety logic are untouched -- this is a re-skin, not a rewrite.

The worker is import-safe without Qt (it degrades to a no-op base) so the rest of the
package can be imported and unit-tested where Qt is absent; the real class is used
only when Qt is available.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

try:  # Qt is optional at import time
    from qtpy.QtCore import QThread, Signal
    _HAVE_QT = True
except Exception:  # pragma: no cover - exercised only where Qt is absent
    QThread = object  # type: ignore
    Signal = None  # type: ignore
    _HAVE_QT = False


def build_argv(subcmd_args: List[str]) -> List[str]:
    """Build the argv for a pyFIB CLI subcommand, unbuffered for live streaming.

    ``-u`` matters: without it the child block-buffers stdout when it is a pipe, so
    the GUI would only see output at process exit (the buffering bug we fixed once
    already). Kept here so every Qt-launched command streams live.
    """
    return [sys.executable, "-u", "-m", "smartfib_executor.cli", *subcmd_args]


def build_module_argv(module: str, args: List[str]) -> List[str]:
    """argv for a standalone pyFIB module (e.g. the coincidence tool), unbuffered."""
    return [sys.executable, "-u", "-m", module, *args]


if _HAVE_QT:

    class SubprocessWorker(QThread):
        """Run a CLI command off the UI thread, streaming stdout as signals."""

        line = Signal(str)               # one line of child stdout (newline stripped)
        finished_rc = Signal(int)        # process return code when it exits
        failed = Signal(str)             # a launch/read error (not a child error)

        def __init__(self, argv: List[str], cwd: Optional[Path] = None,
                     input_text: Optional[str] = None, parent=None):
            super().__init__(parent)
            self._argv = list(argv)
            self._cwd = cwd
            self._input_text = input_text
            self._proc: Optional[subprocess.Popen] = None
            self._stop = False

        def run(self) -> None:  # QThread entry point
            try:
                self.line.emit("$ " + " ".join(self._argv))
                self._proc = subprocess.Popen(
                    self._argv,
                    cwd=str(self._cwd) if self._cwd else None,
                    stdin=(subprocess.PIPE if self._input_text is not None else None),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if self._input_text is not None and self._proc.stdin is not None:
                    try:
                        self._proc.stdin.write(self._input_text)
                        self._proc.stdin.flush()
                        self._proc.stdin.close()
                    except Exception:
                        pass
                assert self._proc.stdout is not None
                for raw in self._proc.stdout:
                    self.line.emit(raw.rstrip("\n"))
                    if self._stop:
                        break
                rc = self._proc.wait()
                self.finished_rc.emit(int(rc))
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))
                self.finished_rc.emit(1)

        def request_stop(self) -> None:
            """Ask the child to stop (best-effort terminate)."""
            self._stop = True
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass

else:  # pragma: no cover - placeholder when Qt is unavailable

    class SubprocessWorker:  # type: ignore
        def __init__(self, *a, **k):
            raise RuntimeError(
                "SubprocessWorker requires qtpy/PyQt5, which is not installed here.")
