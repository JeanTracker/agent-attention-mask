"""Loss-free capture of agent output while the overlay hides the screen.

SC-004 wants the *entire* agent log restored to scrollback; CONSTRAINTS caps
the runner at 50MB RAM. A ring buffer cannot satisfy both -- it drops the
oldest output by construction -- so the backing store is a spill file and
memory holds only two integers (D-007).

The file is created with mkstemp (0600) and unlinked immediately: the runner
keeps the fd, but no path exists on disk for anyone else to read. Agent output
routinely contains credentials, and AGENT_AUTONOMY lists token leakage as
FORBIDDEN, so the log must not be able to outlive the process.
"""

import os
import tempfile

_CHUNK = 1 << 16


class LogBuffer:
    def __init__(self):
        fd, path = tempfile.mkstemp(prefix="amask-", suffix=".log")
        os.unlink(path)
        self._fd = fd
        self._written = 0
        self._flushed = 0

    def append(self, data):
        if not data:
            return
        os.pwrite(self._fd, data, self._written)
        self._written += len(data)

    @property
    def pending(self):
        return self._written - self._flushed

    def flush_to(self, write):
        """Replay everything not yet shown on the real screen.

        Callers must have left the alternate buffer first (P-203) -- bytes
        written while it is up never reach scrollback.
        """
        while self._flushed < self._written:
            size = min(_CHUNK, self._written - self._flushed)
            chunk = os.pread(self._fd, size, self._flushed)
            if not chunk:
                break
            write(chunk)
            self._flushed += len(chunk)

    def discard_pending(self):
        """Drop what was captured without replaying it.

        For an agent that owns the alternate screen there is nothing to
        restore: its output never reaches scrollback in the first place, and
        replaying a long session's worth of frames would just render minutes of
        stale UI before the agent repaints over it.
        """
        self._flushed = self._written

    def tail(self, size):
        """Last `size` captured bytes -- input for the idle-prompt heuristic."""
        start = max(0, self._written - size)
        if start >= self._written:
            return b""
        return os.pread(self._fd, self._written - start, start)

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
