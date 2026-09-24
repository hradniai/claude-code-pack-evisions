# Install instructions for Claude

<purpose>
You are reading this because a user opened Claude Code in a clone of this repository, or pointed you at
this file, and asked you to install it ("install it", "nainstaluj to"). One employee installs the
`evisions` plugin for themselves, on their own laptop or container, through the plugin CLI, then
optionally its settings baseline through the kit's own installer; then you verify it. The user may not
be a developer: speak their language, explain each step in one plain sentence, and never hand them a
technical choice without your recommendation.
</purpose>

<hard_rules>
- **Show every command and wait for the user's yes before running it.** Read-only checks are the
  exception and may run right away. Why: installing changes their Claude Code for every project.
- **Report each result in one line.** Why: the user needs to see what happened, not a transcript.
- **If a step fails, stop, show the exact error, and do not continue** to the next step. Why: a half
  install leaves a state that is harder to diagnose than the original error.
- **Change nothing by hand.** Only two tools write: the `claude plugin` CLI and the kit's settings
  installer `evisions-settings`, which backs up `settings.json` before it writes and can undo exactly
  what it added. Never edit `settings.json` yourself, never copy files into `~/.claude/`, never delete
  or rename anything the user already has. Why: the plugin is namespaced and must not replace the
  user's own files, and a settings change made by hand has no backup and no record to undo it from.
- **Never use `sudo`**, and never read, print or ask for a token or password. Nothing here needs one:
  this repository and every companion below are public.
- **Do not work around a refusal.** If the environment's own policy blocks adding a marketplace or
  installing a plugin, or the kit's safety check blocks a command (a message starting
  `evisions safety:`), stop and tell the user. Only whoever manages that machine's Claude Code policy
  can lift a policy.
</hard_rules>

## What gets installed

One Claude Code plugin, `evisions`, from the marketplace `claude-code-pack-evisions` that this
repository defines. It adds:

- 10 skills (`checkpoint`, `end`, `adr`, `prd`, `bug-log`, `tech-debt-log`, `prompt-eval`, `research`,
  `socratic-brainstormer`, `skill-scanner`) and 6 agents (`features-documenter`, `prompt-engineer`,
  `research-analyst`, `research-lead`, `code-reviewer`, `security-auditor`);
- hooks: at session start, three handlers (the documentation standard, a map of which skill or agent
  does which job, and a short safety protocol); on every message, the current local time; and before
  every `Bash`, `PowerShell`, `Read` and `Grep` call, a safety check that blocks reading secret files
  (env files, SSH, AWS and GnuPG keys, git credentials), recursive deletion with or without `-f`
  (`rm -r`, `find -delete`, `Remove-Item -Recurse`) and downloads piped into a shell. It is a guard
  against accidents by an overeager agent, not a barrier against a determined attacker. The check works
  in every permission mode, bypass mode included, and needs Python 3.9 or newer: without Python it
  blocks commands and file reads instead of letting them through unchecked;
- two helper commands on the `PATH` of Claude's Bash tool while the plugin is enabled: `list-env-keys`
  (the names of the keys in env files, never a value) and `evisions-settings` (the settings installer).

The files it writes into projects are always English; the conversation follows the user's language.

Claude Code does not let a plugin set permission rules or other settings, so the matching settings
baseline is a separate, recommended step (Step 7): `evisions-settings` merges it into the user's
`settings.json` after a dry run and the user's yes, backs up first, and can undo itself. Tell the user
this in two or three sentences before Step 1.

## Step 1: Pre-flight (read-only, run without asking)

Run these and report the results together:

```bash
claude --version
claude plugin list
claude plugin marketplace list
python3 --version
```

If `python3 --version` fails, run `python --version`, and on Windows also `py -3 --version` (there the
interpreter is often named `python`, or reached through the `py` launcher). The kit's hooks try the
same three names, in this order.

Then decide:

- `claude` not found: stop. Claude Code must be installed and on the `PATH` first.
- `claude plugin list` already shows `evisions@claude-code-pack-evisions`: the plugin is installed.
  Offer the update procedure (below) instead of a new install.
- `claude plugin marketplace list` already shows `claude-code-pack-evisions`: skip Step 3.
- A different plugin named `evisions` from another marketplace: tell the user; the two would share the
  `evisions:` prefix. Ask whether to continue.
- Python missing (none of these commands works) or older than 3.9: stop. Explain that the kit's safety check
  runs on Python and, without it, blocks Claude's commands and file reads, so the plugin must not be
  enabled before Python is there. The user installs it themselves, then Step 1 runs again:
  - macOS: `xcode-select --install` (Apple's command line tools include `python3`), or the installer
    from python.org;
  - Windows: the installer from python.org with "Add python.exe to PATH" ticked, then close and reopen
    Git Bash;
  - Linux: the distribution's `python3` package.

## Step 2: Choose where the plugin is installed from

Recommend A. Offer B only when GitHub cannot be reached from this machine.

**A. From GitHub (default).** The repository is public, so no login is needed. A local clone, if there
is one, can be deleted afterwards, and updates arrive through two commands whenever a new version is
released.

**B. From a local clone (offline alternative).** Claude Code then runs the plugin directly from that
folder, so the folder must stay where it is: deleting or moving it breaks the plugin. Updates arrive by
pulling the folder (`git pull`) when online and restarting Claude Code.

Check that A works (read-only):

```bash
GIT_TERMINAL_PROMPT=0 git ls-remote --heads https://github.com/hradniai/claude-code-pack-evisions
```

It lists branches: use A. It fails: show the error; if a local clone exists, offer B.

## Step 3: Register the marketplace

Source A, with the `https://` URL exactly as written:

```bash
claude plugin marketplace add https://github.com/hradniai/claude-code-pack-evisions
```

Do not shorten it to `hradniai/claude-code-pack-evisions`: Claude Code clones the short form over SSH,
which fails on a machine without an SSH key for GitHub.

Source B: resolve the absolute path of the clone with `pwd` in it (on Windows in Git Bash, `pwd -W`,
which prints the `C:/Users/...` form instead of `/c/Users/...`), then:

```bash
claude plugin marketplace add "<absolute path of the clone>"
```

## Step 4: Install the plugin

```bash
claude plugin install evisions@claude-code-pack-evisions
```

This installs for the user across all their projects (the default `user` scope).

## Step 5: Verify

```bash
claude plugin list
claude plugin details evisions@claude-code-pack-evisions
claude plugin marketplace list --json
claude plugin validate "<repository path>"
claude plugin validate "<repository path>/plugins/evisions"
```

`<repository path>` is the local clone for source B. For source A it is the `installLocation` that
`claude plugin marketplace list --json` reports for `claude-code-pack-evisions`.

Pass means all of these:

- `claude plugin list` shows `evisions@claude-code-pack-evisions` with status `enabled`.
- `claude plugin details` lists 10 skills, 6 agents and hooks on `SessionStart` (three handlers),
  `UserPromptSubmit` and `PreToolUse`.
- Both `validate` runs exit with code 0. Warnings are reported to the user, errors stop the install.

## Step 6: Check for name collisions

The plugin never overwrites anything, because its components are namespaced (`evisions:checkpoint`,
`evisions:research-analyst`). But if the user has their own skill, command or agent with the same name,
the short command (for example `/checkpoint`) runs THEIR version, and only the full name reaches the
plugin's. Look (read-only):

```bash
C="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
for n in checkpoint end adr prd bug-log tech-debt-log research prompt-eval socratic-brainstormer skill-scanner; do
  for p in "$C/skills/$n" "$C/commands/$n.md"; do [ -e "$p" ] && echo "$p"; done
done
for n in features-documenter prompt-engineer research-analyst research-lead code-reviewer security-auditor; do
  [ -e "$C/agents/$n.md" ] && echo "$C/agents/$n.md"
done
```

For every hit, tell the user: their own file is untouched; to use the plugin's version they type the
full name (`/evisions:checkpoint`), or they remove their own copy themselves if they no longer want it.
Never delete or rename it for them.

## Step 7: Settings baseline (recommended)

Claude Code does not let a plugin set permission rules or other settings, so the kit ships an
installer, `evisions-settings`, that merges a baseline into the user's `settings.json` (in
`~/.claude/`, or in `$CLAUDE_CONFIG_DIR` when that is set). It detects one of two profiles by itself:

- **`standard`**, when the machine has no administrator's Claude Code policy (typically a laptop):
  85 allow, 83 deny and 27 ask permission rules (everyday safe commands such as `ls` or `git status`
  run without a question, destructive commands and secret files are refused, installs, pushes and
  deletions ask first; the git rules also cover the `git -C <dir> ...` form), bypass mode turned off,
  transcripts kept for 10 years instead of Claude Code's default 30 days, telemetry, error reporting
  and feedback surveys off, and a status line when the user has none.
- **`managed`**, when an administrator's Claude Code policy is present (for example the company server
  containers): that policy owns permissions and bypass mode and the administrator's environment owns
  telemetry, so the installer adds no permission rules, leaves bypass mode alone and sets no
  environment variables; it applies only transcript retention, the feedback survey setting and the
  status line.

Explain it to the user in two or three plain sentences: what it adds, which profile applies, and, on
the standard profile, what turning bypass mode off means: Claude will ask before actions again instead
of running everything unasked, and starting Claude Code with `--dangerously-skip-permissions` no longer
skips the questions.

Run the dry run first. It is read-only and may run without asking; it prints the detected profile, why
it was chosen, and what would be added, and writes nothing:

```bash
"<repository path>/plugins/evisions/bin/evisions-settings"
```

`<repository path>` is the same as in Step 5. On Windows in Git Bash, use the same quoted path with
forward slashes (`C:/Users/...`).

Summarize the plan for the user in plain words and recommend applying it. On the standard profile,
mention one trade-off: turning telemetry off (`DISABLE_TELEMETRY`) also switches off some claude.ai-connected features, such as plugin
and skill sync from claude.ai and Remote Control. A user who needs them can remove that entry from their
settings afterwards; the installer treats a removed baseline item as the user's choice, lists it as
declined and leaves it out on every later `--apply` (`--apply --restore-declined` brings it back). Only
after their yes:

```bash
"<repository path>/plugins/evisions/bin/evisions-settings" --apply
"<repository path>/plugins/evisions/bin/evisions-settings" --check
```

`--apply` backs up the settings file first (`settings.json.bak-evisions-<timestamp>`), adds only what is
missing, and never removes or changes the user's own entries. `--check` must exit with code 0. The
settings take effect after the restart in Step 9.

- The user does not want the status line: add `--no-statusline` to the dry run and to `--apply`. An
  existing status line is never replaced either way.
- The detected profile is wrong (the installer looks for an administrator's policy file on this
  machine; the user knows there is a policy it did not find, or that there is none): run the dry run
  again with `--profile managed` or `--profile standard`, and use the same flag with `--apply`.
- The dry run reports that the settings file is not valid JSON, or holds values Claude Code does not
  accept: stop and show the message. The installer refuses to touch such a file. Do not fix it unless
  the user explicitly asks you to; once it is valid, run the dry run again.
- The dry run notes that the previous `--apply` stopped before it finished: the next `--apply`
  completes it; recommend applying.
- The installer refuses because its record of what it added (`evisions/settings-applied.json` in the
  config folder) was edited or damaged: stop and show the message, which says what to do. Do not
  delete or edit the record yourself; that is the user's call.
- The user declines: skip this step. It can run at any time later with the same commands.

## Step 8: Optional companion plugins

Public add-ons fit this kit: three for planning and building (Superpowers, Replan, Grilling) and an
optional Development group. They are NOT part of this repository: nothing of theirs is copied here,
each installs from its author's own source under its author's license and gets its author's updates.
The kit works without them; its skills use a companion only when it is installed. Offer each in one or
two sentences, and install only what the user confirms, with the same rule as above: show the command,
wait for yes, report one line. Tell the user that every installed plugin costs context in every
session, so they should pick only what they will use. All sources are public, so no GitHub login is
needed when the commands below are used as written.

### Superpowers: building software

`superpowers@claude-plugins-official`, from the official Anthropic marketplace. A disciplined build
workflow: brainstorming, then a written spec, a plan and its execution, plus systematic debugging and
test-driven development. Tell the user plainly that it injects its own instructions at every session
start and pushes Claude to reach for skills eagerly; that is expected, not a fault.

```bash
claude plugin marketplace list
claude plugin install superpowers@claude-plugins-official
```

The first command must list `claude-plugins-official`. Claude Code adds it by itself on the first
interactive start, so it is normally there; a configuration that was never started interactively
lists no marketplaces at all. If it is missing, add it from its official source first (the prefix
makes Claude Code clone over HTTPS, needed when the machine has no SSH key for GitHub):

```bash
CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 claude plugin marketplace add anthropics/claude-plugins-official
```

### Replan: checking a plan, then the result

`replan@claude-replan` by Jiří George Dolejš, from the public repository `kojott/claude-replan`.
`/replan` sends a finished plan to several review subagents in parallel before any work starts;
`/recheck` verifies afterwards that the implementation matches the plan. Its license is MIT with the
Commons Clause, the author's own terms; this kit only points at it.

```bash
claude plugin marketplace add https://github.com/kojott/claude-replan
claude plugin install replan@claude-replan
```

Use the `https://` form as written: the short form `kojott/claude-replan` makes Claude Code clone over
SSH, which fails on a machine without an SSH key for GitHub. Update later with
`claude plugin marketplace update claude-replan` and `claude plugin update replan@claude-replan`.

### Grilling: stress-testing a formed plan

Matt Pocock's `grilling` and `grill-me` skills (MIT, public repository `mattpocock/skills`). `/grill-me`
starts rounds of questions about a plan or decision, each question with a recommended answer, until
nothing is left assumed; Claude also uses `grilling` by itself when the user asks to be grilled. The
kit's `socratic-brainstormer` hands a formed plan over to it.

Default route: only these two skills, installed as the user's own skills into `~/.claude/skills/`
through the `skills` installer. It needs Node.js with `npx`, and the command downloads and runs that
installer from npm; say so before asking for the yes. First check (read-only) that `npx` exists and
that the user has no skills of these names yet; if either folder exists, stop and ask, because the
installer writes to exactly those folders:

```bash
npx --version
ls -d ~/.claude/skills/grilling ~/.claude/skills/grill-me
```

```bash
npx skills@latest add mattpocock/skills --skill grilling --skill grill-me -g -a claude-code -y
```

Update later with `npx skills@latest update grilling grill-me -g`; remove with
`npx skills@latest remove grilling grill-me -g -a claude-code -y`.

Fallback, only when Node.js is missing: the whole plugin from the official marketplace.

```bash
claude plugin install mattpocock-skills@claude-plugins-official
```

The commands then carry its prefix, `/mattpocock-skills:grill-me`, and it updates with the official
marketplace. Tell the user the cost before the yes: it adds 25 skills, one of them a generic `research`
skill that competes with `evisions:research` for the same requests, and every installed skill costs
context in every session. That is why it is the fallback, not the default.

### Development: optional companions for building software

Offer this group to a user who writes or changes code or web pages; skip it for anyone else. All five
come from the official Anthropic marketplace, so first make the same marketplace check as for
Superpowers (`claude plugin marketplace list` must list `claude-plugins-official`). Offer each in one
sentence and install only the ones the user picks, one command each:

```bash
claude plugin install <name>@claude-plugins-official
```

- `security-guidance`: pattern-based security warnings while code is being edited.
- `context7`: up-to-date library documentation instead of the model's memory.
- `playwright`: drives a real browser to check that a web page actually works.
- `code-review`: review of pull requests by several agents.
- `frontend-design`: production-grade frontend and landing pages.

Debugging needs no extra plugin: Superpowers' `systematic-debugging` skill covers it, and the kit ships
no debugger of its own.

## Step 9: Restart and first use

Tell the user to quit Claude Code and start it again. The plugin's skills and hooks load at session
start and settings are read at startup, so the documentation standard, the safety check and the
settings baseline all take effect only after the restart. After the restart, typing `/evisions:` shows
the plugin's commands. Point them to `USER-MANUAL.md` for how to work with it.

Offer an optional first-use check for the new session. It needs a throwaway file with a fake key;
create it now, before the restart (show the command and wait for yes; if it is refused, skip the
check):

```bash
mkdir -p "$HOME/evisions-safety-check" && printf 'DUMMY_KEY=not-a-secret\n' > "$HOME/evisions-safety-check/.env"
```

Then, in the new session, the user asks:

1. "What time is it?" Claude answers with the current local time, not only the date.
2. "Show me the contents of ~/evisions-safety-check/.env". Claude must refuse and must not show the
   value; the block message starts with `evisions safety:`.

Afterwards the user deletes the `evisions-safety-check` folder themselves.

## Update

Source A (GitHub):

```bash
claude plugin marketplace update claude-code-pack-evisions
claude plugin update evisions@claude-code-pack-evisions
```

Source B (local clone): `git -C "<clone path>" pull`. No plugin command is needed, the plugin runs
from the folder.

Then bring the settings in line with the new version: Step 7 again, the dry run first and `--apply`
after the user's yes, with the same `--no-statusline` or `--profile` flag as before. `--apply` adds the
new baseline's rules and removes rules it added earlier that the new baseline dropped; the user's own
entries stay. Use the path form from Step 7 even inside a session where the plugin is enabled: the bare
`evisions-settings` on that session's `PATH` may still belong to the version the session started with.

Either way, restart Claude Code afterwards. Under source A an update arrives only when a new version
number is released; `claude plugin list` shows the installed version.

## Uninstall

First undo the settings baseline. The installer ships with the plugin, so it must run before the plugin
is removed; when the baseline was never applied, it only reports that there is nothing to undo:

```bash
"<repository path>/plugins/evisions/bin/evisions-settings" --remove
```

It backs up the settings file first and removes exactly what it added, including its copy of the
status line script; entries the user changed or added themselves stay. When it created the settings
file itself and nothing else is left in it, it deletes the file instead of leaving an empty one. Then
remove the plugin and the marketplace:

```bash
claude plugin uninstall evisions@claude-code-pack-evisions
claude plugin marketplace remove claude-code-pack-evisions
```

Companion plugins are not removed with the kit; each is removed with its own command
(`claude plugin uninstall <plugin id>`, or `npx skills@latest remove` for the grilling skills).

The files the plugin created inside projects (`WORKSTATE.md`, `worklog.md`, `docs/decision-log.md`,
`docs/decisions/`, `docs/features/`, `docs/prd/`, `BUGS.md`, `TECH-DEBT.md`, `research/`, `prompts/`)
stay; they belong to the projects. Restart Claude
Code afterwards.

<hard_rules_repeat>
Show each command and wait for yes; report one line per result; stop on the first failure. Only the
`claude plugin` CLI and `evisions-settings` write: never edit settings or copy files by hand, never
delete the user's own files, skills, agents or commands, never `sudo`, never touch a token. A policy
refusal or an `evisions safety:` block is reported, not worked around.
</hard_rules_repeat>
