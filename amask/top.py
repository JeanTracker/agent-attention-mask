"""`amask --top`: every session on one screen, and the keys to steer them.

`--ctl` answers one question per invocation, which is the wrong shape for
watching -- the thing you want to know (is it working? is the screen covered?
what did I ask it?) changes second by second. This is the same channel with a
screen in front of it (D-040).

Deliberately `curses` and not a window: a GUI needs a toolkit this repo is not
allowed to depend on (D-006, and `tkinter` is not even present on this
machine's interpreter), while `curses` is standard library. A separate native
app remains possible -- it would speak the same socket -- and that is exactly
why the protocol went in first.

The view owns nothing. It polls `status`, draws what came back, and sends the
same five commands a person could type. Anything it cannot reach is drawn as
unreachable rather than retried, because a session that just exited is the
ordinary case, not an error.
"""

import curses
import time
import unicodedata

from . import config
from . import control

REFRESH = 0.5  # seconds between polls; the panel's own clock is 20fps

# How much one keypress moves a timing value. Small enough to tune by feel,
# large enough that holding the key is not the only way to get anywhere.
STEP = {"overlay_delay": 1.0, "idle_silence": 0.1}

# One line, and it has to fit: measured at 95 cells so a 96-column terminal
# still shows the last key. Longer labels pushed `q 종료` off the screen.
HELP = ("space 선택  a 전체  enter 상세  d 기본  "
        "s 건너뜀  w 깨우기  +/- 덮기  [/] 유휴  r 갱신  q 종료")

# The detail pane's own line. It has a whole screen, but the same 95-cell
# budget applies -- a 96-column terminal must still show the last key.
DETAIL_HELP = ("↑↓ 스크롤  esc/enter 목록  s 건너뜀  w 깨우기  "
               "+/- 덮기  [/] 유휴  d 기본  r 갱신  q 종료")

# What the runner reports in its own words, so a Korean screen does not show
# the protocol's `busy`/`waiting` (D-044).
STATE_NAMES = {"busy": "작업", "waiting": "대기"}

# The row answers "which of these needs me?", so it carries what tells two
# sessions apart -- where it is running and what it was asked. The numbers a
# glance cannot use (how long it has been working, how much is hidden) moved
# to the detail pane (D-044).
COLUMNS = (" ", "PID", "화면", "상태", "덮기", "폴더", "프롬프트")

# The padded columns, measured rather than fixed (D-046). 상태 grows with the
# flags hung off it (`작업·건너뜀·유휴의심·훅없음` is 27 cells) and 폴더 with
# whatever the directory is called; a fixed width cut off exactly the rows
# that had something to say. The floor is what keeps a narrow window
# readable, the ceiling what stops one long name from eating the prompt.
MIN_WIDTH = (1, 5, 4, 8, 4, 8)
MAX_WIDTH = (1, 9, 4, 30, 8, 30)

# The prompt is why the row exists, so it is the one thing never squeezed
# out: the measured columns give cells back until this much is left for it.
PROMPT_FLOOR = 16


def column_widths(cells, total):
    """How wide each padded column is, given what is in it. Pure.

    `cells` is the rows as they will be drawn, `total` the window. The last
    column (the prompt) is not padded -- it takes whatever is left and is
    clipped by the window, so it has no width here.
    """
    count = len(MIN_WIDTH)
    widths = []
    for index in range(count):
        want = max([_width(COLUMNS[index])] +
                   [_width(str(row[index])) for row in cells])
        widths.append(max(MIN_WIDTH[index], min(MAX_WIDTH[index], want)))
    gaps = 2 * count  # two spaces after every padded column
    while sum(widths) + gaps + PROMPT_FLOOR > total:
        # Take from whichever column is furthest above its floor, so a long
        # folder name shrinks before a column that is already at the bone.
        widest = max(range(count), key=lambda i: widths[i] - MIN_WIDTH[i])
        if widths[widest] <= MIN_WIDTH[widest]:
            break
        widths[widest] -= 1
    return tuple(widths)


# Enter is three different numbers depending on where it came from: 10 under
# curses' own newline translation, 13 from a terminal that does not translate,
# KEY_ENTER from a numpad.
_ENTER = (curses.KEY_ENTER, 10, 13)


def action_for(key):
    """One keypress -> what to do. Pure, so the suite can check the table.

    Returns (kind, argument), where kind is one of "quit", "move", "mark",
    "mark-all", "defaults", "cmd", "tune", "refresh", "detail" or None.
    """
    if key in (ord("q"), ord("Q"), 27):
        return "quit", None
    if key in (curses.KEY_DOWN, ord("j")):
        return "move", 1
    if key in (curses.KEY_UP, ord("k")):
        return "move", -1
    if key in (ord("s"), ord("S")):
        return "cmd", "skip"
    if key in (ord("w"), ord("W")):
        return "cmd", "wake"
    if key in (ord("r"), ord("R")):
        return "refresh", None
    if key == ord(" "):
        return "mark", None
    if key in (ord("a"), ord("A")):
        return "mark-all", None
    if key in (ord("d"), ord("D")):
        return "defaults", None
    if key in (ord("+"), ord("=")):
        return "tune", ("overlay_delay", STEP["overlay_delay"])
    if key in (ord("-"), ord("_")):
        return "tune", ("overlay_delay", -STEP["overlay_delay"])
    if key == ord("]"):
        return "tune", ("idle_silence", STEP["idle_silence"])
    if key == ord("["):
        return "tune", ("idle_silence", -STEP["idle_silence"])
    if key in _ENTER:
        return "detail", None
    return None, None


def body_capacity(height):
    """How many body lines fit: the frame spends four rows on itself.

    Title, header, the reply line and the key hints. A window too short for
    even one body row gets zero, and the callers draw nothing rather than
    write over the hints.
    """
    return max(0, height - 4)


def visible_span(count, selected, capacity):
    """Which slice of the list is on screen, as (start, end). Pure.

    Keeps the cursor near the middle rather than scrolling by the least
    possible amount: the least-possible rule needs the previous position,
    and the view is redrawn from scratch twice a second from a list that
    reorders itself, so "where was it last time" is not a thing it knows.
    """
    if capacity <= 0 or count <= 0:
        return 0, 0
    if count <= capacity:
        return 0, count
    start = max(0, min(selected - capacity // 2, count - capacity))
    return start, start + capacity


def clamp_offset(total, capacity, offset):
    """A scroll position that cannot leave the content. Pure."""
    if capacity <= 0 or total <= capacity:
        return 0
    return max(0, min(int(offset), total - capacity))


def more_marker(start, end, total):
    """`↑3 ↓5`, or "" when it all fits -- said in the title bar.

    Without it a short window is indistinguishable from a short list, which
    is exactly the confusion a cut-off frame causes.
    """
    above, below = start, max(0, total - end)
    parts = []
    if above:
        parts.append(f"↑{above}")
    if below:
        parts.append(f"↓{below}")
    return " ".join(parts)


def detail_action_for(key):
    """The same table, read from inside the detail pane (D-043).

    Two differences, both deliberate. Enter, esc and backspace go back to the
    list -- Enter opened the pane, so Enter closing it is the gesture people
    try first. And the commands here apply to the session being read and to no
    other: marks are a list-level gesture, and pressing `s` while looking at
    one session's request must not skip two others. Third, the movement keys
    scroll this session's lines instead of moving between sessions -- there
    is only one session here, and on a short window there is more of it than
    fits (D-045).
    """
    if key in _ENTER or key in (curses.KEY_BACKSPACE, 127, 8, 27):
        return "list", None
    if key in (ord("q"), ord("Q")):
        return "quit", None
    if key in (curses.KEY_DOWN, ord("j")):
        return "scroll", 1
    if key in (curses.KEY_UP, ord("k")):
        return "scroll", -1
    if key in (curses.KEY_NPAGE, ord(" ")):
        return "scroll-page", 1
    if key in (curses.KEY_PPAGE, ord("b")):
        return "scroll-page", -1
    if key in (curses.KEY_HOME, ord("g")):
        return "scroll-edge", -1
    if key in (curses.KEY_END, ord("G")):
        return "scroll-edge", 1
    kind, argument = action_for(key)
    if kind in ("cmd", "tune", "refresh", "defaults"):
        return kind, argument
    return None, None


def describe(status):
    """A status dict -> the cells of its row. Pure, for the same reason."""
    state = _state_name(status)
    marks = []
    if status.get("skip_turn"):
        marks.append("건너뜀")
    if status.get("idle_suspected"):
        marks.append("유휴의심")
    if not status.get("hooks"):
        marks.append("훅없음")
    if marks:
        state += "·" + "·".join(marks)
    settings = status.get("settings") or {}
    return (
        str(status.get("pid", "?")),
        "덮임" if status.get("covered") else "열림",
        state,
        f"{settings.get('overlay_delay', 0):g}s",
        _folder(status.get("cwd")),
        " ".join(str(status.get("prompt") or "-").split()),
    )


def _state_name(status, unknown=None):
    """`busy`/`waiting` in the screen's own language (D-044).

    A value the table does not know is shown as it came, rather than as a
    blank: the hook protocol may grow a state before this view does.
    `unknown` is what to say when the runner reports no state at all --
    the row guesses from `busy`, the detail pane has room to explain.
    """
    raw = status.get("hook_state")
    if raw:
        return STATE_NAMES.get(raw, str(raw))
    if unknown is not None:
        return unknown
    return "작업" if status.get("busy") else "대기"


def _folder(cwd):
    """The last segment of the working directory -- the row has no room for
    the path, and the leading part of two sessions' paths is usually the same
    anyway. The detail pane still shows it in full."""
    text = str(cwd or "").rstrip("/")
    if not text:
        return "-"
    return text.rsplit("/", 1)[-1] or "/"


def detail_lines(info, status, problem=None, width=78):
    """Everything the runner reports about one session, as drawn lines.

    The row answers "which of these needs me?"; this answers "what is this
    one actually doing?" (D-043). So it carries the fields a row has no room
    for -- the agent's session id, where it is running, when it started, all
    three timing values, the prompt in full rather than folded to one line.

    Pure, and it takes the width rather than a window, so the suite can read
    the pane without a terminal.
    """
    label = 14  # the widest label plus a gap; the value gets the rest
    body = max(8, width - label)
    out = []

    def field(name, value):
        lines = _wrap(value, body) or [""]
        out.append(_pad(name, label) + lines[0])
        for extra in lines[1:]:
            out.append(" " * label + extra)

    pid = info.get("pid") or (status or {}).get("pid") or "?"
    field("PID", str(pid))
    argv = (status or {}).get("argv") or info.get("argv") or []
    field("명령", " ".join(str(part) for part in argv) or "-")
    if status is None:
        out.append("")
        field("상태", problem or "닿지 않음")
        out.append("")
        out.append("이 세션은 응답하지 않는다. 이미 끝났다면 다음 갱신에서 목록에서 빠진다.")
        return out

    field("세션 id", str(status.get("session_id") or "-"))
    field("폴더", str(status.get("cwd") or info.get("cwd") or "-"))
    field("시작", _started(info.get("started")))
    out.append("")

    field("화면", "덮임 (레인이 올라가 있다)" if status.get("covered")
           else "열림 (에이전트 화면이 보인다)")
    field("상태", _state_name(status, "알 수 없음" if status.get("hooks")
                              else "훅 없음 -- 시간 추정으로 판단 중"))
    field("작업 시간", f"{status.get('busy_seconds', 0.0):.1f}s"
          if status.get("busy") else "작업 중이 아니다")
    notes = []
    if status.get("skip_turn"):
        notes.append("이번 턴은 덮지 않는다 (q/skip)")
    if status.get("idle_suspected"):
        notes.append("훅은 작업 중이라는데 출력이 끊겼다")
    if not status.get("hooks"):
        notes.append("훅 채널이 없다")
    field("특이사항", " / ".join(notes) or "없음")
    if status.get("model"):
        field("모델", str(status["model"]))
    if status.get("tokens"):
        field("토큰", str(status["tokens"]))
    field("숨긴 출력", f"{_bytes(status.get('hidden_bytes') or 0)}"
          f" ({status.get('hidden_bytes') or 0}B, 덮개가 걷히면 그대로 나온다)")
    out.append("")

    settings = status.get("settings") or {}
    field("덮기 지연", f"{settings.get('overlay_delay', 0):g}s")
    field("유휴 임계", f"{settings.get('idle_silence', 0):g}s")
    field("훅 정체", f"{settings.get('hook_stall', 0):g}s")
    out.append("")
    field("프롬프트", str(status.get("prompt") or "-"))
    return out


def _started(when):
    """The wall-clock start, with how long ago that was."""
    if not when:
        return "-"
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    except (OSError, ValueError, TypeError):
        return "-"
    ago = max(0.0, time.time() - when)
    if ago < 60:
        return f"{stamp} ({ago:.0f}초 전)"
    if ago < 3600:
        return f"{stamp} ({ago / 60:.0f}분 전)"
    return f"{stamp} ({ago / 3600:.1f}시간 전)"


def _bytes(count):
    if count < 1024:
        return f"{count}B"
    if count < 1024 * 1024:
        return f"{count / 1024:.0f}K"
    return f"{count / 1024 / 1024:.1f}M"


def _width(text):
    """Display cells, not characters. Every label here is Korean."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _clip(text, cells):
    """Cut `text` to at most `cells` display columns."""
    out, used = [], 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > cells:
            break
        out.append(ch)
        used += step
    return "".join(out)


def _wrap(text, cells):
    """`text` as lines of at most `cells` display columns, breaking on words.

    Cells and not characters, same as `_clip`: a Korean request is half as
    many characters as it is columns, and wrapping on characters puts the
    line break in the wrong place on every request this tool will ever show.
    A word longer than the width is cut rather than allowed to overflow.
    """
    if cells < 1:
        return []
    out = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = word if not line else line + " " + word
            if _width(candidate) <= cells:
                line = candidate
                continue
            if line:
                out.append(line)
            while _width(word) > cells:
                head = _clip(word, cells)
                out.append(head)
                word = word[len(head):]
            line = word
        out.append(line)
    return out


def _pad(text, cells):
    return _clip(text, cells) + " " * max(0, cells - _width(_clip(text, cells)))


def _put(screen, line, text, attr=curses.A_NORMAL):
    """Write one row, clipped to the window.

    curses errors on a write that ends in the bottom-right cell, and that is
    exactly where the key hints go, so the last column is left alone and the
    error is caught anyway -- a resize between the measurement and the write
    would otherwise take the whole view down.
    """
    height, width = screen.getmaxyx()
    if not 0 <= line < height:
        return
    try:
        screen.addstr(line, 0, _clip(text, max(0, width - 1)), attr)
    except curses.error:
        pass


def targets(rows, marked, selected):
    """Which sessions a command applies to. Pure, and the rule is the UI.

    Marked sessions if there are any, otherwise the highlighted one. That way
    `space`-ing three of them and pressing `d` is one gesture, and someone who
    never touches `space` sees the single-session behaviour they had before.
    """
    if marked:
        return [row for row in rows if row[0].get("pid") in marked]
    if not rows:
        return []
    return [rows[min(selected, len(rows) - 1)]]


def command_targets(rows, marked, selected, viewing):
    """The same question with the mode folded in. Pure.

    The detail pane's rule is the other half of D-043: what the screen is
    showing is what the key reaches, marks or no marks. Keeping both halves
    in one function is what lets the loop run a single dispatch -- the modes
    differ over which keys exist, not over what a command then does.

    A `viewing` pid that is no longer in `rows` yields no target. The loop
    cannot reach that case (it drops back to the list first), but a caller
    that asks about a session which just exited gets an empty list rather
    than an exception.
    """
    if viewing is None:
        return targets(rows, marked, selected)
    row = _find(rows, viewing)
    return [row] if row is not None else []


def default_settings():
    """The stored defaults over the shipped ones -- what `d` pushes (D-041)."""
    from .cli import Settings

    return config.merge(Settings.shipped())


def _poll():
    """Every session, with its status or the reason it could not be read."""
    rows = []
    for info in control.sessions():
        try:
            reply = control.request(info, {"cmd": "status"}, timeout=0.4)
        except (OSError, ValueError) as exc:
            rows.append((info, None, f"닿지 않음: {type(exc).__name__}"))
            continue
        if reply.get("ok"):
            rows.append((info, reply["status"], None))
        else:
            rows.append((info, None, str(reply.get("reason"))))
    return rows


def _send(info, payload):
    try:
        reply = control.request(info, payload, timeout=0.6)
    except (OSError, ValueError) as exc:
        return f"실패: {exc}"
    if reply.get("ok"):
        if "settings" in reply:
            values = reply["settings"]
            return ("적용: " +
                    " ".join(f"{k}={v:g}" for k, v in sorted(values.items())))
        return "보냄"
    return f"거부: {reply.get('reason')}"


def _draw(screen, rows, selected, marked, message):
    screen.erase()
    height, width = screen.getmaxyx()
    start, end = visible_span(len(rows), selected, body_capacity(height))
    title = f" amask  세션 {len(rows)}개"
    if marked:
        title += f"  ({len(marked)}개 선택)"
    marker = more_marker(start, end, len(rows))
    if marker:
        title += f"  {marker}"
    _put(screen, 0, _pad(title, width - 1), curses.A_REVERSE)

    # Every visible row is laid out before any of it is drawn: the columns
    # are as wide as what is in them, and that cannot be known row by row.
    drawn = []
    for index, (info, status, problem) in enumerate(rows[start:end], start):
        mark = "*" if info.get("pid") in marked else " "
        if status is None:
            cells = (mark, str(info.get("pid", "?")), "-", problem or "?",
                     "-", _folder(info.get("cwd")),
                     " ".join(info.get("argv") or []))
        else:
            cells = (mark,) + describe(status)
        drawn.append((index, cells))
    widths = column_widths([cells for _, cells in drawn], width - 1)

    header = "  ".join(_pad(name, size) for name, size in zip(COLUMNS, widths))
    _put(screen, 1, header + "  " + COLUMNS[-1], curses.A_BOLD)

    if not rows:
        _put(screen, 3, "  실행 중인 amask 세션이 없다.")
        _put(screen, 4, "  amask가 돌고 있는데도 비어 있다면, 그 러너가 제어 소켓보다")
        _put(screen, 5, "  오래된 것이다 -- 다시 띄우면 잡힌다.")

    for index, cells in drawn:
        line = 2 + index - start
        text = "  ".join(_pad(cell, size) for cell, size in zip(cells, widths))
        text += "  " + cells[-1]
        attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
        _put(screen, line, _pad(text, width - 1), attr)

    if message:
        _put(screen, height - 2, message)
    _put(screen, height - 1, _pad(HELP, width - 1), curses.A_REVERSE)
    screen.refresh()


def _draw_detail(screen, row, message, offset=0):
    """One session, full screen, scrolled to `offset`.

    Returns the offset it actually drew at, clamped to the content: the loop
    stores that back, so holding `j` at the bottom does not wind up a number
    that then takes the same number of `k`s to undo.
    """
    screen.erase()
    height, width = screen.getmaxyx()
    info, status, problem = row
    pid = info.get("pid") or (status or {}).get("pid") or "?"
    state = "덮임" if (status or {}).get("covered") else "열림"
    if status is None:
        state = "닿지 않음"

    lines = detail_lines(info, status, problem, width=max(20, width - 3))
    capacity = body_capacity(height)
    offset = clamp_offset(len(lines), capacity, offset)
    shown = lines[offset:offset + capacity]

    title = f" amask  세션 {pid}  [{state}]"
    marker = more_marker(offset, offset + len(shown), len(lines))
    if marker:
        title += f"  {marker}"
    _put(screen, 0, _pad(title, width - 1), curses.A_REVERSE)

    for index, line in enumerate(shown):
        _put(screen, 2 + index, "  " + line)

    if message:
        _put(screen, height - 2, message)
    _put(screen, height - 1, _pad(DETAIL_HELP, width - 1), curses.A_REVERSE)
    screen.refresh()
    return offset


def _find(rows, pid):
    """The row for `pid`, or None once that session is gone."""
    for row in rows:
        if row[0].get("pid") == pid:
            return row
    return None


def _loop(screen):
    curses.curs_set(0)
    screen.nodelay(True)
    selected = 0
    marked = set()  # pids, not indices: the list reorders as sessions come and go
    viewing = None  # the pid whose detail pane is open, for the same reason
    offset = 0  # how far the detail pane is scrolled, in lines
    message = ""
    rows = _poll()
    last = time.monotonic()
    while True:
        if selected >= len(rows):
            selected = max(0, len(rows) - 1)
        live = {info.get("pid") for info, _, _ in rows}
        marked &= live  # a session that exited is no longer selected
        if viewing is not None:
            row = _find(rows, viewing)
            if row is None:
                # The ordinary case, not an error: fall back to the list
                # rather than keep drawing a frame that stopped being true.
                message = f"세션 {viewing}이(가) 끝났다"
                viewing = None
            else:
                offset = _draw_detail(screen, row, message, offset)
        if viewing is None:
            _draw(screen, rows, selected, marked, message)

        key = screen.getch()
        if key != -1:
            # Which keys mean what, and what a command would apply to,
            # depend on the mode; what the commands then *do* does not, so
            # the two modes part company only over their own keys.
            if viewing is not None:
                kind, argument = detail_action_for(key)
                chosen = command_targets(rows, marked, selected, viewing)
            else:
                kind, argument = action_for(key)
                chosen = command_targets(rows, marked, selected, None)
            if kind == "quit":
                return

            if viewing is not None:
                page = max(1, body_capacity(screen.getmaxyx()[0]) - 1)
                if kind == "list":
                    viewing, message = None, ""
                elif kind == "scroll":
                    offset = max(0, offset + argument)
                elif kind == "scroll-page":
                    offset = max(0, offset + argument * page)
                elif kind == "scroll-edge":
                    offset = 0 if argument < 0 else 10 ** 6  # clamped on draw
            else:
                if kind == "detail" and rows:
                    viewing, message, offset = rows[selected][0].get("pid"), "", 0
                elif kind == "move" and rows:
                    selected = (selected + argument) % len(rows)
                elif kind == "mark" and rows:
                    pid = rows[selected][0].get("pid")
                    marked.symmetric_difference_update({pid})
                    selected = (selected + 1) % len(rows)
                elif kind == "mark-all":
                    marked = set() if marked == live else set(live)

            if kind == "refresh":
                rows, last, message = _poll(), time.monotonic(), ""
            elif kind == "defaults":
                message = _apply_defaults(chosen)
                rows, last = _poll(), time.monotonic()
            elif kind in ("cmd", "tune") and chosen:
                message = _apply(chosen, kind, argument)
                rows, last = _poll(), time.monotonic()
            continue

        now = time.monotonic()
        if now - last >= REFRESH:
            rows, last = _poll(), now
        else:
            time.sleep(0.02)


def _apply(chosen, kind, argument):
    """Run one command against every chosen session; report it as one line."""
    replies = []
    for info, status, _ in chosen:
        if kind == "cmd":
            replies.append(_send(info, {"cmd": argument}))
            continue
        name, delta = argument
        current = ((status or {}).get("settings") or {}).get(name)
        if current is None:
            replies.append("설정을 읽지 못했다")
        else:
            replies.append(_send(info, {
                "cmd": "set",
                "settings": {name: max(0.0, current + delta)},
            }))
    return _summarise(chosen, replies)


def _apply_defaults(chosen):
    """Push the stored defaults into every chosen session (D-041).

    The file itself only governs new sessions, so this is the deliberate act
    that reaches the running ones -- which is why it is a keystroke and not
    something that happens on its own.
    """
    values = default_settings()
    replies = [_send(info, {"cmd": "set", "settings": values})
               for info, _, _ in chosen]
    shown = " ".join(f"{k}={v:g}" for k, v in sorted(values.items()))
    return f"기본설정 {shown} -> " + _summarise(chosen, replies)


def _summarise(chosen, replies):
    if not chosen:
        return "대상이 없다"
    bad = [reply for reply in replies if not reply.startswith(("보냄", "적용"))]
    if not bad:
        return f"{len(chosen)}개 세션에 적용"
    return f"{len(chosen) - len(bad)}/{len(chosen)}개 적용 -- {bad[0]}"


def main():
    """Entry point for `amask --top`."""
    try:
        curses.wrapper(_loop)
    except KeyboardInterrupt:
        pass
    return 0
