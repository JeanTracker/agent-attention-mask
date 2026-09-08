"""An agent that reports its own state through the runner's hook FIFO.

Claude Code fires the real events; this stands in for it so the runner's
handling can be tested without one. It follows the same contract: write one
JSON object per line to the FIFO named by AMASK_HOOK_FIFO, stamped with
AMASK_HOOK_TOKEN so the runner knows the event is from its own agent.

Its output deliberately contradicts the timing heuristic -- it goes silent
while "working" and chatters while "waiting" -- so a test can tell which of the
two the runner actually believed.
"""

import json
import os
import sys
import time

FIFO = os.environ.get("AMASK_HOOK_FIFO", "")
TOKEN = os.environ.get("AMASK_HOOK_TOKEN", "")
WORK = float(os.environ.get("HOOKED_WORK", "6"))
PROMPT = os.environ.get("HOOKED_PROMPT", "")


def emit(event, **extra):
    if not FIFO:
        return
    record = {"event": event, "token": TOKEN}
    record.update(extra)
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (json.dumps(record) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


sys.stdout.write("ready\n")
sys.stdout.flush()
time.sleep(0.5)

emit("UserPromptSubmit", prompt=PROMPT or "analyse the failing test")
emit("PreToolUse", tool_name="Bash")

# Silent for the whole tool call: the timing heuristic would call this idle and
# uncover the screen. The hooks say otherwise.
time.sleep(WORK)

emit("PostToolUse", tool_name="Bash")
emit("Stop")

# Now chatter, the way an idle prompt repaints. Nothing here is work.
for _ in range(60):
    sys.stdout.write("\x1b[2;1Hidle")
    sys.stdout.flush()
    time.sleep(0.4)
