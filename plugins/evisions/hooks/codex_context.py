"""SessionStart and UserPromptSubmit hooks of the evisions plugin for Codex on Windows.

On macOS and Linux hooks/codex-hooks.json runs the bash scripts `hooks/safety-start codex` and
`hooks/current-time`. On Windows Codex runs hooks through cmd.exe, where bash is often missing, so the
commandWindows entries run this script through hooks/run-python.ps1 instead. It prints the same JSON
as the bash scripts (tests/test_codex_safety.py compares them), with one deliberate difference: on
Windows the tool paths are given as `bash "<path>"`, because list-env-keys and evisions-codex-settings
are bash scripts that PowerShell cannot start (they need Git Bash). The Python-missing status line of
the bash variant has no counterpart: when no Python runs, run-python.ps1 reports that itself.

Usage: codex_context.py safety-start | current-time
Always exits 0, because a context hook must never block a session or a prompt. Standard library only,
Python 3.9+.
"""

import datetime
import json
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = "context/safety-codex.md"
RECORD = os.path.join("evisions", "codex-settings-applied.json")


def on_windows():
    return os.name == "nt"


def read_context(relative):
    """The context file, or the same one-line notice as the bash hook when it is missing."""
    try:
        with open(os.path.join(PLUGIN_ROOT, relative), encoding="utf-8", newline="") as handle:
            return handle.read().rstrip("\n")
    except OSError:
        return "[evisions] %s is missing from the plugin; update or reinstall the plugin to restore it." % relative


def tool_command(name):
    """How the agent starts one of the plugin's bin/ tools on this platform."""
    path = os.path.join(PLUGIN_ROOT, "bin", name)
    if on_windows():
        return 'bash "%s"' % path.replace("\\", "/")
    return '"%s"' % path


def codex_home():
    configured = os.environ.get("CODEX_HOME")
    return configured if configured else os.path.join(os.path.expanduser("~"), ".codex")


def safety_context():
    settings_tool = tool_command("evisions-codex-settings")
    tools = (
        "TOOL PATHS: Codex does not put the plugin's bin/ folder on PATH, so run these tools by their "
        "absolute path, quoted as shown:\n"
        "- list-env-keys: %s\n"
        "- evisions-codex-settings: %s" % (tool_command("list-env-keys"), settings_tool)
    )
    if on_windows():
        tools += "\nBoth are bash scripts: on Windows they need Git Bash."
    status = ""
    if not os.path.isfile(os.path.join(codex_home(), RECORD)):
        status += (
            "\nSETTINGS BASELINE: not installed. The user can preview it with `%s` and install it with "
            "`%s --apply`. Mention this only if the user asks about safety, permissions or setup."
            % (settings_tool, settings_tool)
        )
    return "<evisions_safety>\n%s\n%s%s\n</evisions_safety>" % (read_context(PROTOCOL), tools, status)


def time_context():
    now = datetime.datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    zone = now.tzname()
    if zone:
        stamp = "%s (%s)" % (stamp, zone)
    return "Current local time: %s. Use it for timestamps in journals and other dated files." % stamp


def main(argv):
    # Read the hook input so Codex never writes into a pipe nobody reads.
    try:
        sys.stdin.buffer.read()
    except (OSError, ValueError, AttributeError):
        pass
    part = argv[1] if len(argv) > 1 else ""
    if part == "safety-start":
        event, context = "SessionStart", safety_context()
    elif part == "current-time":
        event, context = "UserPromptSubmit", time_context()
    else:
        return 0
    payload = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
