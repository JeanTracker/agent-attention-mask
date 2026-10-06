"""Under an alias, the agent's name must behave exactly as without one (D-058).

`alias claude='amask claude'` sends every run of the name through amask, the
agent's own commands included, and a user cannot be expected to remember
which ones to type as `command claude`. D-057 was that going wrong:
`claude plugin test` refused to run because amask put options in front of
`plugin`.

So every command the installed agents list in their own `--help`, and the
info flags, run twice on a real pty -- once as the agent, once as
`amask <agent>` -- and the two must print the same and exit the same. The
list comes from the binaries, so a command added in a later release is
checked the day it appears. An agent that is not installed is skipped.
"""

import os
import pty
import re
import select
import shutil
import struct
import fcntl
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amask import cli

AMASK = os.path.join(ROOT, "bin", "amask")
FAILURES = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


def on_pty(argv, timeout=30.0):
    """Run argv on a 40x120 pty; (output, exit code)."""
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        try:
            os.execvp(argv[0], argv)
        except OSError:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    out = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
    else:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0)
    os.close(master)
    return bytes(out), os.waitstatus_to_exitcode(status)


def same(agent, args):
    plain = on_pty([agent] + args)
    wrapped = on_pty([AMASK, agent] + args)
    label = f"{agent} {' '.join(args)}"
    check(f"{label}: same exit code", plain[1] == wrapped[1], f"{plain[1]} vs {wrapped[1]}")
    check(f"{label}: same output", plain[0] == wrapped[0],
          f"{len(plain[0])}B vs {len(wrapped[0])}B; wrapped starts {wrapped[0][:120]!r}")


for agent in ("claude", "codex"):
    binary = shutil.which(agent)
    if binary is None:
        print(f"[SKIP] {agent} is not installed here")
        continue
    commands = cli._agent_commands(binary)
    check(f"{agent} lists its commands", commands, "no Commands block in --help")
    for args in (["--version"], ["--help"]):
        same(agent, args)
    for command in sorted(commands or ()):
        same(agent, [command, "--help"])

print()
if FAILURES:
    print(f"{len(FAILURES)} failed")
    sys.exit(1)
print("all passed")
