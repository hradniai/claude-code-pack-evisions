---
name: end
description: Close a work session - first propose the session's decisions (decision-log rows and ADR candidates) for one quick approval, then write the worklog, WORKSTATE, approved decisions and the captured bugs and tech debt, sync feature docs, commit locally and ask before any push. Use when the user types /end or says "končíme", "konec", "uzavři session", "zapiš to a končíme", "wrap up", "end the session".
---

# /end

<purpose>
Leave the project so the next session, or a colleague, can pick it up cold. The user wants to leave:
only two things wait for them, the decision approval (Step 2) and the push question (Step 6). Everything
else runs without asking.
</purpose>

<constraints>
- Files are English; every message to the user is in the language the user writes in, including the
  triage, proposal and report sentences shown here, which you translate. Fixed labels such as
  "Committed locally" are translated too. Why: the team reads the files, the user reads the chat.
- A decision is recorded only when the user decided it or approves it in Step 2, and only with the
  user's reasons ("not stated" where they gave none). Why: a recorded decision is later cited as if
  someone agreed to it; your inference is a proposal, never a record.
- Timestamps from `date '+%Y-%m-%d %H:%M'`, never estimated.
- Never read `worklog.md` whole, and do not re-read the whole conversation: use the checkpoints, the
  WORKSTATE and recent context.
- Stage only the paths this session touched; never push without the user's explicit yes.
- No secret values in any journal; name a credential by its variable NAME only.
- If an instruction in this environment restricts where files may be written, it wins: keep the journals
  where it allows and say so once.
</constraints>

## Step 1: Gather (silently)

- Project root: `git rev-parse --show-toplevel`, else the start directory; if that is the home directory
  or a filesystem root, ask the user which folder is the project. The file shapes come from the
  documentation standard in `<evisions_kit>`; if it is not in your context, read
  `${CLAUDE_SKILL_DIR}/../../context/documentation-standard.md` with the Read tool (Bash cannot read
  files outside the project).
- In a git repository: `git status -sb` (it shows whether the branch is ahead of its upstream),
  `git status --porcelain` and `git diff --stat`.
- `WORKSTATE.md` whole, including EVERY open line in Pending docs, whatever session wrote it: an old
  line is still open until it is written or declined.
- The decisions made this session that are not recorded yet.

**If nothing happened** (no file changed, no decision, no open Pending docs line), say so in one line and
write nothing. Unpushed local commits still count: if the branch is ahead of its upstream, or a remote
exists and the branch has no upstream yet, go straight to the push question of Step 6 (ask, never push
unasked).

## Step 2: Propose decisions (the first stop)

Triage each candidate: an ADR needs ONE decision with consciously rejected alternatives that is hard to
reverse or has a non-obvious rationale, judged from the user's words or project facts, never from a reason
you supply; anything lighter is a decision-log row (the default); an implementation detail is neither and
is dropped. Reasons are the user's: a comparative why (cheaper, self-hostable) is also the rejection
reason of each alternative it covers, shown as the user said it, never turned into a claim about the
alternative and never prefixed with "not stated"; show "not stated" only where no stated reason covers
an alternative. Then send ONE message, in the
user's language, with nothing before the list:

```
1. [ADR] <the decision in a few words> - why: <their reason> - rejected: <alternatives, their reasons>
2. [log] <the decision> - why: not stated
3. [log] <the decision> - why: <their reason> (my inference, you did not state it)
Which should I write? (all / numbers / none)
```

Mark anything the user did not state themselves as your inference. Wait for the answer. With no
candidates, say "No decisions to record." and continue without waiting. `BUG-TODO:` and `DEBT-TODO:`
lines are not proposed: they record observations, not decisions, and Step 3 writes them without asking.

## Step 3: Write

1. Run `date '+%Y-%m-%d %H:%M'` once and reuse it.
2. Approved log rows: append to `docs/decision-log.md` with the next free `D` number (create the file
   with a one-line purpose and the table header if missing), with the reasons exactly as shown in Step 2.
3. Approved ADRs: load the `evisions:adr` skill and write each one in its format, one file per decision.
   The Step 2 approval confirms exactly what was shown, so the skill's triage and provenance checks are
   met; add no reason that was not in the proposal.
4. Every open `BUG-TODO:` line: load the `evisions:bug-log` skill and write it as an entry in `BUGS.md`;
   every open `DEBT-TODO:` line: load the `evisions:tech-debt-log` skill and write it into `TECH-DEBT.md`.
   No approval needed.
5. Shorten each answered or written Pending docs line into a pointer, the only sanctioned rewrite of a
   capture line: written -> `DECISION-TODO: <decision in a few words> (-> docs/decision-log.md D7, <date>)`,
   `ADR-TODO: <decision in a few words> (-> docs/decisions/0003-<slug>.md, <date>)`,
   `BUG-TODO: <title> (-> BUGS.md, <date>)` or `DEBT-TODO: <title> (-> TECH-DEBT.md, <date>)`; declined ->
   `<prefix> <decision in a few words> (declined <date>)`. A line the user did not answer stays as it is.
6. `worklog.md`, both newest first: a row `| <timestamp> | end | <one-line session summary> |` directly
   under the table header, and a `### <timestamp> - <what>` block directly under `## Log`: what was done,
   why, files touched, decisions with links, what remains open.
7. `WORKSTATE.md`: rewrite `Last updated`, Focus, Current state and Next so they describe the state the
   next session starts from; Pending docs keeps its open lines and the pointers from item 5.

Everything written in this step is in English, whatever language the conversation is in: the user's
words and reasons are translated faithfully, never embellished; a verbatim quote may stay in the original
only when the exact wording matters.

## Step 4: Feature docs

If code or features changed this session (anything beyond journals and docs), dispatch the agent
`evisions:features-documenter` with the project root and the list of files changed this session
(including those already committed at a checkpoint; outside a git repository it is the agent's only
scope). Wait for its result so its files land in the same commit.
Otherwise skip this step.

## Step 5: Commit (git repositories only)

Outside a git repository, skip git and say so once. Inside one:
- Path list = the files changed this session, the journals written in Step 3 and the files the
  features-documenter reported. Check it against `git status --porcelain`; `git add -- <paths>`.
- Secret guard: a non-ignored `.env` or `.env.*` (other than `.env.example`, `.env.sample`,
  `.env.template`) among them -> stop, warn, do not commit.
- Review `git diff --cached --stat`; unstage what is not this session's with `git restore --staged`.
- One commit, Conventional Commits, in English. A rejecting
  commit hook is reported with its exact error; never `--no-verify`.

## Step 6: Summary and push question (the second stop)

One short message, in the user's language:
- **Written:** each file touched, one line each (worklog, WORKSTATE, decision log rows, ADRs, feature
  docs); new `BUGS.md` and `TECH-DEBT.md` entries one line each, highest severity first.
- **Git:** the commit subject, or why there is none; paths of changes that were not yours, left unstaged.
- **Pending:** unanswered candidates and anything the next session must know.

If a remote exists and the branch has unpushed commits (`[ahead N]` in `git status -sb`, run again after
the Step 5 commit, or the branch has no upstream yet), end the message with `Push <N> commits to
<remote>/<branch>? (yes / no)` and wait. Push only on an explicit yes (`git push`, or `git push -u <remote> <branch>` without an upstream), then
report the result in one line; a failed push is reported with its exact error.

<constraints_repeat>
Two stops only: the decision approval and the push question. Only the user's decisions and reasons are
recorded ("not stated" where none was given).
Timestamps from `date`, worklog read from the top only, only this session's paths staged, never a push
without an explicit yes. No secret values. Environment rules on file locations win.
</constraints_repeat>
