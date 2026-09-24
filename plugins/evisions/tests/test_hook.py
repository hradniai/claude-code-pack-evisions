"""Tests for the SessionStart hook that injects the documentation standard and the kit map.

Run from the plugin root: python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "hooks" / "session-start"
HOOKS_JSON = PLUGIN_ROOT / "hooks" / "hooks.json"
STANDARD = "context/documentation-standard.md"
KIT_MAP = "context/kit-map.md"

# Claude Code replaces additionalContext longer than this with a file path and a 2,000-character
# preview (hooks reference, "Add context for Claude"), so the injected text must stay below it.
ADDITIONAL_CONTEXT_CAP = 10_000

NOTE = "SESSION DIRECTORY:"
HOME_NOTE = "SESSION DIRECTORY: this session started in the user's home directory"
ROOT_NOTE = "SESSION DIRECTORY: this session started in a filesystem root"


def run_hook(plugin_root=PLUGIN_ROOT, stdin="", cwd=None, home=None, userprofile=None):
    """Run the hook the way Claude Code does: JSON on stdin, PWD set to the session directory."""
    cwd = Path(cwd) if cwd else plugin_root
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(plugin_root), PWD=str(cwd))
    env.pop("USERPROFILE", None)
    if home is not None:
        env["HOME"] = str(home)
    if userprofile is not None:
        env["USERPROFILE"] = userprofile
    return subprocess.run(
        [str(Path(plugin_root) / "hooks" / "session-start")],
        input=stdin,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse_context(test: unittest.TestCase, result: subprocess.CompletedProcess) -> str:
    """Assert the exact output shape and return the additionalContext string."""
    test.assertEqual(result.returncode, 0, result.stderr)
    test.assertEqual(result.stderr, "")
    payload = json.loads(result.stdout)
    test.assertEqual(set(payload), {"hookSpecificOutput"})
    inner = payload["hookSpecificOutput"]
    test.assertEqual(set(inner), {"hookEventName", "additionalContext"})
    test.assertEqual(inner["hookEventName"], "SessionStart")
    test.assertIsInstance(inner["additionalContext"], str)
    return inner["additionalContext"]


def session_input(cwd: str) -> str:
    return json.dumps({"session_id": "test", "cwd": cwd, "hook_event_name": "SessionStart", "source": "startup"})


@unittest.skipUnless(shutil.which("bash"), "bash is required to run the hook")
class SessionStartHookTest(unittest.TestCase):
    def test_output_is_valid_json_with_both_context_files(self):
        context = parse_context(self, run_hook())
        self.assertTrue(context.startswith("<evisions_kit>"))
        self.assertTrue(context.rstrip().endswith("</evisions_kit>"))
        for marker in (
            "<documentation_standard>",
            "# Documentation standard",
            "</documentation_standard>",
            "<kit_map>",
            "# Kit map",
            "</kit_map>",
        ):
            self.assertIn(marker, context)

    def test_context_files_are_injected_verbatim(self):
        context = parse_context(self, run_hook())
        for name in (STANDARD, KIT_MAP):
            text = (PLUGIN_ROOT / name).read_text(encoding="utf-8").rstrip("\n")
            self.assertIn(text, context, name)

    def test_context_stays_under_the_additional_context_cap(self):
        with tempfile.TemporaryDirectory() as home:
            # The home case adds the session-directory note, so it is the longest output.
            for result in (run_hook(), run_hook(stdin=session_input(home), home=home)):
                context = parse_context(self, result)
                self.assertLess(len(context), ADDITIONAL_CONTEXT_CAP)

    def test_missing_context_file_still_exits_zero_with_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "evisions"
            shutil.copytree(PLUGIN_ROOT, copy)
            (copy / KIT_MAP).unlink()
            context = parse_context(self, run_hook(copy))
            self.assertIn(f"{KIT_MAP} is missing", context)
            self.assertIn("# Documentation standard", context)
            self.assertNotIn("<kit_map>", context)

    def test_both_context_files_missing_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "evisions"
            shutil.copytree(PLUGIN_ROOT, copy)
            shutil.rmtree(copy / "context")
            context = parse_context(self, run_hook(copy))
            self.assertIn(f"{STANDARD} is missing", context)
            self.assertIn(f"{KIT_MAP} is missing", context)

    def test_special_characters_survive_json_escaping(self):
        tricky = 'back\\slash "quoted" tab\there cr\rhere \\n literal, čeština – žluťoučký kůň'
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "evisions"
            shutil.copytree(PLUGIN_ROOT, copy)
            (copy / KIT_MAP).write_text(tricky + "\n", encoding="utf-8", newline="")
            context = parse_context(self, run_hook(copy))
            self.assertIn(tricky, context)


@unittest.skipUnless(shutil.which("bash"), "bash is required to run the hook")
class SessionDirectoryNoteTest(unittest.TestCase):
    """The model is not told $HOME, so the hook flags a session started in home or a filesystem root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.project = self.home / "project"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def context(self, **kwargs) -> str:
        kwargs.setdefault("home", self.home)
        return parse_context(self, run_hook(**kwargs))

    def test_home_from_stdin_cwd(self):
        context = self.context(stdin=session_input(str(self.home)))
        self.assertIn(HOME_NOTE, context)
        self.assertIn(f"({self.home})", context)

    def test_project_directory_gets_no_note(self):
        self.assertNotIn(NOTE, self.context(stdin=session_input(str(self.project)), cwd=self.project))

    def test_home_with_trailing_separator(self):
        self.assertIn(HOME_NOTE, self.context(stdin=session_input(str(self.home)), home=f"{self.home}/"))

    def test_filesystem_root(self):
        self.assertIn(ROOT_NOTE, self.context(stdin=session_input("/")))

    def test_windows_drive_root(self):
        self.assertIn(ROOT_NOTE, self.context(stdin=session_input("C:\\")))

    def test_windows_home_via_userprofile(self):
        context = self.context(
            stdin=session_input("C:\\Users\\jana"), home="/c/Users/jana", userprofile="C:\\Users\\jana"
        )
        self.assertIn(HOME_NOTE, context)

    def test_pretty_printed_input(self):
        stdin = json.dumps({"source": "startup", "cwd": str(self.home)}, indent=2, separators=(",", " : "))
        self.assertIn(HOME_NOTE, self.context(stdin=stdin))

    def test_escaped_quotes_and_cwd_inside_another_value(self):
        odd_home = str(self.home) + '/we"ird'
        stdin = json.dumps({"transcript_path": '/a/"cwd"/b', "cwd": odd_home})
        self.assertIn(HOME_NOTE, self.context(stdin=stdin, home=odd_home))

    def test_stdin_cwd_wins_over_pwd(self):
        context = self.context(stdin=session_input(str(self.project)), cwd=self.home)
        self.assertNotIn(NOTE, context)

    def test_empty_stdin_falls_back_to_pwd(self):
        self.assertIn(HOME_NOTE, self.context(stdin="", cwd=self.home))
        self.assertNotIn(NOTE, self.context(stdin="", cwd=self.project))

    def test_stdin_without_cwd_falls_back_to_pwd(self):
        self.assertIn(HOME_NOTE, self.context(stdin='{"source":"startup"}', cwd=self.home))

    def test_malformed_stdin_falls_back_to_pwd(self):
        self.assertIn(HOME_NOTE, self.context(stdin='{"cwd": 42, "cwd', cwd=self.home))


class HooksJsonTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    def test_session_start_entry_shape(self):
        self.assertEqual(set(self.config), {"hooks"})
        entries = self.config["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|clear|compact")
        handlers = entries[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]["type"], "command")

    def test_command_points_at_an_existing_executable(self):
        command = self.config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
        path = Path(command.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT)).strip('"'))
        self.assertEqual(path, HOOK)
        self.assertTrue(path.is_file())
        self.assertTrue(os.access(path, os.X_OK), f"{path} is not executable")

    def test_hook_script_has_bash_shebang_and_lf_endings(self):
        raw = HOOK.read_bytes()
        self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r", raw)


if __name__ == "__main__":
    unittest.main()
