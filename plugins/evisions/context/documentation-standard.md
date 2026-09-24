<documentation_standard>

# Documentation standard

Compaction and new sessions erase the conversation; the files on disk are what survives. These rules
keep every project resumable by the next session or by a colleague, so they apply without being asked.

<hard_rules>
- **Environment rules win.** If another instruction in this environment restricts where files may be
  written, it outranks this standard: keep the journals where that rule allows and tell the user once
  where they are. Why: two rules must never leave the user with files in a forbidden place.
- **Journals before the first edit.** Before your first Write or Edit in a project (any file, not only
  code), or before dispatching an agent that will write into it (research, prompts), check the project
  root for `WORKSTATE.md` and `worklog.md` and create the missing ones in the same turn, git repository or
  not; the home directory and a filesystem root are the only exception (ask first).
  Why: without them the next session starts blind.
- **Only the user decides.** A decision in the log or an ADR is one the user made, in their words or by
  explicit confirmation, and so are its why and every rejection reason: where they gave none, write "not
  stated". Your own inference is proposed, never recorded. Why: a record is later cited as agreed.
- **Journals are always in English** (every file in the table below), whatever language the conversation
  is in; talk to the user in their language. Translate the user's words faithfully, never embellish; keep a
  verbatim quote in the original only when the exact wording matters. Why: a multilingual team shares
  these files, and English keeps them readable and searchable for everyone.
- **No secret values** in any journal: name a credential by its variable NAME only. Why: journals get
  committed and shared.
- **Never read `worklog.md` whole**, only its top. Why: it grows forever and the top holds what matters.
- **Timestamps come from `date '+%Y-%m-%d %H:%M'`**, never estimated.
</hard_rules>

## Where the files live

Project root = the git top-level (`git rev-parse --show-toplevel`), otherwise the directory the session
started in (no question needed). Never the home directory or a filesystem root: a `SESSION DIRECTORY`
note at the top of this block marks that case; then ask the user which folder is the project (or whether
this work needs journals at all) before creating anything.

| File | Holds |
|---|---|
| `WORKSTATE.md` | LIVE state only: small, read whole, rewritten in place. Not a log. |
| `worklog.md` | History, append-only: entry table on top, dated prose below, both newest first. |
| `docs/decision-log.md` | Directional decisions below the ADR bar, one row each, plus a pointer row per ADR. Append-only. |
| `docs/decisions/NNNN-kebab-title.md` | ADRs, one decision each. |
| `docs/features/` (or an existing root `features/`) | Feature docs, kept by the `evisions:features-documenter` agent. |
| `research/` | Research files, written by `evisions:research`. |
| `prompts/` | Prompt files, written by `evisions:prompt-engineer`. |

No README is required or maintained by this standard; an existing one is left alone.

## Shapes

```
# WORKSTATE: <project>
Last updated: YYYY-MM-DD HH:MM
## Focus                <- what is being worked on now, one or two lines
## Current state        <- where things stand, what works, what blocks
## Next                 <- the next concrete steps
## Pending docs         <- DECISION-TODO: / ADR-TODO: capture lines
```

```
# Worklog: <project>
| Timestamp | Type | Summary |      <- newest row directly under the header; Type: checkpoint or end
|---|---|---|
## Log
### YYYY-MM-DD HH:MM - <what>      <- newest block directly under "## Log": what was done, why,
                                      files touched, decisions with links
```
(`<-` marks an explanation, not file content.)

`docs/decision-log.md`: a one-line purpose, then `| # | Decision | Date | Why | Rejected alternatives /
detail |` with IDs D1, D2, and so on. Why and rejection reasons are the user's, or "not stated".

## Decision log row or ADR

An ADR records ONE decision that had consciously rejected alternatives AND is hard to reverse or has a
non-obvious rationale, judged from the user's words or project facts, never from a reason you supply.
Anything lighter is a decision-log row (the default); an implementation detail is neither. An accepted
ADR's body never changes: a change of mind is a new ADR that supersedes it, and the old one gets only
`status: superseded` and `superseded_by`. The `evisions:adr` skill writes ADRs and triages.

## Capture while working

When a decision surfaces, also during brainstorming and planning, write the decision-log row right away
if the user clearly made it. Otherwise add ONE line to WORKSTATE "Pending docs", complete enough to be
written after this conversation is gone, marked `(unconfirmed)` when the user has not decided it yet:
- `DECISION-TODO: <decision> - why: <their reason or "not stated"> - rejected: <alternatives> - where: <file or topic>`
- `ADR-TODO:` the same fields, for a decision that clears the ADR bar.

Do not stop the work for it. `/end` proposes these for approval; each written line is then rewritten
into a pointer to what was written, the only sanctioned rewrite of a capture line.

## When to write

1. Starting work in a project that has `WORKSTATE.md`: read it whole first; it is the handoff.
2. First edit in a project: the journals already exist (hard rule "Journals before the first edit").
3. A decision is made: decision-log row, or a capture line.
4. Before telling the user something is done: WORKSTATE reflects reality.
5. `/checkpoint` (`evisions:checkpoint`): one worklog entry, a refreshed WORKSTATE, a local commit of
   the touched paths. Never pushes.
6. `/end` (`evisions:end`): proposes decisions for approval, writes the journals, syncs feature docs,
   commits locally and asks before any push.

<bottom_line>
Environment rules on file locations win. Journals exist from the first edit on. WORKSTATE is live state;
the worklog is history read from the top. Decisions and their reasons are the user's own and go to the
log (default) or an ADR, captured as one line when they cannot be written now. No secret values,
timestamps from `date`, journals always in English while you talk to the user in their language.
</bottom_line>

</documentation_standard>
