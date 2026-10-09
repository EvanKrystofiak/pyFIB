"""Run the refine test suite: python -m smartfib_executor.refine.tests"""
from __future__ import annotations
import sys
from . import test_refine, test_calibrate_stage, test_offset_test


def main() -> int:
    print("=" * 60); print("test_refine"); print("=" * 60)
    rc1 = test_refine._run_all()
    rc2 = _run_stage_cal()
    print("=" * 60); print("test_offset_test"); print("=" * 60)
    rc3 = test_offset_test._run_all()
    return rc1 or rc2 or rc3


def _run_stage_cal():
    print("="*60); print("test_calibrate_stage"); print("="*60)
    return test_calibrate_stage._run_all()

if __name__ == "__main__":
    sys.exit(main())
