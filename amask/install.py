"""Put amask on PATH, wire the agents to it, and take all of it away again.

`install.sh` fetches the clone to a fixed place and hands over to this for
the symlink, the PATH line and uninstalling; `amask --wrap`/`--unwrap` come
here for the wrapper functions. All of it is here, where it can be tested without a network and without a shell's
`sed` dialect (D-059).

The README used to say "clone, then symlink", and a clone made in whatever
directory the user stood in ended up inside someone's project, where the
project's own clean-up removed it. The clone now lives in one place, and
everything this writes outside it is either a symlink or a single marked
block in one rc file -- the two things uninstall knows how to find.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys

BEGIN = "# >>> amask >>>"
END = "# <<< amask <<<"
_NOTE = "# Written by amask (install.sh, amask --wrap); changes inside this block are replaced."
_META = re.compile(r"^# amask: (path|wrap) ?(.*)$")
_AGENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def say(message):
    print(f"amask: {message}")


class SetupError(Exception):
    """Something the user has to resolve; nothing was changed for it."""


# -- where things go ---------------------------------------------------------


def default_home():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.environ.get("AMASK_HOME") or os.path.join(data, "amask")


def default_bin():
    return os.environ.get("AMASK_BIN") or os.path.expanduser("~/.local/bin")


def _zdot():
    return os.environ.get("ZDOTDIR") or os.path.expanduser("~")


def default_rc():
    """The rc file the user's login shell reads for an interactive session."""
    if os.environ.get("AMASK_RC"):
        return os.environ["AMASK_RC"]
    shell = os.path.basename(os.environ.get("SHELL", ""))
    if shell == "zsh":
        return os.path.join(_zdot(), ".zshrc")
    if shell == "bash":
        # macOS Terminal starts a login shell, which reads .bash_profile.
        name = ".bash_profile" if platform.system() == "Darwin" else ".bashrc"
        return os.path.expanduser(f"~/{name}")
    return os.path.expanduser("~/.profile")


def rc_candidates(chosen):
    """Every rc file a block may have been written to, for taking it out."""
    home = os.path.expanduser("~")
    names = [chosen, os.path.join(_zdot(), ".zshrc")]
    names += [os.path.join(home, n) for n in (".bashrc", ".bash_profile", ".profile")]
    seen = []
    for name in names:
        if name not in seen and os.path.isfile(name):
            seen.append(name)
    return seen


def _shell_path(path):
    """A path as the rc file should spell it: under $HOME, through $HOME."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return '"$HOME' + path[len(home):] + '"'
    return '"' + path + '"'


# -- the rc block ------------------------------------------------------------


def read_block(text):
    """(lines before, block state, lines after); state is None with no block.

    The state is read from the block's own `# amask:` lines rather than from
    the shell code, so the code can change shape between releases and a
    rewrite still knows what the user asked for.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.rstrip("\n") == BEGIN), None)
    if start is None:
        return lines, None, []
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].rstrip("\n") == END), None)
    if end is None:
        raise SetupError("an amask block in the rc file has no end marker; "
                         f"fix it by hand (look for '{BEGIN}')")
    state = {"path": None, "wrap": {}}
    for line in lines[start + 1:end]:
        match = _META.match(line.rstrip("\n"))
        if not match:
            continue
        kind, value = match.groups()
        if kind == "path":
            state["path"] = value
        else:
            agent, _, target = value.partition(" ")
            if agent:
                state["wrap"][agent] = target or agent
    return lines[:start], state, lines[end + 1:]


def render_block(state):
    """The block for a state, or "" when there is nothing left to say."""
    if not state["path"] and not state["wrap"]:
        return ""
    out = [BEGIN, _NOTE]
    if state["path"]:
        out += [f"# amask: path {state['path']}",
                f"export PATH={_shell_path(state['path'])}:\"$PATH\""]
    for agent in sorted(state["wrap"]):
        target = state["wrap"][agent]
        run = agent if target == agent else _shell_path(target)
        # unalias first: an `alias claude='amask claude'` from the old README
        # would expand inside `claude() {` and make it a syntax error.
        out += [f"# amask: wrap {agent} {target}",
                f"unalias {agent} 2>/dev/null",
                f"{agent}() {{",
                f"  if command -v amask >/dev/null 2>&1; then amask {run} \"$@\"",
                f"  else command {run} \"$@\"; fi",
                "}"]
    out.append(END)
    return "\n".join(out) + "\n"


def update_rc(rc, change):
    """Apply `change(state)` to rc's block; write only if the text differs."""
    try:
        with open(rc) as handle:
            text = handle.read()
    except FileNotFoundError:
        text = ""
    before, state, after = read_block(text)
    state = state or {"path": None, "wrap": {}}
    change(state)
    block = render_block(state)
    if before and not before[-1].endswith("\n"):
        before[-1] += "\n"
    new = "".join(before) + block + "".join(after)
    if new != text:
        os.makedirs(os.path.dirname(rc) or ".", exist_ok=True)
        # Written in place, not replaced: a dotfile managed as a symlink stays one.
        with open(rc, "w") as handle:
            handle.write(new)
    return state


def remove_block(rc):
    """Take the whole block out of rc. True if there was one."""
    with open(rc) as handle:
        text = handle.read()
    before, state, after = read_block(text)
    if state is None:
        return False
    if before and not before[-1].endswith("\n"):
        before[-1] += "\n"
    with open(rc, "w") as handle:
        handle.write("".join(before) + "".join(after))
    return True


def stray_aliases(rc, agent):
    """Line numbers of `alias <agent>=` outside our block."""
    try:
        with open(rc) as handle:
            text = handle.read()
    except FileNotFoundError:
        return []
    pattern = re.compile(rf"^\s*alias\s+{re.escape(agent)}=")
    numbers, inside = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line == BEGIN:
            inside = True
        elif line == END:
            inside = False
        elif not inside and pattern.match(line):
            numbers.append(number)
    return numbers


# -- the steps ---------------------------------------------------------------


def link(home, bindir):
    """Point bindir/amask at the clone's launcher, replacing any symlink."""
    target = os.path.join(home, "bin", "amask")
    if not os.path.isfile(target):
        raise SetupError(f"no launcher at {target}; is {home} an amask clone?")
    os.makedirs(bindir, exist_ok=True)
    name = os.path.join(bindir, "amask")
    if os.path.islink(name):
        old = os.readlink(name)
        if old == target:
            say(f"{name} already points at {home}")
            return
        os.unlink(name)
        say(f"replaced the old link to {old}"
            + ("" if os.path.exists(old) else " (it was broken)"))
    elif os.path.lexists(name):
        raise SetupError(f"{name} exists and is not a symlink; move it away "
                         "and run this again (it was left untouched)")
    os.symlink(target, name)
    say(f"linked {name} -> {target}")


def ensure_path(bindir, rc):
    """Add bindir to PATH in rc unless the shell already has it."""
    on_path = os.path.normpath(bindir) in (
        os.path.normpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    state = read_block(_read(rc))[1]
    if state and state["path"]:
        say(f"{bindir} is already put on PATH by {rc}")
        return
    if on_path:
        say(f"{bindir} is on PATH")
        return

    def add(state):
        state["path"] = bindir

    update_rc(rc, add)
    say(f"added {bindir} to PATH in {rc}; open a new shell to pick it up")


def _read(path):
    try:
        with open(path) as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def agent_target(agent):
    """What the wrapper should run: the name, or an older claude's own path."""
    if shutil.which(agent):
        return agent
    if agent == "claude":
        local = os.path.expanduser("~/.claude/local/claude")
        if os.access(local, os.X_OK):
            return local
    say(f"warning: {agent} is not on PATH; the wrapper calls it by name")
    return agent


def wrap(agent, rc):
    if not _AGENT.match(agent):
        raise SetupError(f"cannot wrap {agent!r}: not a plain command name")
    target = agent_target(agent)

    def add(state):
        state["wrap"][agent] = target

    update_rc(rc, add)
    say(f"{agent} now runs under amask in new shells; `command {agent}` skips it, "
        f"`amask --unwrap {agent}` undoes it")
    for number in stray_aliases(rc, agent):
        say(f"note: {rc}:{number} still has an `alias {agent}=`; "
            "it is overridden only if it comes before the amask block -- remove it")


def unwrap(agent, rcs):
    """Drop one agent's wrapper (all of them for None) from every rc."""
    found = False
    for rc in rcs:
        state = read_block(_read(rc))[1]
        if not state or not state["wrap"]:
            continue
        names = list(state["wrap"]) if agent is None else [agent]
        if not any(n in state["wrap"] for n in names):
            continue

        def drop(state):
            for name in names:
                state["wrap"].pop(name, None)

        update_rc(rc, drop)
        found = True
        say(f"removed the {', '.join(n for n in names)} wrapper from {rc}")
    if not found:
        say("no amask wrapper found" + (f" for {agent}" if agent else ""))
    else:
        say("open a new shell, or `unset -f` the function in this one")


def _live_sessions():
    from amask import control
    return [info.get("pid", "?") for info in control.sessions()]


def _is_clone(home):
    return (os.path.isfile(os.path.join(home, "amask", "cli.py"))
            and os.path.isfile(os.path.join(home, "bin", "amask")))


def uninstall(home, bindir, rcs):
    live = _live_sessions()
    if live:
        say(f"warning: amask is still running (pid {', '.join(map(str, live))}); "
            "those sessions keep going, but lose their hooks")
    # Check before removing anything, so a refusal leaves everything in place.
    if os.path.isdir(home):
        if not _is_clone(home):
            raise SetupError(f"{home} does not look like an amask clone; "
                             "not removing it (set AMASK_HOME if it lives elsewhere)")
        dirty = subprocess.run(["git", "-C", home, "status", "--porcelain"],
                               capture_output=True, text=True)
        if dirty.returncode == 0 and dirty.stdout.strip():
            raise SetupError(f"{home} has local changes; commit or discard "
                             "them first -- nothing was removed")
    for rc in rcs:
        read_block(_read(rc))  # an unclosed block raises here, not halfway

    name = os.path.join(bindir, "amask")
    if os.path.islink(name) and os.path.basename(os.readlink(name)) == "amask":
        old = os.readlink(name)
        os.unlink(name)
        say(f"removed {name}")
        old_home = os.path.dirname(os.path.dirname(old))
        if os.path.normpath(old_home) != os.path.normpath(home) and os.path.isdir(old_home):
            say(f"note: it pointed at another clone, {old_home}, left in place")
    elif os.path.lexists(name):
        say(f"left {name} alone: it is not amask's symlink")

    for rc in rcs:
        if remove_block(rc):
            say(f"removed the amask block from {rc}")
            for agent in ("claude", "codex"):
                for number in stray_aliases(rc, agent):
                    say(f"note: {rc}:{number} still has an `alias {agent}=` -- remove it")

    if os.path.isdir(home):
        shutil.rmtree(home)
        say(f"removed {home}")
    if os.path.isdir(os.path.expanduser("~/.amask")):
        say("~/.amask (stored defaults, control sockets) is kept; "
            "`rm -rf ~/.amask` removes it")
    say("open a new shell, or `unset -f claude codex` in this one")


# -- entry -------------------------------------------------------------------


def parse(argv):
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Install, wire up or remove amask. With no option it "
                    "installs: clone or update, link, and check PATH.")
    parser.add_argument("--home", default=default_home(),
                        help="where the clone lives (AMASK_HOME)")
    parser.add_argument("--bin", default=default_bin(),
                        help="where the amask link goes (AMASK_BIN)")
    parser.add_argument("--rc", default=None,
                        help="the rc file to write (AMASK_RC; default from $SHELL)")
    parser.add_argument("--install", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--link", action="store_true",
                        help="only (re)create the amask symlink")
    parser.add_argument("--path", action="store_true",
                        help="only check PATH, adding the link's directory to the rc file")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the clone, the symlink and the rc block")
    args = parser.parse_args(argv)
    if args.uninstall and (args.link or args.path):
        parser.error("--uninstall takes no other action")
    return args


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    home = os.path.abspath(os.path.expanduser(args.home))
    bindir = os.path.abspath(os.path.expanduser(args.bin))
    rc = os.path.abspath(os.path.expanduser(args.rc or default_rc()))
    try:
        if args.uninstall:
            uninstall(home, bindir, rc_candidates(rc))
            return 0
        if args.install or args.link:
            link(home, bindir)
        if args.install or args.path:
            ensure_path(bindir, rc)
    except SetupError as exc:
        print(f"amask: {exc}", file=sys.stderr)
        return 1
    return 0


def wrap_main(agents):
    """`amask --wrap AGENT...`: the installed amask wires the agents itself."""
    if not agents:
        print("usage: amask --wrap <agent>...   (claude, codex)", file=sys.stderr)
        return 2
    rc = os.path.abspath(os.path.expanduser(default_rc()))
    try:
        for agent in agents:
            wrap(agent, rc)
    except SetupError as exc:
        print(f"amask: {exc}", file=sys.stderr)
        return 1
    return 0


def unwrap_main(agents):
    """`amask --unwrap [AGENT...]`: every wrapped agent when none is named."""
    rcs = rc_candidates(os.path.abspath(os.path.expanduser(default_rc())))
    try:
        for agent in agents or [None]:
            unwrap(agent, rcs)
    except SetupError as exc:
        print(f"amask: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
