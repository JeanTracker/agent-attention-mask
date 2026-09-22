"""Render one `amask --top` frame and print the screen back as text.

curses talks to a terminal, and what it sends is optimised into cursor moves
that are painful to reassemble. Reading the window back with `instr` is the
honest way to see what a person would see, so this fixture draws a frame from
canned rows and prints the cells.
"""

import curses
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from amask import top

ROWS = json.loads(os.environ.get("TOP_ROWS", "[]"))
SELECTED = int(os.environ.get("TOP_SELECTED", "0"))
MESSAGE = os.environ.get("TOP_MESSAGE", "")
MARKED = {int(pid) for pid in os.environ.get("TOP_MARKED", "").split() if pid}
DETAIL = os.environ.get("TOP_DETAIL", "")
OFFSET = int(os.environ.get("TOP_OFFSET", "0"))
# The defaults editor (D-050): its three inputs are the file as it was read,
# the edit in progress and which field the cursor is on.
CONFIG = os.environ.get("TOP_CONFIG", "")
STORED = json.loads(os.environ.get("TOP_STORED", "{}"))
STAGED = json.loads(os.environ.get("TOP_STAGED", "{}"))
CURSOR = int(os.environ.get("TOP_CURSOR", "0"))
# The key reference (D-051).
KEYS = os.environ.get("TOP_KEYS", "")


def main(screen):
    rows = [(row.get("info", {}), row.get("status"), row.get("problem"))
            for row in ROWS]
    if KEYS:
        top._draw_keys(screen, MESSAGE, OFFSET)
    elif CONFIG:
        top._draw_config(screen, top._shipped(), STORED, STAGED, CURSOR,
                         MESSAGE)
    elif DETAIL:
        wanted = int(DETAIL)
        row = top._find(rows, wanted)
        top._draw_detail(screen, row or ({"pid": wanted}, None, "사라짐"),
                         MESSAGE, OFFSET)
    else:
        top._draw(screen, rows, SELECTED, MARKED, MESSAGE)
    height, width = screen.getmaxyx()
    lines = []
    for line in range(height):
        # No length argument: `instr`'s count is bytes, not cells, so asking
        # for `width - 1` cut every Korean line short and made the key hints
        # look clipped when the screen was fine.
        lines.append(screen.instr(line, 0).decode("utf-8", "replace"))
    return lines


out = curses.wrapper(main)
sys.stdout.write("\n".join(line.rstrip() for line in out) + "\n")
