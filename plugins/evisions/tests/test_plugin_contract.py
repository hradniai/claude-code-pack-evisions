"""Contract tests for the evisions plugin and the marketplace repository that ships it.

Run from this directory with `python3 -m unittest`. Standard library only. Every missing or
malformed file is reported as its own failure (one subtest each), never as a crash.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
THIS_FILE = Path(__file__).resolve()

MARKETPLACE_NAME = "claude-code-pack-evisions"
PLUGIN_NAME = "evisions"

SKILLS = (
    "checkpoint",
    "end",
    "adr",
    "prompt-eval",
    "research",
    "socratic-brainstormer",
    "prd",
    "bug-log",
    "tech-debt-log",
    "skill-scanner",
)
AGENTS = (
    "features-documenter",
    "prompt-engineer",
    "research-analyst",
    "research-lead",
    "code-reviewer",
    "security-auditor",
)
# Scripts Claude Code or the user runs directly: each needs a shebang and the executable bit. The
# hooks are started through `bash <script>` so a lost bit on Windows does not disable them, but a git
# install on macOS and Linux keeps the bit, and `bin/` entries are only found on PATH with it.
EXECUTABLES = (
    "hooks/session-start",
    "hooks/safety-start",
    "hooks/current-time",
    "hooks/run-python",
    "bin/list-env-keys",
    "bin/evisions-settings",
)
REQUIRED_FILES = (
    *(f"skills/{skill}/SKILL.md" for skill in SKILLS),
    "skills/adr/template.md",
    "skills/prompt-eval/scripts/sanitize_prompt.py",
    "skills/prd/references/prd-template.md",
    "skills/prd/references/eu-compliance-checklist.md",
    "skills/prd/references/auth-strategy-decision-tree.md",
    "skills/prd/references/threat-model-template.md",
    "skills/skill-scanner/scripts/scan.py",
    "skills/skill-scanner/references/review-contract.md",
    *(f"agents/{agent}.md" for agent in AGENTS),
    "hooks/hooks.json",
    *EXECUTABLES,
    "hooks/bash_safety.py",
    "scripts/env_key_classify.py",
    "scripts/install_settings.py",
    "scripts/statusline.py",
    "settings/baseline.json",
    "context/documentation-standard.md",
    "context/kit-map.md",
    "context/safety.md",
)
# Every hook the plugin must register: (event, matcher that must be covered or None, script name).
REQUIRED_HOOKS = (
    ("SessionStart", None, "session-start"),
    ("SessionStart", None, "session-start\" kit-map"),
    ("SessionStart", None, "safety-start"),
    ("UserPromptSubmit", None, "current-time"),
    ("PreToolUse", ("Bash", "PowerShell", "Read", "Grep"), "bash_safety.py"),
)

# Skills carry name and description (plus disable-model-invocation where needed), agents carry name,
# description, tools and model. Plugin agents ignore hooks, mcpServers and permissionMode, and a
# maintainer's own bookkeeping keys must not travel into the plugin.
SKILL_KEYS = {"name", "description", "disable-model-invocation"}
AGENT_KEYS = {"name", "description", "tools", "model"}
FORBIDDEN_KEYS = {"metadata", "machines", "owner", "path", "hooks", "mcpServers", "permissionMode"}

# Public leak markers: the maintainer's name, which belongs only in LICENSE and the manifests, and
# tools that do not exist on the users' machines, so a mention would send Claude after nothing. This
# file holds the patterns, so the public scan skips it.
LEAK_PATTERNS = {
    "maintainer first name": re.compile(r"[sš]imon", re.IGNORECASE),
    "maintainer surname": re.compile(r"hradn[ií]", re.IGNORECASE),
    "tool that does not ship": re.compile(r"SendMessage|research-engine|idea-file-creator|round-table|PROMPT-EVAL"),
}

# Names that must never appear in this public repository cannot be listed in it either. They live
# in an optional file outside the repository, named by this environment variable: one regex per line,
# `#` comments and blank lines ignored, each matched case-insensitively. Unset, the public subset runs.
PRIVATE_PATTERNS_VARIABLE = "EVISIONS_PRIVATE_LEAK_PATTERNS"

# Exact strings allowed in exactly these files, relative to the repository root. The GitHub slug
# is the repository's own address.
REPO_SLUG = "hradniai/claude-code-pack-evisions"
LEAK_ALLOWLIST = {
    "LICENSE": ("Šimon Hradní", "simon@hradni.net"),
    "README.md": (REPO_SLUG,),
    "INSTRUCTIONS.md": (REPO_SLUG,),
    "USER-MANUAL.md": (REPO_SLUG,),
}
# Optional companion plugins may be named only where the kit routes to them or tells the user how to
# install them; anywhere else a mention makes Claude reach for a plugin that may be absent.
COMPANION_NAMES = re.compile(r"superpowers|\breplan\b|mattpocock", re.IGNORECASE)
COMPANION_FILES = {
    "plugins/evisions/skills/socratic-brainstormer/SKILL.md",
    "plugins/evisions/context/kit-map.md",
    "README.md",
    "INSTRUCTIONS.md",
    "USER-MANUAL.md",
}
# The two manifests may name "Hradni.AI" as marketplace owner and plugin author, nowhere else.
MANIFEST_AUTHOR_FIELDS = {
    ".claude-plugin/marketplace.json": "owner",
    "plugins/evisions/.claude-plugin/plugin.json": "author",
}
MANIFEST_AUTHOR = "Hradni.AI"

# The client's name belongs in the documents written for its people, not in the plugin itself.
COMPANY_NAME = re.compile(r"eVisions|EVISIONS")
COMPANY_NAME_FILES = {
    "README.md",
    "INSTRUCTIONS.md",
    "USER-MANUAL.md",
    "LICENSE",
    ".claude-plugin/marketplace.json",
    "plugins/evisions/.claude-plugin/plugin.json",
}

FORBIDDEN_DASHES = {"\u2014": "U+2014 em dash", "\u2015": "U+2015 horizontal bar"}
NAMESPACED_REFERENCE = re.compile(r"(?<![\w-])evisions:([a-z0-9]+(?:-[a-z0-9]+)*)")
BLOCK_SCALAR = {">", "|", ">-", "|-", ">+", "|+"}


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def repo_files() -> list[Path]:
    """Every file in the repository except git internals, caches and env files (never read)."""
    files = []
    for directory, subdirectories, names in os.walk(REPO_ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in {".git", "__pycache__"}]
        for name in names:
            path = Path(directory) / name
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            if name.startswith(".env") and name != ".env.example":
                continue
            if "evals/results" in relative(path):
                continue
            files.append(path)
    return sorted(files)


def text_files() -> dict[Path, str]:
    """Decodable files with their text; anything that is not UTF-8 is treated as binary."""
    texts = {}
    for path in repo_files():
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return texts


def load_private_patterns() -> tuple[dict[str, re.Pattern[str]], str | None]:
    """Patterns from the optional private file, plus an error when the file is named but unusable."""
    location = os.environ.get(PRIVATE_PATTERNS_VARIABLE)
    if not location:
        return {}, None
    path = Path(location).expanduser()
    if not path.is_file():
        return {}, f"{PRIVATE_PATTERNS_VARIABLE} names {location}, which is not a readable file"
    patterns = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        try:
            patterns[f"private pattern, line {number}"] = re.compile(entry, re.IGNORECASE)
        except re.error as error:
            return {}, f"line {number} of {location} is not a valid regex: {error}"
    if not patterns:
        return {}, f"{location} holds no patterns"
    return patterns, None


PRIVATE_PATTERNS, PRIVATE_PATTERNS_ERROR = load_private_patterns()


def load_json(path: Path) -> tuple[object | None, str | None]:
    if not path.is_file():
        return None, f"missing: {relative(path)}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as error:
        return None, f"invalid JSON in {relative(path)}: {error}"


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_frontmatter(path: Path) -> dict[str, str] | None:
    """Top-level keys of the leading YAML frontmatter block, or None when there is no closed block.

    A tiny line parser, enough for flat `key: value` blocks. Folded (`>`), literal (`|`) and list
    values are joined from their indented continuation lines.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            fields[key] = "" if value in BLOCK_SCALAR else unquote(value)
        elif key and line[:1] in {" ", "\t"}:
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return None


class ManifestTests(unittest.TestCase):
    def test_marketplace_manifest_names(self) -> None:
        data, error = load_json(MARKETPLACE_MANIFEST)
        if error:
            self.fail(error)
        self.assertEqual(data.get("name"), MARKETPLACE_NAME)
        entries = [entry for entry in data.get("plugins", []) if entry.get("name") == PLUGIN_NAME]
        self.assertEqual(len(entries), 1, f"marketplace must list exactly one plugin named {PLUGIN_NAME!r}")

    def test_plugin_manifest_name(self) -> None:
        data, error = load_json(PLUGIN_MANIFEST)
        if error:
            self.fail(error)
        self.assertEqual(data.get("name"), PLUGIN_NAME)

    def test_versions_agree(self) -> None:
        marketplace, error = load_json(MARKETPLACE_MANIFEST)
        if error:
            self.fail(error)
        plugin, error = load_json(PLUGIN_MANIFEST)
        if error:
            self.fail(error)
        entry = next((item for item in marketplace.get("plugins", []) if item.get("name") == PLUGIN_NAME), {})
        self.assertTrue(plugin.get("version"), "plugin.json carries no version")
        self.assertEqual(entry.get("version"), plugin.get("version"), "marketplace entry and plugin.json disagree on version")

    def test_marketplace_source_resolves_to_plugin_root(self) -> None:
        data, error = load_json(MARKETPLACE_MANIFEST)
        if error:
            self.fail(error)
        entry = next((item for item in data.get("plugins", []) if item.get("name") == PLUGIN_NAME), {})
        source = entry.get("source")
        self.assertIsInstance(source, str, "plugin source must be a relative path string")
        self.assertTrue(source.startswith("./"), "a relative plugin source must start with ./")
        self.assertEqual((REPO_ROOT / source).resolve(), PLUGIN_ROOT.resolve())


class LayoutTests(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        for path in REQUIRED_FILES:
            with self.subTest(path=path):
                self.assertTrue((PLUGIN_ROOT / path).is_file(), f"missing: plugins/evisions/{path}")

    def test_no_skill_or_agent_outside_the_spec(self) -> None:
        skills_dir, agents_dir = PLUGIN_ROOT / "skills", PLUGIN_ROOT / "agents"
        found_skills = {path.name for path in skills_dir.iterdir() if path.is_dir()} if skills_dir.is_dir() else set()
        found_agents = {path.stem for path in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()
        self.assertEqual(found_skills - set(SKILLS), set(), "skill folders not in the spec layout")
        self.assertEqual(found_agents - set(AGENTS), set(), "agent files not in the spec layout")

    def test_scripts_are_executable_with_shebang(self) -> None:
        for name in EXECUTABLES:
            script = PLUGIN_ROOT / name
            with self.subTest(script=name):
                if not script.is_file():
                    self.skipTest(f"{name} is missing; reported by test_required_files_exist")
                self.assertTrue(os.access(script, os.X_OK), f"{name} is not executable")
                self.assertTrue(script.read_text(encoding="utf-8").startswith("#!"), f"{name} has no shebang")

    def test_hooks_json_registers_every_hook(self) -> None:
        path = PLUGIN_ROOT / "hooks" / "hooks.json"
        if not path.is_file():
            self.skipTest("hooks/hooks.json is missing; reported by test_required_files_exist")
        data, error = load_json(path)
        if error:
            self.fail(error)
        for event, tools, script in REQUIRED_HOOKS:
            with self.subTest(event=event, script=script):
                groups = data.get("hooks", {}).get(event, [])
                matching = [
                    group
                    for group in groups
                    if any(script in hook.get("command", "") for hook in group.get("hooks", []))
                ]
                self.assertTrue(matching, f"no {event} hook runs {script}")
                if tools:
                    matchers = {name for group in matching for name in group.get("matcher", "").split("|")}
                    self.assertEqual(sorted(set(tools) - matchers), [], f"{script} does not cover these tools")

    def test_hook_commands_quote_the_plugin_root(self) -> None:
        # Claude Code 2.1.281 warns on an unquoted ${CLAUDE_PLUGIN_ROOT}, and a path with a space
        # (a Windows user folder) would split into two arguments.
        path = PLUGIN_ROOT / "hooks" / "hooks.json"
        data, error = load_json(path)
        if error:
            self.fail(error)
        for event, groups in data.get("hooks", {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    with self.subTest(event=event, command=command):
                        unquoted = re.search(r'(?<!")\$\{CLAUDE_PLUGIN_ROOT\}', command)
                        self.assertIsNone(unquoted, "unquoted ${CLAUDE_PLUGIN_ROOT}")


class BaselineTests(unittest.TestCase):
    """The settings baseline is merged into a user's settings.json, where one invalid value voids the
    whole file without a message in headless runs, so its shape is checked here as well as by the
    installer's own tests."""

    def load_baseline(self) -> dict:
        data, error = load_json(PLUGIN_ROOT / "settings" / "baseline.json")
        if error:
            self.fail(error)
        return data

    def rules(self, data: object) -> list[str]:
        found = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key in {"allow", "deny", "ask"} and isinstance(value, list):
                    found.extend(item for item in value if isinstance(item, str))
                else:
                    found.extend(self.rules(value))
        return found

    def test_no_rule_that_never_matches_or_is_skipped(self) -> None:
        for rule in self.rules(self.load_baseline()):
            with self.subTest(rule=rule):
                self.assertNotIn("|", rule, "rules are matched per subcommand, so a pipe never matches")
                self.assertFalse(rule.startswith("Task"), "an unanchored tool-name glob is skipped at load")
                self.assertFalse(rule.startswith("Write("), "Write(path) rules are dead; Edit(path) gates every edit tool")

    def test_no_value_that_voids_the_settings_file(self) -> None:
        text = (PLUGIN_ROOT / "settings" / "baseline.json").read_text(encoding="utf-8")
        self.assertNotRegex(text, r'"cleanupPeriodDays"\s*:\s*0\b', "cleanupPeriodDays 0 fails validation")
        self.assertNotIn('"attribution"', text, "attribution: false makes older CLIs skip the whole file")


class FrontmatterTests(unittest.TestCase):
    def component_files(self) -> list[tuple[str, str, Path]]:
        """(kind, expected name, path) for every spec component plus any stray one on disk."""
        skills = set(SKILLS)
        agents = set(AGENTS)
        if (PLUGIN_ROOT / "skills").is_dir():
            skills |= {path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")}
        if (PLUGIN_ROOT / "agents").is_dir():
            agents |= {path.stem for path in (PLUGIN_ROOT / "agents").glob("*.md")}
        return [("skill", name, PLUGIN_ROOT / "skills" / name / "SKILL.md") for name in sorted(skills)] + [
            ("agent", name, PLUGIN_ROOT / "agents" / f"{name}.md") for name in sorted(agents)
        ]

    def frontmatter_or_skip(self, path: Path) -> dict[str, str]:
        if not path.is_file():
            self.skipTest(f"{relative(path)} is missing; reported by test_required_files_exist")
        fields = read_frontmatter(path)
        if fields is None:
            self.fail(f"{relative(path)} does not start with a closed YAML frontmatter block")
        return fields

    def test_name_and_description(self) -> None:
        for kind, name, path in self.component_files():
            with self.subTest(component=relative(path)):
                fields = self.frontmatter_or_skip(path)
                self.assertEqual(fields.get("name"), name, f"{kind} name must equal its {'folder' if kind == 'skill' else 'file'} name")
                self.assertTrue(fields.get("description"), "description is empty or missing")

    def test_agents_declare_tools_and_model(self) -> None:
        for kind, _, path in self.component_files():
            if kind != "agent":
                continue
            with self.subTest(component=relative(path)):
                fields = self.frontmatter_or_skip(path)
                self.assertTrue(fields.get("tools"), "agent declares no tools")
                self.assertTrue(fields.get("model"), "agent declares no model")

    def test_no_forbidden_keys(self) -> None:
        for _, _, path in self.component_files():
            with self.subTest(component=relative(path)):
                fields = self.frontmatter_or_skip(path)
                self.assertEqual(sorted(FORBIDDEN_KEYS & fields.keys()), [], "forbidden frontmatter keys")

    def test_keys_stay_within_the_allow_list(self) -> None:
        for kind, _, path in self.component_files():
            with self.subTest(component=relative(path)):
                fields = self.frontmatter_or_skip(path)
                allowed = SKILL_KEYS if kind == "skill" else AGENT_KEYS
                self.assertEqual(sorted(fields.keys() - allowed), [], f"keys outside the {kind} allow-list")


class CrossReferenceTests(unittest.TestCase):
    def test_namespaced_references_resolve(self) -> None:
        known = set()
        if (PLUGIN_ROOT / "skills").is_dir():
            known |= {path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")}
        if (PLUGIN_ROOT / "agents").is_dir():
            known |= {path.stem for path in (PLUGIN_ROOT / "agents").glob("*.md")}
        for path, text in text_files().items():
            if path.suffix != ".md":
                continue
            unresolved = sorted({name for name in NAMESPACED_REFERENCE.findall(text) if name not in known})
            with self.subTest(file=relative(path)):
                self.assertEqual(unresolved, [], "evisions:<name> references with no matching skill or agent")


class HygieneTests(unittest.TestCase):
    def scannable_text(self, path: Path, text: str) -> str:
        """The text minus the narrow, explicit exceptions for this file."""
        name = relative(path)
        field = MANIFEST_AUTHOR_FIELDS.get(name)
        if field:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(data.get(field), dict) and data[field].get("name") == MANIFEST_AUTHOR:
                data[field]["name"] = ""
            return json.dumps(data, ensure_ascii=False)
        for allowed in LEAK_ALLOWLIST.get(name, ()):
            text = text.replace(allowed, "")
        return text

    def test_private_patterns_file(self) -> None:
        if PRIVATE_PATTERNS_ERROR:
            self.fail(PRIVATE_PATTERNS_ERROR)
        if not PRIVATE_PATTERNS:
            sys.stderr.write(f"\nnote: {PRIVATE_PATTERNS_VARIABLE} is unset; the leak scan ran the public patterns only\n")

    def test_no_identity_or_private_tool_leaks(self) -> None:
        for path, text in text_files().items():
            scannable = self.scannable_text(path, text)
            if path == THIS_FILE:
                patterns = dict(PRIVATE_PATTERNS)
            else:
                patterns = {**LEAK_PATTERNS, **PRIVATE_PATTERNS}
                if relative(path) not in COMPANION_FILES:
                    patterns["companion plugin outside the routing files"] = COMPANION_NAMES
            with self.subTest(file=relative(path)):
                hits = []
                for label, pattern in patterns.items():
                    for match in pattern.finditer(scannable):
                        line = scannable.count("\n", 0, match.start()) + 1
                        hits.append(f"line {line}: {match.group(0)!r} ({label})")
                self.assertEqual(hits, [], "identity or private-tool leak")

    def test_company_name_only_in_repo_documents(self) -> None:
        for path, text in text_files().items():
            if path == THIS_FILE or relative(path) in COMPANY_NAME_FILES:
                continue
            with self.subTest(file=relative(path)):
                lines = sorted({text.count("\n", 0, match.start()) + 1 for match in COMPANY_NAME.finditer(text)})
                self.assertEqual(lines, [], "company name outside README, manual, INSTRUCTIONS, LICENSE and manifests")

    def test_no_em_dash_or_horizontal_bar(self) -> None:
        for path, text in text_files().items():
            with self.subTest(file=relative(path)):
                hits = [
                    f"line {text.count(chr(10), 0, index) + 1}: {label}"
                    for character, label in FORBIDDEN_DASHES.items()
                    for index in [match.start() for match in re.finditer(character, text)]
                ]
                self.assertEqual(hits, [], "forbidden dash characters")


if __name__ == "__main__":
    unittest.main()
