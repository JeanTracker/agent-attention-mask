# CLAUDE.md

amask — a PTY-wrapping screensaver runner. Product behaviour and module layout are in
`README.md`, the procedure for making changes in `CONTRIBUTING.md`. This file narrows that
down to what you need to know at the start of a session.

## Principles

- **Standard library only** (D-006). Adding a third-party dependency is a decision that
  requires a D entry.
- **Scrollback pollution is a product failure** (P-103). If matrix glyphs leak into the
  normal buffer, the change is wrong.
- **Agent output must never be lost** (SC-004). The judgement to switch to a ring buffer was
  already rejected in D-007.
- Hooks come first for state detection; timing heuristics are the fallback (D-025). Do not
  invert that order.

## Source-of-truth order

1. `.governance/DECISIONS.md` — the final basis for design judgements
2. `.governance/SUCCESS_CRITERIA.md`, `PRODUCT_POLICY.md`, `ENGINEERING_POLICY.md`
3. `README.md`
4. `.governance/AGENT_AUTONOMY.md`, `CURRENT_GOAL.md`

Number 4 is the brief from the outset and has not been kept up to date. Sentences recommending
Go, or calling a cmatrix binary, are still in there, but D-005 and D-006 overturned them. On a
conflict, number 1 wins.

`.governance/PROJECT_STATE.md` is an append log. The summary block at the top is stale, so
read it from the tail, and append only at the tail when updating it too.

`.ai_pipeline/` holds pipeline output and is gitignored. It is overwritten on every run, so do
not cite anything in it as evidence.

## Recording obligations

- Proceed on implementation detail by your own judgement.
- Leave decisions someone will later ask "why?" about — dependencies, heuristics — as a new D
  entry in `DECISIONS.md`. The number is the last entry +1 —
  `grep -o 'D-0[0-9][0-9]' .governance/DECISIONS.md | sort -u | tail -1`.
  Do not edit existing entries, and when departing, write down which D you are departing from.
- Do not settle UX specification changes, SC edits or scrollback policy changes alone.
  Register them in `HUMAN_REVIEW_QUEUE.md` and get on with the work that does not depend on
  that decision.
- Leave verification results at the end of `PROJECT_STATE.md` with the date.

## Verification

```sh
python3 tests/run_fast.py    # ~2s, no terminal needed
python3 tests/run_all.py     # ~12min, real ptys -- before you push
```

10 phase suites in the full run. If you changed code, use the fast tier as you go and the
full one before pushing, and report the result. CI runs only the fast tier on a pull
request (D-054), so the terminal-layer suites -- P-103's scrollback, SC-002's latency --
are checked on this machine or not at all. If you need a real agent's timing or hook
payloads, build the observations first with `tests/tap_agent.py` / `tests/probe_hooks.py`.

## Watch out

`./ai_pipeline.sh` is not read-only. Stages 3 and 5 modify the working tree directly with
`claude -p --permission-mode auto` and update `PROJECT_STATE.md`. Do not run it unless the
user explicitly asks.

If you changed a constant's default in the code, fix the numbers written in README's
"Configuration" table to match. Those constants are the three defined with `_seconds(...)` in
`amask/cli.py`: `IDLE_SILENCE`, `HOOK_STALL` and `OVERLAY_DELAY`.
