# The architecture, as one picture

`docs/generated/architecture.html` is a single self-contained page: twelve
boxes, the boundary of the machine everything runs on, the isolation boundary
of one run, and three named views that dim everything outside the path being
told. Open it with a browser, no server:

```bash
xdg-open docs/generated/architecture.html      # macOS: open
```

It exists for the demo, where the thing being explained is not any one file but
which box calls which, and where the two hard rules physically sit. The views
are the reason it is a page and not a screenshot:

| View | What it walks |
|---|---|
| 一个任务怎么跑完 | board → API → `cc_runner` → a Claude Code process on its own worktree |
| 抢同一个文件时 | a same-workspace claim conflict escalating to a human, the worker blocked on it |
| 自带模型 | Claude Code pointed at the loopback proxy, the key never entering the subprocess |

## Regenerating it

The source of truth is [architecture.archify.json](architecture.archify.json)
next to this file — the picture is derived from it, so edits go there and never
into the HTML. Rendering needs a checkout of
[archify](https://github.com/tt-a1i/archify) (MIT, rendered here with
2.17.0-dev.1):

```bash
node bin/archify.mjs deliver architecture \
  <repo>/docs/reference/architecture.archify.json \
  <repo>/docs/generated/architecture.html --quality showcase
```

`--quality showcase` is what makes this checkable rather than decorative: it
refuses to write the file unless nine artifact checks pass with no warnings —
schema, layout, overlapping labels, arrow sides, and a projected-text-size
check that fails at desktop-readability, not at "it rendered". The output is
deterministic, so a regeneration with an unchanged spec leaves an empty
`git diff`.

Two things the checks cannot catch, both of which happened while drawing this:
a component sitting inside a boundary rectangle it is not a member of (the
vendor box fell inside 这一台机器 by position alone), and a label that is
accurate but reads as the wrong thing. Look at the render before committing it.

Component boxes carry no code anchors yet. archify only links a box to a line
of code when `meta.repository` names a public commit, and this branch is not
pushed.
