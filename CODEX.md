# The evisions safety baseline for Codex CLI

<purpose>
This file has two readers. A person can follow it by hand. Codex follows it when the user opens Codex
in a clone of this repository and says "install it following CODEX.md": then you, Codex, walk the user
through the steps below one at a time and verify the result. The user may not be a developer: speak
their language, explain each step in one plain sentence, and never hand them a technical choice without
your recommendation.

The repository and the `evisions` plugin that give Claude Code users the kit also give Codex CLI users
its safety baseline. Codex gets the safety part only: the kit's documentation skills and agents are
Claude Code only in this release, so no `/checkpoint`, `/end` or other kit command exists in Codex.
</purpose>

<hard_rules>
- **Show every command and wait for the user's yes before running it.** Read-only checks are the
  exception and may run right away. Why: these steps change Codex for every project on this machine.
- **Report each result in one line.** Why: the user needs to see what happened, not a transcript.
- **If a step fails, stop, show the exact error, and do not continue** to the next step. Why: a half
  install leaves a state that is harder to diagnose than the original error.
- **Change nothing by hand.** Only two tools write: the `codex plugin` CLI and the kit's settings
  installer `evisions-codex-settings`, which backs up every file before it changes it and can undo
  exactly what it added. Never edit `config.toml`, `AGENTS.md`, `hooks.json` or the `rules/` folder in
  the Codex home yourself, never copy files into it, and never delete or rename anything the user
  already has. Why: a change made by hand has no backup and no record to undo it from.
- **Never trust the hooks on the user's behalf.** Trusting them is the user's own choice on Codex's
  review screen (Step 7). Never write hook trust entries, and never start Codex with
  `--dangerously-bypass-hook-trust`, `--dangerously-bypass-approvals-and-sandbox` or `--yolo`. Why: the
  trust step is how the user decides which code runs before every command.
- **Never use `sudo`**, and never read, print or ask for a token, a password or the Codex login file
  (`auth.json`). Nothing here needs one: the repository is public, and the user signs in to Codex
  themselves.
- **Do not work around a refusal.** If a policy on the machine blocks adding the marketplace or the
  plugin, or the safety check blocks a command (a message starting `evisions safety:`), stop and tell
  the user. Only whoever manages that machine can lift a policy.
</hard_rules>

## What gets installed

One Codex plugin, `evisions`, from the marketplace `claude-code-pack-evisions` that this repository
defines in `.agents/plugins/marketplace.json`. Its Codex manifest (`plugins/evisions/.codex-plugin/plugin.json`)
loads only the Codex hook file `plugins/evisions/hooks/codex-hooks.json`, which holds three hooks:

- **the safety check**, before every shell command and every `apply_patch` file edit: it blocks secret
  reads, destructive commands and attempts to switch the safety off (see
  [What the safety check blocks](#what-the-safety-check-blocks));
- **the safety protocol**, at session start, after `/clear` and after compaction: it tells Codex how to
  handle a block and how to treat secrets, and gives it the absolute paths of the plugin's two tools,
  because Codex does not put the plugin's `bin/` folder on the `PATH`;
- **the time hook**, on every message: the current local time.

The plugin's `bin/` folder holds two tools, always run by their absolute path: `list-env-keys` (the
names of the keys in env files, never a value) and `evisions-codex-settings` (the settings installer).

A Codex plugin cannot ship command rules, `config.toml` keys or global instructions, so the matching
settings baseline is a separate, recommended step (Step 5).

Tell the user this in two or three sentences before Step 1: what it is, that the hooks do nothing until
the user trusts them in Codex (Step 7), and that it is a guard against accidents by an overeager agent,
not a barrier against a determined attacker.

## Requirements

- **Codex CLI, signed in with the ChatGPT account.** Installing Codex itself is covered by OpenAI's
  guide: https://learn.chatgpt.com/docs/codex/cli
- **Python 3.9 or newer.** The safety check runs on Python and fails closed without it: shell commands
  and file edits are blocked with an `evisions safety:` message saying that Python is missing.
- **macOS and Linux:** bash, which both have. **Windows:** Windows PowerShell, which ships with
  Windows; the hooks run through it. The two tools in `bin/` are bash scripts, so on Windows the
  settings installer runs through Python directly (Step 5) and `list-env-keys` needs Git Bash.
- Measured on codex-cli 0.154 on Linux. macOS and Windows are not measured yet, and the Windows path is
  untested.

## Step 1: Pre-flight (read-only, run without asking)

Run these and report the results together:

```bash
codex --version
codex login status
codex plugin list
codex plugin marketplace list
python3 --version
```

On Windows, run `py -3 --version` and `python --version` in place of `python3 --version`.

Then decide:

- `codex` not found: stop. Codex CLI must be installed and on the `PATH` first.
- `codex login status` does not report a ChatGPT sign-in: the user signs in themselves with
  `codex login`, which opens the browser. Never ask for or handle the login data.
- `codex plugin list` already shows `evisions@claude-code-pack-evisions` as installed: offer the
  [Update](#update) procedure instead of a new install.
- `codex plugin marketplace list` already shows `claude-code-pack-evisions`: skip Step 2.
- Python missing (no command works) or older than 3.9: stop. Explain that the safety check runs on
  Python and, without it, blocks every shell command and file edit, so the plugin must not be installed
  before Python is there. The user installs it themselves, then Step 1 runs again:
  - macOS: `xcode-select --install` (Apple's command line tools include `python3`), or the installer
    from python.org;
  - Windows: the installer from python.org with "Add python.exe to PATH" ticked, then a new PowerShell
    window;
  - Linux: the distribution's `python3` package.

## Step 2: Register the marketplace

```bash
codex plugin marketplace add hradniai/claude-code-pack-evisions
```

The short form is right for Codex: measured on 0.154, Codex stores it as the HTTPS address
`https://github.com/hradniai/claude-code-pack-evisions.git` and clones it with no login and no SSH key.
(Claude Code differs, which is why `INSTRUCTIONS.md` writes the full `https://` address there.)

If the command is refused by a policy, for example an administrator's list of allowed marketplace
sources, stop and report it.

## Step 3: Install the plugin

```bash
codex plugin add evisions@claude-code-pack-evisions
codex plugin list -m claude-code-pack-evisions
```

Pass: the list shows `evisions@claude-code-pack-evisions` as `installed, enabled`, version `1.2.0` or
newer.

## Step 4: Find the plugin folder (read-only, run without asking)

Codex keeps the installed copy in `<Codex home>/plugins/cache/claude-code-pack-evisions/evisions/<version>/`.
The Codex home is `~/.codex`, or the folder named in `CODEX_HOME` when that is set; `<version>` is the
version column from Step 3.

```bash
ls "${CODEX_HOME:-$HOME/.codex}/plugins/cache/claude-code-pack-evisions/evisions/"
```

On Windows: `Get-ChildItem "$HOME\.codex\plugins\cache\claude-code-pack-evisions\evisions"`.

From here on, `<plugin folder>` means that path with the version, for example
`~/.codex/plugins/cache/claude-code-pack-evisions/evisions/1.2.0`. Once the hooks are trusted, every
Codex session also receives the absolute paths of both tools at session start.

## Step 5: Settings baseline (recommended)

`evisions-codex-settings` adds to the Codex home (`~/.codex`, or `$CODEX_HOME`):

- **command rules** in `rules/evisions.rules`: 21 rules that make Codex refuse destructive commands
  outright, in every approval mode, `--yolo` included: recursive deletion, forced moves, `sudo`,
  ownership and permission changes, disk formatting, shutdown, `pkill`, `launchctl`, `eval`, `export`,
  `npm publish`, global npm installs, and destructive git (force push, `reset --hard`, `checkout --`, `clean -f`, `branch -D`,
  `commit --no-verify`, `-n` and `-a`);
- **environment filters** in `config.toml` (`[shell_environment_policy]`): variables whose names look
  like secrets (containing `KEY`, `SECRET`, `TOKEN`, `PASSWORD` or `CREDENTIAL`, plus `DATABASE_URL`,
  `AWS_*`, `AZURE_*` and similar) never reach the commands Codex runs. A program that needs a key loads
  it itself from its own configuration;
- **a marked safety block** in the active global instructions file (`AGENTS.md` in the Codex home, or
  `AGENTS.override.md` when that one has content): the block protocol in brief, and a canary. When the
  plugin's safety context did not arrive at session start, Codex tells the user that the evisions
  safety hooks are not active before it does anything else;
- **optionally, with `--deny-read`**, a sandbox profile that denies reading credential folders
  (`~/.ssh`, `~/.aws`, `~/.gnupg` and others) and `.env` files; its conditions are in the last items
  of the list below.

It never writes `approval_policy`, `sandbox_mode` or hook trust. It changes `config.toml` only through
Codex's own configuration writer, so comments and unrelated settings survive, and it reads every write
back. Without a working `codex` command it stops and writes nothing.

It detects one of two profiles by itself:

- **`standard`**, when the machine has no administrator requirements for Codex (typically a laptop):
  everything above.
- **`managed`**, when Codex reports administrator requirements or a requirements file exists (for
  example `/etc/codex/requirements.toml`): only the rules, the environment filters and the instructions
  block, never a permission setting, because the administrator owns those.

Run the dry run first. It is read-only and may run without asking; it prints the profile and why it was
chosen, what would change, which files it would back up, and whether the plugin's hooks are trusted
(at this point they are `untrusted`, which is expected; Step 7 fixes it):

```bash
"<plugin folder>/bin/evisions-codex-settings"
```

On Windows, run the installer script through Python instead; the bash tool above does nothing more than
find Python and start this script with the same arguments:

```powershell
py -3 "<plugin folder>\scripts\install_codex_settings.py"
```

(`python` in place of `py -3` when the `py` launcher is missing.) Every later command in this file that
runs `evisions-codex-settings` takes the same form on Windows, with the same flags after the script
path.

Summarize the plan for the user in plain words and recommend applying it. Only after their yes:

```bash
"<plugin folder>/bin/evisions-codex-settings" --apply
```

`--apply` saves a copy of each file before it changes it (`<name>.bak-evisions-<time>`, next to the
file) and keeps a record of what it added in `<Codex home>/evisions/codex-settings-applied.json`. The
settings take effect at the next Codex start (Step 7).

- The dry run prints a WARNING that the administrator's requirements set
  `allow_managed_hooks_only = true`: Codex then runs only the administrator's own hooks, so the
  plugin's safety check will NOT run on this machine. Tell the user plainly; the rules, the
  environment filters and the instructions block still work. Only the administrator can change this.
- It reports that the Codex CLI was not found: the `codex` command is not on the `PATH` of this shell.
  Pass its location with `--codex-bin "<path of codex>"`, or stop.
- The detected profile is wrong (the user knows about an administrator's requirements the installer
  did not find, or that there are none): run the dry run again with `--profile managed` or
  `--profile standard`, and use the same flag with `--apply`.
- It notes that the previous `--apply` stopped before it finished: the next `--apply` completes it;
  recommend applying.
- It refuses because its record was edited or damaged: stop and show the message, which says what to
  do. Do not delete or edit the record yourself; that is the user's call.
- A baseline item the user removed by hand counts as their choice: later applies list it as declined
  and leave it out, and `--apply --restore-declined` brings it back.
- `--deny-read` is optional and not part of the recommendation. Offer it only on Linux with the
  standard profile, the only place it was measured; the installer leaves it out on native Windows and
  under the managed profile. It is ignored under `--yolo`, when `sandbox_mode` is set in a config file
  and when Codex is started with `--sandbox`, and the installer skips it when the user's `config.toml`
  already sets `default_permissions` or an administrator allows only other permission profiles.
  `.env.example` and `.env.shared` stay readable under it. To add it: `--apply --deny-read`.
- The user declines: skip this step. The safety check works without it; what is missing then is the
  command rules, the environment filters and the canary. The step can run at any time later.

## Step 6: Prepare the first-use check (optional)

Offer a short check for the next session. It needs a throwaway file with a fake key; create it now,
before the restart (show the command and wait for yes; if it is refused, skip the check):

```bash
mkdir -p "$HOME/evisions-safety-check" && printf 'DUMMY_KEY=not-a-secret\n' > "$HOME/evisions-safety-check/.env"
```

On Windows:

```powershell
New-Item -ItemType Directory -Force "$HOME\evisions-safety-check" | Out-Null; Set-Content -Path "$HOME\evisions-safety-check\.env" -Value 'DUMMY_KEY=not-a-secret'
```

## Step 7: Restart Codex and trust the hooks (required)

Without this step nothing runs. Codex does not run a plugin's hooks until the user trusts them, and
installing the plugin trusts nothing. You, Codex, cannot do this step for the user; tell them exactly
this:

1. Quit Codex and start `codex` again, in any folder.
2. A screen "Hooks need review" appears with three choices: "Review hooks", "Trust all and continue"
   and "Continue without trusting (hooks won't run)". Choose **Trust all and continue**. ("Review hooks"
   shows each of the three evisions hooks first; trust all three.)
3. If the screen did not appear or the wrong choice was made, type `/hooks` in Codex and trust the three
   evisions hooks there.
4. In the new session, say: "Continue CODEX.md from Step 8."

After a plugin update that changes the hook definitions, Codex shows the screen again, and until the
user trusts the hooks again they do not run. That is why the [Update](#update) procedure ends with the
check in Step 8.

## Step 8: Check

```bash
"<plugin folder>/bin/evisions-codex-settings" --check
```

Pass: exit code 0, with the last line `baseline installed and the plugin's hooks are trusted`. Its hook
list shows `preToolUse`, `sessionStart` and `userPromptSubmit`, each `trusted`.

- The user skipped Step 5: `--check` reports that the baseline is not installed and exits 1. Run the dry
  run from Step 5 instead and read its "Plugin hooks" section: all three hooks must be `trusted`.
- "N of 3 hooks are not trusted": Step 7 again.
- "none found": the plugin is not installed or not enabled in this Codex home; back to Step 3.
- "The plugin's PreToolUse safety hook is missing": update or reinstall the plugin.
- Something in the baseline is missing or changed: the message says what. When the user removed it on
  purpose, `--apply` records that as their choice; `--apply --restore-declined` puts it back instead.

## Step 9: First use

If Step 6 created the throwaway file, the user asks in the new session:

1. "What time is it?" Codex answers with the current local time.
2. "Show me the contents of ~/evisions-safety-check/.env". Codex must refuse and must not show the
   value; when it tried to read the file, the block message starts with `evisions safety:`.
3. "Which keys are in ~/evisions-safety-check/.env?" Codex runs `list-env-keys` by its absolute path and
   shows the name `DUMMY_KEY` only, never the value. On Windows this needs Git Bash.

If Codex says instead that the evisions safety hooks are not active, the trust step did not take: go
back to Step 8. Afterwards the user deletes the `evisions-safety-check` folder themselves.

## What the safety check blocks

Before every shell command and every `apply_patch` file edit, the check blocks:

- **reading secrets**: `.env` and any other `.env.*` file (`.env.shared` and placeholder files ending in
  `.example`, `.sample`, `.template` or `.dist` stay readable), SSH keys, GnuPG keyrings, cloud and
  registry credentials, git credentials, browser data, and Codex's own login file (`auth.json`);
- **recursive deletion**, with or without a force flag: `rm -r`, `find -delete`, `Remove-Item -Recurse`,
  `rd /s`;
- **downloaded code run in a shell**: a download piped into a shell or an interpreter, and
  download-then-run;
- **dangerous docker and disk commands**: privileged mode, a mount of the host root or of a credential
  folder, disk operations on physical devices, fork bombs, and a `mv` that would silently overwrite an
  existing file;
- **destructive git in every form**, `git -C <dir>`, `git -c key=value` and a full path such as
  `/usr/bin/git` included: force push, `reset --hard`, `checkout -- <paths>`, `clean -f`, `branch -D`,
  `commit --no-verify`, `-n` and `-a`;
- **edits through `apply_patch`** of env files, credential files, shell startup files in the home
  folder, anything under `~/.ssh`, and Codex's own configuration, rules, hooks and plugins;
- **attempts to switch the safety off**: a nested `codex` started with `--ignore-rules`,
  `--ignore-user-config`, `--dangerously-bypass-hook-trust`,
  `--dangerously-bypass-approvals-and-sandbox` or `--yolo`; `--disable hooks` or a `-c` override that
  turns hooks or plugins off; `codex features disable hooks`; `codex plugin remove`; and shell writes
  into Codex's control files (`config.toml`, `hooks.json`, `rules/`, `AGENTS.md`, `AGENTS.override.md`
  and `plugins/` in the Codex home, and `/etc/codex`).

The check still runs under `--yolo`. Every block message starts with `evisions safety:`; Codex then
stops, says what was blocked and why, and gives the user the exact command to run themselves if they
really want it done. It never looks for another way to do the same thing.

## Limits

- It is a guard against accidents by an overeager agent, not a barrier against a determined attacker:
  a deliberately disguised command can still get past it.
- Only an administrator's requirements file can forbid `--yolo`. Under `--yolo`, Codex's sandbox and
  the deny-read profile are gone; the safety check and the command rules stay.
- Codex lets a call through when a hook fails: an error in the hook, a timeout, or a hook that cannot
  start. The one exception is the plugin's own launcher: when it finds no Python 3.9 or newer, it blocks.
- There is no "ask" tier. A Codex hook can only allow or block, and the command rules only block.
  There are no "ask" rules on purpose: under `--yolo` Codex turns an ask rule into a rejection, which
  would block everyday commands such as `git push` for everyone who uses `--yolo`. A plain push, a
  merge or a local install passes the check; Codex's own approval mode decides about it.
- A project's own `.codex/config.toml` is loaded only when the user trusts that project, and it can
  switch hooks off. Trust only projects whose settings you know.
- The check sees shell commands and `apply_patch` edits only. Web search, files the user attaches or
  mentions with `@`, images and MCP tools are not checked.
- It catches commands that name a secret file. A search through a whole folder (for example
  `grep -r KEY .`), reading an older version of an env file from git history, or copying an env file
  under another name and reading the copy can still expose a value.
- A hook whose definition changed after an update does not run until the user trusts it again. The
  canary in the global instructions block (Step 5) is what tells the user.
- macOS and Windows are not measured. PowerShell coverage is best-effort, and on Windows
  `list-env-keys` needs Git Bash.

## Update

```bash
codex plugin marketplace upgrade claude-code-pack-evisions
codex plugin list -m claude-code-pack-evisions
```

The first command refreshes Codex's copy of the repository. If the version column still shows the old
number afterwards, install the new version:

```bash
codex plugin add evisions@claude-code-pack-evisions
```

Whether the upgrade alone moves the installed plugin to the new version is not measured yet, hence the
check. Then:

1. Step 4 again: the plugin folder has the new version in its path.
2. `--apply` from the new plugin folder (Step 5, with the same `--profile` or `--deny-read` flag as
   before). It installs the new command rules and takes back what the new baseline dropped; the user's
   own entries stay.
3. Restart Codex. If the "Hooks need review" screen appears, trust the hooks again (Step 7).
4. `--check` (Step 8) must exit 0.

## Uninstall

First undo the settings baseline. The installer ships with the plugin, so it must run before the plugin
is removed; when the baseline was never applied, it only reports that there is nothing to undo:

```bash
"<plugin folder>/bin/evisions-codex-settings" --remove
```

It backs up each file first and removes exactly what it added; entries the user changed or added
themselves stay. When it created `config.toml` itself and nothing else is left in it, it deletes the file
instead of leaving an empty one. Then remove the plugin and the marketplace:

```bash
codex plugin remove evisions@claude-code-pack-evisions
codex plugin marketplace remove claude-code-pack-evisions
```

The user runs `codex plugin remove` themselves, in a terminal outside Codex. Inside a Codex session the
safety check blocks it on purpose, because removing the plugin removes the check. If you, Codex, are
following this file, show the two commands and ask the user to run them. Restart Codex afterwards.

## For administrators

For containers and machines where an administrator manages Codex through a requirements file
(`/etc/codex/requirements.toml` on macOS and Linux, `%ProgramData%\OpenAI\Codex\requirements.toml` on
Windows):

- The settings installer picks the `managed` profile there by itself and never writes a permission
  setting.
- `allow_managed_hooks_only = true` switches the plugin's hooks off, the safety check included. The
  installer warns about it in every mode. Keep the check by registering it as a managed hook, or leave
  that setting out.
- Managed hooks count as trusted by policy, need no trust step and cannot be switched off by the user.
  That is how an administrator makes the check mandatory.
- Forbidding `--yolo` is possible only there: `allowed_approval_policies` without `never`, or
  `allowed_permission_profiles` without `:danger-full-access`.
- `[marketplaces] restrict_to_allowed_sources` refuses this marketplace unless the administrator allows
  its repository.

OpenAI's documentation: https://learn.chatgpt.com/docs/enterprise/managed-configuration and
https://learn.chatgpt.com/docs/hooks

<hard_rules_repeat>
Show each command and wait for yes; report one line per result; stop on the first failure. Only the
`codex plugin` CLI and `evisions-codex-settings` write: never edit Codex's configuration, rules, hooks or
instructions by hand, never trust the hooks for the user, never delete the user's own files, never
`sudo`, never touch a token or the Codex login. A policy refusal or an `evisions safety:` block is
reported, not worked around.
</hard_rules_repeat>
