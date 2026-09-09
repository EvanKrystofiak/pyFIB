"""Run the probe test suite: python -m smartfib_executor.probe.tests"""
from __future__ import annotations
import sys
from . import test_probe


def main() -> int:
    print("=" * 60); print("test_probe"); print("=" * 60)
    return test_probe._run_all()


if __name__ == "__main__":
    sys.exit(main())
