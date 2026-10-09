"""python -m smartfib_executor.report.tests  -- all report / run-log tests."""
from __future__ import annotations

import sys

from . import (test_overlay, test_runlog, test_report, test_pattern_diagram, test_mosaic,
               test_z_model)

if __name__ == "__main__":
    rc = 0
    for mod in (test_overlay, test_runlog, test_report, test_pattern_diagram, test_mosaic,
                test_z_model):
        print("=" * 60); print(mod.__name__.rsplit(".", 1)[-1]); print("=" * 60)
        rc = mod._run_all() or rc
    sys.exit(rc)
