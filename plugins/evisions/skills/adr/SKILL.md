---
name: adr
description: Record a decision as an ADR (Architecture Decision Record) in docs/decisions/, or as a docs/decision-log.md row when it is below the ADR bar, and supersede an older ADR when a decision changes. Covers any decision worth keeping - technical, process, client or business. Use when the user says "zapiš ADR", "zapiš to jako rozhodnutí", "zapiš rozhodnutí", "write an ADR", "record this decision", "decision record", "měníme rozhodnutí", or when /end writes an approved ADR.
---

# ADR - decision records

<purpose>
An ADR answers the question nobody can answer six months later: why did we decide it this way? It is
worth writing only for a decision that will be questioned; everything lighter belongs in the decision
log, so this skill triages first and writes second.
</purpose>

<constraints>
- Files are English; every message to the user is in the language the user writes in, including the
  triage, proposal and report sentences shown here, which you translate. Fixed labels such as
  "Committed locally" are translated too. Why: the team reads the files, the user reads the chat.
- Only the user's decision is recorded, with the user's reasons: their words, or their explicit
  confirmation in this session; a reason they never gave is written as "not stated". Why: an ADR is cited
  as the team's agreement; your inference written into it is a false record.
- ONE decision per ADR, never a session summary. Why: a bundle cannot be superseded piece by piece.
- An accepted ADR's body never changes (typo fixes excepted). A change of mind is a NEW ADR that
  supersedes it. Why: the record of what was believed then is the value.
- Dates come from `date`, never estimated. No secret values; name a credential by its variable NAME.
- If an instruction in this environment restricts where files may be written, it wins: write where it
  allows and say so once.
</constraints>

## Step 1: Triage - ADR, log row, or neither

It is an ADR only when ALL hold:
- it is a single decision;
- alternatives were consciously considered and rejected;
- it is hard or costly to reverse, OR its rationale is non-obvious (a newcomer would ask "why?").

Judge each point from what the user said or what the project shows, never from a reason you supply: "hard
to reverse" needs their words or a concrete fact ("we rewrite dozens of scenarios"). A tool preference with
a plain stated reason is a log row.

Otherwise:
- **Below the bar** (no real alternative, or easy to reverse): write a `docs/decision-log.md` row
  `| D<next> | <decision> | <YYYY-MM-DD> | <their why> | <rejected alternatives, each with their reason> |`
  (create the file with a one-line purpose and the table header if missing) and tell the user, in their
  language: "Below the ADR bar (<reason>), recorded as D<n> in docs/decision-log.md."
- **An implementation detail** (a per-task choice): neither. Say so; it belongs in the worklog at most.
- **Several decisions bundled**: split them and triage each one.

## Step 2: Provenance

Check that the user made this decision. If the wording or the decision is your inference, show the
decision sentence and the rejected alternatives, and ask the user to confirm or correct before writing.
What they confirm is what gets written, not your paraphrase.

The same holds for REASONS: the why and every rejection reason are the user's. Where they gave none,
write "not stated" instead of supplying one. Why: an invented reason is later defended as the team's
own. A reason given in another language is translated into English faithfully, never embellished; keep a
verbatim quote in the original only when the exact wording matters.

When the user's why is comparative (cheaper, self-hostable, known to clients), it is also the rejection
reason of each alternative it covers. Write it as the user said it, for example `Power BI - clients know
Looker Studio and it is free`, never turned into a claim about the alternative that the user did not make
("clients don't know Power BI", "not free"), and never prefixed with "not stated". "Not stated" is only
for an alternative that no stated reason covers.

## Step 3: File and number

- Folder: `docs/decisions/` under the project root (`git rev-parse --show-toplevel`, else the start
  directory); create it if missing.
- Number: the highest existing `NNNN` in that folder plus one, starting at `0001`.
- Name: `NNNN-kebab-title.md`, ASCII, at most seven words, e.g. `0003-postgres-for-campaign-data.md`.
- Language: English (headings, labels and body), whatever language the conversation is in. Why: the
  team is multilingual and every ADR must be readable and searchable by all of it.

## Step 4: Write

Read `${CLAUDE_SKILL_DIR}/template.md` with the Read tool (Bash cannot read files outside the project);
if that path did not resolve, use the base directory announced when this skill loaded. Keep its English
headings and labels. Frontmatter:
- `title`: the decision in a few words.
- `status`: `accepted` when the user decided it; `proposed` when they want it recorded but not yet final.
- `date`: when the decision was made, `YYYY-MM-DD` from `date '+%Y-%m-%d'` (for a retroactive ADR, the
  real date; ask when unknown).
- `deciders`: who decided, as the user names them; otherwise `git config user.name`.
- `supersedes` / `superseded_by`: `~` unless Step 5 applies.

Body: Context (the concrete signals that forced the choice), Decision (one or two sentences, then detail),
Alternatives considered (each with the user's reason as they said it; a comparative why covers every
alternative it applies to, written as in Step 2, e.g. `Stay on Make - self-hosting and lower cost`; "not stated"
only when no reason of theirs covers it; at least one), Consequences
(gains, costs, what it creates or ends), References. Keep it to one screen; longer detail goes to a separate file that the ADR links.

Then add a pointer row to `docs/decision-log.md` (`| D<next> | ADR-NNNN: <title> | <date> | <their why> |
-> docs/decisions/NNNN-<slug>.md |`) so the log lists every decision in one place, and shorten a matching
`ADR-TODO:` line in WORKSTATE Pending docs into `ADR-TODO: <decision in a few words> (->
docs/decisions/NNNN-<slug>.md, <date>)`.

## Step 5: Supersede

When a new decision replaces an old ADR:
1. Write the new ADR with `supersedes: NNNN` (the old number).
2. In the old ADR change ONLY two frontmatter fields: `status: superseded` and `superseded_by: <new
   number>`. Nothing else in that file.

A decision that is simply no longer relevant, with no replacement, gets `status: deprecated` and nothing
else changes.

## Anti-patterns

- An ADR without a rejected alternative: it was not a decision worth an ADR; it is a log row.
- Editing an accepted ADR because the team changed its mind: write a new one and supersede.
- Vague context ("it was needed"): useless at the only moment anyone reads it.
- An ADR written from your own conclusion without the user's confirmation.

<constraints_repeat>
Triage first, from the user's words and project facts: ADR only for one decision with rejected
alternatives that is hard to reverse or non-obvious; otherwise a log row (the default), or nothing. Only
the user's decisions and reasons ("not stated" where none was given), one decision per ADR, in English. Accepted bodies are
immutable; change of mind = new ADR plus `status: superseded` and `superseded_by` on the old one. Dates
from `date`. Environment rules on file locations win.
</constraints_repeat>
