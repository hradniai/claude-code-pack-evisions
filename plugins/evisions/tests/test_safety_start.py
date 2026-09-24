"""Tests for the SessionStart hook that injects the safety protocol and the safety status lines.

Run from this directory with `python3 -m unittest`. Standard library only. Every run points
CLAUDE_CONFIG_DIR at a temporary directory, so the machine's real Claude Code config is never read.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = "hooks/safety-start"
PROTOCOL = "context/safety.md"
BASH = shutil.which("bash")

# Claude Code replaces additionalContext longer than this with a file path and a preview.
ADDITIONAL_CONTEXT_CAP = 10_000
# The protocol's own budget, so the status lines and future additions keep room under the cap.
PROTOCOL_BUDGET = 3_500

SAFETY_STATUS = "SAFETY STATUS: Python 3.9+ was not found."
BASELINE = "SETTINGS BASELINE: not installed."


def python_on_path():
    """True when the normal PATH holds an interpreter the hook's probe accepts."""
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            probe = subprocess.run(
                [found, "-S", "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"],
                capture_output=True,
            )
            if probe.returncode == 0:
                return True
    return False


def isolated_bin(directory, *tools):
    """A PATH directory holding only the named tools, so no Python is reachable through it."""
    bin_dir = Path(directory) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if not found:
            raise unittest.SkipTest(f"{tool} is not installed")
        (bin_dir / tool).symlink_to(found)
    return bin_dir


def session_input():
    return json.dumps({"session_id": "test", "hook_event_name": "SessionStart", "source": "startup", "cwd": "/tmp"})


@unittest.skipUnless(BASH, "bash is required to run the hook")
class SafetyStartTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config"
        self.config.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, plugin_root=PLUGIN_ROOT, path=None):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.config), HOME=str(self.tmp))
        if path is not None:
            env["PATH"] = str(path)
        return subprocess.run(
            [str(Path(plugin_root) / HOOK)],
            input=session_input(),
            cwd=str(self.tmp),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def context(self, result):
        """Assert the exact output shape and return the additionalContext string."""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"hookSpecificOutput"})
        inner = payload["hookSpecificOutput"]
        self.assertEqual(set(inner), {"hookEventName", "additionalContext"})
        self.assertEqual(inner["hookEventName"], "SessionStart")
        self.assertIsInstance(inner["additionalContext"], str)
        return inner["additionalContext"]

    def no_python_path(self):
        return isolated_bin(self.tmp, "bash", "cat", "dirname")

    def test_protocol_is_injected_verbatim(self):
        context = self.context(self.run_hook())
        self.assertTrue(context.startswith("<evisions_safety>"))
        self.assertTrue(context.rstrip().endswith("</evisions_safety>"))
        self.assertIn("<safety_protocol>", context)
        protocol = (PLUGIN_ROOT / PROTOCOL).read_text(encoding="utf-8").rstrip("\n")
        self.assertIn(protocol, context)

    def test_protocol_stays_within_its_budget(self):
        self.assertLessEqual(len((PLUGIN_ROOT / PROTOCOL).read_text(encoding="utf-8")), PROTOCOL_BUDGET)

    def test_longest_output_stays_under_the_cap(self):
        # No Python and no baseline: both status lines, the longest possible output.
        context = self.context(self.run_hook(path=self.no_python_path()))
        self.assertIn(SAFETY_STATUS, context)
        self.assertIn(BASELINE, context)
        self.assertLess(len(context), ADDITIONAL_CONTEXT_CAP)

    def test_baseline_line_present_without_the_marker_file(self):
        self.assertIn(BASELINE, self.context(self.run_hook()))

    def test_baseline_line_absent_with_the_marker_file(self):
        (self.config / "evisions").mkdir()
        (self.config / "evisions" / "settings-applied.json").write_text("{}", encoding="utf-8")
        context = self.context(self.run_hook())
        self.assertNotIn("SETTINGS BASELINE", context)

    @unittest.skipUnless(python_on_path(), "no Python 3.9+ on PATH")
    def test_safety_status_absent_with_python(self):
        self.assertNotIn("SAFETY STATUS", self.context(self.run_hook()))

    def test_safety_status_present_without_python(self):
        context = self.context(self.run_hook(path=self.no_python_path()))
        self.assertIn(SAFETY_STATUS, context)
        self.assertIn("Tell the user at the start of the conversation.", context)

    def test_safety_status_present_with_only_a_store_stub(self):
        # A python3 that is found on PATH but cannot run Python must count as missing.
        bin_dir = self.no_python_path()
        stub = bin_dir / "python3"
        stub.write_text(f"#!{BASH}\necho 'Python was not found' >&2\nexit 49\n", encoding="utf-8")
        stub.chmod(0o755)
        self.assertIn(SAFETY_STATUS, self.context(self.run_hook(path=bin_dir)))

    def test_safety_status_absent_with_python_named_python(self):
        bin_dir = self.no_python_path()
        (bin_dir / "python").symlink_to(sys.executable)
        self.assertNotIn("SAFETY STATUS", self.context(self.run_hook(path=bin_dir)))

    def test_missing_protocol_file_still_exits_zero_with_notice(self):
        copy = self.tmp / "evisions"
        shutil.copytree(PLUGIN_ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / PROTOCOL).unlink()
        context = self.context(self.run_hook(plugin_root=copy))
        self.assertIn(f"{PROTOCOL} is missing", context)
        self.assertNotIn("<safety_protocol>", context)
        self.assertIn(BASELINE, context)

    def test_special_characters_survive_json_escaping(self):
        tricky = 'back\\slash "quoted" tab\there cr\rhere \\n literal, čeština – žluťoučký kůň'
        copy = self.tmp / "evisions"
        shutil.copytree(PLUGIN_ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
        # open(newline="") rather than write_text(newline=...), which needs Python 3.10.
        with open(copy / PROTOCOL, "w", encoding="utf-8", newline="") as handle:
            handle.write(tricky + "\n")
        self.assertIn(tricky, self.context(self.run_hook(plugin_root=copy)))


if __name__ == "__main__":
    unittest.main()
