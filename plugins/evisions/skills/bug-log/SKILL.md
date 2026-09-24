---
name: bug-log
description: Write a bug entry into the project's BUGS.md - broken behaviour found WHILE working on something else, logged so it is not lost. Used by /end to write every open BUG-TODO line from WORKSTATE without asking, and on an explicit ask to log a bug now ("zapiš bug", "zaloguj tu chybu", "log this bug", "add it to BUGS.md"), or to mark a logged bug fixed. NOT for the bug the current task is about (fix that one), NOT for code that works but is costly or risky (evisions:tech-debt-log), and NOT for style.
---

# bug-log

<purpose>
Keep broken behaviour that was noticed along the way from being forgotten, with enough detail that
someone can fix it months later without the conversation that found it. The documentation standard
decides WHEN something is captured (a `BUG-TODO:` line in WORKSTATE "Pending docs"); this skill writes the
entry.
</purpose>

<constraints>
- `BUGS.md` is English, whatever language the conversation is in; tell the user about it in their
  language. Why: the team shares the file.
- Never delete an entry; a fixed bug changes its Status in place. Why: the entry is the audit trail.
- `Where` and `Found while` are mandatory. Why: without them the entry cannot be acted on later.
- Record only what was observed; a guess at the cause goes in `Fix idea`, marked as a guess.
- No secret values; name a credential by its variable NAME only.
- Timestamps from `date '+%Y-%m-%d %H:%M'`, never estimated.
</constraints>

## What counts

A bug is broken behaviour: a crash, wrong output, data corruption, a regression, a security hole. It was
found while doing something else, so it is not the task at hand. Code that works but is slow, duplicated,
fragile or untested is tech debt (`evisions:tech-debt-log`). A defect in something the current task
depends on is part of the task: fix it there instead of logging it.

## Steps

1. **Project root**: `git rev-parse --show-toplevel`, else the directory the session started in.
2. **Create `BUGS.md`** at the root from the template below if it does not exist (only on the first real
   bug, never in advance). The first time, add one line to WORKSTATE "Current state":
   `BUGS.md - <N> open`.
3. **Write the entry at the top**, directly under the header block (newest first). Fill every field from
   the capture line and the current context; a field you cannot fill says `not known`.
4. **Pointer.** When the entry came from a `BUG-TODO:` line, rewrite that line into a pointer:
   `BUG-TODO: <title> (-> BUGS.md, <YYYY-MM-DD>)`.
5. **Keep the count** in the WORKSTATE line (`BUGS.md - <N> open`) current.
6. **Tell the user** in their language, one line per new bug, highest severity first.

## Closing a bug

When a logged bug is fixed, in the SAME commit as the fix: change `Status` to `fixed` (or `wontfix`,
`duplicate` with the reason) and append a line to the entry:
`- **Fix (YYYY-MM-DD):** <what changed, where, how it was verified>`. Update the open count in WORKSTATE.

## Template

```markdown
# Bugs: <project>

Broken behaviour found while working on something else, logged so it is not lost. Not the current
task's own bug, not tech debt or style. Newest first. Status changes in place; entries are never deleted.

## YYYY-MM-DD HH:MM - <one-line title>
- **Status:** open            <- open | fixed | wontfix | duplicate
- **Severity:** medium        <- low | medium | high | critical
- **Where:** path/to/file.ts:120-134 (or the feature or screen)
- **Found while:** <what was being worked on when this showed up>
- **What:** <observed behaviour versus expected behaviour>
- **Repro:** <steps or the failing input, if known>
- **Fix idea:** <optional, marked as a guess>
```

(`<-` marks an explanation, not file content.)

Severity: **critical** loses or leaks data, or blocks everyone; **high** breaks a main flow; **medium**
breaks a secondary flow or has a workaround; **low** is cosmetic or rare.
