"""PreToolUse safety check of the evisions plugin, for the Bash, PowerShell, Read and Grep tools.

Launched through hooks/run-python, which blocks the call when no Python 3.9+ exists, because Claude
Code lets a call through silently when a hook cannot start. Standard library only, Python 3.9+.

What it is: defence in depth against a careless or overeager agent, not a boundary against a
determined adversary. It catches the ways an agent realistically reaches a secret or deletes too
much; a deliberately obfuscated command can still get past it.

Why a hook on top of permission rules: Claude Code now matches permission rules against every
subcommand of a compound command, so a deny rule does catch `cd build && rm -rf .`. The hook still
matters because a plugin cannot ship permission rules at all (without the optional settings baseline
this hook is the only layer), because it also runs in bypass mode, and because it catches forms a
rule cannot express: `/bin/rm -r`, `find . -exec rm -r {} +`, `xargs rm -r`, flags in any order or
spelling, `sh -c '...'`, inline interpreter code, and reads of secret values by dozens of commands.

How it reads a command: a small shell lexer (quotes, escapes, `$(...)`, backticks, process
substitution, heredocs, comments, redirections) splits the command into simple commands, strips
assignments and wrappers (sudo, env, timeout, xargs, ...), and follows code handed to `sh -c`,
`eval`, `ssh host ...`, `cmd /c`, `powershell -Command` and a shell's heredoc. The checks then look
at command words and their operands, so `c''at .env` reads as `cat .env`, while a commit message or
a PR body that merely mentions `.env` is not a read. Pattern checks (docker, dd, fork bomb, ...) run
on the text and on a copy with empty quote pairs and in-word escapes removed.

What it blocks:
- recursive deletion anywhere in a command, with or without a force flag: rm -r, -R or --recursive,
  find -delete, Remove-Item -Recurse and its aliases, rd /s. In Claude's non-interactive shell
  `rm -r` deletes without asking, exactly like `rm -rf`.
- reading secret VALUES from env files into the conversation (policy below)
- reading SSH keys, GPG keyrings, cloud and registry credentials, git credentials, browser data
- download-and-execute: a download piped into a shell or into an interpreter that reads its script
  from stdin, `bash <(curl ...)`, `source <(curl ...)`, download then run, and `sh -c` or `eval`
  around destructive commands
- docker --privileged, a host-root mount, a mount of a credential directory
- disk operations on physical devices, fork bombs
- a plain `mv` that would silently overwrite an existing path (Bash only; PowerShell's Move-Item
  refuses to overwrite without -Force)
- PowerShell commands built from concatenated strings and run through `&( )` or Invoke-Expression,
  when combined with -Recurse, -Force or an env file

Env read policy (narrow and value-focused): the threat is secret VALUES entering the conversation,
which happens only when a command READS the file into output Claude sees. A read is:
- a reader command (cat, head, grep, sed, Get-Content, ...) whose FILE operand is a hard env file or
  a glob that can match one (`.env.*`, `.env.{local,production}`);
- an input redirection from one (`< .env`), or `source`/`.` of one;
- a hard env name anywhere in the command text (heredoc bodies included) when the command runs an
  interpreter with inline or stdin code (-c, -e, --eval, a heredoc, `-`) or a reader reaches its
  files indirectly (a variable, a loop, `{}` from find -exec, xargs).
Not a read: naming the file as configuration (`--env-file .env`, `cp .env.example .env`), searching
FOR the text ".env" inside another file (`grep -qxF .env .gitignore`), or mentioning it in a commit
message or PR body. `.env.shared` (the soft tier) and placeholder files ending in .example, .sample,
.template or .dist are readable; every other `.env` or `.env.*` file is hard. The bare command
`list-env-keys --from <path>` (found on PATH) lists key NAMES only, and `--classify` adds each key's
state; a path-prefixed `./list-env-keys` is an unknown script and is blocked on a hard env file.
Sourcing a hard env file (`source .env`, `. ./.env`) is blocked however the command continues: a
program that needs a key loads it itself (dotenv, `--env-file`), so the value never passes through
the conversation.

PowerShell coverage is best-effort and has not been run on Windows: Get-Content and its aliases,
Select-String, Import-Csv, Format-Hex, findstr and [IO.File]::Read* on a hard env file or a secret
path; Remove-Item and its aliases with -Recurse in any abbreviation, cmd-style `rd /s`; `iex`
combined with a web download. The PowerShell-only aliases (gc, type, sls, ...) count only for the
PowerShell tool. Backslashes become slashes before any path matching, because Windows paths arrive
with backslashes.

Exit codes: 0 allows the call, 2 blocks it with the reason on stderr. Every block message starts
with `evisions safety:` so a block can be attributed to this plugin.
"""

import json
import os
import re
import sys

PREFIX = "evisions safety:"
HELPER = "list-env-keys"
SHOWN_LIMIT = 400  # characters of the blocked command repeated in the message
NESTING_LIMIT = 10  # levels of $(...), sh -c and similar followed before giving up

TRAILER = (
    "This is a deliberate safety boundary. Do not retry it or reach the same result another way "
    "(another command, interpreter, script or alias). Tell the user plainly what was blocked and, "
    "if they want it done, give them the exact command to run themselves."
)

BASH, POWERSHELL, CMD = "bash", "powershell", "cmd"
ESCAPES = {BASH: "\\", POWERSHELL: "`", CMD: "^"}
# Runs of characters with no special meaning, consumed in one step so long words stay linear.
PLAIN_RUNS = {
    BASH: re.compile(r"[^\s'\"\\$`<>&|;()]+"),
    POWERSHELL: re.compile(r"[^\s'\"`$<>&|;()]+"),
    CMD: re.compile(r"[^\s'\"^$<>&|;()]+"),
}
QUOTED_RUNS = {BASH: re.compile(r'[^"\\$`]+'), POWERSHELL: re.compile(r'[^"`$]+'), CMD: re.compile(r'[^"^]+')}


# === lexer ======================================================================================
class Word:
    """One shell word: its value with quotes removed and escapes resolved."""

    __slots__ = ("value", "quoted", "expanded", "procsubs")

    def __init__(self):
        self.value = ""
        self.quoted = False  # part of it was quoted or escaped
        self.expanded = False  # it holds $var, ${...}, $(...), `...` or <(...)
        self.procsubs = []  # scripts of the <(...) and >(...) inside it


class Command:
    """One simple command: its words, redirections, heredoc bodies and the operator after it."""

    __slots__ = ("words", "redirects", "heredocs", "sep")

    def __init__(self):
        self.words = []
        self.redirects = []  # (operator, target word)
        self.heredocs = []  # bodies of the heredocs and here-strings fed to it
        self.sep = ""  # the operator that ended it: ";", "&&", "||", "|", "&", newline, "(" or ")"


class Script:
    """The simple commands of one piece of shell text, plus every $(...), `...` and <(...) in it."""

    __slots__ = ("commands", "subs", "dialect")

    def __init__(self, commands, subs, dialect):
        self.commands = commands
        self.subs = subs
        self.dialect = dialect


VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9?@#$!*-]")
REDIRECT_OPS = ("&>>", "<<<", "<<-", "&>", "<<", "<>", "<&", ">>", ">&", ">|", "<", ">")
CONTROL_OPS = ("&&", "||", ";;", "|&", ";", "|", "&")
ANSI_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "e": "\x1b", "E": "\x1b"}


def ansi_c(text, pos):
    """Decode the body of $'...' starting at pos; return (value, position after the quote)."""
    out = []
    while pos < len(text):
        char = text[pos]
        if char == "'":
            return "".join(out), pos + 1
        if char == "\\" and pos + 1 < len(text):
            nxt = text[pos + 1]
            hexa = re.match(r"x([0-9A-Fa-f]{1,2})", text[pos + 1:pos + 4])
            octal = re.match(r"[0-7]{1,3}", text[pos + 1:pos + 4])
            if hexa:
                out.append(chr(int(hexa.group(1), 16)))
                pos += 1 + len(hexa.group(0))
            elif octal:
                out.append(chr(int(octal.group(0), 8)))
                pos += 1 + len(octal.group(0))
            else:
                out.append(ANSI_ESCAPES.get(nxt, nxt))
                pos += 2
            continue
        out.append(char)
        pos += 1
    return "".join(out), pos


def arithmetic_end(text, pos):
    """Position after the `))` closing a `$((` whose body starts at pos."""
    depth = 2
    while pos < len(text):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return len(text)


def substitutions_in(body, dialect):
    """The $(...) scripts inside an unquoted heredoc body, which the shell expands."""
    found = []
    pos = body.find("$(")
    while pos >= 0:
        if body.startswith("$((", pos):
            pos = body.find("$(", arithmetic_end(body, pos + 3))
            continue
        script, end = parse(body, dialect, pos + 2, ")")
        found.append(script)
        pos = body.find("$(", max(end, pos + 2))
    return found


def parse(text, dialect, start=0, closer=None):
    """Lex shell text into simple commands. With a closer, stop after it (for $(...), `...`, <(...))
    and return the position after it. Unbalanced quotes run to the end of the text."""
    n = len(text)
    escape = ESCAPES[dialect]
    plain_run = PLAIN_RUNS[dialect]
    quoted_run = QUOTED_RUNS[dialect]
    commands = []
    subs = []
    pending = []  # heredocs waiting for their body: (delimiter, strip tabs, owner, body expands)
    command = Command()
    word = None
    redirect = None
    depth = 0
    i = start

    def current():
        nonlocal word
        if word is None:
            word = Word()
        return word

    def end_word():
        nonlocal word, redirect
        if word is None:
            return
        if redirect is None:
            command.words.append(word)
        else:
            command.redirects.append((redirect, word))
            if redirect in ("<<", "<<-"):
                pending.append((word.value, redirect == "<<-", command, not word.quoted))
            elif redirect == "<<<":
                command.heredocs.append(word.value)
            redirect = None
        word = None

    def end_command(sep):
        nonlocal command, redirect
        end_word()
        redirect = None
        if command.words or command.redirects:
            command.sep = sep
            commands.append(command)
        elif sep in ("|", "|&") and commands:
            commands[-1].sep = sep  # `( ... ) | next`: the pipe leaves the group's last command
        command = Command()

    def read_heredocs(pos):
        for delimiter, strip_tabs, owner, expands in pending:
            lines = []
            while pos < n:
                end = text.find("\n", pos)
                line = text[pos:] if end < 0 else text[pos:end]
                pos = n if end < 0 else end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                lines.append(line)
            body = "\n".join(lines)
            owner.heredocs.append(body)
            if expands:
                subs.extend(substitutions_in(body, dialect))
        del pending[:]
        return pos

    def dollar(pos, target):
        if text.startswith("((", pos + 1):
            end = arithmetic_end(text, pos + 3)
            target.expanded = True
            target.value += text[pos:end]
            return end
        if text.startswith("(", pos + 1):
            script, end = parse(text, dialect, pos + 2, ")")
            subs.append(script)
            target.expanded = True
            target.value += "$(...)"
            return end
        if text.startswith("{", pos + 1):
            end = text.find("}", pos + 2)
            end = n if end < 0 else end + 1
            target.expanded = True
            target.value += text[pos:end]
            return end
        name = VAR_NAME.match(text, pos + 1)
        if name:
            target.expanded = True
            target.value += "$" + name.group(0)
            return name.end()
        target.value += "$"
        return pos + 1

    def double_quoted(pos):
        target = current()
        target.quoted = True
        while pos < n:
            char = text[pos]
            if char == '"':
                return pos + 1
            if char == escape and pos + 1 < n and (dialect != BASH or text[pos + 1] in '$`"\\\n'):
                if text[pos + 1] != "\n":
                    target.value += text[pos + 1]
                pos += 2
                continue
            if char == "$" and dialect != CMD:
                pos = dollar(pos, target)
                continue
            if char == "`" and dialect == BASH:
                script, pos = parse(text, dialect, pos + 1, "`")
                subs.append(script)
                target.expanded = True
                target.value += "`...`"
                continue
            run = quoted_run.match(text, pos)
            if run:
                target.value += run.group(0)
                pos = run.end()
            else:
                target.value += char
                pos += 1
        return pos

    while i < n:
        char = text[i]
        if closer is not None and char == closer and depth == 0:
            end_command("")
            return Script(commands, subs, dialect), i + 1
        if char in " \t\r":
            end_word()
            i += 1
        elif char == "\n":
            end_command("\n")
            i += 1
            if pending:
                i = read_heredocs(i)
        elif char == "#" and word is None:
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif char == escape:
            if text.startswith("\n", i + 1):
                i += 2
                continue
            target = current()
            target.quoted = True
            target.value += text[i + 1:i + 2]
            i += 2
        elif char == "'":
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            target = current()
            target.quoted = True
            target.value += text[i + 1:end]
            i = end + 1
        elif char == '"':
            i = double_quoted(i + 1)
        elif char == "$" and dialect == BASH and text.startswith("'", i + 1):
            value, i = ansi_c(text, i + 2)
            target = current()
            target.quoted = True
            target.value += value
        elif char == "$" and dialect != CMD:
            i = dollar(i, current())
        elif char == "`" and dialect == BASH:
            script, i = parse(text, dialect, i + 1, "`")
            subs.append(script)
            target = current()
            target.expanded = True
            target.value += "`...`"
        elif char in "<>" and text.startswith("(", i + 1) and dialect == BASH:
            script, i = parse(text, dialect, i + 2, ")")
            subs.append(script)
            target = current()
            target.expanded = True
            target.value += char + "(...)"
            target.procsubs.append(script)
        elif char in "<>" or text.startswith("&>", i):
            if word is not None and not word.quoted and word.value.isdigit():
                word = None  # the file descriptor of this redirection (2>, 1>&2)
            else:
                end_word()
            op = next(op for op in REDIRECT_OPS if text.startswith(op, i))
            i += len(op)
            redirect = op
        elif dialect == POWERSHELL and char == "&" and not text.startswith("&&", i):
            end_word()  # the call operator
            i += 1
        elif char == "(":
            end_command("(")
            depth += 1
            i += 1
        elif char == ")":
            end_command(")")
            depth = max(depth - 1, 0)
            i += 1
        else:
            op = next((op for op in CONTROL_OPS if text.startswith(op, i)), None)
            run = None if op else plain_run.match(text, i)
            if op:
                end_command(op)
                i += len(op)
            elif run:
                current().value += run.group(0)
                i = run.end()
            else:
                current().value += char
                i += 1
    end_command("")
    return Script(commands, subs, dialect), i


def parse_text(text, dialect):
    if dialect != BASH:
        text = text.replace("\\", "/")
    return parse(text, dialect)[0]


def normalized(text, dialect):
    """The text with empty quote pairs and escapes inside words removed (`c''at` -> `cat`)."""
    flat = text.replace("''", "").replace('""', "")
    return re.sub(re.escape(ESCAPES[dialect]) + r"(?=\w)", "", flat)


# === command views ==============================================================================
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
KEYWORDS = {"!", "{", "}", "do", "then", "else", "elif", "if", "while", "until", "coproc"}
# Commands that run another command, with the options of theirs that take a separate value.
WRAPPERS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--chdir"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "nohup": set(),
    "noglob": set(),
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "time": {"-f", "-o"},
    "busybox": set(),
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter", "--arg-file"},
}


def command_name(value):
    """The lowercase file name of a command word: `/bin/rm` -> `rm`, `C:\\x\\cmd.exe` -> `cmd`."""
    name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


class View:
    """A command as it runs: words from the effective command word on, after wrappers."""

    __slots__ = ("words", "name", "indirect")

    def __init__(self, words, indirect):
        self.words = words
        self.name = command_name(words[0].value)
        self.indirect = indirect  # its file operands arrive through xargs or find -exec {}


def skip_wrapper(name, words, k):
    value_options = WRAPPERS[name]
    while k < len(words):
        value = words[k].value
        if value == "--":
            return k + 1
        if len(value) > 1 and value[0] == "-":
            k += 2 if value in value_options else 1
            continue
        if name == "env" and ASSIGNMENT.match(value):
            k += 1
            continue
        return k + 1 if name == "timeout" else k  # timeout's first operand is the duration
    return k


def effective_start(words):
    """(index of the command word after assignments, keywords and wrappers, whether xargs ran it)."""
    k = 0
    via_xargs = False
    while k < len(words):
        value = words[k].value
        name = command_name(value)
        if ASSIGNMENT.match(value) or value in KEYWORDS:
            k += 1
        elif name in WRAPPERS:
            via_xargs = via_xargs or name == "xargs"
            k = skip_wrapper(name, words, k + 1)
        else:
            return k, via_xargs
    return None, via_xargs


def command_views(command):
    """The command itself plus every command find runs through -exec, -execdir, -ok or -okdir."""
    start, via_xargs = effective_start(command.words)
    if start is None:
        return []
    words = command.words[start:]
    views = [View(words, via_xargs)]
    if views[0].name == "find":
        j = 1
        while j < len(words):
            if words[j].value in ("-exec", "-execdir", "-ok", "-okdir"):
                end = j + 1
                while end < len(words) and words[end].value not in (";", "+"):
                    end += 1
                inner = words[j + 1:end]
                inner_start = effective_start(inner)[0]
                if inner_start is not None:
                    views.append(View(inner[inner_start:], True))
                j = end + 1
            else:
                j += 1
    return views


SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish"}
INTERPRETER = re.compile(r"^(?:python[0-9.]*|py|pypy[0-9.]*|perl|ruby|node|nodejs|deno|bun|php)$")
# Per interpreter: short flags that run inline code, short flags that take a separate value.
INLINE_FLAGS = {"perl": ("eE", "IMm"), "ruby": ("e", "IrC"), "node": ("ep", "r"), "nodejs": ("ep", "r"),
                "bun": ("e", "r"), "php": ("rBRFE", "cdz")}
PYTHON_FLAGS = ("c", "WX")


def shell_mode(args):
    """('command', word) for sh -c WORD, ('script', word) for a script file, ('stdin', None) otherwise."""
    saw_c = False
    skip = False
    for word in args:
        value = word.value
        if skip:
            skip = False
            continue
        if len(value) > 1 and value[0] in "-+":
            if value in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                skip = True
            elif value[0] == "-" and not value.startswith("--") and "c" in value[1:]:
                saw_c = True
            continue
        if value == "-":
            continue
        return ("command" if saw_c else "script"), word
    return "stdin", None


def interpreter_mode(name, args):
    """'inline' for -c/-e/--eval code, 'stdin' when the script comes from stdin (no script operand
    or `-`), None for a script file, a module or a command that is no interpreter."""
    if not INTERPRETER.match(name):
        return None
    if name == "deno":
        return "inline" if args and args[0].value == "eval" else None
    is_python = name.startswith(("python", "py"))
    inline_letters, value_letters = PYTHON_FLAGS if is_python else INLINE_FLAGS.get(name, ("e", ""))
    skip = False
    for word in args:
        value = word.value
        if skip:
            skip = False
            continue
        if value == "-":
            return "stdin"
        if value.startswith("--"):
            if value.split("=", 1)[0] in ("--eval", "--print", "--command"):
                return "inline"
            continue
        if len(value) > 1 and value[0] == "-":
            letters = value[1:]
            if any(letter in inline_letters for letter in letters):
                return "inline"
            if is_python and "m" in letters:
                return None  # a module runs, not inline code
            skip = len(letters) == 1 and letters in value_letters
            continue
        return None  # the first operand is a script file
    return "stdin"


def ssh_remote(args):
    """The command an `ssh [options] host command...` runs on the remote host, or None."""
    value_options = set("bcDEeFIiJLlmOopQRSWwB")
    k = 0
    while k < len(args):
        value = args[k].value
        if len(value) > 1 and value[0] == "-":
            k += 2 if len(value) == 2 and value[1] in value_options else 1
            continue
        rest = args[k + 1:]
        return " ".join(word.value for word in rest) if rest else None
    return None


def nested_scripts(view, command, dialect):
    """Code this command hands to another interpreter: sh -c, a shell's heredoc, eval, ssh, cmd /c,
    powershell -Command, Invoke-Expression."""
    name, args = view.name, view.words[1:]

    def after(k, target_dialect):
        rest = args[k + 1:]
        return [parse_text(" ".join(word.value for word in rest), target_dialect)] if rest else []

    if name in SHELLS:
        mode, word = shell_mode(args)
        if mode == "command":
            return [parse_text(word.value, BASH)]
        if mode == "stdin":
            return [parse_text(body, BASH) for body in command.heredocs]
        return []
    if name == "eval" and args:
        return [parse_text(" ".join(word.value for word in args), BASH)]
    if name == "ssh":
        remote = ssh_remote(args)
        return [parse_text(remote, BASH)] if remote else []
    if name == "cmd":
        for k, word in enumerate(args):
            if word.value.lower() in ("/c", "/k"):
                return after(k, CMD)
        return []
    if name in ("powershell", "pwsh"):
        for k, word in enumerate(args):
            lowered = word.value.lower()
            if len(lowered) >= 2 and "-command".startswith(lowered):
                return after(k, POWERSHELL)
        return []
    if dialect == POWERSHELL and name in ("iex", "invoke-expression") and args:
        return [parse_text(" ".join(word.value for word in args), POWERSHELL)]
    return []


def all_scripts(script, depth=0):
    """The script, its substitutions and all code it hands on, recursively."""
    scripts = [script]
    if depth >= NESTING_LIMIT:
        return scripts
    for sub in script.subs:
        scripts.extend(all_scripts(sub, depth + 1))
    for command in script.commands:
        for view in command_views(command):
            for nested in nested_scripts(view, command, script.dialect):
                scripts.extend(all_scripts(nested, depth + 1))
    return scripts


# === env files and secret paths =================================================================
ENV_READ_OK_EXACT = {".env.shared"}
# Suffix match, so multi-part names such as `.env.production.example` stay readable too.
ENV_READ_OK_SUFFIX = (".example", ".sample", ".template", ".dist")
# A hard env name anywhere in a text (inline code, heredoc bodies), globs included.
ENV_TEXT_TOKEN = re.compile(
    r"""(?:^|(?<=[\s'"=:(<>|&;,/\\`{]))(\.env(?:[.*?\[][^\s'"=:()<>|&;,/\\`]*)?)(?![\w-])""", re.IGNORECASE
)
DOTENV_LOADER = re.compile(
    r"\bload_dotenv\b|\bdotenv_values\b|\bfrom\s+dotenv\b|\bimport\s+dotenv\b|require\(\s*['\"]dotenv|dotenv/config"
)
BRACE_GROUP = re.compile(r"\{([^{}]*,[^{}]*)\}")


def env_readable(name):
    name = name.lower()
    return name in ENV_READ_OK_EXACT or name.endswith(ENV_READ_OK_SUFFIX)


def env_name_hard(name):
    """True for a hard env file name, or a glob that can match one (`.env*`, `.env.*`)."""
    lowered = name.lower()
    if not lowered.startswith(".env"):
        return False
    rest = lowered[4:]
    if rest and rest[0] not in ".*?[":
        return False  # `.envrc` and similar are not env files
    return not env_readable(lowered)


def brace_expansions(value, limit=32):
    """`.env.{local,production}` -> `.env.local`, `.env.production` (what the shell passes on)."""
    group = BRACE_GROUP.search(value)
    if not group:
        return [value]
    out = []
    for part in group.group(1).split(","):
        out.extend(brace_expansions(value[:group.start()] + part + value[group.end():], limit))
        if len(out) >= limit:
            break
    return out[:limit]


def hard_env_in_text(text):
    for match in ENV_TEXT_TOKEN.finditer(text):
        if env_name_hard(match.group(1)):
            return match.group(1)
    return None


# Directory entries match the folder followed by `/`, whitespace, a quote or the end of the text.
DIR_END = r"""(?:/|(?=[\s'"`;|&)]|$))"""
SECRET_BOUNDARY = r"""(?:^|(?<=[\s'"=:/~(<>]))"""
BROWSER_DATA = "browser data (cookies, history, sessions)"
# (pattern, label, lowercase substring that must occur first: a cheap test that keeps long texts fast)
SECRET_PATHS = [
    (r"\.ssh" + DIR_END, "SSH keys and configuration", ".ssh"),
    (r"\.gnupg" + DIR_END, "the GPG keyring", ".gnupg"),
    (r"\.aws" + DIR_END, "AWS credentials", ".aws"),
    (r"\.azure" + DIR_END, "Azure credentials", ".azure"),
    (r"\.kube" + DIR_END, "Kubernetes credentials", ".kube"),
    (r"\.config/gh" + DIR_END, "GitHub CLI credentials", ".config/gh"),
    (r"\.docker/config\.json(?![\w.-])", "Docker registry credentials", ".docker/config.json"),
    (r"\.git-credentials(?![\w.-])", "git credentials", ".git-credentials"),
    (r"\.npmrc(?![\w.-])", "npm registry credentials", ".npmrc"),
    (r"\.pypirc(?![\w.-])", "PyPI credentials", ".pypirc"),
    (r"Library/Keychains" + DIR_END, "macOS keychains", "library/keychains"),
    (
        r"Library/(?:Cookies|Safari|Application(?:\\ | )Support/(?:Google/Chrome|Chromium|Firefox"
        r"|BraveSoftware|Microsoft Edge|Arc))" + DIR_END,
        BROWSER_DATA,
        "library/",
    ),
    (r"\.mozilla" + DIR_END, BROWSER_DATA, ".mozilla"),
    (r"\.config/(?:google-chrome|chromium)" + DIR_END, BROWSER_DATA, ".config/"),
    (r"AppData/(?:Local|Roaming)/(?:Google/Chrome|Microsoft/Edge|Mozilla/Firefox|BraveSoftware)" + DIR_END, BROWSER_DATA, "appdata/"),
]
SECRET_PATTERNS = [
    (re.compile(SECRET_BOUNDARY + pattern, re.IGNORECASE), label, needle) for pattern, label, needle in SECRET_PATHS
]


def secret_match(text):
    """The label of the first secret path in text, or None."""
    lowered = text.lower()
    for pattern, label, needle in SECRET_PATTERNS:
        if needle in lowered and pattern.search(text):
            return label
    return None


def env_message(action, name):
    """`action` names what would happen, for example "this command would put"."""
    return (
        "%s the values of the protected env file %s into the conversation. To see which keys it "
        "holds without their values, run `%s --from %s` (add --classify to see whether each key is "
        "empty, a placeholder or filled). `.env.shared` and placeholder files such as `.env.example` "
        "are readable, and passing an env file as configuration (for example `--env-file .env`) is "
        "fine." % (action, name, HELPER, name.replace("\\", "/"))
    )


def secret_message(action, label):
    return "%s %s into the conversation, and secrets like these must never enter it." % (action, label)


def operand_reason(value, action="this command would put"):
    """Block reason when a file operand is a hard env file, a glob matching one, or a secret path."""
    for candidate in brace_expansions(value):
        path = candidate.replace("\\", "/")
        if env_name_hard(path.rstrip("/").rsplit("/", 1)[-1]):
            return env_message(action, value)
        label = secret_match(path.rstrip("/") + "/")
        if label:
            return secret_message(action, label)
    return None


# === reads ======================================================================================
UNIX_READERS = {
    "cat", "tac", "less", "more", "most", "head", "tail", "bat", "batcat", "nl", "xxd", "od", "hexdump",
    "hd", "strings", "base32", "base64", "basenc", "grep", "egrep", "fgrep", "zgrep", "zcat", "zless",
    "rg", "ag", "ack", "awk", "gawk", "mawk", "nawk", "sed", "cut", "sort", "uniq", "rev", "fold", "fmt",
    "paste", "column", "pr", "diff", "comm", "join", "dd", "sqlite3", "jq", "yq", "iconv", "look",
}
# PowerShell and cmd readers, which count only for those shells (`gc` is also `git gc`, `type` a
# bash builtin).
WINDOWS_READERS = {
    "get-content", "gc", "cat", "type", "select-string", "sls", "more", "import-csv", "ipcsv",
    "format-hex", "fhx", "findstr",
}
GREP_SPEC = ("ABCmefdD", {"-e", "-f", "--regexp", "--file"})
AWK_SPEC = ("fvF", {"-f", "--file"})
# Commands whose first operand is a pattern or program, not a file: (short options that take a
# separate value, options that supply the pattern so no operand is one).
PATTERN_SPECS = {
    "grep": GREP_SPEC, "egrep": GREP_SPEC, "fgrep": GREP_SPEC, "zgrep": GREP_SPEC,
    "rg": ("ABCmefgtTjMrE", {"-e", "-f", "--regexp", "--file"}),
    "ag": ("ABCmGg", set()),
    "ack": ("ABCmg", set()),
    "sed": ("efl", {"-e", "-f", "--expression", "--file"}),
    "awk": AWK_SPEC, "gawk": AWK_SPEC, "mawk": AWK_SPEC, "nawk": AWK_SPEC,
    "jq": ("fL", {"-f", "--from-file"}),
    "yq": ("fL", {"-f", "--from-file"}),
    "look": ("t", set()),
}
LONG_VALUE_OPTIONS = {
    "--regexp", "--file", "--expression", "--max-count", "--context", "--after-context",
    "--before-context", "--glob", "--type", "--include", "--exclude", "--exclude-dir", "--from-file",
}


def is_reader(name, dialect):
    return name in UNIX_READERS or (dialect != BASH and name in WINDOWS_READERS)


def windows_operands(name, args):
    pattern_first = name in ("select-string", "sls", "findstr")
    pattern_seen = not pattern_first
    operands = []
    skip = False
    for word in args:
        value = word.value
        lowered = value.lower()
        if skip:
            skip = False
            continue
        if len(value) > 1 and value[0] == "-":
            if lowered.startswith("-pat"):  # -Pattern in any abbreviation takes the pattern
                skip = True
                pattern_seen = True
            continue
        if name == "findstr" and value.startswith("/") and len(value) <= 12 and ":" not in value[3:]:
            if lowered.startswith(("/c:", "/g:")):
                pattern_seen = True
            continue
        if not pattern_seen:
            pattern_seen = True
            continue
        operands.append((value, word))
    return operands


def file_operands(view, dialect):
    """(value, word) for each operand the reader treats as a file to read."""
    name, args = view.name, view.words[1:]
    if name == "dd":
        return [(word.value[3:], word) for word in args if word.value.startswith("if=")]
    if dialect != BASH and name in WINDOWS_READERS:
        return windows_operands(name, args)
    value_letters, pattern_options = PATTERN_SPECS.get(name, ("", set()))
    needs_pattern = name in PATTERN_SPECS
    operands = []
    skip = False
    options_done = False
    for word in args:
        value = word.value
        if skip:
            skip = False
            continue
        if not options_done and value == "--":
            options_done = True
            continue
        if not options_done and len(value) > 1 and value[0] == "-":
            if value.startswith("--"):
                option = value.split("=", 1)[0]
                if option in pattern_options:
                    needs_pattern = False
                skip = "=" not in value and option in LONG_VALUE_OPTIONS and name in PATTERN_SPECS
                continue
            for position, letter in enumerate(value[1:]):
                if letter in value_letters:
                    if "-" + letter in pattern_options:
                        needs_pattern = False
                    skip = position == len(value) - 2
                    break
            continue
        if needs_pattern:
            needs_pattern = False  # the pattern or program, for example `.env` in `grep -qxF .env .gitignore`
            continue
        operands.append((value, word))
    return operands


def later_view(view, dialect, match):
    """A view starting at the first later word that `match` accepts (`docker exec app cat .env`)."""
    for k in range(1, len(view.words)):
        word = view.words[k]
        if not word.value.startswith("-") and match(command_name(word.value)):
            return View(view.words[k:], view.indirect)
    return None


def read_reason(scripts, texts):
    """Block reason for a read of a hard env file or a secret path, else None."""
    trigger = False  # inline or stdin code, or files reached through a variable, xargs or find {}
    for script in scripts:
        dialect = script.dialect
        for command in script.commands:
            for op, target in command.redirects:
                if op in ("<", "<>"):
                    reason = operand_reason(target.value)
                    if reason:
                        return reason
            for view in command_views(command):
                name = view.name
                command_word = view.words[0]
                args = view.words[1:]
                if dialect == BASH and name in ("source", "."):
                    operands = [(word.value, word) for word in args]
                elif is_reader(name, dialect):
                    operands = file_operands(view, dialect)
                    trigger = trigger or view.indirect
                elif name == HELPER and command_word.value != HELPER:
                    for word in args:
                        if env_name_hard(command_name(word.value)):
                            return (
                                "only the bare command `%s` (the plugin's tool on PATH) may open a hard "
                                "env file; `%s` could be any script and could print the values."
                                % (HELPER, command_word.value)
                            )
                    continue
                elif command_word.expanded:
                    operands = [(word.value, word) for word in args]  # an unknown command from a variable
                else:
                    mode = interpreter_mode(name, args)
                    if mode:
                        trigger = True
                    runner = later_view(view, dialect, INTERPRETER.match)
                    if runner and interpreter_mode(runner.name, runner.words[1:]) == "inline":
                        trigger = True  # `uv run python -c ...`
                    reader = later_view(view, dialect, lambda candidate: is_reader(candidate, dialect))
                    operands = file_operands(reader, dialect) if reader else []
                for value, word in operands:
                    reason = operand_reason(value)
                    if reason:
                        return reason
                    if word.expanded or value == "{}":
                        trigger = True
    if trigger:
        for text in texts:
            token = hard_env_in_text(text)
            if token:
                return env_message(
                    "this command runs inline code or reads files indirectly and names %s, so it would put" % token,
                    token,
                )
            if DOTENV_LOADER.search(text):
                return (
                    "this command runs inline code that loads an env file with dotenv, which would put "
                    "its values into the conversation. To see which keys it holds without their values, "
                    "run `%s --from <path>`." % HELPER
                )
            label = secret_match(text)
            if label:
                return secret_message("this command runs inline code or reads files indirectly and would put", label)
    return None


# === recursive deletion =========================================================================
RM_SUBCOMMAND_OWNERS = {"git", "docker", "podman", "npm", "pnpm", "yarn", "bun"}  # `git rm -r` etc.
PS_REMOVE_NAMES = {"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"}
CMD_REMOVE_NAMES = {"rd", "rmdir", "del", "erase"}
RM_BUNDLE = re.compile(r"-[fiIdv]*[rR][fiIdvrR]*")
# PowerShell accepts any unambiguous prefix of a parameter name, and `-Recurse:$true`.
PS_RECURSIVE_WORD = re.compile(r"-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?(?::\S*)?", re.IGNORECASE)

RM_MESSAGE = (
    "recursive deletion (rm -r, -R or --recursive, find -delete, Remove-Item -Recurse, rd /s) is "
    "blocked wherever it appears in a command, with or without a force flag: in this non-interactive "
    "shell `rm -r` deletes without asking. If the deletion is really wanted, the user runs it themselves."
)


def deletes_recursively(view, dialect):
    words = view.words
    if view.name == "find" and any(word.value == "-delete" for word in words):
        return True
    for index, word in enumerate(words):
        name = command_name(word.value)
        unix_rm = name == "rm" and not (index == 1 and view.name in RM_SUBCOMMAND_OWNERS)
        windows = (dialect == POWERSHELL and name in PS_REMOVE_NAMES) or (dialect == CMD and name in CMD_REMOVE_NAMES)
        if not (unix_rm or windows):
            continue
        for flag in words[index + 1:]:
            value = flag.value
            if value in (";", "+", "--"):
                break
            if value == "--recursive" or value.startswith("--recursive=") or RM_BUNDLE.fullmatch(value):
                return True
            if dialect != BASH and (PS_RECURSIVE_WORD.fullmatch(value) or value.lower() == "/s"):
                return True
    return False


# === download and execute =======================================================================
DOWNLOADERS = {"curl", "wget", "fetch", "aria2c", "http", "https", "xh", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"}
DOWNLOAD_MESSAGE = (
    "this command runs downloaded code (a download piped into a shell or an interpreter that reads its "
    "script from stdin, or `bash <(curl ...)`). Download the file, let the user review it, and let the "
    "user run it."
)


def downloads(script):
    return any(view.name in DOWNLOADERS for command in script.commands for view in command_views(command)[:1])


def runs_downloaded_code(script):
    in_download_pipe = False
    for index, command in enumerate(script.commands):
        receives_pipe = index > 0 and script.commands[index - 1].sep in ("|", "|&")
        if not receives_pipe:
            in_download_pipe = False
        views = command_views(command)
        if not views:
            continue
        view = views[0]
        name, args = view.name, view.words[1:]
        runs_code = name in SHELLS or name in ("source", ".") or INTERPRETER.match(name)
        if runs_code and any(downloads(sub) for word in args for sub in word.procsubs):
            return True
        if receives_pipe and in_download_pipe:
            if name in SHELLS and shell_mode(args)[0] == "stdin":
                return True
            if interpreter_mode(name, args) == "stdin" or name in ("iex", "invoke-expression"):
                return True
        if name in DOWNLOADERS:
            in_download_pipe = True
    return False


# === mv overwrite guard =========================================================================
def same_file(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def mv_overwrite_target(view, base_dir):
    """The existing path a plain `mv` would silently overwrite, else None.

    Best-effort: skips variables and globs, which it cannot resolve, and `-n`/`-i` (no-clobber,
    interactive), which cannot overwrite silently. A case-only rename on a case-insensitive file
    system (`mv readme.md README.md`) names one file twice and is allowed. Relative paths resolve
    against the session directory from the hook input.
    """
    if view.name != "mv":
        return None
    args = view.words[1:]
    if any(word.expanded for word in args):
        return None
    target_dir = None
    paths = []
    options_done = False
    k = 0
    while k < len(args):
        value = args[k].value
        if not options_done and value == "--":
            options_done = True
        elif not options_done and len(value) > 1 and value[0] == "-":
            if value in ("--no-clobber", "--interactive") or (
                not value.startswith("--") and ("n" in value[1:] or "i" in value[1:])
            ):
                return None
            if value == "-t" and k + 1 < len(args):
                target_dir = args[k + 1].value
                k += 1
            elif value.startswith("--target-directory="):
                target_dir = value.split("=", 1)[1]
        else:
            paths.append(value)
        k += 1
    if any(char in path for path in paths for char in "*?["):
        return None
    if target_dir is None and len(paths) < 2:
        return None

    def resolve(path):
        path = os.path.expanduser(path)
        return path if os.path.isabs(path) else os.path.join(base_dir, path)

    destination = resolve(target_dir if target_dir is not None else paths[-1])
    sources = paths if target_dir is not None else paths[:-1]
    for source in sources:
        source_path = resolve(source)
        if target_dir is not None or os.path.isdir(destination):
            target = os.path.join(destination, os.path.basename(source_path.rstrip("/")))
        else:
            target = destination
        try:
            if os.path.lexists(target) and not same_file(target, source_path):
                return target
        except OSError:
            continue
    return None


# === other dangerous patterns (matched against the text and its normalized copy) ================
DANGEROUS = [
    (
        r"(curl|wget)\s+[^|;&]*\s*(-o\s*\S+|>\s*\S+).*(?:&&|;|\|\|).*\b(bash|sh|zsh)\b\s+\S+",
        "this command downloads a script and then executes it.",
    ),
    (
        r"\b(bash|sh|zsh)\s+-c\s+[\"'][^\"']*\b(rm\s+-[a-zA-Z]*[rf]|curl|wget|chmod\s+(?:777|666|\+s)|sudo)\b",
        "this command wraps a destructive or remote command in `sh -c`.",
    ),
    (
        r"\beval\s+[\"'][^\"']*\b(rm\s+-[a-zA-Z]*[rf]|curl|wget|chmod\s+(?:777|666|\+s)|sudo)\b",
        "this command wraps a destructive or remote command in `eval`.",
    ),
    (
        r"\bdd\b[^|;&\n]*\b(?:if|of)=/dev/(?:disk|sd[a-z]|nvme|hd[a-z]|vd[a-z]|xvd[a-z]|mmcblk)",
        "this command runs dd against a physical disk device.",
    ),
    (r"\bmkfs(?:\.\w+)?\b[^|;&\n]*\s/dev/", "this command formats a device."),
    (r"\bfdisk\s+/dev/", "this command runs fdisk on a device."),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "this command is a fork bomb."),
    (
        r"docker\s+run\s+[^#\n]*(?:-v|--volume)(?:\s+|=)/:(\s|/)",
        "this command mounts the host's root filesystem into a container.",
    ),
    (r"docker\s+run\s+[^#\n]*--privileged\b", "this command starts a privileged container."),
    (
        r"docker\s+(run|create)\s+[^#\n]*(?:-v|--volume)(?:\s+|=)\S*(\.ssh|\.aws|\.gnupg|\.kube|\.docker|\.config/gh)\b",
        "this command mounts a credential directory (ssh, aws, gnupg, kube, docker, gh) into a container.",
    ),
    (
        r"\bpython3?\s+-c\s+[\"'][^\"']*\b(os\.system|subprocess\.|shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|os\.path\.expanduser.*\.(ssh|aws|gnupg))",
        "this command uses `python -c` to run shell commands, delete files or reach credential directories.",
    ),
]
DANGEROUS_PATTERNS = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in DANGEROUS]

PS_EXECUTE = re.compile(r"(?<![\w-])(?:iex|Invoke-Expression)(?![\w-])", re.IGNORECASE)
PS_DOWNLOAD = re.compile(
    r"(?<![\w-])(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|curl|wget)(?![\w-])|Download(?:String|File|Data)\b",
    re.IGNORECASE,
)
PS_FILE_READ = re.compile(r"\[(?:System\.)?IO\.File\]::Read", re.IGNORECASE)
# A command name built at run time: `&( ... )`, `.( ... )` or Invoke-Expression, from joined strings.
PS_DYNAMIC_CALL = re.compile(r"(?:^|[\s;|({])[&.]\s*\(|(?<![\w-])(?:iex|Invoke-Expression)(?![\w-])", re.IGNORECASE)
PS_CONCAT = re.compile(r"""['"]\s*\+\s*['"$(]|-join\b|\[char\]""", re.IGNORECASE)
PS_RISKY_FLAG = re.compile(
    r"(?:^|\s)-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|f(?:o(?:r(?:c(?:e)?)?)?)?)(?=[\s:]|$)", re.IGNORECASE
)


# === evaluation =================================================================================
def check_command(command, shell, base_dir):
    """The block reason for a Bash or PowerShell command, else None."""
    dialect = POWERSHELL if shell == "PowerShell" else BASH
    text = command.replace("\\", "/") if dialect == POWERSHELL else command
    flat = normalized(text, dialect)
    top = parse(text, dialect)[0]
    scripts = all_scripts(top)

    for script in scripts:
        for item in script.commands:
            if any(deletes_recursively(view, script.dialect) for view in command_views(item)):
                return RM_MESSAGE

    reason = read_reason(scripts, (text, flat))
    if reason:
        return reason
    if dialect == POWERSHELL and PS_FILE_READ.search(flat):
        token = hard_env_in_text(flat)
        if token:
            return env_message("this command would put", token)
        label = secret_match(flat)
        if label:
            return secret_message("this command would put", label)

    if any(runs_downloaded_code(script) for script in scripts):
        return DOWNLOAD_MESSAGE

    if dialect == BASH:
        for item in top.commands:
            views = command_views(item)
            target = mv_overwrite_target(views[0], base_dir) if views else None
            if target:
                return (
                    "this mv would silently overwrite an existing path (%s). Replacing a file needs a "
                    "deliberate decision: `mv -f` is not allowed either, so write the result to the final "
                    "path directly, or ask the user to replace the file." % target
                )

    for pattern, why in DANGEROUS_PATTERNS:
        if pattern.search(text) or pattern.search(flat):
            return why

    if dialect == POWERSHELL:
        if PS_EXECUTE.search(flat) and PS_DOWNLOAD.search(flat):
            return "this command downloads code and runs it with Invoke-Expression."
        if PS_DYNAMIC_CALL.search(flat) and PS_CONCAT.search(flat) and (
            PS_RISKY_FLAG.search(flat) or hard_env_in_text(flat)
        ):
            return (
                "this command builds a command name from strings and runs it with -Recurse, -Force or "
                "an env file, which hides what it really does."
            )
    return None


def check_path(path, verb):
    """The block reason for a Read or Grep path, else None. `verb` is "reading" or "searching"."""
    normalized_path = path.replace("\\", "/")
    label = secret_match(normalized_path.rstrip("/") + "/")
    if label:
        return secret_message("%s %s would put" % (verb, path), label)
    if env_name_hard(normalized_path.rstrip("/").rsplit("/", 1)[-1]):
        return env_message("%s this file would put" % verb, path)
    return None


def check_grep_glob(glob, path):
    normalized_glob = glob.replace("\\", "/")
    for candidate in brace_expansions(normalized_glob):
        if env_name_hard(candidate.rstrip("/").rsplit("/", 1)[-1]):
            return (
                "searching the glob %s would put the values of hard env files into the conversation. "
                "To see which keys an env file holds without their values, run `%s --from <path>`." % (glob, HELPER)
            )
    label = secret_match(path.replace("\\", "/").rstrip("/") + "/" + normalized_glob + "/")
    if label:
        return secret_message("searching the glob %s would put" % glob, label)
    return None


def session_dir(data):
    cwd = data.get("cwd")
    if isinstance(cwd, str) and os.path.isdir(cwd):
        return cwd
    return os.getcwd()


def text_field(tool_input, key):
    value = tool_input.get(key)
    return value if isinstance(value, str) else ""


def evaluate(data):
    """(reason, blocked command or None) when the call must be blocked, else None."""
    tool = data.get("tool_name")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool in ("Bash", "PowerShell"):
        command = text_field(tool_input, "command")
        if not command.strip():
            return None
        reason = check_command(command, tool, session_dir(data))
        return (reason, command) if reason else None

    if tool == "Read":
        path = text_field(tool_input, "file_path")
        reason = check_path(path, "reading") if path else None
        return (reason, None) if reason else None

    if tool == "Grep":
        path = text_field(tool_input, "path")
        glob = text_field(tool_input, "glob")
        reason = (check_path(path, "searching") if path else None) or (
            check_grep_glob(glob, path) if glob else None
        )
        return (reason, None) if reason else None

    return None


def shorten(command):
    return command if len(command) <= SHOWN_LIMIT else command[:SHOWN_LIMIT] + " [...]"


def write_stderr(text):
    # Bytes, not text: on Windows the console encoding may not cover every character of a command,
    # and an encoding error here would crash the hook, which Claude Code treats as "let it through".
    sys.stderr.buffer.write(text.encode("utf-8", "replace"))
    sys.stderr.flush()


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except ValueError:
        # Deliberate fail-open: Claude Code always sends valid JSON, so unparsable input is not a
        # tool call this check could judge, and blocking it would block every call and brick the
        # session if the input format ever changed.
        return 0
    if not isinstance(data, dict):
        return 0

    try:
        verdict = evaluate(data)
    except Exception as error:
        # Deliberate fail-closed: this is a real tool call the check could not evaluate, and a
        # safety gate must not let such a call through unchecked.
        verdict = (
            "the safety check hit an internal error (%s: %s) while checking this call, so the call "
            "is blocked rather than let through unchecked. Tell the user; updating or reinstalling "
            "the plugin may fix it." % (type(error).__name__, error),
            None,
        )
    if not verdict:
        return 0

    reason, command = verdict
    lines = ["%s %s" % (PREFIX, reason)]
    if command:
        lines.append("Blocked command: %s" % shorten(command))
    lines.append(TRAILER)
    write_stderr("\n".join(lines) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
