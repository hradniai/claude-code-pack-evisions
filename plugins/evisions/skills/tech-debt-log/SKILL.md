---
name: tech-debt-log
description: Write a tech-debt entry into the project's TECH-DEBT.md - code that WORKS but is costly or risky (duplication, a workaround, fragile coupling, missing tests, dead code, slow but correct), noticed WHILE working on something else. Used by /end to write every open DEBT-TODO line from WORKSTATE without asking, and on an explicit ask to log debt now ("zapiš tech debt", "zaloguj technický dluh", "log this tech debt", "add it to TECH-DEBT.md"), or to mark logged debt paid. NOT for broken behaviour (evisions:bug-log) and NOT for lint-level style.
---

# tech-debt-log

<purpose>
Keep costly or risky code that was noticed along the way visible, with enough detail that someone can
decide later whether to pay it down. The documentation standard decides WHEN something is captured (a
`DEBT-TODO:` line in WORKSTATE "Pending docs"); this skill writes the entry. Sibling of
`evisions:bug-log`.
</purpose>

<constraints>
- `TECH-DEBT.md` is English, whatever language the conversation is in; tell the user about it in their
  language. Why: the team shares the file.
- Never delete an entry; paid debt changes its Status in place. Why: the entry is the audit trail.
- `Where` and `Found while` are mandatory. Why: without them the entry cannot be acted on later.
- Log it, do not pay it down on the spot unless the user asks. Why: a detour derails the task at hand.
- No secret values; name a credential by its variable NAME only.
- Timestamps from `date '+%Y-%m-%d %H:%M'`, never estimated.
</constraints>

## What counts

Tech debt works today but costs or risks something later: duplicated logic, a workaround or suppressed
check (`as any`, `noqa`, an empty catch) without a fix, fragile coupling, missing tests on logic that
matters, dead code, hardcoded values that belong in config, slow but correct code, an app left on an
older approach the other apps moved away from. Broken behaviour is a bug (`evisions:bug-log`).

## Steps

1. **Project root**: `git rev-parse --show-toplevel`, else the directory the session started in.
2. **Create `TECH-DEBT.md`** at the root from the template below if it does not exist (only on the first
   real item, never in advance). The first time, add one line to WORKSTATE "Current state":
   `TECH-DEBT.md - <N> open`.
3. **Write the entry at the top**, directly under the header block (newest first). Fill every field from
   the capture line and the current context; a field you cannot fill says `not known`.
4. **Pointer.** When the entry came from a `DEBT-TODO:` line, rewrite that line into a pointer:
   `DEBT-TODO: <title> (-> TECH-DEBT.md, <YYYY-MM-DD>)`.
5. **Keep the count** in the WORKSTATE line (`TECH-DEBT.md - <N> open`) current.
6. **Tell the user** in their language, one line per new item, highest severity first.

## Paying it down

When logged debt is paid, in the SAME commit as the change: change `Status` to `paid` (or `wontfix`,
`duplicate` with the reason) and append a line to the entry:
`- **Paid (YYYY-MM-DD):** <what changed, where>`. Update the open count in WORKSTATE.

## Template

```markdown
# Tech debt: <project>

Code that works but is costly or risky, noticed while working on something else. Not a bug (broken
behaviour goes in BUGS.md), not lint-level style. Newest first. Status changes in place; entries are
never deleted.

## YYYY-MM-DD HH:MM - <one-line title>
- **Status:** open            <- open | paid | wontfix | duplicate
- **Severity:** medium        <- how badly it bites if left: low | medium | high | critical
- **Where:** path/to/file.ts:120-134 (or the module or feature)
- **Found while:** <what was being worked on when this was noticed>
- **What:** <the debt, and why it is costly or risky>
- **Risk if left:** <what goes wrong later if nobody pays it down>
- **Paydown idea:** <optional one-liner>
```

(`<-` marks an explanation, not file content.)
