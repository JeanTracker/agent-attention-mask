"""A line-oriented agent that works for a while, like a build or `claude -p`.

Usage: chatty.py <seconds> [mark_at_seconds] [mark_text]
Prints a tick every 100ms so the runner sees it as busy, and optionally emits
a one-off marker partway through so a test can tell when it was produced.
"""

import sys
import time

seconds = float(sys.argv[1])
mark_at = float(sys.argv[2]) if len(sys.argv) > 2 else None
mark = sys.argv[3] if len(sys.argv) > 3 else ""

started = time.monotonic()
marked = mark_at is None
while True:
    now = time.monotonic() - started
    if now >= seconds:
        break
    if not marked and now >= mark_at:
        sys.stdout.write(mark + "\n")
        marked = True
    sys.stdout.write(".")
    sys.stdout.flush()
    time.sleep(0.1)
