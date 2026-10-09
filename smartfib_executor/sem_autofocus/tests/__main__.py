"""Run the SEM autofocus tests: python -m smartfib_executor.sem_autofocus.tests"""
from __future__ import annotations
import sys
from . import test_sem_autofocus, test_curve, test_two_stage


def main() -> int:
    print("=" * 60); print("test_sem_autofocus"); print("=" * 60)
    rc = test_sem_autofocus._run_all()
    print("=" * 60); print("test_curve"); print("=" * 60)
    rc = test_curve._run_all() or rc
    print("=" * 60); print("test_two_stage"); print("=" * 60)
    return test_two_stage._run_all() or rc


if __name__ == "__main__":
    sys.exit(main())
