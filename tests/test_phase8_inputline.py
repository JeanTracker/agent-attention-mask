"""Reconstructing the typed line for the panel (D-019).

Reported: the panel showed "> >|iTerm2 3.5.13이 폴�더 파일 �중 ..." -- broken
characters and scrambled word order. Both came from treating input as a stream
of printable bytes: multi-byte characters split across reads, and editing keys
were dropped so text typed after moving the cursor landed at the end.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amask.inputline import InputLine

FAILURES = []


def check(name, got, expected):
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {got!r} != {expected!r}"))
    if not ok:
        FAILURES.append(name)


def run(chunks):
    line, out = InputLine(), None
    for chunk in chunks:
        result = line.feed(chunk)
        if result is not None:
            out = result
    return out


KOREAN = "iTerm2 3.5.13이 폴더 파일 중 중요한 파일 하나 분석해줘.".encode()


def test_multibyte_text():
    check("한글 한 번에 도착", run([KOREAN + b"\r"]), KOREAN.decode())


def test_multibyte_split_across_reads():
    """One keystroke can straddle two reads; a character must not be mangled."""
    check(
        "한글이 바이트 단위로 쪼개져 도착",
        run([bytes([b]) for b in KOREAN] + [b"\r"]),
        KOREAN.decode(),
    )


def test_insert_after_moving_the_cursor():
    check("커서 왼쪽 이동 후 삽입", run([b"abcd", b"\x1b[D\x1b[D", b"XY", b"\r"]), "abXYcd")


def test_backspace_removes_a_whole_character():
    check("백스페이스가 한 글자를 지움", run(["가나다".encode(), b"\x7f", b"\r"]), "가나")


def test_home_and_end():
    check("Home 후 앞에 삽입", run([b"world", b"\x1b[H", b"hi ", b"\r"]), "hi world")
    check("ctrl-a / ctrl-e", run([b"b", b"\x01", b"a", b"\x05", b"c", b"\r"]), "abc")


def test_delete_forward():
    check("delete 키", run([b"abcd", b"\x1b[D\x1b[D", b"\x1b[3~", b"\r"]), "abd")


def test_kill_line_and_word():
    check("ctrl-u 로 줄 취소", run([b"junk", b"\x15", b"clean", b"\r"]), "clean")
    check("ctrl-w 로 단어 삭제", run([b"keep this", b"\x17", b"that", b"\r"]), "keep that")


def test_terminal_replies_are_ignored():
    """A cursor-position reply once leaked in as literal "[?56;3;1R"."""
    check("커서 위치 응답 무시", run([b"\x1b[?56;3;1R", b"hello", b"\r"]), "hello")
    check("포커스 이벤트 무시", run([b"\x1b[O", b"hi", b"\x1b[I", b"\r"]), "hi")


def test_bracketed_paste_markers_are_not_text():
    check("bracketed paste 마커 제외", run([b"\x1b[200~pasted\x1b[201~", b"\r"]), "pasted")


def test_length_is_capped():
    long_line = b"x" * 500
    result = run([long_line, b"\r"])
    check("길이 상한", len(result), 200)


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("입력 재구성 스위트 전부 통과")
