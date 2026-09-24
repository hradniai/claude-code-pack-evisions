# Install instructions for Claude

<purpose>
You are reading this because a user opened Claude Code in a clone of this repository, or pointed you at
this file, and asked you to install it ("install it", "nainstaluj to"). One employee installs the
`evisions` plugin for themselves, on their own laptop or container, through the plugin CLI; then you
verify it. The user may not be a developer: speak their language, explain each step in one plain
sentence, and never hand them a technical choice without your recommendation.
</purpose>

<hard_rules>
- **Show every command and wait for the user's yes before running it.** Read-only checks are the
  exception and may run right away. Why: installing changes their Claude Code for every project.
- **Report each result in one line.** Why: the user needs to see what happened, not a transcript.
- **If a step fails, stop, show the exact error, and do not continue** to the next step. Why: a half
  install leaves a state that is harder to diagnose than the original error.
- **Change nothing by hand.** Never edit `settings.json`, never copy files into `~/.claude/`, never
  delete or rename anything the user already has; the `claude plugin` CLI does all the writing. Why: the
  plugin is namespaced and must not replace the user's own files.
- **Never use `sudo`**, and never read, print or ask for a token or password. Nothing here needs one:
  this repository and every companion below are public.
- **Do not work around a refusal.** If the environment's own policy blocks adding a marketplace or
  installing a plugin, stop and tell the user. Only whoever manages that machine's Claude Code policy
  can lift it.
</hard_rules>

## What gets installed

One Claude Code plugin, `evisions`, from the marketplace `claude-code-pack-evisions` that this
repository defines. It adds skills (`checkpoint`, `end`, `adr`, `prompt-eval`, `research`,
`socratic-brainstormer`), agents (`features-documenter`, `prompt-engineer`,
`research-analyst`, `research-lead`) and one `SessionStart` hook that loads a documentation standard at
the start of each session. The files it writes into projects are always English; the conversation
follows the user's language. It ships no settings, no permission rules and no safety hooks: the user's
existing setup stays in charge. Tell the user this in two sentences before Step 1.

## Step 1: Pre-flight (read-only, run without asking)

Run these and report the results together:

```bash
claude --version
claude plugin list
claude plugin marketplace list
```

Then decide:

- `claude` not found: stop. Claude Code must be installed and on the `PATH` first.
- `claude plugin list` already shows `evisions@claude-code-pack-evisions`: the plugin is installed.
  Offer the update procedure (below) instead of a new install.
- `claude plugin marketplace list` already shows `claude-code-pack-evisions`: skip Step 3.
- A different plugin named `evisions` from another marketplace: tell the user; the two would share the
  `evisions:` prefix. Ask whether to continue.

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
- `claude plugin details` lists 6 skills, 4 agents and a `SessionStart` hook.
- Both `validate` runs exit with code 0. Warnings are reported to the user, errors stop the install.

## Step 6: Check for name collisions

The plugin never overwrites anything, because its components are namespaced (`evisions:checkpoint`,
`evisions:research-analyst`). But if the user has their own skill, command or agent with the same name,
the short command (for example `/checkpoint`) runs THEIR version, and only the full name reaches the
plugin's. Look (read-only):

```bash
C="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
for n in checkpoint end adr research prompt-eval socratic-brainstormer; do
  for p in "$C/skills/$n" "$C/commands/$n.md"; do [ -e "$p" ] && echo "$p"; done
done
for n in features-documenter prompt-engineer research-analyst research-lead; do
  [ -e "$C/agents/$n.md" ] && echo "$C/agents/$n.md"
done
```

For every hit, tell the user: their own file is untouched; to use the plugin's version they type the
full name (`/evisions:checkpoint`), or they remove their own copy themselves if they no longer want it.
Never delete or rename it for them.

## Step 7: Optional companion plugins

Three public add-ons fit this kit. They are NOT part of this repository: nothing of theirs is copied
here, each installs from its author's own source under its author's license and gets its author's
updates. The kit works without them; its skills use a companion only when it is installed. Offer each
in one or two sentences, and install only what the user confirms, with the same rule as above: show
the command, wait for yes, report one line. All three sources are public, so no GitHub login is needed
when the commands below are used as written.

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

## Step 8: Restart and first use

Tell the user to quit Claude Code and start it again. The plugin's skills and its `SessionStart` hook
load at session start, and the documentation standard reaches a session only through that hook. After
the restart, typing `/evisions:` shows the plugin's commands. Point them to `USER-MANUAL.md`
for how to work with it.

## Update

Source A (GitHub):

```bash
claude plugin marketplace update claude-code-pack-evisions
claude plugin update evisions@claude-code-pack-evisions
```

Source B (local clone): `git -C "<clone path>" pull`. No plugin command is needed, the plugin runs
from the folder.

Either way, restart Claude Code afterwards. Under source A an update arrives only when a new version
number is released; `claude plugin list` shows the installed version.

## Uninstall

```bash
claude plugin uninstall evisions@claude-code-pack-evisions
claude plugin marketplace remove claude-code-pack-evisions
```

Companion plugins are not removed with the kit; each is removed with its own command
(`claude plugin uninstall <plugin id>`, or `npx skills@latest remove` for the grilling skills).

The files the plugin created inside projects (`WORKSTATE.md`, `worklog.md`, `docs/decision-log.md`,
`docs/decisions/`, `docs/features/`, `research/`) stay; they belong to the projects. Restart Claude
Code afterwards.

<hard_rules_repeat>
Show each command and wait for yes; report one line per result; stop on the first failure. Never edit
settings or copy files by hand, never delete the user's own skills, agents or commands, never `sudo`,
never touch a token. A policy refusal is reported, not worked around.
</hard_rules_repeat>
