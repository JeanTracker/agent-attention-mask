"""Deferred entry, re-entry, and coexistence with a full-screen agent.

This is the revised G-001 lifecycle (D-010/D-011, closing HRQ-001/HRQ-002):
the screen is not covered at launch, it is covered once the agent has been
working unattended, and the cycle repeats for the life of the session.

Timing is compressed by harness.FAST_TIMING -- 1s of sustained work covers the
screen instead of 4s -- so the constants under test are the ratios, not the
shipped defaults.
"""

import re
import sys

from harness import fixture, is_rain, run_in_pty

ALT_ENTER = b"\x1b[?1049h"
ALT_EXIT = b"\x1b[?1049l"

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def rain_windows(res):
    """[(start, end)] elapsed times where the rain was on screen.

    A chunk can carry both the last rain cells and the sequence that uncovers
    the screen; closing only on chunks *without* rain merged two overlays into
    one and hid re-entry entirely.
    """
    spans, open_at = [], None
    for when, chunk in res.timeline:
        if open_at is not None and ALT_EXIT in chunk:
            spans.append((open_at, when))
            open_at = None
        if open_at is None and is_rain(chunk):
            open_at = when
    if open_at is not None:
        spans.append((open_at, res.timeline[-1][0]))
    return spans


# -- deferred entry (HRQ-001) ----------------------------------------------


def test_short_command_is_never_covered():
    """A job that finishes inside the delay must not flash the screensaver."""
    res = run_in_pty(["sh", "-c", "printf 'QUICK\\n'"], timeout=20)
    check("짧은 명령은 오버레이 없이 끝남", ALT_ENTER not in res.data, repr(res.data[:80]))
    check("짧은 명령 출력은 그대로 보임", b"QUICK" in res.data, repr(res.data[:80]))


def test_interactive_prompt_is_not_covered_before_the_user_acts():
    """The HRQ-001 regression: an idle REPL must keep its prompt on screen."""
    res = run_in_pty(fixture("tui.py"), timeout=5, observe=True)
    check("기동 직후 유휴 프롬프트는 덮이지 않음", not is_rain(res.data), "덮임")


def test_idle_blips_never_cover_the_screen():
    """An idle agent repaints now and then; that is not work.

    Real regression: interactive `claude` blips every 8-10 seconds at its
    prompt, and treating each blip as work made the screensaver open and
    auto-close on a loop without the user touching anything.
    """
    res = run_in_pty(
        fixture("idler.py"),
        env={"IDLER_BLIP_EVERY": "1.2"},
        timeout=10,
        observe=True,
    )
    blips = res.data.count(b"BLIP")
    check("유휴 블립이 여러 번 발생했다 (조건 성립)", blips >= 4, f"블립 {blips}회")
    check("유휴 블립으로는 덮이지 않음", not is_rain(res.data), "덮임")


def test_real_work_is_still_recognised_after_idling():
    """The same agent, once it actually works, must be covered."""
    res = run_in_pty(
        fixture("idler.py"),
        env={"IDLER_BLIP_EVERY": "1.2", "IDLER_WORK_FIRST": "3"},
        timeout=8,
        observe=True,
    )
    covered = next((t for t, chunk in res.timeline if is_rain(chunk)), None)
    check("연속 작업은 정상적으로 덮임", covered is not None, "덮이지 않음")
    check("작업 시작 후 지연을 두고 덮임", covered is not None and covered > 0.7, f"t={covered}")


def test_agent_working_alone_gets_covered():
    res = run_in_pty(fixture("chatty.py", 4), timeout=20)
    spans = rain_windows(res)
    check("작업 시작 후 오버레이 진입", bool(spans), "진입 없음")
    # FAST_TIMING covers at 1.0s; allow scheduling slack but reject "immediately".
    check(
        "진입이 즉시가 아니라 지연됨 (0.7s 이후)",
        spans and spans[0][0] > 0.7,
        f"첫 진입 t={spans[0][0] if spans else None}",
    )


def test_typing_holds_the_screensaver_off():
    """Input resets the timer: the user is present, so do not cover the screen."""
    res = run_in_pty(
        fixture("chatty.py", 5),
        feed=[(0.5, b"a"), (1.2, b"b"), (1.9, b"c")],
        timeout=20,
    )
    spans = rain_windows(res)
    # Keystrokes at 0.5/1.2/1.9s each push entry out by 1.0s, so nothing before ~2.9s.
    check(
        "입력 중에는 오버레이 진입 보류",
        not spans or spans[0][0] > 2.5,
        f"첫 진입 t={spans[0][0] if spans else None}",
    )


# -- re-entry (HRQ-002) ----------------------------------------------------


def test_screensaver_returns_after_an_automatic_wake():
    """Work, go quiet, work again: the screen should hide twice."""
    res = run_in_pty(
        ["sh", "-c", "sleep 1.2; " + _busy(2.0) + " sleep 1.5; " + _busy(2.0)],
        timeout=30,
    )
    check(
        "자동 복귀 후 재진입",
        res.data.count(ALT_ENTER) >= 2,
        f"진입 {res.data.count(ALT_ENTER)}회",
    )


def test_manual_wake_returns_while_the_agent_is_still_working():
    """A click buys reading time, not a permanent dismissal (D-022).

    Reported: one click closed the screensaver and it never came back, even
    though the agent kept working for the rest of the session.
    """
    res = run_in_pty(fixture("chatty.py", 10), feed=[(2.0, b" ")], timeout=25)
    spans = rain_windows(res)
    woke = res.feeds[0][0]
    after = [s for s in spans if s[0] > woke + 0.3]
    check("수동 복귀 후에도 다시 덮임", bool(after), "재진입 없음")
    if after:
        gap = after[0][0] - woke
        # One constant governs both directions, so this is the plain delay.
        check("재진입은 최초 타임아웃을 그대로 씀", 0.7 < gap < 2.0, f"{gap:.2f}s")


def test_one_delay_governs_both_the_first_cover_and_the_return():
    """No separate grace constant to reason about (D-022)."""
    plain = run_in_pty(fixture("chatty.py", 5), timeout=20)
    manual = run_in_pty(fixture("chatty.py", 10), feed=[(2.0, b" ")], timeout=25)
    first = rain_windows(plain)
    again = [s for s in rain_windows(manual) if s[0] > manual.feeds[0][0] + 0.3]
    if first and again:
        gap = again[0][0] - manual.feeds[0][0]
        check("복귀 후 지연 ≈ 최초 지연", abs(gap - first[0][0]) < 0.6, f"{gap:.2f}s vs {first[0][0]:.2f}s")
    else:
        check("복귀 후 지연 ≈ 최초 지연", False, "측정 구간 부족")


def test_typing_after_a_manual_wake_keeps_it_off():
    """Still typing means still present, whatever the grace says."""
    res = run_in_pty(
        fixture("chatty.py", 10),
        feed=[(2.0, b" "), (2.6, b"a"), (3.2, b"b"), (3.8, b"c")],
        timeout=25,
    )
    again = [s for s in rain_windows(res) if s[0] > 2.3]
    check("타이핑 중에는 재진입 보류", not again or again[0][0] > 4.4, f"t={again[0][0] if again else None}")


# -- coexistence with a full-screen agent (D-010) --------------------------


def test_tui_agent_is_covered_without_nesting_alt_screens():
    """The agent already holds 1049h; the runner must not open a second one."""
    res = run_in_pty(fixture("tui.py"), feed=[(1.0, b"go\r")], timeout=12, observe=True)
    agent_alt = res.data.count(ALT_ENTER)
    check("TUI 에이전트 위에 오버레이 진입", bool(is_rain(res.data)), "매트릭스 미표시")
    check("대체 화면 중첩 없음 (1049h 1회)", agent_alt == 1, f"1049h {agent_alt}회")
    check("대체 화면을 임의로 빠져나오지 않음", ALT_EXIT not in res.data, "1049l 발견")


def test_tui_agent_is_repainted_after_the_overlay_lifts():
    """Painting over a TUI destroys it, so waking has to make it come back.

    The wake happens mid-work, so nothing but the runner's nudge can produce a
    new frame -- the fixture only repaints when the terminal size actually
    changes, the way Node's tty stream does. Without a real size delta this
    left the screen blank.
    """
    res = run_in_pty(
        fixture("tui.py"),
        feed=[(1.0, b"go\r"), (5.0, b" ")],
        env={"TUI_BUSY": "20"},
        timeout=8,
        observe=True,
    )
    after = [(t, chunk) for t, chunk in res.timeline if t > 5.0]
    repaint = next((t for t, chunk in after if b"TUI-FRAME" in chunk), None)
    check("작업 도중 깨워도 TUI가 다시 그려짐", repaint is not None, "재렌더링 없음")
    check(
        "SC-002 재렌더링까지 0.2초 이내",
        repaint is not None and (repaint - 5.0) < 0.2,
        f"{repaint - 5.0:.3f}s" if repaint else "없음",
    )


def test_inline_tui_wake_does_not_replay_the_hidden_frames():
    """An agent that redraws in place has no scrollback to restore (D-018).

    Reported against interactive claude, which does NOT take the alternate
    screen -- it repaints inline with cursor moves. Replaying that stream into
    the normal buffer smeared its UI across the screen.
    """
    res = run_in_pty(
        fixture("inline_tui.py"),
        feed=[(1.0, b"go\r"), (6.0, b" ")],
        env={"TUI_BUSY": "20", "TUI_PAD": "2000"},
        timeout=9,
        observe=True,
    )
    hidden = sum(len(c) for t, c in res.timeline if 2.0 < t < 6.0)
    replayed = sum(len(c) for t, c in res.timeline if 6.0 < t < 6.5)
    check(
        "인라인 TUI 경로에서 숨긴 출력을 재생하지 않음",
        replayed < hidden // 4,
        f"숨김 {hidden // 1024}KB 중 {replayed // 1024}KB 재생",
    )
    check("대체 화면은 러너가 소유", res.data.count(ALT_ENTER) >= 1, "1049h 없음")


def test_line_oriented_agent_still_gets_its_log_back():
    """The discard must not swallow a plain log -- that is SC-004."""
    res = run_in_pty(
        ["sh", "-c", "sleep 1.3; seq -f 'L%04.0f' 1 200; sleep 2"], timeout=20
    )
    check("줄 단위 에이전트는 로그 복원", b"L0200" in res.data, "로그 없음")


def test_tui_wake_does_not_replay_the_hidden_frames():
    """An agent-owned alt screen has no scrollback to restore (D-012).

    Replaying it would render the whole hidden session before the agent paints
    over it, and the cost would grow with how long the screen stayed covered.
    """
    res = run_in_pty(
        fixture("tui.py"),
        feed=[(1.0, b"go\r"), (6.0, b" ")],
        env={"TUI_BUSY": "20", "TUI_PAD": "2000"},
        timeout=9,
        observe=True,
    )
    hidden = sum(len(c) for t, c in res.timeline if 2.0 < t < 6.0)
    replayed = sum(len(c) for t, c in res.timeline if 6.0 < t < 6.5)
    check(
        "TUI 경로에서 숨겨진 출력을 재생하지 않음",
        replayed < hidden // 4,
        f"숨김 {hidden // 1024}KB 중 {replayed // 1024}KB 재생",
    )


def _busy(seconds):
    return f"i=0; while [ $i -lt {int(seconds * 10)} ]; do printf '.'; sleep 0.1; i=$((i+1)); done;"


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("라이프사이클 스위트 전부 통과")
