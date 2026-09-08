"""An agent that streams an answer with realistic pauses, then reports tokens.

Modelled on a measured `claude` response: a ~114ms median between chunks, but
with occasional pauses approaching a second, and a status line whose token
count climbs as the answer grows. Both of those broke the overlay -- the pauses
uncovered the screen mid-answer, and the panel kept showing the first token
count it ever saw.
"""

import os
import sys
import time

SECONDS = float(os.environ.get("STREAM_SECONDS", "6"))
GAP_AT = [float(x) for x in os.environ.get("STREAM_GAPS", "").split(",") if x]
GAP_LEN = float(os.environ.get("STREAM_GAP_LEN", "0.8"))

started = time.monotonic()
tokens = 0
pending = list(GAP_AT)
while True:
    now = time.monotonic() - started
    if now >= SECONDS:
        break
    if pending and now >= pending[0]:
        pending.pop(0)
        time.sleep(GAP_LEN)
        continue
    tokens += 17
    sys.stdout.write(f"\x1b[2;1Hstreaming... {tokens} tokens")
    sys.stdout.flush()
    time.sleep(0.1)

sys.stdout.write(f"\ndone {tokens} tokens\n")
sys.stdout.flush()
time.sleep(30)
