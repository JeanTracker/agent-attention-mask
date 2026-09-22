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

The view owns nothing about a session. It polls `status`, draws what came
back, and sends the same five commands a person could type. Anything it
cannot reach is drawn as unreachable rather than retried, because a session
that just exited is the ordinary case, not an error. The one thing it does
write is the stored defaults file, and only when asked (`c`, then enter --
D-050).
"""

import curses
import os
import time
import unicodedata

from . import config
from . import control

REFRESH = 0.5  # seconds between polls; the panel's own clock is 20fps

# How much one keypress moves a timing value. Small enough to tune by feel,
# large enough that holding the key is not the only way to get anywhere.
STEP = {"overlay_delay": 1.0, "idle_silence": 0.1, "hook_stall": 10.0}

# One line, and it has to fit: measured at 95 cells so a 96-column terminal
# still shows the last key. Longer labels pushed `q quit` off the screen.
HELP = ("space mark  a all  enter detail  s skip  w wake  d defaults  "
        "c config  r poll  ? keys  q quit")

# The detail pane's own line. It has a whole screen, but the same 95-cell
# budget applies -- a 96-column terminal must still show the last key.
DETAIL_HELP = ("↑↓ scroll  esc/enter list  s skip  w wake  d defaults  "
               "c config  r poll  ? keys  q quit")

# The editor's own line (D-050). Six keys with the whole screen to say them
# in, so nothing here is abbreviated.
CONFIG_HELP = ("↑↓ field  +/- change  0 shipped  enter save  esc back  "
               "? keys  q quit")

# The key reference's own line (D-051). It is the screen people arrive at
# when the others were not clear enough, so it says only how to leave.
KEYS_HELP = "↑↓ scroll  esc/enter back  q quit"

# What the runner reports in its own words, so a Korean screen does not show
# the protocol's `busy`/`waiting` (D-044).
STATE_NAMES = {"busy": "working", "waiting": "waiting"}

# The row answers "which of these needs me?", so it carries what tells two
# sessions apart -- where it is running and what it was asked. The numbers a
# glance cannot use (how long it has been working, how much is hidden) moved
# to the detail pane (D-044).
COLUMNS = (" ", "PID", "Screen", "State", "Cover", "Folder", "Prompt")

# The padded columns, measured rather than fixed (D-046). State grows with
# the flags hung off it (`working·skip·idle?·no hooks`) and Folder with
# whatever the directory is called; a fixed width cut off exactly the rows
# that had something to say. The floor is what keeps a narrow window
# readable, the ceiling what stops one long name from eating the prompt.
MIN_WIDTH = (1, 5, 6, 8, 5, 8)
MAX_WIDTH = (1, 9, 8, 30, 8, 30)

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
    "mark-all", "defaults", "cmd", "tune", "refresh", "detail", "config",
    "keys" or None.
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
    if key in (ord("c"), ord("C")):
        return "config", None
    if key in (ord("?"), curses.KEY_F1):
        return "keys", None
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
    if kind in ("cmd", "tune", "refresh", "defaults", "config", "keys"):
        return kind, argument
    return None, None


def describe(status):
    """A status dict -> the cells of its row. Pure, for the same reason."""
    state = _state_name(status)
    marks = []
    if status.get("skip_turn"):
        marks.append("skip")
    if status.get("idle_suspected"):
        marks.append("idle?")
    if not status.get("hooks"):
        marks.append("no hooks")
    if marks:
        state += "·" + "·".join(marks)
    settings = status.get("settings") or {}
    return (
        str(status.get("pid", "?")),
        "covered" if status.get("covered") else "open",
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
    return "working" if status.get("busy") else "waiting"


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
    field("Command", " ".join(str(part) for part in argv) or "-")
    if status is None:
        out.append("")
        field("State", problem or "unreachable")
        out.append("")
        out.append("This session is not answering. If it has already exited "
                   "it drops off the list on the next refresh.")
        return out

    field("Session id", str(status.get("session_id") or "-"))
    field("Folder", str(status.get("cwd") or info.get("cwd") or "-"))
    field("Started", _started(info.get("started")))
    out.append("")

    field("Screen", "covered (the rain is up)" if status.get("covered")
          else "open (the agent's screen is visible)")
    field("State", _state_name(status, "unknown" if status.get("hooks")
                               else "no hooks -- judging by timing"))
    field("Working", f"{status.get('busy_seconds', 0.0):.1f}s"
          if status.get("busy") else "not working")
    notes = []
    if status.get("skip_turn"):
        notes.append("this turn will not be covered (q/skip)")
    if status.get("idle_suspected"):
        notes.append("hooks say working, but the output stopped")
    if not status.get("hooks"):
        notes.append("no hook channel")
    field("Notes", " / ".join(notes) or "none")
    if status.get("model"):
        field("Model", str(status["model"]))
    if status.get("tokens"):
        field("Tokens", str(status["tokens"]))
    field("Hidden", f"{_bytes(status.get('hidden_bytes') or 0)}"
          f" ({status.get('hidden_bytes') or 0}B, all of it comes back"
          f" when the cover lifts)")
    out.append("")

    settings = status.get("settings") or {}
    field("Cover delay", f"{settings.get('overlay_delay', 0):g}s")
    field("Idle silence", f"{settings.get('idle_silence', 0):g}s")
    field("Hook stall", f"{settings.get('hook_stall', 0):g}s")
    out.append("")
    field("Prompt", str(status.get("prompt") or "-"))
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
        return f"{stamp} ({ago:.0f}s ago)"
    if ago < 3600:
        return f"{stamp} ({ago / 60:.0f}m ago)"
    return f"{stamp} ({ago / 3600:.1f}h ago)"


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


def _shipped():
    """What the code itself says, with no file and no env -- the editor's
    baseline and what `0` puts a field back to."""
    from .cli import Settings

    return Settings.shipped()


# -- the key reference (D-051) ----------------------------------------------
#
# The three hint lines fit inside 95 cells, and that budget buys abbreviation:
# `d defaults` cannot say *which* defaults, and `[] idle` cannot say what idle
# means at all. This screen is where the sentence goes. The hint lines stay --
# they are what you read while working -- and this is what `?` opens when they
# were not enough.
#
# The table is the source: the screen is drawn from it, so a key that is added
# without a sentence here is a key this screen will not claim to explain.
KEY_HELP = (
    ("On the list", (
        ("↑ ↓  j k", "move between sessions"),
        ("space", "mark this session. Every command below then applies to all"
                  " the marked ones; with nothing marked it is the"
                  " highlighted row"),
        ("a", "mark them all, or clear the marks"),
        ("enter", "open this session on a screen of its own"),
        ("s", "skip: do not cover for the rest of this turn. The same thing"
              " pressing q does while the rain is up"),
        ("w", "wake: take the cover off now"),
        ("+  -", "this session's cover delay, by 1s a press"),
        ("[  ]", "this session's idle threshold, by 0.1s a press"),
        ("d", "push the stored defaults into this session"),
        ("c", "edit the stored defaults"),
        ("r", "poll now rather than waiting for the next half second"),
        ("q", "quit. The sessions themselves keep running -- this is only a"
              " window onto them"),
    )),
    ("On one session (enter)", (
        ("↑ ↓  j k", "scroll a line"),
        ("space  b", "scroll a page; PgDn and PgUp do the same"),
        ("g  G", "the top, the bottom"),
        ("esc  enter", "back to the list"),
        ("s w + - [ ] d c r",
         "the same commands, and here they reach the session you are looking"
         " at and no other -- marks do not apply"),
    )),
    ("On the defaults (c)", (
        ("↑ ↓", "pick one of the three values"),
        ("+  -", "move it. Nothing is written yet -- the title says"
                 " (unsaved)"),
        ("0", "forget the stored value, so the shipped default applies again"),
        ("enter", "write the file. New sessions only; d is what reaches the"
                  " ones already running"),
        ("esc", "leave without writing"),
    )),
    ("What the words mean", (
        ("cover", "the matrix rain over the agent's screen. Everything the"
                  " agent printed while covered comes back when it lifts"),
        ("cover delay", "how long the agent has to work without a break"
                        " before the rain goes up"),
        ("idle threshold", "with no hooks, how much silence is taken to mean"
                           " the agent stopped working"),
        ("hook stall", "with hooks, how long a working agent may stay silent"
                       " before its last hook is treated as lost"),
        ("stored defaults", "~/.amask/config.json -- what a NEW session starts"
                            " with. A running session keeps its own values"
                            " until you push these into it with d"),
    )),
)

# The label column of the reference. The widest label is `s w + - [ ] d c r`.
KEY_LABEL = 20


def keys_action_for(key):
    """One keypress on the key reference -> what to do. Pure.

    Returns (kind, argument), kind one of "quit", "back", "scroll",
    "scroll-page", "scroll-edge" or None.

    `?` closes it as well as opening it: the screen you opened with one key is
    the screen that key should put away.
    """
    if key in (ord("q"), ord("Q")):
        return "quit", None
    if key in _ENTER or key in (27, curses.KEY_BACKSPACE, 127, 8, ord("?")):
        return "back", None
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
    return None, None


def keys_lines(width=78):
    """The key reference, as drawn lines. Pure, like `detail_lines`.

    Wrapped on cells and not characters, and the sentence is indented under
    its key, so a narrow window folds the explanation rather than losing it
    (D-045).
    """
    body = max(8, width - KEY_LABEL - 2)
    out = []
    for title, rows in KEY_HELP:
        if out:
            out.append("")
        out.append(title)
        for label, sentence in rows:
            wrapped = _wrap(sentence, body) or [""]
            out.append("  " + _pad(label, KEY_LABEL) + wrapped[0])
            for extra in wrapped[1:]:
                out.append(" " * (KEY_LABEL + 2) + extra)
    return out


# -- the stored defaults, edited in place (D-050) ----------------------------
#
# `d` pushes the stored defaults into running sessions, but until now the only
# way to *change* them was `amask --config key=value` in another terminal --
# from the one screen that shows what the values are doing, they were read
# only. This is the same file `--config` writes, with the same validator, in
# front of the same three keys.

# The order the editor lists them in: the file's own order, so the screen and
# `amask --config` cannot disagree about which key is which.
CONFIG_FIELDS = config.KEYS

# How many lines the editor spends before the first field. The draw uses it to
# keep the cursor on screen in a short window (D-045).
CONFIG_HEADER = 4


def config_action_for(key):
    """One keypress inside the defaults editor -> what to do. Pure.

    Returns (kind, argument), kind one of "quit", "back", "field", "edit",
    "clear", "save", "keys" or None.

    `esc` leaves rather than quits, unlike the list: this screen is something
    you stepped into, and the key that gets you out of a pane is the same one
    the detail pane already uses (D-043). `q` still quits outright from
    anywhere, which is the promise the help line has made since D-040.

    Enter saves. Nothing else writes the file -- stepping a value is staged
    and the title says so -- because a screen that wrote on every keypress
    would leave a half-tuned number behind the moment you walked away.
    """
    if key in (ord("q"), ord("Q")):
        return "quit", None
    if key in (27, curses.KEY_BACKSPACE, 127, 8):
        return "back", None
    if key in _ENTER:
        return "save", None
    if key in (curses.KEY_DOWN, ord("j")):
        return "field", 1
    if key in (curses.KEY_UP, ord("k")):
        return "field", -1
    if key in (ord("+"), ord("="), curses.KEY_RIGHT, ord("l")):
        return "edit", 1
    if key in (ord("-"), ord("_"), curses.KEY_LEFT, ord("h")):
        return "edit", -1
    if key in (ord("0"), ord("x"), ord("X")):
        return "clear", None
    if key in (ord("?"), curses.KEY_F1):
        return "keys", None
    return None, None


def stage_value(staged, field, direction, shipped):
    """One `+`/`-` against the staged defaults. Pure; returns (staged, note).

    A key with nothing stored starts from the shipped value rather than from
    zero -- the first press should move the number that is on the screen.

    The result is rounded to the millisecond: `idle_silence` steps by 0.1, and
    binary floats otherwise turn 1.5 into 1.5000000000000002 and then write
    that into the file.
    """
    from .cli import Settings

    current = staged.get(field, shipped[field])
    value = round(max(0.0, min(Settings.LIMIT, current + direction * STEP[field])), 3)
    out = dict(staged)
    out[field] = value
    return out, f"{field} {value:g} -- not saved yet, enter saves"


def clear_value(staged, field, shipped):
    """`0`: forget the stored value, as `amask --config key=` does. Pure.

    Not "set it to the shipped number": a stored value that happens to equal
    the shipped one still pins it, and the point of this key is to stop
    pinning it at all.
    """
    out = dict(staged)
    out.pop(field, None)
    return out, (f"{field} back to the shipped {shipped[field]:g}"
                 " -- not saved yet, enter saves")


def save_defaults(staged):
    """Write the staged defaults. Returns (stored, message).

    `stored` is None when nothing was written, and then the message says why
    and the editor stays where it is -- `config.save` is allowed to fail out
    loud, and a screen that swallowed that would be claiming a change the
    next session will not see.

    Validated by the same `Settings` object the socket and `--config` use, so
    this screen cannot store a value a running session would refuse. Only the
    keys still staged are written, so clearing the last one leaves an empty
    file rather than a file full of the shipped values.
    """
    from .cli import Settings

    probe = Settings(**Settings.shipped())
    if staged:
        error = probe.update(staged)
        if error:
            return None, f"refused: {error}"
    try:
        written = config.save({key: probe.as_dict()[key] for key in staged})
    except OSError as exc:
        return None, f"could not save: {exc}"
    shown = " ".join(f"{k}={v:g}" for k, v in sorted(written.items()))
    return written, (f"saved {shown or '(nothing stored)'}"
                     " -- new sessions only; d pushes it into running ones")


def config_lines(shipped, stored, staged, cursor, width=78):
    """The editor's screen, as drawn lines. Pure, like `detail_lines`.

    Three columns per field: what a new session would get, where that number
    comes from, and what the code itself ships. The origin column is the one
    that answers the question people actually arrive with -- "is this 4
    seconds mine, or is it just what amask does?" -- and it is also where an
    unsaved change announces itself.
    """
    out = [
        "The defaults a NEW session starts with. This writes the file only --",
        "running sessions keep their own values, and `d` in the list pushes",
        f"these into the marked ones. File: {_short(config.path())}",
        "",
    ]
    assert len(out) == CONFIG_HEADER
    for index, field in enumerate(CONFIG_FIELDS):
        value = staged.get(field, shipped[field])
        if field in staged and staged[field] != stored.get(field):
            origin = "changed"
        elif field in staged:
            origin = "stored"
        elif field in stored:
            origin = "cleared"
        else:
            origin = "shipped"
        mark = ">" if index == cursor else " "
        out.append(f" {mark} {_pad(field, 14)}{_pad(f'{value:g}s', 10)}"
                   f"{_pad(origin, 9)}"
                   f"{_pad(f'shipped {shipped[field]:g}s', 14)}"
                   f"step {STEP[field]:g}")
    out.append("")
    if _unsaved(stored, staged):
        out.append("Unsaved. Enter writes it, esc leaves it alone.")
    else:
        out.append("Nothing to save.")
    out.append("")
    out.append("An env knob on a session's own command line still wins over")
    out.append("this file, and that session's own `set` wins over both.")
    return [_clip(line, width) for line in out]


def _short(path):
    """`~/.amask/config.json` rather than the whole home directory: the line
    is there to say which file, and the part that varies is the tail."""
    home = os.path.expanduser("~")
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _unsaved(stored, staged):
    """Whether the editor is holding a change the file does not have."""
    return staged != stored


def _poll():
    """Every session, with its status or the reason it could not be read."""
    rows = []
    for info in control.sessions():
        try:
            reply = control.request(info, {"cmd": "status"}, timeout=0.4)
        except (OSError, ValueError) as exc:
            rows.append((info, None, f"unreachable: {type(exc).__name__}"))
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
        return f"failed: {exc}"
    if reply.get("ok"):
        if "settings" in reply:
            values = reply["settings"]
            return ("applied: " +
                    " ".join(f"{k}={v:g}" for k, v in sorted(values.items())))
        return "sent"
    return f"refused: {reply.get('reason')}"


def _draw(screen, rows, selected, marked, message):
    screen.erase()
    height, width = screen.getmaxyx()
    start, end = visible_span(len(rows), selected, body_capacity(height))
    title = f" amask  {len(rows)} sessions"
    if marked:
        title += f"  ({len(marked)} marked)"
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
        _put(screen, 3, "  No amask session is running.")
        _put(screen, 4, "  If one is running and this is still empty, that runner")
        _put(screen, 5, "  predates the control socket -- restart it to pick it up.")

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
    state = "covered" if (status or {}).get("covered") else "open"
    if status is None:
        state = "unreachable"

    lines = detail_lines(info, status, problem, width=max(20, width - 3))
    capacity = body_capacity(height)
    offset = clamp_offset(len(lines), capacity, offset)
    shown = lines[offset:offset + capacity]

    title = f" amask  session {pid}  [{state}]"
    marker = more_marker(offset, offset + len(shown), len(lines))
    if marker:
        title += f"  {marker}"
    _put(screen, 0, _pad(title, width - 1), curses.A_REVERSE)

    for index, line in enumerate(shown):
        _put(screen, 2 + index, "  " + line)

    if message:
        _put(screen, height - 2, message)
    _put(screen, height - 1, _pad(DETAIL_HELP, width - 1),
         curses.A_REVERSE)
    screen.refresh()
    return offset


def _draw_keys(screen, message, offset=0):
    """The key reference, full screen and scrolled to `offset` (D-051).

    Returns the offset it actually drew at, clamped to the content -- the
    same contract as `_draw_detail`, and for the same reason: how many lines
    the sentences fold into depends on the window, which the loop does not
    know when it handles the key.
    """
    screen.erase()
    height, width = screen.getmaxyx()
    lines = keys_lines(width=max(20, width - 3))
    capacity = body_capacity(height)
    offset = clamp_offset(len(lines), capacity, offset)
    shown = lines[offset:offset + capacity]

    title = " amask  keys"
    marker = more_marker(offset, offset + len(shown), len(lines))
    if marker:
        title += f"  {marker}"
    _put(screen, 0, _pad(title, width - 1), curses.A_REVERSE)

    for index, line in enumerate(shown):
        _put(screen, 2 + index, "  " + line)

    if message:
        _put(screen, height - 2, message)
    _put(screen, height - 1, _pad(KEYS_HELP, width - 1), curses.A_REVERSE)
    screen.refresh()
    return offset


def _draw_config(screen, shipped, stored, staged, cursor, message):
    """The defaults editor, full screen (D-050).

    Scrolled like the other two screens rather than cut off (D-045), but the
    content is fixed, so instead of a scroll position it keeps the field the
    cursor is on in view -- there is no gesture here that moves the body
    without moving the cursor.
    """
    screen.erase()
    height, width = screen.getmaxyx()
    lines = config_lines(shipped, stored, staged, cursor,
                         width=max(20, width - 3))
    capacity = body_capacity(height)
    start, end = visible_span(len(lines), CONFIG_HEADER + cursor, capacity)

    title = " amask  default settings"
    if _unsaved(stored, staged):
        title += "  (unsaved)"
    marker = more_marker(start, end, len(lines))
    if marker:
        title += f"  {marker}"
    _put(screen, 0, _pad(title, width - 1), curses.A_REVERSE)

    for index, line in enumerate(lines[start:end]):
        attr = (curses.A_REVERSE if start + index == CONFIG_HEADER + cursor
                else curses.A_NORMAL)
        _put(screen, 2 + index, "  " + line, attr)

    if message:
        _put(screen, height - 2, message)
    _put(screen, height - 1, _pad(CONFIG_HELP, width - 1),
         curses.A_REVERSE)
    screen.refresh()


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
    editing = False  # whether the defaults editor is in front (D-050)
    helping = False  # whether the key reference is in front of all of it (D-051)
    help_offset = 0  # how far that reference is scrolled
    cursor = 0  # which of the three defaults it is on
    stored = {}  # the file as it was read; staged is the edit in progress
    staged = {}
    shipped = _shipped()
    message = ""
    rows = _poll()
    last = time.monotonic()
    while True:
        if selected >= len(rows):
            selected = max(0, len(rows) - 1)
        live = {info.get("pid") for info, _, _ in rows}
        marked &= live  # a session that exited is no longer selected
        if helping:
            # In front of whichever screen opened it: `esc` puts it away and
            # that screen is still underneath, unchanged.
            help_offset = _draw_keys(screen, message, help_offset)
        elif editing:
            _draw_config(screen, shipped, stored, staged, cursor, message)
        elif viewing is not None:
            row = _find(rows, viewing)
            if row is None:
                # The ordinary case, not an error: fall back to the list
                # rather than keep drawing a frame that stopped being true.
                message = f"session {viewing} ended"
                viewing = None
            else:
                offset = _draw_detail(screen, row, message, offset)
        if not helping and not editing and viewing is None:
            _draw(screen, rows, selected, marked, message)

        key = screen.getch()
        if key != -1:
            # Which keys mean what, and what a command would apply to,
            # depend on the mode; what the commands then *do* does not, so
            # the two modes part company only over their own keys.
            if helping:
                kind, argument = keys_action_for(key)
                page = max(1, body_capacity(screen.getmaxyx()[0]) - 1)
                if kind == "quit":
                    return
                if kind == "back":
                    helping, message = False, ""
                elif kind == "scroll":
                    help_offset = max(0, help_offset + argument)
                elif kind == "scroll-page":
                    help_offset = max(0, help_offset + argument * page)
                elif kind == "scroll-edge":
                    help_offset = 0 if argument < 0 else 10 ** 6  # clamped
                continue

            if editing:
                # The editor talks to a file, not to a session, so it shares
                # nothing with the other two beyond `q`. Its keys are handled
                # here and the dispatch below is skipped entirely.
                kind, argument = config_action_for(key)
                if kind == "quit":
                    return
                if kind == "back":
                    editing, message = False, ""
                elif kind == "field":
                    cursor = (cursor + argument) % len(CONFIG_FIELDS)
                elif kind == "edit":
                    staged, message = stage_value(
                        staged, CONFIG_FIELDS[cursor], argument, shipped)
                elif kind == "clear":
                    staged, message = clear_value(
                        staged, CONFIG_FIELDS[cursor], shipped)
                elif kind == "save":
                    written, message = save_defaults(staged)
                    if written is not None:
                        stored, staged = dict(written), dict(written)
                elif kind == "keys":
                    helping, help_offset, message = True, 0, ""
                continue

            if viewing is not None:
                kind, argument = detail_action_for(key)
                chosen = command_targets(rows, marked, selected, viewing)
            else:
                kind, argument = action_for(key)
                chosen = command_targets(rows, marked, selected, None)
            if kind == "quit":
                return
            if kind == "keys":
                helping, help_offset, message = True, 0, ""
                continue
            if kind == "config":
                # Read the file at the moment the screen opens, not at
                # startup: `amask --config` in another terminal, or another
                # `--top`, may have written it since.
                stored = config.load()
                staged, cursor = dict(stored), 0
                editing, message = True, ""
                continue

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
            replies.append("could not read its settings")
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
    return f"defaults {shown} -> " + _summarise(chosen, replies)


def _summarise(chosen, replies):
    if not chosen:
        return "no target"
    bad = [reply for reply in replies if not reply.startswith(("sent", "applied"))]
    if not bad:
        return f"applied to {len(chosen)} sessions"
    return f"{len(chosen) - len(bad)}/{len(chosen)} applied -- {bad[0]}"


def main():
    """Entry point for `amask --top`."""
    try:
        curses.wrapper(_loop)
    except KeyboardInterrupt:
        pass
    return 0
