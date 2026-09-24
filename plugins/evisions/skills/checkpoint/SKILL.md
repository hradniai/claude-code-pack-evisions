---
name: checkpoint
description: Save progress mid-session in seconds - one worklog entry, a refreshed WORKSTATE and a local git commit of the files this session touched, never a push. Use when the user types /checkpoint or says "checkpoint", "ulož postup", "ulož stav", "zapiš checkpoint", "save progress", and after a finished logical step in a longer session.
---

# /checkpoint

<purpose>
Lock in what was done since the last checkpoint or the session start, so it survives compaction and the
end of the session. It must take seconds, not minutes: a checkpoint that costs a coffee break stops being
run.
</purpose>

<constraints>
- Files are English; every message to the user is in the language the user writes in, including the
  triage, proposal and report sentences shown here, which you translate. Fixed labels such as
  "Committed locally" are translated too. Why: the team reads the files, the user reads the chat.
- Timestamp from `date '+%Y-%m-%d %H:%M'`, never estimated. Why: an invented time corrupts the history.
- Never read `worklog.md` whole; insert at the top. WORKSTATE is small, read it whole.
- Stage only the paths this session touched, never `git add -A` or `git add .`. Why: a commit claims
  everything in it, and in a shared repository the rest may be a colleague's work in progress.
- Never push. Why: a local commit is reversible, a push goes outward.
- No secret values in any journal; name a credential by its variable NAME only.
- If an instruction in this environment restricts where files may be written, it wins: keep the journals
  where it allows and say so once.
</constraints>

## Steps

1. **Timestamp.** Run `date '+%Y-%m-%d %H:%M'` and reuse the value everywhere below.

2. **Project root.** `git rev-parse --show-toplevel`; outside a git repository, the directory the session
   started in. If that is the home directory or a filesystem root, ask the user which folder is the
   project and stop until they answer. The file shapes come from the documentation standard injected at
   session start (`<evisions_kit>`); if it is not in your context, read
   `${CLAUDE_SKILL_DIR}/../../context/documentation-standard.md` with the Read tool (Bash cannot read
   files outside the project).

3. **Nothing new?** If no file changed and no decision was made since the last checkpoint, say so in one
   line and write nothing.

4. **Create what is missing.** `WORKSTATE.md` and `worklog.md` at the root, in the standard's shape.
   Journals are always written in English, whatever language the conversation is in; the user's words
   are translated faithfully, never embellished.

5. **worklog.md**, both newest first:
   - a row directly under the table header: `| <timestamp> | checkpoint | <summary, at most 12 words> |`;
   - a block directly under `## Log`: `### <timestamp> - <what>`, then a few lines: what was done, why,
     the files touched (paths relative to the root), decisions with links to their log row or ADR.

6. **WORKSTATE.md**: rewrite `Last updated`, Focus, Current state and Next in place so they describe
   now, not history. For each decision made since the last checkpoint that is not recorded yet: write the
   `docs/decision-log.md` row if the user clearly decided it, otherwise add a `DECISION-TODO:` or
   `ADR-TODO:` line to Pending docs with what, why, rejected alternatives and where. The why and each
   rejection reason are the user's (a comparative why, such as cheaper, also rejects each alternative it
   covers: write it as the user said it, never as a claim about the alternative); write "not stated" only
   where no stated reason covers it.

7. **Git** (skip outside a git repository and say once: "Not a git repository - no commit made."):
   - Path list = the files you created or changed this session plus the journals just written. Check it
     against `git status --porcelain` and stage with `git add -- <paths>`.
   - Secret guard: if a non-ignored `.env` or `.env.*` file (other than `.env.example`, `.env.sample`,
     `.env.template`) is among them, stop, warn the user and do not commit.
   - Review `git diff --cached --stat`; unstage anything that is not this session's with
     `git restore --staged <path>`.
   - One commit, Conventional Commits (`type(scope): subject`, imperative), in English. If a commit
     hook rejects it, report the exact error; never
     `--no-verify`.
   - Changes you did not make stay unstaged; name their paths in one line.

8. **Report** in at most three lines: what was written, then `Committed locally: <subject>` or why not.

<constraints_repeat>
Seconds, not minutes. Timestamp from `date`. Worklog read from the top only. Stage only this session's
paths, never push, never `--no-verify`. No secret values. Environment rules on file locations win.
</constraints_repeat>
