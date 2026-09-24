"""Tests for bin/list-env-keys and scripts/env_key_classify.py: names and states, never values.

Run from this directory with `python3 -m unittest`. Standard library only. Every run uses a temporary
HOME, CLAUDE_CONFIG_DIR and working directory, so the machine's real env files are never touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TOOL = PLUGIN_ROOT / "bin" / "list-env-keys"
BASH = shutil.which("bash")

SECRET = "supersecretvalue123"
PLACEHOLDER = "your-key-here"
ENV_TEXT = (
    "# project secrets\n"
    f"API_KEY={SECRET}\n"
    "EMPTY_TOKEN=\n"
    f"PLACEHOLDER_SECRET={PLACEHOLDER}\n"
    "PORT=8080\n"
)
PROCESS_SECRET = "procvalue-abc-987"
CONFIG_SECRET = "cfgvalue-xyz-654"
MULTILINE_VALUE = "-----BEGIN PRIVATE KEY-----\nLEAKLINE-KEY-material-001\n-----END PRIVATE KEY-----"


def isolated_bin(directory, *tools):
    """A PATH directory holding only the named tools, so no Python is reachable through it."""
    bin_dir = Path(directory) / "isolated-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if not found:
            raise unittest.SkipTest(f"{tool} is not installed")
        (bin_dir / tool).symlink_to(found)
    return bin_dir


@unittest.skipUnless(BASH, "bash is required to run list-env-keys")
class ListEnvKeysTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.config = self.tmp / "config"
        self.project = self.tmp / "project"
        for directory in (self.home, self.config, self.project):
            directory.mkdir()
        self.env_file = self.project / ".env"
        self.env_file.write_text(ENV_TEXT, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_tool(self, *args, extra_env=None, path=None, tool=TOOL):
        env = {
            "PATH": str(path) if path is not None else os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.config),
        }
        env.update(extra_env or {})
        return subprocess.run(
            [str(tool), *args], cwd=str(self.project), env=env, capture_output=True, text=True, timeout=30
        )

    def assert_no_values(self, result, *values):
        for value in (SECRET, PLACEHOLDER, *values):
            self.assertNotIn(value, result.stdout)
            self.assertNotIn(value, result.stderr)

    def test_from_lists_credential_names_only(self):
        result = self.run_tool("--from", str(self.env_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["API_KEY", "EMPTY_TOKEN", "PLACEHOLDER_SECRET"])
        self.assert_no_values(result)

    def test_pattern_filters_names(self):
        result = self.run_tool("--from", str(self.env_file), "api")
        self.assertEqual(result.stdout.split(), ["API_KEY"])

    def test_default_sources_list_names_and_never_values(self):
        (self.config / ".env").write_text(f"CONFIG_SECRET={CONFIG_SECRET}\n", encoding="utf-8")
        result = self.run_tool(
            extra_env={"SERVICE_API_KEY": PROCESS_SECRET, "MULTI_PRIVATE_KEY": MULTILINE_VALUE}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        names = result.stdout.split()
        for name in ("API_KEY", "EMPTY_TOKEN", "PLACEHOLDER_SECRET", "CONFIG_SECRET", "SERVICE_API_KEY", "MULTI_PRIVATE_KEY"):
            self.assertIn(name, names)
        # A multi-line value must not leak its continuation lines (the old `env | cut` did).
        self.assert_no_values(result, PROCESS_SECRET, CONFIG_SECRET, "LEAKLINE", "END PRIVATE KEY", "BEGIN")

    def test_classify_labels_states_without_values(self):
        result = self.run_tool("--from", str(self.env_file), "--classify")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = dict(line.split(": ", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(lines), {"API_KEY", "EMPTY_TOKEN", "PLACEHOLDER_SECRET"})
        self.assertEqual(lines["EMPTY_TOKEN"], "empty")
        self.assertEqual(lines["PLACEHOLDER_SECRET"], "placeholder")
        self.assertIn(lines["API_KEY"], {"placeholder", "filled (api_key)"})
        self.assert_no_values(result)

    def test_classify_defaults_to_the_project_env_file(self):
        result = self.run_tool("--classify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EMPTY_TOKEN: empty", result.stdout)
        self.assert_no_values(result)

    def test_classify_works_through_a_symlink(self):
        # The plugin's bin/ may be reached through a link; --classify must still find scripts/.
        link = self.tmp / "linked-list-env-keys"
        link.symlink_to(TOOL)
        result = self.run_tool("--from", str(self.env_file), "--classify", tool=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EMPTY_TOKEN: empty", result.stdout)

    def test_classify_without_python_fails_clearly(self):
        bin_dir = isolated_bin(self.tmp, "bash", "grep", "sed", "sort", "dirname", "readlink", "cat")
        result = self.run_tool("--from", str(self.env_file), "--classify", path=bin_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.9", result.stderr)
        self.assert_no_values(result)

    def test_names_only_mode_needs_no_python(self):
        bin_dir = isolated_bin(self.tmp, "bash", "grep", "sed", "sort", "dirname", "readlink", "cat")
        result = self.run_tool("--from", str(self.env_file), path=bin_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["API_KEY", "EMPTY_TOKEN", "PLACEHOLDER_SECRET"])

    def test_missing_from_file_exits_one(self):
        result = self.run_tool("--from", str(self.project / "absent.env"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("file not found", result.stderr)

    def test_from_without_a_path_exits_one(self):
        self.assertEqual(self.run_tool("--from").returncode, 1)

    def test_empty_result_is_success(self):
        only_port = self.project / "ports.env"
        only_port.write_text("PORT=1\n", encoding="utf-8")
        result = self.run_tool("--from", str(only_port))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_regex_is_an_error(self):
        self.assertNotEqual(self.run_tool("--from", str(self.env_file), "(").returncode, 0)

    def test_pattern_starting_with_a_dash_is_a_pattern(self):
        result = self.run_tool("--from", str(self.env_file), "-KEY")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lowercase_and_spaced_keys_are_listed(self):
        # dotenv accepts lowercase names and spaces around `=`; the names must show up, values never.
        mixed = self.project / "mixed.env"
        mixed.write_text(
            f"database_url=postgres://user:{SECRET}@db/app\n  spaced_api_key = {PROCESS_SECRET}\n",
            encoding="utf-8",
        )
        result = self.run_tool("--from", str(mixed))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["database_url", "spaced_api_key"])
        self.assert_no_values(result, PROCESS_SECRET, "postgres://")
        classified = self.run_tool("--from", str(mixed), "--classify")
        self.assertEqual(classified.returncode, 0, classified.stderr)
        lines = dict(line.split(": ", 1) for line in classified.stdout.splitlines())
        self.assertEqual(lines["database_url"], "filled (connection_string)")
        self.assertIn("spaced_api_key", lines)
        self.assert_no_values(classified, PROCESS_SECRET, "postgres://")


class ClassifierTest(unittest.TestCase):
    """The classifier alone, called the way list-env-keys calls it."""

    def classify(self, text, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.env"
            path.write_text(text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "scripts" / "env_key_classify.py"), str(path), *args],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_kinds_are_labelled_and_values_never_printed(self):
        values = {
            "DATABASE_URL": "postgres://user:hunter2pass@db:5432/app",
            "GITHUB_TOKEN": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
            "SMTP_PASSWORD": "correct-horse-battery",
            "NOTIFY_WEBHOOK": "https://hooks.example.com/webhook/abc123",
        }
        text = "".join(f"{name}={value}\n" for name, value in values.items()) + "QUOTED_SECRET=\"changeme\"\r\n"
        result = self.classify(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = dict(line.split(": ", 1) for line in result.stdout.splitlines())
        self.assertEqual(lines["DATABASE_URL"], "filled (connection_string)")
        self.assertEqual(lines["GITHUB_TOKEN"], "filled (api_key)")
        self.assertEqual(lines["SMTP_PASSWORD"], "filled (password)")
        self.assertEqual(lines["NOTIFY_WEBHOOK"], "filled (webhook_url)")
        self.assertEqual(lines["QUOTED_SECRET"], "placeholder")
        for value in values.values():
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_invalid_filter_is_a_clean_error(self):
        result = self.classify("A_KEY=1\n", "(")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid name filter", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_file_exits_one(self):
        result = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "env_key_classify.py"), "/nonexistent/sample.env"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
