"""Virtual PTY spawning and window-size propagation (D-002, P-201).

The agent must believe it owns a real terminal (SC-005), so it runs as a
session leader on the slave side of a pty pair while the runner keeps the real
tty to itself.
"""

import fcntl
import os
import pty
import struct
import termios


def spawn(argv):
    """Run argv on the slave side of a fresh pty. Returns (pid, master_fd)."""
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError as exc:
            os.write(2, f"amask: {argv[0]}: {exc.strerror}\n".encode())
            os._exit(127)
    return pid, master_fd


def set_winsize(fd, rows, cols):
    """Push the real terminal's geometry onto the pty so TUIs lay out right."""
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def exit_code(status):
    """Translate waitpid status into a shell-visible exit code (P-101)."""
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 1
