from __future__ import annotations
import sys
from . import test_smoke
if __name__ == "__main__":
    print("="*60); print("gui_qt smoke test"); print("="*60)
    sys.exit(test_smoke._run_all())
