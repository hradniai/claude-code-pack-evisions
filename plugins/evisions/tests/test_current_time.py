"""Tests for the UserPromptSubmit hook that adds the current local time to every prompt.

Run from this directory with `python3 -m unittest`. Standard library only.
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "hooks" / "current-time"
TIME_LINE = re.compile(r"^Current local time: \d{4}-\d{2}-\d{2} \d{2}:\d{2} \([^)]+\)\. Use it for timestamps")


def run_hook(stdin='{"prompt": "hello"}', tz=None):
    env = dict(os.environ)
    if tz is not None:
        env["TZ"] = tz
    return subprocess.run([str(HOOK)], input=stdin, env=env, capture_output=True, text=True, timeout=30)


@unittest.skipUnless(shutil.which("bash"), "bash is required to run the hook")
class CurrentTimeTest(unittest.TestCase):
    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"hookSpecificOutput"})
        inner = payload["hookSpecificOutput"]
        self.assertEqual(set(inner), {"hookEventName", "additionalContext"})
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        return inner["additionalContext"]

    def test_output_is_valid_json_with_the_time(self):
        self.assertRegex(self.context(run_hook()), TIME_LINE)

    def test_timezone_is_named(self):
        self.assertIn("(UTC)", self.context(run_hook(tz="UTC")))

    def test_long_prompt_on_stdin_is_consumed(self):
        # Larger than a pipe buffer: the hook must read it rather than leave the writer blocked.
        self.assertRegex(self.context(run_hook(stdin=json.dumps({"prompt": "x" * 300_000}))), TIME_LINE)

    def test_script_is_executable_bash_with_lf_endings(self):
        raw = HOOK.read_bytes()
        self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == "__main__":
    unittest.main()
