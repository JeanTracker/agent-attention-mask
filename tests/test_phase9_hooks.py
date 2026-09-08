"""Agent state from the agent itself, not from guessing at its output (D-025).

The runner used to infer "working" from how output was spaced, which needed
retuning twice and still cannot tell a silent ten-second tool call from a
finished turn. Claude Code reports both directly. These cases drive the same
FIFO its hooks write to.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import fixture, is_rain, run_in_pty
from amask.hooks import EVENT_MEANING, BUSY, WAITING, HookChannel

ALT_EXIT = b"\x1b[?1049l"
FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


EMITTER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "amask-hook"
)


# -- the channel ------------------------------------------------------------


def test_channel_carries_events():
    channel = HookChannel(EMITTER)
    check("FIFO 생성", channel.open(), "생성 실패")
    subprocess.run([EMITTER, channel.path, "PreToolUse"], input=b'{"tool_name":"Bash"}')
    subprocess.run([EMITTER, channel.path, "Stop"], input=b"{}")
    time.sleep(0.2)
    events = channel.read_events()
    channel.close()
    check("이벤트 수신", [e.get("event") for e in events] == ["PreToolUse", "Stop"], str(events))
    check("페이로드 필드 보존", events and events[0].get("tool_name") == "Bash", str(events[:1]))


def test_settings_fragment_is_additive_and_complete():
    channel = HookChannel(EMITTER, path="/tmp/does-not-need-to-exist")
    settings = channel.settings()
    check("hooks 키만 담음", list(settings) == ["hooks"], str(list(settings)))
    check("모든 이벤트 등록", set(settings["hooks"]) == set(EVENT_MEANING), str(set(settings["hooks"])))
    check("도구 이벤트에 matcher", settings["hooks"]["PreToolUse"][0].get("matcher") == "*", "없음")
    check("JSON 직렬화 가능", isinstance(json.loads(channel.settings_json()), dict))


def test_a_broken_emitter_call_is_silent():
    """A problem in the screensaver must never disturb the agent (P-101)."""
    done = subprocess.run(
        [EMITTER, "/tmp/no-such-fifo-here", "Stop"], input=b"not json",
        capture_output=True,
    )
    check("종료코드 0", done.returncode == 0, str(done.returncode))
    check("stderr 없음", done.stderr == b"", done.stderr[:80].decode("utf-8", "replace"))


# -- the runner -------------------------------------------------------------


def test_a_silent_tool_call_stays_covered():
    """The heuristic would uncover here; the hooks say the agent is working."""
    res = run_in_pty(
        fixture("hooked.py"),
        env={"HOOKED_WORK": "6", "AMASK_IDLE_SILENCE": "1.0"},
        timeout=9,
        observe=True,
    )
    covered = next((t for t, chunk in res.timeline if is_rain(chunk)), None)
    check("작업 중 오버레이 진입", covered is not None, "진입 없음")
    # The agent is silent from ~0.5s to ~6.5s. Waking during that means the
    # runner fell back to timing.
    woke = next((t for t, chunk in res.timeline if ALT_EXIT in chunk), None)
    check("무음인 툴 호출 동안 유지", woke is None or woke > 6.0, f"woke={woke}")


def test_stop_uncovers_immediately():
    res = run_in_pty(
        fixture("hooked.py"),
        env={"HOOKED_WORK": "6", "AMASK_IDLE_SILENCE": "1.0"},
        timeout=12,
        observe=True,
    )
    woke = next((t for t, chunk in res.timeline if ALT_EXIT in chunk), None)
    check("Stop 직후 복귀", woke is not None and 6.0 < woke < 7.5, f"woke={woke}")


def test_chatter_after_stop_does_not_re_cover():
    """An idle prompt repaints; the agent said it stopped, so believe it."""
    res = run_in_pty(
        fixture("hooked.py"),
        env={"HOOKED_WORK": "3", "AMASK_IDLE_SILENCE": "1.0"},
        timeout=14,
        observe=True,
    )
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.5]
    check("Stop 이후 재진입 없음", not after, f"재진입 t={after[0] if after else None}")


def test_the_prompt_comes_from_the_hook():
    """No reconstructing it from keystrokes -- the agent reports it exactly."""
    res = run_in_pty(
        fixture("hooked.py"),
        env={"HOOKED_WORK": "6", "HOOKED_PROMPT": "지침서 충돌 검토해줘"},
        timeout=9,
        observe=True,
    )
    check("훅이 보낸 프롬프트가 패널에", "지침서 충돌 검토해줘".encode() in res.data, "표시 없음")


def test_without_hooks_the_heuristic_still_runs():
    """Any agent that reports nothing must behave exactly as before."""
    res = run_in_pty(fixture("chatty.py", 4), timeout=20)
    covered = next((t for t, chunk in res.timeline if is_rain(chunk)), None)
    check("훅 없는 에이전트도 정상 동작", covered is not None and covered > 0.7, f"t={covered}")


def test_events_from_another_runner_are_ignored():
    """Panels must be independent whatever else writes to the FIFO (D-030)."""
    mine = HookChannel(EMITTER)
    assert mine.open()
    subprocess.run([EMITTER, mine.path, "PreToolUse", mine.token], input=b"{}")
    subprocess.run([EMITTER, mine.path, "Stop", "someone-elses-token"], input=b"{}")
    time.sleep(0.2)
    events = mine.read_events()
    mine.close()
    tokens = [e.get("token") for e in events]
    check("두 이벤트 모두 도착 (조건 성립)", len(events) == 2, str(events))
    check("자기 토큰 이벤트를 구분 가능", tokens[0] == mine.token != tokens[1], str(tokens))


def test_two_channels_get_different_tokens_and_paths():
    a, b = HookChannel(EMITTER), HookChannel(EMITTER, path="/tmp/mx-other.fifo")
    check("토큰이 서로 다름", a.token != b.token, "같음")
    check("설정에 각자의 토큰이 박힘", a.token in a.settings_json() and b.token in b.settings_json())


def test_a_waiting_agent_does_not_go_stale():
    """After Stop, nothing happening is not a reason to start guessing again.

    Reported: a finished panel re-covered itself minutes later, when another
    terminal starting a Claude session made its footer repaint. The stall
    backstop had expired and the timing heuristic took that blip for work.
    """
    import amask.cli as cli
    from amask import hooks as hooks_mod

    class Fake:
        available = True

    runner = cli.Runner.__new__(cli.Runner)
    runner.hooks = Fake()
    runner._hook_state = hooks_mod.WAITING
    runner._hook_at = time.monotonic() - (cli.HOOK_STALL + 60)
    check("오래된 waiting도 유효", runner._hooks_speaking(), "만료됨")

    runner._hook_state = hooks_mod.BUSY
    check("오래된 busy는 만료", not runner._hooks_speaking(), "유효 판정")
    runner._hook_at = time.monotonic()
    check("최근 busy는 유효", runner._hooks_speaking(), "만료됨")


def test_an_idle_panel_ignores_its_own_repaints():
    """The end-to-end shape of the same bug: Stop, then chatter, no re-cover."""
    res = run_in_pty(
        fixture("hooked.py"),
        env={"HOOKED_WORK": "3", "AMASK_IDLE_SILENCE": "1.0",
             "AMASK_HOOK_STALL": "2"},
        timeout=16,
        observe=True,
    )
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 6.0]
    check("stall 경과 후에도 재진입 없음", not after, f"재진입 t={after[0] if after else None}")


def test_stale_fifos_are_swept():
    import tempfile

    directory = tempfile.mkdtemp()
    dead = os.path.join(directory, "amask-hooks-999999.fifo")
    os.mkfifo(dead, 0o600)
    HookChannel(EMITTER, path=os.path.join(directory, "amask-hooks-1.fifo")).open()
    check("죽은 프로세스의 FIFO 정리", not os.path.exists(dead), "남아있음")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("훅 스위트 전부 통과")
