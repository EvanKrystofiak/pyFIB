"""Run the refine test suite: python -m smartfib_executor.refine.tests"""
from __future__ import annotations
import sys
from . import test_refine


def main() -> int:
    print("=" * 60); print("test_refine"); print("=" * 60)
    return test_refine._run_all()


if __name__ == "__main__":
    sys.exit(main())
