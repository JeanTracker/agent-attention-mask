# amask

[![CI](https://github.com/JeanTracker/agent-attention-mask/actions/workflows/ci.yml/badge.svg)](https://github.com/JeanTracker/agent-attention-mask/actions/workflows/ci.yml)

A screensaver runner for long-running AI coding agents. It wraps the agent, covers the
terminal with matrix digital rain while it works, and hands the screen back the moment you
are needed.

```sh
amask claude -p "refactor this"
amask codex exec "make the tests pass"
```

```
  > Use the Bash tool to run 'sleep 12; echo done', then
    summarise in 2 paragraphs

  working...                                            8s
  MODEL     Opus 5 (1M context)
  DIR       ~/edu/amask
  BUFFERED  5.1KB (831 tokens)

                             press any key to return
                                 q to skip this turn
```

- **Transparent.** The agent runs on a virtual PTY and believes it is on a real terminal.
  Colour, interactive input and the exit code pass through unchanged.
- **Scrollback stays clean.** Output produced while the screen is covered is held in an
  anonymous spill file and flushed into scrollback in full when the screen comes back. No
  matrix residue is left behind.
- **No flicker.** The screen is covered only once the agent has worked for 4 seconds without
  a break. Short commands and idle repaints never clear that bar.
- **With `claude` it does not guess.** The agent reports its own state over Claude Code
  hooks, so even a 10-minute tool call stays covered and the screen returns when the agent
  says it is done.
- **No dependencies.** Python 3 standard library only.

## Install

Python 3.11+ and an xterm-compatible terminal. There is no packaging step -- the clone is the
installation.

```sh
git clone https://github.com/JeanTracker/agent-attention-mask.git amask && cd amask
./bin/amask claude
```

To call it from anywhere, symlink the launcher into a directory on your PATH:

```sh
ln -s "$PWD/bin/amask" ~/.local/bin/amask
```

Leave the clone where it is: the launcher resolves the symlink back to it to find both the
package and the hook emitter.

If stdout is not a tty, there is no screen to hide, and the runner hands the process straight
over with `execvp`.

## Usage

```sh
amask <agent command> [args...]   # run an agent under the rain
amask --top                       # watch and steer every session on one screen
amask --ls                        # list the running sessions
amask --config [key=value...]     # show or set the stored defaults
amask --ctl <pid|last> <command>  # status | get | set | skip | wake
amask --version
```

**Pressing `q` while covered stops it covering again for that turn** -- including any subagent
that turn started. Submitting your next prompt lifts it. Any other key just hands the screen
back; the key used to wake is not forwarded to the agent, so waking with Enter neither
submits an empty prompt nor approves a pending confirmation.

### Wrapping `claude` by default

To have every session run covered without typing `amask`, alias it. What the alias should say
depends on what `claude` already is, so ask first with `type claude`.

**If the answer is a path**, the plain alias works (bash, zsh and ksh all take the same line;
only the rc file differs -- `~/.zshrc`, `~/.bashrc`, or `~/.bash_profile` for the login shell
macOS Terminal starts):

```sh
alias claude='amask claude'
```

**If the answer is an alias of its own**, you are on an older Claude Code install that points
`claude` at a path not on PATH. Pass that path through instead, carrying over any flags the
old alias had:

```sh
alias claude='amask ~/.claude/local/claude'
```

Either way the argument's basename has to stay `claude`: that is what the runner matches on
before it wires up its hooks, and a name that does not match falls back to timing heuristics.
`command claude` still runs the agent unwrapped.

Avoid a wrapper *script* named `claude` earlier on PATH. The runner launches the agent with
`execvp`, which resolves the name through PATH again, finds the wrapper and calls back into
amask forever.

## How it works

1. Run the agent on the slave side of a virtual PTY; the runner holds the real tty.
2. Do not cover anything at startup -- an interactive agent's prompt stays visible.
3. Once the agent has worked for 4 seconds without a break, and with no user input in the
   meantime, cover the screen and render the rain with a status panel in the centre.
4. While covered, all of the agent's output accumulates in an anonymous spill file.
5. Hand the screen back, and flush that log in full, on any keypress or click (within 0.2 s),
   when the agent exits, or when it falls silent for 1.5 s (done, or waiting on you).
6. Back to step 3, until the session ends.

Typing resets the timer, so the screen never covers mid-input. The same 4 seconds are counted
again after you wake the screen by hand.

### Knowing the agent's state

When wrapping `claude`, the runner asks rather than infers. It creates one FIFO and wires up
hooks with `--settings`, so `bin/amask-hook` writes one line per event. `UserPromptSubmit`,
`PreToolUse`, `PostToolUse` and `SubagentStop` read as working; `Notification` (a permission
request, say), `Stop` and `SessionEnd` read as waiting and hand the screen back at once.
`--settings` loads *in addition to* your own settings, so `~/.claude/settings.json` is
neither read nor written, and the wiring lives only for that one process.

Any other agent can report the same way by writing one-line JSON to the FIFO named by
`AMASK_HOOK_FIFO`, carrying the `AMASK_HOOK_TOKEN` value:
`{"event": "PreToolUse", "token": "<token>"}`. Events with the wrong token are ignored, which
is what keeps concurrent runners out of each other's way.

Without hooks, silence is read as a *stretch* rather than a single gap, because silence means
opposite things from one agent to the next:

| Situation | Observed output | Reading |
|---|---|---|
| `claude -p "..."` working | 5.9 s of silence, then the answer | silence is work |
| interactive claude working | streaming every ~114 ms, pausing up to 0.8 s | activity is work |
| interactive claude at the prompt | a 60-150 byte repaint every 8-10 s | a repaint is not work |

The rule: if it has not said anything yet it is working; if it spoke and then stopped it is
waiting. Idle repaints cannot build up a stretch, so the screen does not flicker.

For a TUI agent, output is a picture being maintained rather than a log. The runner detects
that (cursor movement, screen clears, scroll regions) and, instead of replaying hidden output
over a stale screen, briefly changes the pty size so the agent redraws itself.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AMASK_OVERLAY_DELAY` | `4.0` | cover once continuous work has lasted this long |
| `AMASK_IDLE_SILENCE` | `1.5` | without hooks, this much silence breaks a stretch of work |
| `AMASK_HOOK_STALL` | `120.0` | if hooks go this quiet while working, fall back to the heuristic |
| `AMASK_DEBUG` | -- | record every cover/uncover judgement to this file |
| `AMASK_RUN_DIR` | `~/.amask/run` | control socket and its discovery file |
| `AMASK_CONFIG` | `~/.amask/config.json` | the stored defaults |

The first three are what a session *starts* with; each can then be retuned live per session
over the control socket, which is never written to disk.

`amask --config` is what a *new* session starts with:

```sh
amask --config                        # stored values, and what a new session gets
amask --config overlay_delay=8        # store one
amask --config overlay_delay=         # forget it again
```

Narrowest wins: the shipped default, then this file, then an env knob, then a session's own
`set` over the socket. Changing the file does not reach sessions that are already running --
`d` in `--top` is the deliberate act that does.

## Watching and steering sessions

Each runner opens a Unix socket, so something other than the terminal it runs in can see what
it is doing. `amask --top` is the view onto it, refreshed twice a second:

```
 amask  3 sessions  (2 marked)
   PID    Screen   State          Cover  Folder                Prompt
*  23875  covered  working        4s     amask                 add the config feature
   24110  open     waiting·skip   20s    release-notes         draft the release notes
*  24777  covered  working·idle?  8s     agent-attention-mask  run the whole suite
defaults overlay_delay=8 -> applied to 2 sessions
space mark  a all  enter detail  d default  s skip  w wake  +/- cover  [/] idle  r poll  q quit
```

Every column is as wide as what is actually in it, and the prompt takes what is left. `space`
marks a session (`a` marks them all) and every command then applies to the marked ones: `s`
skips their turn, `w` wakes them, `d` pushes the stored defaults into them, `+`/`-` move their
cover delay and `[`/`]` their idle threshold. With nothing marked, the highlighted row is the
target.

`enter` opens one session on its own screen -- session id, working directory, how long it has
been working, how much output is hidden, all three timing values and the prompt in full. The
same commands work there and apply only to the session you are looking at.

```
 amask  session 23875  [covered]

  PID           23875
  Command       claude --resume
  Session id    sess-abc
  Folder        /home/me/work
  Started       2026-09-22 10:54:29 (3m ago)

  Screen        covered (the rain is up)
  State         working
  Working       54.2s
  Notes         hooks say working, but the output stopped
  Model         Opus 5
  Hidden        43K (43869B, all of it comes back when the cover lifts)

  Cover delay   4s
  Idle silence  1.5s
  Hook stall    120s

  Prompt        add the config feature
↑↓ scroll  esc/enter list  s skip  w wake  +/- cover  [/] idle  d default  r poll  q quit
```

The same channel answers one question at a time, for scripts:

```sh
amask --ls                            # pid, agent command, session id
amask --ctl last status               # covered? working? what was asked?
amask --ctl last set overlay_delay=8  # retune this session, live
amask --ctl last skip                 # same as pressing q
amask --ctl last wake                 # same as touching a key
```

`examples/watch_sessions.py` is the smallest external client, and its closing comment is the
whole protocol: read `~/.amask/run/amask-ctl-<pid>.json` for the socket path and token,
connect, send one JSON object per line, read one back. The socket is 0600 inside a 0700
directory, `skip` is refused when there is no turn to skip, and there is deliberately no way
to cover the screen or to type at the agent from outside.

## Notes for iTerm2

Turning off **"Save lines to scrollback in alternate screen mode"** in the profile is
recommended; the default is on, so you have to do it yourself.

The runner does not dirty scrollback either way -- the overlay emits neither a newline nor
`ESC[2J`, so it never creates a line that can be pushed out of the alternate screen. The
reason to turn the option off is the *agent's* side: a full-screen agent such as interactive
`claude` clears the screen on every frame, and with this option on iTerm2 files each of those
frames into scrollback (some 80 frames over 9 seconds, in one measurement). That happens with
or without amask.

## Uninstall

Remove the symlink, remove the alias from your rc file, delete the clone. Nothing is
registered anywhere else: hook settings are passed on Claude Code's command line for that one
run, and each runner's FIFO is unlinked when it exits. The one thing that outlives a session
is `~/.amask` -- the `--config` defaults and the control sockets -- and `rm -rf ~/.amask` is
safe once nothing is running.

## Development

```sh
python3 tests/run_all.py
```

Nine phase suites, driving real ptys against fixture agents in `tests/fixtures/`. CI runs
them on Linux and macOS against Python 3.11-3.13.

`CONTRIBUTING.md` has the procedure for changing the code and the module map.
`.governance/DECISIONS.md` records why the design is the way it is -- every judgement above
traces back to a numbered entry there -- and `.governance/PROJECT_STATE.md` carries the
measurements with their dates.

Release notes are in `CHANGELOG.md`.

## Licence

Not yet chosen.
