"""Phase 5: the CONSTRAINTS budget -- under 50MB RAM while buffering.

This is the check that justifies the spill file (D-007). The agent dumps
megabytes while the overlay is up, so every byte is captured; if the buffer
lived in memory, RSS would track the log size.
"""

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

from harness import RUNNER

FAILURES = []
LIMIT_MB = 50


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def rss_mb(pid):
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        return int(out) / 1024 if out else None
    except (ValueError, OSError):
        return None


def measure(agent_argv, seconds, rows=40, cols=120):
    """Run the agent under the runner; return (peak_rss_mb, bytes_seen)."""
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execv(RUNNER, [RUNNER] + list(agent_argv))
        os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    peak = 0.0
    seen = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        current = rss_mb(pid)
        if current:
            peak = max(peak, current)
        readable, _, _ = select.select([master], [], [], 0.1)
        if readable:
            try:
                data = os.read(master, 1 << 20)
            except OSError:
                break
            if not data:
                break
            seen += len(data)

    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except OSError:
        pass
    os.close(master)
    return peak, seen


def test_memory_stays_bounded_while_buffering():
    # ~11MB of agent output, all of it captured while the overlay is up.
    lines = 1_500_000
    peak, _ = measure(["sh", "-c", f"seq 1 {lines}"], seconds=12)
    check(
        f"CONSTRAINTS 버퍼링 중 RSS < {LIMIT_MB}MB (약 11MB 출력)",
        peak and peak < LIMIT_MB,
        f"peak={peak:.1f}MB",
    )
    print(f"       측정된 최대 RSS: {peak:.1f}MB")


def test_idle_animation_is_cheap():
    peak, _ = measure(["sh", "-c", "sleep 8"], seconds=6)
    check(
        f"애니메이션 유지 중 RSS < {LIMIT_MB}MB",
        peak and peak < LIMIT_MB,
        f"peak={peak:.1f}MB",
    )
    print(f"       측정된 최대 RSS: {peak:.1f}MB")


def test_spill_file_is_not_reachable_on_disk():
    """FORBIDDEN: agent output may contain tokens; the log must not persist."""
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execv(RUNNER, [RUNNER, "sh", "-c", "printf SECRET; sleep 5"])
        os._exit(127)
    time.sleep(1.5)
    listing = subprocess.run(
        ["sh", "-c", f"lsof -p {pid} 2>/dev/null | grep -c 'amask.*\\.log'"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    named = subprocess.run(
        ["sh", "-c", "ls /var/folders/*/*/T/amask-*.log 2>/dev/null | wc -l"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except OSError:
        pass
    os.close(master)
    check("FORBIDDEN 스필 파일이 경로로 노출되지 않음 (즉시 unlink)", named == "0", f"발견 {named}건")
    print(f"       lsof상 열린 로그 fd: {listing}건 (unlink된 상태로 유지)")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("Phase 5 전부 통과")
