"""Run the coincidence test suite: python -m smartfib_executor.coincidence.tests"""
from __future__ import annotations
import sys
from . import test_coincidence
if __name__ == "__main__":
    print("=" * 60); print("test_coincidence"); print("=" * 60)
    sys.exit(test_coincidence._run_all())
