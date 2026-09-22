"""The cover/uncover decision, with the clock as an argument (D-054).

The judgement the runner exists to make -- is the agent working, unattended,
right now? -- used to live inside the select loop, reading `time.monotonic()`
at a dozen points and tangled with the pty, the rain and the panel. Testing it
therefore meant starting a real runner against a real agent on a real pty and
waiting in real seconds; the suite spent twelve minutes doing almost nothing
but sleeping.

Nothing here reads the clock, opens anything or draws anything. Every method
takes `now`, so a test can hand it 0.0, 1.4 and 9.9 in consecutive calls and
check the same state machine the product runs, in milliseconds.

What this deliberately does *not* replace: the terminal-level guarantees
(P-103's scrollback, SC-002's latency, the alternate-screen sequences) are
about real bytes reaching a real terminal, and a fake clock says nothing about
them. Those keep their pty tests.
"""

from . import hooks


class Judge:
    """The state behind `should_cover`, and the events that move it.

    `settings` is the live `Settings` object, so a retune over the control
    socket is picked up on the next call rather than captured here.
    `hooks_live` is a zero-argument callable saying whether the hook channel
    is open at all -- it becomes true partway through startup, so the answer
    cannot be frozen at construction.
    """

    def __init__(self, settings, hooks_live=lambda: False):
        self.settings = settings
        self.hooks_live = hooks_live
        self.last_output = None  # None until the agent has said anything
        self.busy_since = None  # start of the current run of work
        self.hook_state = None  # None until the agent reports something
        self.hook_at = 0.0
        # Set by the skip key or the socket, cleared when the next prompt is
        # submitted: the user has said this turn is not worth covering.
        self.skip_turn = False
        self.hold_until = 0.0

    # -- what happened -----------------------------------------------------

    def started(self, now):
        """The runner just spawned the agent.

        The work clock starts here, because an agent that has not spoken yet
        is working -- a one-shot run is silent for its whole thinking time.
        """
        self.busy_since = now
        self.hold_until = now + self.settings.overlay_delay

    def output(self, now):
        """The agent produced bytes."""
        if self.busy_since is None:
            self.busy_since = now
        self.last_output = now

    def hook(self, meaning, now):
        """A hook event the runner has already accepted as the main thread's.

        Returns True when the runner should wake -- the turn ended or the
        agent is asking the user something.
        """
        self.hook_state = meaning
        self.hook_at = now
        if meaning == hooks.BUSY:
            if self.busy_since is None:
                self.busy_since = self.hook_at
            return False
        self.busy_since = None
        return True

    def turn_opened(self):
        """A prompt the *user* submitted: whatever they skipped is over."""
        self.skip_turn = False

    def skip(self):
        """The skip key, or the socket's `skip`."""
        self.skip_turn = True

    def accepts_skip(self):
        """Whether there is a turn to skip at all.

        During a WAITING span the next prompt would clear the flag anyway, so
        accepting it there would report a success that quietly evaporates.
        """
        return self.busy_since is not None

    def held(self, now):
        """The user is at the keyboard, or just uncovered the screen by hand."""
        self.hold_until = now + self.settings.overlay_delay

    # -- what it means -----------------------------------------------------

    def hooks_speaking(self, now):
        """Whether the hook channel is the thing to believe right now.

        A latched BUSY goes stale: if the agent said it was working and then
        said nothing for `hook_stall`, the completion was lost and the timing
        heuristic takes over again. WAITING deliberately does not expire
        (D-030) -- nothing has happened, which is the point.
        """
        if not self.hooks_live() or self.hook_state is None:
            return False
        if self.hook_state != hooks.BUSY:
            return True
        return (now - self.hook_at) < self.settings.hook_stall

    def agent_is_idle(self, now):
        """True once the agent has produced output and then fallen silent.

        The arming condition matters in both directions: an agent that has not
        printed anything yet is starting up, not waiting at a prompt, so it
        counts as working and may be covered.
        """
        if self.last_output is None:
            return False
        return (now - self.last_output) >= self.settings.idle_silence

    def idle(self, now):
        """The tick's reading of the agent, hooks first (D-025).

        Clears the work clock when idle, so the next run of output starts a
        fresh one -- and so an idle prompt's own repaints cannot keep an
        already-finished turn looking busy.
        """
        if self.hooks_speaking(now):
            idle = self.hook_state != hooks.BUSY
        else:
            idle = self.agent_is_idle(now)
        if idle:
            self.busy_since = None
        return idle

    def should_cover(self, now):
        """Is the agent genuinely working, unattended, right now?

        Two conditions, each there because of a way the screensaver misfires:
        recent typing (or a recent manual wake) means the user is at the
        keyboard, and the work has to be sustained rather than a stray repaint.
        """
        if self.skip_turn:
            # A keypress buys reading time, not a permanent dismissal -- of
            # every other key. This one is the exception the user asked for,
            # because the case it covers (an interrupted turn the hook channel
            # never reports) cannot be detected from this side at all (D-036).
            return False
        if now < self.hold_until:
            return False
        return self.busy_since is not None and (
            (now - self.busy_since) >= self.settings.overlay_delay
        )

    def idle_suspected(self, now, after):
        """Hooks say working, but the output stopped: a hint, not a decision.

        `after` is the threshold, which is deliberately not one of the three
        tunable settings (D-036) -- it changes emphasis and nothing else.
        """
        if self.hook_state != hooks.BUSY or self.last_output is None:
            return False
        return (now - self.last_output) >= after

    def busy_seconds(self, now):
        return (now - self.busy_since) if self.busy_since else 0.0
