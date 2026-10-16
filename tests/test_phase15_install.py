"""Installing, wiring and uninstalling amask, in a throwaway HOME (D-059).

The report that started this: a clone made inside someone's project was
cleaned away with the project, and every tool call in the still-running
claude session printed `amask-hook: No such file or directory`. So: the hook
command must say nothing when its emitter is gone, and `install.sh` must put
the clone where no project can reach it, replace whatever link the old README
left, and take back exactly what it wrote and nothing else.

Everything runs against a local git repository made from this working tree,
so no network is needed and the tree under test is the one being tested.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amask.hooks import HookChannel

FAILURES = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


# -- the hook command --------------------------------------------------------

work = tempfile.mkdtemp(prefix="amask-install-test-")
try:
    gone = os.path.join(work, "moved-away", "bin", "amask-hook")
    command = HookChannel(gone, path="/tmp/unused").settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    done = subprocess.run(["/bin/sh", "-c", command], input=b"{}", capture_output=True)
    check("a vanished emitter: exit 0", done.returncode == 0, done.returncode)
    check("a vanished emitter: no stderr", done.stderr == b"", done.stderr)

    channel = HookChannel(os.path.join(ROOT, "bin", "amask-hook"))
    if channel.open():
        command = channel.settings()["hooks"]["Stop"][0]["hooks"][0]["command"]
        subprocess.run(["/bin/sh", "-c", command], input=b"{}")
        time.sleep(0.1)
        events = channel.read_events()
        channel.close()
        check("the guarded command still delivers",
              [(e.get("event"), e.get("token")) for e in events] == [("Stop", channel.token)],
              repr(events))
    else:
        check("FIFO for the guarded command", False, "could not create")

    # -- a repository to install from ---------------------------------------

    repo = os.path.join(work, "repo")
    os.makedirs(repo)
    for part in ("amask", "bin", "mod"):
        shutil.copytree(os.path.join(ROOT, part), os.path.join(repo, part),
                        ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    # .gitignore too: it is what keeps a used clone's __pycache__ from
    # reading as a local change, here as in the real repository.
    for name in ("install.sh", ".gitignore"):
        shutil.copy2(os.path.join(ROOT, name), repo)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master"]
    subprocess.run(git + ["init", "-q", repo], check=True)
    subprocess.run(git + ["-C", repo, "add", "-A"], check=True)
    subprocess.run(git + ["-C", repo, "commit", "-qm", "x"], check=True)

    home = os.path.join(work, "home")
    os.makedirs(home)
    dest = os.path.join(home, ".local", "share", "amask")
    link = os.path.join(home, ".local", "bin", "amask")
    rc = os.path.join(home, ".zshrc")
    fakes = os.path.join(work, "fakes")
    os.makedirs(fakes)
    system_path = "/usr/bin:/bin:/usr/sbin:/sbin"
    env = {
        "HOME": home, "SHELL": "/bin/zsh", "PATH": f"{fakes}:{system_path}",
        "AMASK_REPO": repo, "TMPDIR": work,
    }
    if shutil.which("git"):
        env["PATH"] += ":" + os.path.dirname(shutil.which("git"))
    env["PATH"] = os.path.dirname(sys.executable) + ":" + env["PATH"]

    def install(*args, stdin_script=False):
        if stdin_script:
            with open(os.path.join(ROOT, "install.sh"), "rb") as handle:
                return subprocess.run(["sh", "-s", "--"] + list(args), input=handle.read(),
                                      env=env, capture_output=True, text=False, cwd=work)
        return subprocess.run(["sh", os.path.join(ROOT, "install.sh")] + list(args),
                              env=env, capture_output=True, cwd=work)

    def amask(*args):
        """The installed amask, through the link install.sh made."""
        return subprocess.run([link] + list(args), env=env, capture_output=True, cwd=work)

    def read(path):
        with open(path) as handle:
            return handle.read()

    FOREIGN_TOP = "export EDITOR=vim\nalias claude='amask claude'\n"
    FOREIGN_TAIL = "# the user's own last line, no newline"
    with open(rc, "w") as handle:
        handle.write(FOREIGN_TOP)

    # -- install ------------------------------------------------------------

    os.makedirs(os.path.dirname(link))
    os.symlink(os.path.join(work, "old-project", "amask", "bin", "amask"), link)  # the old README's, broken

    done = install(stdin_script=True)  # what `curl | sh` does
    out = done.stdout.decode() + done.stderr.decode()
    check("curl | sh installs", done.returncode == 0, out)
    check("the clone is in the fixed place", os.path.isfile(os.path.join(dest, "amask", "cli.py")), out)
    check("the broken link is replaced", os.readlink(link) == os.path.join(dest, "bin", "amask"),
          os.readlink(link))
    check("and says so", "it was broken" in out, out)
    text = read(rc)
    check("PATH is added in a block", '# >>> amask >>>' in text and
          'export PATH="$HOME/.local/bin":"$PATH"' in text, text)
    check("the user's lines are kept, in front", text.startswith(FOREIGN_TOP), text)

    with open(rc, "a") as handle:
        handle.write(FOREIGN_TAIL)
    before = read(rc)
    done = install()
    check("installing again updates", done.returncode == 0 and b"updated" in done.stdout,
          done.stdout + done.stderr)
    check("and leaves the rc file as it was", read(rc) == before, read(rc))
    check("and does not claim to add PATH again", b"added" not in done.stdout, done.stdout)

    os.unlink(link)
    os.symlink(os.path.join(work, "elsewhere", "bin", "amask"), link)
    done = install("--link")
    check("--link replaces a link to another clone",
          done.returncode == 0 and os.readlink(link) == os.path.join(dest, "bin", "amask"),
          done.stdout + done.stderr)

    os.unlink(link)
    with open(link, "w") as handle:
        handle.write("#!/bin/sh\necho mine\n")
    done = install("--link")
    check("a regular file is refused", done.returncode == 1, done.returncode)
    check("and left alone", read(link) == "#!/bin/sh\necho mine\n", read(link))
    os.unlink(link)
    install("--link")

    # -- wrap ---------------------------------------------------------------

    with open(os.path.join(fakes, "claude"), "w") as handle:
        handle.write('#!/bin/sh\necho "real claude $*"\n')
    os.chmod(os.path.join(fakes, "claude"), 0o755)

    done = amask("--wrap", "claude")
    check("amask --wrap wraps", done.returncode == 0 and "claude() {" in read(rc),
          done.stdout + done.stderr)
    check("--wrap points at the old alias", b"still has an `alias claude=`" in done.stdout,
          done.stdout)
    once = read(rc)
    amask("--wrap", "claude")
    check("--wrap twice writes it once", read(rc) == once and once.count("claude() {") == 1, read(rc))

    def run_in(shell, line, path):
        script = f'. "{rc}"\n{line}\n'
        if shell == "bash":  # aliases are off in a non-interactive bash; turn them on
            script = "shopt -s expand_aliases\n" + script
        # A HOME without the real link, or the block's PATH line would put
        # the installed amask in front of the fake one.
        return subprocess.run([shell, "-c", script], capture_output=True, text=True,
                              env=dict(env, PATH=path, HOME=work))

    with open(os.path.join(fakes, "amask"), "w") as handle:
        handle.write('#!/bin/sh\necho "amask $*"\n')
    os.chmod(os.path.join(fakes, "amask"), 0o755)
    for shell in ("bash", "zsh"):
        if not shutil.which(shell):
            print(f"[SKIP] {shell} is not installed")
            continue
        done = run_in(shell, "claude hi", env["PATH"])
        check(f"{shell}: the old alias does not break the function",
              done.returncode == 0 and done.stderr == "", done.stderr)
        check(f"{shell}: claude runs under amask", done.stdout.strip() == "amask claude hi",
              done.stdout)
        no_amask = os.path.join(work, "no-amask")
        os.makedirs(no_amask, exist_ok=True)
        shutil.copy2(os.path.join(fakes, "claude"), no_amask)
        done = run_in(shell, "claude hi", f"{no_amask}:{system_path}")
        check(f"{shell}: without amask, claude still runs", done.stdout.strip() == "real claude hi",
              done.stdout + done.stderr)

    done = amask("--unwrap", "claude")
    text = read(rc)
    check("--unwrap removes the function", done.returncode == 0 and "claude() {" not in text, text)
    check("--unwrap keeps the PATH line", "export PATH=" in text, text)
    check("--unwrap keeps the user's lines",
          text.startswith(FOREIGN_TOP) and text.rstrip("\n").endswith(FOREIGN_TAIL.rstrip()), text)

    amask("--wrap", "claude", "codex")
    done = amask("--unwrap")
    text = read(rc)
    check("--unwrap with no name takes every wrapper",
          done.returncode == 0 and "claude() {" not in text and "codex() {" not in text, text)
    check("--wrap with no agent is a usage error", amask("--wrap").returncode == 2)
    check("install.sh leaves wrapping to amask", install("--wrap", "claude").returncode != 0)

    # -- uninstall ----------------------------------------------------------

    amask("--wrap", "codex")
    with open(os.path.join(dest, "amask", "cli.py"), "a") as handle:
        handle.write("# a local edit\n")
    done = install("--uninstall")
    check("a clone with local changes is not removed",
          done.returncode == 1 and os.path.isdir(dest) and os.path.islink(link)
          and "# >>> amask >>>" in read(rc), done.stdout + done.stderr)
    subprocess.run(["git", "-C", dest, "checkout", "-q", "--", "."], check=True)

    done = install("--uninstall")
    out = done.stdout.decode()
    check("--uninstall succeeds", done.returncode == 0, out + done.stderr.decode())
    check("the clone is gone", not os.path.exists(dest), dest)
    check("the link is gone", not os.path.lexists(link), link)
    text = read(rc)
    check("the block is gone", "amask >>>" not in text and "codex() {" not in text, text)
    check("only the block is gone", text == FOREIGN_TOP + FOREIGN_TAIL + "\n"
          or text == FOREIGN_TOP + FOREIGN_TAIL, repr(text))

    # The way README says: the installed clone's own script removes itself.
    install()
    amask("--wrap", "claude")
    with open(os.path.join(dest, "notes.txt"), "w") as handle:
        handle.write("mine\n")
    done = subprocess.run(["sh", os.path.join(dest, "install.sh"), "--uninstall"],
                          env=env, capture_output=True, cwd=work)
    check("an untracked file of yours keeps the clone",
          done.returncode == 1 and os.path.isdir(dest), done.stdout + done.stderr)
    os.unlink(os.path.join(dest, "notes.txt"))
    done = subprocess.run(["sh", os.path.join(dest, "install.sh"), "--uninstall"],
                          env=env, capture_output=True, cwd=work)
    check("the installed install.sh uninstalls itself",
          done.returncode == 0 and not os.path.exists(dest) and not os.path.lexists(link)
          and "amask >>>" not in read(rc), done.stdout + done.stderr)

    # The clone already deleted by hand, the link and block left behind.
    install()
    amask("--wrap", "claude")
    shutil.rmtree(dest)
    done = install("--uninstall", stdin_script=True)  # no clone, no file: a throwaway one
    check("--uninstall without the clone still cleans up",
          done.returncode == 0 and not os.path.lexists(link) and "amask >>>" not in read(rc),
          done.stdout + done.stderr)
    check("and removes its throwaway copy",
          not any(n.startswith("tmp") for n in os.listdir(work)), os.listdir(work))
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    sys.exit(1)
print("all passed")
