from __future__ import annotations
import sys
from . import test_workflow, test_positions_schema
if __name__ == "__main__":
    print("="*60); print("test_workflow"); print("="*60)
    rc1 = test_workflow._run_all()
    print("="*60); print("test_positions_schema"); print("="*60)
    rc2 = test_positions_schema._run_all()
    sys.exit(rc1 or rc2)
