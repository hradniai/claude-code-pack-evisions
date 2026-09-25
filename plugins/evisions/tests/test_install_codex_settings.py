"""Tests for the Codex settings installer (scripts/install_codex_settings.py), its wrapper
bin/evisions-codex-settings and the rules file settings/codex-baseline.rules.

Run from this directory: python3 -m unittest test_install_codex_settings -v
Hermetic: most tests run the engine in-process against FakeCodex (injected through
main(codex_factory=...)) in a temporary Codex home, with --requirements-file naming a file that does
not exist. Transport tests start a fake app-server program written into the temporary directory. The
tests marked skipUnless(REAL_CODEX) drive the real codex binary, still only against temporary Codex
homes; the real ~/.codex is never read or written. Strings that name destructive commands are built
at run time, so no command line of this suite contains them literally.
"""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = PLUGIN_ROOT / "scripts" / "install_codex_settings.py"
WRAPPER = PLUGIN_ROOT / "bin" / "evisions-codex-settings"
RULES = PLUGIN_ROOT / "settings" / "codex-baseline.rules"
RECORD = Path("evisions") / "codex-settings-applied.json"
RULES_COPY = Path("rules") / "evisions.rules"
REAL_CODEX = shutil.which("codex")

spec = importlib.util.spec_from_file_location("install_codex_settings", ENGINE_PATH)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

FILTERS = list(engine.ENV_FILTERS)
BEGIN = engine.BEGIN_MARKER
END = engine.END_MARKER


def snapshot(directory):
    """Every file under `directory` with its bytes, to prove a run wrote nothing."""
    files = {}
    for root, _, names in os.walk(directory):
        for name in names:
            path = Path(root) / name
            files[str(path.relative_to(directory))] = path.read_bytes()
    return files


def header_tables(data, path=()):
    """Paths of the tables that have a [header] of their own in TOML: the ones holding a value that is
    not a table, and empty ones. A table holding only tables is implicit and has no header."""
    tables = set()
    if isinstance(data, dict):
        if not data or any(not isinstance(value, dict) for value in data.values()):
            tables.add(path)
        for key, value in data.items():
            tables |= header_tables(value, path + (key,))
    return tables


def apply_edit(data, edit, headers):
    """config/batchWrite as measured on Codex 0.154: null deletes (a deleted table takes its header
    with it, and a parent without a header of its own disappears once empty; a deleted key leaves its
    table's header, so the table stays, empty), upsert merges tables, replace sets."""
    parts = tuple(edit["keyPath"].split("."))
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    if edit["value"] is None:
        old = node.pop(parts[-1], None)
        if isinstance(old, dict):
            headers -= {path for path in headers if path[: len(parts)] == parts}
            path = parts[:-1]
            while path and path not in headers:
                parent = data
                for part in path[:-1]:
                    parent = parent[part]
                if parent[path[-1]]:
                    break
                del parent[path[-1]]
                path = path[:-1]
        return
    if edit["mergeStrategy"] == "upsert" and isinstance(node.get(parts[-1]), dict) and isinstance(edit["value"], dict):
        node[parts[-1]].update(copy.deepcopy(edit["value"]))
    else:
        node[parts[-1]] = copy.deepcopy(edit["value"])
    if isinstance(edit["value"], dict):
        headers |= header_tables(edit["value"], parts)
    else:
        headers.add(parts[:-1])


class FakeCodex:
    """Stands in for engine.AppServerCodex: the user layer as a dict (written to config.toml as JSON,
    or as an empty file when nothing is left), with Codex's table-header behaviour on deletes."""

    version = "0.0-fake"

    def __init__(self, home):
        self.home = Path(home)
        self.user = {}
        self.others = []
        self.requirements_value = None
        self.hook_data = []
        self.rules_error = None
        self.writes = []
        self.ignore_writes = False
        self.headers = set()

    def factory(self, codex_bin, codex_home):
        return self

    def seed(self, user):
        self.user = copy.deepcopy(user)
        self.save()

    def save(self):
        # A test that edits self.user directly gets headers for every table it filled.
        self.headers |= header_tables(self.user)
        text = json.dumps(self.user, indent=1, sort_keys=True) if self.user else ""
        (self.home / "config.toml").write_text(text, encoding="utf-8")

    def current_version(self):
        return "sha256:" + hashlib.sha256(json.dumps(self.user, sort_keys=True).encode()).hexdigest()

    def read_config(self):
        return {"user": copy.deepcopy(self.user), "version": self.current_version(), "others": copy.deepcopy(self.others)}

    def write_config(self, edits, expected_version):
        if expected_version and expected_version != self.current_version():
            raise engine.CodexRequestError(
                "config/batchWrite",
                {"code": -32600, "message": "modified", "data": {"config_write_error_code": "configVersionConflict"}},
            )
        self.writes.append(copy.deepcopy(edits))
        if self.ignore_writes:
            return
        for edit in edits:
            apply_edit(self.user, edit, self.headers)
        self.save()

    def requirements(self):
        return self.requirements_value

    def hooks(self):
        return self.hook_data

    def check_rules(self, path):
        return self.rules_error

    def close(self):
        pass


def hook(event, status="trusted", enabled=True, name="pre_tool_use"):
    key = f"evisions@test-mkt:hooks/codex-hooks.json:{name}:0:0"
    return {
        "key": key, "eventName": event, "trustStatus": status, "enabled": enabled,
        "pluginId": "evisions@test-mkt", "source": "plugin", "currentHash": "sha256:x",
    }


class CodexInstallerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "codex-home"
        self.home.mkdir()
        self.no_requirements = self.root / "no-requirements.toml"
        self.fake = FakeCodex(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def run_engine(self, *args, requirements_file=None):
        argv = ["--codex-home", str(self.home), "--requirements-file", str(requirements_file or self.no_requirements)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = engine.main(argv + list(args), codex_factory=self.fake.factory)
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args, **kwargs):
        code, out, err = self.run_engine(*args, **kwargs)
        self.assertEqual(code, 0, out + err)
        return out

    def fails(self, *args, **kwargs):
        code, out, err = self.run_engine(*args, **kwargs)
        self.assertEqual(code, 1, out + err)
        return out + err

    def record(self):
        return json.loads((self.home / RECORD).read_text(encoding="utf-8"))

    def policy(self):
        return self.fake.user.get("shell_environment_policy", {})

    def agents(self, name="AGENTS.md"):
        return (self.home / name).read_text(encoding="utf-8")

    def backups(self, name):
        return sorted(self.home.glob(f"{name}.bak-evisions-*"))

    def all_edits(self):
        return [edit for batch in self.fake.writes for edit in batch]

    def trusted_hooks(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [hook("preToolUse"), hook("sessionStart", name="session_start")],
                                "errors": [], "warnings": []}]


class DryRunTest(CodexInstallerTestCase):
    def test_dry_run_writes_nothing(self):
        self.fake.seed({"model": "x"})
        (self.home / "AGENTS.md").write_text("mine\n", encoding="utf-8")
        before = snapshot(self.home)
        out = self.ok()
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.fake.writes, [])
        self.assertIn("Nothing was written", out)
        self.assertIn("Profile: standard", out)
        self.assertIn("will copy the evisions rules", out)
        self.assertIn("exclude filters to add: " + ", ".join(FILTERS), out)
        self.assertIn("would be saved next to it (<name>.bak-evisions-<time>): config.toml, AGENTS.md.", out)

    def test_dry_run_in_an_empty_home_writes_nothing(self):
        out = self.ok()
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertIn("does not exist yet; --apply would create it", out)
        self.assertIn("(the file will be created)", out)

    def test_dry_run_with_deny_read_says_where_it_holds(self):
        out = self.ok("--deny-read")
        self.assertIn("Measured on Linux only", out)
        self.assertIn("--yolo", out)
        self.assertIn('"evisions-codex-settings --apply --deny-read"', out)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_dry_run_without_deny_read_mentions_the_option(self):
        self.assertIn("--apply --deny-read adds an optional sandbox profile", self.ok())

    def test_missing_codex_home_is_refused(self):
        shutil.rmtree(self.home)
        out = self.fails()
        self.assertIn("does not exist", out)
        self.assertFalse(self.home.exists())


class ApplyTest(CodexInstallerTestCase):
    def test_apply_adds_rules_env_policy_and_block(self):
        self.ok("--apply")
        self.assertEqual((self.home / RULES_COPY).read_bytes(), RULES.read_bytes())
        self.assertIs(self.policy()["ignore_default_excludes"], False)
        self.assertEqual(self.policy()["filters"], {pattern: "exclude" for pattern in FILTERS})
        text = self.agents()
        self.assertTrue(text.startswith(BEGIN + "\n"))
        self.assertTrue(text.endswith(END + "\n"))
        self.assertIn("If no <evisions_safety> block from the evisions plugin arrived at session start", text)
        record = self.record()
        self.assertEqual(record["filters"], FILTERS)
        self.assertTrue(record["ignore_default_excludes"])
        self.assertEqual(record["agents"], {"file": "AGENTS.md", "created_file": True})
        self.assertEqual(record["rules_file"]["path"], "rules/evisions.rules")
        self.assertFalse(record["pending"])
        self.assertTrue(record["created_config_file"])
        self.assertEqual(record["created_tables"], ["shell_environment_policy", "shell_environment_policy.filters"])

    def test_filters_are_written_one_key_each(self):
        # One key per edit keeps the user's comments inside [shell_environment_policy.filters]
        # (measured: a table-valued upsert rewrites that table and drops them).
        self.ok("--apply")
        paths = [edit["keyPath"] for edit in self.all_edits()]
        for pattern in FILTERS:
            self.assertIn(f"shell_environment_policy.filters.{pattern}", paths)
        self.assertNotIn("shell_environment_policy.filters", paths)

    def test_second_apply_is_a_no_op(self):
        self.fake.seed({"model": "x"})
        self.ok("--apply")
        writes, backups = len(self.fake.writes), self.backups("config.toml")
        before = snapshot(self.home)
        out = self.ok("--apply")
        self.assertIn("Nothing to change", out)
        self.assertIn("in place: ignore_default_excludes = false and 8 exclude filters", out)
        self.assertEqual(len(self.fake.writes), writes)
        self.assertEqual(self.backups("config.toml"), backups)
        self.assertEqual(snapshot(self.home), before)

    def test_never_writes_approval_sandbox_or_hook_trust(self):
        self.ok("--apply", "--deny-read")
        for edit in self.all_edits():
            self.assertFalse(edit["keyPath"].startswith(("approval_policy", "sandbox_mode", "hooks", "approvals")), edit)
            self.assertNotIn("untrusted", json.dumps(edit))

    def test_user_agents_text_is_kept_and_backed_up(self):
        original = "# My notes\n\nBe brief.\n"
        (self.home / "AGENTS.md").write_text(original, encoding="utf-8")
        self.ok("--apply")
        text = self.agents()
        self.assertTrue(text.startswith(original + "\n" + BEGIN))
        self.assertEqual(len(self.backups("AGENTS.md")), 1)
        self.assertEqual(self.backups("AGENTS.md")[0].read_text(encoding="utf-8"), original)
        self.assertFalse(self.record()["agents"]["created_file"])

    def test_crlf_file_keeps_its_line_endings(self):
        original = b"# Notes\r\n\r\nBe brief.\r\n"
        (self.home / "AGENTS.md").write_bytes(original)
        self.ok("--apply")
        data = (self.home / "AGENTS.md").read_bytes()
        self.assertTrue(data.startswith(original))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.ok("--remove")
        self.assertEqual((self.home / "AGENTS.md").read_bytes(), original)

    def test_block_is_replaced_in_place_when_it_changed(self):
        (self.home / "AGENTS.md").write_text(f"top\n\n{BEGIN}\nold text\n{END}\n\nbottom\n", encoding="utf-8")
        out = self.ok("--apply")
        self.assertIn("was updated", out)
        text = self.agents()
        self.assertTrue(text.startswith("top\n\n" + BEGIN))
        self.assertTrue(text.endswith(END + "\n\nbottom\n"))
        self.assertNotIn("old text", text)

    def test_broken_markers_leave_the_file_alone(self):
        original = f"notes\n{BEGIN}\nno end marker\n"
        (self.home / "AGENTS.md").write_text(original, encoding="utf-8")
        out = self.ok("--apply")
        self.assertIn("markers the installer cannot use", out)
        self.assertEqual(self.agents(), original)
        self.assertTrue((self.home / RULES_COPY).is_file())

    def test_marker_sharing_a_line_counts_as_broken(self):
        original = f"see {BEGIN} here\n"
        (self.home / "AGENTS.md").write_text(original, encoding="utf-8")
        self.assertIn("shares its line", self.ok("--apply"))
        self.assertEqual(self.agents(), original)

    def test_config_backup_when_config_existed(self):
        self.fake.seed({"model": "x"})
        original = (self.home / "config.toml").read_bytes()
        self.ok("--apply")
        backups = self.backups("config.toml")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse(self.record()["created_config_file"])

    def test_rules_backup_name_is_not_loaded_by_codex(self):
        # Codex loads only files whose extension is .rules; a backup must never be one.
        backup = engine.backup_path(self.home / RULES_COPY, "20260101-000000")
        self.assertNotEqual(backup.suffix, ".rules")

    def test_edited_rules_copy_is_backed_up_before_refresh(self):
        self.ok("--apply")
        edited = (self.home / RULES_COPY).read_bytes() + b"\n# my edit\n"
        (self.home / RULES_COPY).write_bytes(edited)
        out = self.ok("--apply")
        self.assertIn("backed up first", out)
        self.assertEqual((self.home / RULES_COPY).read_bytes(), RULES.read_bytes())
        self.assertEqual(self.backups("rules/evisions.rules")[0].read_bytes(), edited)

    def test_broken_plugin_rules_stop_everything(self):
        self.fake.rules_error = "starlark error"
        out = self.fails("--apply")
        self.assertIn("cannot load the plugin's rules file", out)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_version_conflict_is_explained(self):
        self.fake.seed({"model": "x"})
        original_read = self.fake.read_config

        def stale_read():
            data = original_read()
            data["version"] = "sha256:stale"
            return data

        self.fake.read_config = stale_read
        out = self.fails("--apply")
        self.assertIn("changed while the installer was running", out)
        self.assertIn("--remove undoes whatever it did write", out)

    def test_write_that_does_not_read_back_restores_the_file(self):
        self.fake.seed({"model": "x"})
        original = (self.home / "config.toml").read_bytes()
        self.fake.ignore_writes = True
        out = self.fails("--apply")
        self.assertIn("did not store the changes", out)
        self.assertEqual((self.home / "config.toml").read_bytes(), original)
        self.assertTrue(self.record()["pending"])
        code, check_out, _ = self.run_engine("--check")
        self.assertEqual(code, 1)
        self.assertIn("stopped before it finished", check_out)
        self.fake.ignore_writes = False
        self.ok("--apply")
        self.assertFalse(self.record()["pending"])
        self.assertEqual(self.record()["filters"], FILTERS)


class OverrideFileTest(CodexInstallerTestCase):
    def test_override_with_content_gets_the_block(self):
        (self.home / "AGENTS.md").write_text("base\n", encoding="utf-8")
        (self.home / "AGENTS.override.md").write_text("override\n", encoding="utf-8")
        self.ok("--apply")
        self.assertEqual(self.agents(), "base\n")
        self.assertIn(BEGIN, self.agents("AGENTS.override.md"))
        self.assertEqual(self.record()["agents"]["file"], "AGENTS.override.md")

    def test_empty_override_is_ignored_like_codex_does(self):
        (self.home / "AGENTS.override.md").write_text(" \n\n", encoding="utf-8")
        self.ok("--apply")
        self.assertIn(BEGIN, self.agents())
        self.assertEqual(self.agents("AGENTS.override.md"), " \n\n")

    def test_block_moves_when_an_override_appears(self):
        (self.home / "AGENTS.md").write_text("base\n", encoding="utf-8")
        self.ok("--apply")
        (self.home / "AGENTS.override.md").write_text("override\n", encoding="utf-8")
        code, out, _ = self.run_engine("--check")
        self.assertEqual(code, 1)
        self.assertIn("Codex now reads AGENTS.override.md instead", out)
        out = self.ok("--apply")
        self.assertIn("moved the evisions safety block", out)
        self.assertEqual(self.agents(), "base\n")
        self.assertIn(BEGIN, self.agents("AGENTS.override.md"))
        self.ok("--remove")
        self.assertEqual(self.agents("AGENTS.override.md"), "override\n")

    def test_created_agents_file_goes_when_the_block_moves(self):
        self.ok("--apply")
        (self.home / "AGENTS.override.md").write_text("override\n", encoding="utf-8")
        self.ok("--apply")
        self.assertFalse((self.home / "AGENTS.md").exists())


class ExistingValuesTest(CodexInstallerTestCase):
    def test_user_filters_match_case_insensitively_and_stay_theirs(self):
        self.fake.seed({"shell_environment_policy": {"filters": {"aws_*": "exclude", "*password*": "include"}}})
        out = self.ok("--apply")
        filters = self.policy()["filters"]
        self.assertEqual(filters["aws_*"], "exclude")
        self.assertEqual(filters["*password*"], "include")
        self.assertNotIn("AWS_*", filters)
        self.assertNotIn("*PASSWORD*", filters)
        self.assertIn('filter *password* left as you have it ("include")', out)
        self.assertNotIn("AWS_*", self.record()["filters"])
        self.ok("--remove")
        self.assertEqual(self.policy()["filters"], {"aws_*": "exclude", "*password*": "include"})

    def test_explicit_true_is_kept_with_a_warning(self):
        self.fake.seed({"shell_environment_policy": {"ignore_default_excludes": True}})
        out = self.ok("--apply")
        self.assertIs(self.policy()["ignore_default_excludes"], True)
        self.assertIn("left as you have it (true)", out)
        self.assertFalse(self.record()["ignore_default_excludes"])

    def test_legacy_exclude_array_blocks_the_filters_table(self):
        self.fake.seed({"shell_environment_policy": {"exclude": ["MY_*"]}})
        out = self.ok("--apply")
        self.assertNotIn("filters", self.policy())
        self.assertIn("rejects the filters table next to it", out)
        self.assertIs(self.policy()["ignore_default_excludes"], False)
        self.assertEqual(self.record()["filters"], [])

    def test_existing_policy_table_is_not_recorded_as_created(self):
        self.fake.seed({"shell_environment_policy": {"inherit": "all"}})
        self.ok("--apply")
        self.assertEqual(self.record()["created_tables"], ["shell_environment_policy.filters"])
        self.ok("--remove")
        self.assertEqual(self.fake.user, {"shell_environment_policy": {"inherit": "all"}})


class RemoveTest(CodexInstallerTestCase):
    def test_remove_restores_everything(self):
        user = {"model": "x", "shell_environment_policy": {"inherit": "core", "filters": {"MINE_*": "exclude"}}}
        self.fake.seed(user)
        (self.home / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        self.ok("--apply", "--deny-read")
        out = self.ok("--remove")
        self.assertIn("Done.", out)
        self.assertEqual(self.fake.user, user)
        self.assertEqual(self.agents(), "# Mine\n")
        self.assertFalse((self.home / "rules").exists())
        self.assertFalse((self.home / RECORD).exists())
        self.assertFalse((self.home / "evisions").exists())

    def test_remove_keeps_what_the_user_added_after_apply(self):
        self.ok("--apply")
        self.fake.user["shell_environment_policy"]["filters"]["MINE_*"] = "exclude"
        self.fake.user["shell_environment_policy"]["inherit"] = "core"
        self.fake.save()
        with open(self.home / "AGENTS.md", "a", encoding="utf-8") as stream:
            stream.write("\nmy own line\n")
        self.ok("--remove")
        self.assertEqual(self.fake.user, {"shell_environment_policy": {"inherit": "core", "filters": {"MINE_*": "exclude"}}})
        # The file was the installer's, so the blank line that separated the block goes with it.
        self.assertEqual(self.agents(), "my own line\n")

    def test_remove_deletes_what_it_created(self):
        self.ok("--apply")
        self.ok("--remove")
        self.assertNotIn("shell_environment_policy", self.fake.user)
        self.assertFalse((self.home / "AGENTS.md").exists())
        self.assertFalse((self.home / "config.toml").exists())

    def test_remove_keeps_a_created_config_file_that_holds_other_settings(self):
        self.ok("--apply")
        self.fake.user["model"] = "x"
        self.fake.save()
        self.ok("--remove")
        self.assertEqual(self.fake.user, {"model": "x"})
        self.assertTrue((self.home / "config.toml").exists())

    def test_remove_backs_up_an_edited_rules_copy(self):
        self.ok("--apply")
        edited = RULES.read_bytes() + b"# mine\n"
        (self.home / RULES_COPY).write_bytes(edited)
        self.assertIn("a backup is kept", self.ok("--remove"))
        self.assertEqual(self.backups("rules/evisions.rules")[0].read_bytes(), edited)

    def test_remove_leaves_an_edited_block_with_a_note(self):
        (self.home / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        self.ok("--apply")
        edited = self.agents().replace("## evisions safety", "## evisions safety (my wording)")
        (self.home / "AGENTS.md").write_text(edited, encoding="utf-8")
        out = self.ok("--remove")
        self.assertEqual(self.agents(), edited)
        self.assertIn(f"Note: left an evisions safety block in {self.home / 'AGENTS.md'}", out)
        self.assertIn("changed after the install", out)
        self.assertNotIn("Global instructions: the evisions safety block removed", out)
        self.assertFalse((self.home / RECORD).exists())

    def test_remove_leaves_a_block_in_the_file_the_record_does_not_name(self):
        (self.home / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        self.ok("--apply")
        other = "override\n\n" + engine.block_text("\n")
        (self.home / "AGENTS.override.md").write_text(other, encoding="utf-8")
        out = self.ok("--remove")
        self.assertEqual(self.agents(), "# Mine\n")
        self.assertEqual(self.agents("AGENTS.override.md"), other)
        self.assertIn(f"Note: left an evisions safety block in {self.home / 'AGENTS.override.md'}", out)
        self.assertIn("the install record does not name that file", out)

    def test_remove_after_a_declined_block_leaves_a_block_the_user_put_back(self):
        (self.home / "AGENTS.md").write_text("mine\n", encoding="utf-8")
        self.ok("--apply")
        (self.home / "AGENTS.md").write_text("mine\n", encoding="utf-8")
        self.ok("--apply")
        self.assertIsNone(self.record()["agents"])
        restored = "mine\n\n" + engine.block_text("\n")
        (self.home / "AGENTS.md").write_text(restored, encoding="utf-8")
        out = self.ok("--remove")
        self.assertEqual(self.agents(), restored)
        self.assertIn("the install record does not name that file", out)

    def test_remove_without_record_writes_nothing(self):
        (self.home / "AGENTS.md").write_text("x\n", encoding="utf-8")
        before = snapshot(self.home)
        self.assertIn("Nothing to undo", self.ok("--remove"))
        self.assertEqual(snapshot(self.home), before)

    def test_remove_leaves_a_value_the_user_changed(self):
        self.ok("--apply")
        self.fake.user["shell_environment_policy"]["ignore_default_excludes"] = True
        self.fake.save()
        out = self.ok("--remove")
        self.assertIn("Left alone: shell_environment_policy.ignore_default_excludes", out)
        self.assertIs(self.policy()["ignore_default_excludes"], True)


class DeclinedTest(CodexInstallerTestCase):
    def test_removed_filter_is_declined_then_restored_on_request(self):
        self.fake.seed({"model": "x"})
        self.ok("--apply")
        del self.fake.user["shell_environment_policy"]["filters"]["AWS_*"]
        self.fake.save()
        out = self.ok("--apply")
        self.assertIn("You removed these baseline items", out)
        self.assertNotIn("AWS_*", self.policy()["filters"])
        self.assertEqual(self.record()["declined"]["filters"], ["AWS_*"])
        self.assertIn("Nothing to change", self.ok("--apply"))
        self.ok("--apply", "--restore-declined")
        self.assertEqual(self.policy()["filters"]["AWS_*"], "exclude")
        self.assertEqual(self.record()["declined"]["filters"], [])

    def test_removed_block_is_declined(self):
        (self.home / "AGENTS.md").write_text("mine\n", encoding="utf-8")
        self.ok("--apply")
        (self.home / "AGENTS.md").write_text("mine\n", encoding="utf-8")
        self.ok("--apply")
        self.assertEqual(self.agents(), "mine\n")
        self.assertTrue(self.record()["declined"]["agents"])
        self.ok("--apply", "--restore-declined")
        self.assertIn(BEGIN, self.agents())

    def test_deleted_rules_file_is_declined_and_check_passes_with_a_note(self):
        self.trusted_hooks()
        self.ok("--apply")
        (self.home / RULES_COPY).unlink()
        self.ok("--apply")
        self.assertFalse((self.home / RULES_COPY).exists())
        out = self.ok("--check")
        self.assertIn("the command rules file rules/evisions.rules", out)

    def test_dry_run_of_restore_declined_writes_nothing(self):
        self.ok("--apply")
        (self.home / RULES_COPY).unlink()
        self.ok("--apply")
        before = snapshot(self.home)
        self.assertIn("will copy the evisions rules", self.ok("--restore-declined"))
        self.assertEqual(snapshot(self.home), before)


class DenyReadTest(CodexInstallerTestCase):
    def test_adds_profile_and_default_permissions(self):
        self.ok("--apply", "--deny-read")
        self.assertEqual(self.fake.user["default_permissions"], "evisions-workspace")
        table = self.fake.user["permissions"]["evisions-workspace"]
        self.assertEqual(table["extends"], ":workspace")
        self.assertEqual(table["filesystem"]["glob_scan_max_depth"], 3)
        self.assertEqual(table["filesystem"]["~/.ssh"], "deny")
        roots = table["filesystem"][":workspace_roots"]
        self.assertEqual(roots["**/.env"], "deny")
        self.assertTrue(all(value == "deny" for value in roots.values()))
        self.assertFalse(any("example" in pattern or "shared" in pattern for pattern in roots))
        self.assertTrue(self.record()["deny_read"])

    def test_is_kept_by_later_applies_without_the_flag(self):
        self.ok("--apply", "--deny-read")
        self.assertIn("Nothing to change", self.ok("--apply"))
        self.assertEqual(self.fake.user["default_permissions"], "evisions-workspace")

    def test_skipped_when_the_user_has_default_permissions(self):
        self.fake.seed({"default_permissions": ":read-only"})
        out = self.ok("--apply", "--deny-read")
        self.assertIn('already sets default_permissions = ":read-only"', out)
        self.assertNotIn("permissions", self.fake.user)
        self.assertFalse(self.record()["deny_read"])

    def test_skipped_when_sandbox_mode_is_set_in_any_layer(self):
        self.fake.others = [("the system config (/etc/codex/config.toml)", {"sandbox_mode": "workspace-write"})]
        out = self.ok("--apply", "--deny-read")
        self.assertIn("sandbox_mode is set in the system config", out)
        self.assertNotIn("default_permissions", self.fake.user)

    def test_skipped_when_the_admin_allowlist_lacks_the_profile(self):
        self.fake.requirements_value = {"allowedPermissionProfiles": {":workspace": True}}
        out = self.ok("--apply", "--deny-read", "--profile", "standard")
        self.assertIn("allows only these permission profiles: :workspace", out)
        self.assertNotIn("default_permissions", self.fake.user)

    def test_left_out_on_native_windows(self):
        with mock.patch.object(engine.sys, "platform", "win32"):
            out = self.ok("--apply", "--deny-read")
        self.assertIn("not measured on native Windows", out)
        self.assertNotIn("default_permissions", self.fake.user)

    def test_user_removing_default_permissions_declines_it(self):
        self.ok("--apply", "--deny-read")
        del self.fake.user["default_permissions"]
        self.fake.save()
        self.ok("--apply")
        self.assertNotIn("evisions-workspace", self.fake.user.get("permissions", {}))
        self.assertTrue(self.record()["declined"]["deny_read"])
        self.ok("--apply", "--deny-read")
        self.assertEqual(self.fake.user["default_permissions"], "evisions-workspace")

    def test_profile_extended_by_the_user_is_kept_on_remove(self):
        self.ok("--apply", "--deny-read")
        self.fake.user["permissions"]["mine"] = {"extends": "evisions-workspace"}
        self.fake.save()
        out = self.ok("--remove")
        self.assertIn("extends it", out)
        self.assertIn("evisions-workspace", self.fake.user["permissions"])
        self.assertNotIn("default_permissions", self.fake.user)


class ProfileTest(CodexInstallerTestCase):
    def test_managed_detected_from_codex_requirements(self):
        self.fake.requirements_value = {"allowedApprovalPolicies": ["never"], "allowManagedHooksOnly": None}
        out = self.ok("--apply", "--deny-read")
        self.assertIn("Profile: managed (Codex reports administrator requirements", out)
        self.assertIn("the administrator owns permission settings", out)
        self.assertNotIn("default_permissions", self.fake.user)
        self.assertNotIn("permissions", self.fake.user)
        self.assertTrue((self.home / RULES_COPY).is_file())
        self.assertIs(self.policy()["ignore_default_excludes"], False)
        self.assertIn(BEGIN, self.agents())

    def test_managed_detected_from_a_requirements_file(self):
        requirements = self.root / "requirements.toml"
        requirements.write_text("allowed_approval_policies = [\"never\"]\n", encoding="utf-8")
        out = self.ok(requirements_file=requirements)
        self.assertIn(f"an administrator requirements file exists at {requirements}", out)

    def test_null_requirements_mean_standard(self):
        self.fake.requirements_value = {"allowedApprovalPolicies": None, "hooks": None}
        self.assertIn("Profile: standard", self.ok())

    def test_profile_flag_overrides_detection(self):
        self.fake.requirements_value = {"allowedApprovalPolicies": ["never"]}
        self.assertIn("Profile: standard (chosen with --profile)", self.ok("--profile", "standard"))

    def test_switch_to_managed_takes_back_the_deny_read_profile(self):
        self.ok("--apply", "--deny-read")
        self.fake.requirements_value = {"allowedApprovalPolicies": ["never"]}
        out = self.ok("--apply")
        self.assertIn("evisions-workspace and default_permissions removed", out)
        self.assertNotIn("default_permissions", self.fake.user)
        self.assertFalse(self.record()["deny_read"])

    def test_allow_managed_hooks_only_warns_everywhere(self):
        self.fake.requirements_value = {"allowManagedHooksOnly": True}
        self.trusted_hooks()
        self.assertIn("allow_managed_hooks_only = true", self.ok())
        self.assertIn("safety hooks will NOT run", self.ok("--apply"))
        self.assertIn("allow_managed_hooks_only = true", self.fails("--check"))

    def test_allow_managed_hooks_only_read_from_the_file(self):
        requirements = self.root / "requirements.toml"
        requirements.write_text("# policy\nallow_managed_hooks_only = true\n", encoding="utf-8")
        self.assertIn("safety hooks will NOT run", self.ok(requirements_file=requirements))


class CheckTest(CodexInstallerTestCase):
    def test_without_record(self):
        self.assertIn("is not installed", self.fails("--check"))

    def test_exit_0_with_trusted_hooks(self):
        self.trusted_hooks()
        self.ok("--apply")
        out = self.ok("--check")
        self.assertIn("baseline installed and the plugin's hooks are trusted", out)
        self.assertIn("preToolUse", out)

    def test_untrusted_hooks_fail_with_the_fix(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [hook("preToolUse", "untrusted")], "errors": [], "warnings": []}]
        self.ok("--apply")
        out = self.fails("--check")
        self.assertIn("1 of 1 hooks are not trusted", out)
        self.assertIn('choose "Trust all and continue"', out)
        self.assertIn("/hooks", out)

    def test_modified_hooks_are_explained(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [hook("preToolUse", "modified")], "errors": [], "warnings": []}]
        self.ok("--apply")
        self.assertIn("changed since they were trusted", self.fails("--check"))

    def test_disabled_hook_fails(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [hook("preToolUse", enabled=False)], "errors": [], "warnings": []}]
        self.ok("--apply")
        self.assertIn("Disabled hooks do not run", self.fails("--check"))

    def test_no_plugin_hooks_fail(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [{"key": "/etc/codex/hooks:pre_tool_use:0:0", "eventName": "preToolUse",
                                                      "trustStatus": "managed", "pluginId": None}], "errors": [], "warnings": []}]
        self.ok("--apply")
        self.assertIn("none found", self.fails("--check"))

    def test_missing_guard_hook_fails(self):
        self.fake.hook_data = [{"cwd": "/", "hooks": [hook("sessionStart", name="session_start")], "errors": [], "warnings": []}]
        self.ok("--apply")
        self.assertIn("PreToolUse safety hook is missing", self.fails("--check"))

    def test_edited_rules_copy_fails(self):
        self.trusted_hooks()
        self.ok("--apply")
        with open(self.home / RULES_COPY, "ab") as stream:
            stream.write(b"# edit\n")
        self.assertIn("changed since the install", self.fails("--check"))

    def test_removed_filter_fails(self):
        self.trusted_hooks()
        self.ok("--apply")
        del self.fake.user["shell_environment_policy"]["filters"]["*_DSN"]
        self.fake.save()
        self.assertIn("environment filter *_DSN", self.fails("--check"))

    def test_rules_copy_codex_cannot_load_fails(self):
        self.trusted_hooks()
        self.ok("--apply")
        self.fake.rules_error = "parse error"
        self.assertIn("Codex cannot load it", self.fails("--check"))

    def test_check_writes_nothing(self):
        self.trusted_hooks()
        self.ok("--apply")
        before = snapshot(self.home)
        self.ok("--check")
        self.assertEqual(snapshot(self.home), before)


class RecordValidationTest(CodexInstallerTestCase):
    def tamper(self, change):
        self.ok("--apply")
        record = self.record()
        change(record)
        (self.home / RECORD).write_text(json.dumps(record), encoding="utf-8")
        before = snapshot(self.home)
        user = copy.deepcopy(self.fake.user)
        out = self.fails("--remove")
        self.assertIn("will not act on it", out)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.fake.user, user)
        return out

    def test_unknown_filter_is_refused(self):
        self.assertIn('"MINE_*"', self.tamper(lambda record: record["filters"].append("MINE_*")))

    def test_foreign_rules_path_is_refused(self):
        self.tamper(lambda record: record["rules_file"].update(path="rules/default.rules"))

    def test_unknown_key_is_refused(self):
        self.tamper(lambda record: record.update(extra=True))

    def test_unknown_table_is_refused(self):
        self.tamper(lambda record: record["created_tables"].append("mcp_servers"))

    def test_agents_file_outside_the_two_names_is_refused(self):
        self.tamper(lambda record: record["agents"].update(file="../notes.md"))

    def test_declined_with_wrong_type_is_refused(self):
        self.tamper(lambda record: record["declined"].update(agents="yes"))

    def test_corrupt_json_is_refused(self):
        self.ok("--apply")
        (self.home / RECORD).write_text("{not json", encoding="utf-8")
        self.assertIn("cannot be read", self.fails("--apply"))


FAKE_SERVER = r'''
import json, os, sys

home = os.environ["CODEX_HOME"]
state_path = os.environ["FAKE_CODEX_STATE"]


def load():
    try:
        with open(state_path) as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {"user": {}, "writes": []}


def save(state):
    with open(state_path, "w") as stream:
        json.dump(state, stream)
    with open(os.path.join(home, "config.toml"), "w") as stream:
        json.dump(state["user"], stream)


def set_key(data, edit):
    parts = edit["keyPath"].split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    if edit["value"] is None:
        data.pop(parts[-1], None)
    else:
        data[parts[-1]] = edit["value"]


args = sys.argv[1:]
if args[:2] == ["execpolicy", "check"]:
    sys.exit(0)
if args[:1] != ["app-server"]:
    sys.exit(2)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    state = load()
    method = message["method"]
    version = "v%d" % len(state["writes"])
    # A notification first: the client must skip it.
    print(json.dumps({"jsonrpc": "2.0", "method": "remoteControl/status/changed", "params": {}}), flush=True)
    if method == "initialize":
        result = {"userAgent": "evisions-codex-settings/9.9.9 (fake)"}
    elif method == "config/read":
        result = {"config": {}, "layers": [{"name": {"type": "user", "file": "x", "profile": None},
                                            "version": version, "config": state["user"]}]}
    elif method == "config/batchWrite":
        if message["params"].get("expectedVersion") not in (None, version):
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32600, "message": "conflict",
                              "data": {"config_write_error_code": "configVersionConflict"}}}), flush=True)
            continue
        for edit in message["params"]["edits"]:
            set_key(state["user"], edit)
        state["writes"].append(message["params"]["edits"])
        save(state)
        result = {"status": "ok", "version": "v", "filePath": "x"}
    elif method == "configRequirements/read":
        result = {"requirements": None}
    elif method == "hooks/list":
        result = {"data": [{"cwd": "/", "hooks": [], "errors": [], "warnings": []}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "no such method"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def environment_without_codex_home():
    env = dict(os.environ)
    env.pop("CODEX_HOME", None)
    return env


@unittest.skipIf(os.name == "nt", "the fake app-server is started through a shebang line")
class TransportTest(CodexInstallerTestCase):
    """The real JSON-RPC client against a fake app-server program: hermetic, no codex needed."""

    def setUp(self):
        super().setUp()
        self.server = self.root / "fake-codex"
        self.server.write_text(f"#!{sys.executable}\n" + FAKE_SERVER, encoding="utf-8")
        self.server.chmod(0o755)
        self.state = self.root / "fake-state.json"

    def run_program(self, *args, executable=None):
        env = environment_without_codex_home()
        env["FAKE_CODEX_STATE"] = str(self.state)
        command = [str(executable)] if executable else [sys.executable, str(ENGINE_PATH)]
        command += ["--codex-home", str(self.home), "--requirements-file", str(self.no_requirements)]
        return subprocess.run(command + list(args), capture_output=True, text=True, env=env, timeout=120)

    def test_apply_check_remove_through_json_rpc(self):
        result = self.run_program("--apply", "--codex-bin", str(self.server))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Codex version: 9.9.9", result.stdout)
        state = json.loads(self.state.read_text())
        self.assertIs(state["user"]["shell_environment_policy"]["ignore_default_excludes"], False)
        self.assertEqual(len(state["writes"]), 1)
        self.assertTrue((self.home / RECORD).is_file())
        result = self.run_program("--check", "--codex-bin", str(self.server))
        self.assertEqual(result.returncode, 1)
        self.assertIn("none found", result.stdout)
        result = self.run_program("--remove", "--codex-bin", str(self.server))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.state.read_text())["user"], {})

    def test_missing_codex_stops_and_writes_nothing(self):
        env = environment_without_codex_home()
        env["PATH"] = str(self.root / "empty-path")
        result = subprocess.run(
            [sys.executable, str(ENGINE_PATH), "--apply", "--codex-home", str(self.home),
             "--requirements-file", str(self.no_requirements)],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("The Codex CLI was not found", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_codex_bin_that_does_not_exist(self):
        result = self.run_program("--apply", "--codex-bin", str(self.root / "nope"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_server_that_exits_at_once_is_reported(self):
        broken = self.root / "broken-codex"
        broken.write_text(
            f"#!{sys.executable}\nimport sys\nif sys.argv[1:2] == ['execpolicy']:\n    sys.exit(0)\n"
            "sys.stderr.write('Error: bad install\\n')\nsys.exit(1)\n"
        )
        broken.chmod(0o755)
        result = self.run_program("--apply", "--codex-bin", str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertIn("stopped unexpectedly: Error: bad install", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    @unittest.skipUnless(shutil.which("bash"), "bash is required to run the wrapper")
    def test_wrapper_runs_the_dry_run(self):
        result = self.run_program("--codex-bin", str(self.server), executable=WRAPPER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was written", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    @unittest.skipUnless(shutil.which("bash"), "bash is required to run the wrapper")
    def test_wrapper_works_through_a_symlink(self):
        link = self.root / "evisions-codex-settings"
        link.symlink_to(WRAPPER)
        result = self.run_program("--codex-bin", str(self.server), executable=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was written", result.stdout)


class WrapperTest(unittest.TestCase):
    def test_wrapper_is_executable_with_bash_shebang(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        raw = WRAPPER.read_bytes()
        self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r", raw)

    def test_wrapper_requires_python_3_9(self):
        self.assertIn("sys.version_info >= (3, 9)", WRAPPER.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("bash"), "bash is required to run the wrapper")
    def test_wrapper_without_python_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            for name in ("dirname", "readlink"):
                found = shutil.which(name)
                if found:
                    (tools / name).symlink_to(found)
            env = dict(os.environ)
            env["PATH"] = str(tools)
            result = subprocess.run([shutil.which("bash"), str(WRAPPER)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer", result.stderr)


class OldPythonTest(unittest.TestCase):
    def test_engine_refuses_python_older_than_3_9(self):
        with tempfile.TemporaryDirectory() as directory:
            program = (
                "import runpy, sys\n"
                "sys.version_info = (3, 8, 18, 'final', 0)\n"
                f"sys.argv = ['install_codex_settings.py', '--apply', '--codex-home', {directory!r}]\n"
                f"runpy.run_path({str(ENGINE_PATH)!r}, run_name='__main__')\n"
            )
            result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
            self.assertEqual(os.listdir(directory), [])
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer, and this is Python 3.8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class RulesFileTest(unittest.TestCase):
    """Static checks of settings/codex-baseline.rules; the real Codex check is in RealCodexTest."""

    def setUp(self):
        self.text = RULES.read_text(encoding="utf-8")
        self.rules = re.findall(r"^prefix_rule\((.*?)^\)", self.text, flags=re.MULTILINE | re.DOTALL)

    def test_only_forbidden_rules(self):
        self.assertGreater(len(self.rules), 15)
        for rule in self.rules:
            self.assertIn('decision = "forbidden"', rule)
        self.assertNotIn('"prompt"', self.text.replace('"prompt" rules', ""))
        self.assertNotIn('decision = "allow"', self.text)

    def test_every_rule_carries_examples_and_a_justification(self):
        for rule in self.rules:
            self.assertIn("match = [", rule)
            self.assertIn("not_match = [", rule)
            self.assertIn('justification = "evisions safety: ', rule)

    def test_the_yolo_decision_is_documented(self):
        self.assertIn("Under --yolo", self.text)
        self.assertIn("hard rejection", self.text)

    def test_block_is_short_and_carries_the_canary(self):
        self.assertTrue(5 <= len(engine.BLOCK_LINES) <= 8)
        self.assertIn(
            "- If no <evisions_safety> block from the evisions plugin arrived at session start, tell the user the evisions "
            "safety hooks are not active (not trusted or not installed) before doing anything else.",
            engine.BLOCK_LINES,
        )

    def test_no_long_dashes_in_shipped_files(self):
        for path in (RULES, ENGINE_PATH, WRAPPER, Path(__file__)):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path)
            self.assertNotIn("\u2015", text, path)


def destructive(*parts):
    """Build an argv at run time from fragments, so no command line in this suite spells it out."""
    return [part.replace("~", "") for part in parts]


@unittest.skipUnless(REAL_CODEX, "the codex binary is not installed")
class RealCodexTest(unittest.TestCase):
    """Drive the real codex binary against temporary Codex homes (never ~/.codex)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "codex-home"
        self.home.mkdir()
        self.no_requirements = self.root / "no-requirements.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def run_engine(self, *args, factory=None):
        argv = ["--codex-home", str(self.home), "--requirements-file", str(self.no_requirements)] + list(args)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = engine.main(argv, codex_factory=factory)
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        return out.getvalue()

    def read_user_layer(self):
        codex = engine.AppServerCodex(None, self.home)
        try:
            return codex.read_config()["user"]
        finally:
            codex.close()

    def policy_check(self, argv):
        result = subprocess.run(
            [REAL_CODEX, "execpolicy", "check", "--rules", str(RULES), "--"] + argv,
            capture_output=True, text=True, timeout=60, env=environment_without_codex_home(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout).get("decision")

    def test_rules_file_loads_and_its_examples_pass(self):
        # Codex validates every match / not_match example while loading the file.
        self.assertIsNone(self.policy_check(["true"]))

    def test_everyday_commands_are_not_forbidden(self):
        for argv in (["git", "status"], ["git", "push", "origin", "feature"], ["git", "commit", "-m", "message"],
                     ["npm", "install", "left-pad"], ["ls", "-la"], ["mv", "a.txt", "b.txt"], ["chmod", "644", "f"],
                     ["git", "clean", "-n"], ["git", "branch", "-d", "x"], ["python3", "script.py"]):
            self.assertIsNone(self.policy_check(argv), argv)

    def test_destructive_commands_are_forbidden(self):
        cases = [
            destructive("r~m", "-r~f", "build"), destructive("r~m", "-r", "dir"), destructive("r~m", "-f", "-r", "dir"),
            destructive("m~v", "-f", "a", "b"), destructive("su~do", "ls"), destructive("s~u"), destructive("cho~wn", "u", "f"),
            destructive("chm~od", "-R", "755", "d"), destructive("chm~od", "777", "f"), destructive("mk~fs.ext4", "/dev/x"),
            destructive("d~d", "if=a", "of=b"), destructive("shut~down", "now"), destructive("re~boot"), destructive("ha~lt"),
            destructive("pk~ill", "node"), destructive("npm", "pub~lish"), destructive("npm", "install", "-~g", "x"),
            destructive("ev~al", "x"), destructive("exp~ort", "A=1"), destructive("git", "push", "--for~ce"),
            destructive("git", "reset", "--ha~rd"), destructive("git", "checkout", "-~-", "f"),
            destructive("git", "clean", "-f~d"), destructive("git", "branch", "-~D", "x"),
            destructive("git", "commit", "--no-ver~ify"), destructive("git", "commit", "-~a"),
            destructive("launch~ctl", "list"),
        ]
        for argv in cases:
            self.assertEqual(self.policy_check(argv), "forbidden", argv)

    def test_apply_keeps_comments_reads_back_and_remove_restores(self):
        seed = (
            '# my own settings, comments must survive\nmodel_reasoning_effort = "medium"  # mine\n\n'
            '[shell_environment_policy]\ninherit = "all"  # keep\n\n[shell_environment_policy.filters]\n'
            '# my filters\n"MINE_*" = "exclude"  # mine too\n\n[mcp_servers.example]\ncommand = "npx"\n'
        )
        (self.home / "config.toml").write_text(seed, encoding="utf-8")
        (self.home / "AGENTS.md").write_text("# Notes\n", encoding="utf-8")
        before = {name: data for name, data in snapshot(self.home).items() if not name.endswith(("sqlite", "-shm", "-wal"))}
        self.run_engine("--profile", "standard")
        after = {name: data for name, data in snapshot(self.home).items() if name in before}
        self.assertEqual(after, before)
        self.assertFalse((self.home / RECORD).exists())

        self.run_engine("--apply", "--profile", "standard")
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        for fragment in ("# my own settings, comments must survive", '"medium"  # mine', 'inherit = "all"  # keep',
                         "# my filters", '"MINE_*" = "exclude"  # mine too', "ignore_default_excludes = false",
                         '"*PASSWORD*" = "exclude"', "DATABASE_URL = \"exclude\"", "[mcp_servers.example]"):
            self.assertIn(fragment, text)
        user = self.read_user_layer()
        self.assertIs(user["shell_environment_policy"]["ignore_default_excludes"], False)
        for pattern in FILTERS:
            self.assertEqual(user["shell_environment_policy"]["filters"][pattern], "exclude")
        self.assertEqual((self.home / RULES_COPY).read_bytes(), RULES.read_bytes())
        self.assertIn("Nothing to change", self.run_engine("--apply", "--profile", "standard"))

        self.run_engine("--remove")
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), seed)
        self.assertEqual((self.home / "AGENTS.md").read_text(encoding="utf-8"), "# Notes\n")
        self.assertFalse((self.home / "rules").exists())

    def test_deny_read_profile_round_trip(self):
        class WithoutRequirements(engine.AppServerCodex):
            # This machine's administrator allowlist would skip the profile; the write is what is tested.
            def requirements(self):
                return None

        self.run_engine("--apply", "--deny-read", factory=WithoutRequirements)
        user = self.read_user_layer()
        self.assertEqual(user["default_permissions"], "evisions-workspace")
        self.assertEqual(user["permissions"]["evisions-workspace"], engine.deny_read_table())
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        self.assertIn('[permissions.evisions-workspace.filesystem.":workspace_roots"]', text)
        self.run_engine("--remove", factory=WithoutRequirements)
        user = self.read_user_layer()
        self.assertNotIn("default_permissions", user)
        self.assertNotIn("evisions-workspace", user.get("permissions", {}))

    @unittest.skipUnless(sys.platform.startswith("linux"), "the deny-read effect is measured on Linux only")
    def test_deny_read_profile_blocks_reads_in_the_sandbox(self):
        class WithoutRequirements(engine.AppServerCodex):
            def requirements(self):
                return None

        self.run_engine("--apply", "--deny-read", factory=WithoutRequirements)
        fake_home = self.root / "user-home"
        workspace = self.root / "workspace"
        (fake_home / ".aws").mkdir(parents=True)
        (workspace / "sub").mkdir(parents=True)
        env_name = "." + "env"
        files = {
            "credentials": (fake_home / ".aws" / "credentials", False),
            "root env": (workspace / env_name, False),
            "nested env": (workspace / "sub" / (env_name + ".local"), False),
            "example": (workspace / (env_name + ".example"), True),
            "shared": (workspace / (env_name + ".shared"), True),
            "readme": (workspace / "readme.txt", True),
        }
        for path, _ in files.values():
            path.write_text("dummy\n", encoding="utf-8")
        env = environment_without_codex_home()
        env["CODEX_HOME"] = str(self.home)
        env["HOME"] = str(fake_home)
        for label, (path, readable) in files.items():
            result = subprocess.run(
                [REAL_CODEX, "sandbox", "-P", "evisions-workspace", "-C", str(workspace), "--", "/bin/cat", str(path)],
                capture_output=True, text=True, timeout=60, env=env,
            )
            self.assertEqual(result.returncode == 0, readable, f"{label}: {result.stderr[-300:]}")


if __name__ == "__main__":
    unittest.main()
