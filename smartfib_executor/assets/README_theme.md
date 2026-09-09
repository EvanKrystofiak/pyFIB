# Shared dark theme (fib_dark_theme)

The same look elymaker uses, packaged as one portable file so gisdep (or any
qtpy / PyQt5 / PySide2 app) can match it.

## Apply it (recommended)

Copy `fib_dark_theme.py` next to your app and call it once at startup, before
building windows:

```python
import sys
from qtpy.QtWidgets import QApplication
import fib_dark_theme

app = QApplication.instance() or QApplication(sys.argv)
fib_dark_theme.apply_theme(app)      # Fusion base + dark palette + stylesheet
# ... build your main window ...
```

`apply_theme()` does three things: sets the Fusion base style, applies a dark
palette (so native-drawn widgets match), then the stylesheet. Using only the
`.qss` (below) skips the palette, so some native bits stay light — prefer the
module.

## Or apply just the stylesheet

```python
with open("fib_dark_theme.qss") as fh:
    app.setStyleSheet(fh.read())
```

## Matching elymaker's touches

- Give a prominent/toggle button `objectName("toolButton")` for the big rounded,
  accent-on-checked treatment used by elymaker's Tools dock.
- `fib_dark_theme.ACCENT`, `BG`, `TEXT`, etc. are exported if you paint custom
  widgets; `CANVAS_BG/GRID/AXIS` are there if gisdep has a canvas.

Retune colours at the top of `fib_dark_theme.py` and everything moves together.
The colour constants are identical to elymaker's `elymaker/gui/style.py`, so the
two apps stay in sync by construction — if you'd rather I fold this exact file
into the elymaker package as a shared export (so there's a single source), say
so and I'll wire it in.
