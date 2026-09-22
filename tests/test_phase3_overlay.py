"""Overlay correctness: hide without losing, restore without contaminating.

Automated gates for P-103 (no scrollback contamination), SC-004 (complete log
restored), SC-002 (wake under 0.2s) and D-008 (the wake gesture is not agent
input). Timing is compressed by harness.FAST_TIMING: the screen is covered
1s after the agent starts working rather than the default 4s.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import budget, fixture, is_rain, run_in_pty
from amask.term import TerminalController

# Named, not inlined: a 3.11 f-string cannot carry a backslash inside the
# expression part, and these appear in failure details.
ED2 = b"\x1b[2J"
ALT_ENTER = b"\x1b[?1049h"
ALT_EXIT = b"\x1b[?1049l"
SENTINEL = b"AGENT-OUTPUT-SENTINEL"

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def split_at_overlay(data):
    """(during_overlay, after_overlay) around the alternate-buffer window."""
    start = data.find(ALT_ENTER)
    end = data.rfind(ALT_EXIT)
    if start < 0 or end < 0:
        return None, None
    return data[start:end], data[end + len(ALT_EXIT):]


def test_overlay_opens_once_the_agent_is_working():
    out, _ = run_in_pty(fixture("chatty.py", 3, 2.0, SENTINEL.decode()), timeout=20)
    check("P-202 대체 화면 진입/이탈 시퀀스 사용", ALT_ENTER in out and ALT_EXIT in out, repr(out[:80]))


def test_agent_output_is_withheld_then_restored():
    out, _ = run_in_pty(fixture("chatty.py", 3, 2.0, SENTINEL.decode()), timeout=20)
    during, after = split_at_overlay(out)
    check("P-103 오버레이 중 에이전트 출력이 화면에 나가지 않음", during is not None and SENTINEL not in during)
    check("SC-004/P-203 복귀 후 로그 플러시", after is not None and SENTINEL in after, repr((after or b"")[-120:]))


def test_no_matrix_glyphs_leak_into_normal_buffer():
    out, _ = run_in_pty(fixture("chatty.py", 3), timeout=20)
    _, after = split_at_overlay(out)
    check("P-103 일반 버퍼에 매트릭스 잔상 없음", not is_rain(after or b""), "레인 출력 유출")


def test_full_log_survives_a_large_burst():
    """Every line reaches the screen exactly once, none of it during the overlay.

    The split is deliberately not "everything after ALT_EXIT": under load the
    overlay can open partway through the burst, and lines printed before it
    opened legitimately sit ahead of ALT_ENTER. What must hold is that no line
    is lost and none is painted while the screen is covered.
    """
    lines = 2000
    # Tagged lines, because the rain paints bare digits: a plain `seq` token can
    # be forged or split by animation bytes, an L-prefixed one cannot.
    out, _ = run_in_pty(
        ["sh", "-c", f"sleep 1.3; seq -f 'L%04.0f' 1 {lines}; sleep 2"], timeout=30
    )
    during, _ = split_at_overlay(out)
    got = set(re.findall(rb"L\d{4}", out))
    missing = [n for n in range(1, lines + 1) if b"L%04d" % n not in got]
    check(f"SC-004 {lines}줄 전량 보존", not missing, f"{len(missing)}줄 누락: {missing[:5]}")
    leaked = re.findall(rb"L\d{4}", during or b"")
    check("P-103 오버레이 중에는 한 줄도 화면에 나가지 않음", not leaked, f"{len(leaked)}줄 유출")


def test_auto_wake_on_agent_exit():
    out, code = run_in_pty(
        ["sh", "-c", f"sleep 1.4; printf '{SENTINEL.decode()}\\n'; exit 7"], timeout=20
    )
    _, after = split_at_overlay(out)
    check("SC-003 에이전트 종료 시 자동 복귀", after is not None and SENTINEL in after)
    check("P-101 오버레이 경유해도 종료코드 보존 (7)", code == 7, str(code))


def test_manual_wake_is_fast():
    res = run_in_pty(fixture("chatty.py", 5), feed=[(2.0, b" ")], timeout=20)
    fed = res.feeds[0][0] if res.feeds else None
    woke = next((t for t, chunk in res.timeline if ALT_EXIT in chunk and fed and t > fed), None)
    ok = fed is not None and woke is not None and (woke - fed) < budget(0.2)
    check("SC-002 키 입력 후 0.2초 이내 복귀", ok, f"fed={fed} woke={woke}")


# The answer is typed at 5.0s, not at 3.0s, and that number is load-bearing.
# The agent works until 2.5s and the wake lands at 2.0s, so the runner is
# still inside a stretch of work when its own 1.0s cover delay elapses at
# 3.0s -- it covers again, right on top of the feed, and eats the keystroke
# as another wake gesture (D-008). The agent then waits for a line that will
# never come and the case hangs until the harness kills it. Whether the race
# is won or lost varies run to run; it was seen both ways. By 5.0s the work
# has stopped, the idle threshold has handed the screen back, and the answer
# is ordinary agent input again.
def test_wake_key_is_not_forwarded_to_the_agent():
    res = run_in_pty(
        fixture("busy_then_read.py", 2.5),
        feed=[(2.0, b"x"), (5.0, b"hello\r")],
        timeout=30,
    )
    check("D-008 깨우기 키가 에이전트 입력으로 새지 않음", b"GOT:hello" in res.data, repr(res.data[-160:]))


def test_mouse_click_wakes_without_injecting_garbage():
    res = run_in_pty(
        fixture("busy_then_read.py", 2.5),
        feed=[(2.0, b"\x1b[<0;10;5M"), (2.1, b"\x1b[<0;10;5m"), (5.0, b"hello\r")],
        timeout=30,
    )
    check("SC-002/P-204 마우스 클릭 깨우기 + 잔여 이벤트 삼킴", b"GOT:hello" in res.data, repr(res.data[-160:]))


def test_the_overlay_never_sends_ed2():
    """P-103 by construction: ED 2 inside the alternate screen spills.

    iTerm2 copies the grid into scrollback before ED 2 clears it, so a single
    `ESC[2J` between 1049h and 1049l puts a frame of rain into the user's
    history -- measured on the branch where the agent owns the alternate
    buffer. The terminal's own behaviour cannot be reproduced inside a pty,
    but the byte that triggers it can be, so this
    guards the invariant the fix rests on rather than the leak itself.
    """
    class Capture(TerminalController):
        """Every byte the controller would put on the tty, and none of the
        agent's -- a pty capture mixes the two, and a full-screen agent
        repaints with ED 2 of its own."""

        def __init__(self):
            super().__init__()
            self.is_tty = True
            self.out = bytearray()

        def write(self, data):
            self.out += data.encode() if isinstance(data, str) else data

        def get_size(self):
            return (24, 80)

    for name, take_alt in (("러너가 대체 화면을 열 때", True),
                           ("에이전트가 이미 쥐고 있을 때", False)):
        term = Capture()
        term.enter_overlay(take_alt=take_alt)
        covered = len(term.out)
        term.exit_overlay()
        ed2 = term.out.count(ED2)
        check(f"P-103 오버레이 구간에 ED 2 없음 ({name})",
              ED2 not in term.out, f"ED 2 {ed2}회")
        check(f"화면은 여전히 지워진다 ({name})",
              b"\x1b[2K" in term.out[:covered], "지우는 시퀀스가 없다")

    # The one place ED 2 is still fine is a line-oriented agent's own output,
    # which the runner only passes through.
    res = run_in_pty(fixture("chatty.py", 6), timeout=14, observe=True)
    enter = res.data.find(ALT_ENTER)
    leave = res.data.find(ALT_EXIT, enter + 1) if enter >= 0 else -1
    window = res.data[enter:leave] if 0 <= enter < leave else b""
    check("실제 실행에서도 오버레이 구간에 ED 2 없음",
          enter >= 0 and b"\x1b[2J" not in window,
          f"ED 2 {window.count(ED2)}회" if enter >= 0 else "1049h 없음")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("오버레이 스위트 전부 통과")
