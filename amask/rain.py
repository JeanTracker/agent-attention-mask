"""Built-in digital rain renderer (D-005).

Calling the system `cmatrix` binary was rejected: it is ncurses-based, so
initscr()/endwin() drive the alternate screen themselves. Nesting that inside
our own 1049h/1049l pair risks a stray rmcup -- and an abnormal exit skips
endwin() entirely, leaving matrix glyphs in the normal buffer. P-103 calls
scrollback contamination a product failure, so the runner owns every byte it
emits instead.

Two things keep it legible (D-015). Glyphs are plain ASCII, because half-width
katakana render at inconsistent widths depending on font and the terminal's
ambiguous-width setting -- when they land wide, neighbouring columns collide
and the screen turns to mush. And drops fall in every other column, so a glyph
always has blank space beside it the way cmatrix's do.

The colour ramp is taken from a reference project (D-020): a
four-stop truecolor fade from a near-white head through green to a dark tail,
rather than three flat SGR colours. A cell is painted once at each stop as the
head passes it, so the trail reads as a gradient without repainting it.

Rendering is incremental: each tick repaints only the cells that changed, which
keeps a full-screen animation at a few KB per frame.
"""

import random
import string

# Keyboard characters only. `|`, `_` and `-` are left out on the design's
# advice: near the panel's edge they read as border glyphs, which is exactly the
# box the panel is trying not to draw (D-027).
GLYPHS = string.ascii_letters + string.digits + "!@#$%^&*()=+[]{};:,.<>/?~"

# Columns between drops. 2 leaves one blank column beside every glyph, which is
# what stops the rain from looking like a solid smear.
COLUMN_STEP = 2

HEAD = (200, 255, 210)    # near-white leading glyph
BRIGHT = (110, 240, 140)
BODY = (40, 190, 90)
TAIL = (12, 70, 35)

_RESET = "\x1b[0m"


def _sgr(rgb, bold=False):
    """Truecolor foreground with no background, so the rain never shades a cell."""
    return f"\x1b[0;{'1;' if bold else ''}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


class Rain:
    def __init__(self, rows, cols, rng=None):
        self._rng = rng or random.Random()
        self.hole = {}
        self.halo = {}
        self.speed_scale = 1.0
        self.resize(rows, cols)

    def resize(self, rows, cols):
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self._columns = list(range(0, self.cols, COLUMN_STEP))
        self._drops = [self._new_drop(seeded=True) for _ in self._columns]

    def set_hole(self, spans):
        """Keep the rain out of {row: (left, right)}, 0-based inclusive.

        The status panel lives there. Drops still advance through the region;
        they are simply not painted inside it, so they emerge below as if the
        panel were in front of the rain rather than cut out of it.
        """
        self.hole = spans or {}

    def set_halo(self, spans):
        """{row: [(left, right), ...]} where every drop is drawn at TAIL.

        The panel has no background, so a bright head two columns from the text
        flickers beside it. Forcing the darkest stage there makes the boundary
        fade out instead (D-027).
        """
        self.halo = spans or {}

    def set_speed_scale(self, scale):
        """Slow the rain while the agent is idle -- a second channel for it."""
        self.speed_scale = max(0.05, scale)

    def _hidden(self, row, col):
        span = self.hole.get(row) if self.hole else None
        return span is not None and span[0] <= col <= span[1]

    def _in_halo(self, row, col):
        for left, right in self.halo.get(row, ()):
            if left <= col <= right:
                return True
        return False

    def _new_drop(self, seeded=False):
        return {
            # Start above the viewport so drops enter from the top edge.
            "row": self._rng.uniform(-self.rows, 0) if seeded else -1.0,
            "speed": self._rng.uniform(0.25, 1.1),
            "length": self._rng.randint(max(3, self.rows // 6), max(4, self.rows)),
        }

    def frame(self):
        """Advance one tick; return the escape sequence for the changed cells."""
        out = []
        for index, drop in enumerate(self._drops):
            col = self._columns[index]
            prev = int(drop["row"])
            drop["row"] += drop["speed"] * self.speed_scale
            head = int(drop["row"])
            if head == prev:
                continue  # slow column, nothing moved this tick

            # Each cell is painted once per stop as the head moves past it,
            # so the column ends up holding the whole ramp at once.
            if self._visible(head, col):
                out.append(self._cell(head, col, _sgr(HEAD, bold=True)))
            if self._visible(prev, col):
                out.append(self._cell(prev, col, _sgr(BRIGHT, bold=True)))

            length = drop["length"]
            mid = head - max(2, length // 3)
            if self._visible(mid, col):
                out.append(self._cell(mid, col, _sgr(BODY)))

            deep = head - max(3, (length * 2) // 3)
            if self._visible(deep, col):
                out.append(self._cell(deep, col, _sgr(TAIL)))

            tail = head - length
            if self._visible(tail, col):
                out.append(f"\x1b[{tail + 1};{col + 1}H ")

            if tail >= self.rows:
                self._drops[index] = self._new_drop()

        if not out:
            return b""
        out.append(_RESET)
        return "".join(out).encode("ascii", "replace")

    def _visible(self, row, col):
        return 0 <= row < self.rows and not self._hidden(row, col)

    def _cell(self, row, col, colour):
        if self._in_halo(row, col):
            colour = _sgr(TAIL)
        return f"\x1b[{row + 1};{col + 1}H{colour}{self._rng.choice(GLYPHS)}"
