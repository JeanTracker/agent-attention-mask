"""Real-terminal state control: raw mode, alternate screen, mouse tracking.

Every mutation the runner makes to the user's terminal is paired with an
idempotent restore that runs on *any* exit path -- normal return, exception, or
SIGINT/SIGTERM/SIGHUP. This module lands before anything else (Phase 0) so that
the later phases can crash freely without wedging the terminal.

P-202 requires the overlay to live strictly inside the alternate screen buffer;
P-103 treats scrollback contamination as a product failure. Both of those are
enforced here rather than at each call site.
"""

import atexit
import fcntl
import os
import signal
import struct
import termios
import tty

ALT_ENTER = b"\x1b[?1049h"
ALT_EXIT = b"\x1b[?1049l"
CURSOR_HIDE = b"\x1b[?25l"
CURSOR_SHOW = b"\x1b[?25h"
# 1000h = button events, 1006h = SGR extended coordinates (P-204).
MOUSE_ON = b"\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = b"\x1b[?1006l\x1b[?1000l"
CLEAR_HOME = b"\x1b[2J\x1b[H"

_GUARDED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class TerminalController:
    """Owns every escape sequence and termios change made to the real tty."""

    def __init__(self, in_fd=0, out_fd=1):
        self.in_fd = in_fd
        self.out_fd = out_fd
        self.is_tty = os.isatty(in_fd) and os.isatty(out_fd)
        self._saved_termios = None
        self._overlay = False
        self._took_alt = False
        self._restored = False
        self._guards_installed = False

    # -- output ------------------------------------------------------------

    def write(self, data):
        """Write to the real terminal, retrying on partial writes."""
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        while data:
            try:
                n = os.write(self.out_fd, data)
            except InterruptedError:
                continue
            except (BrokenPipeError, OSError):
                return
            data = data[n:]

    def get_size(self):
        """(rows, cols) of the real terminal, with a sane fallback."""
        try:
            packed = fcntl.ioctl(self.out_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            rows, cols, _, _ = struct.unpack("HHHH", packed)
            if rows and cols:
                return rows, cols
        except OSError:
            pass
        return 24, 80

    # -- termios -----------------------------------------------------------

    def enter_raw(self):
        """Raw mode: we forward keystrokes byte-for-byte to the agent's pty.

        ISIG is off here on purpose -- Ctrl-C becomes a 0x03 byte forwarded to
        the pty, so the agent's own line discipline raises the signal. That is
        what keeps P-101 (no distortion of agent behaviour) true.
        """
        if not self.is_tty or self._saved_termios is not None:
            return
        self._saved_termios = termios.tcgetattr(self.in_fd)
        tty.setraw(self.in_fd)

    def exit_raw(self):
        if self._saved_termios is None:
            return
        try:
            termios.tcsetattr(self.in_fd, termios.TCSADRAIN, self._saved_termios)
        except termios.error:
            pass
        self._saved_termios = None

    # -- overlay -----------------------------------------------------------

    def enter_overlay(self, take_alt=True):
        """Cover the screen and take over input events.

        take_alt=False means the agent already put the terminal into the
        alternate buffer itself (interactive full-screen TUIs do). Sending
        1049h on top of that would clear the agent's screen *and* leave the
        1049 pair unbalanced, so the overlay just paints over what is there.
        """
        if not self.is_tty or self._overlay:
            return
        seq = ALT_ENTER if take_alt else b""
        self.write(seq + CURSOR_HIDE + MOUSE_ON + CLEAR_HOME)
        self._overlay = True
        self._took_alt = take_alt

    def exit_overlay(self, restore=b""):
        """Uncover the screen.

        Callers must do this *before* flushing buffered agent output (P-203);
        anything written while the alternate buffer is up is discarded by the
        terminal instead of landing in scrollback.

        When the overlay did not open the alternate buffer, dropping out of it
        would strand the agent's UI in the normal buffer -- so the rain is
        cleared in place and the caller repaints the agent instead.
        """
        if not self._overlay:
            return
        if self._took_alt:
            self.write(MOUSE_OFF + CURSOR_SHOW + ALT_EXIT + CURSOR_SHOW)
        else:
            self.write(MOUSE_OFF + CLEAR_HOME + CURSOR_SHOW)
        # The agent may have had mouse tracking or a hidden cursor of its own;
        # the lines above just cleared them, so put back what it asked for.
        self.write(restore)
        self._overlay = False
        self._took_alt = False

    @property
    def in_overlay(self):
        return self._overlay

    @property
    def took_alt(self):
        """True when the overlay itself opened the alternate buffer."""
        return self._took_alt

    # -- safety net --------------------------------------------------------

    def install_guards(self):
        """Restore the terminal on interpreter exit and on fatal signals."""
        if self._guards_installed:
            return
        atexit.register(self.restore)
        for sig in _GUARDED_SIGNALS:
            try:
                signal.signal(sig, self._on_fatal_signal)
            except (ValueError, OSError):
                pass
        self._guards_installed = True

    def _on_fatal_signal(self, signum, _frame):
        self.restore()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def restore(self):
        """Idempotent: safe to call from atexit, a handler, and normal exit."""
        if self._restored:
            return
        self._restored = True
        self.exit_overlay()
        self.exit_raw()
        if self.is_tty:
            self.write(CURSOR_SHOW)
