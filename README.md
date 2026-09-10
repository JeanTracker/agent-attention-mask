# amask

A single-window screensaver runner that wraps a long-running AI coding agent, covers the
terminal with matrix digital rain while it works, and hands the screen back the moment you
are needed.

```
amask claude -p "refactor this"
amask codex exec "make the tests pass"
```

**It is transparent.** The agent runs on a virtual PTY, so it believes it is attached to a
real terminal. ANSI colour and interactive input work as they always did, and the exit code
is passed through unchanged.

**Scrollback stays clean.** Output produced while the screen is covered never reaches the
display; it accumulates in an anonymous spill file and is flushed into scrollback in full
when the screen is handed back. Not one line of matrix residue is left behind.

**It does not flicker.** The screen is only covered once the agent has worked for 4 seconds
without a break. Short commands and idle repaints never clear that bar.

**With `claude` it does not guess.** Instead of timing output, it lets the agent report its
own state over Claude Code hooks.

**It has no dependencies.** Python 3 standard library only.

## Installing and running

There is no packaging. Clone the repository and call `bin/amask` directly, or symlink it onto
your PATH.

```sh
./bin/amask <agent command> [args...]
ln -s "$PWD/bin/amask" ~/bin/amask   # to use it as in the examples above
```

If stdout is not a tty (a pipe or a redirection), the runner decides there is no screen to
hide and hands the process straight over with `execvp`.

macOS + iTerm2 (any xterm-compatible terminal), Python 3 (verified on 3.14.6). In the iTerm2
profile, use it with **"Save lines to scrollback in alternate screen mode" turned off** (the
default). With it on, alternate screen content is saved to scrollback too and matrix residue
is left behind.

## How it works

1. Run the agent on the slave side of a virtual PTY; the runner holds the real tty.
2. **Do not cover the screen at startup.** An interactive agent's prompt stays visible.
3. Once the agent has **worked for 4 seconds without a break** with no user input in the
   meantime, cover the screen and render digital rain. A status panel appears in the centre
   showing what work is running.
4. While covered, all of the agent's output accumulates in an anonymous spill file.
5. When any of the following happens, hand the screen back and flush the accumulated log in
   full.
   - Any keypress or mouse click (within 0.2 s)
   - The agent process exits
   - The agent produces no output for 1.5 s or more (= done, or waiting on user input)
6. **Return to step 3.** When the agent goes back to work, cover the screen again. This
   cycles until the session ends.

The same 4 seconds are counted again after you wake the screen by hand. That gives you time
to read, but covers again if the agent is still working. Typing resets the timer, so the
screen never covers mid-input. The key used to wake is not forwarded to the agent, so waking
with Enter neither submits an empty prompt nor approves a pending y/n confirmation.

### What you see

```
  > Use the Bash tool to run 'sleep 12; echo done', then
    summarise in 2 paragraphs

  working...                                            8s
  MODEL     Opus 5 (1M context)
  DIR       ~/edu/iterm-matrix
  BUFFERED  5.1KB (831 tokens)

                             press any key to return
```

The rain uses only ASCII characters found on the keyboard and falls every other column.
Half-width katakana is drawn two cells wide depending on the font and terminal settings and
so bleeds into the neighbouring column; ASCII is always exactly one cell wide, so it cannot.

The panel **paints no background.** A terminal has no alpha, so the moment you give it a
background it becomes a surface pasted over the matrix. Instead the rain steps around the
panel, and in the 2 columns / 1 row just outside it the rain is drawn only in the darkest
tail colour, so the edge fades out rather than ending.

An item whose value could not be read **loses its whole row** rather than showing `-` or `—`.
Scraping failure is the normal state, and a placeholder only advertises it. MODEL is a value
found in a string the agent prints itself, so on an agent that prints no such thing the row
is simply absent. DIR and elapsed time are always present.

`BUFFERED` measures what the overlay has hidden along two axes — bytes of terminal output,
and tokens the model has generated since the screen was covered. If the token value cannot be
read, or has not grown, the parenthesis is omitted.

The width is `clamp(cols-14, 34, 66)`; below 34 columns the panel is not drawn at all, and
below 20 rows it is drawn compact with the blank rows removed. While waiting, `working...`
becomes a grey `waiting` and the rain runs at half speed — the same fact carried on two
channels, colour and motion.

The prompt on the top line is the last line the user typed. It is held in memory only and
never written to a file.

### How it knows the agent's state

When wrapping `claude`, **it asks rather than infers.** The runner creates one FIFO and wires
up Claude Code hooks with `--settings`, so that on every event `bin/amask-hook` writes one
line to that FIFO. `UserPromptSubmit`, `PreToolUse`, `PostToolUse` and `SubagentStop` read as
working; `Notification` (a permission request, say), `Stop` and `SessionEnd` read as waiting
and hand the screen back at once.

`--settings` **loads in addition to** the user's settings rather than overriding them, so the
settings file in your home directory is neither read nor written. The hook wiring exists only
for the lifetime of the process.

With hooks present, silence is never used as evidence. Even a 10-minute tool call stays
covered, and the screen is handed back when the agent says it is done. `UserPromptSubmit`
carries the prompt verbatim, so the panel's prompt is exact too.

Another agent can report its state the same way by writing one-line JSON to the FIFO that
`AMASK_HOOK_FIFO` points at. It has to carry the `AMASK_HOOK_TOKEN` value along with it —
`{"event": "PreToolUse", "token": "<token>"}`. Events whose token does not match are ignored,
and that is the mechanism that keeps concurrently running runners from interfering with each
other.

### Without hooks

Silence means the opposite thing from one agent to the next, so what is examined is not a
single piece of output but a **continuous stretch** of it.

| Situation | Observed output | Reading |
|---|---|---|
| `claude -p "..."` working | 5.9 s of complete silence, then the answer only | silence is work |
| interactive claude working | streaming roughly every 114 ms, but pausing up to 0.8 s | activity is work |
| interactive claude at the prompt | a 60–150 byte repaint every 8–10 s | silence is waiting, a repaint is not work |

There is one rule — **if it has not said anything yet it is working; if it spoke and then
stopped it is waiting.** A stretch is the span over which output gaps stay under 1.5 s, and
that stretch has to pass 4 seconds before the screen is covered. Idle repaints cannot build
up a stretch, so the screen does not flicker. The two recalibrations it took to arrive at
this judgement are in D-013 of `.governance/DECISIONS.md`.

### Agents that repaint

For a TUI such as `claude`, output is not a log but **a picture that is maintained**. The
runner recognises such an agent (detecting cursor movement, screen clears, scroll-region
setup and the like) and does not replay the hidden output when handing the screen back —
replaying it paints over the stale screen and smears the UI. Instead it briefly changes the
pty size so the agent redraws itself. The full story, including Node-based TUIs ignoring a
`SIGWINCH` that carries the same size, is in D-010 and D-012.

A tool that only draws a progress bar with `\r` or `ESC[K` does not fall under this. It never
leaves the current line, so its output is still a log and is restored to scrollback in full.

### Timing

**`AMASK_OVERLAY_DELAY`** (default `4.0`) — cover the screen once continuous work has lasted
this long. The same value is used for the wait between waking and covering again.

**`AMASK_IDLE_SILENCE`** (default `1.5`) — without hooks, treat this much output-free time as
breaking the stretch of work.

**`AMASK_HOOK_STALL`** (default `120.0`) — if the hooks stay this quiet while working, treat
the completion event as lost and fall back to the heuristic. Does not apply to the waiting
state.

**`AMASK_DEBUG`** — if set, record every cover/uncover judgement to that file.

## Tests

```sh
python3 tests/run_all.py
```

Each suite corresponds to an SC item in `.governance/SUCCESS_CRITERIA.md` and to policy and
decision numbers.

| Suite | What it verifies |
|---|---|
| `test_phase1_passthrough.py` | SC-005, P-101 — equivalence as a transparent wrapper |
| `test_phase3_overlay.py` | SC-002, SC-004, P-103, P-202, P-203, D-008 |
| `test_phase4_idle.py` | SC-003 — silence-based idle detection and false-positive avoidance |
| `test_phase5_resources.py` | the 50 MB RAM constraint, spill file never exposed |
| `test_phase6_lifecycle.py` | deferred entry, re-entry, coexistence with a TUI (D-010, D-011) |
| `test_phase7_presentation.py` | rain glyphs and spacing, status panel, focus events (D-015–D-017) |
| `test_phase8_inputline.py` | reconstructing the line being typed — multibyte and editing keys (D-019) |
| `test_phase9_hooks.py` | hook-based state reporting and the heuristic fallback (D-025) |

The scripts in `tests/fixtures/` stand in for an agent — `chatty.py` is work that prints line
by line, `tui.py` a full-screen agent that occupies the alternate screen, `idler.py` an agent
that does nothing but repaint periodically at its prompt. The rest are in the same directory.

`tests/tap_agent.py` is not a test but an instrument. It runs an arbitrary agent on a PTY and
records its output timing; it produced the evidence behind the observation table above.

```sh
python3 tests/tap_agent.py claude
```

`tests/probe_hooks.py` is an instrument too. It wires up every hook event that is documented
and catches what actually fires and what payload arrives. This is where the evidence behind
hook-related judgements comes from.

```sh
python3 tests/probe_hooks.py --interrupt 12 "Write 800 words about rain"
```

## Modules

**`cli.py`** — the overlay ↔ passthrough state machine. Every judgement above converges here.

**`term.py`** — sole owner of real tty state. Raw mode, alternate screen, mouse tracking, and
restoration on every exit path.

**`ptyproxy.py`** — PTY creation, winsize propagation, exit code translation.

**`logbuf.py`** — lossless log buffer backed by an anonymous spill file.

**`rain.py`** — the built-in digital rain renderer (repaints changed cells only).

**`hud.py`** — the status panel.

**`inputline.py`** — reconstruction of the line the user is typing.

**`hooks.py`** — FIFO creation and Claude Code hook wiring. Cleaning up FIFOs left behind by
dead runners happens here too.

The procedure for changing the code is in `CONTRIBUTING.md`.

The reasoning behind design judgements is recorded in `.governance/DECISIONS.md` and
`.governance/ASSUMPTIONS_AND_HYPOTHESES.md`, and measurements in
`.governance/PROJECT_STATE.md` with their dates.
There is no licence file yet.
