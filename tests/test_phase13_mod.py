"""The Claude Code mod and the gate in front of it, without a terminal (D-056).

An interrupted turn fires no Claude Code hook, so amask ships a mod that
reports it, and attaches it only to an interactive claude new enough to raise
what the mod listens for. What can be checked without a live agent is here:
the version gate, the command line, and the path from the emitter to the
runner's reading of `TurnAborted`. The mod's own behaviour is in
`mod/test/` for `claude plugin test`; whether a real ESC produces the event is
`tests/probe_hooks.py --plugin-dir mod --interrupt ...`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amask import cli, hooks
from amask.hooks import HookChannel

EMITTER = os.path.join(ROOT, "bin", "amask-hook")
FAILURES = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


# -- the version gate --------------------------------------------------------

check("parse claude's own output", cli._parse_version("2.1.287 (Claude Code)") == (2, 1, 287))
check("parse a bare version", cli._parse_version("3.0.0") == (3, 0, 0))
check("garbage is no version", cli._parse_version("Claude Code") is None)
check("nothing is no version", cli._parse_version("") is None and cli._parse_version(None) is None)

check("2.1.285 loads mods but raises no turn events", not cli._mod_supported((2, 1, 285)))
check("2.1.286 is the floor", cli._mod_supported((2, 1, 286)))
check("later releases pass", cli._mod_supported((2, 2, 0)) and cli._mod_supported((3, 0, 0)))
check("an unknown version keeps the mod off", not cli._mod_supported(None))

work = tempfile.mkdtemp(prefix="amask-mod-")


def fake(name, body):
    path = os.path.join(work, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755)
    return path


got = cli._claude_version(fake("new", 'echo "2.1.290 (Claude Code)"'))
check("asks the binary itself", got == (2, 1, 290), repr(got))
got = cli._claude_version(fake("fails", 'echo "2.1.290"; exit 1'))
check("a failing --version is unknown", got is None, repr(got))
got = cli._claude_version(fake("odd", 'echo "usage: claude [options]"'))
check("unparseable output is unknown", got is None, repr(got))
got = cli._claude_version(os.path.join(work, "absent"))
check("a missing binary is unknown", got is None, repr(got))

# -- the command line --------------------------------------------------------

check("--settings seen", cli._passes_settings(["claude", "--settings", "{}"]))
check("--settings= seen too", cli._passes_settings(["claude", "--settings={}"]))
check("a prompt mentioning it is not it", not cli._passes_settings(["claude", "-p", "use --settings x"]))

# -- what is a session (D-058) -----------------------------------------------

CLAUDE_HELP = """Usage: claude [options] [command] [prompt]

Commands:
  agents [options]                      Manage background agents
  mcp                                   Configure and manage MCP servers
  plugin|plugins                        Manage Claude Code plugins
  stop|kill <id|name>                   Stop a background session
  update|upgrade                        Check for updates
"""
CODEX_HELP = """Usage: codex [OPTIONS] [PROMPT]

Commands:
  exec              Run Codex non-interactively [aliases: e]
  login             Manage login
  apply             Apply the latest diff produced by Codex agent as a `git apply` to your local
                    working tree [aliases: a]
  help              Print this message or the help of the given subcommand(s)

Arguments:
  [PROMPT]
"""
got = cli._parse_commands(CLAUDE_HELP)
check("claude's commands, | aliases included",
      got == {"agents", "mcp", "plugin", "plugins", "stop", "kill", "update", "upgrade"}, sorted(got))
got = cli._parse_commands(CODEX_HELP)
check("codex's commands, [aliases] included",
      got == {"exec", "e", "login", "apply", "a", "help"}, sorted(got))
check("no Commands block, no commands", cli._parse_commands("Usage: x\n") == frozenset())

claude_cmds = lambda _: cli._parse_commands(CLAUDE_HELP)
codex_cmds = lambda _: cli._parse_commands(CODEX_HELP)
asked = []
def never(binary):
    asked.append(binary)
    return None

for argv, want in [
    (["claude"], True), (["claude", "--resume"], True), (["claude", "-c"], True),
    (["claude", "fix the bug"], True), (["claude", "--model", "opus"], True),
    (["claude", "plugin", "test", "mod"], False), (["claude", "--debug", "mcp", "list"], False),
    (["claude", "upgrade"], False), (["claude", "--version"], False), (["claude", "-p", "--help"], False),
    (["/opt/claude/bin/claude", "mcp"], False),
]:
    check(f"claude {' '.join(argv[1:]) or '(bare)'} -> {'session' if want else 'step aside'}",
          cli._is_session(argv, claude_cmds) == want)

for argv, want in [
    (["codex"], True), (["codex", "fix the bug"], True), (["codex", "exec", "make tests pass"], True),
    (["codex", "e", "x"], True), (["codex", "resume", "--last"], True),
    (["codex", "login"], False), (["codex", "a"], False), (["codex", "help"], False),
    (["codex", "exec", "--help"], False), (["codex", "-V"], False),
]:
    check(f"codex {' '.join(argv[1:]) or '(bare)'} -> {'session' if want else 'step aside'}",
          cli._is_session(argv, codex_cmds) == want)

check("other agents are wrapped as before", cli._is_session(["ls", "-l"], never) and not asked)
check("a bare session never asks --help", cli._is_session(["claude", "--resume"], never) and not asked)
check("an agent that cannot say is left alone", cli._is_session(["claude", "something"], never) is False)

check("-p is a print run", cli._is_print(["claude", "-p", "hi"]))
check("--print is a print run", cli._is_print(["claude", "--print", "hi"]))
check("interactive is not", not cli._is_print(["claude", "--resume"]))

got = cli._wire_claude(["claude", "--resume", "x"], "{}", "/clone/mod")
check("ours first, the user's after",
      got == ["claude", "--settings", "{}", "--plugin-dir", "/clone/mod", "--resume", "x"], repr(got))
got = cli._wire_claude(["claude", "--resume"], "{}", None)
check("no mod, no --plugin-dir", got == ["claude", "--settings", "{}", "--resume"], repr(got))

check("the mod ships beside the package",
      cli._mod_path() == os.path.join(ROOT, "mod")
      and os.path.isfile(os.path.join(ROOT, "mod", "hooks", "register.ts")))

# -- emitter to runner -------------------------------------------------------

check("TurnAborted means waiting", hooks.EVENT_MEANING.get("TurnAborted") == hooks.WAITING)
fragment = HookChannel(EMITTER, path="/tmp/unused").settings()["hooks"]
check("and is no Claude Code hook", "TurnAborted" not in fragment, sorted(fragment))

channel = HookChannel(EMITTER)
if channel.open():
    # What the mod runs: the emitter, the FIFO, the event, the token, `{}` on stdin.
    subprocess.run([EMITTER, channel.path, "TurnAborted", channel.token], input=b"{}")
    time.sleep(0.1)
    events = channel.read_events()
    channel.close()
    check("the mod's call reaches the runner",
          [(e.get("event"), e.get("token")) for e in events] == [("TurnAborted", channel.token)],
          repr(events))
else:
    check("FIFO for the emitter check", False, "could not create one")

# -- the mod's manifest, by the installed claude ----------------------------

claude = shutil.which("claude")
if claude is None:
    print("[SKIP] claude plugin validate -- claude is not installed here")
else:
    done = subprocess.run([claude, "plugin", "validate", os.path.join(ROOT, "mod")],
                          capture_output=True, text=True, timeout=60)
    check("claude plugin validate mod", done.returncode == 0, (done.stdout + done.stderr)[-400:])

shutil.rmtree(work, ignore_errors=True)

print()
if FAILURES:
    print(f"{len(FAILURES)} failed")
    sys.exit(1)
print("all passed")
