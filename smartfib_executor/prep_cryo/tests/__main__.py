from __future__ import annotations
import sys
from . import test_prep
if __name__ == "__main__":
    print("="*60); print("prep_cryo tests"); print("="*60)
    sys.exit(test_prep._run_all())
