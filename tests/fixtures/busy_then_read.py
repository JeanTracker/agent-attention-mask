"""Works for a while, then waits for a line and echoes it.

Stands in for an agent that finishes a task and then asks the user something --
the case where a wake keystroke must not be mistaken for the answer (D-008).
"""

import sys
import time

seconds = float(sys.argv[1])
started = time.monotonic()
while time.monotonic() - started < seconds:
    sys.stdout.write(".")
    sys.stdout.flush()
    time.sleep(0.1)

line = sys.stdin.readline().rstrip("\n").rstrip("\r")
sys.stdout.write(f"\nGOT:{line}\n")
sys.stdout.flush()
