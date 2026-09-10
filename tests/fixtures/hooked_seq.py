"""An agent that replays a scripted hook sequence, timings and all.

`hooked.py` covers the ordinary shape -- prompt, tool call, Stop -- but the
cases in D-031 and D-032 turn on *when* an event arrives relative to Stop and
on which turn it claims to belong to. Those need the sequence spelled out, so
this reads one from HOOKED_SEQ as JSON:

    [[delay_seconds, "EventName", {"prompt_id": "p1", ...}], ...]

Delays are cumulative from start. Output is deliberately at odds with the
events -- silent while working, chattering afterwards -- so a test can tell
whether the runner believed the hooks or the timing heuristic.
"""

import json
import os
import sys
import threading
import time

FIFO = os.environ.get("AMASK_HOOK_FIFO", "")
TOKEN = os.environ.get("AMASK_HOOK_TOKEN", "")
SEQ = json.loads(os.environ.get("HOOKED_SEQ", "[]"))
TOTAL = float(os.environ.get("HOOKED_TOTAL", "14"))


def emit(event, extra):
    if not FIFO:
        return
    record = {"event": event, "token": TOKEN}
    record.update(extra or {})
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (json.dumps(record) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


def play():
    started = time.monotonic()
    for delay, event, extra in SEQ:
        wait = float(delay) - (time.monotonic() - started)
        if wait > 0:
            time.sleep(wait)
        emit(event, extra)


sys.stdout.write("ready\n")
sys.stdout.flush()


def echo_stdin():
    """Report every byte that reaches us, so D-008 can be tested by absence."""
    while True:
        data = os.read(0, 1024)
        if not data:
            return
        sys.stdout.write("SAW:%s" % data.decode("utf-8", "replace"))
        sys.stdout.flush()


threading.Thread(target=play, daemon=True).start()
threading.Thread(target=echo_stdin, daemon=True).start()

# An idle prompt's repaint, running the whole time: it must never on its own
# convince the runner that there is work happening.
deadline = time.monotonic() + TOTAL
while time.monotonic() < deadline:
    sys.stdout.write("\x1b[2;1Hidle")
    sys.stdout.flush()
    time.sleep(0.4)
