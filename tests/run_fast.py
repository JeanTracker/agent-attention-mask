"""The layer that does not need a terminal: run this as often as you like.

Two tiers, and the line between them is what a fake clock can stand in for
(D-054). The cases here drive pure state machines and pure layout functions,
so they finish in about a second. What they cannot cover is bytes reaching a
real terminal -- the scrollback invariant (P-103), the latency budgets
(SC-002), the alternate-screen sequences -- and that is `run_all.py`, which
takes minutes and is what you run before pushing.
"""

import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    ["test_phase8_inputline.py"],
    ["test_phase11_judge.py"],
    # The same suite as `run_all.py` runs, minus the cases that start a
    # runner: its TUI, config-file and protocol checks are pure.
    ["test_phase10_control.py", "--fast"],
]

failed = []
for suite in SUITES:
    print(f"\n===== {' '.join(suite)} =====", flush=True)
    if subprocess.call([sys.executable] + suite, cwd=HERE) != 0:
        failed.append(suite[0])

print()
if failed:
    print(f"실패한 스위트: {', '.join(failed)}")
    sys.exit(1)
print("빠른 계층 전부 통과 — 터미널 계층은 `python3 tests/run_all.py`")
