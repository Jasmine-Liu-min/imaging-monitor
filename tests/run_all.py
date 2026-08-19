"""Run the whole test suite without requiring pytest (CI entrypoint).

Usage: python3 tests/run_all.py
"""
from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODULES = [
    "tests.test_store",
    "tests.test_rules",
    "tests.test_report",
    "tests.test_ai_fallback",
    "tests.smoke_test",
]


def main() -> int:
    failures = 0
    for mod_name in MODULES:
        mod = importlib.import_module(mod_name)
        print(f"== {mod_name} ==")
        try:
            rc = mod.main()
            if rc:
                failures += 1
                print(f"FAILED {mod_name} (rc={rc})")
        except AssertionError as exc:
            failures += 1
            print(f"FAILED {mod_name}: {exc}")
    print("\nALL OK" if not failures else f"\n{failures} module(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
