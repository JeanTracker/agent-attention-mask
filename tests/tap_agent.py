"""Record an agent's raw pty output with timings.

Input for H-001: the idle-prompt heuristic assumes an agent goes quiet when it
is waiting for the user. Ink-based TUIs often repaint on a timer instead, in
which case byte silence never happens and SC-003 needs a different signal.
This measures which world we are in before Phase 4 commits to a detector.
"""

import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

QUERIES = {
    "DSR cursor-position (ESC[6n)": rb"\x1b\[6n",
    "DA device-attributes (ESC[c)": rb"\x1b\[[0-9>?]*c",
    "OSC 10/11 colour query": rb"\x1b\]1[01];\?",
}


def tap(argv, schedule, total, rows=40, cols=120):
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp(argv[0], argv)
        os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    events = []
    pending = list(schedule)
    started = time.monotonic()
    while True:
        now = time.monotonic() - started
        if now > total:
            break
        while pending and now >= pending[0][0]:
            _, payload, label = pending.pop(0)
            os.write(master, payload)
            events.append((now, b"", label))
        r, _, _ = select.select([master], [], [], 0.02)
        if not r:
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        events.append((time.monotonic() - started, data, None))

    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    os.close(master)
    return events


def report(events, windows):
    raw = b"".join(e[1] for e in events)
    print("총 출력 %d bytes, 청크 %d개\n" % (len(raw), sum(1 for e in events if e[1])))

    print("== 터미널 질의 시퀀스 ==")
    for name, pattern in QUERIES.items():
        print("  %s: %d회" % (name, len(re.findall(pattern, raw))))

    print("\n== 마커 ==")
    for t, _, label in events:
        if label:
            print("  t=%6.2fs  -> %s" % (t, label))

    for name, lo, hi in windows:
        chunks = [(t, d) for t, d, label in events if d and lo <= t <= hi]
        print("\n== %s (t=%s~%ss) ==" % (name, lo, hi))
        if not chunks:
            print("  출력 없음 (완전 무음)")
            continue
        gaps = []
        prev = lo
        for t, _ in chunks:
            gaps.append(t - prev)
            prev = t
        gaps.append(hi - prev)
        ends_nl = chunks[-1][1].endswith(b"\n")
        print("  청크 %d개, 최대 무음 %.3fs, 중앙 간격 %.3fs"
              % (len(chunks), max(gaps), sorted(gaps)[len(gaps) // 2]))
        print("  마지막 청크 끝 40바이트: %r" % (chunks[-1][1][-40:],))
        print("  개행으로 끝나는가: %s" % ends_nl)


if __name__ == "__main__":
    cmd = sys.argv[1:] or ["claude"]
    schedule = [(12.0, b"Use the Bash tool to run the command: ls -1\r", "프롬프트 전송")]
    windows = [
        ("A. 시작 후 유휴 프롬프트", 6.0, 11.5),
        ("B. 작업 중", 13.0, 20.0),
        ("C. 권한 확인 대기 추정", 25.0, 40.0),
    ]
    report(tap(cmd, schedule, total=42.0), windows)
