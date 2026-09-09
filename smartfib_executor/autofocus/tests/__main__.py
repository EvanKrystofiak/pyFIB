"""Run every autofocus test suite with one command.

    python -m smartfib_executor.autofocus.tests

Suites:
    test_autofocus     -- pure core: metrics, sweep planning, refinement, search
    test_real_data     -- replays measured curves from REAL Crossbeam 550 FIB images
    test_scope_source  -- the real instrument path against a mock SmartSEM

None of these need hardware, pywin32, or a display.
"""

from __future__ import annotations

import sys


def main() -> int:
    from . import test_autofocus, test_real_data, test_scope_source

    total_failed = 0
    for mod in (test_autofocus, test_real_data, test_scope_source):
        name = mod.__name__.rsplit(".", 1)[-1]
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
        total_failed += mod._run_all()

    print(f"\n{'=' * 60}")
    print("ALL SUITES PASSED" if total_failed == 0
          else f"{total_failed} SUITE(S) HAD FAILURES")
    print("=" * 60)
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
