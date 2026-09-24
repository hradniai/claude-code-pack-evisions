"""Tests for the status line script (scripts/statusline.py).

Run from this directory: python3 -m unittest test_statusline -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN_ROOT / "scripts" / "statusline.py"
ANSI = re.compile(r"\033\[[0-9;]*m")


def sample(project_dir="/nonexistent/evisions-demo", now=None):
    now = now or time.time()
    return {
        "model": {"display_name": "Opus"},
        "effort": {"level": "high"},
        "output_style": {"name": "explanatory"},
        "agent": {"name": "research-analyst"},
        "workspace": {"project_dir": project_dir, "current_dir": project_dir},
        "cost": {"total_cost_usd": 1.234},
        "worktree": {"name": "feature-x", "branch": "feature-x-branch"},
        "context_window": {
            "total_input_tokens": 1_250_000,
            "total_output_tokens": 45_600,
            "context_window_size": 200_000,
            "used_percentage": 42.4,
        },
        "rate_limits": {
            # 30 spare seconds so the countdown text cannot drop a unit while the test runs.
            "five_hour": {"used_percentage": 85, "resets_at": int(now) + 2 * 3600 + 5 * 60 + 30},
            "seven_day": {"used_percentage": 90, "resets_at": int(now) + 3 * 86400 + 3600 + 30},
        },
    }


def run(stdin, columns="120"):
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    if columns is not None:
        env["COLUMNS"] = columns
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=stdin.encode("utf-8"), capture_output=True, env=env, timeout=30
    )


def plain(output):
    return ANSI.sub("", output.decode("utf-8"))


class StatuslineTest(unittest.TestCase):
    def test_full_sample_gives_three_lines(self):
        result = run(json.dumps(sample()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        lines = plain(result.stdout).splitlines()
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("Opus", lines[0])
        self.assertIn("effort:high", lines[0])
        self.assertIn("explanatory", lines[0])
        self.assertIn("▶ research-analyst", lines[0])
        self.assertIn("1.2M↑ 46k↓", lines[0])
        self.assertIn("$1.23", lines[0])
        self.assertIn("evisions-demo", lines[1])
        self.assertIn("[wt:feature-x]", lines[1])
        self.assertIn("⎇ feature-x-branch", lines[1], "falls back to worktree.branch without a git repo")
        self.assertIn("ctx: 85k/200k (42%)", lines[1])
        self.assertIn("5h: 85%", lines[2])
        self.assertIn("reset in 2h", lines[2])
        self.assertIn("7d: 90%", lines[2])
        self.assertIn("reset in 3d 1h", lines[2])

    def test_lines_are_padded_to_the_width(self):
        lines = plain(run(json.dumps(sample()), columns="120").stdout).splitlines()
        for line in lines:
            self.assertEqual(len(line), 120, line)

    def test_context_colors_follow_thresholds(self):
        for percent, color in ((10, "\033[32m"), (60, "\033[33m"), (85, "\033[31m")):
            data = {"model": {"display_name": "M"}, "context_window": {"used_percentage": percent, "context_window_size": 1000}}
            output = run(json.dumps(data)).stdout.decode("utf-8")
            self.assertIn(f"ctx: {color}", output)

    def test_seven_day_hidden_when_on_pace(self):
        now = time.time()
        data = sample(now=now)
        data["rate_limits"]["seven_day"] = {"used_percentage": 10, "resets_at": int(now + 3 * 86400)}
        self.assertNotIn("7d:", plain(run(json.dumps(data)).stdout))

    def test_empty_object(self):
        result = run("{}")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertIn("default", plain(result.stdout))

    def test_garbage_input(self):
        for stdin in ("not json at all", "[1, 2, 3]", "", '{"model": "just a string"}', "\x00\xff"):
            with self.subTest(stdin=stdin):
                result = run(stdin)
                self.assertEqual(result.returncode, 0)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_render_failure_degrades_and_reports_on_stderr(self):
        program = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('statusline', {str(SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "def boom(*args):\n"
            "    raise RuntimeError('boom')\n"
            "module.render = boom\n"
            "module.main()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], input=json.dumps(sample()).encode("utf-8"), capture_output=True, timeout=30
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(plain(result.stdout), "Opus")
        self.assertEqual(result.stderr.decode("utf-8"), "statusline: rendering: RuntimeError: boom\n")

    def test_bad_input_is_reported_on_stderr(self):
        result = run("not json at all")
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"statusline: reading the status line JSON: JSONDecodeError", result.stderr)

    def test_wrong_types_degrade(self):
        data = {
            "model": {"display_name": ["x"]},
            "cost": {"total_cost_usd": "abc"},
            "context_window": {"used_percentage": "n/a", "context_window_size": {}},
            "rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": "not a time"}, "seven_day": 7},
            "workspace": {"project_dir": 42},
        }
        result = run(json.dumps(data))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertIn("5h: 50%", plain(result.stdout))

    def test_iso_reset_time(self):
        data = {"model": {"display_name": "M"}, "rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": "2999-01-01T00:00:00Z"}}}
        self.assertIn("reset in", plain(run(json.dumps(data)).stdout))

    def test_narrow_width(self):
        result = run(json.dumps(sample()), columns="20")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        lines = plain(result.stdout).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Opus", lines[0])

    def test_without_columns_variable(self):
        result = run(json.dumps(sample()), columns=None)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(plain(result.stdout).splitlines()), 3)

    @unittest.skipUnless(shutil.which("git"), "git is required for the branch test")
    def test_branch_comes_from_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
            subprocess.run(["git", "-C", tmp, "symbolic-ref", "HEAD", "refs/heads/from-git"], check=True, capture_output=True)
            lines = plain(run(json.dumps(sample(project_dir=tmp))).stdout).splitlines()
            self.assertIn("⎇ from-git", lines[1])


if __name__ == "__main__":
    unittest.main()
