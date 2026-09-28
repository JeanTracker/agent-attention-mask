"""Works for a while, then waits for a line and echoes it.

Stands in for an agent that finishes a task and then asks the user something --
the case where a wake keystroke must not be mistaken for the answer (D-008).
"""

import sys
import time

seconds = float(sys.argv[1])
# Optional: stamp the wall-clock moment the work ends, so a test can measure
# the silence before a wake rather than time since the fork (which also
# counts however long this interpreter took to start).
stamp = len(sys.argv) > 2 and sys.argv[2] == "stamp"
started = time.monotonic()
while time.monotonic() - started < seconds:
    sys.stdout.write(".")
    sys.stdout.flush()
    time.sleep(0.1)
if stamp:
    sys.stdout.write(f"\nDONE_AT:{time.time():.3f}\n")
    sys.stdout.flush()

line = sys.stdin.readline().rstrip("\n").rstrip("\r")
sys.stdout.write(f"\nGOT:{line}\n")
sys.stdout.flush()
