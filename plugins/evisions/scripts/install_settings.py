#!/usr/bin/env python3
"""Merge the evisions settings baseline into the user's Claude Code settings.json, check it, undo it.

A plugin cannot ship permission rules, the bypass-mode lock, a status line, env variables or other
settings keys: a plugin's own settings.json honours only `agent` and `subagentStatusLine`. This script
merges those keys from settings/baseline.json into the user's settings file instead
($CLAUDE_CONFIG_DIR/settings.json, else ~/.claude/settings.json). bin/evisions-settings runs it.

Modes:
  (no flag)   dry run: print what --apply would change and write nothing
  --apply     merge the baseline; back the file up first; write atomically
  --apply --restore-declined
              the same, and also bring back baseline items the user removed
  --remove    undo exactly what earlier applies added, using the record
  --check     exit 0 when everything the record lists is still in place, else exit 1

Profiles: `standard` merges permission rules, the bypass-mode lock and the env variables. `managed`
(an administrator policy file exists) leaves permissions, bypass mode and the environment to the
administrator and merges only the plain settings and the status line. Scalars and env variables are written only when the
user has no value, and the user's own list entries are never touched or removed.

The record at <config-dir>/evisions/settings-applied.json lists exactly what this script added, so
--remove and upgrades take back only that. Its path is a contract: the plugin's SessionStart hook
tests for it. An item an earlier apply added that is now missing from the user's file was removed on
purpose: it moves to the record's `declined` key and later applies leave it out until
--restore-declined.

Python 3.9+, standard library only. Expected problems end with a plain sentence and exit 1, never a
traceback. The version guard below the imports runs before any other code. The file deliberately
avoids syntax newer than Python 3.6 (no `from __future__ import annotations`, no `X | None`), so an
older interpreter reaches the guard instead of stopping at a SyntaxError.
"""

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

if sys.version_info < (3, 9):
    sys.stderr.write(
        "evisions-settings needs Python 3.9 or newer, and this is Python %d.%d. Install a newer Python "
        "from https://www.python.org/downloads/ and run it again; nothing was written.\n" % sys.version_info[:2]
    )
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
DEFAULT_BASELINE = PLUGIN_ROOT / "settings" / "baseline.json"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
STATUSLINE_SOURCE = SCRIPT_DIR / "statusline.py"

PROGRAM = "evisions-settings"
RECORD_DIR = "evisions"
RECORD_FILE = "settings-applied.json"
STATUSLINE_FILE = "statusline.py"
BACKUP_INFIX = ".bak-evisions-"

STANDARD = "standard"
MANAGED = "managed"
LISTS = ("allow", "deny", "ask")
LIST_KEYS = tuple(f"permissions.{name}" for name in LISTS)
BYPASS_KEY = "permissions.disableBypassPermissionsMode"
STATUSLINE_KEY = "statusLine"

# Where Claude Code looks for a file-based administrator policy, per platform.
MANAGED_DIRS = {
    "darwin": "/Library/Application Support/ClaudeCode",
    "win32": "C:\\Program Files\\ClaudeCode",
}
LINUX_MANAGED_DIR = "/etc/claude-code"

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# Older Claude Code versions skip a whole settings file that holds "attribution": false.
NEVER_WRITE = {"attribution"}
BASELINE_SECTIONS = ("permissions", "bypassLock", "settings", "env")


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# Every scalar the installer may write, with what Claude Code accepts. A value outside these makes
# Claude Code reject the whole settings file, so anything unknown is refused rather than written.
KNOWN_SETTINGS = {
    "cleanupPeriodDays": ("a whole number, 1 or more (0 fails validation)", lambda v: is_int(v) and v >= 1),
    "feedbackSurveyRate": ("a number from 0 to 1", lambda v: is_number(v) and 0 <= v <= 1),
}
KNOWN_BYPASS = {
    "disableBypassPermissionsMode": ('the string "disable"', lambda v: v == "disable"),
}
# The env variables the installer may write. A new one must be added here as well as to the baseline,
# so a stray or tampered record can never make --remove delete an unrelated variable of the user's.
KNOWN_ENV_NAMES = ("DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING")
# The only file and the only containers the installer ever creates; the record may name nothing else.
RECORD_FILES = (f"{RECORD_DIR}/{STATUSLINE_FILE}",)
CREATABLE_CONTAINERS = ("permissions",) + LIST_KEYS + ("env",)


class InstallerError(Exception):
    """An expected problem: reported as plain sentences, exit code 1, nothing written."""


# --------------------------------------------------------------------------------------------------
# Small helpers


MISSING = object()


def same(left: object, right: object) -> bool:
    """JSON equality: unlike ==, it keeps 0 apart from false and 1 apart from 1.0."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def show(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def get_path(data: dict, dotted: str) -> object:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def set_path(data: dict, dotted: str, value: object, created: list) -> None:
    """Set a dotted key, creating missing parent objects and recording each one in `created`."""
    parts = dotted.split(".")
    node = data
    for depth, part in enumerate(parts[:-1], start=1):
        if part not in node:
            node[part] = {}
            add_unique(created, ".".join(parts[:depth]))
        node = node[part]
    node[parts[-1]] = value


def delete_path(data: dict, dotted: str) -> None:
    parts = dotted.split(".")
    node: object = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def add_unique(items: list, item: object) -> None:
    if item not in items:
        items.append(item)


def normalized_rule(rule: object) -> object:
    """`Edit(*)` and `Edit` are the same rule to Claude Code; compare them as one."""
    if isinstance(rule, str) and rule.endswith("(*)"):
        return rule[:-3]
    return rule


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


def read_json(path: Path) -> object:
    """Parse a JSON file strictly (NaN and Infinity are rejected, as Claude Code rejects them)."""
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text, parse_constant=reject_constant)


def dump_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory and os.replace, so a crash never leaves a
    half-written file. A symlinked settings file is written through, so the link survives."""
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
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(target))
    except BaseException:
        # Clean up the temporary file, then let the original error through.
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def backup_path(settings_path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = settings_path.with_name(f"{settings_path.name}{BACKUP_INFIX}{stamp}")
    counter = 1
    while candidate.exists():
        candidate = settings_path.with_name(f"{settings_path.name}{BACKUP_INFIX}{stamp}-{counter}")
        counter += 1
    return candidate


def plugin_version() -> str:
    try:
        manifest = read_json(PLUGIN_MANIFEST)
    except (OSError, ValueError):
        return "unknown"
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) and version else "unknown"


def resolve_config_dir(option: Optional[str]) -> Path:
    raw = option or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
    return Path(os.path.abspath(os.path.expanduser(raw)))


def quote_for_sh(path: str) -> str:
    """Quote a path for sh/bash: double quotes normally, single quotes when it holds $, `, " or \\."""
    if not any(character in path for character in '$`"\\'):
        return f'"{path}"'
    return "'" + path.replace("'", "'\\''") + "'"


def statusline_command(script: Path) -> str:
    # Forward slashes: on Windows the command runs in Git Bash, where backslashes are escapes. The
    # chain covers macOS and Linux (python3), most Windows installs (python) and Windows with only the
    # py launcher (py -3); a missing or broken interpreter fails over to the next one.
    quoted = quote_for_sh(script.as_posix())
    return f"python3 {quoted} 2>/dev/null || python {quoted} 2>/dev/null || py -3 {quoted}"


# --------------------------------------------------------------------------------------------------
# Loading and validating inputs


def rule_problem(rule: object) -> Optional[str]:
    """Why a permission rule must not be written, or None when it is fine."""
    if not isinstance(rule, str) or not rule.strip():
        return "is not a non-empty string"
    if rule != rule.strip():
        return "has leading or trailing spaces"
    if "|" in rule:
        return "contains '|'; rules are matched per subcommand, so it would never match"
    tool, parenthesis, rest = rule.partition("(")
    if "*" in tool:
        return "has a wildcard in the tool name, which Claude Code skips at load"
    if not TOOL_NAME.match(tool):
        return "does not start with a tool name"
    if parenthesis and not rest.endswith(")"):
        return "has text after the closing parenthesis, which makes it invalid"
    if parenthesis and rest == "*)":
        return "is written with (*); write the bare tool name instead"
    return None


def visible_items(section: dict):
    """Items of a baseline section, minus the maintainer comments (keys starting with _)."""
    return [(key, value) for key, value in section.items() if not key.startswith("_")]


def load_baseline(path: Path) -> dict:
    """The validated baseline as {"lists": {...}, "bypass": {...}, "settings": {...}, "env": {...}}."""
    try:
        raw = read_json(path)
    except FileNotFoundError:
        raise InstallerError(f"The baseline file {path} is missing. Reinstall the evisions plugin.")
    except (OSError, ValueError) as error:
        raise InstallerError(f"The baseline file {path} cannot be read ({error}). Reinstall the evisions plugin.")
    problems = []
    if not isinstance(raw, dict):
        raise InstallerError(f"The baseline file {path} is not a JSON object. Reinstall the evisions plugin.")
    sections = {}
    for key, value in visible_items(raw):
        if key not in BASELINE_SECTIONS:
            problems.append(f'unknown section "{key}"')
        elif not isinstance(value, dict):
            problems.append(f'section "{key}" is not an object')
        else:
            sections[key] = value

    lists = {name: [] for name in LISTS}
    for key, value in visible_items(sections.get("permissions", {})):
        if key not in LISTS:
            problems.append(f'permissions.{key} is not one of allow, deny, ask')
            continue
        if not isinstance(value, list):
            problems.append(f"permissions.{key} is not a list")
            continue
        for rule in value:
            problem = rule_problem(rule)
            if problem:
                problems.append(f"permissions.{key} rule {show(rule)} {problem}")
            elif rule in lists[key]:
                problems.append(f"permissions.{key} lists {show(rule)} twice")
            else:
                lists[key].append(rule)
    overlap = {normalized_rule(rule) for rule in lists["allow"]} & {
        normalized_rule(rule) for rule in lists["deny"] + lists["ask"]
    }
    for rule in sorted(overlap, key=str):
        problems.append(f"{show(rule)} is both allowed and denied or asked")

    checked = {}
    for section, known in (("bypassLock", KNOWN_BYPASS), ("settings", KNOWN_SETTINGS)):
        checked[section] = {}
        for key, value in visible_items(sections.get(section, {})):
            if key in NEVER_WRITE or key not in known:
                problems.append(f'{section}.{key} is not a key the installer knows how to write safely')
                continue
            expected, valid = known[key]
            if not valid(value):
                problems.append(f"{section}.{key} is {show(value)}, but it must be {expected}")
                continue
            checked[section][key] = value

    env = {}
    for key, value in visible_items(sections.get("env", {})):
        if not ENV_NAME.match(key):
            problems.append(f'env name "{key}" is not a valid variable name')
        elif key not in KNOWN_ENV_NAMES:
            problems.append(f'env.{key} is not a variable the installer knows how to write (add it to KNOWN_ENV_NAMES)')
        elif not isinstance(value, str):
            problems.append(f"env.{key} is {show(value)}, but env values must be strings")
        else:
            env[key] = value

    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise InstallerError(
            f"The baseline file {path} holds values that are not safe to write, so nothing was changed:\n{details}"
        )
    return {"lists": lists, "bypass": checked["bypassLock"], "settings": checked["settings"], "env": env}


def describe_type(value: object) -> str:
    if isinstance(value, list):
        return "a list"
    if isinstance(value, str):
        return "a text value"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if value is None:
        return "null"
    return "an object"


def load_settings(path: Path) -> tuple:
    """(data, existed). Raises InstallerError when the file cannot be merged safely."""
    if not path.exists():
        return {}, False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise InstallerError(f"{path} is not UTF-8 text. Fix or move that file first; nothing was written.")
    except OSError as error:
        raise InstallerError(f"Could not read {path}: {error.strerror or error}.")
    if not text.strip():
        return {}, True
    try:
        data = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise InstallerError(
            f"{path} is not valid JSON (line {error.lineno}, column {error.colno}: {error.msg}). "
            "Claude Code ignores a file like this as a whole. Fix it first, then run this again; nothing was written."
        )
    except ValueError as error:
        raise InstallerError(f"{path} is not valid JSON ({error}). Fix it first; nothing was written.")
    if not isinstance(data, dict):
        raise InstallerError(
            f"{path} holds {describe_type(data)} at the top level, where Claude Code expects an object "
            "({ ... }). Fix it first, then run this again; nothing was written."
        )
    problems = []
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        problems.append(f'"permissions" is {describe_type(permissions)}, where Claude Code expects an object')
    else:
        for name in LISTS:
            if name in permissions and not isinstance(permissions[name], list):
                problems.append(
                    f'"permissions.{name}" is {describe_type(permissions[name])}, where Claude Code expects a list'
                )
    if "env" in data and not isinstance(data["env"], dict):
        problems.append(f'"env" is {describe_type(data["env"])}, where Claude Code expects an object')
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise InstallerError(
            f"{path} has values Claude Code does not accept, so it probably ignores the whole file:\n{details}\n"
            "Fix them first, then run this again; nothing was written."
        )
    return data, True


def user_value_warnings(data: dict) -> list:
    """Known values in the user's own file that make Claude Code ignore the whole file."""
    warnings = []
    if "cleanupPeriodDays" in data and not KNOWN_SETTINGS["cleanupPeriodDays"][1](data["cleanupPeriodDays"]):
        warnings.append(
            f"Your file sets cleanupPeriodDays to {show(data['cleanupPeriodDays'])}. Claude Code accepts only a "
            "whole number of 1 or more and otherwise ignores the entire settings file, these rules included. "
            "Change it (3650 keeps transcripts for ten years)."
        )
    if "feedbackSurveyRate" in data and not KNOWN_SETTINGS["feedbackSurveyRate"][1](data["feedbackSurveyRate"]):
        warnings.append(
            f"Your file sets feedbackSurveyRate to {show(data['feedbackSurveyRate'])}. Claude Code accepts only a "
            "number from 0 to 1 and otherwise ignores the entire settings file. Change it."
        )
    if data.get("attribution") is False:
        warnings.append(
            'Your file sets "attribution" to false. Claude Code versions older than 2.1.281 skip a settings '
            "file that holds this value. If you use an older version anywhere, use the object form instead."
        )
    return warnings


def default_managed_dir() -> Path:
    for prefix, directory in MANAGED_DIRS.items():
        if sys.platform.startswith(prefix):
            return Path(directory)
    return Path(LINUX_MANAGED_DIR)


def detect_profile(managed_dir: Optional[str]) -> tuple:
    """(profile, reason): managed when a file-based administrator policy exists."""
    base = Path(managed_dir) if managed_dir else default_managed_dir()
    try:
        main_file = base / "managed-settings.json"
        if main_file.is_file():
            return MANAGED, f"an administrator policy was found at {main_file}"
        drop_ins = base / "managed-settings.d"
        if drop_ins.is_dir():
            found = sorted(
                entry for entry in drop_ins.iterdir()
                if entry.suffix == ".json" and not entry.name.startswith(".") and entry.is_file()
            )
            if found:
                return MANAGED, f"an administrator policy was found at {found[0]}"
    except PermissionError:
        return MANAGED, (
            f"{base} exists but cannot be read, so an administrator probably manages this machine "
            "(use --profile standard if that is wrong)"
        )
    return STANDARD, f"no administrator policy was found in {base}"


def scalar_problem(key: str, value: object) -> Optional[str]:
    """Why a dotted key and value could not have been written by the installer, or None."""
    if key == STATUSLINE_KEY:
        valid = (
            isinstance(value, dict)
            and set(value) == {"type", "command"}
            and value["type"] == "command"
            and isinstance(value["command"], str)
        )
        return None if valid else "is not a status line the installer writes"
    if key == BYPASS_KEY:
        return None if KNOWN_BYPASS["disableBypassPermissionsMode"][1](value) else "has a value the installer never writes"
    if key in KNOWN_SETTINGS:
        return None if KNOWN_SETTINGS[key][1](value) else "has a value the installer never writes"
    if key.startswith("env.") and key[len("env."):] in KNOWN_ENV_NAMES:
        return None if isinstance(value, str) else "has a value the installer never writes"
    return "is not a key the installer writes"


def rule_lists(section: object, name: str, problems: list) -> dict:
    """The three permission-list keys of a record section; every entry must be a writable rule."""
    result = {key: [] for key in LIST_KEYS}
    for key in LIST_KEYS:
        entries = section.get(key, []) if isinstance(section, dict) else []
        if not isinstance(entries, list):
            problems.append(f'"{name}.{key}" is not a list')
            continue
        for rule in entries:
            problem = rule_problem(rule)
            if problem:
                problems.append(f'"{name}.{key}" rule {show(rule)} {problem}')
            else:
                result[key].append(rule)
    return result


def load_record(path: Path) -> Optional[dict]:
    """The validated record, or None when there is none.

    The record decides what --remove deletes and what --apply treats as the user's choice, so it is
    checked strictly: every key, value, rule and file must be one the installer itself could have
    written. Anything else refuses the whole run rather than act on a damaged or edited record.
    """
    if not path.exists():
        return None
    advice = (
        f"If you did not edit that file yourself, delete it and run {PROGRAM} --apply again; the installer "
        "then treats everything already in your settings as yours. Nothing was written."
    )
    try:
        raw = read_json(path)
    except (OSError, ValueError) as error:
        raise InstallerError(f"The install record {path} cannot be read ({error}). {advice}")
    if not isinstance(raw, dict):
        raise InstallerError(f"The install record {path} is not a JSON object. {advice}")

    problems = []
    for key in ("added", "set", "declined"):
        if key in raw and not isinstance(raw[key], dict):
            problems.append(f'"{key}" is not an object')
    added_raw = raw.get("added", {})
    if isinstance(added_raw, dict):
        problems += [f'"added.{key}" is not a permission list' for key in added_raw if key not in LIST_KEYS]
    added = rule_lists(added_raw, "added", problems)

    set_raw = raw.get("set", {})
    set_values = {}
    for key, value in (set_raw.items() if isinstance(set_raw, dict) else []):
        problem = scalar_problem(key, value)
        if problem:
            problems.append(f'"set.{key}" {problem}')
        else:
            set_values[key] = value

    declined_raw = raw.get("declined", {})
    declined = rule_lists(declined_raw, "declined", problems)
    for key, value in (declined_raw.items() if isinstance(declined_raw, dict) else []):
        if key in LIST_KEYS:
            continue
        problem = scalar_problem(key, value)
        if problem:
            problems.append(f'"declined.{key}" {problem}')
        else:
            declined[key] = value

    files = raw.get("files", [])
    if not isinstance(files, list) or any(entry not in RECORD_FILES for entry in files):
        problems.append(f'"files" may only name {", ".join(RECORD_FILES)}, but holds {show(files)}')
    created = raw.get("created", [])
    if not isinstance(created, list) or any(entry not in CREATABLE_CONTAINERS for entry in created):
        problems.append(f'"created" may only name {", ".join(CREATABLE_CONTAINERS)}, but holds {show(created)}')
    for key in ("pending", "created_settings_file"):
        if key in raw and not isinstance(raw[key], bool):
            problems.append(f'"{key}" is not true or false')
    if raw.get("profile") not in (None, STANDARD, MANAGED):
        problems.append(f'"profile" is {show(raw.get("profile"))}')
    if not isinstance(raw.get("baseline_version", ""), str):
        problems.append('"baseline_version" is not text')

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
        "added": added,
        "set": set_values,
        "declined": declined,
        "files": list(files),
        "created": list(created),
        "created_settings_file": raw.get("created_settings_file", False),
    }


# --------------------------------------------------------------------------------------------------
# Planning


def desired_state(baseline: dict, profile: str, statusline_value: Optional[dict]) -> tuple:
    """(lists, scalars) this profile wants in the file; scalars are keyed by dotted path."""
    lists = {name: [] for name in LISTS}
    scalars = {}
    if profile == STANDARD:
        lists = {name: list(baseline["lists"][name]) for name in LISTS}
        for key, value in baseline["bypass"].items():
            scalars[f"permissions.{key}"] = value
    for key, value in baseline["settings"].items():
        scalars[key] = value
    # Under an administrator's policy the administrator's environment owns telemetry; DISABLE_TELEMETRY
    # would also keep Claude Code's built-in sec-default hooks module, which shields that policy, off.
    if profile == STANDARD:
        for key, value in baseline["env"].items():
            scalars[f"env.{key}"] = value
    if statusline_value is not None:
        scalars[STATUSLINE_KEY] = statusline_value
    return lists, scalars


class Plan:
    """What a merge changes, plus the new file content and the new record parts."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.added_rules = {name: [] for name in LISTS}
        self.removed_rules = {name: [] for name in LISTS}
        self.skipped_allow: list = []
        self.tightened: list = []
        self.set_keys: list = []
        self.updated_keys: list = []
        self.unset_keys: list = []
        self.kept_keys: list = []
        self.same_keys: list = []
        self.record_added = {key: [] for key in LIST_KEYS}
        self.record_set: dict = {}
        # Items an earlier apply added that the user removed: list rules by list key, scalars by
        # dotted key. Later applies leave them out until --restore-declined.
        self.declined = {key: [] for key in LIST_KEYS}
        self.newly_declined: list = []
        self.created: list = []


def plan_merge(data: dict, record: Optional[dict], lists: dict, scalars: dict, restore_declined: bool = False) -> Plan:
    """Merge the wanted lists and scalars into a copy of `data`.

    Entries and keys the record lists as ours but that are no longer wanted are taken back, which is
    how both an upgrade and --remove (nothing wanted) work. What the user had is never recorded, so it
    is never taken back. A recorded item that is still wanted but missing from the file was removed by
    the user on purpose: it becomes declined and is not added again; one that reappears in the file,
    with any value, is the user's own from then on. `restore_declined` forgets every decline, so all
    missing baseline items are added again.

    A pending record belongs to an apply that failed part way, so a recorded item missing from the
    file may simply never have been written: it is added, never declined. Declines that record carries
    were decided by that run from the file as the user left it, so they stay.
    """
    plan = Plan()
    new = copy.deepcopy(data)
    previous_added = record["added"] if record else {key: [] for key in LIST_KEYS}
    previous_set = record["set"] if record else {}
    carry_declines = record is not None and not restore_declined
    detect_declines = carry_declines and not record["pending"]
    previous_declined = record["declined"] if carry_declines else {key: [] for key in LIST_KEYS}
    plan.created = list(record["created"]) if record else []

    def current_list(name: str) -> list:
        value = get_path(new, f"permissions.{name}")
        return list(value) if isinstance(value, list) else []

    stale = {name: [rule for rule in previous_added[f"permissions.{name}"] if rule not in lists[name]] for name in LISTS}
    user_allow = {normalized_rule(rule) for rule in current_list("allow") if rule not in stale["allow"]}
    blocked = {
        normalized_rule(rule)
        for name in ("deny", "ask")
        for rule in current_list(name)
        if rule not in stale[name]
    }

    for name in LISTS:
        key = f"permissions.{name}"
        exists = get_path(new, key) is not MISSING
        current = current_list(name)
        plan.removed_rules[name] = [rule for rule in stale[name] if rule in current]
        current = [rule for rule in current if rule not in stale[name]]
        present = {normalized_rule(rule) for rule in current}
        kept = [rule for rule in previous_added[key] if rule in lists[name] and rule in current]
        declined = []
        candidates = (previous_added[key] if detect_declines else []) + previous_declined[key]
        for rule in candidates:
            if rule in lists[name] and normalized_rule(rule) not in present and rule not in declined:
                declined.append(rule)
                if rule in previous_added[key]:
                    plan.newly_declined.append((key, rule))
        plan.declined[key] = declined
        added = []
        for rule in lists[name]:
            if normalized_rule(rule) in present or rule in declined:
                continue
            if name == "allow" and normalized_rule(rule) in blocked:
                plan.skipped_allow.append(rule)
                continue
            current.append(rule)
            present.add(normalized_rule(rule))
            added.append(rule)
            if name != "allow" and normalized_rule(rule) in user_allow:
                plan.tightened.append((name, rule))
        plan.added_rules[name] = added
        if added or plan.removed_rules[name]:
            if exists or current:
                set_path(new, key, current, plan.created)
            if not exists and current:
                add_unique(plan.created, key)
        plan.record_added[key] = kept + [rule for rule in added if rule not in kept]

    for key, wanted in scalars.items():
        have = get_path(new, key)
        if have is MISSING and detect_declines and key in previous_set:
            plan.declined[key] = previous_set[key]
            plan.newly_declined.append((key, previous_set[key]))
            continue
        if have is MISSING and carry_declines and key in previous_declined and key not in LIST_KEYS:
            plan.declined[key] = previous_declined[key]
            continue
        ours = key in previous_set and have is not MISSING and same(have, previous_set[key])
        if have is MISSING:
            set_path(new, key, wanted, plan.created)
            plan.set_keys.append((key, wanted))
            plan.record_set[key] = wanted
        elif ours:
            if not same(have, wanted):
                set_path(new, key, wanted, plan.created)
                plan.updated_keys.append((key, have, wanted))
            plan.record_set[key] = wanted
        elif same(have, wanted):
            plan.same_keys.append(key)
        else:
            plan.kept_keys.append((key, have, wanted))

    for key, value in previous_set.items():
        if key in scalars:
            continue
        have = get_path(new, key)
        if have is not MISSING and same(have, value):
            delete_path(new, key)
            plan.unset_keys.append((key, value))

    # Drop the containers this script created once they are empty again (deepest first).
    for dotted in sorted(list(plan.created), key=lambda path: -path.count(".")):
        value = get_path(new, dotted)
        if value is MISSING:
            plan.created.remove(dotted)
        elif isinstance(value, (dict, list)) and not value:
            delete_path(new, dotted)
            plan.created.remove(dotted)

    plan.data = new
    return plan


def statusline_referenced(data: dict, script: Path) -> bool:
    """True when the user's current statusLine still runs the copied script."""
    value = data.get(STATUSLINE_KEY)
    command = value.get("command") if isinstance(value, dict) else None
    return isinstance(command, str) and script.as_posix() in command


def plan_files(plan: Plan, record: Optional[dict], config_dir: Path, copied_script: Path) -> tuple:
    """(file actions, recorded files). Actions are ("copy" | "refresh" | "delete", path)."""
    actions = []
    files = []
    relative_script = copied_script.relative_to(config_dir).as_posix()
    if STATUSLINE_KEY in plan.record_set:
        if not copied_script.is_file():
            actions.append(("copy", copied_script))
        elif copied_script.read_bytes() != STATUSLINE_SOURCE.read_bytes():
            actions.append(("refresh", copied_script))
        files.append(relative_script)
    for relative in record["files"] if record else []:
        if relative in files:
            continue
        target = config_dir / relative
        if not target.exists():
            continue
        if statusline_referenced(plan.data, target):
            files.append(relative)
        else:
            actions.append(("delete", target))
    return actions, files


# --------------------------------------------------------------------------------------------------
# Reporting


class Context:
    """Everything a run needs, resolved from the command line."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.config_dir = resolve_config_dir(args.config_dir)
        self.settings_path = self.config_dir / "settings.json"
        self.record_dir = self.config_dir / RECORD_DIR
        self.record_path = self.record_dir / RECORD_FILE
        self.copied_script = self.record_dir / STATUSLINE_FILE
        self.version = plugin_version()


def print_lines(lines: list) -> None:
    print("\n".join(lines))


def list_section(title: str, rules_by_list: dict) -> list:
    lines = []
    total = sum(len(rules) for rules in rules_by_list.values())
    if not total:
        return lines
    lines.append(f"{title} ({total} in total):")
    for name in LISTS:
        rules = rules_by_list[name]
        if rules:
            lines.append(f"  {name}, {plural(len(rules), 'rule')}:")
            lines.extend(f"    {rule}" for rule in rules)
    return lines


def scalar_lines(entries: list, env: bool) -> list:
    lines = []
    for key, value in entries:
        is_env = key.startswith("env.")
        if is_env != env:
            continue
        name = key[len("env."):] if is_env else key
        lines.append(f"  {name} = {show(value)}")
    return lines


def unset_line(key: str, value: object) -> str:
    if key == STATUSLINE_KEY:
        return "  statusLine (the evisions status line)"
    return f"  {key} = {show(value)}"


def describe_plan(plan: Plan, profile: str, done: bool) -> list:
    """The body of a dry run or an apply report."""
    lines = []
    lines += list_section("Permission rules " + ("added" if done else "to add"), plan.added_rules)
    lines += list_section(
        "Permission rules " + ("removed" if done else "to remove")
        + " (added by an earlier install, no longer in the baseline)",
        plan.removed_rules,
    )
    if plan.skipped_allow:
        lines.append("Not added to allow, because your own deny or ask list already covers them:")
        lines.extend(f"  {rule}" for rule in plan.skipped_allow)
    for name, rule in plan.tightened:
        lines.append(
            f"Note: your allow list has {rule}, which the baseline puts in {name}. "
            + ("Deny wins, so Claude Code will block it." if name == "deny" else "Claude Code will ask before running it.")
        )
    if profile == MANAGED:
        lines.append(
            "Permission rules, bypass mode and environment variables: the administrator's policy owns "
            "permissions and bypass mode, and the administrator's environment owns telemetry; the installer "
            "leaves them alone."
        )

    bypass_set = [value for key, value in plan.set_keys if key == BYPASS_KEY]
    if bypass_set:
        lines.append(
            f"Bypass mode: permissions.disableBypassPermissionsMode {'set' if done else 'will be set'} to "
            f"{show(bypass_set[0])}. A session started with --dangerously-skip-permissions then runs in the "
            "normal ask-first mode."
        )
    plain = [(key, value) for key, value in plan.set_keys if key not in (BYPASS_KEY, STATUSLINE_KEY)]
    settings = scalar_lines(plain, env=False)
    if settings:
        lines.append("Settings set:" if done else "Settings to set:")
        lines += settings
    env = scalar_lines(plain, env=True)
    if env:
        lines.append("Environment variables set:" if done else "Environment variables to set:")
        lines += env
    for key, old, new in plan.updated_keys:
        if key == STATUSLINE_KEY:
            lines.append(f"Status line: {'updated' if done else 'will be updated'} to run {new.get('command')}")
        else:
            lines.append(f"{key}: {'changed' if done else 'will change'} from {show(old)} to {show(new)} (it was set by an earlier install).")
    if plan.unset_keys:
        lines.append(("Removed" if done else "To remove") + " (set by an earlier install, no longer wanted):")
        lines.extend(unset_line(key, value) for key, value in plan.unset_keys)
    kept = [(key, have, wanted) for key, have, wanted in plan.kept_keys if key != STATUSLINE_KEY]
    if kept:
        lines.append("Left as you have them:")
        for key, have, wanted in kept:
            lines.append(f"  {key}: you have {show(have)}; the baseline value would be {show(wanted)}")
    same_keys = [key for key in plan.same_keys if key != STATUSLINE_KEY]
    if same_keys:
        lines.append("Already set the same way by you: " + ", ".join(same_keys))
    lines += declined_lines(plan.declined)
    return lines


def declined_lines(declined: dict) -> list:
    """The note listing baseline items the user removed, which --apply leaves out."""
    entries = [f"  {key}: {rule}" for key in LIST_KEYS for rule in declined.get(key, [])]
    for key in declined:
        if key not in LIST_KEYS:
            entries.append("  statusLine (the evisions status line)" if key == STATUSLINE_KEY else f"  {key}")
    if not entries:
        return []
    return [
        f"You removed these baseline items; --apply leaves them out ({PROGRAM} --apply --restore-declined "
        "brings them back):"
    ] + entries


def statusline_line(plan: Plan, actions: list, note: Optional[str], done: bool) -> Optional[str]:
    if note:
        return f"Status line: {note}"
    if STATUSLINE_KEY in plan.declined:
        return "Status line: left out, because you removed it."
    if any(key == STATUSLINE_KEY for key, _ in plan.set_keys):
        command = plan.record_set[STATUSLINE_KEY]["command"]
        return f"Status line: {'set' if done else 'will be set'} to run a copy of the plugin's script: {command}"
    if any(key == STATUSLINE_KEY for key, _, _ in plan.kept_keys) or STATUSLINE_KEY in plan.same_keys:
        return "Status line: left alone, because you already have one."
    if any(action == "refresh" for action, _ in actions):
        return f"Status line: script {'refreshed' if done else 'will be refreshed'} from the plugin."
    return None


def file_lines(actions: list, done: bool) -> list:
    lines = []
    for action, path in actions:
        if action == "copy":
            lines.append(f"{'Copied' if done else 'Will copy'} the status line script to {path}")
        elif action == "delete":
            lines.append(f"{'Deleted' if done else 'Will delete'} {path} (installed by an earlier install, no longer used)")
    return lines


# --------------------------------------------------------------------------------------------------
# Modes


def record_without_time(record: Optional[dict]) -> Optional[dict]:
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "applied_at"}


def pending_record(old: Optional[dict], new: dict) -> dict:
    """The record written before anything else: the union of the old and the new record, marked pending.

    If the run stops part way (a read-only or locked settings file, a full disk), the next --apply sees
    the mark and finishes the job instead of reading unwritten items as declined, and --remove still
    knows every item that may have landed, old and new.
    """
    merged = copy.deepcopy(new)
    merged["pending"] = True
    if old is None:
        return merged
    declined = new["declined"]
    for key in LIST_KEYS:
        merged["added"][key] += [
            rule for rule in old["added"][key] if rule not in merged["added"][key] and rule not in declined[key]
        ]
    for key, value in old["set"].items():
        if key not in declined:
            # The old value wins: after a failed settings write the file still holds it.
            merged["set"][key] = value
    merged["files"] += [entry for entry in old["files"] if entry not in merged["files"]]
    merged["created"] += [entry for entry in old["created"] if entry not in merged["created"]]
    merged["created_settings_file"] = new["created_settings_file"] or old["created_settings_file"]
    return merged


def run_install(args: argparse.Namespace, dry_run: bool) -> int:
    context = Context(args)
    baseline = load_baseline(Path(args.baseline) if args.baseline else DEFAULT_BASELINE)
    data, existed = load_settings(context.settings_path)
    if args.profile:
        profile, reason = args.profile, "chosen with --profile"
    else:
        profile, reason = detect_profile(args.managed_dir)
    record = load_record(context.record_path)

    statusline_value = None
    statusline_note = None
    if args.no_statusline:
        statusline_note = "skipped (--no-statusline)."
    elif not STATUSLINE_SOURCE.is_file():
        statusline_note = "skipped, because the plugin's status line script is missing."
    else:
        statusline_value = {"type": "command", "command": statusline_command(context.copied_script)}

    lists, scalars = desired_state(baseline, profile, statusline_value)
    plan = plan_merge(data, record, lists, scalars, restore_declined=args.restore_declined)
    actions, files = plan_files(plan, record, context.config_dir, context.copied_script)

    settings_changed = not same(plan.data, data)
    new_record = {
        "baseline_version": context.version,
        "profile": profile,
        "applied_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "pending": False,
        "added": plan.record_added,
        "set": plan.record_set,
        "declined": plan.declined,
        "files": files,
        "created": plan.created,
        # When the installer created settings.json, --remove deletes it again once nothing else is in it.
        "created_settings_file": bool(record and record["created_settings_file"]) or (not existed and settings_changed),
    }
    record_changed = not same(record_without_time(record), record_without_time(new_record))
    changed = settings_changed or bool(actions) or record_changed

    header = "evisions settings installer" + (" (dry run: nothing is written)" if dry_run else "")
    lines = [header, ""]
    lines.append(f"Settings file: {context.settings_path}")
    if not existed:
        lines.append("  It does not exist yet; " + ("--apply would create it." if dry_run else "it will be created."))
    lines.append(f"Profile: {profile} ({reason})")
    lines.append(f"Baseline version: {context.version}")
    if record and record["pending"]:
        lines.append(
            "Note: the previous --apply stopped before it finished; "
            + ("--apply completes it." if dry_run else "this run completes it.")
        )
    for warning in user_value_warnings(data):
        lines.append(f"Warning: {warning}")
    if args.restore_declined:
        lines.append("--restore-declined: baseline items you removed earlier are added again.")
    lines.append("")

    body = describe_plan(plan, profile, done=not dry_run and changed)
    status = statusline_line(plan, actions, statusline_note, done=not dry_run and changed)
    if status:
        body.append(status)
    body += file_lines(actions, done=not dry_run and changed)
    if changed and not settings_changed and not actions:
        body.append(
            "Your settings file stays as it is; only the install record "
            + ("will be updated." if dry_run else "was updated.")
        )

    if not changed:
        apart = " apart from the items you removed" if declined_lines(plan.declined) else ""
        print_lines(lines + body + [
            "",
            f"Nothing to change: your settings already match the evisions baseline{apart} "
            f"(version {context.version}, profile {profile}).",
        ])
        return 0

    backup = backup_path(context.settings_path) if existed and settings_changed else None
    if dry_run:
        if backup:
            body.append(f"Backup: before writing, a copy of your current file would be saved as {backup}")
        elif settings_changed:
            body.append("Backup: none needed, because there is no file yet.")
        command = f"{PROGRAM} --apply" + (" --restore-declined" if args.restore_declined else "")
        print_lines(lines + body + ["", f'Nothing was written. Run "{command}" to make these changes.'])
        return 0

    # Two phases: a pending record naming everything that may land goes first, the final record last.
    # Copies come before the settings write and deletions after it, so the settings file never points
    # at a status line script that is gone.
    try:
        write_atomic(context.record_path, dump_json(pending_record(record, new_record)))
        for action, path in actions:
            if action in ("copy", "refresh"):
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(STATUSLINE_SOURCE), str(path))
        if settings_changed:
            if backup:
                shutil.copy2(os.path.realpath(str(context.settings_path)), str(backup))
            write_atomic(context.settings_path, dump_json(plan.data))
        for action, path in actions:
            if action == "delete":
                path.unlink()
        write_atomic(context.record_path, dump_json(new_record))
    except OSError as error:
        raise InstallerError(
            f"Could not write {error.filename or 'a file'}: {error.strerror or error}. Nothing is lost: once the "
            f"file can be written, {PROGRAM} --apply finishes what this run started, and {PROGRAM} --remove "
            "undoes whatever it did write."
        )

    footer = [""]
    if backup:
        footer.append(f"Your previous settings file was saved as {backup}")
    restart = " Restart Claude Code to load the new settings." if settings_changed else ""
    footer.append(f'Done.{restart} "{PROGRAM} --remove" undoes the changes this installer made.')
    print_lines(lines + body + footer)
    return 0


def run_remove(args: argparse.Namespace) -> int:
    context = Context(args)
    data, existed = load_settings(context.settings_path)
    record = load_record(context.record_path)
    if record is None:
        print(f"Nothing to undo: there is no record of an earlier {PROGRAM} --apply in {context.record_dir}.")
        return 0

    empty_lists = {name: [] for name in LISTS}
    plan = plan_merge(data, record, empty_lists, {})
    settings_changed = not same(plan.data, data)
    # A settings file the installer created goes away again when nothing but its own items was in it.
    delete_settings = record["created_settings_file"] and existed and same(plan.data, {})
    deletions = []
    kept_files = []
    for relative in record["files"]:
        target = context.config_dir / relative
        if not target.exists():
            continue
        if statusline_referenced(plan.data, target):
            kept_files.append(target)
        else:
            deletions.append(target)

    lines = ["evisions settings installer: removing what earlier installs added", ""]
    lines.append(f"Settings file: {context.settings_path}")
    lines.append("")
    lines += list_section("Permission rules removed", plan.removed_rules)
    if plan.unset_keys:
        lines.append("Settings removed:")
        lines.extend(unset_line(key, value) for key, value in plan.unset_keys)
    changed_by_user = [key for key in record["set"] if key not in {k for k, _ in plan.unset_keys}]
    for key in changed_by_user:
        if get_path(data, key) is not MISSING:
            lines.append(f"Left alone: {key}, because you changed it after the install.")
    for target in deletions:
        lines.append(f"Deleted {target}")
    for target in kept_files:
        lines.append(f"Kept {target}, because your status line still runs it.")
    if delete_settings:
        lines.append(f"Deleted {context.settings_path}: the installer created it, and nothing else is in it.")
    if not settings_changed and not deletions and not delete_settings:
        lines.append("Nothing of the baseline was left in your settings file.")

    # No backup when the file goes: everything in it was the installer's own.
    backup = backup_path(context.settings_path) if existed and settings_changed and not delete_settings else None
    try:
        if delete_settings:
            os.unlink(os.path.realpath(str(context.settings_path)))
        elif settings_changed:
            if backup:
                shutil.copy2(os.path.realpath(str(context.settings_path)), str(backup))
            write_atomic(context.settings_path, dump_json(plan.data))
        for target in deletions:
            target.unlink()
        context.record_path.unlink()
        if context.record_dir.is_dir() and not any(context.record_dir.iterdir()):
            context.record_dir.rmdir()
    except OSError as error:
        raise InstallerError(f"Could not write {error.filename or 'a file'}: {error.strerror or error}.")

    lines.append("")
    if backup:
        lines.append(f"Your previous settings file was saved as {backup}")
    lines.append("Done. Restart Claude Code to load the changed settings.")
    print_lines(lines)
    return 0


def run_check(args: argparse.Namespace) -> int:
    context = Context(args)
    record = load_record(context.record_path)
    if record is None:
        print(f"The evisions baseline is not installed: there is no record at {context.record_path}. Run {PROGRAM} --apply.")
        return 1
    if record["pending"]:
        print(f"The last {PROGRAM} --apply stopped before it finished. Run {PROGRAM} --apply to complete it.")
        return 1
    data, _ = load_settings(context.settings_path)
    missing = []
    for key in LIST_KEYS:
        current = get_path(data, key)
        current = current if isinstance(current, list) else []
        missing += [f"{key}: {rule}" for rule in record["added"][key] if rule not in current]
    for key, value in record["set"].items():
        have = get_path(data, key)
        if have is MISSING:
            missing.append(f"{key} (not set)")
        elif not same(have, value):
            missing.append(f"{key} (now {show(have)}, installed as {show(value)})")
    for relative in record["files"]:
        if not (context.config_dir / relative).is_file():
            missing.append(f"file {context.config_dir / relative}")
    # Declined items are the user's choice, not damage; list only those still absent from the file.
    declined = {}
    for key in LIST_KEYS:
        current = get_path(data, key)
        current = current if isinstance(current, list) else []
        declined[key] = [rule for rule in record["declined"][key] if rule not in current]
    for key, value in record["declined"].items():
        if key not in LIST_KEYS and get_path(data, key) is MISSING:
            declined[key] = value
    version = record.get("baseline_version") or "unknown"
    profile = record.get("profile") or "unknown"
    note = declined_lines(declined)
    if missing:
        print(f"The evisions baseline (version {version}, profile {profile}) is incomplete. Missing or changed:")
        for entry in missing:
            print(f"  {entry}")
        print(
            f"If you removed them on purpose, run {PROGRAM} --apply once: it records them as your choice and "
            f"later updates leave them out. {PROGRAM} --apply --restore-declined puts them back instead."
        )
        if note:
            print_lines(note)
        return 1
    print(f"baseline installed (version {version}, profile {profile})")
    if note:
        print_lines(note)
    if context.version != version:
        print(f"The plugin is now version {context.version}; run {PROGRAM} --apply to bring the settings up to date.")
    return 0


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        allow_abbrev=False,
        description=(
            "Merge the evisions safety and privacy baseline into your Claude Code settings.json. "
            "Without a flag it only shows what would change."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the changes (a backup is made first)")
    mode.add_argument("--remove", action="store_true", help="undo what earlier --apply runs added")
    mode.add_argument("--check", action="store_true", help="exit 0 when the baseline is installed and intact")
    parser.add_argument(
        "--restore-declined",
        action="store_true",
        help="with --apply: add back the baseline items you removed (without --apply: show what that would do)",
    )
    parser.add_argument("--profile", choices=(STANDARD, MANAGED), help="override the detected profile")
    parser.add_argument("--no-statusline", action="store_true", help="do not set up the status line")
    parser.add_argument("--config-dir", help="Claude Code config directory (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    parser.add_argument("--managed-dir", help="directory to look in for an administrator policy")
    parser.add_argument("--baseline", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.restore_declined and (args.remove or args.check):
        parser.error("--restore-declined works only with --apply, or alone as a dry run")
    return args


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.remove:
            return run_remove(args)
        if args.check:
            return run_check(args)
        return run_install(args, dry_run=not args.apply)
    except InstallerError as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
