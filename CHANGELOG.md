# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The reasoning behind each judgement is recorded as a numbered entry in
`.governance/DECISIONS.md`; this file only says what changed.

## [Unreleased]

### Added

- **The stored defaults are editable from `--top`.** `c` opens them on their own screen:
  `↑`/`↓` pick one of the three timing values, `+`/`-` move it, `0` forgets it again and
  `enter` writes the file. `esc` leaves without writing. As before, the file governs new
  sessions only -- `d` in the list is still what pushes values into running ones (D-050).
- **`?` in `--top` opens a key reference.** Every key with a sentence on what it does,
  grouped by screen, plus what `cover`, `cover delay`, `idle threshold`, `hook stall` and
  `stored defaults` mean. It scrolls, and `?` or `esc` puts it away again (D-051).

### Changed

- **The `--top` hint bar reads `what it does(key)`** -- `skip(s)`, `push defaults(d)` -- with
  the key in bold, grouped by purpose with `│`, and laid out for the window it is in: fuller
  wording when there is room, and the keys it can spare dropped when there is not. `help(?)`
  and `quit(q)` survive any width (D-052).
- **The cover/uncover state machine moved into `amask/judge.py`, with time as an argument.**
  No behaviour change; what changes is that it can be tested without a terminal. The suite
  is now two tiers: `tests/run_fast.py` (~2s, no pty) and `tests/run_all.py` (~12min, real
  runners). CI runs the fast tier on pull requests and the full one on `master`; contributors
  run the full one before pushing (D-054).
- CI runs the suites on Linux and macOS against Python 3.13 only. The suite drives real
  ptys for minutes a job, and the axis that has ever caught anything twice is the OS; the
  3.11 floor is now checked by hand (D-053).

## [0.1.0] - 2026-09-22

First release. The runner, the overlay and the control channel are in place, and the
nine phase suites pass on Linux and macOS.

### Added

- **The runner.** Wraps an agent on a virtual PTY, covers the terminal with matrix digital
  rain once the agent has worked for 4 seconds without a break, and hands the screen back on
  any keypress, on the agent exiting, or on 1.5 s of silence. Colour, interactive input and
  the exit code pass through unchanged; a non-tty stdout hands the process straight over.
- **Lossless output.** Everything the agent writes while covered accumulates in an anonymous
  spill file and is flushed into scrollback in full when the screen returns. The overlay
  emits neither a newline nor `ESC[2J`, so it cannot dirty scrollback itself.
- **Hook-based state.** When wrapping `claude`, the agent reports its own state over Claude
  Code hooks through a per-session FIFO, so a long tool call stays covered and the screen
  returns when the agent says it is done. Any other agent can report the same way. Without
  hooks, a timing heuristic reads a *stretch* of output rather than a single gap.
- **`q` skips the turn.** Pressing `q` while covered stops the rain for that whole request,
  including work its subagents report afterwards. The next prompt you submit lifts it.
- **Status panel.** Prompt, state, elapsed time, model, working directory and how much output
  is being held back, drawn without a background so the rain shows through.
- **Control socket.** Each runner serves `status`, `get`, `set`, `skip` and `wake` over a Unix
  socket, 0600 inside a 0700 directory and gated on a per-session token.
- **`amask --top`.** Every session on one screen, refreshed twice a second: mark several with
  `space`, then skip, wake, retune or push the stored defaults to all of them at once.
  `enter` opens one session in full, with its own scrolling.
- **`amask --ls`, `--ctl`, `--config`, `--version`.** The one-shot side of the same channel,
  and the stored defaults that new sessions start from.
- **TUI agents.** A full-screen agent is detected and redrawn by a brief pty resize instead of
  having its hidden output replayed over a stale screen.
- **CI.** The phase suites run on Linux and macOS against Python 3.11, 3.12 and 3.13.
- **Licence.** MIT.

[Unreleased]: https://github.com/JeanTracker/agent-attention-mask/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JeanTracker/agent-attention-mask/releases/tag/v0.1.0
