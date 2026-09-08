"""What the overlay looks like, and what must not disturb it.

Covers the rain's legibility rules (D-015), the status panel as specified by
Claude Design (D-027) and the terminal-event handling that kept cancelling the
screensaver by mistake (D-017).
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import fixture, is_rain, run_in_pty
from amask import hud as hud_mod
from amask.hud import Hud

ALT_EXIT = b"\x1b[?1049l"

# A cursor move, a truecolor foreground, and exactly one glyph.
RAIN_RE = re.compile(
    rb"\x1b\[(\d+);(\d+)H\x1b\[0;(?:1;)?38;2;\d+;\d+;\d+m(.)(?=\x1b|$)", re.S
)

FAILURES = []


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def overlay_bytes(res):
    """Everything painted while the screen was covered."""
    start = res.data.find(b"\x1b[?1049h")
    end = res.data.rfind(ALT_EXIT)
    if start < 0:
        return b""
    return res.data[start:end if end > start else len(res.data)]


def panel(prompt="", model="", tokens="", cwd="~/edu/iterm-matrix", cols=80, rows=24, **kw):
    hud = Hud("cmd", cwd)
    hud.prompt, hud.model, hud.tokens = prompt, model, tokens
    kw.setdefault("busy", True)
    kw.setdefault("since", time.monotonic() - 14)
    kw.setdefault("hidden_bytes", 0)
    kw.setdefault("frame", 3)
    return hud, hud.layout(rows, cols, **kw)


def text_of(line):
    segments, right = line
    return "".join(t for _, _, t in segments) + "".join(t for _, _, t in right)


def row_starting(panel_obj, label):
    for line in panel_obj.lines:
        if line[0] and line[0][0][2].startswith(label):
            return line
    return None


# -- rain legibility (D-015, D-027) -----------------------------------------


def test_rain_uses_only_keyboard_characters():
    """Half-width katakana rendered at inconsistent widths and smeared."""
    res = run_in_pty(fixture("chatty.py", 4), timeout=20)
    glyphs = [m.group(3) for m in RAIN_RE.finditer(overlay_bytes(res))]
    non_ascii = [g for g in glyphs if not (0x20 <= g[0] <= 0x7E)]
    check("레인 글리프가 ASCII 자판 문자뿐", not non_ascii, f"{len(non_ascii)}자 비ASCII")
    check("글리프가 실제로 그려졌다 (조건 성립)", len(glyphs) > 50, f"{len(glyphs)}자")


def test_border_like_glyphs_are_excluded():
    """`|`, `_` and `-` read as a border beside the panel's edge (D-027)."""
    from amask.rain import GLYPHS

    for ch in "|_-":
        check(f"레인 글리프에서 {ch!r} 제외", ch not in GLYPHS, "포함됨")


def test_rain_leaves_a_blank_column_between_drops():
    res = run_in_pty(fixture("chatty.py", 4), timeout=20)
    columns = {int(m.group(2)) for m in RAIN_RE.finditer(overlay_bytes(res))}
    parities = {c % 2 for c in columns}
    check("레인이 한 칸 걸러 떨어짐", len(columns) > 5 and len(parities) == 1, f"열 {sorted(columns)[:8]}")


# -- panel geometry (D-027) --------------------------------------------------


def test_geometry_follows_the_spec():
    for cols, rows, want_w, want_x in ((80, 24, 66, 7), (120, 40, 66, 27), (40, 18, 34, 3)):
        _, p = panel(prompt="테스트", cols=cols, rows=rows)
        check(
            f"{cols}x{rows} 폭·위치",
            (p.width, p.x0) == (want_w, want_x),
            f"W={p.width} x0={p.x0}, 기대 {want_w}/{want_x}",
        )
        check(f"{cols}x{rows} 세로 중앙", p.y0 == max(0, (rows - p.height) // 2), str(p.y0))


def test_compact_drops_the_blank_rows():
    _, full = panel(prompt="테스트", cols=80, rows=24)
    _, tight = panel(prompt="테스트", cols=80, rows=18)
    check("24행은 full", not full.compact and full.height >= 8, str(full.height))
    check("18행은 compact", tight.compact, "compact 아님")
    check("compact가 더 짧음", tight.height < full.height, f"{tight.height} vs {full.height}")
    check("compact에 빈 행 없음", all(text_of(l).strip() for l in tight.lines), "빈 행 있음")


def test_a_narrow_terminal_gets_no_panel():
    _, p = panel(prompt="테스트", cols=30, rows=24)
    check("34칸 미만이면 패널 없음", p is None, "그려짐")


# -- panel content (D-027) ---------------------------------------------------


def test_missing_values_remove_their_rows():
    """A dash advertises the scrape failing; a missing row does not."""
    _, bare = panel(prompt="테스트")
    _, full = panel(prompt="테스트", model="Opus 5", tokens="1,204", hidden_bytes=12800)
    labels = " ".join(text_of(l) for l in bare.lines)
    check("값 없으면 MODEL 행 없음", "MODEL" not in labels, labels)
    check("숨긴 출력 없으면 BUFFERED 행 없음", "BUFFERED" not in labels, labels)
    check("DIR은 항상 있음", "DIR" in labels, labels)
    check("값이 있으면 행이 생김", full.height > bare.height, f"{full.height} vs {bare.height}")


def test_status_and_elapsed_share_a_row():
    _, p = panel(prompt="테스트")
    row = next(l for l in p.lines if "working" in text_of(l))
    check("status 행에 ELAPSED 동거", "14s" in text_of(row), text_of(row))
    check("ELAPSED는 우측 정렬 필드", bool(row[1]), "right 없음")


def test_the_spinner_is_dots_not_a_glyph():
    frames = set()
    for f in range(4):
        _, p = panel(prompt="t", frame=f)
        # Left segments only: the right field holds ELAPSED on the same row.
        row = next(l for l in p.lines if "work" in text_of(l))
        frames.add("".join(t for _, _, t in row[0]))
    check("스피너가 점 4프레임", frames == set(hud_mod.SPINNER), str(sorted(frames)))
    check("회전 글리프 없음", not any(c in "|/\\" for c in "".join(frames)), str(frames))


def test_waiting_is_grey_and_not_bold():
    _, p = panel(prompt="테스트", busy=False)
    row = next(l for l in p.lines if "waiting" in text_of(l))
    rgb, bold, _ = row[0][0]
    check("대기는 회색", rgb == hud_mod.STATUS_IDLE, str(rgb))
    check("대기는 bold 아님", not bold, "bold")


def test_dir_keeps_its_tail_and_others_their_head():
    long_dir = "~/work/clients/northwind/platform/services/ingest-gateway/src/handlers"
    _, p = panel(
        prompt="t",
        cwd=long_dir,
        model="A very long model name that will never fit inside the value column",
    )
    dir_row = text_of(row_starting(p, "DIR"))
    model_row = text_of(row_starting(p, "MODEL"))
    check("DIR은 꼬리 유지", dir_row.endswith("handlers") and "..." in dir_row, dir_row)
    check("MODEL은 머리 유지", "A very long model" in model_row and model_row.endswith("..."), model_row)


def test_no_ambiguous_width_glyphs_anywhere():
    """One double-width surprise shifts every alignment in the panel."""
    banned = "…←↑→↓—●─│╭╮╯╰·"
    hud = Hud("cmd", "~/edu/iterm-matrix")
    hud.prompt, hud.model, hud.tokens = "테스트", "Opus 5", "↓1,204"
    hud.mark_cover()
    hud.tokens = "↓2,400"
    p = hud.layout(24, 80, busy=True, since=time.monotonic() - 5, hidden_bytes=12800, frame=1)
    found = [ch for ch in "".join(text_of(l) for l in p.lines) if ch in banned]
    check("ambiguous-width 글리프 없음", not found, str(found))


def test_buffered_reports_bytes_and_the_tokens_grown_since_covering():
    """The parenthetical has to describe the same hidden stretch (D-029).

    The scraped figure counts the whole turn, so printing it raw beside a byte
    count implied a conversion between two unrelated quantities.
    """
    hud = Hud("cmd", "~/edu/iterm-matrix")
    hud.prompt, hud.tokens = "t", "↓ 373"
    hud.mark_cover()
    hud.tokens = "↓ 1,204"
    p = hud.layout(24, 80, busy=True, since=time.monotonic() - 5, hidden_bytes=61297, frame=1)
    row = text_of(row_starting(p, "BUFFERED"))
    check("바이트와 토큰 증가분을 함께 표기", "59.9KB (831 tokens)" in row, row)
    check("화살표는 제거됨", "↓" not in row, row)


def test_buffered_omits_tokens_when_they_cannot_be_trusted():
    for name, before, after in (
        ("덮을 때 값 미상", "", "1,204"),
        ("증가 없음", "373", "373"),
    ):
        hud = Hud("cmd", "~/edu/iterm-matrix")
        hud.prompt, hud.tokens = "t", before
        hud.mark_cover()
        hud.tokens = after
        p = hud.layout(24, 80, busy=True, since=time.monotonic() - 5, hidden_bytes=61297, frame=1)
        row = text_of(row_starting(p, "BUFFERED"))
        check(f"{name}: 괄호 없이 크기만", row.strip().endswith("59.9KB"), row)


def test_buffered_survives_the_counter_restarting():
    """A fresh turn resets the agent's own counter partway through."""
    hud = Hud("cmd", "~/edu/iterm-matrix")
    hud.prompt, hud.tokens = "t", "900"
    hud.mark_cover()
    hud.tokens = "120"
    p = hud.layout(24, 80, busy=True, since=time.monotonic() - 5, hidden_bytes=61297, frame=1)
    row = text_of(row_starting(p, "BUFFERED"))
    check("카운터 리셋 시 음수 대신 현재값", "(120 tokens)" in row, row)


def test_token_parsing():
    from amask.hud import token_count

    for text, want in (("373", 373), ("1,204", 1204), ("1.0k", 1000), ("12.5k", 12500), ("", None)):
        check(f"토큰 파싱 {text!r}", token_count(text) == want, str(token_count(text)))


def test_the_panel_paints_no_background():
    hud = Hud("cmd", "~/edu/iterm-matrix")
    hud.prompt = "테스트"
    painted = hud.render(24, 80, busy=True, since=time.monotonic() - 3, hidden_bytes=0, frame=1)
    check("배경색을 설정하지 않음", b"48;2;" not in painted and b"48;5;" not in painted, "배경 있음")
    check("무언가는 그렸다 (조건 성립)", b"press any key to return" in painted)


# -- the rain around the panel (D-027) ---------------------------------------


def test_the_rain_is_kept_out_of_the_panel():
    res = run_in_pty(fixture("chatty.py", 5), feed=[(0.4, b"do the thing\r")], timeout=20)
    painted = overlay_bytes(res)
    spans = _clear_spans_from(painted)
    check("패널 영역을 파악함 (조건 성립)", len(spans) >= 6, f"{len(spans)}행")
    intruders = [
        (int(m.group(1)) - 1, int(m.group(2)) - 1)
        for m in RAIN_RE.finditer(painted)
        if (int(m.group(1)) - 1) in spans
        and spans[int(m.group(1)) - 1][0] <= int(m.group(2)) - 1 <= spans[int(m.group(1)) - 1][1]
    ]
    check("패널 안에 레인 없음", not intruders, f"{len(intruders)}회 침범")


def test_the_halo_is_tail_coloured_only():
    """A bright head two columns from the text flickers beside it."""
    from amask.rain import Rain, TAIL

    hud = Hud("cmd", "~/edu/iterm-matrix")
    hud.prompt = "테스트"
    hud.render(24, 80, busy=True, since=time.monotonic() - 3, hidden_bytes=0, frame=1)
    halo, clear = hud.halo_spans(24, 80), hud.clear_spans(24, 80)
    check("halo 영역이 생성됨", bool(halo), "없음")

    rain = Rain(24, 80)
    rain.set_hole(clear)
    rain.set_halo(halo)
    painted = b"".join(rain.frame() for _ in range(120))
    wrong, inside = [], 0
    for m in re.finditer(rb"\x1b\[(\d+);(\d+)H\x1b\[0;(?:1;)?38;2;(\d+);(\d+);(\d+)m", painted):
        row, col = int(m.group(1)) - 1, int(m.group(2)) - 1
        colour = (int(m.group(3)), int(m.group(4)), int(m.group(5)))
        if any(a <= col <= b for a, b in halo.get(row, ())):
            inside += 1
            if colour != TAIL:
                wrong.append((row, col, colour))
    check("halo에 레인이 실제로 들어옴 (조건 성립)", inside > 0, "0셀")
    check("halo 안은 전부 TAIL 색", not wrong, f"{len(wrong)}셀, 예: {wrong[:2]}")


def test_the_rain_slows_down_while_waiting():
    from amask.rain import Rain

    rain = Rain(24, 80)
    rain.set_speed_scale(0.5)
    check("속도 배율 적용", rain.speed_scale == 0.5, str(rain.speed_scale))


def _clear_spans_from(painted):
    """Panel rows and extents, from the blanking runs the panel emits."""
    spans = {}
    for m in re.finditer(rb"\x1b\[(\d+);(\d+)H\x1b\[0m( +)", painted):
        row, left, width = int(m.group(1)) - 1, int(m.group(2)) - 1, len(m.group(3))
        spans[row] = (left, left + width - 1)
    return spans


# -- terminal events (D-017) ------------------------------------------------


def test_focus_events_do_not_cancel_the_screensaver():
    """Clicking another iTerm2 tab sent these and cancelled the matrix."""
    res = run_in_pty(
        fixture("chatty.py", 6), feed=[(2.0, b"\x1b[O"), (2.5, b"\x1b[I")], timeout=20
    )
    woke = next((t for t, chunk in res.timeline if ALT_EXIT in chunk), None)
    check("포커스 아웃/인으로 오버레이가 꺼지지 않음", woke is None or woke > 3.0, f"woke={woke}")
    still = [t for t, chunk in res.timeline if is_rain(chunk) and t > 2.6]
    check("포커스 이벤트 후에도 레인 유지", bool(still), "레인 중단됨")


def test_a_real_key_still_wakes_after_focus_events():
    res = run_in_pty(
        fixture("chatty.py", 6),
        feed=[(2.0, b"\x1b[O"), (2.3, b"\x1b[I"), (3.0, b" ")],
        timeout=20,
    )
    woke = next((t for t, chunk in res.timeline if ALT_EXIT in chunk), None)
    check("실제 키 입력은 여전히 깨움", woke is not None and 3.0 <= woke < 3.3, f"woke={woke}")


def test_agent_terminal_modes_are_restored_on_wake():
    res = run_in_pty(fixture("modes.py"), feed=[(2.5, b" ")], timeout=10, observe=True)
    woke = res.feeds[0][0]
    window = b"".join(c for t, c in res.timeline if woke <= t <= woke + 0.5)
    for mode in (b"1002", b"1006", b"1004"):
        check(
            f"복귀 후 에이전트 모드 {mode.decode()} 복원",
            b"\x1b[?" + mode + b"h" in window,
            "복원 안 됨",
        )


def test_token_count_tracks_the_latest_figure():
    """Output kept growing while TOKENS sat still: `search` took the oldest."""
    res = run_in_pty(
        fixture("streamer.py"), env={"STREAM_SECONDS": "6"}, timeout=10, observe=True
    )
    painted = overlay_bytes(res)
    shown = [int(m.group(1).replace(b",", b"")) for m in re.finditer(rb"\(([\d,]+) tokens\)", painted)]
    check("패널에 토큰 증가분이 표시됨", bool(shown), "표시 없음")
    check("토큰 증가분이 커짐", len(set(shown)) > 1 and shown == sorted(shown), f"{sorted(set(shown))[:6]}")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)}건 실패: {', '.join(FAILURES)}")
        sys.exit(1)
    print("표현 스위트 전부 통과")
