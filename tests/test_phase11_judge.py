"""The cover/uncover state machine, with time handed to it (D-054).

Every case here is the same judgement the runner makes in `_tick`, driven by
numbers instead of by a clock: no pty, no agent, no waiting. The phase suites
that do start a real runner are still the authority on what reaches the
terminal (P-103, SC-002) -- what moved here is the part where the suite used
to spend twelve minutes asleep.

The scenarios mirror the pty cases they came from, and each one names the
decision it pins.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amask import hooks
from amask.cli import IDLE_HINT_AFTER, Settings
from amask.judge import Judge

FAILURES = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


def covers(judge, now):
    """One `_tick`, as the runner runs it: read the agent, then decide.

    The order is the product's own (`cli.Runner._tick`) and it matters --
    `idle` is what clears the work clock, so a case that asks `should_cover`
    without ticking is asking a question the runner never asks.
    """
    judge.idle(now)
    return judge.should_cover(now)


def make(hooks_live=False, **overrides):
    """A judge with the shipped settings, or whatever the case needs."""
    values = {"idle_silence": 1.5, "hook_stall": 120.0, "overlay_delay": 4.0}
    values.update(overrides)
    return Judge(Settings(**values), lambda: hooks_live)


# -- with no hooks: the timing heuristic (D-025) -----------------------------


def test_sustained_work_covers_after_the_delay():
    judge = make()
    judge.started(0.0)
    for t in (0.1, 1.0, 2.0, 3.0):
        judge.output(t)
    check("아직 4초가 안 됐으면 덮지 않는다", not covers(judge, 3.5))
    judge.output(4.1)
    check("일이 계속되면 4초에 덮는다", covers(judge, 4.2),
          f"busy_since={judge.busy_since}")


def test_a_silent_agent_is_working_not_waiting():
    """An agent that has not printed anything yet is starting up (D-011)."""
    judge = make()
    judge.started(0.0)
    check("한 글자도 안 냈어도 일하는 중", not judge.idle(2.0))
    check("그래서 4초 뒤에는 덮인다", covers(judge, 4.1))


def test_silence_ends_the_run_of_work():
    judge = make()
    judge.started(0.0)
    judge.output(1.0)
    check("0.6초 침묵은 아직 일하는 중", not judge.idle(1.6))
    check("1.5초 침묵이면 유휴", judge.idle(2.6))
    check("유휴가 되면 작업 시계가 풀린다", judge.busy_since is None)
    check("유휴 상태에서는 덮지 않는다", not covers(judge, 9.0))


def test_a_streaming_pause_does_not_end_the_run():
    """H-001: claude's streaming gaps reach 0.8s; 1.5s clears them (D-023)."""
    judge = make()
    judge.started(0.0)
    now = 0.0
    for _ in range(10):
        now += 0.8
        judge.output(now)
        check("스트리밍 공백에서는 유휴로 넘어가지 않는다", not judge.idle(now + 0.79),
              f"t={now}")
    check("멈추면 넘어간다", judge.idle(now + 1.6))


def test_typing_holds_the_screensaver_off():
    judge = make()
    judge.started(0.0)
    judge.output(3.9)
    judge.held(3.9)  # a keystroke: the user is at the keyboard
    check("방금 입력했으면 덮지 않는다", not judge.should_cover(4.2))
    check("한 번의 입력이 영구 면제는 아니다", judge.should_cover(8.0),
          f"hold_until={judge.hold_until}")


def test_one_delay_governs_the_first_cover_and_the_return():
    """D-022: waking by hand buys exactly the same delay as the first cover."""
    judge = make(overlay_delay=2.0)
    judge.started(0.0)
    check("2초면 덮는다", judge.should_cover(2.1))
    judge.held(2.1)  # woke by hand
    check("깨운 직후에는 안 덮는다", not judge.should_cover(3.0))
    check("같은 2초가 지나면 다시 덮는다", judge.should_cover(4.2))


# -- the skip key (D-036, D-038) ---------------------------------------------


def test_skip_holds_until_the_next_prompt():
    judge = make()
    judge.started(0.0)
    judge.skip()
    check("건너뛴 턴은 덮지 않는다", not judge.should_cover(9.0))
    judge.turn_opened()
    check("다음 프롬프트가 억제를 푼다", judge.should_cover(9.0),
          f"busy_since={judge.busy_since}")


def test_skip_is_refused_when_there_is_no_turn():
    judge = make()
    judge.started(0.0)
    check("턴이 돌고 있으면 받는다", judge.accepts_skip())
    judge.hook(hooks.WAITING, 1.0)
    check("대기 중에는 건너뛸 턴이 없다", not judge.accepts_skip())


# -- with hooks: what the agent says beats what the silence suggests ---------


def test_a_silent_tool_call_stays_covered():
    """The case the hook channel exists for (D-025)."""
    judge = make(hooks_live=True)
    judge.started(0.0)
    judge.hook(hooks.BUSY, 0.5)
    judge.output(1.0)
    check("10초를 조용해도 훅이 일한다고 하면 일하는 중",
          not judge.idle(11.0))
    check("그래서 덮인 채로 있는다", covers(judge, 11.0))


def test_stop_uncovers_at_once():
    judge = make(hooks_live=True)
    judge.started(0.0)
    judge.hook(hooks.BUSY, 0.5)
    check("Stop은 깨우라고 답한다", judge.hook(hooks.WAITING, 5.0))
    check("Stop 뒤에는 유휴", judge.idle(5.0))
    check("Stop 뒤 구간은 덮지 않는다 (D-035)", not covers(judge, 20.0))


def test_waiting_never_goes_stale():
    """D-030: 'waiting' does not expire -- nothing has happened."""
    judge = make(hooks_live=True, hook_stall=5.0)
    judge.started(0.0)
    judge.hook(hooks.WAITING, 1.0)
    judge.output(2.0)  # an idle prompt repainting itself
    check("한참 뒤에도 훅을 믿는다", judge.hooks_speaking(600.0))
    check("자기 리페인트가 다시 덮게 만들지 않는다", not covers(judge, 600.0),
          f"busy_since={judge.busy_since}")


def test_a_lost_completion_falls_back_to_timing():
    """D-025: a latched BUSY goes stale after hook_stall."""
    judge = make(hooks_live=True, hook_stall=5.0)
    judge.started(0.0)
    judge.hook(hooks.BUSY, 1.0)
    judge.output(1.0)
    check("임계 안에서는 훅을 믿는다", judge.hooks_speaking(5.0))
    check("임계를 넘으면 훅을 믿지 않는다", not judge.hooks_speaking(6.1))
    check("그때는 침묵이 다시 증거가 된다", judge.idle(6.1))


def test_hooks_that_never_arrive_change_nothing():
    judge = make(hooks_live=False)
    judge.started(0.0)
    judge.hook(hooks.BUSY, 1.0)
    judge.output(1.0)
    check("채널이 없으면 훅 상태는 판정에 쓰이지 않는다",
          not judge.hooks_speaking(1.1))
    check("침묵이 그대로 증거", judge.idle(2.6))


def test_the_idle_hint_is_a_hint_and_not_a_decision():
    judge = make(hooks_live=True)
    judge.started(0.0)
    judge.hook(hooks.BUSY, 0.5)
    judge.output(1.0)
    check("출력이 막 멈춘 것은 의심하지 않는다",
          not judge.idle_suspected(5.0, IDLE_HINT_AFTER))
    check("오래 멈추면 의심한다",
          judge.idle_suspected(1.0 + IDLE_HINT_AFTER + 0.1, IDLE_HINT_AFTER))
    check("의심해도 덮기는 유지된다 (강조만 바뀐다)",
          judge.should_cover(1.0 + IDLE_HINT_AFTER + 0.1))
    judge.hook(hooks.WAITING, 30.0)
    check("대기 중이면 의심할 것도 없다",
          not judge.idle_suspected(60.0, IDLE_HINT_AFTER))


def test_a_retune_is_picked_up_live():
    """The socket's `set` changes the same object the judge reads (D-039).

    With one edge worth knowing: a hold already running keeps the deadline it
    was given. The new value governs the work threshold immediately and the
    *next* hold, not one that is already counting down.
    """
    judge = make()
    judge.started(0.0)  # hold_until = 4.0, from the old delay
    check("기본 4초로는 아직", not judge.should_cover(2.5))
    judge.settings.update({"overlay_delay": 2.0})
    check("이미 걸린 유예는 그 기한을 지킨다", not judge.should_cover(2.5),
          f"hold_until={judge.hold_until}")
    check("유예가 끝나면 새 임계로 판정한다", judge.should_cover(4.1))
    judge.held(10.0)
    check("다음 유예는 새 값으로 잡힌다", judge.hold_until == 12.0,
          str(judge.hold_until))


def test_busy_seconds_is_what_the_panel_and_the_socket_report():
    judge = make()
    judge.started(10.0)
    check("작업 시간은 시작 시각부터", abs(judge.busy_seconds(14.5) - 4.5) < 1e-9,
          str(judge.busy_seconds(14.5)))
    judge.output(14.0)
    judge.idle(100.0)  # long since silent
    check("유휴가 되면 0", judge.busy_seconds(100.0) == 0.0,
          str(judge.busy_seconds(100.0)))


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("판정 스위트 전부 통과")
