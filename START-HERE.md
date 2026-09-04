# Start here

This is the only file in this repository that is about the *template*.
Everything else is about *your project* — which is why the first command you
run says it cannot judge anything yet.

```bash
./ci.sh; echo $?     # 2
```

**Exit 2 is not a failure. It is "could not judge."** It is the most important
idea in this repository and you have now met it on line one: a harness cannot
tell you whether a project is sound when nobody has yet written down what the
project is. Not green, not red — unjudgeable. And because exit 2 is never a
pass, nothing can be shipped on it.

Work the list below down. When the last item is done, `./ci.sh` exits 0, and
this file is gone.

## The list

- [ ] **`CLAUDE.md`** — the one paragraph, and the hard rules. Every line here
      is paid on every turn of every session, forever, so the cap is 100 lines
      and `scripts/gates/check_context_budget.py` enforces it.
- [ ] **`README.md`** — what this is, and the shortest path from a clean clone
      to something that works. Someone will paste that block verbatim; run it
      in a fresh clone before you believe it.
- [ ] **`ARCHITECTURE.md`** — the invariants, and for each one, the thing that
      would break if it stopped holding.
- [ ] **`CONTRIBUTING.md`**, **`SECURITY.md`** — the two nobody writes until
      an outsider needs them, at which point it is too late to be useful.
- [ ] **`docs/decisions/0001-agent-conventions.md`** — the first decision. Date
      it, and name the alternative you rejected. A decision record without a
      rejected alternative is a description.
- [ ] **`CODE_OF_CONDUCT.md`** — one contact address, near the bottom.
- [ ] **`LICENSE`** — not shipped, deliberately: it is a legal choice, not a
      template. `check_community_health.py` stays red until you make it.
- [ ] **Read `scripts/guards/*.py`.** They already run before every Bash,
      Write and Edit in this repository. That is code you are handing the keys
      to, and it arrived in a copy of somebody else's repository.
- [ ] **Watch one check fail.** Break a gate on purpose and confirm
      `scripts/gates/selftest.py` goes red. Until you have seen that, you have
      a file, not a check.
- [ ] **Delete this file, and `.github/README.md`.** The second one is
      the template's own front page — GitHub shows it in preference to
      your `README.md`, which is exactly what you do not want once
      yours is written.

## What you were given

| | |
|---|---|
| `scripts/guards/` | refuse a dangerous tool call *before* it runs |
| `scripts/gates/` | judge the worktree, in `ci.sh` and in CI |
| `scripts/context/` | what the hooks in `.claude/settings.json` call |
| `.claude/skills/` | how to write docs, checks, the public face, and how to fold a note pile back into the repository |
| `.claude/rules/` | two rules, each under 50 tokens and scoped by `paths:`, so they load only when a matching file is read. Keep, edit, or delete them |
| `.claude/wiki/` | an empty catalogue of what keeps going wrong. The plugin's `/learn` fills it from session transcripts; no agent reads it |
| `ci.sh` | the one roster. `.github/workflows/ci.yml` calls it rather than restating it |
| `.claude/settings.json` | wires those hooks, and declares the `cc-repo-harness` plugin as this repository's dependency |

The plugin is declared, not vendored. It holds what *measures* this repository
— `/assess` and the readers behind it — and nothing this repository needs in
order to work: delete the declaration and everything above still runs. Claude
Code will ask each person whether to trust it, which is the right question to
be asked about third-party code arriving in a clone.

Three layers, and they are not redundant: the hooks act while an agent is
working, `ci.sh` acts before you push, and CI acts before anything merges. A
check that only runs on a laptop is the weakest of the three, because it is the
one nobody is obliged to run.
