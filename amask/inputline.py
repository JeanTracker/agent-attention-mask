"""Reconstruct the line the user is typing, for the status panel (D-019).

Appending every printable byte was not enough. Two things broke it:

  - Multi-byte text. Korean is three UTF-8 bytes per character and a keystroke
    can straddle two reads, so byte-wise handling produced replacement
    characters mid-word.
  - Editing. Arrow keys and backspace were dropped, so text typed after moving
    the cursor was appended at the end instead of inserted, and the panel
    showed a scrambled version of what the user actually wrote.
  - String replies. iTerm2 answers the agent's XTVERSION probe with a DCS
    string, `ESC P > | iTerm2 3.5.13 ESC \\`, whose body was taken for typed
    text -- the panel opened with a literal ">|iTerm2 3.5.13".

So this keeps a real cursor and a list of characters, and decodes incrementally
so a split character is held until it is complete. It is only ever painted on
the user's own screen -- never written to the log, which would put whatever
they typed into a file (AGENT_AUTONOMY treats credential leakage as FORBIDDEN).
"""

import codecs

MAX_CHARS = 200

# CSI sequences that move or edit, keyed by their final byte and parameters.
_LEFT = ("D",)
_RIGHT = ("C",)
_HOME = ("H",)
_END = ("F",)


class InputLine:
    """A minimal line editor fed the raw bytes the user's keys produce."""

    def __init__(self):
        self._chars = []
        self._cursor = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = None  # bytes of an escape sequence being collected

    # -- feeding -----------------------------------------------------------

    def feed(self, data):
        """Consume input bytes; returns the submitted line, or None."""
        submitted = None
        for byte in data:
            if self._pending is not None:
                if self._absorb_escape(byte):
                    self._pending = None
                continue
            if byte == 0x1B:
                self._pending = bytearray()
                continue
            for ch in self._decoder.decode(bytes([byte])):
                result = self._char(ch)
                if result is not None:
                    submitted = result
        return submitted

    def _absorb_escape(self, byte):
        """Collect an escape sequence; act on it and report when it ends."""
        self._pending.append(byte)
        opener = self._pending[:1]

        # String sequences -- DCS, APC, PM, SOS -- run until ST or BEL. Their
        # bodies are plain text, so mistaking one for typing puts the
        # terminal's answer into the panel.
        if opener in (b"P", b"X", b"^", b"_"):
            if byte == 0x07:
                return True
            return len(self._pending) > 2 and self._pending[-2:] == b"\x1b\\"

        if opener == b"]":  # OSC, likewise string-terminated
            if byte == 0x07:
                return True
            return len(self._pending) > 2 and self._pending[-2:] == b"\x1b\\"

        if opener not in (b"[", b"O"):
            return True  # a two-byte escape such as ESC 7: nothing to do

        if 0x40 <= byte <= 0x7E and len(self._pending) > 1:
            self._apply_csi(self._pending[1:-1].decode("ascii", "replace"), chr(byte))
            return True
        return False

    def _apply_csi(self, params, final):
        if final in _LEFT:
            self._cursor = max(0, self._cursor - 1)
        elif final in _RIGHT:
            self._cursor = min(len(self._chars), self._cursor + 1)
        elif final in _HOME or params == "1":
            self._cursor = 0
        elif final in _END or params == "4":
            self._cursor = len(self._chars)
        elif final == "~":
            if params == "3":  # delete forward
                if self._cursor < len(self._chars):
                    del self._chars[self._cursor]
            elif params == "1":
                self._cursor = 0
            elif params == "4":
                self._cursor = len(self._chars)

    def _char(self, ch):
        code = ord(ch)
        if ch in ("\r", "\n"):
            text = "".join(self._chars).strip()
            self.reset()
            return text
        if code in (0x7F, 0x08):  # backspace
            if self._cursor > 0:
                self._cursor -= 1
                del self._chars[self._cursor]
        elif code == 0x15:  # ctrl-u, discard the line
            self.reset()
        elif code == 0x17:  # ctrl-w, discard the word before the cursor
            while self._cursor and self._chars[self._cursor - 1].isspace():
                self._cursor -= 1
                del self._chars[self._cursor]
            while self._cursor and not self._chars[self._cursor - 1].isspace():
                self._cursor -= 1
                del self._chars[self._cursor]
        elif code == 0x01:  # ctrl-a
            self._cursor = 0
        elif code == 0x05:  # ctrl-e
            self._cursor = len(self._chars)
        elif code >= 0x20 and len(self._chars) < MAX_CHARS:
            self._chars.insert(self._cursor, ch)
            self._cursor += 1
        return None

    # -- state -------------------------------------------------------------

    def reset(self):
        self._chars = []
        self._cursor = 0

    @property
    def text(self):
        return "".join(self._chars)
