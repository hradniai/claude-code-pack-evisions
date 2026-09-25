#!/usr/bin/env python3
"""Add the evisions safety baseline to the user's Codex CLI configuration, check it, undo it.

A Codex plugin can ship skills and hooks, but not command rules, config.toml keys or global
instructions. This script adds those in the Codex home ($CODEX_HOME, else ~/.codex) instead;
bin/evisions-codex-settings runs it. What --apply adds:
  - rules/evisions.rules, a copy of settings/codex-baseline.rules: forbidden rules for destructive
    commands;
  - in config.toml, [shell_environment_policy] ignore_default_excludes = false plus exclude filters
    for secret-looking variable names, so commands Codex runs do not inherit secret values;
  - a marked block in the active global instructions file (AGENTS.override.md when it has content,
    else AGENTS.md): the deny protocol in brief, and a canary that makes the agent warn the user when
    the plugin's safety hooks are not active;
  - with --deny-read (standard profile only, opt-in): a permission profile that extends :workspace,
    denies reading credential folders and .env files, and is set as default_permissions.
It never writes approval_policy, sandbox_mode or hook trust. The user trusts the plugin's hooks in
Codex itself; --check reports whether they are trusted.

config.toml is changed only through Codex's own writer (`codex app-server`, JSON-RPC
config/batchWrite), which keeps comments and unrelated tables, and every write is read back. Without
a working codex binary the script stops and writes nothing: it never edits TOML by hand. Starting the
app-server can create Codex's own state files in the Codex home, as any Codex start does; the script
itself writes nothing in a dry run.

Modes:
  (no flag)   dry run: print what --apply would change and write nothing
  --apply     make the changes; back up every file it changes first
  --apply --restore-declined
              the same, and also bring back baseline items the user removed
  --remove    undo exactly what earlier applies added, using the record
  --check     exit 0 when everything the record lists is in place AND the plugin's hooks are
              installed and trusted, else exit 1

Profiles: `standard` adds everything above. `managed` (Codex reports administrator requirements, or
a requirements.toml exists) adds only the rules file, the environment filters and the instructions
block, and never permission settings. When the requirements set allow_managed_hooks_only = true, the
plugin's hooks cannot run at all, and every mode says so.

The record at <codex-home>/evisions/codex-settings-applied.json lists exactly what this script added,
so --remove and upgrades take back only that. Its path is a contract: the plugin's SessionStart hook
tests for it. An item an earlier apply added that is now missing was removed on purpose: it moves to
the record's `declined` key and later applies leave it out until --restore-declined.

Python 3.9+, standard library only. Expected problems end with a plain sentence and exit 1, never a
traceback. The version guard below the imports runs before any other code. The file deliberately
avoids syntax newer than Python 3.6 (no `from __future__ import annotations`, no `X | None`), so an
older interpreter reaches the guard instead of stopping at a SyntaxError.
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

if sys.version_info < (3, 9):
    sys.stderr.write(
        "evisions-codex-settings needs Python 3.9 or newer, and this is Python %d.%d. Install a newer "
        "Python from https://www.python.org/downloads/ and run it again; nothing was written.\n" % sys.version_info[:2]
    )
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
RULES_SOURCE = PLUGIN_ROOT / "settings" / "codex-baseline.rules"
PLUGIN_MANIFESTS = (PLUGIN_ROOT / ".codex-plugin" / "plugin.json", PLUGIN_ROOT / ".claude-plugin" / "plugin.json")

PROGRAM = "evisions-codex-settings"
PLUGIN_NAME = "evisions"
RECORD_DIR = "evisions"
RECORD_FILE = "codex-settings-applied.json"
RULES_RELATIVE = "rules/evisions.rules"
CONFIG_FILE = "config.toml"
AGENTS_FILE = "AGENTS.md"
OVERRIDE_FILE = "AGENTS.override.md"
AGENT_FILES = (AGENTS_FILE, OVERRIDE_FILE)
BACKUP_INFIX = ".bak-evisions-"

STANDARD = "standard"
MANAGED = "managed"

POLICY = "shell_environment_policy"
FILTERS_TABLE = POLICY + ".filters"
EXCLUDES_KEY = POLICY + ".ignore_default_excludes"
# The exclude filters --apply adds. ignore_default_excludes = false already hides names containing
# KEY, SECRET or TOKEN; measured on Codex 0.154, these catch what that automatic list misses. A
# filter that leaves this list moves to RETIRED_ENV_FILTERS, so upgrades still take it back while a
# tampered record can never make --remove delete an unrelated filter of the user's.
ENV_FILTERS = (
    "*PASSWORD*",
    "*PASSWD*",
    "*CREDENTIAL*",
    "DATABASE_URL",
    "*_DSN",
    "*CONNECTION_STRING*",
    "AWS_*",
    "AZURE_*",
)
RETIRED_ENV_FILTERS = ()
KNOWN_FILTERS = ENV_FILTERS + RETIRED_ENV_FILTERS
# The only tables the installer creates, parents first; --remove deletes one only once it is empty.
CREATABLE_TABLES = (POLICY, FILTERS_TABLE)

# The optional deny-read permission profile (standard profile, --deny-read), as measured on Linux
# with Codex 0.154: a profile that extends :workspace, exact home paths, and exact-name globs under
# :workspace_roots. A glob can only deny, so .env.example and .env.shared stay readable only because
# the hard .env names are listed one by one. glob_scan_max_depth bounds the pre-expansion of **.
PROFILE_NAME = "evisions-workspace"
DENY_READ_PATHS = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.config/gh",
    "~/.git-credentials",
    "~/.docker/config.json",
    "~/.npmrc",
    "~/.pypirc",
    "~/Library/Keychains",
)
DENY_READ_GLOBS = (
    "**/.env",
    "**/.env.local",
    "**/.env.production",
    "**/.env.development",
    "**/.env.staging",
    "**/.env.test",
    "**/.env.*.local",
)
GLOB_SCAN_MAX_DEPTH = 3

BEGIN_MARKER = "<!-- evisions-safety:begin -->"
END_MARKER = "<!-- evisions-safety:end -->"
BLOCK_LINES = (
    "## evisions safety",
    "- If no <evisions_safety> block from the evisions plugin arrived at session start, tell the user the evisions safety hooks are not active (not trusted or not installed) before doing anything else.",
    "- When the evisions safety hook or a rule blocks a command, stop that step. Never retry it, reword it or reach the same result another way; give the user the exact command to run themselves and carry on with the rest.",
    "- Never print, log or repeat a secret value (API keys, tokens, passwords, what .env files hold). Key names are fine: the plugin's list-env-keys helper shows them.",
    "- Never change Codex's own settings, rules or hooks (config.toml, rules/, hooks.json) to get around a block.",
    "- evisions-codex-settings manages this block: edit outside the markers only; --remove takes it out.",
)

HOOK_FIX = (
    'To trust them: start codex, and in the "Hooks need review" screen choose "Trust all and continue" '
    "(or type /hooks in Codex to review them one by one). Then run " + PROGRAM + " --check again."
)
NO_CODEX = (
    "The Codex CLI was not found (there is no codex command on PATH). Install Codex, or pass the path of "
    "the codex program with --codex-bin. The installer changes config.toml only through Codex itself, "
    "so nothing was written."
)
RPC_TIMEOUT = 60
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InstallerError(Exception):
    """An expected problem: reported as plain sentences, exit code 1."""


class CodexRequestError(InstallerError):
    """Codex answered a request with an error."""

    def __init__(self, method: str, error: dict) -> None:
        self.method = method
        self.code = error.get("code")
        self.data = error.get("data") if isinstance(error.get("data"), dict) else {}
        self.detail = str(error.get("message") or error)
        super().__init__(f"Codex refused {method}: {self.detail}")


# --------------------------------------------------------------------------------------------------
# Small helpers


MISSING = object()


def same(left: object, right: object) -> bool:
    """JSON equality: unlike ==, it keeps 0 apart from false."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def show(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def tense(done: bool, past: str, future: str) -> str:
    return past if done else future


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_path(data: object, dotted: str) -> object:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def is_empty_table(value: object) -> bool:
    """True for a table that holds nothing but empty tables."""
    return isinstance(value, dict) and all(is_empty_table(item) for item in value.values())


def reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)


def dump_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file in the same directory and os.replace, so a crash never leaves a
    half-written file. A symlinked file is written through, so the link survives."""
    target = Path(os.path.realpath(str(path)))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(str(target)).st_mode)
    except FileNotFoundError:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(target))
    except BaseException:
        # Clean up the temporary file, then let the original error through.
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}-{counter}")
        counter += 1
    return candidate


def make_backup(path: Path, stamp: str, made: list) -> None:
    """Copy `path` to <path>.bak-evisions-<stamp> before it changes (the extension keeps a rules
    backup out of Codex's *.rules scan)."""
    backup = backup_path(path, stamp)
    shutil.copy2(os.path.realpath(str(path)), str(backup))
    made.append(backup)


def plugin_version() -> str:
    for manifest in PLUGIN_MANIFESTS:
        try:
            data = read_json(manifest)
        except (OSError, ValueError):
            continue
        version = data.get("version") if isinstance(data, dict) else None
        if isinstance(version, str) and version:
            return version
    return "unknown"


def resolve_codex_home(option: Optional[str]) -> Path:
    raw = option or os.environ.get("CODEX_HOME") or os.path.join("~", ".codex")
    return Path(os.path.abspath(os.path.expanduser(raw)))


def default_requirements_file() -> Path:
    """Where Codex reads an administrator's requirements file on this platform."""
    if sys.platform.startswith("win"):
        return Path(os.environ.get("ProgramData") or "C:\\ProgramData") / "OpenAI" / "Codex" / "requirements.toml"
    return Path("/etc/codex/requirements.toml")


def read_text(path: Path) -> Optional[str]:
    """A text file exactly as stored (line endings and a BOM kept), or None when it does not exist."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InstallerError(f"Could not read {path}: {error.strerror or error}. Nothing was written.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InstallerError(f"{path} is not UTF-8 text. Fix or move that file first; nothing was written.")


def rules_count(data: bytes) -> int:
    return len(re.findall(rb"^prefix_rule\(", data, flags=re.MULTILINE))


# --------------------------------------------------------------------------------------------------
# Codex: the app-server client. Tests replace it with a fake that has the same methods.


class AppServerCodex:
    """What the installer needs from Codex, through `codex app-server` (JSON-RPC over stdio) and
    `codex execpolicy check`. The server starts on first use and serves every request of one run."""

    def __init__(self, codex_bin: Optional[str], codex_home: Path) -> None:
        self.codex_bin = codex_bin
        self.codex_home = codex_home
        self.process = None
        self.messages: "queue.Queue" = queue.Queue()
        self.stderr_tail: list = []
        self.stderr_thread = None
        self.next_id = 0
        self.version: Optional[str] = None
        self._binary: Optional[str] = None

    def binary(self) -> str:
        if self._binary:
            return self._binary
        if self.codex_bin:
            path = os.path.abspath(os.path.expanduser(self.codex_bin))
            if not os.path.isfile(path):
                raise InstallerError(f"--codex-bin {path} does not exist. Nothing was written.")
            self._binary = path
        else:
            found = shutil.which("codex")
            if not found:
                raise InstallerError(NO_CODEX)
            self._binary = found
        return self._binary

    def environment(self) -> dict:
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.codex_home)
        return env

    def _read_stdout(self, stream) -> None:
        for line in stream:
            self.messages.put(line)
        self.messages.put(None)

    def _read_stderr(self, stream) -> None:
        for line in stream:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-20:]

    def start(self) -> None:
        if self.process is not None:
            return
        binary = self.binary()
        try:
            self.process = subprocess.Popen(
                [binary, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.environment(),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as error:
            raise InstallerError(
                f"Could not start {binary} app-server ({error.strerror or error}). The installer changes "
                "config.toml only through Codex itself, so nothing was written."
            )
        threading.Thread(target=self._read_stdout, args=(self.process.stdout,), daemon=True).start()
        self.stderr_thread = threading.Thread(target=self._read_stderr, args=(self.process.stderr,), daemon=True)
        self.stderr_thread.start()
        result = self._call(
            "initialize",
            {"clientInfo": {"name": PROGRAM, "version": plugin_version()}, "capabilities": {"experimentalApi": True}},
        )
        self._send({"jsonrpc": "2.0", "method": "initialized"})
        agent = result.get("userAgent") if isinstance(result, dict) else None
        match = re.match(r"^[^/]+/(\S+)", agent) if isinstance(agent, str) else None
        self.version = match.group(1) if match else None

    def _send(self, message: dict) -> None:
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise InstallerError(self._stopped_message())

    def _stopped_message(self) -> str:
        # Let the stderr reader catch the last lines of a server that just exited.
        if self.stderr_thread is not None:
            self.stderr_thread.join(timeout=5)
        detail = " ".join(line for line in self.stderr_tail if line and "PATH aliases" not in line)[-600:]
        return (
            "Codex's app-server stopped unexpectedly" + (f": {detail}" if detail else "") + ". The installer "
            "changes config.toml only through Codex itself, so it stopped here."
        )

    def _call(self, method: str, params: dict) -> object:
        self.next_id += 1
        request_id = self.next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            try:
                line = self.messages.get(timeout=RPC_TIMEOUT)
            except queue.Empty:
                raise InstallerError(f"Codex did not answer {method} within {RPC_TIMEOUT} seconds.")
            if line is None:
                raise InstallerError(self._stopped_message())
            try:
                message = json.loads(line)
            except ValueError:
                continue
            # Notifications and requests from the server carry a method; only our answer counts.
            if not isinstance(message, dict) or "method" in message or message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {"message": message["error"]}
                raise CodexRequestError(method, error)
            return message.get("result")

    def request(self, method: str, params: dict) -> object:
        self.start()
        return self._call(method, params)

    def read_config(self) -> dict:
        """The user layer of config.toml (as data), its version, and every other active layer."""
        result = self.request("config/read", {"includeLayers": True})
        state = {"user": {}, "version": None, "others": []}
        for layer in (result or {}).get("layers") or []:
            name = layer.get("name") or {}
            config = layer.get("config") if isinstance(layer.get("config"), dict) else {}
            if name.get("type") == "user" and not name.get("profile"):
                state["user"] = config
                state["version"] = layer.get("version")
            elif not layer.get("disabledReason"):
                state["others"].append((describe_layer(name), config))
        return state

    def write_config(self, edits: list, expected_version: Optional[str]) -> None:
        params = {"edits": edits}
        if expected_version:
            params["expectedVersion"] = expected_version
        self.request("config/batchWrite", params)

    def requirements(self) -> Optional[dict]:
        result = self.request("configRequirements/read", {})
        requirements = (result or {}).get("requirements")
        return requirements if isinstance(requirements, dict) else None

    def hooks(self) -> list:
        result = self.request("hooks/list", {})
        data = (result or {}).get("data")
        return data if isinstance(data, list) else []

    def check_rules(self, path: Path) -> Optional[str]:
        """None when Codex loads the rules file (which also runs its match/not_match examples)."""
        try:
            result = subprocess.run(
                [self.binary(), "execpolicy", "check", "--rules", str(path), "--", "true"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=self.environment(), timeout=RPC_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"codex execpolicy check could not run ({error})"
        if result.returncode == 0:
            return None
        return (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"

    def close(self) -> None:
        if self.process is None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None


def describe_layer(name: dict) -> str:
    kind = name.get("type")
    where = name.get("file") or name.get("dotCodexFolder") or name.get("domain") or name.get("name")
    labels = {
        "system": "the system config",
        "mdm": "a device-management profile",
        "enterpriseManaged": "the organisation's cloud config",
        "project": "a project's .codex folder",
        "sessionFlags": "command-line flags",
        "legacyManagedConfigTomlFromFile": "the legacy managed_config.toml",
        "legacyManagedConfigTomlFromMdm": "a legacy device-management profile",
        "packagedDefaults": "Codex's packaged defaults",
    }
    label = labels.get(kind, f"a {kind} config layer")
    return f"{label} ({where})" if where else label


# --------------------------------------------------------------------------------------------------
# Profiles and administrator requirements


def has_requirements(requirements: Optional[dict]) -> bool:
    return isinstance(requirements, dict) and any(value not in (None, [], {}) for value in requirements.values())


def requirements_file_state(path: Path) -> tuple:
    """(exists, text or None). A file that exists but cannot be read still counts as present."""
    try:
        if not path.is_file():
            return False, None
        return True, path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return True, None
    except OSError:
        return False, None


def detect_profile(option: Optional[str], requirements: Optional[dict], requirements_file: Path) -> tuple:
    if option:
        return option, "chosen with --profile"
    if has_requirements(requirements):
        return MANAGED, "Codex reports administrator requirements on this machine"
    exists, _ = requirements_file_state(requirements_file)
    if exists:
        return MANAGED, f"an administrator requirements file exists at {requirements_file}"
    return STANDARD, "no administrator requirements were found"


def managed_hooks_only(requirements: Optional[dict], requirements_file: Path) -> bool:
    if isinstance(requirements, dict) and requirements.get("allowManagedHooksOnly") is True:
        return True
    _, text = requirements_file_state(requirements_file)
    return bool(text and re.search(r"(?m)^\s*allow_managed_hooks_only\s*=\s*true\b", text))


def hooks_only_warning() -> str:
    return (
        "WARNING: the administrator's requirements set allow_managed_hooks_only = true. Codex then runs only "
        "hooks the administrator manages, so the evisions plugin's safety hooks will NOT run on this machine. "
        "Ask the administrator to allow them (for example as managed hooks in requirements.toml). The rules "
        "file, the environment filters and the instructions block still work."
    )


# --------------------------------------------------------------------------------------------------
# The record


def empty_declined() -> dict:
    return {"rules_file": False, "ignore_default_excludes": False, "filters": [], "agents": False, "deny_read": False}


RECORD_KEYS = (
    "baseline_version", "profile", "applied_at", "pending", "rules_file", "ignore_default_excludes",
    "filters", "created_tables", "agents", "deny_read", "declined", "created_config_file",
)


def filter_list(value: object, name: str, problems: list) -> list:
    if not isinstance(value, list):
        problems.append(f'"{name}" is not a list')
        return []
    result = []
    for entry in value:
        if entry not in KNOWN_FILTERS:
            problems.append(f'"{name}" names {show(entry)}, which is not a filter the installer writes')
        elif entry in result:
            problems.append(f'"{name}" lists {show(entry)} twice')
        else:
            result.append(entry)
    return result


def load_record(path: Path) -> Optional[dict]:
    """The validated record, or None when there is none.

    The record decides what --remove deletes and what --apply treats as the user's choice, so it is
    checked strictly: every key and value must be one the installer itself could have written.
    Anything else refuses the whole run rather than act on a damaged or edited record.
    """
    if not path.exists():
        return None
    advice = (
        f"If you did not edit that file yourself, delete it and run {PROGRAM} --apply again; the installer "
        "then treats everything already in your Codex settings as yours. Nothing was written."
    )
    try:
        raw = read_json(path)
    except (OSError, ValueError) as error:
        raise InstallerError(f"The install record {path} cannot be read ({error}). {advice}")
    if not isinstance(raw, dict):
        raise InstallerError(f"The install record {path} is not a JSON object. {advice}")

    problems = [f'unknown key "{key}"' for key in raw if key not in RECORD_KEYS]
    rules_file = raw.get("rules_file")
    if rules_file is not None and not (
        isinstance(rules_file, dict)
        and set(rules_file) == {"path", "sha256"}
        and rules_file["path"] == RULES_RELATIVE
        and isinstance(rules_file["sha256"], str)
        and SHA256.match(rules_file["sha256"])
    ):
        problems.append(f'"rules_file" may only name {RULES_RELATIVE} with its checksum, but holds {show(rules_file)}')
    filters = filter_list(raw.get("filters", []), "filters", problems)
    created_tables = raw.get("created_tables", [])
    if not isinstance(created_tables, list) or any(entry not in CREATABLE_TABLES for entry in created_tables):
        problems.append(f'"created_tables" may only name {", ".join(CREATABLE_TABLES)}, but holds {show(created_tables)}')
        created_tables = []
    agents = raw.get("agents")
    if agents is not None and not (
        isinstance(agents, dict)
        and set(agents) == {"file", "created_file"}
        and agents["file"] in AGENT_FILES
        and isinstance(agents["created_file"], bool)
    ):
        problems.append(f'"agents" may only name {" or ".join(AGENT_FILES)}, but holds {show(agents)}')
    for key in ("pending", "ignore_default_excludes", "deny_read", "created_config_file"):
        if key in raw and not isinstance(raw[key], bool):
            problems.append(f'"{key}" is not true or false')
    declined_raw = raw.get("declined", {})
    declined = empty_declined()
    if not isinstance(declined_raw, dict):
        problems.append('"declined" is not an object')
    else:
        for key, value in declined_raw.items():
            if key not in declined:
                problems.append(f'unknown key "declined.{key}"')
            elif key == "filters":
                declined["filters"] = filter_list(value, "declined.filters", problems)
            elif not isinstance(value, bool):
                problems.append(f'"declined.{key}" is not true or false')
            else:
                declined[key] = value
    if raw.get("profile") not in (None, STANDARD, MANAGED):
        problems.append(f'"profile" is {show(raw.get("profile"))}')
    for key in ("baseline_version", "applied_at"):
        if not isinstance(raw.get(key, ""), str):
            problems.append(f'"{key}" is not text')

    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise InstallerError(
            f"The install record {path} holds entries the installer never writes, so it will not act on it:\n"
            f"{details}\n{advice}"
        )
    return {
        "baseline_version": raw.get("baseline_version"),
        "profile": raw.get("profile"),
        "applied_at": raw.get("applied_at"),
        "pending": raw.get("pending", False),
        "rules_file": rules_file,
        "ignore_default_excludes": raw.get("ignore_default_excludes", False),
        "filters": filters,
        "created_tables": list(created_tables),
        "agents": agents,
        "deny_read": raw.get("deny_read", False),
        "declined": declined,
        "created_config_file": raw.get("created_config_file", False),
    }


def record_without_time(record: Optional[dict]) -> Optional[dict]:
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "applied_at"}


def pending_record(old: Optional[dict], new: dict) -> dict:
    """The record written before anything else: the union of the old and the new record, marked pending.

    If the run stops part way, the next --apply sees the mark and finishes the job instead of reading
    unwritten items as declined, and --remove still knows every item that may have landed.
    """
    merged = copy.deepcopy(new)
    merged["pending"] = True
    if old is None:
        return merged
    declined = new["declined"]
    if merged["rules_file"] is None and old["rules_file"] and not declined["rules_file"]:
        merged["rules_file"] = old["rules_file"]
    merged["ignore_default_excludes"] = new["ignore_default_excludes"] or (
        old["ignore_default_excludes"] and not declined["ignore_default_excludes"]
    )
    merged["filters"] += [p for p in old["filters"] if p not in merged["filters"] and p not in declined["filters"]]
    merged["created_tables"] += [t for t in old["created_tables"] if t not in merged["created_tables"]]
    if merged["agents"] is None and old["agents"] and not declined["agents"]:
        merged["agents"] = old["agents"]
    merged["deny_read"] = new["deny_read"] or (old["deny_read"] and not declined["deny_read"])
    merged["created_config_file"] = new["created_config_file"] or old["created_config_file"]
    return merged


# --------------------------------------------------------------------------------------------------
# The instructions block


def split_lines(text: str) -> list:
    """Lines with their endings; only "\\n" ends a line, as for Codex and git."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def find_block(text: Optional[str]) -> tuple:
    """("none", None), ("one", (start, end)) with the offsets of the marker lines, or ("broken", reason)."""
    if text is None:
        return "none", None
    lines = split_lines(text)
    begins = [index for index, line in enumerate(lines) if line.strip() == BEGIN_MARKER]
    ends = [index for index, line in enumerate(lines) if line.strip() == END_MARKER]
    if text.count(BEGIN_MARKER) != len(begins) or text.count(END_MARKER) != len(ends):
        return "broken", "a marker shares its line with other text"
    if not begins and not ends:
        return "none", None
    if len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0]:
        start = sum(len(line) for line in lines[: begins[0]])
        end = sum(len(line) for line in lines[: ends[0] + 1])
        return "one", (start, end)
    return "broken", "the markers are not one begin line followed by one end line"


def newline_of(text: Optional[str]) -> str:
    return "\r\n" if text and "\r\n" in text else "\n"


def block_text(newline: str) -> str:
    return newline.join((BEGIN_MARKER,) + BLOCK_LINES + (END_MARKER,)) + newline


def insert_block(text: str, block: str, newline: str) -> str:
    """Append the block after one blank line; the user's text stays as it is."""
    if not text.strip("\ufeff"):
        return text + block
    if text.endswith(newline + newline):
        separator = ""
    elif text.endswith("\n"):
        separator = newline
    else:
        separator = newline + newline
    return text + separator + block


def remove_block(text: str, span: tuple, newline: str) -> str:
    """Take the block out, and the one blank line insert_block put before it."""
    before, after = text[: span[0]], text[span[1]:]
    if not after and before.endswith(newline + newline):
        before = before[: -len(newline)]
    elif after.startswith(newline) and (not before or before.endswith(newline + newline)):
        after = after[len(newline):]
    return before + after


# --------------------------------------------------------------------------------------------------
# Planning


def deny_read_table() -> dict:
    filesystem = {"glob_scan_max_depth": GLOB_SCAN_MAX_DEPTH}
    for path in DENY_READ_PATHS:
        filesystem[path] = "deny"
    filesystem[":workspace_roots"] = {pattern: "deny" for pattern in DENY_READ_GLOBS}
    return {"extends": ":workspace", "filesystem": filesystem}


class State:
    """What is on disk and in Codex now."""

    def __init__(self, context, config: dict, requirements: Optional[dict]) -> None:
        self.user = config.get("user") if isinstance(config.get("user"), dict) else {}
        self.version = config.get("version")
        self.others = list(config.get("others") or [])
        self.requirements = requirements
        self.config_exists = context.config_path.exists()
        try:
            self.rules_bytes = context.rules_target.read_bytes() if context.rules_target.is_file() else None
        except OSError as error:
            raise InstallerError(f"Could not read {context.rules_target}: {error.strerror or error}. Nothing was written.")
        self.source_bytes = context.source_bytes
        self.agents = {name: read_text(context.codex_home / name) for name in AGENT_FILES}


class Plan:
    """What a run changes, how to describe it, and the new record."""

    def __init__(self) -> None:
        self.record: dict = {}
        self.declined = empty_declined()
        self.newly_declined: list = []
        self.rules_action: Optional[str] = None  # copy, refresh, delete
        self.rules_backup = False
        self.edits: list = []  # config/batchWrite edits
        self.checks: list = []  # (dotted key, expected value or MISSING), read back after the write
        self.excludes: Optional[str] = None  # set, ours, same, kept, removed
        self.excludes_value: object = None
        self.env_added: list = []
        self.env_ours: list = []
        self.env_same: list = []
        self.env_kept: list = []
        self.env_removed: list = []
        self.env_blocked: list = []
        self.legacy_env: list = []
        self.deny: Optional[str] = None  # add, update, ours, remove
        self.deny_skipped: list = []
        self.deny_note: Optional[str] = None
        self.agents_writes: list = []  # (file name, new text or None to delete)
        self.agents: Optional[str] = None  # add, update, same, moved, removed, broken
        self.agents_file: Optional[str] = None
        self.agents_created = False
        self.agents_left: list = []  # (file name, reason) of marked blocks --remove leaves alone
        self.warnings: list = []

    def set_key(self, dotted: str, value: object, strategy: str = "upsert") -> None:
        self.edits.append({"keyPath": dotted, "mergeStrategy": strategy, "value": value})
        self.checks.append((dotted, value))

    def delete_key(self, dotted: str) -> None:
        self.edits.append({"keyPath": dotted, "mergeStrategy": "replace", "value": None})
        self.checks.append((dotted, MISSING))


def plan_changes(state: State, record: Optional[dict], wanted: dict, restore_declined: bool = False) -> Plan:
    """Plan every change: add what is wanted and missing, take back what the record lists as ours but
    is no longer wanted (an upgrade, a profile switch, --remove), and leave the user's own values alone.

    A recorded item missing from the files was removed by the user on purpose: it becomes declined and
    is not added again. A pending record belongs to an apply that failed part way, so its missing
    items may never have been written: they are added, never declined. `restore_declined` forgets
    every decline.
    """
    plan = Plan()
    carry = record is not None and not restore_declined
    detect = carry and not record["pending"]
    previous = record["declined"] if carry else empty_declined()
    plan_rules(plan, state, record, wanted, detect, previous)
    plan_env(plan, state, record, wanted, detect, previous)
    plan_deny_read(plan, state, record, wanted, detect, previous)
    plan_agents(plan, state, record, wanted, detect, previous)
    plan.record["declined"] = plan.declined
    return plan


def plan_rules(plan: Plan, state: State, record: Optional[dict], wanted: dict, detect: bool, previous: dict) -> None:
    recorded = record["rules_file"] if record else None
    have = state.rules_bytes
    entry = {"path": RULES_RELATIVE, "sha256": sha256(state.source_bytes)}
    plan.record["rules_file"] = None
    if wanted["rules"]:
        if have is None:
            if recorded and detect:
                plan.declined["rules_file"] = True
                plan.newly_declined.append(f"the command rules file {RULES_RELATIVE}")
            elif previous["rules_file"]:
                plan.declined["rules_file"] = True
            else:
                plan.rules_action = "copy"
                plan.record["rules_file"] = entry
        else:
            plan.record["rules_file"] = entry
            if have != state.source_bytes:
                plan.rules_action = "refresh"
                plan.rules_backup = recorded is None or sha256(have) != recorded["sha256"]
    elif recorded and have is not None:
        plan.rules_action = "delete"
        plan.rules_backup = sha256(have) != recorded["sha256"]


def plan_env(plan: Plan, state: State, record: Optional[dict], wanted: dict, detect: bool, previous: dict) -> None:
    policy = state.user.get(POLICY) if isinstance(state.user.get(POLICY), dict) else {}
    filters = policy.get("filters") if isinstance(policy.get("filters"), dict) else {}
    plan.legacy_env = [name for name in ("exclude", "include_only") if name in policy]
    # Codex matches filter names case-insensitively; the file's own spelling is kept for deletes.
    by_lower = {str(key).lower(): (key, value) for key, value in filters.items()}
    previous_filters = record["filters"] if record else []
    created = list(record["created_tables"]) if record else []
    had_policy = POLICY in state.user
    had_filters = "filters" in policy

    have = policy.get("ignore_default_excludes", MISSING)
    ours = bool(record and record["ignore_default_excludes"])
    plan.record["ignore_default_excludes"] = False
    if wanted["excludes"]:
        if have is MISSING:
            if ours and detect:
                plan.declined["ignore_default_excludes"] = True
                plan.newly_declined.append("shell_environment_policy.ignore_default_excludes")
            elif previous["ignore_default_excludes"]:
                plan.declined["ignore_default_excludes"] = True
            else:
                plan.set_key(EXCLUDES_KEY, False)
                plan.excludes = "set"
                plan.record["ignore_default_excludes"] = True
        elif have is False:
            plan.excludes = "ours" if ours else "same"
            plan.record["ignore_default_excludes"] = ours
        else:
            plan.excludes, plan.excludes_value = "kept", have
    elif ours and have is False:
        plan.delete_key(EXCLUDES_KEY)
        plan.excludes = "removed"

    recorded = []
    for pattern in wanted["filters"]:
        entry = by_lower.get(pattern.lower())
        mine = pattern in previous_filters
        if entry is None:
            if mine and detect:
                plan.declined["filters"].append(pattern)
                plan.newly_declined.append(f"environment filter {pattern}")
            elif pattern in previous["filters"]:
                plan.declined["filters"].append(pattern)
            elif plan.legacy_env:
                plan.env_blocked.append(pattern)
            else:
                plan.set_key(f"{FILTERS_TABLE}.{pattern}", "exclude")
                plan.env_added.append(pattern)
                recorded.append(pattern)
        elif entry[1] == "exclude":
            (plan.env_ours if mine else plan.env_same).append(pattern)
            if mine:
                recorded.append(pattern)
        else:
            plan.env_kept.append((entry[0], entry[1]))
    removed_keys = set()
    for pattern in previous_filters:
        if pattern in wanted["filters"]:
            continue
        entry = by_lower.get(pattern.lower())
        if entry is not None and entry[1] == "exclude":
            plan.delete_key(f"{FILTERS_TABLE}.{entry[0]}")
            plan.env_removed.append(pattern)
            removed_keys.add(entry[0])
    plan.record["filters"] = recorded

    # A table this run creates is recorded. Codex's writer leaves an emptied table's header behind,
    # so a recorded table is deleted once nothing of the user's is left in it (deepest first).
    adding = plan.excludes == "set" or bool(plan.env_added)
    if adding and not had_policy and POLICY not in created:
        created.append(POLICY)
    if plan.env_added and not had_filters and FILTERS_TABLE not in created:
        created.append(FILTERS_TABLE)
    filters_after = {key: value for key, value in filters.items() if key not in removed_keys}
    if FILTERS_TABLE in created and had_filters and not filters_after and not plan.env_added:
        plan.delete_key(FILTERS_TABLE)
        created.remove(FILTERS_TABLE)
    policy_after = {}
    for key, value in policy.items():
        if key == "ignore_default_excludes" and plan.excludes == "removed":
            continue
        policy_after[key] = filters_after if key == "filters" else value
    if POLICY in created and had_policy and not adding and is_empty_table(policy_after):
        plan.delete_key(POLICY)
        created = [table for table in created if table not in (POLICY, FILTERS_TABLE)]
    plan.record["created_tables"] = [table for table in CREATABLE_TABLES if table in created]


def deny_read_blockers(state: State, current: object, table: object) -> list:
    reasons = []
    if current is not MISSING:
        reasons.append(f"your config.toml already sets default_permissions = {show(current)}")
    if table is not MISSING:
        reasons.append(f"your config.toml already has a permission profile named {PROFILE_NAME}")
    for description, config in [("your config.toml", state.user)] + state.others:
        if "sandbox_mode" in config:
            reasons.append(f"sandbox_mode is set in {description}, and Codex then ignores default_permissions")
    allowed = state.requirements.get("allowedPermissionProfiles") if isinstance(state.requirements, dict) else None
    if isinstance(allowed, dict) and not allowed.get(PROFILE_NAME):
        names = ", ".join(sorted(name for name, on in allowed.items() if on)) or "none"
        reasons.append(
            f"the administrator allows only these permission profiles: {names}; Codex would replace {PROFILE_NAME} silently"
        )
    return reasons


def plan_deny_read(plan: Plan, state: State, record: Optional[dict], wanted: dict, detect: bool, previous: dict) -> None:
    current = state.user.get("default_permissions", MISSING)
    permissions = state.user.get("permissions") if isinstance(state.user.get("permissions"), dict) else {}
    table = permissions.get(PROFILE_NAME, MISSING)
    ours = bool(record and record["deny_read"])
    plan.record["deny_read"] = False
    referencing = sorted(
        name for name, value in permissions.items()
        if name != PROFILE_NAME and isinstance(value, dict) and value.get("extends") == PROFILE_NAME
    )

    def take_back() -> None:
        if current == PROFILE_NAME:
            plan.delete_key("default_permissions")
        if table is not MISSING:
            if referencing:
                plan.warnings.append(
                    f"The permission profile {PROFILE_NAME} stays, because your profile {', '.join(referencing)} "
                    "extends it. Delete it yourself once nothing uses it."
                )
            else:
                plan.delete_key(f"permissions.{PROFILE_NAME}")
        plan.deny = "remove"

    if not wanted["deny_read"]:
        if ours:
            take_back()
        return
    if ours:
        if detect and current != PROFILE_NAME:
            plan.declined["deny_read"] = True
            plan.newly_declined.append(f"the deny-read profile (default_permissions = {show(PROFILE_NAME)})")
            take_back()
            return
        if current is MISSING:
            plan.set_key("default_permissions", PROFILE_NAME, "replace")
        desired = deny_read_table()
        if not same(table, desired):
            plan.set_key(f"permissions.{PROFILE_NAME}", desired, "replace")
            plan.deny = "update" if table is not MISSING else "add"
        else:
            plan.deny = "update" if current is MISSING else "ours"
        plan.record["deny_read"] = True
        return
    if previous["deny_read"] and not wanted["deny_read_explicit"]:
        plan.declined["deny_read"] = True
        return
    blockers = deny_read_blockers(state, current, table)
    if blockers:
        plan.deny_skipped = blockers
        return
    plan.set_key(f"permissions.{PROFILE_NAME}", deny_read_table(), "replace")
    plan.set_key("default_permissions", PROFILE_NAME, "replace")
    plan.deny = "add"
    plan.record["deny_read"] = True


def plan_agents(plan: Plan, state: State, record: Optional[dict], wanted: dict, detect: bool, previous: dict) -> None:
    texts = state.agents
    active = OVERRIDE_FILE if (texts[OVERRIDE_FILE] or "").strip("\ufeff \t\r\n") else AGENTS_FILE
    blocks = {name: find_block(texts[name]) for name in AGENT_FILES}
    prev = record["agents"] if record else None
    plan.record["agents"] = None
    broken = [(name, blocks[name][1]) for name in AGENT_FILES if blocks[name][0] == "broken"]
    if broken:
        for name, reason in broken:
            plan.warnings.append(
                f"{name} holds evisions-safety markers the installer cannot use ({reason}), so it left the "
                f"instructions block alone. Fix or delete the marker lines {BEGIN_MARKER} and {END_MARKER}, "
                "then run this again."
            )
        plan.agents = "broken"
        plan.record["agents"] = prev
        plan.declined["agents"] = previous["agents"]
        return

    def created_here(name: str) -> bool:
        return bool(prev and prev["file"] == name and prev["created_file"])

    def take_out(name: str) -> None:
        new_text = remove_block(texts[name], blocks[name][1], newline_of(texts[name]))
        plan.agents_writes.append((name, None if created_here(name) and not new_text.strip("\ufeff \t\r\n") else new_text))

    if not wanted["agents"]:
        # Only the block the record names, and only while it is still exactly what the installer wrote;
        # any other marked block is the user's to delete, so it stays and the user hears where it is.
        for name in AGENT_FILES:
            if blocks[name][0] != "one":
                continue
            start, end = blocks[name][1]
            if not (prev and prev["file"] == name):
                plan.agents_left.append((name, "the install record does not name that file"))
            elif texts[name][start:end] != block_text(newline_of(texts[name])):
                plan.agents_left.append((name, "the text between its markers was changed after the install"))
            else:
                take_out(name)
                plan.agents = "removed"
        return

    text = texts[active]
    newline = newline_of(text)
    block = block_text(newline)
    plan.agents_file = active
    if blocks[active][0] == "one":
        start, end = blocks[active][1]
        if text[start:end] != block:
            plan.agents_writes.append((active, text[:start] + block + text[end:]))
            plan.agents = "update"
        else:
            plan.agents = "same"
        plan.agents_created = created_here(active)
    else:
        if prev and detect and blocks[prev["file"]][0] == "none":
            plan.declined["agents"] = True
            plan.newly_declined.append(f"the instructions block in {prev['file']}")
            return
        if previous["agents"]:
            plan.declined["agents"] = True
            return
        plan.agents_writes.append((active, insert_block(text or "", block, newline)))
        plan.agents = "add"
        plan.agents_created = text is None
    other = OVERRIDE_FILE if active == AGENTS_FILE else AGENTS_FILE
    if blocks[other][0] == "one":
        take_out(other)
        plan.agents = "moved"
    plan.record["agents"] = {"file": active, "created_file": plan.agents_created}


# --------------------------------------------------------------------------------------------------
# Reporting


class Context:
    """Everything a run needs, resolved from the command line."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.codex_home = resolve_codex_home(args.codex_home)
        self.config_path = self.codex_home / CONFIG_FILE
        self.rules_target = self.codex_home / RULES_RELATIVE
        self.record_dir = self.codex_home / RECORD_DIR
        self.record_path = self.record_dir / RECORD_FILE
        self.requirements_file = Path(args.requirements_file) if args.requirements_file else default_requirements_file()
        self.version = plugin_version()
        self.source_bytes = b""

    def require_home(self) -> None:
        if not self.codex_home.is_dir():
            raise InstallerError(
                f"The Codex home {self.codex_home} does not exist, so Codex has not been set up there. Start codex "
                "once (or check CODEX_HOME and --codex-home), then run this again. Nothing was written."
            )

    def load_source(self) -> None:
        try:
            self.source_bytes = RULES_SOURCE.read_bytes()
        except OSError:
            raise InstallerError(f"The plugin's rules file {RULES_SOURCE} is missing. Reinstall the evisions plugin.")


def print_lines(lines: list) -> None:
    print("\n".join(lines))


def describe(plan: Plan, context: Context, done: bool) -> list:
    lines = []
    target = context.rules_target
    count = rules_count(context.source_bytes)
    if plan.rules_action == "copy":
        lines.append(f"Command rules: {tense(done, 'copied', 'will copy')} the evisions rules ({plural(count, 'rule')} that block destructive commands) to {target}")
    elif plan.rules_action == "refresh":
        extra = " (the file there differs from what the installer wrote, so it is backed up first)" if plan.rules_backup else ""
        lines.append(f"Command rules: {target} {tense(done, 'was refreshed', 'will be refreshed')} from the plugin{extra}")
    elif plan.rules_action == "delete":
        extra = " (it was edited after the install, so a backup is kept)" if plan.rules_backup else ""
        lines.append(f"Command rules: {tense(done, 'deleted', 'will delete')} {target}{extra}")
    elif plan.record.get("rules_file"):
        lines.append(f"Command rules: {target} is up to date ({plural(count, 'rule')}).")

    env = []
    if plan.excludes == "set":
        env.append(f"  ignore_default_excludes {tense(done, 'set', 'will be set')} to false (hides names containing KEY, SECRET or TOKEN)")
    elif plan.excludes == "same":
        env.append("  ignore_default_excludes = false: you already have it")
    elif plan.excludes == "kept":
        env.append(
            f"  ignore_default_excludes: left as you have it ({show(plan.excludes_value)}); with that value, variables "
            "whose names contain KEY, SECRET or TOKEN reach every command Codex runs"
        )
    elif plan.excludes == "removed":
        env.append(f"  ignore_default_excludes {tense(done, 'removed', 'will be removed')}")
    in_place = (["ignore_default_excludes = false"] if plan.excludes == "ours" else []) + (
        [plural(len(plan.env_ours), "exclude filter")] if plan.env_ours else []
    )
    if in_place:
        env.append("  in place: " + " and ".join(in_place))
    if plan.env_added:
        env.append(f"  exclude filters {tense(done, 'added', 'to add')}: {', '.join(plan.env_added)}")
    if plan.env_same:
        env.append(f"  exclude filters you already have: {', '.join(plan.env_same)}")
    for key, value in plan.env_kept:
        env.append(f"  filter {key} left as you have it ({show(value)})")
    if plan.env_removed:
        env.append(f"  exclude filters {tense(done, 'removed', 'to remove')}: {', '.join(plan.env_removed)}")
    if plan.env_blocked:
        env.append(
            f"  not added: {', '.join(plan.env_blocked)}. Your config.toml uses the older shell_environment_policy "
            f"{' and '.join(plan.legacy_env)} list, and Codex rejects the filters table next to it in the same file. "
            'Move your entries into [shell_environment_policy.filters] (each as "PATTERN" = "exclude") and run --apply again.'
        )
    if env:
        lines.append(f"Environment variables kept away from commands Codex runs ({CONFIG_FILE} [shell_environment_policy]):")
        lines += env

    if plan.deny == "add":
        lines.append(
            f"Deny-read profile: {tense(done, 'added', 'will add')} the permission profile {PROFILE_NAME} (extends :workspace) and "
            f'{tense(done, "set", "will set")} default_permissions = "{PROFILE_NAME}". Sandboxed commands cannot read '
            f"{', '.join(DENY_READ_PATHS)}, nor {', '.join(g[3:] for g in DENY_READ_GLOBS)} in any workspace folder."
        )
    elif plan.deny == "update":
        lines.append(f"Deny-read profile: {PROFILE_NAME} {tense(done, 'was brought up to date', 'will be brought up to date')}.")
    elif plan.deny == "ours":
        lines.append(f"Deny-read profile: {PROFILE_NAME} is in place.")
    elif plan.deny == "remove":
        lines.append(f"Deny-read profile: {PROFILE_NAME} and default_permissions {tense(done, 'removed', 'will be removed')}.")
    if plan.deny in ("add", "update", "ours"):
        lines.append(
            "  Measured on Linux only. Codex ignores it under --yolo (full access), when sandbox_mode is set in any "
            "config file, and when --sandbox is passed."
        )
    for reason in plan.deny_skipped:
        lines.append(f"Deny-read profile: skipped, because {reason}.")
    if plan.deny_note:
        lines.append(f"Deny-read profile: {plan.deny_note}")

    agents_path = context.codex_home / (plan.agents_file or AGENTS_FILE)
    if plan.agents == "add":
        created = " (the file will be created)" if plan.agents_created and not done else (" (the file was created)" if plan.agents_created else "")
        lines.append(f"Global instructions: {tense(done, 'added', 'will add')} the evisions safety block to {agents_path}{created}")
    elif plan.agents == "update":
        lines.append(f"Global instructions: the evisions safety block in {agents_path} {tense(done, 'was updated', 'will be updated')}")
    elif plan.agents == "moved":
        lines.append(
            f"Global instructions: {tense(done, 'moved', 'will move')} the evisions safety block to {agents_path}, "
            "the file Codex reads now"
        )
    elif plan.agents == "same":
        lines.append(f"Global instructions: the evisions safety block in {agents_path} is up to date.")
    elif plan.agents == "removed":
        lines.append(f"Global instructions: the evisions safety block {tense(done, 'removed', 'will be removed')}.")
    for name, reason in plan.agents_left:
        lines.append(
            f"Note: left an evisions safety block in {context.codex_home / name}, because {reason}; delete the lines "
            f"from {BEGIN_MARKER} to {END_MARKER} yourself if you no longer want it."
        )

    lines += declined_lines(plan.declined)
    for warning in plan.warnings:
        lines.append(f"Warning: {warning}")
    return lines


def declined_lines(declined: dict) -> list:
    entries = []
    if declined["rules_file"]:
        entries.append(f"  the command rules file {RULES_RELATIVE}")
    if declined["ignore_default_excludes"]:
        entries.append("  shell_environment_policy.ignore_default_excludes = false")
    for pattern in declined["filters"]:
        entries.append(f"  environment filter {pattern}")
    if declined["agents"]:
        entries.append("  the instructions block")
    if declined["deny_read"]:
        entries.append(f"  the deny-read profile {PROFILE_NAME}")
    if not entries:
        return []
    return [f"You removed these baseline items; --apply leaves them out ({PROGRAM} --apply --restore-declined brings them back):"] + entries


def is_evisions_hook(hook: dict) -> bool:
    plugin = hook.get("pluginId") or ""
    return plugin.split("@")[0] == PLUGIN_NAME or str(hook.get("key", "")).startswith(PLUGIN_NAME + "@")


def hook_report(codex, hooks_only: bool) -> tuple:
    """(ok, lines): whether the plugin's hooks are installed, enabled and trusted, per hook."""
    hooks, errors, seen = [], [], set()
    for entry in codex.hooks():
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks") or []:
            if isinstance(hook, dict) and is_evisions_hook(hook) and hook.get("key") not in seen:
                seen.add(hook.get("key"))
                hooks.append(hook)
        for error in entry.get("errors") or []:
            if PLUGIN_NAME in json.dumps(error):
                errors.append(error)
    lines = ["Plugin hooks (the evisions safety check runs as a Codex hook):"]
    if not hooks:
        lines.append(
            "  none found: the evisions plugin is not installed or not enabled in this Codex home. Install it with "
            "codex plugin add evisions@<marketplace>, then run this again."
        )
    for hook in sorted(hooks, key=lambda item: (str(item.get("eventName")), str(item.get("key")))):
        status = str(hook.get("trustStatus"))
        if hook.get("enabled") is False:
            status += ", disabled"
        lines.append(f"  {str(hook.get('eventName')):<16} {status:<12} {hook.get('key')}")
    for error in errors:
        lines.append(f"  error: {error.get('message', error)} ({error.get('path', '')})")
    untrusted = [hook for hook in hooks if hook.get("trustStatus") not in ("trusted", "managed")]
    disabled = [hook for hook in hooks if hook.get("enabled") is False]
    has_guard = any(hook.get("eventName") == "preToolUse" for hook in hooks)
    if hooks and not has_guard:
        lines.append("  The plugin's PreToolUse safety hook is missing; update or reinstall the evisions plugin.")
    if untrusted:
        modified = any(hook.get("trustStatus") == "modified" for hook in untrusted)
        reason = (
            "Some hook definitions changed since they were trusted (usually after a plugin update), and Codex skips "
            "them until they are trusted again. " if modified else "Codex does not run a hook until you trust it. "
        )
        lines.append(f"  {len(untrusted)} of {len(hooks)} hooks are not trusted, so they do not run. {reason}{HOOK_FIX}")
    if disabled:
        lines.append("  Disabled hooks do not run; review them in Codex with /hooks.")
    if hooks_only:
        lines.append("  " + hooks_only_warning())
    ok = bool(hooks) and has_guard and not untrusted and not disabled and not errors and not hooks_only
    return ok, lines


def hook_lines_for_report(codex, hooks_only: bool) -> list:
    """The hook section of a dry run or an apply, where it informs but never decides the exit code."""
    try:
        return hook_report(codex, hooks_only)[1]
    except InstallerError as error:
        return [f"Plugin hooks: Codex could not list them ({error})."]


# --------------------------------------------------------------------------------------------------
# Writing


def apply_config_edits(codex, context: Context, plan: Plan, state: State, stamp: str, backups: list) -> None:
    """Write the edits through Codex, read them back, and restore the old file if they did not land."""
    if not plan.edits:
        return
    existed = context.config_path.exists()
    before = context.config_path.read_bytes() if existed else None
    if existed:
        make_backup(context.config_path, stamp, backups)
    try:
        codex.write_config(plan.edits, state.version)
    except CodexRequestError as error:
        if error.data.get("config_write_error_code") == "configVersionConflict":
            raise InstallerError(
                f"{context.config_path} changed while the installer was running (probably Codex itself). "
                "Run the command again."
            )
        raise
    problems = []
    try:
        after = codex.read_config()
    except InstallerError as error:
        problems.append(f"Codex cannot load the file any more ({error})")
        after = None
    if after is not None:
        user = after.get("user") or {}
        for dotted, expected in plan.checks:
            have = lookup_filter(user, dotted) if dotted.startswith(FILTERS_TABLE + ".") else get_path(user, dotted)
            if expected is MISSING and have is not MISSING:
                problems.append(f"{dotted} is still set")
            elif expected is not MISSING and (have is MISSING or not same(have, expected)):
                problems.append(f"{dotted} reads back as {show(None if have is MISSING else have)}")
    if problems:
        if before is not None:
            write_atomic(context.config_path, before)
        elif context.config_path.exists():
            context.config_path.unlink()
        raise InstallerError(
            f"Codex did not store the changes to {context.config_path} as expected ({'; '.join(problems)}). "
            "The file was put back as it was."
        )


def lookup_filter(user: dict, dotted: str) -> object:
    """A filter value by case-insensitive name, as Codex matches it."""
    name = dotted[len(FILTERS_TABLE) + 1:].lower()
    filters = get_path(user, FILTERS_TABLE)
    if not isinstance(filters, dict):
        return MISSING
    for key, value in filters.items():
        if str(key).lower() == name:
            return value
    return MISSING


def apply_rules(context: Context, plan: Plan, stamp: str, backups: list) -> None:
    target = context.rules_target
    if plan.rules_action in ("copy", "refresh"):
        if plan.rules_backup and target.exists():
            make_backup(target, stamp, backups)
        write_atomic(target, context.source_bytes)
    elif plan.rules_action == "delete":
        if plan.rules_backup:
            make_backup(target, stamp, backups)
        os.unlink(os.path.realpath(str(target)))
        rules_dir = target.parent
        if rules_dir.is_dir() and not any(rules_dir.iterdir()):
            rules_dir.rmdir()


def apply_agents(context: Context, plan: Plan, state: State, stamp: str, backups: list) -> None:
    for name, text in plan.agents_writes:
        path = context.codex_home / name
        if state.agents[name] is not None and text is not None:
            make_backup(path, stamp, backups)
        if text is None:
            os.unlink(os.path.realpath(str(path)))
        else:
            write_atomic(path, text.encode("utf-8"))


# --------------------------------------------------------------------------------------------------
# Modes


def gather(codex, context: Context) -> tuple:
    """(state, requirements) read from disk and from Codex."""
    context.load_source()
    problem = codex.check_rules(RULES_SOURCE)
    if problem:
        raise InstallerError(
            f"This Codex version cannot load the plugin's rules file {RULES_SOURCE} ({problem}). Update Codex or the "
            "evisions plugin; nothing was written. (One broken rules file makes Codex ignore every user rules file.)"
        )
    requirements = codex.requirements()
    state = State(context, codex.read_config(), requirements)
    return state, requirements


def wanted_items(args: argparse.Namespace, profile: str, record: Optional[dict]) -> tuple:
    """(wanted, note about the deny-read profile)."""
    requested = bool(
        args.deny_read
        or (record and record["deny_read"])
        or (args.restore_declined and record and record["declined"]["deny_read"])
    )
    note = None
    deny = requested
    if requested and profile == MANAGED:
        deny = False
        note = "left out, because under the managed profile the administrator owns permission settings."
    elif requested and sys.platform.startswith("win"):
        deny = False
        note = "left out: it is not measured on native Windows, where Codex can refuse sandbox rules it cannot enforce."
    elif not requested and profile == STANDARD:
        note = (
            f"not requested. {PROGRAM} --apply --deny-read adds an optional sandbox profile that blocks reading "
            "credential folders and .env files."
        )
    wanted = {
        "rules": True,
        "excludes": True,
        "filters": ENV_FILTERS,
        "agents": True,
        "deny_read": deny,
        "deny_read_explicit": bool(args.deny_read),
    }
    return wanted, note


def run_install(args: argparse.Namespace, codex, dry_run: bool) -> int:
    context = Context(args)
    context.require_home()
    record = load_record(context.record_path)
    state, requirements = gather(codex, context)
    profile, reason = detect_profile(args.profile, requirements, context.requirements_file)
    hooks_only = managed_hooks_only(requirements, context.requirements_file)
    wanted, deny_note = wanted_items(args, profile, record)
    plan = plan_changes(state, record, wanted, restore_declined=args.restore_declined)
    plan.deny_note = deny_note

    created_config = bool(plan.edits) and not state.config_exists
    new_record = {
        "baseline_version": context.version,
        "profile": profile,
        "applied_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "pending": False,
        "rules_file": plan.record["rules_file"],
        "ignore_default_excludes": plan.record["ignore_default_excludes"],
        "filters": plan.record["filters"],
        "created_tables": plan.record["created_tables"],
        "agents": plan.record["agents"],
        "deny_read": plan.record["deny_read"],
        "declined": plan.record["declined"],
        # When the installer created config.toml, --remove deletes it again once nothing else is in it.
        "created_config_file": bool(record and record["created_config_file"]) or created_config,
    }
    record_changed = not same(record_without_time(record), record_without_time(new_record))
    files_changed = bool(plan.edits or plan.rules_action or plan.agents_writes)
    changed = files_changed or record_changed

    header = "evisions Codex settings installer" + (" (dry run: nothing is written)" if dry_run else "")
    lines = [header, "", f"Codex home: {context.codex_home}", f"Config file: {context.config_path}"]
    if getattr(codex, "version", None):
        lines.append(f"Codex version: {codex.version}")
    lines.append(f"Profile: {profile} ({reason})")
    lines.append(f"Baseline version: {context.version}")
    if record and record["pending"]:
        lines.append("Note: the previous --apply stopped before it finished; " + ("--apply completes it." if dry_run else "this run completes it."))
    if hooks_only:
        lines.append(hooks_only_warning())
    if args.restore_declined:
        lines.append("--restore-declined: baseline items you removed earlier are added again.")
    lines.append("")
    body = describe(plan, context, done=not dry_run and changed)
    if changed and not files_changed:
        body.append("Your files stay as they are; only the install record " + ("will be updated." if dry_run else "was updated."))

    if not changed:
        apart = " apart from the items you removed" if declined_lines(plan.declined) else ""
        print_lines(lines + body + ["", f"Nothing to change: your Codex settings already match the evisions baseline{apart} "
                                    f"(version {context.version}, profile {profile}).", ""]
                    + hook_lines_for_report(codex, hooks_only))
        return 0

    if dry_run:
        hook_lines = hook_lines_for_report(codex, hooks_only)
        saved = ([RULES_RELATIVE] if plan.rules_backup and state.rules_bytes is not None else []) + (
            [CONFIG_FILE] if plan.edits and state.config_exists else []
        ) + [name for name, text in plan.agents_writes if text is not None and state.agents[name] is not None]
        if saved:
            body.append(
                "Backup: before writing, a copy of each file that changes would be saved next to it "
                f"(<name>.bak-evisions-<time>): {', '.join(saved)}."
            )
        if plan.edits and not state.config_exists:
            body.append(f"{context.config_path} does not exist yet; --apply would create it.")
        command = f"{PROGRAM} --apply" + (" --deny-read" if args.deny_read else "") + (" --restore-declined" if args.restore_declined else "")
        print_lines(lines + body + ["", f'Nothing was written. Run "{command}" to make these changes.', ""] + hook_lines)
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list = []
    # Two phases: a pending record naming everything that may land goes first, the final record last.
    try:
        write_atomic(context.record_path, dump_json(pending_record(record, new_record)).encode("utf-8"))
        apply_rules(context, plan, stamp, backups)
        apply_config_edits(codex, context, plan, state, stamp, backups)
        apply_agents(context, plan, state, stamp, backups)
        write_atomic(context.record_path, dump_json(new_record).encode("utf-8"))
    except (OSError, InstallerError) as error:
        if isinstance(error, OSError):
            reason_text = f"Could not write {error.filename or 'a file'}: {error.strerror or error}."
        else:
            reason_text = str(error)
        raise InstallerError(
            f"{reason_text} Nothing is lost: once that is fixed, {PROGRAM} --apply finishes what this run started, "
            f"and {PROGRAM} --remove undoes whatever it did write."
        )

    footer = [""]
    for backup in backups:
        footer.append(f"Backup of the previous file: {backup}")
    footer.append(f'Done. Restart Codex to load the changes. "{PROGRAM} --remove" undoes them; "{PROGRAM} --check" verifies them.')
    print_lines(lines + body + footer + [""] + hook_lines_for_report(codex, hooks_only))
    return 0


def run_remove(args: argparse.Namespace, codex) -> int:
    context = Context(args)
    record = load_record(context.record_path) if context.codex_home.is_dir() else None
    if record is None:
        print(f"Nothing to undo: there is no record of an earlier {PROGRAM} --apply in {context.record_dir}.")
        return 0
    context.load_source()
    state = State(context, codex.read_config(), None)
    nothing = {"rules": False, "excludes": False, "filters": (), "agents": False, "deny_read": False, "deny_read_explicit": False}
    plan = plan_changes(state, record, nothing, restore_declined=True)

    lines = ["evisions Codex settings installer: removing what earlier installs added", "", f"Codex home: {context.codex_home}", ""]
    body = describe(plan, context, done=True)
    if plan.excludes is None and record["ignore_default_excludes"] and get_path(state.user, EXCLUDES_KEY) is not MISSING:
        body.append("Left alone: shell_environment_policy.ignore_default_excludes, because you changed it after the install.")
    if not (plan.edits or plan.rules_action or plan.agents_writes):
        body.append("Nothing of the baseline was left in your Codex settings.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list = []
    try:
        apply_rules(context, plan, stamp, backups)
        apply_config_edits(codex, context, plan, state, stamp, backups)
        apply_agents(context, plan, state, stamp, backups)
        if record["created_config_file"] and context.config_path.exists():
            after = codex.read_config().get("user") or {}
            text = context.config_path.read_text(encoding="utf-8", errors="replace")
            if is_empty_table(after) and not re.sub(r"(?m)^\s*\[[^\]]*\]\s*$", "", text).strip():
                context.config_path.unlink()
                body.append(f"Deleted {context.config_path}: the installer created it, and nothing else is in it.")
        context.record_path.unlink()
        if context.record_dir.is_dir() and not any(context.record_dir.iterdir()):
            context.record_dir.rmdir()
    except OSError as error:
        raise InstallerError(f"Could not write {error.filename or 'a file'}: {error.strerror or error}.")

    footer = [""]
    for backup in backups:
        footer.append(f"Backup of the previous file: {backup}")
    footer.append("Done. Restart Codex to load the changed settings.")
    print_lines(lines + body + footer)
    return 0


def run_check(args: argparse.Namespace, codex) -> int:
    context = Context(args)
    record = load_record(context.record_path) if context.codex_home.is_dir() else None
    if record is None:
        print(f"The evisions Codex baseline is not installed: there is no record at {context.record_path}. Run {PROGRAM} --apply.")
        return 1
    if record["pending"]:
        print(f"The last {PROGRAM} --apply stopped before it finished. Run {PROGRAM} --apply to complete it.")
        return 1
    context.load_source()
    missing, notes = [], []

    if record["rules_file"]:
        target = context.rules_target
        if not target.is_file():
            missing.append(f"command rules file {target} (missing)")
        else:
            data = target.read_bytes()
            if sha256(data) != record["rules_file"]["sha256"]:
                missing.append(f"command rules file {target} (changed since the install)")
            problem = codex.check_rules(target)
            if problem:
                missing.append(f"command rules file {target}: Codex cannot load it ({problem})")
        if record["rules_file"]["sha256"] != sha256(context.source_bytes):
            notes.append(f"The plugin has newer command rules; run {PROGRAM} --apply to install them.")

    requirements = codex.requirements()
    user = codex.read_config().get("user") or {}
    if record["ignore_default_excludes"] and get_path(user, EXCLUDES_KEY) is not False:
        missing.append("shell_environment_policy.ignore_default_excludes = false")
    for pattern in record["filters"]:
        if lookup_filter(user, f"{FILTERS_TABLE}.{pattern}") != "exclude":
            missing.append(f"environment filter {pattern}")
    if record["deny_read"]:
        if user.get("default_permissions") != PROFILE_NAME:
            missing.append(f'default_permissions = "{PROFILE_NAME}"')
        if not same(get_path(user, f"permissions.{PROFILE_NAME}"), deny_read_table()):
            missing.append(f"permission profile {PROFILE_NAME} (missing or changed)")
    if record["agents"]:
        name = record["agents"]["file"]
        text = read_text(context.codex_home / name)
        status, span = find_block(text)
        if status != "one" or text[span[0]:span[1]] != block_text(newline_of(text)):
            missing.append(f"the instructions block in {context.codex_home / name} (missing or changed)")
        override = read_text(context.codex_home / OVERRIDE_FILE)
        if name == AGENTS_FILE and (override or "").strip("\ufeff \t\r\n"):
            missing.append(f"the instructions block is in {AGENTS_FILE}, but Codex now reads {OVERRIDE_FILE} instead")

    hooks_only = managed_hooks_only(requirements, context.requirements_file)
    hooks_ok, hook_lines = hook_report(codex, hooks_only)
    version = record.get("baseline_version") or "unknown"
    profile = record.get("profile") or "unknown"
    note = declined_lines(record["declined"])
    if missing:
        print(f"The evisions Codex baseline (version {version}, profile {profile}) is incomplete. Missing or changed:")
        for entry in missing:
            print(f"  {entry}")
        print(
            f"If you removed them on purpose, run {PROGRAM} --apply once: it records them as your choice and later "
            f"updates leave them out. {PROGRAM} --apply --restore-declined puts them back instead."
        )
    else:
        print(f"Settings: the evisions Codex baseline is in place (version {version}, profile {profile}).")
    if note:
        print_lines(note)
    print_lines(notes + hook_lines)
    if context.version != version:
        print(f"The plugin is now version {context.version}; run {PROGRAM} --apply to bring the settings up to date.")
    if missing or not hooks_ok:
        return 1
    print("baseline installed and the plugin's hooks are trusted")
    return 0


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        allow_abbrev=False,
        description=(
            "Add the evisions safety baseline to your Codex CLI settings: command rules, environment filters, "
            "a global instructions block and, on request, a deny-read sandbox profile. Without a flag it only "
            "shows what would change."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="make the changes (every changed file is backed up first)")
    mode.add_argument("--remove", action="store_true", help="undo what earlier --apply runs added")
    mode.add_argument(
        "--check", action="store_true",
        help="exit 0 when the baseline is in place and the plugin's hooks are installed and trusted",
    )
    parser.add_argument(
        "--deny-read", action="store_true",
        help="also add the deny-read sandbox profile (standard profile only; measured on Linux only)",
    )
    parser.add_argument(
        "--restore-declined", action="store_true",
        help="with --apply: add back the baseline items you removed (without --apply: show what that would do)",
    )
    parser.add_argument("--profile", choices=(STANDARD, MANAGED), help="override the detected profile")
    parser.add_argument("--codex-home", help="Codex home directory (default: $CODEX_HOME or ~/.codex)")
    parser.add_argument("--codex-bin", help="path of the codex program (default: codex on PATH)")
    parser.add_argument("--requirements-file", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (args.restore_declined or args.deny_read) and (args.remove or args.check):
        parser.error("--restore-declined and --deny-read work only with --apply, or alone as a dry run")
    return args


def main(argv: Optional[list] = None, codex_factory=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    factory = codex_factory or AppServerCodex
    codex = factory(args.codex_bin, resolve_codex_home(args.codex_home))
    try:
        if args.remove:
            return run_remove(args, codex)
        if args.check:
            return run_check(args, codex)
        return run_install(args, codex, dry_run=not args.apply)
    except InstallerError as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1
    finally:
        codex.close()


if __name__ == "__main__":
    sys.exit(main())
