from __future__ import annotations
import sys
from . import test_setup_move
if __name__ == "__main__":
    print("="*60); print("setup_move tests"); print("="*60)
    sys.exit(test_setup_move._run_all())
