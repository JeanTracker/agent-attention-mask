"""Run every phase suite; exit non-zero if any check fails."""

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
