"""Tests for the SessionStart hook that injects the safety protocol and the safety status lines.

Run from this directory with `python3 -m unittest`. Standard library only. Every run points
CLAUDE_CONFIG_DIR and CLAUDE_PLUGIN_DATA at temporary directories and puts a fake `curl` and a fake
`claude` first on PATH, so the machine's real Claude Code config is never read and no test reaches the
network.
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
HOOK = "hooks/safety-start"
PROTOCOL = "context/safety.md"
BASH = shutil.which("bash")

# Claude Code replaces additionalContext longer than this with a file path and a preview.
ADDITIONAL_CONTEXT_CAP = 10_000
# The protocol's own budget, so the status lines and future additions keep room under the cap.
PROTOCOL_BUDGET = 3_500

SAFETY_STATUS = "SAFETY STATUS: Python 3.9+ was not found."
BASELINE = "SETTINGS BASELINE: not installed."
VERSION_LINE = "CLAUDE CODE VERSION:"
BEHIND = "the newest published version"
UNKNOWN = "the running Claude Code version could not be determined"
CACHE = "claude-code-versions"
CURRENT = "2.1.282"
TAGS = {"stable": "2.1.274", "latest": CURRENT, "next": CURRENT}


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


def session_input(source="startup"):
    return json.dumps({"session_id": "test", "hook_event_name": "SessionStart", "source": source, "cwd": "/tmp"})


def write_tool(bin_dir, name, body):
    """A fake command: a bash script with the given body."""
    path = Path(bin_dir) / name
    path.write_text(f"#!{BASH}\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_claude(bin_dir, version=CURRENT, output=None, name="claude"):
    line = output if output is not None else f"{version} (Claude Code)"
    return write_tool(bin_dir, name, f"printf '%s\\n' {json.dumps(line)}")


def fake_curl(bin_dir, tags=TAGS, raw=None, fail=False):
    """A fake curl that logs every call next to itself, then prints the dist-tags or fails."""
    log = Path(bin_dir) / "curl-calls"
    if fail:
        body = f'echo "$*" >> "{log}"\nexit 7'
    else:
        answer = raw if raw is not None else json.dumps(tags, separators=(",", ":"))
        body = f'echo "$*" >> "{log}"\nprintf \'%s\' {json.dumps(answer)}'
    return write_tool(bin_dir, "curl", body)


class HookTestCase(unittest.TestCase):
    """Runs the hook hermetically: temporary config and data folders, fake claude and curl first on PATH."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config"
        self.config.mkdir()
        self.data = self.tmp / "plugin-data"
        # Fake tools first on PATH: the current version, and a registry that cannot be reached.
        self.fakes = self.tmp / "fakes"
        self.fakes.mkdir()
        fake_claude(self.fakes)
        fake_curl(self.fakes, fail=True)

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, plugin_root=PLUGIN_ROOT, path=None, source="startup", args=(), **extra):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.config), HOME=str(self.tmp), CLAUDE_PLUGIN_DATA=str(self.data))
        for name in ("CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
            env.pop(name, None)
        env["PATH"] = str(path) if path is not None else f"{self.fakes}{os.pathsep}{env.get('PATH', '')}"
        env.update({key: str(value) for key, value in extra.items()})
        return subprocess.run(
            [str(Path(plugin_root) / HOOK), *args],
            input=session_input(source),
            cwd=str(self.tmp),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def payload(self, result):
        """Assert the exact output shape and return the parsed JSON."""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertLessEqual(set(payload), {"hookSpecificOutput", "systemMessage"})
        inner = payload["hookSpecificOutput"]
        self.assertEqual(set(inner), {"hookEventName", "additionalContext"})
        self.assertEqual(inner["hookEventName"], "SessionStart")
        self.assertIsInstance(inner["additionalContext"], str)
        return payload

    def context(self, result):
        """The additionalContext string of a run that emits no systemMessage."""
        payload = self.payload(result)
        self.assertNotIn("systemMessage", payload)
        return payload["hookSpecificOutput"]["additionalContext"]

    def no_python_path(self):
        bin_dir = isolated_bin(self.tmp, "bash", "cat", "dirname", "mktemp", "rm", "date", "mkdir", "mv")
        fake_claude(bin_dir)
        fake_curl(bin_dir, fail=True)
        return bin_dir


@unittest.skipUnless(BASH, "bash is required to run the hook")
class SafetyStartTest(HookTestCase):
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
        # No Python, no baseline and an outdated Claude Code: every status line, the longest output.
        bin_dir = self.no_python_path()
        fake_claude(bin_dir, "2.1.199")
        fake_curl(bin_dir)
        payload = self.payload(self.run_hook(path=bin_dir))
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(SAFETY_STATUS, context)
        self.assertIn(BASELINE, context)
        self.assertIn(VERSION_LINE, context)
        self.assertLess(len(context), ADDITIONAL_CONTEXT_CAP)
        self.assertLess(len(payload["systemMessage"]), ADDITIONAL_CONTEXT_CAP)

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


@unittest.skipUnless(BASH, "bash is required to run the hook")
class VersionCheckTest(HookTestCase):
    """At startup the hook compares the running Claude Code with the newest published version."""

    def version_lines(self, context):
        return [line for line in context.splitlines() if line.startswith(VERSION_LINE)]

    def curl_calls(self, bin_dir=None):
        log = Path(bin_dir or self.fakes) / "curl-calls"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def write_cache(self, age_seconds, **fields):
        self.data.mkdir(parents=True, exist_ok=True)
        values = {"checked_at": int(time.time()) - age_seconds, "failed": "", "latest": "", "stable": ""}
        values.update(fields)
        (self.data / CACHE).write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")

    def behind(self, result):
        """Assert the warning in both places and return (version line, systemMessage)."""
        payload = self.payload(result)
        lines = self.version_lines(payload["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("systemMessage", payload)
        return lines[0], payload["systemMessage"]

    def test_behind_warns_with_the_update_commands(self):
        fake_claude(self.fakes, "2.1.199")
        fake_curl(self.fakes)
        line, message = self.behind(self.run_hook())
        self.assertIn("this session runs Claude Code 2.1.199; the newest published version is 2.1.282", line)
        self.assertIn("before anything else", line)
        for command in ("`claude update`", "`npm install -g @anthropic-ai/claude-code@latest`",
                        "`brew upgrade claude-code`", "`winget upgrade Anthropic.ClaudeCode`", "`claude doctor`"):
            self.assertIn(command, line)
        self.assertIn("administrator", line)
        self.assertIn("Claude Code 2.1.199 is older than the newest published version, 2.1.282", message)
        self.assertIn("claude update", message)
        self.assertEqual(len(self.curl_calls()), 1)
        self.assertIn("registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags", self.curl_calls()[0])
        self.assertIn("--max-time 3", self.curl_calls()[0])

    def test_equal_or_newer_says_nothing(self):
        fake_curl(self.fakes)
        for version in (CURRENT, "2.1.300", "2.2.0", "3.0"):
            with self.subTest(version=version):
                fake_claude(self.fakes, version)
                self.assertEqual(self.version_lines(self.context(self.run_hook())), [])

    def test_versions_compare_as_numbers(self):
        fake_claude(self.fakes, "2.1.99")
        fake_curl(self.fakes, tags={"latest": "2.1.100"})
        line, _ = self.behind(self.run_hook())
        self.assertIn("2.1.99", line)
        fake_claude(self.fakes, "2.1.100")
        self.write_cache(0, latest="2.1.99")
        self.assertEqual(self.version_lines(self.context(self.run_hook())), [])

    def test_offline_says_nothing_and_retries_after_an_hour(self):
        fake_claude(self.fakes, "2.1.199")
        self.assertEqual(self.version_lines(self.context(self.run_hook())), [])
        self.assertEqual(len(self.curl_calls()), 1)
        self.assertIn("failed=1", (self.data / CACHE).read_text(encoding="utf-8"))
        self.context(self.run_hook())
        self.assertEqual(len(self.curl_calls()), 1, "a failure is cached, so no second request")
        self.write_cache(3700, failed="1")
        fake_curl(self.fakes)
        self.behind(self.run_hook())

    def test_garbage_from_the_registry_says_nothing(self):
        fake_claude(self.fakes, "2.1.199")
        for raw in ("<html>502 Bad Gateway</html>", '{"latest":"not-a-version"}', '{"latest":2}', ""):
            with self.subTest(raw=raw):
                if (self.data / CACHE).exists():
                    (self.data / CACHE).unlink()
                fake_curl(self.fakes, raw=raw)
                self.assertEqual(self.version_lines(self.context(self.run_hook())), [])

    def test_unknown_running_version_gets_one_neutral_line(self):
        for output in ("hello", "2.1.x (Claude Code)", "v22.1.0", ""):
            with self.subTest(output=output):
                fake_claude(self.fakes, output=output)
                lines = self.version_lines(self.context(self.run_hook()))
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(UNKNOWN, lines[0])
                self.assertIn("only if the user asks", lines[0])

    def test_unknown_version_and_offline_too(self):
        bin_dir = isolated_bin(self.tmp, "bash", "cat", "dirname", "mktemp", "rm", "date", "mkdir", "mv")
        lines = self.version_lines(self.context(self.run_hook(path=bin_dir)))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(UNKNOWN, lines[0])

    def test_fresh_cache_makes_no_request(self):
        fake_claude(self.fakes, CURRENT)
        fake_curl(self.fakes)
        self.write_cache(60, latest="2.1.300", stable="2.1.290")
        line, _ = self.behind(self.run_hook())
        self.assertIn("the newest published version is 2.1.300", line)
        self.assertEqual(self.curl_calls(), [])

    def test_damaged_cache_is_ignored(self):
        fake_claude(self.fakes, "2.1.199")
        self.write_cache(60, latest="abc", stable="$(id)")
        self.assertEqual(self.version_lines(self.context(self.run_hook())), [])
        self.data.joinpath(CACHE).write_text("garbage without equals\nlatest\n=\n", encoding="utf-8")
        fake_curl(self.fakes)
        self.behind(self.run_hook())
        self.assertEqual(len(self.curl_calls()), 1, "a cache without a timestamp is stale")

    def test_stale_cache_is_refreshed(self):
        fake_claude(self.fakes, CURRENT)
        fake_curl(self.fakes)
        self.write_cache(7 * 3600, latest="2.1.300")
        self.assertEqual(self.version_lines(self.context(self.run_hook())), [])
        self.assertEqual(len(self.curl_calls()), 1)
        cache = (self.data / CACHE).read_text(encoding="utf-8")
        self.assertIn(f"latest={CURRENT}", cache)
        self.assertIn(f"stable={TAGS['stable']}", cache)

    def test_cache_falls_back_to_the_config_folder(self):
        fake_curl(self.fakes)
        env_without_data = dict(CLAUDE_PLUGIN_DATA="")
        self.context(self.run_hook(**env_without_data))
        self.assertTrue((self.config / "evisions" / CACHE).is_file())
        self.assertFalse((self.config / "evisions" / "settings-applied.json").exists())

    def test_stable_channel_compares_with_the_stable_tag(self):
        (self.config / "settings.json").write_text('{"autoUpdatesChannel": "stable"}', encoding="utf-8")
        fake_curl(self.fakes)
        fake_claude(self.fakes, TAGS["stable"])
        self.assertEqual(self.version_lines(self.context(self.run_hook())), [])
        fake_claude(self.fakes, "2.1.270")
        line, message = self.behind(self.run_hook())
        self.assertIn("the newest published version on the stable release channel is 2.1.274", line)
        self.assertIn("`npm install -g @anthropic-ai/claude-code@stable`", line)
        self.assertIn("@stable", message)

    def test_the_running_executable_wins_over_path(self):
        fake_claude(self.fakes, CURRENT)
        fake_curl(self.fakes)
        running = self.tmp / "running"
        running.mkdir()
        old = fake_claude(running, "2.1.199")
        line, _ = self.behind(self.run_hook(CLAUDE_CODE_EXECPATH=old))
        self.assertIn("runs Claude Code 2.1.199", line)
        broken = write_tool(running, "broken", "exit 1")
        self.assertEqual(self.version_lines(self.context(self.run_hook(CLAUDE_CODE_EXECPATH=broken))), [])

    def test_only_at_startup(self):
        fake_claude(self.fakes, "2.1.199")
        fake_curl(self.fakes)
        for source in ("clear", "compact"):
            with self.subTest(source=source):
                self.assertEqual(self.version_lines(self.context(self.run_hook(source=source))), [])
        self.assertEqual(self.curl_calls(), [])

    def test_nonessential_traffic_off_makes_no_request(self):
        fake_claude(self.fakes, "2.1.199")
        fake_curl(self.fakes)
        self.assertEqual(self.version_lines(self.context(self.run_hook(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1))), [])
        self.assertEqual(self.curl_calls(), [])

    def test_hanging_claude_is_cut_off_without_a_timeout_command(self):
        # macOS has no timeout command: the hook's own watchdog must stop a hanging `claude --version`,
        # including a child process it leaves behind.
        bin_dir = isolated_bin(self.tmp, "bash", "cat", "dirname", "mktemp", "rm", "date", "mkdir", "mv", "sleep")
        self.assertIsNone(shutil.which("timeout", path=str(bin_dir)))
        write_tool(bin_dir, "claude", "sleep 30\necho '2.1.1 (Claude Code)'")
        started = time.monotonic()
        lines = self.version_lines(self.context(self.run_hook(path=bin_dir)))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(len(lines), 1)
        self.assertIn(UNKNOWN, lines[0])


class SharedHelperTest(unittest.TestCase):
    MARKED = re.compile(r"^# --- json string field:.*?^# --- end of json string field ---$", re.MULTILINE | re.DOTALL)

    def test_json_string_field_is_identical_in_both_hooks(self):
        blocks = set()
        for name in ("hooks/session-start", "hooks/safety-start"):
            found = self.MARKED.search((PLUGIN_ROOT / name).read_text(encoding="utf-8"))
            self.assertIsNotNone(found, f"{name} has no marked json string field helper")
            blocks.add(found.group(0))
        self.assertEqual(len(blocks), 1, "the json string field helper differs between the hooks")


if __name__ == "__main__":
    unittest.main()
