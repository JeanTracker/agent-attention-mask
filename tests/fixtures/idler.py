"""An agent that paints once, then only blips -- an idle interactive prompt.

Measured against a real idle `claude`: a burst at startup, then a short
repaint every 8-10 seconds and nothing in between. Compressed here so a test
can watch several blips go by in a few seconds.

Optionally does a stretch of real work first, so a test can check that genuine
work is still recognised by the same fixture.
"""

import os
import sys
import time

BLIP_EVERY = float(os.environ.get("IDLER_BLIP_EVERY", "1.5"))
WORK_FIRST = float(os.environ.get("IDLER_WORK_FIRST", "0"))

sys.stdout.write("\x1b[2J\x1b[HPROMPT READY")
sys.stdout.flush()

if WORK_FIRST:
    started = time.monotonic()
    while time.monotonic() - started < WORK_FIRST:
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(0.1)

while True:
    time.sleep(BLIP_EVERY)
    # A cursor/status repaint: small, and far too short to be work.
    sys.stdout.write("\x1b[1;1HBLIP")
    sys.stdout.flush()
