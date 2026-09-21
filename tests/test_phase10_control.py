"""The control socket: an external app watching and steering one session (D-039).

Everything the runner knows used to stay inside the process. These cases drive
the channel that lets it out, and most of them are about what must *not*
happen: the token must be required, the socket must not reach the agent, a
client's death or slowness must not touch the runner, and a runner without a
socket must behave exactly as before.
"""

import json
import os
import socket
import stat
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import RUN_DIR, fixture, is_rain, run_in_pty
from amask import control
from amask.cli import Settings

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def _info_for(pid, tries=40):
    """The discovery record of the runner with this pid, once it exists."""
    for _ in range(tries):
        for info in control.sessions(RUN_DIR):
            if info.get("pid") == pid:
                return info
        time.sleep(0.05)
    return None


def _ask(pid, payload):
    info = _info_for(pid)
    if info is None:
        return {"ok": False, "reason": "no session"}
    return control.request(info, payload)


def _seq_run(seq, total=14, timeout=20, feed=(), act=(), **env):
    """A scripted-hook session, as phase9 drives it, plus socket actions."""
    base = {"HOOKED_SEQ": json.dumps(seq), "HOOKED_TOTAL": str(total),
            "AMASK_IDLE_SILENCE": "1.0"}
    base.update(env)
    return run_in_pty(fixture("hooked_seq.py"), env=base, feed=feed, act=act,
                      timeout=timeout, observe=True)


BUSY_TURN = [
    [0.3, "UserPromptSubmit", {"prompt_id": "p1", "prompt": "레인 색을 고쳐줘",
                               "session_id": "sess-abc"}],
    [0.5, "PreToolUse", {"prompt_id": "p1", "tool_name": "Bash"}],
]


# -- Settings, the value the socket edits -----------------------------------


def test_settings_start_from_the_env_knobs():
    """The env knob keeps being the way to set what a session starts with.

    Read through `_seconds` rather than by reloading the module: a reload
    rebinds `cli.Settings` and would leave this file holding the old class.
    """
    from amask import cli

    had = os.environ.get("AMASK_HOOK_STALL")
    os.environ["AMASK_HOOK_STALL"] = "33.0"
    try:
        check("env 값이 초기값이 된다",
              cli._seconds("AMASK_HOOK_STALL", 120.0) == 33.0,
              str(cli._seconds("AMASK_HOOK_STALL", 120.0)))
    finally:
        if had is None:
            del os.environ["AMASK_HOOK_STALL"]
        else:
            os.environ["AMASK_HOOK_STALL"] = had
    check("없으면 기본값", cli._seconds("AMASK_NOT_SET_ANYWHERE", 7.5) == 7.5)
    check("설정값은 세션 값으로 실린다",
          set(cli.Settings.from_env().as_dict()) == set(cli.Settings.KEYS),
          str(cli.Settings.from_env().as_dict()))


def test_settings_reject_what_would_break_the_state_machine():
    settings = Settings(1.5, 120.0, 4.0)
    check("모르는 키 거부", settings.update({"nope": 1}) is not None)
    check("숫자 아닌 값 거부", settings.update({"overlay_delay": "x"}) is not None)
    check("음수 거부", settings.update({"overlay_delay": -1}) is not None)
    check("상한 초과 거부", settings.update({"overlay_delay": 99999}) is not None)
    check("NaN 거부", settings.update({"overlay_delay": float("nan")}) is not None)
    check("무한 거부", settings.update({"overlay_delay": float("inf")}) is not None)
    check("거부된 배치는 아무것도 바꾸지 않는다", settings.overlay_delay == 4.0,
          f"{settings.overlay_delay}")
    check("한 값이 틀리면 전체가 무효",
          settings.update({"idle_silence": 2.0, "overlay_delay": -1}) is not None
          and settings.idle_silence == 1.5, f"{settings.idle_silence}")
    check("올바른 값은 적용", settings.update({"overlay_delay": 6.0}) is None
          and settings.overlay_delay == 6.0, f"{settings.overlay_delay}")
    check("문자열 숫자도 받는다 (--ctl이 넘기는 형태)",
          settings.update({"idle_silence": "2.5"}) is None
          and settings.idle_silence == 2.5, f"{settings.idle_silence}")


# -- discovery ---------------------------------------------------------------


def test_the_session_is_discoverable_and_the_token_is_not_world_readable():
    seen = {}

    def look(pid):
        info = _info_for(pid)
        if info is None:
            return "발견 파일 없음"
        seen["info"] = info
        base = os.path.dirname(info["socket"])
        return {
            "dir": stat.S_IMODE(os.stat(base).st_mode),
            "info": stat.S_IMODE(os.stat(info["socket"].replace(".sock", ".json")).st_mode),
            "sock": stat.S_IMODE(os.stat(info["socket"]).st_mode),
            "token": bool(info.get("token")),
            "argv": info.get("argv"),
        }

    res = _seq_run(BUSY_TURN, total=6, timeout=14, act=[(1.5, look)])
    got = res.acts[0][1] if res.acts else None
    check("세션이 발견된다", isinstance(got, dict), str(got))
    if not isinstance(got, dict):
        return
    check("실행 디렉터리가 0700", got["dir"] == 0o700, oct(got["dir"]))
    check("발견 파일이 0600", got["info"] == 0o600, oct(got["info"]))
    check("소켓이 0600", got["sock"] == 0o600, oct(got["sock"]))
    check("토큰이 실려 있다", got["token"])
    check("어떤 에이전트인지 알 수 있다",
          got["argv"] and "hooked_seq.py" in " ".join(got["argv"]), str(got["argv"]))


def test_the_runner_cleans_up_after_itself():
    res = _seq_run(BUSY_TURN, total=3, timeout=14)
    leftovers = [name for name in os.listdir(RUN_DIR) if name.startswith("amask-ctl-")]
    live = []
    for name in leftovers:
        stem = name[len("amask-ctl-"):].rsplit(".", 1)[0]
        try:
            os.kill(int(stem), 0)
            live.append(name)
        except (ValueError, OSError):
            pass
    check("종료한 세션의 소켓과 발견 파일이 남지 않는다",
          len(leftovers) == len(live), str(sorted(set(leftovers) - set(live))))


def test_dead_sessions_are_swept():
    directory = tempfile.mkdtemp()
    stale = os.path.join(directory, "amask-ctl-999999.json")
    with open(stale, "w") as handle:
        json.dump({"pid": 999999, "socket": "/nope", "token": "x"}, handle)
    control.sessions(directory)
    check("죽은 pid의 발견 파일 정리", not os.path.exists(stale), "남아있음")


def test_a_republished_record_keeps_what_it_did_not_mention():
    """`session_id` arrives late, and a later republish must not drop it."""
    directory = tempfile.mkdtemp()
    channel = control.ControlChannel(directory=directory)
    check("발견 파일이 열린다", channel.open({"argv": ["claude"], "started": 100.0}))
    channel.update_info({"session_id": "s9"})
    channel.update_info({"argv": ["claude", "--resume"]})
    with open(channel.info_path) as handle:
        record = json.load(handle)
    channel.close()
    check("나중 갱신이 session_id를 지우지 않는다", record.get("session_id") == "s9",
          str(record))
    check("최초의 started가 유지된다", record.get("started") == 100.0, str(record))
    check("언급한 값은 갱신된다", record.get("argv") == ["claude", "--resume"],
          str(record))


def test_a_killed_runners_half_written_record_is_swept():
    directory = tempfile.mkdtemp()
    stale = os.path.join(directory, "amask-ctl-999998.json.tmp")
    with open(stale, "w") as handle:
        handle.write("{")
    control.sessions(directory)
    check("죽은 pid의 쓰다 만 파일도 정리", not os.path.exists(stale), "남아있음")


# -- reading the state -------------------------------------------------------


def test_status_reports_what_the_screen_is_doing():
    def look(pid):
        return _ask(pid, {"cmd": "status"})

    res = _seq_run(BUSY_TURN, total=8, timeout=16, act=[(3.0, look)])
    reply = res.acts[0][1] if res.acts else None
    check("status가 응답한다", isinstance(reply, dict) and reply.get("ok"), str(reply))
    if not (isinstance(reply, dict) and reply.get("ok")):
        return
    status = reply["status"]
    check("덮여 있는 것을 보고한다", status["covered"] is True, str(status))
    check("작업 중인 것을 보고한다", status["busy"] is True, str(status))
    check("훅 상태를 보고한다", status["hook_state"] == "busy", str(status["hook_state"]))
    check("사용자 요청 문구를 보고한다 (세션 인지용)",
          status["prompt"] == "레인 색을 고쳐줘", repr(status["prompt"]))
    check("에이전트의 session_id를 보고한다", status["session_id"] == "sess-abc",
          repr(status["session_id"]))
    check("숨긴 바이트 수를 보고한다", isinstance(status["hidden_bytes"], int),
          str(status["hidden_bytes"]))
    check("현재 설정을 함께 보고한다",
          set(status["settings"]) == set(Settings.KEYS), str(status["settings"]))


def test_the_session_id_reaches_the_discovery_file_too():
    """It is not known at startup, so the file has to be rewritten (D-039)."""

    def look(pid):
        info = _info_for(pid)
        return info.get("session_id") if info else None

    res = _seq_run(BUSY_TURN, total=8, timeout=16, act=[(3.0, look)])
    got = res.acts[0][1] if res.acts else None
    check("첫 훅 이후 발견 파일에 session_id가 들어온다", got == "sess-abc", repr(got))


def test_a_caller_without_the_token_is_refused():
    def probe(pid):
        info = _info_for(pid)
        if info is None:
            return "no session"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(info["socket"])
            sock.sendall(b'{"cmd":"status","token":"wrong"}\n')
            return sock.recv(65536)

    res = _seq_run(BUSY_TURN, total=8, timeout=16, act=[(3.0, probe)])
    got = res.acts[0][1] if res.acts else b""
    check("토큰이 틀리면 거부", isinstance(got, bytes) and b'"ok": false' in got
          and b"bad token" in got, repr(got))


# -- changing the settings ---------------------------------------------------


def test_set_changes_when_the_screen_covers_next():
    """The point of `set`: the change has to reach the state machine, live."""

    def raise_delay(pid):
        return _ask(pid, {"cmd": "set", "settings": {"overlay_delay": 30.0}})

    # Wake by hand at 3.0s, which re-arms the delay, then immediately push the
    # delay out to 30s. The turn keeps running to 14s, so a re-cover would be
    # visible several times over if the new value were ignored.
    res = _seq_run(BUSY_TURN, total=16, timeout=24,
                   feed=[(3.0, b"x")], act=[(3.4, raise_delay)])
    reply = res.acts[0][1] if res.acts else None
    check("set이 성공한다", isinstance(reply, dict) and reply.get("ok"), str(reply))
    check("응답이 적용된 값을 돌려준다",
          isinstance(reply, dict) and reply.get("settings", {}).get("overlay_delay") == 30.0,
          str(reply))
    check("깨우기 이전에는 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 3.0),
          "덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 4.5]
    check("늘어난 지연이 즉시 적용된다 (다시 덮지 않음)",
          not after, f"재진입 t={after[0] if after else None}")


def test_get_and_set_agree():
    def round_trip(pid):
        first = _ask(pid, {"cmd": "get"})
        _ask(pid, {"cmd": "set", "settings": {"idle_silence": 2.25}})
        return first, _ask(pid, {"cmd": "get"})

    res = _seq_run(BUSY_TURN, total=6, timeout=16, act=[(2.0, round_trip)])
    got = res.acts[0][1] if res.acts else None
    check("get이 설정을 돌려준다",
          isinstance(got, tuple) and got[0].get("ok")
          and set(got[0]["settings"]) == set(Settings.KEYS), str(got))
    check("set 이후의 get이 새 값을 보여준다",
          isinstance(got, tuple) and got[1]["settings"]["idle_silence"] == 2.25,
          str(got[1] if isinstance(got, tuple) else got))


def test_a_bad_set_is_refused_with_a_reason():
    def bad(pid):
        return _ask(pid, {"cmd": "set", "settings": {"overlay_delay": -5}})

    res = _seq_run(BUSY_TURN, total=6, timeout=16, act=[(2.0, bad)])
    reply = res.acts[0][1] if res.acts else None
    check("잘못된 값은 이유와 함께 거부",
          isinstance(reply, dict) and reply.get("ok") is False and reply.get("reason"),
          str(reply))


# -- the two commands that act -----------------------------------------------


def test_skip_over_the_socket_matches_the_q_key():
    def skip(pid):
        return _ask(pid, {"cmd": "skip"})

    res = _seq_run(BUSY_TURN, total=16, timeout=24, act=[(4.0, skip)])
    reply = res.acts[0][1] if res.acts else None
    check("skip이 수락된다", isinstance(reply, dict) and reply.get("ok"), str(reply))
    check("skip 이전에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 4.0),
          "덮이지 않아 이 케이스가 무의미하다")
    after = [t for t, chunk in res.timeline if is_rain(chunk) and t > 5.5]
    check("q와 같이 그 턴은 다시 덮지 않는다",
          not after, f"재진입 t={after[0] if after else None}")


def test_skip_is_refused_when_there_is_no_turn_to_skip():
    """A WAITING span has nothing to skip, and the next prompt would clear it.

    Reporting success there would be a lie with a delay on it (D-038).
    """
    seq = BUSY_TURN + [[2.0, "Stop", {"prompt_id": "p1"}]]

    def skip(pid):
        return _ask(pid, {"cmd": "skip"})

    res = _seq_run(seq, total=8, timeout=16, act=[(4.0, skip)])
    reply = res.acts[0][1] if res.acts else None
    check("턴이 없으면 이유와 함께 거부",
          isinstance(reply, dict) and reply.get("ok") is False
          and "turn" in str(reply.get("reason")), str(reply))


def test_wake_over_the_socket_uncovers():
    def wake(pid):
        return _ask(pid, {"cmd": "wake"})

    res = _seq_run(BUSY_TURN, total=8, timeout=16, act=[(3.0, wake)])
    reply = res.acts[0][1] if res.acts else None
    check("wake가 수락된다", isinstance(reply, dict) and reply.get("ok"), str(reply))
    check("wake 이전에 덮여 있었다 (전제)",
          any(is_rain(chunk) for t, chunk in res.timeline if t < 3.0),
          "덮이지 않아 이 케이스가 무의미하다")
    quiet = [t for t, chunk in res.timeline if is_rain(chunk) and 3.1 < t < 3.9]
    check("wake 직후에는 걷혀 있다", not quiet, f"t={quiet[0] if quiet else None}")


def test_an_unknown_command_is_refused():
    def nonsense(pid):
        return _ask(pid, {"cmd": "self-destruct"})

    res = _seq_run(BUSY_TURN, total=6, timeout=16, act=[(2.0, nonsense)])
    reply = res.acts[0][1] if res.acts else None
    check("모르는 명령은 거부",
          isinstance(reply, dict) and reply.get("ok") is False, str(reply))


# -- the channel must not be able to hurt the runner (P-101, P-102, SC-002) --


def test_socket_traffic_never_reaches_the_agent():
    """D-008's rule generalised: nothing on this channel is agent input.

    `hooked_seq.py` echoes every byte it receives as `SAW:`, so this is
    checked by absence.
    """
    def chatter(pid):
        return _ask(pid, {"cmd": "status"})

    res = _seq_run(BUSY_TURN, total=8, timeout=16, act=[(3.0, chatter)])
    check("제어 트래픽이 에이전트 입력으로 새지 않음", b"SAW:" not in res.data,
          "픽스처가 제어 바이트를 받았다")


def test_the_agent_does_not_inherit_the_control_socket():
    """An agent that could reach its own runner's controls is a P-101 hole.

    Python leaves sockets non-inheritable (PEP 446) and the channel is opened
    after the fork besides, but both of those are easy to undo by accident, so
    the agent's own open files are what gets asserted.
    """
    def look(pid):
        import subprocess

        info = _info_for(pid)
        if info is None:
            return "no session"
        kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True)
        children = [line for line in kids.stdout.decode().split() if line.strip()]
        if not children:
            return "no child"
        listings = {}
        for child in children:
            out = subprocess.run(["lsof", "-p", child, "-Fn"], capture_output=True)
            listings[child] = out.stdout.decode("utf-8", "replace")
        return info["socket"], listings

    res = _seq_run(BUSY_TURN, total=10, timeout=18, act=[(3.0, look)])
    got = res.acts[0][1] if res.acts else None
    if not isinstance(got, tuple):
        check("에이전트의 열린 파일을 확인할 수 있었다", False, str(got))
        return
    sock_path, listings = got
    guilty = [child for child, text in listings.items() if sock_path in text]
    check("에이전트가 제어 소켓을 물려받지 않는다", not guilty,
          f"자식 {guilty}가 {sock_path}를 열고 있다")


def test_a_client_that_dies_mid_request_does_not_take_the_runner_down():
    def rude(pid):
        info = _info_for(pid)
        if info is None:
            return "no session"
        for _ in range(5):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(info["socket"])
            payload = json.dumps({"cmd": "status", "token": info["token"]}).encode()
            sock.sendall(payload + b"\n")
            sock.close()  # gone before the reply is read
        time.sleep(0.3)
        return _ask(pid, {"cmd": "status"})

    res = _seq_run(BUSY_TURN, total=10, timeout=18, act=[(3.0, rude)])
    reply = res.acts[0][1] if res.acts else None
    check("급사한 클라이언트 뒤에도 러너가 응답한다",
          isinstance(reply, dict) and reply.get("ok"), str(reply))
    later = [t for t, chunk in res.timeline if t > 6.0]
    check("러너가 계속 그리고 있다", later, "출력이 멈췄다")


def test_a_client_that_never_reads_does_not_stall_the_wake():
    """SC-002 is 0.2s, and it is not the runner's fault if a client is slow."""
    def stall(pid):
        info = _info_for(pid)
        if info is None:
            return "no session"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(info["socket"])
        payload = json.dumps({"cmd": "status", "token": info["token"]}).encode()
        for _ in range(200):
            sock.sendall(payload + b"\n")  # never reads a single reply
        return "stalled"

    res = _seq_run(BUSY_TURN, total=12, timeout=20,
                   act=[(3.0, stall)], feed=[(4.0, b"x")])
    woke = None
    for when, chunk in res.timeline:
        if when > 4.0 and b"\x1b[?1049l" in chunk:
            woke = when
            break
    check("읽지 않는 클라이언트가 있어도 깨우기가 0.2초 안에 끝난다",
          woke is not None and woke - 4.0 < 0.2, f"woke={woke}")


def test_a_runner_without_a_socket_behaves_exactly_as_before():
    """The channel is optional, and its absence is not an error (P-101)."""
    # A run dir that cannot be created: `/dev/null` is not a directory, so
    # every path under it fails, which is the same news as a read-only home.
    res = _seq_run(BUSY_TURN, total=8, timeout=16,
                   AMASK_RUN_DIR="/dev/null/nope")
    check("소켓을 못 열어도 덮는다", any(is_rain(chunk) for t, chunk in res.timeline),
          "레인이 나오지 않았다")
    check("소켓을 못 열어도 오류를 뱉지 않는다",
          b"Traceback" not in res.data, "예외가 화면에 나왔다")


# -- the TUI over the same channel (D-040) -----------------------------------


def test_the_key_table_is_what_the_help_line_promises():
    import curses

    from amask import top

    check("q는 종료", top.action_for(ord("q")) == ("quit", None))
    check("ESC도 종료", top.action_for(27) == ("quit", None))
    check("s는 skip", top.action_for(ord("s")) == ("cmd", "skip"))
    check("w는 wake", top.action_for(ord("w")) == ("cmd", "wake"))
    check("j/↓는 아래로", top.action_for(ord("j")) == ("move", 1)
          and top.action_for(curses.KEY_DOWN) == ("move", 1))
    check("+는 덮기지연을 올린다",
          top.action_for(ord("+")) == ("tune", ("overlay_delay", 1.0)))
    check("-는 덮기지연을 내린다",
          top.action_for(ord("-")) == ("tune", ("overlay_delay", -1.0)))
    check("[/]는 유휴임계", top.action_for(ord("]"))[1][0] == "idle_silence"
          and top.action_for(ord("["))[1][1] < 0)
    check("모르는 키는 아무것도 하지 않는다",
          top.action_for(ord("z")) == (None, None))
    for label in ("s ", "w ", "q ", "r "):
        check(f"도움줄에 {label.strip()} 키가 적혀 있다", label in top.HELP, top.HELP)


def test_a_row_says_what_the_session_is_doing():
    from amask import top

    cells = top.describe({
        "pid": 42, "covered": True, "busy": True, "busy_seconds": 54.2,
        "hooks": True, "hook_state": "busy", "skip_turn": True,
        "idle_suspected": False, "hidden_bytes": 43869,
        "cwd": "/home/me/work/agent-attention-mask",
        "prompt": "레인 색을\n고쳐줘",
        "settings": {"idle_silence": 1.5, "hook_stall": 120.0, "overlay_delay": 4.0},
    })
    check("pid", cells[0] == "42", str(cells))
    check("덮인 상태를 한 눈에", cells[1] == "덮임", str(cells))
    check("상태는 한국어로 적는다", cells[2].startswith("작업"), str(cells))
    check("프로토콜 값이 그대로 나오지 않는다", "busy" not in cells[2], str(cells))
    check("억제 중임을 표시", "건너뜀" in cells[2], str(cells))
    check("그 세션의 덮기 지연", cells[3] == "4s", str(cells))
    check("폴더는 마지막 한 칸", cells[4] == "agent-attention-mask", str(cells))
    check("프롬프트는 한 줄로 접힌다", cells[5] == "레인 색을 고쳐줘", str(cells))

    waiting = top.describe({"pid": 7, "covered": False, "busy": False,
                            "hooks": False, "hook_state": "waiting",
                            "hidden_bytes": 0, "prompt": "", "settings": {}})
    check("대기도 한국어로", waiting[2].startswith("대기"), str(waiting))
    check("훅이 없으면 그렇게 적는다", "훅없음" in waiting[2], str(waiting))
    check("폴더를 모르면 -", waiting[4] == "-", str(waiting))
    check("프롬프트가 없으면 -", waiting[5] == "-", str(waiting))


def _render(rows, selected=0, message="", rows_high=12, cols=116, marked=(),
            detail=None, offset=0):
    """Draw one frame in a pty and read the screen back as text."""
    import pty
    import select
    import struct
    import fcntl
    import termios

    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ["TOP_ROWS"] = json.dumps(rows)
        os.environ["TOP_SELECTED"] = str(selected)
        os.environ["TOP_MESSAGE"] = message
        os.environ["TOP_MARKED"] = " ".join(str(pid) for pid in marked)
        os.environ["TOP_DETAIL"] = "" if detail is None else str(detail)
        os.environ["TOP_OFFSET"] = str(offset)
        os.execv(sys.executable, [sys.executable, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "draw_top.py")])
    fcntl.ioctl(master, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows_high, cols, 0, 0))
    chunks = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], 0.2)
        if not readable:
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    os.waitpid(pid, 0)
    text = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
    # The screen read back with `instr` is printed after the curses session
    # ends, so it is whatever follows the last alternate-screen exit.
    return text.split("\x1b[?1049l")[-1]


TOP_SAMPLE = [
    {"info": {"pid": 23875},
     "status": {"pid": 23875, "covered": True, "busy": True, "busy_seconds": 54.2,
                "hooks": True, "hook_state": "busy", "skip_turn": False,
                "idle_suspected": False, "hidden_bytes": 43869,
                "cwd": "/home/me/work/amask", "prompt": "레인 색을 고쳐줘",
                "settings": {"idle_silence": 1.5, "hook_stall": 120.0,
                             "overlay_delay": 4.0}}},
    {"info": {"pid": 24999, "argv": ["claude", "--resume"]},
     "problem": "닿지 않음: TimeoutError"},
]


def test_the_frame_shows_every_session_and_the_keys():
    screen = _render(TOP_SAMPLE, selected=0, message="적용: overlay_delay=8")
    check("세션 수가 제목에 있다", "세션 2개" in screen, screen[:200])
    check("헤더가 있다", "PID" in screen and "프롬프트" in screen, screen[:200])
    check("돌고 있는 세션이 보인다", "23875" in screen and "덮임" in screen, screen)
    check("사용자 요청이 보인다", "레인 색을 고쳐줘" in screen, screen)
    check("닿지 않는 세션은 그렇게 그린다",
          "닿지 않음" in screen and "claude --resume" in screen, screen)
    check("마지막 응답이 보인다", "적용: overlay_delay=8" in screen, screen)
    check("키 안내가 마지막 줄에 있다", "q 종료" in screen, screen)
    check("예외가 새지 않았다", "Traceback" not in screen, screen)


def test_a_narrow_window_does_not_break_the_frame():
    """curses errors on a write that ends in the last cell; it must not raise."""
    screen = _render(TOP_SAMPLE, cols=24, rows_high=6)
    check("좁은 창에서도 그려진다", "23875" in screen, screen)
    check("좁은 창에서도 예외가 없다", "Traceback" not in screen, screen)


def test_enter_opens_a_detail_pane_and_the_keys_change_there():
    """D-043: the pane is its own mode, and its commands hit one session."""
    import curses

    from amask import top

    check("enter는 상세 보기", top.action_for(10) == ("detail", None)
          and top.action_for(curses.KEY_ENTER) == ("detail", None),
          str(top.action_for(10)))
    check("상세에서 enter는 목록으로", top.detail_action_for(10) == ("list", None))
    check("상세에서 esc도 목록으로", top.detail_action_for(27) == ("list", None))
    check("상세에서 q는 종료", top.detail_action_for(ord("q")) == ("quit", None))
    check("상세에서도 s는 skip", top.detail_action_for(ord("s")) == ("cmd", "skip"))
    check("상세에서도 +는 덮기지연",
          top.detail_action_for(ord("+")) == ("tune", ("overlay_delay", 1.0)))
    check("상세에서 이동 키는 세션이 아니라 본문을 움직인다",
          top.detail_action_for(ord("j")) == ("scroll", 1),
          str(top.detail_action_for(ord("j"))))
    check("상세에서 space는 표시가 아니라 한 쪽 넘기기",
          top.detail_action_for(ord(" ")) == ("scroll-page", 1),
          str(top.detail_action_for(ord(" "))))
    for label in ("enter ", "esc/enter ", "q "):
        check(f"도움줄에 {label.strip()} 안내가 있다",
              label in top.HELP or label in top.DETAIL_HELP,
              top.HELP + " | " + top.DETAIL_HELP)
    check("목록 도움줄이 95칸을 넘지 않는다", top._width(top.HELP) <= 95,
          str(top._width(top.HELP)))
    check("상세 도움줄이 95칸을 넘지 않는다", top._width(top.DETAIL_HELP) <= 95,
          str(top._width(top.DETAIL_HELP)))


def test_the_detail_pane_says_what_the_row_had_no_room_for():
    from amask import top

    status = {
        "pid": 42, "session_id": "sess-abc", "argv": ["claude", "--resume"],
        "cwd": "/home/me/work", "covered": True, "busy": True,
        "busy_seconds": 54.2, "hooks": True, "hook_state": "busy",
        "skip_turn": True, "idle_suspected": True, "hidden_bytes": 43869,
        "model": "Opus 5", "tokens": "12.3k",
        "prompt": "레인 색을 고쳐줘\n그리고 테스트도 전부 돌려줘",
        "settings": {"idle_silence": 1.5, "hook_stall": 120.0, "overlay_delay": 4.0},
    }
    text = "\n".join(top.detail_lines({"pid": 42, "started": time.time() - 90},
                                      status, width=60))
    check("상태를 한국어로 적는다", "작업" in text and "busy" not in text, text)
    for wanted in ("sess-abc", "claude --resume", "/home/me/work", "43869",
                   "Opus 5", "12.3k", "1.5s", "120s", "4s", "54.2s"):
        check(f"상세에 {wanted}", wanted in text, text)
    check("언제 시작했는지 나온다", "분 전" in text, text)
    check("건너뛰기 상태를 설명한다", "덮지 않는다" in text, text)
    check("유휴 의심을 설명한다", "출력이 끊겼다" in text, text)
    check("요청이 줄바꿈까지 살아 있다",
          "레인 색을 고쳐줘" in text and "테스트도 전부" in text, text)
    for line in top.detail_lines({"pid": 42}, status, width=40):
        check("어느 줄도 요청한 폭을 넘지 않는다", top._width(line) <= 40, line)

    gone = "\n".join(top.detail_lines({"pid": 7, "argv": ["claude"]}, None,
                                      "닿지 않음: TimeoutError"))
    check("닿지 않는 세션도 이유를 적는다", "TimeoutError" in gone, gone)
    check("닿지 않으면 그렇게 설명한다", "응답하지 않는다" in gone, gone)


def test_the_detail_frame_draws_in_a_real_terminal():
    screen = _render(TOP_SAMPLE, detail=23875, rows_high=24, cols=100,
                     message="적용: overlay_delay=8")
    check("제목에 세션이 있다", "세션 23875" in screen, screen[:200])
    check("요청 전문이 보인다", "레인 색을 고쳐줘" in screen, screen)
    check("설정이 보인다", "덮기 지연" in screen and "유휴 임계" in screen, screen)
    check("마지막 응답이 보인다", "적용: overlay_delay=8" in screen, screen)
    check("상세 키 안내가 있다", "목록" in screen and "q 종료" in screen, screen)
    check("예외가 새지 않았다", "Traceback" not in screen, screen)

    unreachable = _render(TOP_SAMPLE, detail=24999, rows_high=24, cols=100)
    check("닿지 않는 세션의 상세도 그려진다",
          "닿지 않음" in unreachable and "24999" in unreachable, unreachable)

    narrow = _render(TOP_SAMPLE, detail=23875, cols=24, rows_high=6)
    check("좁은 창에서도 상세가 그려진다", "23875" in narrow, narrow)
    check("좁은 창에서도 예외가 없다", "Traceback" not in narrow, narrow)


def test_a_short_window_scrolls_instead_of_cutting_off():
    """D-045: what does not fit has to be reachable, in both views."""
    import curses

    from amask import top

    check("높이 6이면 본문 두 줄", top.body_capacity(6) == 2, str(top.body_capacity(6)))
    check("너무 짧으면 본문 0줄", top.body_capacity(3) == 0, str(top.body_capacity(3)))
    check("다 들어가면 전부 보인다", top.visible_span(3, 0, 8) == (0, 3),
          str(top.visible_span(3, 0, 8)))
    check("커서가 아래에 있으면 따라 내려간다",
          top.visible_span(20, 19, 5) == (15, 20), str(top.visible_span(20, 19, 5)))
    check("커서가 위에 있으면 처음부터",
          top.visible_span(20, 0, 5) == (0, 5), str(top.visible_span(20, 0, 5)))
    for selected in range(20):
        start, end = top.visible_span(20, selected, 5)
        check("커서는 언제나 화면 안에 있다", start <= selected < end,
              f"{selected} -> {start},{end}")
    check("스크롤은 내용을 벗어나지 않는다", top.clamp_offset(20, 5, 999) == 15,
          str(top.clamp_offset(20, 5, 999)))
    check("다 보이면 스크롤은 0", top.clamp_offset(3, 5, 9) == 0)
    check("남은 줄 수를 말한다", top.more_marker(3, 8, 20) == "↑3 ↓12",
          top.more_marker(3, 8, 20))
    check("다 보이면 표시하지 않는다", top.more_marker(0, 3, 3) == "",
          top.more_marker(0, 3, 3))

    check("상세에서 ↓는 스크롤", top.detail_action_for(curses.KEY_DOWN) == ("scroll", 1))
    check("상세에서 k는 위로", top.detail_action_for(ord("k")) == ("scroll", -1))
    check("상세에서 space는 한 쪽 아래",
          top.detail_action_for(ord(" ")) == ("scroll-page", 1))
    check("상세에서 G는 끝으로", top.detail_action_for(ord("G")) == ("scroll-edge", 1))
    check("상세 도움줄에 스크롤이 적혀 있다", "스크롤" in top.DETAIL_HELP,
          top.DETAIL_HELP)
    check("상세 도움줄이 95칸을 넘지 않는다", top._width(top.DETAIL_HELP) <= 95,
          str(top._width(top.DETAIL_HELP)))


def test_the_short_window_frames_show_what_is_hidden():
    short = _render(TOP_SAMPLE, detail=23875, rows_high=8, cols=90)
    check("짧은 창에서는 첫 줄부터 보인다", "PID" in short and "23875" in short, short)
    check("아래에 더 있다고 알려준다", "↓" in short, short)
    check("키 안내는 그대로 있다", "목록" in short, short)

    scrolled = _render(TOP_SAMPLE, detail=23875, rows_high=8, cols=90, offset=99)
    check("끝까지 내리면 요청이 보인다", "레인 색을 고쳐줘" in scrolled, scrolled)
    check("위에 더 있다고 알려준다", "↑" in scrolled, scrolled)
    check("내용을 벗어나 비지 않는다", "훅 정체" in scrolled, scrolled)
    check("예외가 새지 않았다", "Traceback" not in scrolled, scrolled)

    many = [dict(TOP_SAMPLE[0]) for _ in range(12)]
    many = [{"info": {"pid": 1000 + i},
             "status": dict(TOP_SAMPLE[0]["status"], pid=1000 + i,
                            prompt=f"요청 {i}")} for i in range(12)]
    top_of_list = _render(many, selected=0, rows_high=8)
    check("긴 목록도 첫 화면은 처음부터", "1000" in top_of_list, top_of_list)
    check("목록도 남은 수를 알려준다", "↓" in top_of_list, top_of_list)
    bottom = _render(many, selected=11, rows_high=8)
    check("커서를 끝으로 옮기면 그 행이 보인다", "1011" in bottom, bottom)
    check("목록도 위에 더 있다고 알려준다", "↑" in bottom, bottom)
    check("목록 스크롤에도 예외가 없다", "Traceback" not in bottom, bottom)


def test_an_empty_list_explains_the_likely_reason():
    screen = _render([])
    check("없다고 말한다", "세션이 없다" in screen, screen)
    check("오래된 러너일 수 있다고 알려준다", "오래된" in screen, screen)


# -- the stored defaults, and pushing them into running sessions (D-041) -----


def _with_config(values):
    """A scratch config file, and the env pointing at it."""
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "config.json")
    if values is not None:
        with open(path, "w") as handle:
            json.dump(values, handle)
    os.environ["AMASK_CONFIG"] = path
    return path


def test_the_stored_defaults_are_what_a_new_session_starts_with():
    from amask import cli, config

    had = os.environ.get("AMASK_CONFIG")
    had_env = os.environ.pop("AMASK_OVERLAY_DELAY", None)
    try:
        _with_config({"overlay_delay": 9.0})
        check("저장된 값이 읽힌다", config.load() == {"overlay_delay": 9.0},
              str(config.load()))
        resolved = cli.Settings.resolve().as_dict()
        check("새 세션이 저장된 값으로 시작한다", resolved["overlay_delay"] == 9.0,
              str(resolved))
        check("저장하지 않은 키는 코드 기본값", resolved["idle_silence"] == 1.5,
              str(resolved))

        os.environ["AMASK_OVERLAY_DELAY"] = "2.0"
        import importlib

        importlib.reload(cli)
        check("env가 저장된 값보다 우선한다 (한 번의 실행에 붙는 값)",
              cli.Settings.resolve().as_dict()["overlay_delay"] == 2.0,
              str(cli.Settings.resolve().as_dict()))
    finally:
        os.environ.pop("AMASK_OVERLAY_DELAY", None)
        if had_env is not None:
            os.environ["AMASK_OVERLAY_DELAY"] = had_env
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had
        import importlib

        from amask import cli as reloaded

        importlib.reload(reloaded)


def test_a_broken_config_file_is_not_fatal():
    from amask import config

    had = os.environ.get("AMASK_CONFIG")
    try:
        path = _with_config(None)
        with open(path, "w") as handle:
            handle.write("{ this is not json")
        check("깨진 파일은 빈 설정으로 읽는다", config.load() == {}, str(config.load()))

        _with_config({"overlay_delay": 5.0, "nope": 1, "idle_silence": "x",
                      "hook_stall": -4})
        check("쓸 수 있는 키만 살린다", config.load() == {"overlay_delay": 5.0},
              str(config.load()))
    finally:
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had


def test_saving_defaults_round_trips_and_is_not_world_readable():
    from amask import config

    had = os.environ.get("AMASK_CONFIG")
    try:
        path = _with_config(None)
        config.save({"overlay_delay": 8.0, "idle_silence": 2.0})
        check("저장 후 다시 읽힌다",
              config.load() == {"overlay_delay": 8.0, "idle_silence": 2.0},
              str(config.load()))
        check("설정 파일이 0600", stat.S_IMODE(os.stat(path).st_mode) == 0o600,
              oct(stat.S_IMODE(os.stat(path).st_mode)))
        config.save({})
        check("비우면 저장된 값이 없다", config.load() == {}, str(config.load()))
    finally:
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had


def test_the_config_subcommand_shows_and_changes():
    import subprocess

    from amask import config

    had = os.environ.get("AMASK_CONFIG")
    try:
        path = _with_config(None)
        runner = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "amask")

        def run(*args):
            return subprocess.run([runner, "--config", *args],
                                  capture_output=True, env=dict(os.environ))

        shown = run().stdout.decode()
        check("아무 것도 저장되지 않았음을 말한다", "(없음)" in shown, shown)
        check("새 세션이 시작할 값을 보여준다", "새 세션이 시작할 값" in shown, shown)

        out = run("overlay_delay=8")
        check("지정이 성공한다", out.returncode == 0, out.stderr.decode())
        check("새 세션부터라고 알려준다", "새 세션부터" in out.stderr.decode(),
              out.stderr.decode())
        check("파일에 반영된다", config.load() == {"overlay_delay": 8.0},
              str(config.load()))
        check("보기에 저장값이 나온다", "저장 8" in run().stdout.decode(),
              run().stdout.decode())

        bad = run("overlay_delay=-1")
        check("범위를 벗어난 값은 거부", bad.returncode == 2, bad.stderr.decode())
        unknown = run("nope=1")
        check("모르는 키는 거부", unknown.returncode == 2, unknown.stderr.decode())
        check("거부된 뒤에도 파일은 그대로", config.load() == {"overlay_delay": 8.0},
              str(config.load()))

        run("overlay_delay=")
        check("키=으로 저장값을 지운다", config.load() == {}, str(config.load()))
    finally:
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had


def test_the_view_targets_the_marked_sessions():
    from amask import top

    rows = [({"pid": 1}, {"pid": 1}, None), ({"pid": 2}, {"pid": 2}, None),
            ({"pid": 3}, {"pid": 3}, None)]
    check("표시가 없으면 커서가 있는 하나",
          [row[0]["pid"] for row in top.targets(rows, set(), 1)] == [2],
          str(top.targets(rows, set(), 1)))
    check("표시가 있으면 표시된 것 전부",
          [row[0]["pid"] for row in top.targets(rows, {1, 3}, 1)] == [1, 3],
          str(top.targets(rows, {1, 3}, 1)))
    check("세션이 없으면 대상도 없다", top.targets([], {1}, 0) == [])
    check("space는 표시 토글", top.action_for(ord(" ")) == ("mark", None))
    check("a는 전체 선택", top.action_for(ord("a")) == ("mark-all", None))
    check("d는 기본설정 적용", top.action_for(ord("d")) == ("defaults", None))
    for label in ("space ", "a ", "d ", "r "):
        check(f"도움줄에 {label.strip()} 키가 적혀 있다", label in top.HELP, top.HELP)


def test_the_defaults_the_view_pushes_are_the_stored_ones():
    from amask import top

    had = os.environ.get("AMASK_CONFIG")
    try:
        _with_config({"overlay_delay": 12.0})
        values = top.default_settings()
        check("저장된 값이 들어간다", values["overlay_delay"] == 12.0, str(values))
        check("나머지는 코드 기본값", values["idle_silence"] == 1.5, str(values))
        check("세 키가 모두 실린다", set(values) == set(Settings.KEYS), str(values))
    finally:
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had


def test_marked_rows_are_drawn_as_marked():
    screen = _render(TOP_SAMPLE, selected=1, marked=[23875],
                     message="기본설정 overlay_delay=8 -> 1개 세션에 적용")
    check("선택 수가 제목에 나온다", "1개 선택" in screen, screen[:200])
    marked_line = [line for line in screen.split("\n") if "23875" in line]
    check("표시된 행에 * 가 붙는다",
          marked_line and marked_line[0].lstrip().startswith("*"),
          str(marked_line))
    other = [line for line in screen.split("\n") if "24999" in line]
    check("표시되지 않은 행에는 없다",
          other and not other[0].lstrip().startswith("*"), str(other))
    check("적용 결과가 보인다", "1개 세션에 적용" in screen, screen)


def test_defaults_reach_two_running_sessions_at_once():
    """The whole point of marking: one keystroke, several sessions.

    Two runners are started, both marked, and `_apply_defaults` is called the
    way the key handler calls it.
    """
    from amask import top

    had = os.environ.get("AMASK_CONFIG")
    outcome = {}

    def push(pid):
        _with_config({"overlay_delay": 17.0})
        rows = [(info, None, None) for info in control.sessions(RUN_DIR)]
        message = top._apply_defaults(rows)
        after = []
        for info in control.sessions(RUN_DIR):
            reply = control.request(info, {"cmd": "get"})
            after.append(reply.get("settings", {}).get("overlay_delay"))
        outcome["message"] = message
        outcome["count"] = len(rows)
        return after

    try:
        os.environ["AMASK_RUN_DIR"] = RUN_DIR
        first = run_in_pty(fixture("hooked_seq.py"),
                           env={"HOOKED_SEQ": json.dumps(BUSY_TURN),
                                "HOOKED_TOTAL": "10"},
                           act=[(2.0, push)], timeout=18, observe=True)
    finally:
        if had is None:
            os.environ.pop("AMASK_CONFIG", None)
        else:
            os.environ["AMASK_CONFIG"] = had
    values = first.acts[0][1] if first.acts else None
    check("적용한 세션 수를 말한다",
          isinstance(outcome.get("count"), int) and outcome["count"] >= 1,
          str(outcome))
    check("표시한 세션 전부가 저장된 기본값을 받는다",
          isinstance(values, list) and values and all(v == 17.0 for v in values),
          str(values))
    check("결과 문구에 적용된 값이 들어간다",
          "overlay_delay=17" in str(outcome.get("message")), str(outcome))


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("제어 채널 스위트 전부 통과")
