"""The status panel drawn over the rain.

Implements the Claude Design spec `Matrix HUD.dc.html` / `HUD-SPEC.md` (D-027),
which replaces the shaded-band panel that came before it.

The panel paints **no background at all**. On a medium with no alpha that is the
only way not to produce a surface: text cells set a foreground and leave the
terminal's own ground behind them, and the panel's empty cells merely keep the
rain out. What marks the edge instead is the rain itself -- it steps around the
panel, and in a two-column margin around it every drop is forced to the darkest
tail colour, so the boundary reads as the rain fading rather than as a box.

Two consequences worth stating, both from the spec:

  - Alignment has to carry the block on its own. The width is fixed for a given
    terminal and the value column sits at a constant offset, so the rows read as
    one panel even with nothing drawn behind them.
  - A value that could not be scraped removes its row rather than showing a
    placeholder. Scraping failure is the normal case, and a dash advertises it;
    a missing row just makes the panel shorter.
"""

import time

# Every foreground is one of the rain's own four stops, except the idle grey --
# the single colour in the panel that is not part of the rain, which is what
# makes "the agent has stopped" legible at a glance while the rain still moves.
PROMPT = (200, 255, 210)       # bold; same as the rain's head
MARK = (110, 240, 140)         # bold; the "> " before the prompt
STATUS_WORK = (110, 240, 140)  # bold
STATUS_IDLE = (168, 158, 144)  # not bold, and deliberately not green
VALUE = (160, 222, 178)
DIM = (70, 140, 92)            # labels, and the hint

RESET = "\x1b[0m"

# Panel geometry, all from the spec.
MIN_COLS = 34      # below this there is no room for a panel at all
MIN_WIDTH = 34     # hint (23 columns) plus a 20-column value field

# The wake hint, and the skip hint beside it (D-036). All ASCII, and all
# narrower than the 23 columns the wake hint already claimed above -- panel
# width comes from the terminal, not from the content, so a wider hint would
# overflow rather than widen. The two skip forms are deliberately the same
# length: the emphasis changes colour and weight, not geometry, so the panel
# does not shift when the agent falls quiet.
HINT_WAKE = "press any key to return"    # 23 columns
HINT_SKIP = "q to skip this turn"        # 19
HINT_SKIP_LOUD = "idle? q to skip now"   # 19
MAX_WIDTH = 66     # a comfortable maximum line length for the prompt
SIDE_MARGIN = 14   # 7 columns of live rain either side, so it reads as overlay
PAD = 2            # left and right padding inside the panel
LABEL_WIDTH = 10   # longest label is 7 columns; 8 plus a 2-column gap
COMPACT_ROWS = 20  # below this, drop the blank rows
HALO = 2           # columns of forced-dark rain around the panel

# 200ms per frame, 800ms per cycle. The text is the state, so there is no
# spinning glyph to catch the eye, and at most three cells change per frame.
SPINNER = ("working", "working.", "working..", "working...")

# Deliberately absent: U+2026, arrows, em dash, box drawing. All are East Asian
# *ambiguous* width, so terminals set to double-width ambiguity render them two
# columns wide and every alignment in the panel shifts by one.
ELLIPSIS = "..."


def sgr(rgb, bold=False):
    """Foreground only -- the panel never sets a background."""
    return f"\x1b[0;{'1;' if bold else ''}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


# -- widths -----------------------------------------------------------------


def char_width(ch):
    code = ord(ch)
    wide = (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )
    return 2 if wide else 1


def display_width(text):
    return sum(char_width(ch) for ch in text)


def cut_head(text, limit):
    """Keep the beginning, for values recognised by their first words."""
    if display_width(text) <= limit:
        return text
    out, width = "", 0
    for ch in text:
        step = char_width(ch)
        if width + step > limit - len(ELLIPSIS):
            break
        out += ch
        width += step
    return out + ELLIPSIS


def cut_tail(text, limit):
    """Keep the end. A path's distinguishing part is its last few segments."""
    if display_width(text) <= limit:
        return text
    out, width = "", 0
    for ch in reversed(text):
        step = char_width(ch)
        if width + step > limit - len(ELLIPSIS):
            break
        out = ch + out
        width += step
    return ELLIPSIS + out


def _units(text):
    """Split into wrap units: ASCII words, but Korean and CJK one character.

    Korean rarely spaces between phrases, so wrapping it on word boundaries
    leaves half-empty lines.
    """
    units, current = [], ""
    for ch in text:
        if char_width(ch) == 2:
            if current:
                units.append(current)
                current = ""
            units.append(ch)
        elif ch == " ":
            units.append(current + ch)
            current = ""
        else:
            current += ch
    if current:
        units.append(current)
    return units


def wrap(text, limit, max_lines):
    lines, current = [], ""
    for unit in _units(text):
        if current and display_width(current) + display_width(unit.rstrip()) > limit:
            lines.append(current.rstrip())
            current = ""
        if display_width(unit) > limit:
            piece = ""
            for ch in unit:
                if display_width(piece) + char_width(ch) > limit:
                    lines.append(piece)
                    piece = ""
                piece += ch
            current = piece
        else:
            current += unit
    if current.rstrip():
        lines.append(current.rstrip())
    if not lines:
        return [""]
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        rest = " ".join(lines[max_lines:])
        kept[-1] = cut_head(f"{kept[-1]} {rest}", limit)
        return kept
    return lines


def format_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def token_count(text):
    """Parse the scraped token figure: "1,204", "1.0k", "373" -> int, or None."""
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in ".,").replace(",", "")
    if not cleaned:
        return None
    scale = 1
    if cleaned[-1] in "kK":
        scale, cleaned = 1000, cleaned[:-1]
    elif cleaned[-1] in "mM":
        scale, cleaned = 1000000, cleaned[:-1]
    try:
        return int(float(cleaned) * scale)
    except ValueError:
        return None


def format_size(count):
    if count < 1024:
        return f"{count}B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f}KB"
    return f"{count / (1024 * 1024):.1f}MB"


class Panel:
    """A laid-out panel: where it sits, and what each of its rows contains."""

    def __init__(self, width, x0, y0, lines, compact):
        self.width = width
        self.x0 = x0
        self.y0 = y0
        self.lines = lines
        self.compact = compact

    @property
    def height(self):
        return len(self.lines)

    @property
    def inner(self):
        return self.width - 2 * PAD

    def clear_spans(self, rows, cols):
        """{row: (left, right)} the rain must not paint at all."""
        out = {}
        for offset in range(self.height):
            row = self.y0 + offset
            if 0 <= row < rows:
                out[row] = (max(0, self.x0), min(cols - 1, self.x0 + self.width - 1))
        return out

    def halo_spans(self, rows, cols):
        """{row: [(left, right), ...]} where the rain is forced to its tail colour.

        Without this the boundary flickers: with no background behind the text,
        a bright head landing two columns away is far more distracting than the
        text is readable.
        """
        out = {}
        clear = self.clear_spans(rows, cols)
        left = max(0, self.x0 - HALO)
        right = min(cols - 1, self.x0 + self.width - 1 + HALO)
        for row in range(self.y0 - 1, self.y0 + self.height + 1):
            if not 0 <= row < rows:
                continue
            band = clear.get(row)
            if band is None:
                out[row] = [(left, right)]
            else:
                out[row] = [(left, band[0] - 1), (band[1] + 1, right)]
        return out


class Hud:
    def __init__(self, command, cwd):
        self.command = command
        self.cwd = cwd
        self.prompt = ""
        self.model = ""
        self.tokens = ""
        # The scraped token figure counts the whole turn, so on its own it says
        # nothing about the stretch the overlay is hiding. Remembering where it
        # stood when the screen was covered turns it into one that does (D-029).
        self.tokens_at_cover = None
        self._last = None
        self._panel = None
        self._cleared = None

    # -- layout ------------------------------------------------------------

    def layout(self, rows, cols, busy=True, since=None, hidden_bytes=0, frame=0,
               idle_hint=False):
        """Build the panel for this terminal, or None if it will not fit."""
        if cols < MIN_COLS:
            return None
        width = max(MIN_WIDTH, min(MAX_WIDTH, cols - SIDE_MARGIN))
        compact = rows < COMPACT_ROWS
        inner = width - 2 * PAD
        value_width = inner - LABEL_WIDTH

        lines = []

        def blank():
            if not compact:
                lines.append(([], []))

        blank()
        asked = self.prompt or self.command
        for index, text in enumerate(wrap(asked, inner - 2, 1 if compact else 2)):
            lines.append(
                ([(MARK, True, "  " if index else "> "), (PROMPT, True, text)], [])
            )
        blank()

        status = SPINNER[frame % len(SPINNER)] if busy else "waiting"
        elapsed = format_duration(time.monotonic() - since) if since else ""
        lines.append(
            (
                [(STATUS_WORK if busy else STATUS_IDLE, busy, status)],
                [(VALUE, False, elapsed)] if elapsed else [],
            )
        )

        for label, value, keep_tail in (
            ("MODEL", self.model, False),
            ("DIR", self.cwd, True),
            ("BUFFERED", self._buffered(hidden_bytes), False),
        ):
            if not value:
                continue  # no placeholder: a missing row is quieter than a dash
            shown = (cut_tail if keep_tail else cut_head)(value, value_width)
            lines.append(
                ([(DIM, False, label.ljust(LABEL_WIDTH)), (VALUE, False, shown)], [])
            )

        blank()
        lines.append(([], [(DIM, False, HINT_WAKE)]))
        if idle_hint:
            # Nothing has come out of the agent for a while and the hook
            # channel is still claiming work -- which is what an interrupted
            # turn looks like, since interrupts fire no hook. The runner will
            # not act on that suspicion; it points at the key instead.
            lines.append(([], [(STATUS_WORK, True, HINT_SKIP_LOUD)]))
        else:
            lines.append(([], [(DIM, False, HINT_SKIP)]))
        blank()

        height = len(lines)
        return Panel(
            width,
            max(0, (cols - width) // 2),
            max(0, (rows - height) // 2),
            lines,
            compact,
        )

    # -- rendering ---------------------------------------------------------

    def render(self, rows, cols, busy, since, hidden_bytes, frame, idle_hint=False):
        """Escape sequence for the panel, or b"" when nothing changed."""
        panel = self.layout(rows, cols, busy, since, hidden_bytes, frame, idle_hint)
        self._panel = panel
        if panel is None:
            self._last = None
            return b""

        signature = (panel.x0, panel.y0, panel.width, tuple(map(_freeze, panel.lines)))
        if signature == self._last:
            return b""
        self._last = signature

        out = []
        # Blank the panel first. The rain never paints inside it, so anything
        # left over from a taller or wider panel would sit there forever.
        for row, span in sorted(self._blank_rows(panel, rows, cols).items()):
            out.append(
                f"\x1b[{row + 1};{span[0] + 1}H{RESET}{' ' * (span[1] - span[0] + 1)}"
            )

        text_x = panel.x0 + PAD
        for offset, (segments, right) in enumerate(panel.lines):
            row = panel.y0 + offset
            if not 0 <= row < rows:
                continue
            if segments:
                out.append(_paint(row, text_x, segments))
            if right:
                width = sum(display_width(t) for _, _, t in right)
                out.append(_paint(row, text_x + panel.inner - width, right))
        out.append(RESET)

        self._cleared = (panel.x0, panel.y0, panel.width, panel.height)
        return "".join(out).encode("utf-8", "replace")

    def _blank_rows(self, panel, rows, cols):
        """Rows to wipe: the panel, plus whatever the previous one covered."""
        spans = dict(panel.clear_spans(rows, cols))
        if self._cleared:
            x0, y0, width, height = self._cleared
            for row in range(y0, y0 + height):
                if not 0 <= row < rows:
                    continue
                existing = spans.get(row)
                left = min(x0, existing[0]) if existing else x0
                right = max(x0 + width - 1, existing[1]) if existing else x0 + width - 1
                spans[row] = (max(0, left), min(cols - 1, right))
        return spans

    def _buffered(self, hidden_bytes):
        """How much the overlay is holding back, in bytes and in tokens.

        Both figures describe the same hidden stretch, which is the only way
        the parenthetical is honest -- the raw scraped count is per turn, so
        printing it beside the byte count implied a conversion between two
        quantities that are not related (D-028, D-029).
        """
        if not hidden_bytes:
            return ""
        size = format_size(hidden_bytes)
        now = token_count(_ascii_only(self.tokens))
        base = self.tokens_at_cover
        if now is None or base is None:
            return size
        if now < base:
            # A fresh turn restarted the agent's counter partway through.
            base = 0
        grown = now - base
        return f"{size} ({grown:,} tokens)" if grown > 0 else size

    def mark_cover(self):
        """Called when the screen is covered: fix the token baseline."""
        self.tokens_at_cover = token_count(_ascii_only(self.tokens))

    def forget(self):
        """Drop the render cache, so the next call repaints from scratch."""
        self._last = None
        self._cleared = None

    # -- what the rain needs to know ---------------------------------------

    def clear_spans(self, rows, cols):
        return self._panel.clear_spans(rows, cols) if self._panel else {}

    def halo_spans(self, rows, cols):
        return self._panel.halo_spans(rows, cols) if self._panel else {}


def _paint(row, col, segments):
    body = "".join(f"{sgr(rgb, bold)}{text}" for rgb, bold, text in segments)
    return f"\x1b[{row + 1};{col + 1}H{body}"


def _freeze(line):
    segments, right = line
    return (tuple(segments), tuple(right))


def _ascii_only(text):
    """Token counts arrive with arrows and other ambiguous-width decoration."""
    return "".join(ch for ch in text if 0x20 <= ord(ch) < 0x7F).strip()
