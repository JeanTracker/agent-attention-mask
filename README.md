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

Python 3 (verified on 3.14.6) and an xterm-compatible terminal; macOS + iTerm2 is what this
is tested on. Nothing else — the standard library is the whole dependency list, and there is
no packaging step. The clone is the installation.

```sh
git clone https://github.com/JeanTracker/agent-attention-mask.git amask && cd amask
./bin/amask claude
```

To call it from anywhere, symlink the launcher into a directory already on your PATH. On
macOS `~/.local/bin` usually is and `~/bin` usually is not, so check before you pick one.

```sh
ln -s "$PWD/bin/amask" ~/.local/bin/amask
```

Leave the clone where it is. The launcher resolves the symlink back to the clone to find
both the package and the hook emitter, so moving the clone breaks the link.

If stdout is not a tty (a pipe or a redirection), the runner decides there is no screen to
hide and hands the process straight over with `execvp`.

### Wrapping `claude` by default

To have every interactive session run covered without typing `amask` each time, alias it.
What the alias should say depends on what `claude` already is, so ask first:

```sh
type claude
```

**If the answer is a path**, the plain alias works. bash, zsh and ksh all take the same
line; only the file it belongs in differs — `~/.zshrc`, `~/.bashrc` on Linux, or
`~/.bash_profile` for the login shells macOS Terminal starts.

```sh
alias claude='amask claude'
```

**If the answer is an alias of its own**, you are on an older Claude Code install, which
wires up `alias claude=~/.claude/local/claude` because that path is not on PATH.
Overwriting it leaves amask calling a `claude` that PATH cannot find, so pass the path
through instead — and carry over any flags the old alias had, because losing them is silent:

```sh
alias claude='amask ~/.claude/local/claude'
```

Either way the argument's basename has to stay `claude`. That is what the runner matches on
before it injects its hook settings, and a name that does not match drops the session to
the timing heuristics. `command claude` and `\claude` still run the agent unwrapped when
you want it.

What to avoid is a wrapper script named `claude` placed earlier on PATH. The runner launches
the agent with `execvp`, which resolves the name `claude` through PATH again, finds the
wrapper, and calls back into amask — forever. A shim that execs an absolute path does work,
and covers the non-interactive shells an alias never reaches; the alias is just the version
with less to get wrong.

### The iTerm2 scrollback setting

**Turning off "Save lines to scrollback in alternate screen mode" in the iTerm2 profile is
recommended. The default is on, so you have to turn it off yourself** — the registered
default in 3.7.0 is on, and that value applies whenever the profile has no such key.

The runner itself does not dirty scrollback regardless of this setting. The overlay emits
neither a newline nor `ESC[2J`, so it never creates a line that can be pushed out of the
alternate screen (D-037). Measurement confirms no matrix residue is left even with this
option on.

The reason to turn it off is **the agent's side**. A full-screen agent such as interactive
`claude` redraws the screen with `ESC[2J` on every frame, and with this option on iTerm2 puts
that screen into scrollback before clearing it. In one measurement, some 80 frames piled up
over 9 seconds. This happens without the runner too, so the runner cannot prevent it — but
unchecking this box makes it go away as well.

### Uninstalling

Remove the symlink, remove the alias line from your shell's rc file, delete the clone.
Nothing was registered anywhere else: the hook settings are handed to Claude Code on its
own command line for that one run and never written into `~/.claude/settings.json`, and
the FIFO each runner opens is unlinked when it exits. The one thing that outlives a
session is `~/.amask` — the defaults saved by `--config` and the control sockets — and
`rm -rf ~/.amask` is safe once nothing is running.

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

**Pressing `q` while covered stops it covering again for that turn.** That holds for the
whole request, including any subagent it started: work reported after the skip -- a
subagent's tool calls, a background task announcing that it finished -- does not bring the
rain back. Submitting the next prompt yourself lifts it automatically. This key is what you
want when you have interrupted a response — an interrupt fires no hook at all, so the
runner has no way to know the agent stopped and mistakes it for working for up to two
minutes (`AMASK_HOOK_STALL`). When that state is suspected, the panel's hint changes to
`idle? q to skip now`. Like every other wake key, `q` is not forwarded to the agent.

### What you see

```
  > Use the Bash tool to run 'sleep 12; echo done', then
    summarise in 2 paragraphs

  working...                                            8s
  MODEL     Opus 5 (1M context)
  DIR       ~/edu/iterm-matrix
  BUFFERED  5.1KB (831 tokens)

                             press any key to return
                                 q to skip this turn
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

These three env values set what a session *starts* with. From then on the same three can be
retuned per session over the control socket below, which overrides the env value for that
runner only and is never written to disk — a new session starts from the env again.

**`AMASK_RUN_DIR`** — where the control socket and its discovery file live. Defaults to
`~/.amask/run`.

**`AMASK_CONFIG`** — the stored defaults file. Defaults to `~/.amask/config.json`.

### Watching and steering a session from outside

Each runner opens a Unix socket so something other than the terminal it is running in can
see what it is doing. `amask --top` is the view onto it — every session on one screen,
refreshed twice a second:

```
 amask  세션 3개  (2개 선택)
   PID    화면  상태           덮기  폴더                  프롬프트
*  23875  덮임  작업           4s    amask                 기본 설정 기능 넣어줘
   24110  열림  대기·건너뜀    20s   release-notes         릴리스 노트 초안 써줘
*  24777  덮임  작업·유휴의심  8s    agent-attention-mask  테스트 전부 돌려줘
기본설정 overlay_delay=8 -> 2개 세션에 적용
space 선택  a 전체  enter 상세  d 기본  s 건너뜀  w 깨우기  +/- 덮기  [/] 유휴  r 갱신  q 종료
```

Every column is as wide as what is actually in it: `상태` grows when a session has flags
hung off it, `폴더` with the name of the directory, and the prompt takes what is left. A
narrow window shrinks them back rather than cutting either off mid-word (D-046).

`space` marks a session (`a` marks them all) and every command then applies to the marked
ones: `s` skips their turn (the same thing as pressing `q` in each), `w` wakes them, `d`
pushes the stored defaults into them, `+`/`-` move their cover delay and `[`/`]` their idle
threshold. With nothing marked, the highlighted row is the target. It is `curses` rather than
a window because a toolkit would be a dependency (D-006); a native app can be written against
the same socket.

`enter` opens the highlighted session on its own screen — the agent's session id, the working
directory in full, when it started, how long it has been working, how much output is hidden,
the model and token count it is reporting, all three timing values and the prompt in full
rather than folded to one line (D-043, D-044). It keeps refreshing while it
is open, and the same commands work there, except that they apply to the session you are
looking at and ignore the marks. `esc`, `enter` or backspace goes back to the list; a session
that exits while you are reading it drops you back with a note.

Neither screen cuts anything off on a short window (D-045). The list scrolls to keep the
cursor in view, the pane scrolls with `↑↓`/`jk`, `space`/`b` by the page and `g`/`G` to
either end, and the title says how much is off-screen in each direction (`↑3 ↓12`) so a
short window is not mistaken for a short list.

```
 amask  세션 23875  [덮임]

  PID           23875
  명령          claude --resume
  세션 id       sess-abc
  폴더          /home/me/work
  시작          2026-09-21 14:02:11 (3분 전)

  화면          덮임 (레인이 올라가 있다)
  상태          작업
  작업 시간     54.2s
  특이사항      훅은 작업 중이라는데 출력이 끊겼다
  모델          Opus 5
  숨긴 출력     43K (43869B, 덮개가 걷히면 그대로 나온다)

  덮기 지연     4s
  유휴 임계     1.5s
  훅 정체       120s

  프롬프트      기본 설정 기능 넣어줘
↑↓ 스크롤  esc/enter 목록  s 건너뜀  w 깨우기  +/- 덮기  [/] 유휴  d 기본  r 갱신  q 종료
```

### The stored defaults

`amask --config` is what a *new* session starts with, kept in `~/.amask/config.json`:

```sh
amask --config                        # stored values, and what a new session gets
amask --config overlay_delay=8        # store one
amask --config overlay_delay=         # forget it again
```

Narrowest wins: the shipped default, then this file, then an env knob (it belongs to one
invocation), then a session's own `set` over the socket. Changing the file does not reach
sessions that are already running — `d` in `--top` is the deliberate act that does, on as
many sessions as you marked.

The same channel answers one question at a time, for scripts and for a quick look:

```sh
amask --ls                            # pid, agent command, session id
amask --ctl last status               # covered? working? what was asked?
amask --ctl last set overlay_delay=8  # retune this session, live
amask --ctl last skip                 # same as pressing q
amask --ctl last wake                 # same as touching a key
```

`--ctl` takes a pid or `last`. `status` reports whether the screen is covered right now, how
long the agent has been working, the agent's own session id, the question the user asked, how
many bytes are being held back, the model and token count scraped from the agent's own status
line (empty for an agent that prints neither), and the session's current timing values.

`examples/watch_sessions.py` is the smallest external client: it lists every session, prints
one line each, and can push a `set`/`skip`/`wake` to all of them. Its bottom comment is the
whole protocol — read `~/.amask/run/amask-ctl-<pid>.json` for the socket path and token,
connect, send one JSON object per line, read one back — so a client in any language is the
same twenty lines.

The socket lives in `~/.amask/run` at 0600 inside a 0700 directory, beside a discovery file
carrying the token every request has to present. `skip` is refused when there is no turn to
skip, and there is deliberately no way to cover the screen or to type at the agent from
outside. A runner that cannot create the socket simply runs without one.

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

**`control.py`** — the control socket: discovery, the token, and the transport. What the
commands *mean* stays in `cli.py`.

**`config.py`** — the stored defaults, and the precedence between them, the env knobs and a
session's own values.

**`top.py`** — the `--top` view. It owns nothing: it polls the socket and sends the same
commands a person could type.

The procedure for changing the code is in `CONTRIBUTING.md`.

The reasoning behind design judgements is recorded in `.governance/DECISIONS.md` and
`.governance/ASSUMPTIONS_AND_HYPOTHESES.md`, and measurements in
`.governance/PROJECT_STATE.md` with their dates.
There is no licence file yet.
