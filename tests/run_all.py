"""Run every phase suite; exit non-zero if any check fails.

This is the terminal layer: it starts real runners on real ptys and takes
minutes, most of it waiting. `run_fast.py` is the tier that does not need a
terminal and finishes in a couple of seconds -- run that while you work, and
this before you push (D-054).
""" 

import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    "test_phase1_passthrough.py",
    "test_phase3_overlay.py",
    "test_phase4_idle.py",
    "test_phase5_resources.py",
    "test_phase6_lifecycle.py",
    "test_phase7_presentation.py",
    "test_phase8_inputline.py",
    "test_phase9_hooks.py",
    "test_phase10_control.py",
    "test_phase11_judge.py",
]

failed = []
for suite in SUITES:
    print(f"\n===== {suite} =====", flush=True)
    if subprocess.call([sys.executable, suite], cwd=HERE) != 0:
        failed.append(suite)

print()
if failed:
    print(f"실패한 스위트: {', '.join(failed)}")
    sys.exit(1)
print("전체 통과")
