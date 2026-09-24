"""Tests for the settings installer (scripts/install_settings.py) and its wrapper bin/evisions-settings.

Run from this directory: python3 -m unittest test_install_settings -v
Hermetic: every run passes --config-dir and --managed-dir pointing at temporary directories, and
CLAUDE_CONFIG_DIR is removed from the environment, so the real ~/.claude is never read or written.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ENGINE = PLUGIN_ROOT / "scripts" / "install_settings.py"
WRAPPER = PLUGIN_ROOT / "bin" / "evisions-settings"
BASELINE = PLUGIN_ROOT / "settings" / "baseline.json"
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
RECORD = Path("evisions") / "settings-applied.json"
COPIED_SCRIPT = Path("evisions") / "statusline.py"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def visible(section):
    return {key: value for key, value in section.items() if not key.startswith("_")}


BASE = load(BASELINE)
ALLOW = BASE["permissions"]["allow"]
DENY = BASE["permissions"]["deny"]
ASK = BASE["permissions"]["ask"]
SETTINGS = visible(BASE["settings"])
ENV = visible(BASE["env"])
VERSION = load(MANIFEST)["version"]


def environment():
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def snapshot(directory):
    """Every file under `directory` with its bytes, to prove a run wrote nothing."""
    files = {}
    for root, _, names in os.walk(directory):
        for name in names:
            path = Path(root) / name
            files[str(path.relative_to(directory))] = path.read_bytes()
    return files


def all_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from all_keys(value)


class InstallerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.no_policy = self.root / "no-policy"
        self.no_policy.mkdir()
        self.settings = self.config / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def run_engine(self, *args, managed_dir=None):
        command = [sys.executable, str(ENGINE), "--config-dir", str(self.config)]
        command += ["--managed-dir", str(managed_dir or self.no_policy)]
        return subprocess.run(
            command + list(args), capture_output=True, text=True, env=environment(), timeout=60
        )

    def ok(self, *args, **kwargs):
        result = self.run_engine(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def write_settings(self, data):
        self.settings.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def data(self):
        return load(self.settings)

    def backups(self):
        return sorted(self.config.glob("settings.json.bak-evisions-*"))

    def managed_policy(self, drop_in=False):
        policy = self.root / "policy"
        if drop_in:
            (policy / "managed-settings.d").mkdir(parents=True)
            (policy / "managed-settings.d" / "x.json").write_text("{}", encoding="utf-8")
        else:
            policy.mkdir()
            (policy / "managed-settings.json").write_text("{}", encoding="utf-8")
        return policy

    def custom_baseline(self, change):
        """A copy of the baseline, changed by `change(data)`, for upgrade and validation tests."""
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        change(data)
        path = self.root / "baseline-custom.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class DryRunTest(InstallerTestCase):
    def test_dry_run_writes_nothing_with_existing_file(self):
        self.write_settings({"model": "opus", "permissions": {"allow": ["Bash(foo *)"]}})
        before = snapshot(self.config)
        result = self.ok()
        self.assertEqual(snapshot(self.config), before)
        self.assertIn("Nothing was written", result.stdout)
        self.assertIn("Profile: standard", result.stdout)
        self.assertIn("would be saved as", result.stdout)

    def test_dry_run_writes_nothing_without_file(self):
        result = self.ok()
        self.assertEqual(list(self.config.iterdir()), [])
        self.assertIn("does not exist yet", result.stdout)
        self.assertIn(f"allow, {len(ALLOW)} rules", result.stdout)
        self.assertIn(f"deny, {len(DENY)} rules", result.stdout)
        self.assertIn(f"ask, {len(ASK)} rules", result.stdout)
        self.assertIn("cleanupPeriodDays = 3650", result.stdout)
        self.assertIn('DISABLE_TELEMETRY = "1"', result.stdout)
        self.assertIn("Status line: will be set", result.stdout)


class ApplyTest(InstallerTestCase):
    def test_apply_on_missing_file_creates_standard_baseline(self):
        self.ok("--apply")
        data = self.data()
        self.assertEqual(data["permissions"]["allow"], ALLOW)
        self.assertEqual(data["permissions"]["deny"], DENY)
        self.assertEqual(data["permissions"]["ask"], ASK)
        self.assertEqual(data["permissions"]["disableBypassPermissionsMode"], "disable")
        for key, value in SETTINGS.items():
            self.assertEqual(data[key], value)
        self.assertEqual(data["env"], ENV)
        self.assertEqual(data["statusLine"]["type"], "command")
        script = (self.config / COPIED_SCRIPT).as_posix()
        self.assertEqual(
            data["statusLine"]["command"],
            f'python3 "{script}" 2>/dev/null || python "{script}" 2>/dev/null || py -3 "{script}"',
        )
        self.assertEqual((self.config / COPIED_SCRIPT).read_bytes(), (PLUGIN_ROOT / "scripts" / "statusline.py").read_bytes())
        self.assertEqual(self.backups(), [])
        raw = self.settings.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("}\n"))
        self.assertIn('\n  "permissions": {\n    "allow": [\n      "Read",', raw)

    def test_apply_preserves_user_keys_and_order(self):
        user = {
            "model": "opus",
            "someUnknownKey": {"nested": [1, 2, {"x": "ž"}]},
            "permissions": {
                "allow": ["Bash(foo *)", "Read", "mcp__server__tool"],
                "deny": ["Bash(bar *)"],
                "ask": ["Bash(baz *)"],
                "additionalDirectories": ["/data"],
            },
            "env": {"MY_VAR": "x"},
        }
        self.write_settings(user)
        self.ok("--apply")
        data = self.data()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["someUnknownKey"], user["someUnknownKey"])
        self.assertEqual(data["permissions"]["additionalDirectories"], ["/data"])
        self.assertEqual(data["permissions"]["allow"][:3], ["Bash(foo *)", "Read", "mcp__server__tool"])
        self.assertEqual(data["permissions"]["deny"][:1], ["Bash(bar *)"])
        self.assertEqual(data["permissions"]["ask"][:1], ["Bash(baz *)"])
        self.assertEqual(data["env"]["MY_VAR"], "x")
        self.assertIn('"ž"', self.settings.read_text(encoding="utf-8"), "ensure_ascii must be off")
        for name in ("allow", "deny", "ask"):
            rules = data["permissions"][name]
            self.assertEqual(len(rules), len(set(rules)), f"duplicates in {name}")
        self.assertEqual(data["permissions"]["allow"].count("Read"), 1)
        record = load(self.config / RECORD)
        self.assertNotIn("Read", record["added"]["permissions.allow"], "the user's own entry must not be recorded")

    def test_user_deny_matching_baseline_allow_stays(self):
        self.write_settings({"permissions": {"deny": ["Bash(git *)"], "allow": ["Bash(sudo *)"]}})
        result = self.ok("--apply")
        data = self.data()
        self.assertIn("Bash(git *)", data["permissions"]["deny"])
        self.assertNotIn("Bash(git *)", data["permissions"]["allow"], "an allow the user denies is not added")
        self.assertIn("Bash(sudo *)", data["permissions"]["allow"])
        self.assertIn("Bash(sudo *)", data["permissions"]["deny"])
        self.assertIn("Not added to allow", result.stdout)
        self.assertIn("Deny wins", result.stdout)

    def test_edit_star_counts_as_bare_edit(self):
        self.write_settings({"permissions": {"allow": ["Edit(*)"]}})
        self.ok("--apply")
        allow = self.data()["permissions"]["allow"]
        self.assertIn("Edit(*)", allow)
        self.assertNotIn("Edit", allow)

    def test_second_apply_is_a_no_op(self):
        self.write_settings({"model": "opus"})
        self.ok("--apply")
        before = snapshot(self.config)
        result = self.ok("--apply")
        self.assertIn("Nothing to change", result.stdout)
        self.assertEqual(snapshot(self.config), before)
        self.assertEqual(len(self.backups()), 1)

    def test_backup_created_when_file_existed(self):
        self.write_settings({"model": "opus"})
        original = self.settings.read_bytes()
        result = self.ok("--apply")
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertRegex(backups[0].name, r"^settings\.json\.bak-evisions-\d{8}-\d{6}$")
        self.assertIn(str(backups[0]), result.stdout)

    def test_empty_file_counts_as_empty_settings(self):
        self.settings.write_text("  \n", encoding="utf-8")
        self.ok("--apply")
        self.assertEqual(self.data()["permissions"]["allow"], ALLOW)

    def test_symlinked_settings_file_stays_a_symlink(self):
        real = self.root / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text('{"model": "opus"}', encoding="utf-8")
        self.settings.symlink_to(real)
        self.ok("--apply")
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(load(real)["model"], "opus")
        self.assertIn("permissions", load(real))


class RefusalTest(InstallerTestCase):
    def assert_refused(self, content, message):
        self.settings.write_text(content, encoding="utf-8")
        before = snapshot(self.config)
        result = self.run_engine("--apply")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(snapshot(self.config), before, "a refused apply must write nothing")
        self.assertEqual(self.backups(), [])

    def test_invalid_json_is_refused(self):
        self.assert_refused('{"model": "opus",', "is not valid JSON")

    def test_nan_is_refused(self):
        self.assert_refused('{"feedbackSurveyRate": NaN}', "is not valid JSON")

    def test_top_level_array_is_refused(self):
        self.assert_refused("[]", "at the top level")

    def test_permissions_not_an_object_is_refused(self):
        self.assert_refused('{"permissions": ["Read"]}', '"permissions" is a list')

    def test_allow_not_a_list_is_refused(self):
        self.assert_refused('{"permissions": {"allow": "Read"}}', '"permissions.allow" is a text value')

    def test_dry_run_reports_invalid_json_too(self):
        self.settings.write_text("{", encoding="utf-8")
        result = self.run_engine()
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not valid JSON", result.stderr)


class ProfileTest(InstallerTestCase):
    def test_managed_profile_adds_no_permissions_no_bypass_lock_and_no_env(self):
        policy = self.managed_policy()
        result = self.ok("--apply", managed_dir=policy)
        data = self.data()
        self.assertNotIn("permissions", data)
        self.assertNotIn("env", data, "the administrator's environment owns telemetry")
        for key, value in SETTINGS.items():
            self.assertEqual(data[key], value)
        self.assertIn("statusLine", data)
        self.assertEqual(set(data), set(SETTINGS) | {"statusLine"})
        self.assertIn("Profile: managed", result.stdout)
        self.assertIn("the administrator's policy owns permissions and bypass mode", result.stdout)
        self.assertIn("the administrator's environment owns telemetry", result.stdout)
        self.assertNotIn("DISABLE_TELEMETRY", result.stdout)
        record = load(self.config / RECORD)
        self.assertEqual(record["profile"], "managed")
        self.assertEqual(record["added"], {"permissions.allow": [], "permissions.deny": [], "permissions.ask": []})
        self.assertEqual(sorted(record["set"]), sorted(list(SETTINGS) + ["statusLine"]))

    def test_managed_profile_keeps_user_permissions_untouched(self):
        policy = self.managed_policy()
        user = {"permissions": {"allow": ["Bash(foo *)"], "defaultMode": "acceptEdits"}}
        self.write_settings(user)
        self.ok("--apply", managed_dir=policy)
        self.assertEqual(self.data()["permissions"], user["permissions"])

    def test_managed_detected_via_drop_in(self):
        policy = self.managed_policy(drop_in=True)
        result = self.ok(managed_dir=policy)
        self.assertIn("Profile: managed", result.stdout)
        self.assertIn("x.json", result.stdout)

    def test_hidden_or_non_json_drop_in_does_not_count(self):
        policy = self.root / "policy"
        (policy / "managed-settings.d").mkdir(parents=True)
        (policy / "managed-settings.d" / ".hidden.json").write_text("{}", encoding="utf-8")
        (policy / "managed-settings.d" / "notes.txt").write_text("x", encoding="utf-8")
        result = self.ok(managed_dir=policy)
        self.assertIn("Profile: standard", result.stdout)

    def test_profile_flag_overrides_detection(self):
        policy = self.managed_policy()
        self.ok("--apply", "--profile", "standard", managed_dir=policy)
        self.assertEqual(self.data()["permissions"]["disableBypassPermissionsMode"], "disable")

    def test_switch_from_standard_to_managed_takes_back_permissions_and_env(self):
        self.write_settings({"permissions": {"allow": ["Bash(foo *)"]}, "env": {"MY_VAR": "x"}})
        self.ok("--apply")
        self.assertEqual(self.data()["env"]["DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        result = self.ok("--apply", managed_dir=self.managed_policy())
        data = self.data()
        self.assertEqual(data["permissions"], {"allow": ["Bash(foo *)"]})
        self.assertEqual(data["env"], {"MY_VAR": "x"}, "env variables the standard install set are taken back")
        self.assertEqual(data["cleanupPeriodDays"], SETTINGS["cleanupPeriodDays"])
        self.assertIn("env.DISABLE_TELEMETRY", result.stdout)
        record = load(self.config / RECORD)
        self.assertNotIn("env.DISABLE_TELEMETRY", record["set"])
        self.assertNotIn("env.DISABLE_TELEMETRY", record["declined"], "taken back by the switch, not declined")

    def test_env_created_by_standard_install_disappears_on_switch(self):
        self.ok("--apply")
        self.ok("--apply", managed_dir=self.managed_policy())
        self.assertNotIn("env", self.data())


class ExistingValuesTest(InstallerTestCase):
    def test_existing_values_are_not_overwritten(self):
        self.write_settings({
            "cleanupPeriodDays": 30,
            "env": {"DISABLE_TELEMETRY": "0"},
            "permissions": {"disableBypassPermissionsMode": "custom"},
        })
        result = self.ok("--apply")
        data = self.data()
        self.assertEqual(data["cleanupPeriodDays"], 30)
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], "0")
        self.assertEqual(data["env"]["DISABLE_ERROR_REPORTING"], ENV["DISABLE_ERROR_REPORTING"])
        self.assertEqual(data["permissions"]["disableBypassPermissionsMode"], "custom")
        self.assertIn("Left as you have them", result.stdout)
        self.assertIn("permissions.disableBypassPermissionsMode: you have", result.stdout)
        record = load(self.config / RECORD)
        for key in ("cleanupPeriodDays", "env.DISABLE_TELEMETRY", "permissions.disableBypassPermissionsMode"):
            self.assertNotIn(key, record["set"])

    def test_existing_statusline_untouched(self):
        mine = {"type": "command", "command": "~/my-statusline.sh", "padding": 0}
        self.write_settings({"statusLine": mine})
        result = self.ok("--apply")
        self.assertEqual(self.data()["statusLine"], mine)
        self.assertFalse((self.config / COPIED_SCRIPT).exists())
        self.assertIn("left alone", result.stdout)

    def test_no_statusline_flag(self):
        self.ok("--apply", "--no-statusline")
        self.assertNotIn("statusLine", self.data())
        self.assertFalse((self.config / COPIED_SCRIPT).exists())
        self.assertEqual(load(self.config / RECORD)["files"], [])

    def test_no_statusline_later_takes_back_our_statusline(self):
        self.ok("--apply")
        self.ok("--apply", "--no-statusline")
        self.assertNotIn("statusLine", self.data())
        self.assertFalse((self.config / COPIED_SCRIPT).exists())

    def test_invalid_user_value_is_warned_about(self):
        self.write_settings({"cleanupPeriodDays": 0})
        result = self.ok()
        self.assertIn("Warning: Your file sets cleanupPeriodDays to 0", result.stdout)

    def test_changed_statusline_script_is_refreshed(self):
        self.ok("--apply")
        copied = self.config / COPIED_SCRIPT
        copied.write_text("# stale copy\n", encoding="utf-8")
        result = self.ok("--apply")
        self.assertIn("refreshed", result.stdout)
        self.assertEqual(copied.read_bytes(), (PLUGIN_ROOT / "scripts" / "statusline.py").read_bytes())


class RemoveTest(InstallerTestCase):
    def test_remove_restores_the_pre_apply_file(self):
        user = {
            "model": "opus",
            "permissions": {"allow": ["Read", "Bash(foo *)"], "deny": ["Bash(bar *)"]},
            "env": {"MY_VAR": "x"},
            "cleanupPeriodDays": 30,
        }
        self.write_settings(user)
        self.ok("--apply")
        self.ok("--remove")
        self.assertEqual(self.data(), user)
        self.assertFalse((self.config / RECORD).exists())
        self.assertFalse((self.config / COPIED_SCRIPT).exists())
        self.assertFalse((self.config / "evisions").exists())
        self.assertEqual(len(self.backups()), 2)

    def test_remove_keeps_what_the_user_added_after_apply(self):
        user = {"permissions": {"deny": ["Bash(bar *)"]}}
        self.write_settings(user)
        self.ok("--apply")
        data = self.data()
        data["permissions"]["allow"].append("Bash(custom *)")
        data["theme"] = "dark"
        data["env"]["LATER"] = "1"
        self.write_settings(data)
        self.ok("--remove")
        expected = {
            "permissions": {"deny": ["Bash(bar *)"], "allow": ["Bash(custom *)"]},
            "theme": "dark",
            "env": {"LATER": "1"},
        }
        self.assertEqual(self.data(), expected)

    def test_remove_deletes_the_settings_file_it_created(self):
        self.ok("--apply")
        self.assertTrue(load(self.config / RECORD)["created_settings_file"])
        result = self.ok("--remove")
        self.assertFalse(self.settings.exists(), "no {} may be left behind")
        self.assertEqual(list(self.config.iterdir()), [], "the config dir is exactly as before the apply")
        self.assertIn("the installer created it", result.stdout)

    def test_remove_keeps_a_created_file_that_now_holds_user_settings(self):
        self.ok("--apply")
        data = self.data()
        data["theme"] = "dark"
        self.write_settings(data)
        self.ok("--remove")
        self.assertEqual(self.data(), {"theme": "dark"})

    def test_remove_keeps_a_file_that_existed_before(self):
        self.write_settings({})
        self.ok("--apply")
        self.assertFalse(load(self.config / RECORD)["created_settings_file"])
        self.ok("--remove")
        self.assertEqual(self.data(), {})

    def test_remove_leaves_user_modified_scalar(self):
        self.ok("--apply")
        data = self.data()
        data["cleanupPeriodDays"] = 90
        self.write_settings(data)
        result = self.ok("--remove")
        self.assertEqual(self.data(), {"cleanupPeriodDays": 90})
        self.assertIn("Left alone: cleanupPeriodDays", result.stdout)

    def test_remove_without_record_writes_nothing(self):
        self.write_settings({"model": "opus"})
        before = snapshot(self.config)
        result = self.ok("--remove")
        self.assertIn("Nothing to undo", result.stdout)
        self.assertEqual(snapshot(self.config), before)


class UpgradeTest(InstallerTestCase):
    def test_upgrade_takes_back_entries_that_left_the_baseline(self):
        def older(data):
            data["permissions"]["allow"].append("Bash(oldtool *)")
            data["permissions"]["allow"].append("Bash(legacy *)")
            data["permissions"]["deny"].append("Bash(olddeny *)")

        self.write_settings({"permissions": {"allow": ["Bash(legacy *)"]}})
        self.ok("--apply", "--baseline", str(self.custom_baseline(older)))
        data = self.data()
        self.assertIn("Bash(oldtool *)", data["permissions"]["allow"])

        result = self.ok("--apply")
        data = self.data()
        self.assertNotIn("Bash(oldtool *)", data["permissions"]["allow"])
        self.assertNotIn("Bash(olddeny *)", data["permissions"]["deny"])
        self.assertIn("Bash(legacy *)", data["permissions"]["allow"], "the user's own entry must stay")
        self.assertIn("no longer in the baseline", result.stdout)
        record = load(self.config / RECORD)
        self.assertNotIn("Bash(oldtool *)", record["added"]["permissions.allow"])

    def test_upgrade_takes_back_an_env_variable_that_left_the_baseline(self):
        self.ok("--apply")
        newer = self.custom_baseline(lambda d: d["env"].pop("DISABLE_ERROR_REPORTING"))
        self.ok("--apply", "--baseline", str(newer))
        self.assertNotIn("DISABLE_ERROR_REPORTING", self.data()["env"])
        self.assertNotIn("env.DISABLE_ERROR_REPORTING", load(self.config / RECORD)["set"])

    def test_upgrade_changes_a_value_it_set_itself(self):
        self.ok("--apply", "--baseline", str(self.custom_baseline(lambda d: d["settings"].update(cleanupPeriodDays=100))))
        self.assertEqual(self.data()["cleanupPeriodDays"], 100)
        self.ok("--apply")
        self.assertEqual(self.data()["cleanupPeriodDays"], SETTINGS["cleanupPeriodDays"])


class DeclinedTest(InstallerTestCase):
    """A baseline item the user deletes after an apply is their choice: later applies leave it out."""

    NOTE = "You removed these baseline items; --apply leaves them out"

    def record(self):
        return load(self.config / RECORD)

    def apply_then_delete(self):
        self.ok("--apply")
        data = self.data()
        del data["env"]["DISABLE_TELEMETRY"]
        data["permissions"]["deny"].remove("Bash(sudo *)")
        self.write_settings(data)
        return data

    def test_reapply_leaves_removed_items_out_and_records_them(self):
        edited = self.apply_then_delete()
        result = self.ok("--apply")
        self.assertEqual(self.data(), edited, "nothing the user removed may come back")
        record = self.record()
        self.assertEqual(record["declined"]["env.DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        self.assertEqual(record["declined"]["permissions.deny"], ["Bash(sudo *)"])
        self.assertNotIn("env.DISABLE_TELEMETRY", record["set"])
        self.assertNotIn("Bash(sudo *)", record["added"]["permissions.deny"])
        self.assertIn(self.NOTE, result.stdout)
        self.assertIn("permissions.deny: Bash(sudo *)", result.stdout)
        self.assertIn("  env.DISABLE_TELEMETRY", result.stdout)
        self.assertEqual(len(self.backups()), 0, "only the record changed, so no settings backup")

        before = snapshot(self.config)
        result = self.ok("--apply")
        self.assertIn("Nothing to change", result.stdout)
        self.assertIn(self.NOTE, result.stdout)
        self.assertEqual(snapshot(self.config), before)

    def test_dry_run_shows_declined_items(self):
        self.apply_then_delete()
        self.ok("--apply")
        before = snapshot(self.config)
        result = self.ok()
        self.assertIn(self.NOTE, result.stdout)
        self.assertIn("permissions.deny: Bash(sudo *)", result.stdout)
        self.assertEqual(snapshot(self.config), before)

    def test_restore_declined_brings_both_back(self):
        self.apply_then_delete()
        self.ok("--apply")
        preview = self.ok("--restore-declined")
        self.assertIn("Bash(sudo *)", preview.stdout)
        self.assertIn("--apply --restore-declined", preview.stdout)
        self.ok("--apply", "--restore-declined")
        data = self.data()
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        self.assertIn("Bash(sudo *)", data["permissions"]["deny"])
        record = self.record()
        self.assertEqual(record["declined"], {"permissions.allow": [], "permissions.deny": [], "permissions.ask": []})
        self.assertEqual(record["set"]["env.DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        self.assertIn("Bash(sudo *)", record["added"]["permissions.deny"])

    def test_restore_declined_right_after_deleting(self):
        self.apply_then_delete()
        self.ok("--apply", "--restore-declined")
        data = self.data()
        self.assertEqual(data["env"]["DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        self.assertIn("Bash(sudo *)", data["permissions"]["deny"])

    def test_check_passes_with_a_note(self):
        self.apply_then_delete()
        result = self.run_engine("--check")
        self.assertEqual(result.returncode, 1, "a deletion not yet seen by --apply is still reported")
        self.assertIn("If you removed them on purpose", result.stdout)

        self.ok("--apply")
        result = self.ok("--check")
        self.assertIn(f"baseline installed (version {VERSION}, profile standard)", result.stdout)
        self.assertIn(self.NOTE, result.stdout)
        self.assertIn("permissions.deny: Bash(sudo *)", result.stdout)
        self.assertIn("  env.DISABLE_TELEMETRY", result.stdout)

    def test_remove_still_restores_the_pre_apply_file(self):
        user = {"model": "opus", "permissions": {"allow": ["Bash(foo *)"]}}
        self.write_settings(user)
        self.apply_then_delete()
        self.ok("--apply")
        self.ok("--remove")
        self.assertEqual(self.data(), user)
        self.assertFalse((self.config / RECORD).exists())

    def test_declined_item_that_reappears_is_the_users(self):
        self.apply_then_delete()
        self.ok("--apply")
        data = self.data()
        data["env"]["DISABLE_TELEMETRY"] = "0"
        data["permissions"]["deny"].append("Bash(sudo *)")
        self.write_settings(data)
        self.ok("--apply")
        record = self.record()
        self.assertNotIn("env.DISABLE_TELEMETRY", record["declined"])
        self.assertNotIn("env.DISABLE_TELEMETRY", record["set"])
        self.assertEqual(record["declined"]["permissions.deny"], [])
        self.assertNotIn("Bash(sudo *)", record["added"]["permissions.deny"])
        self.ok("--remove")
        data = self.data()
        self.assertEqual(data["env"], {"DISABLE_TELEMETRY": "0"})
        self.assertEqual(data["permissions"]["deny"], ["Bash(sudo *)"])

    def test_new_baseline_items_are_still_added(self):
        def older(data):
            data["env"].pop("DISABLE_ERROR_REPORTING")
            data["permissions"]["deny"].remove("Bash(halt*)")

        older_baseline = str(self.custom_baseline(older))
        self.ok("--apply", "--baseline", older_baseline)
        data = self.data()
        del data["env"]["DISABLE_TELEMETRY"]
        data["permissions"]["deny"].remove("Bash(sudo *)")
        self.write_settings(data)
        self.ok("--apply", "--baseline", older_baseline)

        self.ok("--apply")
        data = self.data()
        self.assertIn("Bash(halt*)", data["permissions"]["deny"], "a rule new in the baseline is added")
        self.assertEqual(data["env"]["DISABLE_ERROR_REPORTING"], ENV["DISABLE_ERROR_REPORTING"])
        self.assertNotIn("Bash(sudo *)", data["permissions"]["deny"])
        self.assertNotIn("DISABLE_TELEMETRY", data["env"])

    def test_deleted_statusline_is_declined(self):
        self.ok("--apply")
        data = self.data()
        del data["statusLine"]
        self.write_settings(data)
        result = self.ok("--apply")
        self.assertNotIn("statusLine", self.data())
        self.assertFalse((self.config / COPIED_SCRIPT).exists())
        self.assertIn("statusLine", self.record()["declined"])
        self.assertIn("Status line: left out, because you removed it.", result.stdout)

    def test_restore_declined_only_with_apply(self):
        for other in ("--check", "--remove"):
            with self.subTest(other=other):
                result = self.run_engine(other, "--restore-declined")
                self.assertEqual(result.returncode, 2)
                self.assertIn("--restore-declined works only with --apply", result.stderr)


class BaselineValidationTest(InstallerTestCase):
    def assert_baseline_refused(self, change, message):
        path = self.custom_baseline(change)
        result = self.run_engine("--apply", "--baseline", str(path))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertEqual(list(self.config.iterdir()), [])

    def test_cleanup_zero_refused(self):
        self.assert_baseline_refused(lambda d: d["settings"].update(cleanupPeriodDays=0), "cleanupPeriodDays")

    def test_pipe_rule_refused(self):
        self.assert_baseline_refused(lambda d: d["permissions"]["deny"].append("Bash(curl * | sh*)"), "contains '|'")

    def test_unanchored_glob_refused(self):
        self.assert_baseline_refused(lambda d: d["permissions"]["allow"].append("Task*"), "wildcard in the tool name")

    def test_star_form_refused(self):
        self.assert_baseline_refused(lambda d: d["permissions"]["allow"].append("Skill(*)"), "bare tool name")

    def test_attribution_refused(self):
        self.assert_baseline_refused(lambda d: d["settings"].update(attribution=False), "attribution")

    def test_unknown_setting_refused(self):
        self.assert_baseline_refused(lambda d: d["settings"].update(alwaysThinkingEnabled=True), "alwaysThinkingEnabled")

    def test_unknown_env_variable_refused(self):
        self.assert_baseline_refused(lambda d: d["env"].update(PATH="/tmp"), "KNOWN_ENV_NAMES")


class WrittenFileHygieneTest(InstallerTestCase):
    def assert_clean(self, data):
        raw = json.dumps(data)
        self.assertNotEqual(data.get("cleanupPeriodDays"), 0)
        self.assertNotIn("attribution", raw)
        self.assertNotIn("Task*", raw)
        self.assertEqual([key for key in all_keys(data) if key.startswith("_")], [])
        for name in ("allow", "deny", "ask"):
            for rule in data.get("permissions", {}).get(name, []):
                self.assertNotIn("|", rule)

    def test_standard_file_is_clean(self):
        self.ok("--apply")
        self.assert_clean(self.data())

    def test_managed_file_is_clean(self):
        self.ok("--apply", managed_dir=self.managed_policy())
        self.assert_clean(self.data())

    def test_baseline_itself_has_no_dropped_rules(self):
        rules = ALLOW + DENY + ASK
        self.assertFalse([rule for rule in rules if "|" in rule])
        self.assertNotIn("Task*", rules)
        self.assertNotIn("Read(./.env)", rules)
        self.assertFalse([rule for rule in rules if rule.endswith("(*)")])
        self.assertNotIn("alwaysThinkingEnabled", BASE)
        self.assertNotIn("hooks", BASE)
        self.assertNotIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", ENV)
        self.assertIn("Bash(evisions-settings)", ALLOW)
        self.assertFalse([rule for rule in ALLOW if "--apply" in rule or "--remove" in rule])

    def test_every_git_rule_has_a_dash_c_twin(self):
        # Measured on 2.1.281: Bash(git reset --hard*) does not stop git -C . reset --hard.
        for rules in (DENY, ASK):
            for rule in rules:
                if rule.startswith("Bash(git ") and not rule.startswith("Bash(git -C "):
                    with self.subTest(rule=rule):
                        self.assertIn(rule.replace("Bash(git ", "Bash(git -C * ", 1), rules)


class RecordAndCheckTest(InstallerTestCase):
    def test_record_exists_at_contract_path(self):
        self.ok("--apply")
        record = load(self.config / "evisions" / "settings-applied.json")
        self.assertEqual(record["baseline_version"], VERSION)
        self.assertEqual(record["profile"], "standard")
        self.assertRegex(record["applied_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(record["added"]["permissions.allow"], ALLOW)
        self.assertEqual(record["added"]["permissions.deny"], DENY)
        self.assertEqual(record["added"]["permissions.ask"], ASK)
        self.assertEqual(record["set"]["permissions.disableBypassPermissionsMode"], "disable")
        self.assertEqual(record["set"]["cleanupPeriodDays"], SETTINGS["cleanupPeriodDays"])
        self.assertEqual(record["set"]["env.DISABLE_TELEMETRY"], ENV["DISABLE_TELEMETRY"])
        self.assertIn("statusLine", record["set"])
        self.assertEqual(record["declined"], {"permissions.allow": [], "permissions.deny": [], "permissions.ask": []})
        self.assertEqual(record["files"], ["evisions/statusline.py"])
        self.assertIs(record["pending"], False)
        self.assertIs(record["created_settings_file"], True)

    def test_check_exit_codes(self):
        result = self.run_engine("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stdout)

        self.ok("--apply")
        result = self.ok("--check")
        self.assertIn(f"baseline installed (version {VERSION}, profile standard)", result.stdout)

        data = self.data()
        data["permissions"]["deny"].remove("Bash(sudo *)")
        self.write_settings(data)
        before = snapshot(self.config)
        result = self.run_engine("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("permissions.deny: Bash(sudo *)", result.stdout)
        self.assertEqual(snapshot(self.config), before, "--check must write nothing")

    def test_check_and_apply_are_exclusive(self):
        result = self.run_engine("--check", "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.config.iterdir()), [])


RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class FailedWriteTest(InstallerTestCase):
    """A failed apply must never make a later apply decline anything, and --remove must still work."""

    def assert_full_baseline(self, data):
        self.assertEqual(data["permissions"]["allow"], ALLOW)
        self.assertEqual(data["permissions"]["deny"], DENY)
        self.assertEqual(data["permissions"]["ask"], ASK)
        self.assertEqual(data["permissions"]["disableBypassPermissionsMode"], "disable")
        self.assertEqual(data["env"], ENV)

    def assert_no_declines(self):
        record = load(self.config / RECORD)
        self.assertEqual(record["declined"], {"permissions.allow": [], "permissions.deny": [], "permissions.ask": []})

    def locked_target(self, content):
        """settings.json as a symlink into a directory that can be made read-only."""
        locked = self.root / "locked"
        locked.mkdir()
        target = locked / "settings.json"
        target.write_text(json.dumps(content), encoding="utf-8")
        self.settings.symlink_to(target)
        return locked, target

    def apply_while_read_only(self, locked, *args):
        os.chmod(locked, 0o555)
        try:
            return self.run_engine("--apply", *args)
        finally:
            os.chmod(locked, 0o755)

    @unittest.skipIf(RUNNING_AS_ROOT, "root ignores directory permissions")
    def test_read_only_target_then_writable_adds_everything(self):
        locked, target = self.locked_target({"model": "opus"})
        result = self.apply_while_read_only(locked)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Could not write", result.stderr)
        self.assertIn("finishes what this run started", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(load(target), {"model": "opus"})
        self.assertIs(load(self.config / RECORD)["pending"], True)

        result = self.ok("--apply")
        self.assertIn("stopped before it finished", result.stdout)
        self.assertNotIn("You removed these baseline items", result.stdout)
        data = load(target)
        self.assertEqual(data["model"], "opus")
        self.assert_full_baseline(data)
        self.assert_no_declines()
        self.assertIs(load(self.config / RECORD)["pending"], False)
        self.ok("--check")

    def test_unwritable_target_even_for_root_then_fixed(self):
        # A regular file where the settings directory should be makes the write fail for any user.
        blocker = self.root / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        self.settings.symlink_to(blocker / "settings.json")
        for _ in range(2):
            result = self.run_engine("--apply")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assert_no_declines()
        result = self.run_engine("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("stopped before it finished", result.stdout)

        blocker.unlink()
        blocker.mkdir()
        self.ok("--apply")
        self.assert_full_baseline(load(blocker / "settings.json"))
        self.assert_no_declines()

    def test_remove_after_a_failed_first_apply(self):
        blocker = self.root / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        self.settings.symlink_to(blocker / "settings.json")
        self.assertEqual(self.run_engine("--apply").returncode, 1)
        self.ok("--remove")
        self.assertFalse((self.config / "evisions").exists(), "record and copied script are gone")
        self.assertTrue(self.settings.is_symlink())

    @unittest.skipIf(RUNNING_AS_ROOT, "root ignores directory permissions")
    def test_remove_after_a_failed_upgrade_undoes_what_landed(self):
        user = {"model": "opus", "permissions": {"allow": ["Bash(foo *)"]}}
        locked, target = self.locked_target(user)
        older = self.custom_baseline(lambda d: d["permissions"]["deny"].append("Bash(oldtool *)"))
        self.ok("--apply", "--baseline", str(older))
        self.assertIn("Bash(oldtool *)", load(target)["permissions"]["deny"])
        self.assertEqual(self.apply_while_read_only(locked).returncode, 1)
        record = load(self.config / RECORD)
        self.assertIn("Bash(oldtool *)", record["added"]["permissions.deny"], "the pending record keeps old items")
        self.ok("--remove")
        self.assertEqual(load(target), user)


class RecordValidationTest(InstallerTestCase):
    """--remove and --apply act only on a record the installer could have written itself."""

    USER = {"model": "opus", "env": {"PATH": "/usr/bin"}, "permissions": {"allow": ["Bash(foo *)"]}}

    def valid_record(self):
        empty = {"permissions.allow": [], "permissions.deny": [], "permissions.ask": []}
        return {"baseline_version": VERSION, "profile": "standard", "added": dict(empty), "set": {},
                "declined": dict(empty), "files": [], "created": []}

    def assert_refused(self, change=None, raw=None):
        victim = self.root / "victim"
        victim.write_text("keep me", encoding="utf-8")
        self.write_settings(self.USER)
        (self.config / "evisions").mkdir()
        if raw is None:
            record = self.valid_record()
            change(record, victim)
            raw = json.dumps(record)
        (self.config / RECORD).write_text(raw, encoding="utf-8")
        before = snapshot(self.config)
        for mode in (["--remove"], ["--apply"], ["--check"], []):
            with self.subTest(mode=mode):
                result = self.run_engine(*mode)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("delete it and run evisions-settings --apply again", result.stderr)
                self.assertEqual(snapshot(self.config), before, "a refused run writes nothing")
                self.assertTrue(victim.exists(), "a file outside the installer's own must never be deleted")
        self.assertEqual(self.data(), self.USER)

    def test_file_outside_the_config_dir(self):
        self.assert_refused(lambda record, victim: record.update(files=["../victim"]))

    def test_absolute_file_path(self):
        self.assert_refused(lambda record, victim: record.update(files=[str(victim)]))

    def test_unknown_set_key(self):
        self.assert_refused(lambda record, victim: record.update(set={"model": "opus"}))

    def test_unknown_env_key(self):
        self.assert_refused(lambda record, victim: record.update(set={"env.PATH": "/usr/bin"}))

    def test_set_value_the_installer_never_writes(self):
        self.assert_refused(lambda record, victim: record.update(set={"cleanupPeriodDays": 0}))

    def test_status_line_with_foreign_keys(self):
        line = {"type": "command", "command": "x", "padding": 0}
        self.assert_refused(lambda record, victim: record.update(set={"statusLine": line}))

    def test_non_string_rule(self):
        self.assert_refused(lambda record, victim: record["added"].update({"permissions.allow": ["Bash(foo *)", 42]}))

    def test_rule_outside_the_grammar(self):
        self.assert_refused(lambda record, victim: record["added"].update({"permissions.deny": ["Bash(curl x | sh)"]}))

    def test_unknown_list_key(self):
        self.assert_refused(lambda record, victim: record["added"].update({"hooks": ["Bash(foo *)"]}))

    def test_unknown_declined_key(self):
        self.assert_refused(lambda record, victim: record["declined"].update({"model": "opus"}))

    def test_unknown_created_container(self):
        self.assert_refused(lambda record, victim: record.update(created=["model"]))

    def test_pending_not_a_boolean(self):
        self.assert_refused(lambda record, victim: record.update(pending="yes"))

    def test_corrupt_json(self):
        self.assert_refused(raw="{")

    def test_record_from_the_previous_version_is_accepted(self):
        self.ok("--apply")
        record = load(self.config / RECORD)
        for key in ("pending", "created_settings_file", "declined"):
            record.pop(key)
        (self.config / RECORD).write_text(json.dumps(record), encoding="utf-8")
        result = self.ok("--apply")
        self.assertIn("Nothing to change", result.stdout)
        self.ok("--remove")


@unittest.skipUnless(shutil.which("bash"), "bash is required to run the wrapper")
class WrapperTest(InstallerTestCase):
    def run_wrapper(self, executable):
        return subprocess.run(
            ["bash", str(executable), "--config-dir", str(self.config), "--managed-dir", str(self.no_policy)],
            capture_output=True, text=True, env=environment(), timeout=60,
        )

    def test_wrapper_runs_the_dry_run(self):
        result = self.run_wrapper(WRAPPER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was written", result.stdout)
        self.assertEqual(list(self.config.iterdir()), [])

    def test_wrapper_works_through_a_symlink(self):
        link = self.root / "evisions-settings"
        link.symlink_to(WRAPPER)
        result = self.run_wrapper(link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was written", result.stdout)

    def test_wrapper_is_executable_with_bash_shebang(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        raw = WRAPPER.read_bytes()
        self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r", raw)

    def test_wrapper_without_python_fails_clearly(self):
        # A PATH holding only the tools the wrapper needs besides Python.
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("dirname", "readlink"):
            found = shutil.which(name)
            if found:
                (tools / name).symlink_to(found)
        env = environment()
        env["PATH"] = str(tools)
        result = subprocess.run(
            [shutil.which("bash"), str(WRAPPER)], capture_output=True, text=True, env=env, timeout=60
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer", result.stderr)

    def test_wrapper_requires_python_3_9(self):
        self.assertIn("sys.version_info >= (3, 9)", WRAPPER.read_text(encoding="utf-8"))


class OldPythonTest(InstallerTestCase):
    def test_engine_refuses_python_older_than_3_9(self):
        # Simulate an older interpreter: the guard reads sys.version_info before any other code runs.
        program = (
            "import runpy, sys\n"
            "sys.version_info = (3, 8, 18, 'final', 0)\n"
            f"sys.argv = ['install_settings.py', '--apply', '--config-dir', {str(self.config)!r}]\n"
            f"runpy.run_path({str(ENGINE)!r}, run_name='__main__')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, env=environment(), timeout=60
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer, and this is Python 3.8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.config.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
