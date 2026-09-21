"""A local control socket: one per runner, for an external app to watch it.

The runner already knows everything such an app wants -- what was asked,
whether the agent is working, whether the screen is covered right now -- and
until now none of it left the process. This is the channel that lets it out,
and lets the three state constants be retuned per session (D-039).

Shape, and why:

* `AF_UNIX`/`SOCK_STREAM` under a fixed per-user directory. Not the FIFO's
  `tempfile.gettempdir()`: that follows the *runner's* `TMPDIR`, which an
  external app has no way to know. A fixed short path also keeps clear of
  Darwin's 104-byte `sun_path` limit, which `/var/folders/.../T/` already
  spends two thirds of.
* A discovery file beside the socket carries the token, so finding a session is
  reading a directory and authenticating is reading one file. Both are 0600
  inside a 0700 directory, and the token is checked on every request -- the
  same belt and braces as the hook FIFO (D-030).
* Every failure here is swallowed. A screensaver's control channel must not be
  able to take the agent down with it (P-101), so a runner whose socket cannot
  be created simply runs without one.
* Non-blocking throughout, with a cap on the work one select cycle may do. The
  runner's cycle owes the user a 0.2s wake (SC-002) and owes the animation a
  20fps tick; a client must not be able to spend that budget.
"""

import errno
import json
import os
import secrets
import socket
import stat

# Every runner's socket and discovery file live here. `AMASK_RUN_DIR` is for
# the test suite, which must not write into the user's home.
ENV_RUN_DIR = "AMASK_RUN_DIR"
DIR_MODE = 0o700
FILE_MODE = 0o600

# One request line is small; anything larger is a client bug or a probe.
MAX_LINE = 64 * 1024
# Per select cycle, across all clients. See the module note on the budget.
MAX_REQUESTS_PER_CYCLE = 16
BACKLOG = 8


def run_dir():
    """The directory holding every live runner's socket and discovery file."""
    override = os.environ.get(ENV_RUN_DIR)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".amask", "run")


def _names(directory, pid):
    base = os.path.join(directory, f"amask-ctl-{pid}")
    return base + ".sock", base + ".json"


def sweep_stale(directory):
    """Remove the socket and discovery file of runners that are gone.

    A killed runner never gets to clean up after itself, and a stale
    discovery file is worse than a missing one -- an app would offer the user
    a session that cannot answer.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.startswith("amask-ctl-"):
            continue
        stem = name[len("amask-ctl-"):]
        for suffix in (".json.tmp", ".sock", ".json"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        else:
            continue
        try:
            pid = int(stem)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            try:
                os.unlink(os.path.join(directory, name))
            except OSError:
                pass
        except OSError:
            pass  # alive but not ours to signal


def sessions(directory=None):
    """Every live runner's discovery record, newest last.

    This is the client half: an external app calls it to list what it can talk
    to. Unreadable or half-written records are skipped rather than raised --
    a session appearing mid-scan must not break the listing.
    """
    directory = directory or run_dir()
    sweep_stale(directory)
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("amask-ctl-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name)) as handle:
                info = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or "socket" not in info:
            continue
        out.append(info)
    out.sort(key=lambda info: info.get("started", 0))
    return out


def request(info, payload, timeout=2.0):
    """Send one command to one session and return its reply.

    Client side. Raises OSError if the session cannot be reached, which for a
    caller is the same news as the session being gone.
    """
    payload = dict(payload)
    payload["token"] = info.get("token")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(info["socket"])
        sock.sendall(json.dumps(payload).encode() + b"\n")
        buf = bytearray()
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_LINE:
                break
    line, _, _ = bytes(buf).partition(b"\n")
    if not line.strip():
        raise OSError("no reply")
    return json.loads(line)


class _Conn:
    """One connected client: what it has said, and what it is owed."""

    def __init__(self, sock):
        self.sock = sock
        self.inbox = bytearray()
        self.outbox = bytearray()
        self.closing = False

    def fileno(self):
        return self.sock.fileno()


class ControlChannel:
    """The runner's half. Owns the socket, the discovery file, and the clients.

    The runner drives it: `open()` once, `fds()` into its select call,
    `service(readable, handler)` once a cycle, `close()` at the end. Command
    meaning stays in the runner -- this class does transport and nothing else.
    """

    def __init__(self, directory=None, pid=None, token=None):
        self.dir = directory or run_dir()
        self.pid = pid or os.getpid()
        self.token = token or secrets.token_hex(8)
        self.socket_path, self.info_path = _names(self.dir, self.pid)
        self.available = False
        self._server = None
        self._conns = {}
        # The published record, kept here so a republish merges rather than
        # replaces: `session_id` arrives long after the file is first written
        # and must not be droppable by a later update that does not mention it.
        self._info = {}

    # -- lifecycle ---------------------------------------------------------

    def open(self, info=None):
        """Create the socket and publish the discovery file.

        Returns False when that is not possible, and a False here is not an
        error the user should hear about: the runner's job is the screen.
        """
        try:
            os.makedirs(self.dir, mode=DIR_MODE, exist_ok=True)
            # An existing directory keeps its own mode, so say it outright --
            # the token in there is what authenticates a control request.
            os.chmod(self.dir, DIR_MODE)
            sweep_stale(self.dir)
            for path in (self.socket_path, self.info_path):
                if os.path.exists(path):
                    os.unlink(path)
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.setblocking(False)
            self._server.bind(self.socket_path)
            os.chmod(self.socket_path, FILE_MODE)
            self._server.listen(BACKLOG)
            self._write_info(info or {})
            self.available = True
        except Exception:
            # Wider than OSError on purpose. This is the one place the policy
            # is "a control channel may not take the agent down" (P-101), and
            # `open()` runs outside the runner's try/finally -- anything
            # raised here would kill the session before it could clean up.
            self.close()
            self.available = False
        return self.available

    def _write_info(self, extra):
        self._info.update(extra or {})
        record = dict(self._info)
        record.update({
            "pid": self.pid,
            "socket": self.socket_path,
            "token": self.token,
        })
        tmp = self.info_path + ".tmp"
        # 0600 from the moment it exists: it carries the token, so there must
        # be no window in which it is world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(record, handle)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self.info_path)

    def update_info(self, extra):
        """Republish the discovery file, e.g. once the session id is known.

        `session_id` only arrives with the first hook event, so it cannot be
        in the file the runner writes at startup.
        """
        if not self.available:
            return
        try:
            self._write_info(extra)
        except OSError:
            pass

    def close(self):
        for conn in list(self._conns.values()):
            self._drop(conn)
        self._conns.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        for path in (self.socket_path, self.info_path):
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
        self.available = False

    # -- the runner's select loop ------------------------------------------

    def fds(self):
        if not self.available:
            return []
        return [self._server] + list(self._conns.values())

    def service(self, readable, handler):
        """Accept, read, dispatch and flush -- once, within the cycle budget.

        `handler(request_dict) -> reply_dict` is the runner's command table.
        Everything that can go wrong with a client is contained here: a dead
        one is dropped, a rude one is dropped, and neither is the runner's
        problem.
        """
        if not self.available:
            return
        ready = set(readable)
        if self._server in ready:
            self._accept()
        budget = MAX_REQUESTS_PER_CYCLE
        for conn in list(self._conns.values()):
            if conn in ready:
                budget = self._read(conn, handler, budget)
            self._flush(conn)

    def _accept(self):
        while True:
            try:
                sock, _ = self._server.accept()
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    pass
                return
            sock.setblocking(False)
            conn = _Conn(sock)
            self._conns[sock.fileno()] = conn

    def _read(self, conn, handler, budget):
        try:
            chunk = conn.sock.recv(65536)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return budget
            self._drop(conn)
            return budget
        if not chunk:
            self._drop(conn)
            return budget
        conn.inbox += chunk
        # The budget caps how many lines one cycle dispatches, so a client that
        # sent a burst can legitimately have several requests queued here --
        # the cap that matters is on a single line, plus a ceiling on the
        # backlog so a client cannot make the runner hold unbounded memory.
        if len(conn.inbox) > MAX_LINE * MAX_REQUESTS_PER_CYCLE:
            self._drop(conn)
            return budget
        while b"\n" in conn.inbox and budget > 0:
            line, _, rest = conn.inbox.partition(b"\n")
            conn.inbox = bytearray(rest)
            if len(line) > MAX_LINE:
                self._drop(conn)
                return budget
            if not line.strip():
                continue
            budget -= 1
            self._reply(conn, self._dispatch(line, handler))
        return budget

    def _dispatch(self, line, handler):
        try:
            payload = json.loads(line)
        except ValueError:
            return {"ok": False, "reason": "not JSON"}
        if not isinstance(payload, dict):
            return {"ok": False, "reason": "not an object"}
        if payload.get("token") != self.token:
            # Same rule as the hook FIFO: a caller without the token is not
            # this runner's client, whatever it found on the filesystem.
            return {"ok": False, "reason": "bad token"}
        try:
            reply = handler(payload)
        except Exception as exc:  # the runner must survive a bad command
            return {"ok": False, "reason": f"{type(exc).__name__}"}
        return reply if isinstance(reply, dict) else {"ok": False, "reason": "no reply"}

    def _reply(self, conn, reply):
        try:
            conn.outbox += json.dumps(reply).encode() + b"\n"
        except (TypeError, ValueError):
            conn.outbox += b'{"ok":false,"reason":"unserialisable"}\n'

    def _flush(self, conn):
        while conn.outbox:
            try:
                sent = conn.sock.send(conn.outbox)
            except (BrokenPipeError, ConnectionResetError):
                self._drop(conn)
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    return  # try again next cycle; the client is just slow
                self._drop(conn)
                return
            if not sent:
                return
            del conn.outbox[:sent]

    def _drop(self, conn):
        # By identity, not by fd: a closed socket reports -1, so the key it
        # was filed under is no longer derivable from it.
        for fd, existing in list(self._conns.items()):
            if existing is conn:
                self._conns.pop(fd, None)
        try:
            conn.sock.close()
        except OSError:
            pass


def mode_of(path):
    """The permission bits of `path`, for the tests that assert on them."""
    return stat.S_IMODE(os.stat(path).st_mode)
