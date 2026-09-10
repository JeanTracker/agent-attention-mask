"""Runner lifecycle: agent on a pty, matrix on the screen, log in a spill file.

Two states, and the transition between them is symmetric (G-001 revised
2026-09-07, closing HRQ-001/HRQ-002):

  PASSTHROUGH  the runner is invisible; the agent owns the screen.
  OVERLAY      the rain covers the screen and agent output goes to the log.

The runner starts in PASSTHROUGH -- covering the screen at launch would hide
an interactive agent's prompt before the user had said anything. It covers the
screen only once the agent has been *continuously* busy for OVERLAY_DELAY with
nobody typing, and it uncovers on any of: a keypress or click, the agent going
quiet, or the agent exiting. Both directions repeat for the life of the
process, so a long session hides and reveals itself as often as the work does.

Telling work from waiting is the whole problem, and silence means opposite
things depending on the agent. Measured on this machine:

  `claude -p "..."`   silent for 5.9s while it works, then prints the answer.
                      Silence *is* the work.
  interactive `claude`  streams at a ~114ms median while working, but pauses
                      up to 800ms mid-answer; at its prompt, silent but for a
                      60-150 byte repaint every 8-10 seconds. Silence is the
                      wait, and the repaints are not work.

So the runner tracks a *run* of activity rather than reacting to single chunks,
and the run starts at launch: an agent that has said nothing yet is working, an
agent that has spoken and then stopped is waiting. Cover once such a run has
lasted OVERLAY_DELAY. That covers the silent one-shot immediately-ish, leaves
the idle prompt alone (its blips never accumulate), and hides the interactive
agent once it is genuinely busy. Reacting to single chunks is what made an idle
prompt flash the screensaver open and shut on a loop.
"""

import os
import re
import select
import signal
import sys
import time

from . import ptyproxy
from .logbuf import LogBuffer
from . import hooks
from .hud import Hud
from .inputline import InputLine
from .rain import Rain
from .term import CLEAR_HOME, TerminalController

USAGE = "사용법: amask <에이전트 명령> [인자...]"

# select() timeout: the animation tick, and how fast SIGWINCH/SIGCHLD flags are
# noticed. 20fps is smooth enough and leaves SC-002's 0.2s budget untouched.
TICK = 0.05

READ_CHUNK = 65536


def _seconds(name, default):
    """Timing override, for tuning and for keeping the test suite quick."""
    try:
        return max(0.0, float(os.environ[name]))
    except (KeyError, ValueError):
        return default


# H-001, measured against `claude`. An 80-second streaming response emitted 574
# chunks at a 114ms median -- but with 18 gaps over 600ms and a worst case of
# 807ms, far longer than an early short sample suggested. At 600ms the
# screensaver therefore lifted and re-covered eighteen times mid-answer.
# Nothing in that run gapped past 1.0s, and an idle prompt is silent for
# seconds at a stretch (4.9s and 15s observed, with brief repaints every 8-10s),
# so 1.5s clears the worst streaming pause by ~1.9x while staying far below any
# real wait for the user.
IDLE_SILENCE = _seconds("AMASK_IDLE_SILENCE", 1.5)

# With hooks the agent says what it is doing, so silence proves nothing -- a
# ten-minute tool call is silent and busy. This is only a backstop against a
# lost *completion*: if the agent said it was working and then went quiet for
# this long, assume the Stop event never arrived and start guessing again.
#
# It deliberately does not apply to a waiting agent (D-030). "Waiting" does not
# go stale -- nothing has happened, which is the point -- and expiring it meant
# an idle session drifted back to the heuristic and then re-covered itself the
# moment its own footer repainted, which another terminal starting a Claude
# session is enough to cause.
HOOK_STALL = _seconds("AMASK_HOOK_STALL", 120.0)

# Interrupting a response fires no hook at all -- not Stop, not StopFailure,
# not Notification -- so a latched BUSY has no event to release it and the
# screen stays covered until HOOK_STALL runs out.
# Detecting that by timing was rejected: a fullscreen agent goes byte-silent
# for over two seconds mid-answer, so silence cannot be told from work without
# a threshold, and a wrong call there uncovers the screen while the agent is
# still writing (D-025, D-036). The user gets a key instead.
#
# This constant is the one place a silence threshold survives, and only
# because it changes *emphasis* and nothing else: guessing wrong makes a hint
# louder than it needed to be. It is deliberately not an env knob -- README's
# timing section is for the three constants that decide state.
IDLE_HINT_AFTER = 10.0

# Pressed while the screen is covered, this skips the rest of the turn.
SKIP_KEYS = (b"q", b"Q")

# `UserPromptSubmit` does not only fire for things the user typed. The harness
# injects its own messages as prompts -- a finished background task, a system
# reminder -- and they arrive with the notification markup as the prompt text,
# which the panel then displayed instead of the question (D-031). Measured
# payloads: a background task lands as "<task-notification>\n<task-id>...".
INJECTED_PROMPT_PREFIXES = (
    "<task-notification>",
    "<system-reminder>",
    "<local-command-",
)

# How long the agent must be *continuously* busy before the screen is covered.
# Continuously is the operative word: an idle interactive claude still repaints
# briefly every 8-10 seconds (measured: 149 and 59 byte blips), and treating
# each blip as work made the screensaver flash open and shut on a loop. Work
# that deserves hiding sustains output -- claude's spinner ticks about every
# 105ms for as long as it is thinking or running a tool.
# It also governs how long the screen is left alone after the user uncovered
# it by hand -- one delay for both, rather than a second constant to reason
# about (D-022). Uncovering by hand is just another reason the user is present.
OVERLAY_DELAY = _seconds("AMASK_OVERLAY_DELAY", 4.0)

# After a wake gesture, keep eating stdin briefly. A mouse click's release
# event (\x1b[<0;10;5m) arrives after we have already sent MOUSE_OFF, and those
# bytes must not reach the agent as keystrokes (D-008).
WAKE_DRAIN = 0.05

# A blanket drain cannot cover the release event without also eating fast
# typing, so for a short window after waking we forward stdin but strip mouse
# reports specifically. The window is bounded because the agent may enable its
# own mouse tracking, and filtering its events would distort it (P-101).
WAKE_MOUSE_GUARD = 0.3

# How long the pty stays one row short while nudging a full-screen agent to
# repaint. Long enough for its signal handler to observe the smaller size, far
# inside SC-002's budget -- the screen is already uncovered by then.
REPAINT_SETTLE = 0.06

# SGR (1006) reports, plus the legacy X10 form in case the terminal falls back.
_MOUSE_REPORT = re.compile(rb"\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[M[\s\S]{3}")

# Sequences the agent sends *to* the terminal to probe its capabilities, and
# the replies those produce on stdin. They are invisible, so forwarding them
# through the overlay costs nothing and keeps the agent's view of the terminal
# honest (SC-005); the replies must not be mistaken for a wake gesture (D-009).
_TERM_QUERY = re.compile(
    rb"\x1b\[6n"                          # cursor position
    rb"|\x1b\[\?6n"                       # extended cursor position
    rb"|\x1b\[[0-9>=?]*c"                  # device attributes
    rb"|\x1b\[>[0-9;]*q"                   # XTVERSION
    rb"|\x1b\[\?[0-9;]*\$p"               # DECRQM, mode support
    rb"|\x1b\][0-9;]*\?(?:\x07|\x1b\\)"   # OSC colour queries
)
# Every shape the terminal answers in. Missing one is not cosmetic: the reply
# is swallowed as a keypress, the agent never hears back, and it concludes the
# terminal lacks the capability. iTerm2 answers XTVERSION with a DCS string --
# `ESC P > | iTerm2 3.5.13 ESC \` -- which was neither forwarded nor filtered,
# so it both cancelled the screensaver and landed in the panel as literal text.
_TERM_REPLY = re.compile(
    rb"\x1b\[\??[0-9;]*R"                    # CPR / DECXCPR
    rb"|\x1b\[[?>=][0-9;]*c"                  # DA1 / DA2 / DA3
    rb"|\x1b\[\?[0-9;]*\$y"                  # DECRPM
    rb"|\x1bP[\s\S]*?\x1b\\"                 # DCS: XTVERSION, DECRQSS, ...
    rb"|\x1b\][0-9;]*;[^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC replies
)

# Whether the *agent* has the alternate buffer open, so the runner does not
# nest its own inside it (D-010).
_ALT_ON = re.compile(rb"\x1b\[\?(?:1049|1047|47)h")
_ALT_OFF = re.compile(rb"\x1b\[\?(?:1049|1047|47)l")

# Whether the agent redraws in place rather than appending lines (D-018).
# Moving the cursor to a row, walking it upward, clearing the screen or setting
# a scroll region all mean the output is a picture being maintained, not a log.
# Deliberately excludes \r and EL, which line-oriented tools use for progress
# bars without ever leaving the current row -- their output is still a log.
_REPAINT_HINT = re.compile(
    rb"\x1b\[\d*;\d*[Hf]|\x1b\[[Hf]|\x1b\[\d*[ABdr]|\x1b\[[ABdr]|\x1b\[2J|\x1b[78]"
)

# Private modes the agent may own that the overlay also touches. Interactive
# claude turns on all four mouse modes plus focus reporting and bracketed
# paste, so uncovering has to hand them back rather than leave them off (D-017).
_TRACKED_MODES = (25, 1000, 1002, 1003, 1005, 1006, 1004, 2004)
_MODE_CHANGE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")

# The terminal's answer to focus reporting. Switching iTerm2 tabs sends these,
# and treating them as a keypress cancelled the screensaver the moment the user
# looked at another tab -- a reported bug (D-017).
_FOCUS_EVENT = re.compile(rb"\x1b\[[IO]")

# Anything the terminal or the user's keys send as an escape sequence. Dropping
# only the ESC byte was not enough: a cursor-position reply arrived as
# "\x1b[?56;3;1R" and the tail of it landed in the panel as literal text.
_ESCAPE_SEQ = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?<>!]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)

# Best-effort scrapes of the agent's own status line, for the panel. These are
# the shapes interactive claude prints ("Opus 5 (1M context)", "↓2 tokens");
# any agent that does not print them simply shows nothing for that field.
# The middle dot and arrows are UTF-8 byte sequences here, since the agent's
# output is raw bytes: \xc2\xb7 is "·", \xe2\x86\x91/\x93 are the arrows.
_MODEL_HINT = re.compile(rb"(Opus|Sonnet|Haiku|GPT|Gemini)(?:[^|\x1b\n\r\xc2]|\xc2(?!\xb7)){0,40}")
_TOKENS_HINT = re.compile(
    rb"(?:\xe2\x86[\x91\x93])?\s*([0-9][0-9.,]*\s*[kKmM]?)\s*tokens"
)

# Everything after the model name itself: claude appends its effort level and
# plan, which are not worth a panel row.
_MODEL_TRIM = re.compile(r"\s*(?:with|\||\u00b7)")

# How much recent agent text to keep for those scrapes.
_CHROME_TAIL = 8192


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr)
        return 0 if argv else 2

    term = TerminalController()
    if not term.is_tty:
        # Nothing to hide, so the wrapper has nothing to add. Hand the process
        # over outright: exit code and signal disposition stay exact (P-101).
        os.execvp(argv[0], argv)

    return Runner(term, argv).run()


class Runner:
    def __init__(self, term, argv):
        self.term = term
        self.argv = argv
        self.master_fd = None
        self.pid = None
        self.log = LogBuffer()
        self.rain = None
        self._resized = False
        self._child_signalled = False
        self._watch_stdin = True
        self._status = None
        self._mouse_guard_until = 0.0
        self._last_output = None  # None until the agent has said anything
        self._busy_since = None  # start of the current run of work; set in run()
        self._agent_alt = False
        self._agent_left_alt = False  # agent dropped its TUI while covered
        self._agent_repaints = False  # agent maintains a picture, not a log
        self._hold_until = 0.0
        self._restore_size_at = None  # deferred half of the repaint nudge
        self._agent_modes = {}  # private modes the agent turned on or off
        self._typing = InputLine()  # what the user is typing, for the HUD
        self._chrome = bytearray()  # recent agent text, for scraping its status
        self._frame = 0
        self.hud = Hud(" ".join(argv), _short_path(os.getcwd()))
        self.hooks = hooks.HookChannel(_emitter_path())
        self._hook_state = None  # None until the agent reports something
        self._hook_at = 0.0
        # Set by SKIP_KEYS, cleared when the next prompt is submitted: the
        # user has said this turn is not worth covering.
        self._skip_turn = False

    # -- lifecycle ---------------------------------------------------------

    def run(self):
        self.term.install_guards()
        rows, cols = self.term.get_size()
        argv = self._with_hooks(self.argv)
        self.pid, self.master_fd = ptyproxy.spawn(argv)
        ptyproxy.set_winsize(self.master_fd, rows, cols)
        self.term.enter_raw()
        signal.signal(signal.SIGWINCH, self._on_winch)
        signal.signal(signal.SIGCHLD, self._on_chld)

        self.rain = Rain(rows, cols)
        # Start uncovered, and treat the command line itself as the first
        # dispatch: `amask claude -p "..."` never sees a keystroke, and
        # an interactive agent simply will not sustain output at its prompt.
        # The work clock starts now, because an agent that has not spoken yet is
        # working -- a one-shot run is silent for its whole thinking time.
        self._busy_since = time.monotonic()
        self._hold_until = self._busy_since + OVERLAY_DELAY

        try:
            self._loop()
        finally:
            self._wake()  # uncovers the screen, then restores the log
            self.term.restore()
            self.log.close()
            self.hooks.close()
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass

        return ptyproxy.exit_code(self._reap())

    def _on_winch(self, _signum, _frame):
        self._resized = True

    def _on_chld(self, _signum, _frame):
        self._child_signalled = True

    # -- main loop ---------------------------------------------------------

    def _loop(self):
        while True:
            if self._resized:
                self._resized = False
                self._apply_resize()

            if self._child_signalled and self._child_is_gone():
                # The agent is gone but its last writes may still be in the
                # pty. Drain before waking so SC-004 keeps the whole log.
                self._child_signalled = False
                self._drain_master()
                return

            watch = [self.master_fd] + ([self.term.in_fd] if self._watch_stdin else [])
            if self.hooks.available:
                watch.append(self.hooks.fileno())
            try:
                readable, _, _ = select.select(watch, [], [], TICK)
            except InterruptedError:
                continue
            except OSError:
                return

            if self.master_fd in readable:
                data = _read(self.master_fd)
                if not data:
                    return  # pty closed: agent exited
                self._sink(data)

            if self.hooks.available and self.hooks.fileno() in readable:
                self._handle_hooks()

            if self._watch_stdin and self.term.in_fd in readable:
                if not self._handle_stdin():
                    return

            self._tick()

    def _tick(self):
        """Drive the state machine and the animation once per select cycle."""
        if self._restore_size_at is not None and time.monotonic() >= self._restore_size_at:
            self._restore_size_at = None
            rows, cols = self.term.get_size()
            ptyproxy.set_winsize(self.master_fd, rows, cols)

        now = time.monotonic()
        if self._hooks_speaking():
            # The agent is telling us what it is doing; silence is not evidence
            # of anything while a tool call runs.
            idle = self._hook_state != hooks.BUSY
            if idle:
                # Its own output must not restart the clock either: an idle
                # prompt still repaints every few seconds, and that was enough
                # to re-cover the screen after the agent said it had stopped.
                self._busy_since = None
        else:
            idle = self._agent_is_idle()
            if idle:
                # The run of output ended, so the next one starts a fresh clock.
                self._busy_since = None

        if self.term.in_overlay:
            if idle:
                # SC-003: the agent stopped producing output, so it is finished
                # or waiting on the user. Wake without being asked.
                self._wake()
            else:
                self._frame += 1
                rows, cols = self.term.get_size()
                # Render the panel first so the rain knows exactly which cells
                # the bands occupy this frame; their widths follow their text.
                busy = self._busy_since is not None
                panel = self.hud.render(
                    rows,
                    cols,
                    busy=busy,
                    since=self._busy_since or now,
                    hidden_bytes=self.log.pending,
                    frame=self._frame // 4,
                    idle_hint=self._idle_suspected(),
                )
                self.rain.set_hole(self.hud.clear_spans(rows, cols))
                self.rain.set_halo(self.hud.halo_spans(rows, cols))
                # A stopped agent under a still-moving rain: the grey status
                # says it, and halving the speed says it again without words.
                self.rain.set_speed_scale(1.0 if busy else 0.5)
                self.term.write(self.rain.frame())
                self.term.write(panel)
        elif self._should_cover(now):
            self._sleep()

    # -- agent state -------------------------------------------------------

    def _with_hooks(self, argv):
        """Wire the agent's hooks to us, if we can and it is Claude Code.

        The FIFO path also goes into the environment, so any agent can report
        its own state without a settings file.
        """
        if not self.hooks.open():
            return argv
        # The child inherits both halves; anything that finds the FIFO
        # without the token is not this runner's agent and is ignored.
        os.environ[hooks.ENV_VAR] = self.hooks.path
        os.environ[hooks.ENV_TOKEN] = self.hooks.token
        if os.path.basename(argv[0]) != "claude" or "--settings" in argv:
            # Not Claude Code, or the user is already passing settings of their
            # own -- leave the command line alone and rely on the heuristic.
            return argv
        return [argv[0], "--settings", self.hooks.settings_json()] + argv[1:]

    def _handle_hooks(self):
        for event in self.hooks.read_events():
            if event.get("token") != self.hooks.token:
                # Not from the agent this runner started. Panels are meant to
                # be independent, so anything else writing here is ignored.
                _debug(f"hook rejected: {event.get('event')}")
                continue
            name = event.get("event")
            meaning = hooks.EVENT_MEANING.get(name)
            if meaning is None:
                continue
            prompt_id = event.get("prompt_id")

            # `agent_id` is only present on events fired inside a subagent
            # -- Claude Code documents it as the marker for exactly that -- so
            # an event carrying one is not the main thread's state. Claude Code runs an unnamed internal
            # subagent after a turn ends -- to build the next-prompt
            # suggestion -- and its SubagentStop lands ~2s after Stop. Reading
            # that as work put the panel back up saying "working" on an idle
            # agent (D-033).
            if meaning == hooks.BUSY and event.get("agent_id"):
                _debug(f"hook {name} ignored: subagent {event.get('agent_id')}")
                continue

            if name == "UserPromptSubmit":
                # A new turn: whatever the user skipped is over.
                self._skip_turn = False

            prompt = event.get("prompt")
            if prompt:
                # Straight from the agent, so no reconstructing it from
                # keystrokes -- and no way for it to come out garbled. But only
                # when the user is the one who said it: injected prompts carry
                # notification markup, and showing that is worse than showing
                # the question it interrupted, so the panel keeps the old one
                # (D-031). The state still changes -- real work does follow an
                # injected prompt.
                if _is_injected_prompt(prompt):
                    _debug(f"hook {name}: injected prompt kept off the panel")
                else:
                    self.hud.prompt = " ".join(str(prompt).split())[:200]

            # The turn id is in the line because the state machine has three
            # inputs and a wrong call looks the same from outside whichever
            # one made it; this is what tells them apart after the fact.
            _debug(f"hook {name} -> {meaning} prompt_id={prompt_id}")
            self._hook_state = meaning
            self._hook_at = time.monotonic()
            if meaning == hooks.BUSY:
                if self._busy_since is None:
                    self._busy_since = self._hook_at
            else:
                # The agent finished its turn or is asking the user something.
                self._busy_since = None
                self._wake()

    def _idle_suspected(self):
        """Hooks say working, but nothing has come out for a while.

        Display only -- see IDLE_HINT_AFTER. This never feeds the state
        machine, so a false positive costs a louder hint and nothing more.
        """
        if self._hook_state != hooks.BUSY or self._last_output is None:
            return False
        return (time.monotonic() - self._last_output) >= IDLE_HINT_AFTER

    def _hooks_speaking(self):
        """True while the agent's own report is still the best thing we have."""
        if not self.hooks.available or self._hook_state is None:
            return False
        if self._hook_state != hooks.BUSY:
            return True  # a waiting agent stays waiting until it says otherwise
        return (time.monotonic() - self._hook_at) < HOOK_STALL

    def _handle_stdin(self):
        """Returns False if the loop should stop."""
        data = _read(self.term.in_fd, 4096)
        if not data:
            self._watch_stdin = False  # stdin closed; keep draining the agent
            return True

        if self.term.in_overlay:
            passive = b"".join(
                _TERM_REPLY.findall(data) + _FOCUS_EVENT.findall(data)
            )
            if passive and not _FOCUS_EVENT.sub(b"", _TERM_REPLY.sub(b"", data)):
                # Machine traffic, not a user gesture: the terminal answering
                # the agent's capability probe (D-009), or reporting that the
                # window lost or gained focus because the user switched tabs
                # (D-017). Pass it through and stay asleep.
                try:
                    os.write(self.master_fd, passive)
                except OSError:
                    return False
                return True

            # SC-002: any key or click is a wake gesture, not agent input. The
            # bytes that woke us are discarded rather than forwarded -- an
            # Enter press must not submit an empty prompt or answer a pending
            # y/n confirmation.
            #
            # Which is exactly why the skip key can live here and nowhere else:
            # nothing typed while covered reaches the agent, so spending `q`
            # on the runner costs the agent nothing (D-008 still holds). While
            # uncovered every byte is forwarded, and taking `q` out of that
            # stream would eat the letter from what the user is typing.
            if data.strip() in SKIP_KEYS:
                self._skip_turn = True
                _debug("skip: user asked to stay uncovered for this turn")
            self._wake()
            self._drain_stdin(WAKE_DRAIN)
            self._mouse_guard_until = time.monotonic() + WAKE_MOUSE_GUARD
            return True

        if time.monotonic() < self._mouse_guard_until:
            data = _MOUSE_REPORT.sub(b"", data)
            if not data:
                return True

        if _FOCUS_EVENT.fullmatch(data):
            # Focus in/out while uncovered: forward it, but it is not the user
            # giving the agent work, so it must not re-arm the screensaver.
            try:
                os.write(self.master_fd, data)
            except OSError:
                return False
            return True

        self._capture_prompt(data)

        # Real input: the user is present, so hold the screensaver off.
        self._hold_until = time.monotonic() + OVERLAY_DELAY
        try:
            os.write(self.master_fd, data)
        except OSError:
            return False
        return True

    # -- state transitions -------------------------------------------------

    def _sink(self, data):
        self._track_modes(data)
        self._scrape_status(data)
        if not self._agent_repaints and _REPAINT_HINT.search(data):
            self._agent_repaints = True
        if _ALT_ON.search(data):
            self._agent_alt = True
        if _ALT_OFF.search(data):
            self._agent_alt = False
            if self.term.in_overlay and not self.term.took_alt:
                # The agent abandoned its full-screen UI while hidden, so what
                # follows is normal-buffer output that does belong in
                # scrollback after all.
                self._agent_left_alt = True

        if self.term.in_overlay:
            # Forward capability probes live, but keep them OUT of the log:
            # replaying them on flush would make the terminal answer a second
            # time, and that reply would arrive in passthrough and be typed
            # straight into the agent. The agent already got its answer.
            self.log.append(_TERM_QUERY.sub(b"", data))
            self._mark_output()
            for query in _TERM_QUERY.findall(data):
                self.term.write(query)
        else:
            self._mark_output()
            self.term.write(data)

    def _scrape_status(self, data):
        """Pick the model and token count out of the agent's own chrome.

        Purely opportunistic: these are strings interactive claude happens to
        print, so a miss leaves the field blank rather than guessing. The text
        is accumulated across reads with the escape sequences taken out --
        a TUI splits its status line over several chunks and threads colour
        codes through the middle of words, so scraping a single raw read finds
        nothing.
        """
        self._chrome += _ESCAPE_SEQ.sub(b"", data)
        del self._chrome[:-_CHROME_TAIL]

        # claude prints its name more than once: spaced in the splash, and
        # squashed to "Opus5(1Mcontext)" in the compact status bar, which
        # positions each run of characters instead of emitting the spaces
        # between them. Take every candidate and keep the readable one --
        # searching for just the first match always returned whichever sat
        # earlier in the buffer, which was the squashed one.
        best = self.hud.model
        for match in _MODEL_HINT.finditer(self._chrome):
            name = _MODEL_TRIM.split(
                match.group(0).decode("utf-8", "replace"), 1
            )[0].strip(" -|·")
            if name and _model_rank(name) > _model_rank(best):
                best = name
        self.hud.model = best
        # The last match, not the first: the count only goes up, and the buffer
        # holds every value the status line has shown. Taking the first match
        # pinned the panel to the oldest one still in the window -- measured,
        # it read 25 while the live figure was 373.
        tokens = _TOKENS_HINT.findall(self._chrome)
        if tokens:
            self.hud.tokens = tokens[-1].decode("ascii", "replace").strip()

    def _track_modes(self, data):
        """Remember which private modes the agent has turned on."""
        for params, action in _MODE_CHANGE.findall(data):
            for part in params.split(b";"):
                try:
                    mode = int(part)
                except ValueError:
                    continue
                if mode in _TRACKED_MODES:
                    self._agent_modes[mode] = action == b"h"

    def _mode_restore(self):
        """Re-assert the agent's own modes after the overlay cleared them."""
        out = []
        for mode, on in self._agent_modes.items():
            out.append(b"\x1b[?%dh" % mode if on else b"\x1b[?%dl" % mode)
        return b"".join(out)

    def _mark_output(self):
        now = time.monotonic()
        if self._busy_since is None:
            self._busy_since = now
        self._last_output = now

    def _agent_is_idle(self):
        """True once the agent has produced output and then fallen silent.

        The arming condition matters in both directions: an agent that has not
        printed anything yet is starting up, not waiting at a prompt, so it
        counts as working and may be covered.
        """
        if self._last_output is None:
            return False
        return (time.monotonic() - self._last_output) >= IDLE_SILENCE

    def _capture_prompt(self, data):
        """Track the line being typed so the overlay can show what was asked."""
        submitted = self._typing.feed(data)
        if submitted:
            self.hud.prompt = submitted
            # The turn boundary for an agent that fires no hooks at all.
            self._skip_turn = False

    def _should_cover(self, now):
        """Is the agent genuinely working, unattended, right now?

        Two conditions, each there because of a way the screensaver misfires:
        recent typing (or a recent manual wake) means the user is at the
        keyboard, and the work has to be sustained rather than a stray repaint.
        """
        if self._skip_turn:
            # _wake() promises a keypress "buys reading time, not a permanent
            # dismissal", and that is still true of every other key. This one
            # is the exception the user asked for, because the case it covers
            # -- an interrupted turn the hook channel never reports -- cannot
            # be detected from the runner's side at all (D-036).
            return False
        if now < self._hold_until:
            return False
        return self._busy_since is not None and (now - self._busy_since) >= OVERLAY_DELAY

    def _sleep(self):
        """Cover the screen with the rain."""
        _debug(
            f"cover: hooks={self._hooks_speaking()} state={self._hook_state} "
            f"busy_for={time.monotonic() - (self._busy_since or 0):.2f}"
        )
        rows, cols = self.term.get_size()
        self.rain.resize(rows, cols)
        self.hud.forget()  # nothing of the panel survives an uncovered screen
        self.hud.mark_cover()  # the token baseline for "tokens while hidden"
        self.rain.set_hole(self.hud.clear_spans(rows, cols))
        self.rain.set_halo(self.hud.halo_spans(rows, cols))
        self._agent_left_alt = False
        # If the agent is running a full-screen UI it already holds the
        # alternate buffer; paint over it rather than nesting another (D-010).
        self.term.enter_overlay(take_alt=not self._agent_alt)

    def _wake(self):
        """Uncover the screen and hand the display back to the agent.

        Uncovering restarts the same clock that covered it in the first place:
        if the agent is still working when it runs out, the screensaver comes
        back. A keypress buys reading time, not a permanent dismissal.
        """
        if not self.term.in_overlay:
            return
        _debug(f"wake: hooks={self._hooks_speaking()} state={self._hook_state}")
        took_alt = self.term.took_alt
        self.term.exit_overlay(self._mode_restore())  # must precede the flush (P-203)

        repainting = self._agent_repaints or self._agent_alt
        if repainting and not self._agent_left_alt:
            # The hidden bytes are a picture being maintained, not a log, so
            # there is nothing in them for scrollback. Replaying them paints
            # stale frames over the restored screen and leaves the agent
            # drawing relative to a corrupted cursor -- measured against
            # interactive claude, the result was unreadable. Drop them and let
            # the agent redraw itself instead.
            self.log.discard_pending()
        else:
            self.log.flush_to(self.term.write)

        if repainting or not took_alt:
            self._nudge_repaint()
        self._hold_until = time.monotonic() + OVERLAY_DELAY

    def _nudge_repaint(self):
        """Make a full-screen agent redraw itself from scratch.

        A bare SIGWINCH is not enough: Node's tty stream (and so Ink, and so
        interactive claude) re-reads the winsize and emits 'resize' only when
        the dimensions actually differ, and frameworks that poll the size
        behave the same way. Measured against a fixture that mimics this, the
        screen simply stayed blank. So briefly shrink the pty by one row and
        restore it -- the kernel raises a real SIGWINCH for each change and the
        agent sees a genuine delta.
        """
        rows, cols = self.term.get_size()
        if rows > 1:
            # Shrink now, restore on a later tick. Restoring immediately loses
            # the race: the agent's handler runs after both ioctls and reads
            # back the original size, sees no delta, and skips the redraw --
            # measured, the screen stayed blank. Holding the smaller size for
            # one tick makes both transitions observable.
            ptyproxy.set_winsize(self.master_fd, rows - 1, cols)
            self._restore_size_at = time.monotonic() + REPAINT_SETTLE

        # Belt and braces for agents that watch the signal rather than the
        # size. Address the foreground group so a wrapper script's child gets
        # it too.
        try:
            pgid = os.tcgetpgrp(self.master_fd)
        except OSError:
            pgid = self.pid
        try:
            os.killpg(pgid, signal.SIGWINCH)
        except OSError:
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except OSError:
                pass

    def _apply_resize(self):
        rows, cols = self.term.get_size()
        ptyproxy.set_winsize(self.master_fd, rows, cols)
        if self.term.in_overlay:
            self.rain.resize(rows, cols)
            self.hud.forget()  # the panel moves with the terminal size
            self.term.write(CLEAR_HOME)

    # -- teardown helpers --------------------------------------------------

    def _child_is_gone(self):
        if self._status is not None:
            return True
        try:
            done, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self._status = 0
            return True
        if done:
            self._status = status  # run() reads it instead of waiting again
            return True
        return False

    def _reap(self):
        if self._status is None:
            try:
                _, self._status = os.waitpid(self.pid, 0)
            except ChildProcessError:
                self._status = 0
        return self._status

    def _drain_master(self):
        while True:
            try:
                readable, _, _ = select.select([self.master_fd], [], [], 0.02)
            except (InterruptedError, OSError):
                return
            if not readable:
                return
            data = _read(self.master_fd)
            if not data:
                return
            self._sink(data)

    def _drain_stdin(self, seconds):
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            try:
                readable, _, _ = select.select([self.term.in_fd], [], [], remaining)
            except (InterruptedError, OSError):
                return
            if readable and not _read(self.term.in_fd, 4096):
                return


def _model_rank(name):
    """How presentable a scraped model name is: spaced beats squashed."""
    if not name:
        return (0, 0)
    return (1 if " " in name else 0, len(name))


_DEBUG = os.environ.get("AMASK_DEBUG")


def _is_injected_prompt(prompt):
    """True for a `UserPromptSubmit` the harness raised, not the user (D-031)."""
    return str(prompt).lstrip().startswith(INJECTED_PROMPT_PREFIXES)


def _debug(message):
    """Append one line to the debug log, when AMASK_DEBUG names a file.

    The state machine now has three inputs -- output timing, hook events and
    the user -- and a wrong decision looks the same from outside whichever one
    caused it. This is how to tell them apart without a terminal.
    """
    if not _DEBUG:
        return
    try:
        with open(_DEBUG, "a") as handle:
            handle.write(f"{time.monotonic():10.3f} {message}\n")
    except OSError:
        pass


def _emitter_path():
    """The hook emitter shipped next to this package."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "amask-hook",
    )


def _short_path(path):
    """~-relative when it helps, so the panel shows a recognisable folder."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _read(fd, size=READ_CHUNK):
    """Read once, mapping a pty master's post-exit EIO to a clean EOF."""
    try:
        return os.read(fd, size)
    except InterruptedError:
        return b""
    except OSError:
        return b""
