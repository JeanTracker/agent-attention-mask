# Contributing

There is a procedure to go through every time you change a line of code: work out which
authority tier the change falls under, implement it, get `tests/run_all.py` passing, and
record the reasoning under `.governance/`. Four steps.
The repository's module layout is in the "Modules" section of `README.md`, and the document
precedence in "Source-of-truth order" in `CLAUDE.md`.

## Work out the tier first

`.governance/DECISION_RULES.md` defines three tiers. The verdict changes the procedure, so
reach it before opening the code.

**AUTONOMOUS** — implementation detail: the PTY control loop, buffer management, rendering,
terminal sequence optimisation. Just do it.

**AUTONOMOUS_WITH_RECORD** — decisions someone will later ask "why?" about, such as adding a
third-party dependency or changing a state-detection heuristic. Do it, then append a D entry
to `.governance/DECISIONS.md`.

**HUMAN_REQUIRED** — changing the UX specification, editing `SUCCESS_CRITERIA.md`, changing
the scrollback preservation policy. Do not settle these alone; register an HRQ entry in
`.governance/HUMAN_REVIEW_QUEUE.md`, then get on with the work that does not depend on that
decision. The closed HRQ-001 and HRQ-002 are examples of the format.

Destroying system integrity, security risks and credential exposure are FORBIDDEN. Stop
immediately.

## What not to break while implementing

Dependencies are standard library only (D-006). By the time you need a `pip install` the
change is already AUTONOMOUS_WITH_RECORD, and the D entry has to say why the standard library
will not do.

If even one line of matrix residue is left in scrollback, the product has failed (P-103). If
you touched the rain output path, `tests/test_phase3_overlay.py` — which checks that
condition — must pass.

## Tests

```sh
python3 tests/run_all.py
```

It runs the 8 phase suites in order and exits non-zero if any one of them fails. Add an entry
to the relevant phase suite for new behaviour, and if you need something to stand in for an
agent, add a fixture under `tests/fixtures/` — `streamer.py`, for instance, is the fixture
that reproduces real claude's streaming intervals and growing token count to pin the D-023
and D-024 regressions.

If you need a real agent's timing or hook payloads, build the observations with an instrument
before writing the test.

```sh
python3 tests/tap_agent.py claude
python3 tests/probe_hooks.py --interrupt 12 "Write 800 words about rain"
```

## Documents that have to change with the code

Take changing a single timing constant as the example — say you adjust the 1.5 s default of
`AMASK_IDLE_SILENCE`. The constant is `IDLE_SILENCE` in `amask/cli.py`, and fixing only that
leaves three places out of step.
README's "Configuration" table writes the default out as a number, and the value 1.5 s is itself
the conclusion of D-023.

So the order is code → the README default → a new D entry in `DECISIONS.md`. Existing D
entries are not edited. Even when deliberately departing from an earlier decision, as D-028
does, take a new number and say in the body which decision you are departing from.
The number is the last entry +1.

```sh
grep -o 'D-0[0-9][0-9]' .governance/DECISIONS.md | sort -u | tail -1
```

Finally, append this change's date, reasoning and verification result to the end of
`PROJECT_STATE.md`. That file is an append log, not a status board to overwrite. The summary
block at the top stopped being updated long ago, so leave it alone and add only at the tail.

If you touched hook-related code, start by confirming the payloads are unchanged. Event names
and fields change quietly from release to release, so match the installed binary rather than
the documentation — run `tests/probe_hooks.py`, see what actually arrives, and leave the
result in `PROJECT_STATE.md` along with the version you observed.

## Running it through the pipeline

```sh
./ai_pipeline.sh "the goal you want implemented"
```

With the argument omitted it uses `.governance/CURRENT_GOAL.md` as the goal. This script
modifies the working tree directly in stages 3 and 5 and updates `PROJECT_STATE.md`, so if you
have uncommitted changes, commit them before running it. The per-stage agents and models are
changed in `pipeline.config.env`.

## See also

- `CLAUDE.md` — the rules an AI agent has to follow in this repository
- `.governance/DECISIONS.md` — the reasoning behind design judgements
