# claude-code-pack-evisions

A Claude Code plugin marketplace with one plugin, `evisions`: working discipline for the eVisions team.
It keeps every project resumable through plain files on disk (current state, history, decisions,
feature docs) and adds help for research, prompt writing and thinking an idea through. Since 1.1.0 it
also carries a safety baseline (a hook that blocks secret reads and destructive commands, and an
installer for the matching Claude Code settings), a PRD interview, code and security review agents, a
log for bugs and tech debt found along the way, and an admission check for third-party skills.

The user manual for the team, written for non-developers, is [`USER-MANUAL.md`](USER-MANUAL.md).
Since 1.2.0 the same repository also installs the safety baseline into OpenAI's Codex CLI; see
[Using it with Codex](#using-it-with-codex).

## What the plugin gives

| Component | Command or agent | What it does |
|---|---|---|
| Session hook | runs by itself | At session start, after `/clear` and after compaction, loads the documentation standard and a map of which skill or agent does which job (two of the three `SessionStart` handlers; the third is the safety protocol below). |
| Safety protocol | runs by itself | At the same moments, tells Claude how to handle a block (stop, say what was blocked and why, give the user the exact command to run themselves, never work around it) and never to show a secret value; reports when Python is missing and whether the settings baseline is installed. At session start it also checks the running Claude Code against the newest published version and, when it is behind, has Claude tell the user how to update. See [Claude Code version check](#claude-code-version-check). |
| Safety hook | runs by itself | Before every `Bash`, `PowerShell`, `Read` and `Grep` call, blocks secret reads and destructive commands. See [Safety baseline](#safety-baseline). |
| Time hook | runs by itself | Tells Claude the current local time on every message (Claude Code gives it only the date), so timestamps in the journals are right. |
| Skill `checkpoint` | `/checkpoint` | Mid-session save: one worklog entry, a refreshed `WORKSTATE.md`, a local git commit of the touched files. Never pushes. |
| Skill `end` | `/end` | Session close: proposes the session's decisions for approval, writes worklog, WORKSTATE and the approved decisions, writes the captured bugs and tech debt without asking, syncs feature docs, commits, asks before any push. |
| Skill `adr` | `/adr` | Writes an architecture decision record (ADR) into `docs/decisions/`, or supersedes one. |
| Skill `prd` | `/prd` | Interviews the user, one block of questions at a time and in their language, to a PRD (product requirements document) written in English to `docs/prd/<slug>-prd.md`: problem, goals, users, scope and acceptance criteria, plus the technical dimensions a non-developer does not know to ask about (GDPR and EU AI Act triage, data classification, authentication, backup and disaster recovery, a STRIDE threat model). The WHAT and WHY, not the HOW. |
| Skill `bug-log` | `/bug-log` | Writes a bug found while working on something else into `BUGS.md`, or marks a logged bug fixed. `/end` runs it for every open `BUG-TODO:` line in `WORKSTATE.md`. |
| Skill `tech-debt-log` | `/tech-debt-log` | Writes code that works but is costly or risky into `TECH-DEBT.md`, or marks logged debt paid. `/end` runs it for every open `DEBT-TODO:` line in `WORKSTATE.md`. |
| Skill `skill-scanner` | `/skill-scanner` | Checks a third-party skill or plugin on disk before it is installed: a bundled static scanner plus a read-only review, returning `BLOCK`, `REVIEW` or `PERMIT WITHIN COVERAGE` with the evidence, and compares a new version against an earlier scan. Never installs, runs or imports the target. |
| Skill `prompt-eval` | `/prompt-eval` | Static sanity check of a prompt by a bundled script. Calls no model. |
| Skill `research` | `/research` | Routes a research question to one research agent or to a lead with up to five workers; returns a verdict with sources and saves a file in `research/`. |
| Skill `socratic-brainstormer` | `/socratic-brainstormer` | Questions that develop the user's own raw idea instead of handing them an answer. |
| Agent `features-documenter` | `evisions:features-documenter` | Keeps `docs/features/` in line with what the code does, citing file paths. Runs as part of `/end`. |
| Agent `prompt-engineer` | `evisions:prompt-engineer` | Writes or improves a prompt, system prompt, skill or agent file, then runs the sanity check. |
| Agent `research-analyst` | `evisions:research-analyst` | One-angle web research with tier-labelled findings. |
| Agent `research-lead` | `evisions:research-lead` | Several-angle research: splits the question, runs up to five analysts, synthesizes. |
| Agent `code-reviewer` | `evisions:code-reviewer` | Code review of the scope the user names (the whole app, a feature, files, a commit or the uncommitted changes): correctness bugs, security weaknesses, silenced errors, needless complexity, changes beyond the task, naming. Re-verifies every finding in a confidence pass and drops false positives; answers in the user's language. Read-only: it reports, it never fixes. |
| Agent `security-auditor` | `evisions:security-auditor` | Security audit of the scope the user names, the server and deployment configuration included: secrets, authentication and authorization, injection, data exposure, the AI lethal trifecta, LLM cost guards, GDPR and EU AI Act. Confidence pass as above; each finding with its business risk and a fix, in the user's language. Read-only. For code AND security, both agents run in parallel. |
| Helper `list-env-keys` | on the `PATH` of Claude's Bash tool | Lists the names of the keys in env files, never a value. `--from <file>` reads one file, `--classify` adds each key's state (empty, placeholder, filled). |
| Helper `evisions-settings` | on the `PATH` of Claude's Bash tool, or by path | Installs, checks and removes the settings baseline that a plugin cannot set itself. See [Safety baseline](#safety-baseline). |

Every command also works with the plugin prefix, for example `/evisions:checkpoint`. The prefix is
needed when the user has their own skill or command of the same name.

## Install

Every employee installs the plugin for themselves, on their own laptop or container. The repository is
public, so no GitHub login is needed. Requirements: Claude Code, and Python 3.9 or newer for the safety
hook, the prompt check and the skill scanner (on Windows also Git Bash, which the hooks run in). Without Python the safety
hook blocks Claude's commands and file reads; see [Safety baseline](#safety-baseline).

With Claude's help: clone this repository, open Claude Code in it and say "install it following
INSTRUCTIONS.md". Claude follows [`INSTRUCTIONS.md`](INSTRUCTIONS.md), confirms each command, offers
the settings baseline and verifies the result. By hand:

```bash
claude plugin marketplace add https://github.com/hradniai/claude-code-pack-evisions
claude plugin install evisions@claude-code-pack-evisions
claude plugin list
```

Then run the settings installer if the baseline is wanted (see [Safety baseline](#safety-baseline)) and
restart Claude Code. Use the `https://` URL as written: the short form
`hradniai/claude-code-pack-evisions` makes Claude Code clone over SSH, which fails without an SSH key for
GitHub.

Offline, register a local clone instead: `claude plugin marketplace add <path to the clone>`. The
plugin then runs from that folder, which must stay in place.

## Update

```bash
claude plugin marketplace update claude-code-pack-evisions
claude plugin update evisions@claude-code-pack-evisions
```

Then re-run `evisions-settings --apply` by path, so the new version's installer runs (see
[Safety baseline](#safety-baseline)), restart Claude Code and run `evisions-settings --check`. An update arrives only when the
`version` in the manifests changes. When installed from a local clone, `git pull` in the clone,
`evisions-settings --apply` and a restart are enough.

## Uninstall

```bash
"<repository path>/plugins/evisions/bin/evisions-settings" --remove
claude plugin uninstall evisions@claude-code-pack-evisions
claude plugin marketplace remove claude-code-pack-evisions
```

The first line undoes the settings baseline and must run while the plugin is still there, because the
installer ships with it; when the baseline was never applied, it reports that there is nothing to undo.
Restart Claude Code afterwards.

## Using it with Codex

The same marketplace and the same `evisions` plugin install into OpenAI's Codex CLI, with the safety
part only: the skills and agents above are Claude Code only in this release. The install, the required
hook trust step, the check, the update and the removal are in [`CODEX.md`](CODEX.md); Codex can follow
it itself when opened in a clone of this repository and told "install it following CODEX.md". In short:

```bash
codex plugin marketplace add hradniai/claude-code-pack-evisions
codex plugin add evisions@claude-code-pack-evisions
```

then the settings installer `evisions-codex-settings` from the plugin folder, a Codex restart with
"Trust all and continue" on the "Hooks need review" screen (without it no hook runs), and
`evisions-codex-settings --check`, which must exit 0. Codex stores the short marketplace form as an
HTTPS address, so no SSH key is needed there.

| Codex-side file | What it does |
|---|---|
| `.agents/plugins/marketplace.json` | The marketplace entry Codex reads (Claude Code reads `.claude-plugin/marketplace.json`). |
| `plugins/evisions/.codex-plugin/plugin.json` | The Codex manifest: loads only `hooks/codex-hooks.json`, no skills or agents. |
| `plugins/evisions/hooks/codex-hooks.json` | Three hooks: the safety check before every shell command and `apply_patch` edit, the safety protocol at session start, the local time on every message. |
| `plugins/evisions/hooks/bash_safety.py --runtime codex` | The same safety check in Codex mode: adds destructive git in every form (`git -C` included), `apply_patch` edits of env, credential, shell startup and Codex control files, and attempts to switch the safety off. |
| `plugins/evisions/context/safety-codex.md` | The safety protocol text for Codex. |
| `plugins/evisions/hooks/run-python.ps1`, `hooks/codex_context.py` | The fail-closed Windows launcher and the Windows variant of the context hooks (untested on Windows). |
| `plugins/evisions/bin/evisions-codex-settings` | Settings installer for the Codex home: 21 forbidden command rules (`settings/codex-baseline.rules`), environment filters that keep secret-looking variables away from commands, a marked safety block in the global `AGENTS.md`, and an optional deny-read profile. Dry run by default; `--apply`, `--check`, `--remove`, `--restore-declined`, `--deny-read`, `--profile`. |

## Safety baseline

The baseline narrows what can go wrong when Claude runs commands and reads files. It is a guard against
accidents by an overeager agent, not a barrier against a determined attacker: a deliberately disguised
command can still get past it. It is not a sandbox; its known gaps are listed under [Limits](#limits).

**The safety hook** is part of the plugin and active for everyone who installs it, with no settings
change. Before every `Bash`, `PowerShell`, `Read` and `Grep` call it blocks:

- reading secret values from env files (`.env`, `.env.local`, `.env.production` and any other `.env.*`,
  also through a wildcard such as `.env.*`; `.env.shared` and placeholder files ending in `.example`,
  `.sample`, `.template` or `.dist` stay readable), including inline interpreter code (`python -c`,
  `node -e`, a heredoc) or a loop that reads them;
- reading SSH, AWS and GnuPG keys, git credentials, browser data and similar secret folders;
- recursive deletion anywhere in a command, with or without `-f`: `rm -r`, `find -delete`,
  `Remove-Item -Recurse`, also inside `find -exec`, `xargs` or chained commands. `git rm -r` and
  `docker rm` are exempt; deleting one file (`rm file`) and removing an empty folder (`rmdir`) pass;
- a download piped into a shell or an interpreter, and download-then-run; `sh -c` and `eval` around
  destructive commands;
- dangerous docker flags (privileged mode, a mount of the host root or of a credential folder), disk
  wiping, fork bombs, and a `mv` that would silently overwrite an existing file.

It reads a command the way a shell does (quotes, escapes, subshells, heredocs), so `c''at .env` counts
as a read, while a commit message or a pull request text that merely mentions `.env` is not blocked.
Every block message starts with `evisions safety:`. The hook works in every permission mode, bypass
mode (`--dangerously-skip-permissions`) included. PowerShell coverage is best-effort and untested on
Windows.

**Python.** The hook is `hooks/bash_safety.py`, started through the launcher `hooks/run-python`, and it
needs Python 3.9 or newer. Without Python it fails closed: Bash commands and file reads are blocked with
a message saying Python is missing, because a safety check that cannot start would otherwise let
everything through silently. That makes Python 3.9 or newer an install requirement.

**The settings installer.** Claude Code does not let a plugin set permission rules or other settings
(measured on 2.1.281: a plugin's `settings.json` honours only `agent` and `subagentStatusLine`), so
`evisions-settings` merges the rest of the baseline into the user's `~/.claude/settings.json` (or
`$CLAUDE_CONFIG_DIR/settings.json`). While the plugin is enabled it is on the `PATH` of Claude's Bash
tool; outside a session, run it by path: `<repository path>/plugins/evisions/bin/evisions-settings`,
where `<repository path>` is the local clone or the marketplace's `installLocation` from
`claude plugin marketplace list --json`. Right after an update, use the path form: the command on a
running session's `PATH` may still belong to the version that session started with.

| Command | What it does |
|---|---|
| `evisions-settings` | Dry run: prints the profile and why it was chosen, and what would be added. Writes nothing. |
| `evisions-settings --apply` | Backs up the settings file, then adds only what is missing. Refuses a settings file that is not valid JSON, never removes or changes the user's own entries, and records what it added. Safe to re-run: after a plugin update it brings the settings in line with the new baseline, adding new rules and removing rules it added earlier that the new baseline dropped. An apply that was interrupted is finished by the next run, and a record of what it added that was edited or damaged makes it refuse and write nothing. |
| `evisions-settings --check` | Checks the whole settings file first, one line per problem: an `Error` is something that makes Claude Code ignore the file or skip a value or rule (invalid JSON, `cleanupPeriodDays` below 1, a malformed rule), a `Warning` is a rule that loads but does not work as written (a `\|` in it, `Write(path)` in deny or ask, a wildcard tool name in allow) or that editors validating `settings.json` against the official schema flag (an unknown tool name), and `attribution: false`, which versions before 2.1.281 reject. Then it reports whether the baseline is installed and intact. Exits 1 on an error or a missing baseline item, 0 when there are warnings only. |
| `evisions-settings --apply --restore-declined` | Adds back the baseline items the user removed earlier, which later applies otherwise leave out. |
| `evisions-settings --remove` | Backs up, then undoes exactly what it added. Entries the user changed or added themselves stay. When it created the settings file and nothing else is left in it, it deletes the file instead of leaving an empty one. Without a record it reports that there is nothing to undo. |
| `--no-statusline` | Skips the status line. |
| `--profile standard`, `--profile managed` | Overrides the profile detection. |

Where its files go, all in the Claude Code config folder (`~/.claude/`, or `$CLAUDE_CONFIG_DIR`):
backups next to the settings file as `settings.json.bak-evisions-<timestamp>`, made before every write
to an existing file; the record of what it added in `evisions/settings-applied.json`, which `--check`,
`--remove` and later applies read and the safety protocol tests for; and a copy of the status line
script in `evisions/statusline.py`, so the status line does not depend on where the plugin is installed.
The script keeps a small per-session cache in `evisions/statusline-cache/` (files untouched for two
weeks are pruned); `--remove` deletes it together with the script.

The profile is detected automatically, from whether an administrator policy file
(`managed-settings.json`, or files in `managed-settings.d/`) exists in Claude Code's policy folder for
the platform:

- **`standard`**: no administrator policy on the machine, typically a laptop. Adds permission rules:
  83 allow rules for everyday safe commands, so Claude does not ask about every `ls` or `git status`;
  83 deny rules for destructive commands and secret files; 27 ask rules for installs, pushes,
  deletions and similar. Every git deny rule, and the ask rules for `git push`, `rebase` and `merge`,
  has a `git -C <dir> ...` twin, because a rule such as `Bash(git reset --hard*)` does not match
  `git -C . reset --hard` (measured on Claude Code 2.1.281). Keeps transcripts for 10 years
  (`cleanupPeriodDays: 3650`; Claude Code's default is 30 days, after which `/resume` loses older
  sessions). Turns off telemetry and error reporting
  (`DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`) and feedback surveys. Sets the kit's status line
  (described below) when the user has none.
- **`managed`**: the machine has an administrator's Claude Code policy, for example the company server
  containers. That policy owns permissions, and the administrator's environment owns telemetry, so the
  installer adds no permission rules and sets no environment variables; it applies only transcript
  retention, the feedback survey setting, and the status line when absent. An env var an earlier
  standard install set is taken back when the profile becomes managed.

Neither profile touches bypass mode (`--dangerously-skip-permissions`), and every rule the installer
writes matches the official settings schema, so editors show no error in `settings.json`. Version
1.1.0 turned bypass mode off (`permissions.disableBypassPermissionsMode: "disable"`) and added two
allow rules the schema rejects (`ListMcpResourcesTool`, `ReadMcpResourceTool`); the next `--apply`
takes all three back, but a lock the user set themselves stays. In bypass mode Claude stops asking
before most actions, but the deny rules still block, the safety hook still runs and the ask rules
(installs, pushes, deletions) still ask; allow rules have no effect there.

In both profiles a scalar setting or env var is set only when the user has no value of their own, and
an existing status line is never replaced. On the standard profile, `DISABLE_TELEMETRY` also switches
off Claude Code's feature-flag fetching, so some claude.ai-connected features, such as plugin and skill
sync from claude.ai and Remote Control, stop working. A user who needs them removes that entry from
their settings. The installer treats any baseline item the user removes (a setting, an env var or a rule) as
their choice: later applies leave it out and list it as declined, and
`evisions-settings --apply --restore-declined` brings such items back. Settings take effect after
Claude Code restarts.

**The status line.** It shows the model and effort, the project and git branch, how full the context is,
5-hour and 7-day usage with their reset times, and the session's cost, tokens (subagents included), turns
and time. It adapts to the width of the terminal, which Claude Code passes in `COLUMNS`: a wide window
gets two lines, a narrow one, such as a phone over SSH, gets up to five shorter lines with the model,
context and usage limits first, and only the least important fields are left out when five lines are not
enough.

## Claude Code version check

At every session start (not after `/clear` or compaction) the safety protocol hook compares the
running Claude Code with the newest version published on the npm registry
(`https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags`: the `latest` tag, or
`stable` when the user's settings set `"autoUpdatesChannel": "stable"` or Claude Code runs from
Homebrew's `claude-code` cask). When the running version is older, Claude tells the user at the start
of the conversation, and Claude Code shows a warning line, with the update commands: `claude update`
(native installer), `npm install -g @anthropic-ai/claude-code@latest` (npm), `brew upgrade claude-code`
or `brew upgrade claude-code@latest` (Homebrew), `winget upgrade Anthropic.ClaudeCode` (WinGet). On the
company server containers automatic updates are off and the administrator updates Claude Code.

The lookup is one `curl` request with a 3-second limit. Its answer is cached for 6 hours in the
plugin's data folder (`$CLAUDE_PLUGIN_DATA`, else `evisions/` in the Claude Code config folder), a
failed lookup for 1 hour, so most starts make no request and an offline machine waits at most once an
hour. Offline, without `curl`, or with an unreadable answer, nothing is said; only when the running
version cannot be determined either does Claude get one neutral line. With
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` set, no request is made. The running version comes from the
executable Claude Code names in `CLAUDE_CODE_EXECPATH` (measured on 2.1.282; 2.1.199 does not set it),
else from `claude --version` on the `PATH`. That fallback can read a different install when a machine
has several (measured: 2.1.199 running beside a newer global install produced no warning); with a
single install, as on the company server containers, it reads the running one (measured: 2.1.199 got
the warning line and the `systemMessage`). The Homebrew cask detection and Windows are not measured.

## Files it creates in a project

| Path | Holds |
|---|---|
| `WORKSTATE.md` | Live state: focus, current state, next steps, decisions waiting for confirmation. Rewritten in place. |
| `worklog.md` | Append-only history, newest first. |
| `docs/decision-log.md` | Smaller decisions, one row each, plus a pointer row per ADR. |
| `docs/decisions/NNNN-title.md` | ADRs, one decision each. |
| `docs/features/` | Feature docs (an existing root `features/` is used instead when the project has one). |
| `docs/prd/` | PRDs, `<slug>-prd.md`, written by `/prd`. |
| `BUGS.md` | Bugs found along the way, written by `evisions:bug-log`, newest first. |
| `TECH-DEBT.md` | Code that works but is costly or risky, written by `evisions:tech-debt-log`, newest first. |
| `research/` | Research reports, `{topic}-research-{YYYY-MM-DD}.md`. |
| `prompts/` | Prompts written by the prompt engineer. |

Journals are always written in English, whatever language the user speaks; the conversation follows
the user's language. Only decisions the user made or confirmed are recorded, and a reason the user did
not give is recorded as "not stated". Bugs and tech debt differ: noticed along the way, they are
captured as `BUG-TODO:` or `DEBT-TODO:` lines in `WORKSTATE.md`, and `/end` writes them into `BUGS.md`
and `TECH-DEBT.md` without an approval step. Where an environment rule restricts where files may be written,
that rule wins over the plugin's documentation standard.

## Limits

- The prompt check is hygiene only: a pasted key, no output format, no fallback for missing input,
  unfenced placeholders. It never says whether a prompt works. For that, run the prompt on 3-5 real
  inputs and compare the outputs with what a good result looks like.
- Research uses web search and fetch, and cites a source for every claim. A claim without a source is
  reported as unsourced, never filled in.
- No model facts are bundled (no model IDs, prices or rankings), because they go stale faster than the
  plugin ships. A model recommendation must come from an official vendor page fetched in that session,
  or be labelled unverified.
- The safety hook guards against accidents by an overeager agent, not against a determined attacker.
  It and the deny rules catch commands that name a secret file, so these can still expose an env
  file's values: a directory-wide search (for example `grep -r KEY .`), `git show HEAD:.env`,
  `docker compose config` (which prints the resolved environment), and copying an env file under
  another name and reading the copy.
- A repository's own `.claude/settings.json` can set `disableAllHooks: true`, which switches off user
  and plugin hooks, this safety hook included. Only an administrator's managed policy can prevent that.
- Windows: the hooks need Git Bash and Python. PowerShell coverage is best-effort and untested on
  Windows.
- The skill scanner is static: it never runs the target, so behaviour that is new or dormant can evade
  it. `PERMIT WITHIN COVERAGE` is an admission decision, not a safety certificate. It needs its
  evidence directory (`$SKILL_SCANNER_EVIDENCE_DIR`, else `~/.skill-scanner/evidence`) to exist before
  it saves a report.
- On a machine with an administrator policy, the plugin's hook runs in addition to the administrator's
  own; both may report a block for the same command.

## Companion plugins

Public add-ons fit the kit in two groups: three for planning and building, and a development group.
They are optional and not part of this repository: nothing of theirs is copied here, each installs from
its author's source under its author's license and gets its author's updates, and the kit's skills use
them only when they are installed. Every installed plugin costs context in every session, so install
only what the person actually uses. `INSTRUCTIONS.md` Step 8 installs each one only after the user
confirms.

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

**Development.** All from the official Anthropic marketplace, each installed with
`claude plugin install <name>@claude-plugins-official`:

| Companion | What it adds |
|---|---|
| `security-guidance` | Pattern-based security warnings while code is being edited. |
| `context7` | Up-to-date library documentation instead of the model's memory. |
| `playwright` | Drives a real browser to check that a web page actually works. |
| `code-review` | Review of pull requests by several agents. |
| `frontend-design` | Production-grade frontend and landing pages. |

The kit ships no debugger: debugging is covered by Superpowers' `systematic-debugging` skill.

## Maintaining

```bash
python3 -m unittest            # run inside plugins/evisions/tests
claude plugin validate .
claude plugin validate plugins/evisions
```

The unit tests cover the hooks, the settings installer, the helpers and the skill scanner as well as
the plugin contract. Every release
raises `version` in `.claude-plugin/marketplace.json`, `plugins/evisions/.claude-plugin/plugin.json`
and `plugins/evisions/.codex-plugin/plugin.json`; the contract test fails when they disagree.

Left out of the general starter pack's kernel on purpose: a large-file read guard (it blocked
screenshots, and Claude Code's Read tool now truncates large files itself), four pipe deny rules that
never matched on current Claude Code, and the notes and inbox automation hooks.

## License

All rights reserved; use is limited to the audience named in [`LICENSE`](LICENSE).
