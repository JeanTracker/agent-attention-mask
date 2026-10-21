"""The panel's model scrape, fed bytes instead of a live agent (D-055).

`_pick_model` sees everything the agent prints with escapes stripped --
its status line and its reply text alike -- so the cases here are the two
halves of that: the names claude really draws must come through, and prose
that happens to mention a model family must not.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amask.cli import _ESCAPE_SEQ, _model_label, _pick_model

FAILURES = []


def check(name, condition, detail=""):
    ok = bool(condition)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


def pick(text, current=""):
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return _pick_model(_ESCAPE_SEQ.sub(b"", raw), current)


# What claude draws: spaced in the splash, squashed in the compact bar, with
# effort and plan trailing after a separator.
got = pick("Opus 5.5 (1M context) with high effort · Claude Max")
check("splash name stops at the context window", got == "Opus 5.5 (1M context)", repr(got))

got = pick("Opus5(1Mcontext)")
check("squashed status bar still reads", got == "Opus5(1Mcontext)", repr(got))

got = pick("Opus5(1Mcontext) ... Opus 5 (1M context)")
check("spaced beats squashed whichever comes first", got == "Opus 5 (1M context)", repr(got))

got = pick("\x1b[2mSonnet\x1b[0m 5 \x1b[38;5;8m|\x1b[0m ~/edu")
check("colour codes threaded through the name", got == "Sonnet 5", repr(got))

for name in ("Fable 5.1", "GPT-5", "Gemini 2.5 Pro"):
    got = pick(name + " · Claude Max")
    check(f"other families, with version: {name}", got == name, repr(got))

# The reported case: the reply mentioned Gemini, and forty bytes of the reply
# and the TUI's scroll hint became the model name.
got = pick('Gemini는 "twoprimi Jump to bottom (click) ↓')
check("family word without a version is prose", got == "", repr(got))

got = pick('Gemini는 "twoprimi Jump to bottom (click) ↓', "Opus 5 (1M context)")
check("prose does not replace a real name", got == "Opus 5 (1M context)", repr(got))

got = pick("Opus 5 (1M context) ... compared with Gemini 2.5 Pro and GPT-5 mini")
check("status line outranks a model named in the reply", got == "Opus 5 (1M context)", repr(got))

# The reported case (D-060): the user's own status line draws "[Opus | Max]",
# so the splash is the only place the versioned name appears, and a reply
# listing model ids put a longer "Sonnet 5.5" on screen. Length must not win.
got = pick("Opus 5.5: 'claude-opus-5-5', Sonnet 5.5: 'claude-sonnet-5-5'", "Opus 5.5")
check("a longer family named in the reply does not replace the name", got == "Opus 5.5", repr(got))

got = pick("Opus 5.5 · Claude Max ... Sonnet 5.5: 'claude-sonnet-5-5'")
check("between equal shapes the first seen wins", got == "Opus 5.5", repr(got))

got = pick("Opus5.5(1Mcontext)", "Opus 5.5")
check("a context window still upgrades the name", got == "Opus5.5(1Mcontext)", repr(got))

got = pick("Magnum Opus 5 minutes", "")
check("known gap: a family and number in prose still read as a name", got == "Opus 5", repr(got))

got = pick("Opusculum 5, Haikus 3")
check("family word inside a longer word is not a name", got == "", repr(got))

# The hook's exact id, written the way the panel already spells the name
# (D-061). Only Claude Code's own id shape is rewritten; anything else shows
# as it came.
for model_id, label in (
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-haiku-5", "Haiku 5"),
    ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
    ("claude-opus-4-1[1m]", "Opus 4.1[1m]"),
    ("gpt-5-codex", "gpt-5-codex"),
    ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20241022"),
):
    got = _model_label(model_id)
    check(f"hook model id {model_id} -> {label}", got == label, repr(got))

print()
if FAILURES:
    print(f"{len(FAILURES)} failed")
    sys.exit(1)
print("all passed")
