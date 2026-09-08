"""Phase 1: the runner must be an invisible wrapper.

These are the SC-005 / P-101 guarantees -- the agent's view of the world and
its exit code must be identical to running it unwrapped.

Since Phase 3 the overlay comes up at launch, so anything that types at the
agent has to spend one keystroke waking the screen first (D-008). Output-only
cases are unaffected: the log is flushed when the agent exits.
"""

import sys

from harness import run_in_pty

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def test_agent_sees_a_tty():
    out, code = run_in_pty(
        ["python3", "-c", "import sys; print('TTY', sys.stdout.isatty(), sys.stdin.isatty())"]
    )
    check("SC-005 자식이 stdout/stdin을 tty로 인식", b"TTY True True" in out, repr(out))
    check("SC-005 정상 종료코드 0", code == 0, str(code))


def test_exit_code_is_preserved():
    _, code = run_in_pty(["sh", "-c", "exit 42"])
    check("P-101 종료코드 그대로 전달 (42)", code == 42, str(code))


def test_output_is_passed_through():
    out, _ = run_in_pty(["sh", "-c", "printf 'line1\\nline2\\n'"])
    check("출력 패스스루", b"line1" in out and b"line2" in out, repr(out))


def test_ansi_colour_survives():
    out, _ = run_in_pty(["python3", "-c", r"print('\x1b[31mRED\x1b[0m')"])
    check("SC-005 ANSI 컬러 시퀀스 보존", b"\x1b[31mRED" in out, repr(out))


def test_stdin_reaches_the_agent():
    out, code = run_in_pty(
        ["python3", "-c", "print('GOT:' + input())"],
        feed=[(0.4, b"hello\r")],
    )
    check("대화형 입력 전달", b"GOT:hello" in out, repr(out))
    check("대화형 실행 종료코드 0", code == 0, str(code))


def test_window_size_is_propagated():
    out, _ = run_in_pty(
        ["python3", "-c", "import os; s=os.get_terminal_size(); print(f'SIZE {s.lines}x{s.columns}')"],
        rows=30,
        cols=100,
    )
    check("winsize 전파 (30x100)", b"SIZE 30x100" in out, repr(out))


def test_missing_command_reports_127():
    out, code = run_in_pty(["definitely-not-a-real-command-xyz"])
    check("존재하지 않는 명령 → 127", code == 127, str(code))
    check("존재하지 않는 명령 → 에러 메시지", b"amask" in out, repr(out))


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("Phase 1 전부 통과")
