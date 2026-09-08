"""SC-003's automatic wake-up, driven by the H-001 silence heuristic.

Thresholds come from measured behaviour (see tests/tap_agent.py): `claude`
repaints every ~105ms while working, worst gap 232ms, and is fully silent at a
prompt. The detector must fire on the silence and stay quiet during the churn.

Silence is read in both directions since the lifecycle revision: it is what
ends an overlay, and its absence is what lets one begin. These cases cover the
ending; tests/test_phase6_lifecycle.py covers the beginning.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import fixture, is_rain, run_in_pty

ALT_EXIT = b"\x1b[?1049l"
SENTINEL = b"IDLE-SENTINEL"

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def first_rain(res):
    return next((t for t, chunk in res.timeline if is_rain(chunk)), None)


def test_silence_triggers_an_automatic_wake():
    """Work long enough to be covered, then wait at a 'prompt' without exiting."""
    res = run_in_pty(fixture("chatty.py", 2.0, 1.8, SENTINEL.decode()) + [], timeout=20)
    covered, woke = first_rain(res), res.first_time(ALT_EXIT)
    check("SC-003 무음 감지로 자동 복귀", woke is not None, "복귀 안 함")
    check("오버레이가 실제로 켜졌었다", covered is not None, "진입 없음")
    # chatty stops at 2.0s and the process lingers ~0.1s; a wake before 2.9s
    # means silence fired, not the exit.
    check("SC-003 종료가 아니라 유휴로 깨어남", woke is not None and woke < 2.9, f"woke={woke}")


def test_busy_agent_is_not_woken_early():
    """A spinner-like agent repainting every 100ms must stay covered."""
    res = run_in_pty(fixture("chatty.py", 4.0), timeout=25)
    covered, woke = first_rain(res), res.first_time(ALT_EXIT)
    check("작업 중 오버레이 유지", covered is not None and woke is not None and woke > covered)
    # Covered at ~1s, busy until 4s: waking before ~3.5s would be a false positive.
    check("작업 중 오탐 없음 (4초 연속 출력)", woke is not None and woke > 3.5, f"woke={woke}")


def test_silent_agent_counts_as_working():
    """A quiet long-running job is the canonical screensaver case.

    Silence only means "waiting for the user" once the agent has spoken at
    least once. A build that prints nothing for a minute is working, so it gets
    covered -- and stays covered, since there is no first output to arm the
    idle detector against.
    """
    res = run_in_pty(["sh", "-c", "sleep 3"], timeout=20)
    covered = first_rain(res)
    check("출력 없는 장시간 작업도 덮음", covered is not None, "오버레이 진입 안 함")
    check("진입은 지연 후 (0.7s 이후)", covered is not None and covered > 0.7, f"t={covered}")
    woke = res.first_time(ALT_EXIT)
    check("종료 전까지 계속 덮여 있음", woke is not None and woke > 2.5, f"woke={woke}")


def test_log_is_still_complete_after_an_idle_wake():
    res = run_in_pty(fixture("chatty.py", 2.0, 1.8, SENTINEL.decode()), timeout=20)
    tail = res.data[res.data.rfind(ALT_EXIT):]
    check("SC-004 유휴 복귀 시에도 로그 보존", SENTINEL in tail, repr(tail[:120]))


def test_idle_threshold_is_tunable():
    """The constant is measured, not magic -- it has to be overridable.

    The agent has to stay alive past going quiet, otherwise its exit wakes the
    screen first and both runs measure the same thing.
    """
    args = fixture("busy_then_read.py", 2.0)
    slow = run_in_pty(args, env={"AMASK_IDLE_SILENCE": "2.5"}, timeout=12, observe=True)
    fast = run_in_pty(args, timeout=12, observe=True)
    slow_woke, fast_woke = slow.first_time(ALT_EXIT), fast.first_time(ALT_EXIT)
    ok = slow_woke is not None and fast_woke is not None and slow_woke > fast_woke + 1.0
    check("유휴 임계값 조정 가능", ok, f"기본={fast_woke} 느림={slow_woke}")


def test_a_streaming_pause_does_not_uncover_the_screen():
    """Measured: an 80s claude answer paused over 600ms eighteen times.

    At the old 600ms threshold each of those lifted the screensaver and it
    re-covered seconds later, so the matrix flickered through the answer.
    """
    res = run_in_pty(
        fixture("streamer.py"),
        env={
            "AMASK_IDLE_SILENCE": "1.5",
            "STREAM_SECONDS": "8",
            "STREAM_GAPS": "3,5",
            "STREAM_GAP_LEN": "0.8",
        },
        timeout=12,
        observe=True,
    )
    covered = first_rain(res)
    check("스트리밍 중 오버레이 진입", covered is not None, "진입 없음")
    woke = res.first_time(ALT_EXIT)
    check("0.8초 공백으로는 깨지 않음", woke is None or woke > 8.0, f"woke={woke}")


def test_the_default_threshold_clears_a_real_streaming_pause():
    """The constant is measured, and the margin is the point."""
    import amask.cli as cli

    check("기본 유휴 임계값이 관측 최대 공백(0.81s)보다 큼", cli.IDLE_SILENCE >= 1.2, f"{cli.IDLE_SILENCE}s")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("유휴 감지 스위트 전부 통과")
