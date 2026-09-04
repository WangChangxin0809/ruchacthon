# The harness, as a repository you can start from

> A working set of checks, hooks and conventions for a repository where coding
> agents do the work — with every sentence only its owner can write left
> deliberately blank.

**Use this template**, clone it, and run:

```bash
./ci.sh; echo $?     # 2
```

**Exit 2 is not a failure. It is "could not judge."** A harness cannot tell you
whether a project is sound when nobody has yet written down what the project is.
Not green, not red — unjudgeable. And because 2 is never a pass, nothing can be
shipped on it.

`START-HERE.md` is the list. When the last item is done, `./ci.sh` exits 0, and
both that file and this one are gone.

## What you get

| | |
|---|---|
| `scripts/guards/` | refuse a dangerous tool call *before* it runs — a push to a protected branch, a computed `rm -rf`, a credential about to be committed |
| `scripts/gates/` | judge the worktree: unfilled templates, broken doc routes, a file too long for an agent to read in one call, always-on context past its budget |
| `scripts/context/` | what the hooks call — a session brief, a rule delivered at the moment a matching file is read, a check that a turn is not ending with the tree red |
| `.claude/skills/` | how to write docs, checks, the public face and CI; how to fold a note pile back into the repository; how to find a skill somebody already wrote |
| `.claude/rules/` | two rules true of any repository, each scoped so it costs nothing until it matches |
| `ci.sh` | the one roster. `.github/workflows/ci.yml` calls it rather than restating it |

Three layers, and they are not redundant: the hooks act while an agent is
working, `ci.sh` acts before you push, and CI acts before anything merges. A
check that runs only on a laptop is the weakest of the three, because it is the
one nobody is obliged to run.

## What it is not

Not a framework, and nothing to install: Python 3.9+, no dependencies, and every
file is yours the moment you use the template. Not a style guide — everything
here either runs or is deleted. And not a finished repository: the prose is
placeholders because the four or five sentences that matter are the ones nobody
else can write for you.

The `cc-repo-harness` plugin is declared in `.claude/settings.json` as a
dependency, not vendored. It holds what *measures* a repository and nothing this
repository needs in order to work — delete the declaration and everything above
still runs.
