# claude-code-pack-evisions

A Claude Code plugin marketplace with one plugin, `evisions`: working discipline for the eVisions team.
It keeps every project resumable through plain files on disk (current state, history, decisions,
feature docs) and adds help for research, prompt writing and thinking an idea through.

The user manual for the team, written for non-developers, is [`USER-MANUAL.md`](USER-MANUAL.md).

## What the plugin gives

| Component | Command or agent | What it does |
|---|---|---|
| Session hook | runs by itself | At session start, after `/clear` and after compaction, loads the documentation standard and a map of which skill or agent does which job. |
| Skill `checkpoint` | `/checkpoint` | Mid-session save: one worklog entry, a refreshed `WORKSTATE.md`, a local git commit of the touched files. Never pushes. |
| Skill `end` | `/end` | Session close: proposes the session's decisions for approval, writes worklog, WORKSTATE and the approved decisions, syncs feature docs, commits, asks before any push. |
| Skill `adr` | `/adr` | Writes an architecture decision record (ADR) into `docs/decisions/`, or supersedes one. |
| Skill `prompt-eval` | `/prompt-eval` | Static sanity check of a prompt by a bundled script. Calls no model. |
| Skill `research` | `/research` | Routes a research question to one research agent or to a lead with up to five workers; returns a verdict with sources and saves a file in `research/`. |
| Skill `socratic-brainstormer` | `/socratic-brainstormer` | Questions that develop the user's own raw idea instead of handing them an answer. |
| Agent `features-documenter` | `evisions:features-documenter` | Keeps `docs/features/` in line with what the code does, citing file paths. Runs as part of `/end`. |
| Agent `prompt-engineer` | `evisions:prompt-engineer` | Writes or improves a prompt, system prompt, skill or agent file, then runs the sanity check. |
| Agent `research-analyst` | `evisions:research-analyst` | One-angle web research with tier-labelled findings. |
| Agent `research-lead` | `evisions:research-lead` | Several-angle research: splits the question, runs up to five analysts, synthesizes. |

Every command also works with the plugin prefix, for example `/evisions:checkpoint`. The prefix is
needed when the user has their own skill or command of the same name.

## Install

Every employee installs the plugin for themselves, on their own laptop or container. The repository is
public, so no GitHub login is needed.

With Claude's help: clone this repository, open Claude Code in it and say "install it following
INSTRUCTIONS.md". Claude follows [`INSTRUCTIONS.md`](INSTRUCTIONS.md), confirms each command and
verifies the result. By hand:

```bash
claude plugin marketplace add https://github.com/hradniai/claude-code-pack-evisions
claude plugin install evisions@claude-code-pack-evisions
claude plugin list
```

Then restart Claude Code. Use the `https://` URL as written: the short form
`hradniai/claude-code-pack-evisions` makes Claude Code clone over SSH, which fails without an SSH key for
GitHub.

Offline, register a local clone instead: `claude plugin marketplace add <path to the clone>`. The
plugin then runs from that folder, which must stay in place.

## Update

```bash
claude plugin marketplace update claude-code-pack-evisions
claude plugin update evisions@claude-code-pack-evisions
```

Restart Claude Code afterwards. An update arrives only when the `version` in the manifests changes. When
installed from a local clone, `git pull` in the clone and a restart are enough.

## Uninstall

```bash
claude plugin uninstall evisions@claude-code-pack-evisions
claude plugin marketplace remove claude-code-pack-evisions
```

## What it does not touch

The plugin carries no settings, no permission rules and no safety hooks. The team's own Claude Code
setup stays in charge, and where an environment rule restricts where files may be written, that rule
wins over the plugin's documentation standard.

## Files it creates in a project

| Path | Holds |
|---|---|
| `WORKSTATE.md` | Live state: focus, current state, next steps, decisions waiting for confirmation. Rewritten in place. |
| `worklog.md` | Append-only history, newest first. |
| `docs/decision-log.md` | Smaller decisions, one row each, plus a pointer row per ADR. |
| `docs/decisions/NNNN-title.md` | ADRs, one decision each. |
| `docs/features/` | Feature docs (an existing root `features/` is used instead when the project has one). |
| `research/` | Research reports, `{topic}-research-{YYYY-MM-DD}.md`. |
| `prompts/` | Prompts written by the prompt engineer. |

Journals are always written in English, whatever language the user speaks; the conversation follows
the user's language. Only decisions the user made or confirmed are recorded, and a reason the user did
not give is recorded as "not stated".

## Limits

- The prompt check is hygiene only: a pasted key, no output format, no fallback for missing input,
  unfenced placeholders. It never says whether a prompt works. For that, run the prompt on 3-5 real
  inputs and compare the outputs with what a good result looks like.
- Research uses web search and fetch, and cites a source for every claim. A claim without a source is
  reported as unsourced, never filled in.
- No model facts are bundled (no model IDs, prices or rankings), because they go stale faster than the
  plugin ships. A model recommendation must come from an official vendor page fetched in that session,
  or be labelled unverified.

## Companion plugins

Three public add-ons fit the kit. They are optional and not part of this repository: nothing of theirs
is copied here, each installs from its author's source under its author's license and gets its
author's updates, and the kit's skills use them only when they are installed. `INSTRUCTIONS.md` Step 7
installs each one only after the user confirms.

| Companion | Install | What it adds |
|---|---|---|
| Superpowers | `claude plugin install superpowers@claude-plugins-official` (official Anthropic marketplace) | A disciplined build workflow: brainstorming, written spec, plan, execution, plus systematic debugging and test-driven development. Injects its own instructions at every session start and pushes Claude to use skills eagerly; that is expected. |
| Replan, by Jiří George Dolejš | `claude plugin marketplace add https://github.com/kojott/claude-replan`, then `claude plugin install replan@claude-replan` | `/replan` validates a plan with parallel review subagents before execution, `/recheck` verifies the implementation matches the plan. License: MIT with the Commons Clause. |
| Grilling, by Matt Pocock | `npx skills@latest add mattpocock/skills --skill grilling --skill grill-me -g -a claude-code -y` (needs Node.js) | `/grill-me`: rounds of questions on a plan or decision, each with a recommended answer, until nothing is left assumed. MIT. |

Grilling without Node.js: `claude plugin install mattpocock-skills@claude-plugins-official`. That is the
fallback, not the default: it adds 25 skills, one of them a generic `research` skill that competes with
`evisions:research`, and every skill costs context in every session.

The `https://` form for replan is deliberate: Claude Code clones the short form `kojott/claude-replan`
over SSH, which fails without an SSH key for GitHub.

## Maintaining

```bash
python3 -m unittest            # run inside plugins/evisions/tests
claude plugin validate .
claude plugin validate plugins/evisions
```

Every release raises `version` in both `.claude-plugin/marketplace.json` and
`plugins/evisions/.claude-plugin/plugin.json`; the contract test fails when they disagree.

## License

All rights reserved; use is limited to the audience named in [`LICENSE`](LICENSE).
