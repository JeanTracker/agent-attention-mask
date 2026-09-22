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
from amask.hud import Hud

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
    from amask.judge import Judge

    # The judgement, with the clock handed in rather than waited out (D-054).
    settings = cli.Settings.from_env()
    judge = Judge(settings, lambda: True)
    now = 1000.0
    judge.hook_state = hooks_mod.WAITING
    judge.hook_at = now - (settings.hook_stall + 60)
    check("오래된 waiting도 유효", judge.hooks_speaking(now), "만료됨")

    judge.hook_state = hooks_mod.BUSY
    check("오래된 busy는 만료", not judge.hooks_speaking(now), "유효 판정")
    judge.hook_at = now
    check("최근 busy는 유효", judge.hooks_speaking(now), "만료됨")


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


# -- events that arrive after their turn stopped (D-032) --------------------


def _seq_run(seq, total=14, timeout=20, feed=(), **env):
    """Drive the runner against a scripted hook sequence."""
    base = {"HOOKED_SEQ": json.dumps(seq), "HOOKED_TOTAL": str(total),
            "AMASK_IDLE_SILENCE": "1.0"}
    base.update(env)
    return run_in_pty(fixture("hooked_seq.py"), env=base, feed=feed,
                      timeout=timeout, observe=True)


def test_subagent_stop_after_its_stop_is_ignored():
    """The measured shape: SubagentStop lands ~2s after the Stop of its turn.

    Claude Code builds the next-prompt suggestion in an unnamed internal
    subagent once the turn is over. Taking its SubagentStop for work re-covered
    the screen and showed "working" while the agent sat idle -- the panel
    disagreeing with iTerm2's own session status (D-033).
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "무엇이 문제인가"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [3.0, "PostToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [3.2, "Stop", {"prompt_id": "p1"}],
        [5.3, "SubagentStop", {"prompt_id": "p1", "agent_type": "",
                               "agent_id": "ae4865d65c745a4f0"}],
    ])
    check("Stop 시점에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 3.2),
          "작업 중에도 덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 4.0]
    check("Stop 이후 도착한 SubagentStop이 화면을 다시 덮지 않음",
          not after, f"재진입 t={after[0] if after else None}")


def test_next_real_turn_still_covers():
    """The guard keys on the stopped turn only, so the next one is unaffected."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "첫 질문"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [2.0, "Stop", {"prompt_id": "p1"}],
        [2.6, "SubagentStop", {"prompt_id": "p1", "agent_type": "",
                               "agent_id": "ae4865d65c745a4f0"}],
        [3.4, "UserPromptSubmit", {"prompt_id": "p2", "prompt": "둘째 질문"}],
        [3.6, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=12)
    later = [t for t, chunk in res.timeline if is_rain(chunk) and t > 4.0]
    check("다음 턴은 정상적으로 다시 덮인다", later,
          "p2가 시작됐는데도 덮이지 않음")


def test_background_subagent_tool_call_is_not_main_state():
    """The spec's rule is about agent_id, not about one event name.

    A background subagent's PreToolUse landing after the main thread's Stop
    would otherwise re-cover the screen exactly like the suggestion subagent.
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "질문"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [3.2, "Stop", {"prompt_id": "p1"}],
        [5.0, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash",
                             "agent_id": "a4fa65ccfc10d929c",
                             "agent_type": "general-purpose"}],
    ])
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 4.0]
    check("서브에이전트의 툴 호출도 메인 상태로 읽지 않음",
          not after, f"재진입 t={after[0] if after else None}")


def test_main_thread_tool_call_still_counts():
    """And an event without agent_id is still the main thread working."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "질문"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [2.0, "Stop", {"prompt_id": "p1"}],
        [3.4, "UserPromptSubmit", {"prompt_id": "p2", "prompt": "다음 질문"}],
        [3.6, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=12)
    later = [t for t, chunk in res.timeline if is_rain(chunk) and t > 4.0]
    check("agent_id 없는 이벤트는 여전히 메인 작업", later, "덮이지 않음")


def test_notification_does_not_close_the_turn():
    """A permission prompt is mid-turn, so the work after approval still counts.

    Marking the turn stopped on Notification would make every PreToolUse that
    follows the user's approval look like a straggler.
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "질문"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [1.8, "Notification", {"prompt_id": "p1",
                               "notification_type": "tool_permission",
                               "message": "Claude needs your permission"}],
        [3.2, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
    ], total=12)
    resumed = [t for t, chunk in res.timeline if is_rain(chunk) and t > 3.6]
    check("Notification 이후의 같은 턴 작업은 여전히 작업으로 인정",
          resumed, "승인 후 작업인데 덮이지 않음")


# -- prompts the harness injected, not the user (D-031) ---------------------


TASK_NOTIFICATION = (
    "<task-notification>\n<task-id>bwr0likft</task-id>\n"
    "<tool-use-id>toolu_01DKa4dFc5CnuyJphXuCwNeW</tool-use-id>\n"
    "<status>completed</status>\n</task-notification>"
)


def test_injected_prompt_stays_off_the_panel():
    """A finished background task arrives as UserPromptSubmit carrying markup.

    The panel showed that markup where the question belongs. It should keep
    showing what the user actually asked.
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "레인 색을 고쳐줘"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [2.0, "Stop", {"prompt_id": "p1"}],
        [3.0, "UserPromptSubmit", {"prompt_id": "p2", "prompt": TASK_NOTIFICATION}],
        [3.2, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=12)
    check("주입된 알림 마크업이 패널에 나오지 않음",
          b"task-notification" not in res.data, "패널에 마크업이 그려졌다")
    check("사용자가 실제로 물어본 것이 패널에 유지됨",
          "레인 색을 고쳐줘".encode() in res.data, "질문이 사라졌다")


def test_real_prompt_still_reaches_the_panel():
    """The filter must not swallow an ordinary question."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1",
                                   "prompt": "왜 <div> 태그가 깨지나"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
    ], total=10)
    check("꺽쇠가 들어간 평범한 질문은 그대로 표시",
          "왜 <div> 태그가 깨지나".encode() in res.data, "질문이 걸러졌다")


def test_emitter_forwards_prompt_id():
    """The guard is worthless if prompt_id never leaves the hook process."""
    channel = HookChannel(EMITTER)
    channel.open()
    subprocess.run([EMITTER, channel.path, "SubagentStop"],
                   input=b'{"prompt_id":"p9","agent_id":"ag1","agent_type":""}')
    time.sleep(0.2)
    events = channel.read_events()
    channel.close()
    got = events[0] if events else {}
    check("emitter가 prompt_id를 전달", got.get("prompt_id") == "p9", str(events))
    check("emitter가 agent_id를 전달", got.get("agent_id") == "ag1", str(events))


# -- Stop means the user can type, so the screen stays theirs (D-035) --------


def test_stop_with_background_tasks_never_re_covers():
    """`Stop` while a background subagent runs is still WAITING, forever.

    Measured: the main thread's `Stop` arrives with `background_tasks` still
    listing a running subagent, and from that moment a typed prompt executes
    immediately -- so covering the screen would hide an input box that works.
    OVERLAY_DELAY is squeezed to 1.5s here and the sequence runs 14s, which is
    room for eight covers if the state ever flipped back.
    """
    running = [{"id": "ad6f92b64daf7bd60", "type": "subagent",
                "status": "running", "description": "Sleep 45 then report",
                "agent_type": "general-purpose"}]
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "서브에이전트 띄워줘"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Agent"}],
        [2.0, "PostToolUse", {"prompt_id": "p1", "tool_name": "Agent"}],
        [2.2, "Stop", {"prompt_id": "p1", "background_tasks": running}],
        # The subagent keeps working, and keeps saying so.
        [4.0, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash",
                             "agent_id": "ad6f92b64daf7bd60",
                             "agent_type": "general-purpose"}],
        [8.0, "PostToolUse", {"prompt_id": "p1", "tool_name": "Bash",
                              "agent_id": "ad6f92b64daf7bd60",
                              "agent_type": "general-purpose"}],
        [9.0, "SubagentStop", {"prompt_id": "p1", "agent_type": "general-purpose",
                               "agent_id": "ad6f92b64daf7bd60",
                               "background_tasks": running}],
    ], total=14, timeout=22, AMASK_OVERLAY_DELAY="1.5")
    check("Stop 시점에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 2.2),
          "작업 중에도 덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 3.0]
    check("백그라운드가 도는 중에도 Stop 이후로는 덮지 않는다",
          not after, f"재진입 t={after[0] if after else None}")


def test_the_injected_notification_turn_still_covers():
    """The one thing that may cover after a `Stop`: a new turn.

    A finished background task comes back as an injected `UserPromptSubmit`
    (D-031) and real work follows it, so that turn is genuinely busy. This is
    the case the rule above must not swallow -- without it, "never cover after
    Stop" would mean never covering again for the rest of the session.
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "서브에이전트 띄워줘"}],
        [2.0, "Stop", {"prompt_id": "p1", "background_tasks": [
            {"id": "bxge9kxgq", "type": "shell", "status": "running"}]}],
        [4.0, "UserPromptSubmit", {"prompt_id": "p2",
                                   "prompt": "<task-notification>\n<task-id>bxge9kxgq</task-id>"}],
        [4.4, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=14, timeout=22, AMASK_OVERLAY_DELAY="1.5")
    quiet = [t for t, chunk in res.timeline if is_rain(chunk) and 2.5 < t < 4.0]
    check("주입 전의 유휴 구간은 덮이지 않는다",
          not quiet, f"재진입 t={quiet[0] if quiet else None}")
    later = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.0]
    check("주입된 알림이 연 턴은 작업으로 인정된다", later,
          "새 턴이 시작됐는데도 덮이지 않음")


# -- the skip key (D-036) ---------------------------------------------------


def test_skip_key_keeps_the_screen_uncovered_for_the_turn():
    """Interrupting fires no hook, so the user needs a way out by hand.

    The hook channel keeps claiming work for HOOK_STALL after an interrupt and
    there is no event to release it. `q` while covered says "not this turn".
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "긴 작업"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
    ], total=14, timeout=20, feed=[(4.0, b"q")])
    check("q 이전에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 4.0),
          "덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.5]
    check("q 이후 같은 턴에서는 다시 덮지 않음",
          not after, f"재진입 t={after[0] if after else None}")


def test_other_keys_still_only_buy_reading_time():
    """Every other key keeps the old promise: reading time, not a dismissal."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "긴 작업"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
    ], total=16, timeout=22, feed=[(4.0, b"x")])
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.5]
    check("다른 키는 억제하지 않는다 (다시 덮인다)", after, "재진입하지 않음")


def test_next_prompt_clears_the_skip():
    """The skip is scoped to the turn the user skipped, not to the session."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "첫 작업"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
        [6.0, "UserPromptSubmit", {"prompt_id": "p2", "prompt": "둘째 작업"}],
        [6.2, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=18, timeout=24, feed=[(4.0, b"q")])
    skipped = [t for t, chunk in res.timeline if is_rain(chunk) and 5.0 < t < 6.0]
    resumed = [t for t, chunk in res.timeline if is_rain(chunk) and t > 7.5]
    check("억제 중에는 덮이지 않음", not skipped, f"t={skipped[0] if skipped else None}")
    check("새 프롬프트가 오면 억제 해제", resumed, "다음 턴에도 덮이지 않음")


def test_skip_holds_while_a_subagent_of_that_turn_keeps_working():
    """`q` covers the whole turn, subagents included.

    A turn the user skipped often has an `Agent` call in it, and the subagent
    goes on firing events afterwards. Those are the same request the user just
    said "not this turn" about, so none of them may put the rain back (D-038).
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "서브에이전트 띄워줘"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Agent"}],
        [6.0, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash",
                             "agent_id": "ad6f92b64daf7bd60",
                             "agent_type": "general-purpose"}],
        [7.0, "PostToolUse", {"prompt_id": "p1", "tool_name": "Bash",
                              "agent_id": "ad6f92b64daf7bd60",
                              "agent_type": "general-purpose"}],
        [8.0, "SubagentStop", {"prompt_id": "p1", "agent_type": "general-purpose",
                               "agent_id": "ad6f92b64daf7bd60"}],
    ], total=16, timeout=22, feed=[(4.0, b"q")])
    check("q 이전에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 4.0),
          "덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.5]
    check("서브에이전트 이벤트가 억제를 깨지 않음",
          not after, f"재진입 t={after[0] if after else None}")


def test_injected_notification_does_not_clear_the_skip():
    """A background task finishing is not the user asking for something.

    The turn it opens is genuinely busy (D-035), but the user has already said
    "not this turn" about the request that started that background work, so
    the skip has to survive it. Only a prompt the *user* submitted releases it.
    """
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "서브에이전트 띄워줘"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Agent"}],
        [6.0, "UserPromptSubmit", {"prompt_id": "p2", "prompt": TASK_NOTIFICATION}],
        [6.2, "PreToolUse", {"prompt_id": "p2", "tool_name": "Bash"}],
    ], total=16, timeout=22, feed=[(4.0, b"q")])
    check("q 이전에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 4.0),
          "덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 7.5]
    check("주입된 알림이 억제를 풀지 않음",
          not after, f"재진입 t={after[0] if after else None}")


def test_skip_key_does_not_reach_the_agent():
    """D-008 still holds: the skip key is spent on the runner, not forwarded."""
    res = _seq_run([
        [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "작업"}],
        [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
    ], total=12, timeout=18, feed=[(4.0, b"q")])
    check("q가 에이전트 입력으로 새지 않음", b"SAW:q" not in res.data,
          "픽스처가 q를 받았다")


# -- the idle hint, which is emphasis and nothing else ----------------------


def test_idle_hint_is_off_while_output_flows():
    from amask.hud import HINT_SKIP, HINT_SKIP_LOUD

    check("두 힌트 폭이 같다 (패널이 흔들리지 않음)",
          len(HINT_SKIP) == len(HINT_SKIP_LOUD),
          f"{len(HINT_SKIP)} vs {len(HINT_SKIP_LOUD)}")

    hud = Hud("claude", "~/x")
    hud.prompt = "질문"
    plain = hud.layout(40, 100, busy=True, since=time.monotonic(), idle_hint=False)
    loud = hud.layout(40, 100, busy=True, since=time.monotonic(), idle_hint=True)
    flat = lambda panel: " ".join(
        text for line in panel.lines for seg in line for _, _, text in seg)
    check("평소에는 조용한 힌트", HINT_SKIP in flat(plain), flat(plain))
    check("평소에는 강조 힌트가 없다", HINT_SKIP_LOUD not in flat(plain), flat(plain))
    check("idle 의심 시 강조 힌트", HINT_SKIP_LOUD in flat(loud), flat(loud))
    check("행 수는 그대로 (표시만 바뀜)",
          len(plain.lines) == len(loud.lines),
          f"{len(plain.lines)} vs {len(loud.lines)}")


def test_idle_hint_never_touches_state():
    """The one surviving silence threshold must not reach the state machine."""
    import inspect

    from amask import cli

    source = inspect.getsource(cli.Runner._tick)
    check("_tick은 idle_suspected를 상태 판정에 쓰지 않음",
          source.count("idle_suspected") == 1
          and "idle_hint=self.judge.idle_suspected(" in source,
          "상태 분기에서 참조됨")

    # And the judge itself keeps it out of the decision (D-036, D-054).
    from amask.judge import Judge

    decision = inspect.getsource(Judge.should_cover) + inspect.getsource(Judge.idle)
    check("Judge의 판정 경로에도 들어가지 않는다",
          "idle_suspected" not in decision, decision)


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("훅 스위트 전부 통과")
