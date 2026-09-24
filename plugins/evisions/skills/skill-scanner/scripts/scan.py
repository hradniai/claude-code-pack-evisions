#!/usr/bin/env python3
"""Read-only, dependency-free security scanner for AI agent skills and plugins."""

from __future__ import annotations

import argparse
import ast
import base64
import bisect
import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable


VERSION = "0.3.0"
SCHEMA_VERSION = "1.1"
MAX_FILES = 10_000
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_RATIO = 100
MAX_DIRECTORY_DEPTH = 64
MAX_FINDINGS_PER_RULE_PER_FILE = 20
EVIDENCE_LIMIT = 180

EXIT_PERMIT = 0
EXIT_ERROR = 2
EXIT_REVIEW = 10
EXIT_BLOCK = 20

DECISION_RANK = {"inform": 0, "review": 1, "block": 2}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

TEXT_EXTENSIONS = {
    ".md", ".mdx", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".xml", ".html", ".htm", ".css", ".csv", ".ini", ".cfg", ".conf", ".sh",
    ".bash", ".zsh", ".fish", ".ps1", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".mjs", ".cjs", ".rb", ".php", ".go", ".rs", ".java", ".kt", ".sql",
    ".lock", ".properties", ".dockerfile", ".bat", ".cmd", ".psm1", ".vbs",
    ".tf", ".tfvars", ".gradle", ".applescript", ".scpt", ".command", ".pl", ".lua",
}
SCRIPT_EXTENSIONS = {
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".py", ".js", ".mjs", ".cjs",
    ".ts", ".rb", ".php", ".pl", ".lua", ".bat", ".cmd", ".vbs", ".applescript",
    ".scpt", ".command",
}
REGEX_SCRIPT_EXTENSIONS = SCRIPT_EXTENSIONS - {".py"}
PROSE_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}
EXECUTABLE_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".appimage", ".class", ".jar", ".wasm",
    ".pyc", ".pyo",
}
ARCHIVE_EXTENSIONS = {
    ".zip", ".skill", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".whl", ".jar", ".docx", ".xlsx", ".pptx",
}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
PROTECTED_EXACT = {
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", "credentials",
    "credentials.json", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "secrets.yaml",
    "secrets.yml", "secrets.json",
}
PROTECTED_PARTS = {".ssh", ".aws", ".gnupg", ".kube"}
PROTECTED_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SAFE_ENV_DOC_SUFFIXES = {".example", ".sample", ".template", ".shared"}

BIDI_AND_INVISIBLE = {
    0x00AD, 0x061C, 0x180E,
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
    0xFEFF, 0x115F, 0x1160, 0x3164, 0xFFA0,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}

# Unicode Tag Characters carry a full ASCII alphabet that renders as nothing at
# all. Deprecated for language tagging and with no remaining legitimate use in
# source or documentation, so an occurrence is a payload rather than a quirk.
TAG_CHARACTER_RANGE = range(0xE0000, 0xE0080)

# Variation selectors legitimately follow emoji and CJK ideographs, so a run of
# them carrying an encoded message is what distinguishes an attack from normal
# text; a lone selector after a pictograph is not reportable.
VARIATION_SELECTORS = {*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)}
MAX_BENIGN_VARIATION_RUN = 4

PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
URL_USERINFO_RE = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*[\"']?bearer\s+)([^\s\"',}]+)")
AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*[\"']?(?:basic|digest|token|apikey)\s+)([^\s\"',}]+)"
)
COOKIE_RE = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)([^\r\n]+)")
CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)((?:[A-Z0-9_.-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|PRIVATE[_-]?KEY)"
    r"[A-Z0-9_.-]*)\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{6,})"
)
KNOWN_SECRET_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|npm_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{35}|ya29\.[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


class ScanError(Exception):
    """Scanner input or processing failure."""


@dataclass
class ArtifactFile:
    path: str
    kind: str
    size: int
    mode: int
    sha256: str | None
    analyzed: bool
    note: str | None = None
    content: bytes | None = None

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mode": oct(self.mode),
            "sha256": self.sha256,
            "analyzed": self.analyzed,
            "note": self.note,
        }


@dataclass
class Finding:
    rule_id: str
    severity: str
    confidence: str
    category: str
    decision: str
    path: str
    line: int | None
    evidence: str
    message: str
    artifact_sha256: str | None
    engine: str = "deterministic"

    def fingerprint(self) -> str:
        stable = "\0".join(
            [self.rule_id, self.path, str(self.line or 0), normalize_evidence(self.evidence)]
        )
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["fingerprint"] = self.fingerprint()
        return result


def normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def redact_evidence(value: str) -> str:
    value = value.replace("\x00", "\\0")
    value = PRIVATE_KEY_RE.sub("<redacted:private-key-material>", value)
    value = URL_USERINFO_RE.sub(r"\1<redacted:url-credentials>@", value)
    value = BEARER_RE.sub(r"\1<redacted>", value)
    value = AUTHORIZATION_RE.sub(r"\1<redacted>", value)
    value = COOKIE_RE.sub(r"\1<redacted>", value)
    value = CREDENTIAL_VALUE_RE.sub(r"\1<redacted>", value)
    value = KNOWN_SECRET_RE.sub("<redacted:possible-secret>", value)
    value = value.strip().replace("\t", " ")
    if len(value) > EVIDENCE_LIMIT:
        return value[: EVIDENCE_LIMIT - 3] + "..."
    return value


def is_protected_path(logical_path: str) -> bool:
    path = PurePosixPath(logical_path)
    name = path.name.lower()
    if any(part.lower() in PROTECTED_PARTS for part in path.parts):
        return True
    if name in PROTECTED_EXACT:
        return True
    if path.suffix.lower() in PROTECTED_SUFFIXES:
        return True
    if name.startswith(".env."):
        suffix = name[len(".env") :]
        return suffix not in SAFE_ENV_DOC_SUFFIXES
    return False


def safe_archive_path(name: str) -> bool:
    if not name or "\x00" in name or name.startswith(("/", "\\")):
        return False
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if any(part == ".." for part in path.parts):
        return False
    return not bool(re.match(r"^[A-Za-z]:", normalized))


def symlink_escapes(link_path: str, target: str) -> bool:
    if not target or target.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", target):
        return True
    base = PurePosixPath(link_path).parent
    depth = 0
    for part in (base / target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
        else:
            depth += 1
        if depth < 0:
            return True
    return False


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def artifact_digest(files: Iterable[ArtifactFile]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value.path):
        row = "\0".join(
            [
                item.path, item.kind, str(item.size), oct(item.mode), item.sha256 or "UNREAD",
                item.note or "",
            ]
        )
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


class FindingList(list):
    """A findings list that deduplicates and caps in constant time per insertion.

    Scanning both deduplicates findings and limits how many times one rule may
    report against one file. Doing either by walking the accumulated list makes
    insertion linear, so a large artifact with many matches costs time quadratic
    in the number of findings.
    """

    def __init__(self) -> None:
        super().__init__()
        self.seen: set[tuple[str, str, int | None, str]] = set()
        self.rule_counts: dict[tuple[str, str], int] = {}


def add_finding(
    findings: list[Finding],
    item: ArtifactFile | None,
    rule_id: str,
    severity: str,
    confidence: str,
    category: str,
    decision: str,
    message: str,
    evidence: str,
    line: int | None = None,
    path: str | None = None,
) -> None:
    finding = Finding(
        rule_id=rule_id,
        severity=severity,
        confidence=confidence,
        category=category,
        decision=decision,
        path=path if path is not None else (item.path if item else "."),
        line=line,
        evidence=redact_evidence(evidence),
        message=message,
        artifact_sha256=item.sha256 if item else None,
    )
    key = (finding.rule_id, finding.path, finding.line, finding.evidence)
    seen = getattr(findings, "seen", None)
    if seen is None:
        if any((f.rule_id, f.path, f.line, f.evidence) == key for f in findings):
            return
    elif key in seen:
        return
    else:
        seen.add(key)
    counts = getattr(findings, "rule_counts", None)
    if counts is not None:
        rule_key = (finding.rule_id, finding.path)
        count = counts.get(rule_key, 0)
        counts[rule_key] = count + 1
        if count == MAX_FINDINGS_PER_RULE_PER_FILE:
            findings.append(
                Finding(
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    category=finding.category,
                    decision=finding.decision,
                    path=finding.path,
                    line=None,
                    evidence=f"further {finding.rule_id} matches suppressed",
                    message=(
                        f"{finding.rule_id} matched more than "
                        f"{MAX_FINDINGS_PER_RULE_PER_FILE} times in this file; "
                        "remaining matches are not listed individually."
                    ),
                    artifact_sha256=finding.artifact_sha256,
                )
            )
            return
        if count > MAX_FINDINGS_PER_RULE_PER_FILE:
            return
    findings.append(finding)


def reject_broad_target(target: Path) -> None:
    resolved = target.resolve(strict=True)
    broad = {
        Path("/").resolve(),
        Path.home().resolve(),
        (Path.home() / ".claude").resolve(),
        Path("/etc").resolve(),
        Path("/home").resolve(),
        Path("/root").resolve(),
        Path("/usr").resolve(),
        Path("/var").resolve(),
    }
    if resolved in broad:
        raise ScanError(f"refusing broad target: {resolved}")


def protected_item(
    logical: str, size: int, mode: int, findings: list[Finding], message: str | None = None
) -> ArtifactFile:
    item = ArtifactFile(logical, "protected", size, mode, None, False, "content not read")
    add_finding(
        findings, item, "AIS-DATA-001", "critical", "high", "sensitive-data", "block",
        message or "Protected credential material is shipped in the artifact; content was not read.",
        logical,
    )
    return item


def changed_item(
    logical: str, size: int, mode: int, findings: list[Finding], evidence: str
) -> ArtifactFile:
    item = ArtifactFile(logical, "changed-during-scan", size, mode, None, False, evidence)
    add_finding(
        findings, item, "AIS-INTAKE-008", "critical", "high", "intake-race", "block",
        "Artifact entry changed while it was being opened or read.", evidence,
    )
    return item


def stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size)


def stat_stable(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat_identity(before) == stat_identity(after)
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def hash_descriptor(fd: int) -> tuple[str | None, int, os.stat_result]:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SOURCE_BYTES:
            return None, total, os.fstat(fd)
        digest.update(chunk)
    return digest.hexdigest(), total, os.fstat(fd)


def read_fd(fd: int, expected: os.stat_result) -> tuple[bytes, str, os.stat_result, bool]:
    digest = hashlib.sha256()
    stored = bytearray()
    total = 0
    overflow = False
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_TOTAL_BYTES:
            overflow = True
            break
        digest.update(chunk)
        if total <= MAX_FILE_BYTES:
            stored.extend(chunk)
        else:
            stored.clear()
    final = os.fstat(fd)
    return bytes(stored), digest.hexdigest(), final, overflow


def read_regular_at(
    parent_fd: int,
    name: str,
    logical: str,
    expected: os.stat_result,
    findings: list[Finding],
) -> ArtifactFile:
    mode = stat.S_IMODE(expected.st_mode)
    if is_protected_path(logical):
        return protected_item(logical, expected.st_size, mode, findings)
    if expected.st_nlink > 1:
        item = ArtifactFile(logical, "hardlink", expected.st_size, mode, None, False, "content not read")
        add_finding(
            findings, item, "AIS-PATH-005", "critical", "high", "path-safety", "block",
            "Regular file has multiple hard links; content was not read.", f"link_count={expected.st_nlink}",
        )
        return item
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        return changed_item(logical, expected.st_size, mode, findings, str(error))
    try:
        opened = os.fstat(fd)
        if stat_identity(opened) != stat_identity(expected):
            return changed_item(logical, expected.st_size, mode, findings, "inode or size changed")
        content, digest, final, overflow = read_fd(fd, opened)
        if overflow:
            return changed_item(
                logical, final.st_size, stat.S_IMODE(final.st_mode), findings,
                "file exceeded the read budget during scan",
            )
        if not stat_stable(opened, final):
            return changed_item(logical, final.st_size, stat.S_IMODE(final.st_mode), findings, "file changed during read")
    finally:
        os.close(fd)
    if opened.st_size > MAX_FILE_BYTES:
        item = ArtifactFile(
            logical, "oversized", opened.st_size, stat.S_IMODE(opened.st_mode), digest, False,
            "file limit exceeded",
        )
        add_finding(
            findings, item, "AIS-INTAKE-003", "high", "high", "analyzability", "block",
            f"File exceeds the {MAX_FILE_BYTES}-byte analysis limit.", str(opened.st_size),
        )
        return item
    return ArtifactFile(
        logical, "file", opened.st_size, stat.S_IMODE(opened.st_mode), digest, True, content=content
    )


def top_level_symlink(path: Path, st: os.stat_result) -> tuple[list[ArtifactFile], list[Finding], str, None]:
    findings: list[Finding] = FindingList()
    link_target = os.readlink(path)
    item = ArtifactFile(path.name, "symlink", 0, stat.S_IMODE(st.st_mode), None, False, link_target)
    add_finding(
        findings, item, "AIS-PATH-002", "critical", "high", "path-safety", "block",
        "A top-level scan target may not be a symlink.", link_target,
    )
    return [item], findings, "symlink", None


def load_directory(target: Path) -> tuple[list[ArtifactFile], list[Finding], str, None]:
    reject_broad_target(target)
    findings: list[Finding] = FindingList()
    files: list[ArtifactFile] = []
    state = {"count": 0, "total": 0, "stop": False}
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(target, root_flags)
    except OSError as error:
        raise ScanError(f"cannot open directory without following links: {target}: {error}") from error

    def walk_fd(directory_fd: int, prefix: PurePosixPath, depth: int = 0) -> None:
        if depth > MAX_DIRECTORY_DEPTH:
            # Recursive traversal past the interpreter's own recursion limit
            # would abort the scan with a traceback and an exit code outside the
            # documented contract, which reads as a tool failure rather than a
            # refusal to admit the artifact.
            add_finding(
                findings, None, "AIS-INTAKE-012", "high", "high", "analyzability", "block",
                f"Artifact nests directories deeper than the {MAX_DIRECTORY_DEPTH}-level limit.",
                prefix.as_posix() or ".", path=prefix.as_posix() or ".",
            )
            state["stop"] = True
            return
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as error:
            add_finding(
                findings, None, "AIS-INTAKE-007", "high", "high", "analyzability", "block",
                "Directory could not be enumerated safely.", str(error), path=prefix.as_posix() or ".",
            )
            state["stop"] = True
            return
        for name in names:
            if state["stop"]:
                return
            logical_path = prefix / name
            logical = logical_path.as_posix()
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                files.append(changed_item(logical, 0, 0, findings, str(error)))
                continue
            if name == ".git" and stat.S_ISDIR(entry_stat.st_mode):
                add_finding(
                    findings, None, "AIS-INTAKE-006", "info", "high", "provenance", "inform",
                    "Git metadata was excluded from the artifact payload scan.", logical, path=logical,
                )
                continue
            state["count"] += 1
            if state["count"] > MAX_FILES:
                add_finding(
                    findings, None, "AIS-INTAKE-001", "high", "high", "analyzability", "block",
                    f"Artifact exceeds the {MAX_FILES}-entry limit.", str(state["count"]), path=logical,
                )
                state["stop"] = True
                return
            mode = stat.S_IMODE(entry_stat.st_mode)
            if stat.S_ISLNK(entry_stat.st_mode):
                try:
                    link_target = os.readlink(name, dir_fd=directory_fd)
                except OSError as error:
                    link_target = f"unreadable link: {error}"
                item = ArtifactFile(logical, "symlink", 0, mode, None, False, link_target)
                files.append(item)
                decision = "block" if symlink_escapes(logical, link_target) else "review"
                add_finding(
                    findings, item, "AIS-PATH-001", "critical" if decision == "block" else "medium",
                    "high", "path-safety", decision, "Symlink is not followed.", link_target,
                )
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError as error:
                    files.append(changed_item(logical, 0, mode, findings, str(error)))
                    continue
                try:
                    if stat_identity(os.fstat(child_fd)) != stat_identity(entry_stat):
                        files.append(changed_item(logical, 0, mode, findings, "directory inode changed"))
                    else:
                        walk_fd(child_fd, logical_path, depth + 1)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                item = ArtifactFile(logical, "special", entry_stat.st_size, mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-PATH-003", "high", "high", "path-safety", "block",
                    "Special filesystem entries are not analyzable skill content.", logical,
                )
                continue
            state["total"] += entry_stat.st_size
            if state["total"] > MAX_TOTAL_BYTES:
                add_finding(
                    findings, None, "AIS-INTAKE-002", "high", "high", "analyzability", "block",
                    f"Artifact exceeds the {MAX_TOTAL_BYTES}-byte total limit.", str(state["total"]), path=logical,
                )
                state["stop"] = True
                return
            files.append(read_regular_at(directory_fd, name, logical, entry_stat, findings))

    try:
        walk_fd(root_fd, PurePosixPath())
    finally:
        os.close(root_fd)
    return files, findings, "directory", None


def zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def zip_preflight(source: BinaryIO, path: str, findings: list[Finding]) -> bool:
    source.seek(0, os.SEEK_END)
    source_size = source.tell()
    source.seek(max(0, source_size - 65_557))
    tail = source.read(65_557)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail) - position < 22:
        add_finding(
            findings, None, "AIS-INTAKE-009", "high", "high", "analyzability", "block",
            "ZIP end-of-central-directory record is missing or truncated.", path, path=path,
        )
        return False
    _, _, _, _, entries, central_size, _, _ = struct.unpack_from("<4s4H2LH", tail, position)
    if entries == 0xFFFF or central_size == 0xFFFFFFFF:
        add_finding(
            findings, None, "AIS-INTAKE-010", "high", "high", "analyzability", "block",
            "ZIP64 metadata is not supported by the bounded v1 parser.", path, path=path,
        )
        return False
    if entries > MAX_FILES or central_size > MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        add_finding(
            findings, None, "AIS-INTAKE-001", "high", "high", "analyzability", "block",
            "ZIP central directory exceeds the bounded metadata limits.",
            f"entries={entries} central_bytes={central_size}", path=path,
        )
        return False
    return True


def load_zip(source: BinaryIO, target_name: str, source_sha256: str) -> tuple[list[ArtifactFile], list[Finding], str, str]:
    findings: list[Finding] = FindingList()
    files: list[ArtifactFile] = []
    if not zip_preflight(source, target_name, findings):
        return files, findings, "zip", source_sha256
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            add_finding(
                findings, None, "AIS-INTAKE-001", "high", "high", "analyzability", "block",
                f"Archive exceeds the {MAX_FILES}-entry limit.", str(len(infos)), path=target_name,
            )
            return files, findings, "zip", source_sha256
        declared_total = sum(info.file_size for info in infos)
        if declared_total > MAX_TOTAL_BYTES:
            add_finding(
                findings, None, "AIS-INTAKE-002", "high", "high", "analyzability", "block",
                f"Archive exceeds the {MAX_TOTAL_BYTES}-byte total limit.", str(declared_total), path=target_name,
            )
            return files, findings, "zip", source_sha256
        for info in infos:
            if info.is_dir():
                continue
            logical = info.filename.replace("\\", "/")
            mode = (info.external_attr >> 16) & 0o7777
            if not safe_archive_path(logical):
                item = ArtifactFile(logical, "unsafe-path", info.file_size, mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-PATH-004", "critical", "high", "path-safety", "block",
                    "Archive entry escapes or uses an unsafe path.", logical,
                )
                continue
            if is_protected_path(logical):
                files.append(protected_item(logical, info.file_size, mode, findings))
                continue
            if info.flag_bits & 0x1:
                item = ArtifactFile(logical, "encrypted", info.file_size, mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-INTAKE-005", "high", "high", "analyzability", "block",
                    "Encrypted archive entries cannot be analyzed.", logical,
                )
                continue
            if info.compress_size and info.file_size > 1_000_000:
                if info.file_size / max(info.compress_size, 1) > MAX_ARCHIVE_RATIO:
                    item = ArtifactFile(logical, "compression-bomb", info.file_size, mode, None, False)
                    files.append(item)
                    add_finding(
                        findings, item, "AIS-INTAKE-004", "critical", "high", "analyzability", "block",
                        "Archive entry exceeds the permitted compression ratio.", logical,
                    )
                    continue
            if info.file_size > MAX_FILE_BYTES:
                item = ArtifactFile(logical, "oversized", info.file_size, mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-INTAKE-003", "high", "high", "analyzability", "block",
                    f"Archive entry exceeds the {MAX_FILE_BYTES}-byte analysis limit.", logical,
                )
                continue
            content = archive.read(info)
            if zip_member_is_symlink(info):
                target_value = content.decode("utf-8", "replace")
                item = ArtifactFile(logical, "symlink", len(content), mode, sha256_bytes(content), False, target_value)
                files.append(item)
                decision = "block" if symlink_escapes(logical, target_value) else "review"
                add_finding(
                    findings, item, "AIS-PATH-001", "critical" if decision == "block" else "medium",
                    "high", "path-safety", decision, "Archive symlink is not followed.", target_value,
                )
                continue
            files.append(ArtifactFile(logical, "file", len(content), mode, sha256_bytes(content), True, content=content))
    return files, findings, "zip", source_sha256


def load_tar(source: BinaryIO, target_name: str, source_sha256: str) -> tuple[list[ArtifactFile], list[Finding], str, str]:
    findings: list[Finding] = FindingList()
    files: list[ArtifactFile] = []
    count = 0
    declared_total = 0
    source.seek(0)
    with tarfile.open(fileobj=source, mode="r:*") as archive:
        for member in archive:
            count += 1
            if count > MAX_FILES:
                add_finding(
                    findings, None, "AIS-INTAKE-001", "high", "high", "analyzability", "block",
                    f"Archive exceeds the {MAX_FILES}-entry limit.", str(count), path=target_name,
                )
                break
            logical = member.name.replace("\\", "/")
            if not safe_archive_path(logical):
                item = ArtifactFile(logical, "unsafe-path", member.size, member.mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-PATH-004", "critical", "high", "path-safety", "block",
                    "Archive entry escapes or uses an unsafe path.", logical,
                )
                break
            if is_protected_path(logical):
                files.append(protected_item(logical, member.size, member.mode, findings))
                break
            if member.issym() or member.islnk():
                item = ArtifactFile(logical, "symlink", 0, member.mode, None, False, member.linkname)
                files.append(item)
                decision = "block" if symlink_escapes(logical, member.linkname) else "review"
                add_finding(
                    findings, item, "AIS-PATH-001", "critical" if decision == "block" else "medium",
                    "high", "path-safety", decision, "Archive link is not followed.", member.linkname,
                )
                if decision == "block":
                    break
                continue
            if not member.isfile():
                if member.isdir():
                    continue
                item = ArtifactFile(logical, "special", member.size, member.mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-PATH-003", "high", "high", "path-safety", "block",
                    "Special archive entries are not analyzable skill content.", logical,
                )
                break
            declared_total += member.size
            if declared_total > MAX_TOTAL_BYTES:
                add_finding(
                    findings, None, "AIS-INTAKE-002", "high", "high", "analyzability", "block",
                    f"Archive exceeds the {MAX_TOTAL_BYTES}-byte total limit.", str(declared_total), path=logical,
                )
                break
            if member.size > MAX_FILE_BYTES:
                item = ArtifactFile(logical, "oversized", member.size, member.mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-INTAKE-003", "high", "high", "analyzability", "block",
                    f"Archive entry exceeds the {MAX_FILE_BYTES}-byte analysis limit.", logical,
                )
                break
            handle = archive.extractfile(member)
            if handle is None:
                item = ArtifactFile(logical, "unreadable", member.size, member.mode, None, False)
                files.append(item)
                add_finding(
                    findings, item, "AIS-INTAKE-007", "high", "high", "analyzability", "block",
                    "Archive entry could not be read.", logical,
                )
                break
            content = handle.read(MAX_FILE_BYTES + 1)
            files.append(ArtifactFile(logical, "file", len(content), member.mode, sha256_bytes(content), True, content=content))
    return files, findings, "tar", source_sha256


def revalidate_parsed_source(
    fd: int,
    opened: os.stat_result,
    expected_sha256: str,
    parsed: tuple[list[ArtifactFile], list[Finding], str, str],
) -> tuple[list[ArtifactFile], list[Finding], str, str | None]:
    files, findings, target_type, _ = parsed
    actual_sha256, _, final = hash_descriptor(fd)
    if actual_sha256 != expected_sha256 or not stat_stable(opened, final):
        item = changed_item(
            ".", final.st_size, stat.S_IMODE(final.st_mode), findings,
            "archive changed between source hashing and content analysis",
        )
        files.append(item)
        return files, findings, target_type, None
    return files, findings, target_type, expected_sha256


def load_target(target: Path) -> tuple[list[ArtifactFile], list[Finding], str, str | None]:
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        raise ScanError(f"target does not exist: {target}")
    if stat.S_ISLNK(target_stat.st_mode):
        return top_level_symlink(target, target_stat)
    reject_broad_target(target)
    resolved = target.resolve(strict=True)
    if is_protected_path(resolved.as_posix()):
        findings: list[Finding] = FindingList()
        item = protected_item(
            target.name, target_stat.st_size, stat.S_IMODE(target_stat.st_mode), findings,
            "The supplied target resolves inside a protected credential path; content was not read.",
        )
        return [item], findings, "protected", None
    if stat.S_ISDIR(target_stat.st_mode):
        return load_directory(target)
    if not stat.S_ISREG(target_stat.st_mode):
        raise ScanError(f"target is not a regular file or directory: {target}")
    if target_stat.st_nlink > 1:
        findings = FindingList()
        item = ArtifactFile(
            target.name, "hardlink", target_stat.st_size, stat.S_IMODE(target_stat.st_mode),
            None, False, "content not read",
        )
        add_finding(
            findings, item, "AIS-PATH-005", "critical", "high", "path-safety", "block",
            "Top-level file has multiple hard links; content was not read.",
            f"link_count={target_stat.st_nlink}",
        )
        return [item], findings, "hardlink", None
    if target_stat.st_size > MAX_SOURCE_BYTES:
        findings = FindingList()
        item = ArtifactFile(
            target.name, "oversized-source", target_stat.st_size, stat.S_IMODE(target_stat.st_mode),
            None, False, "source limit exceeded",
        )
        add_finding(
            findings, item, "AIS-INTAKE-011", "high", "high", "analyzability", "block",
            f"Input exceeds the {MAX_SOURCE_BYTES}-byte source limit before type probing.",
            str(target_stat.st_size),
        )
        return [item], findings, "file", None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as error:
        raise ScanError(f"cannot open target without following links: {target}: {error}") from error
    try:
        opened = os.fstat(fd)
        if stat_identity(opened) != stat_identity(target_stat):
            findings = FindingList()
            item = changed_item(target.name, target_stat.st_size, stat.S_IMODE(target_stat.st_mode), findings, "target inode changed")
            return [item], findings, "file", None
        source_sha256, hashed_bytes, final = hash_descriptor(fd)
        if source_sha256 is None:
            findings = FindingList()
            item = changed_item(
                target.name, final.st_size, stat.S_IMODE(final.st_mode), findings,
                "target exceeded the source budget during read",
            )
            return [item], findings, "file", None
        if not stat_stable(opened, final):
            findings = FindingList()
            item = changed_item(target.name, final.st_size, stat.S_IMODE(final.st_mode), findings, "target changed during read")
            return [item], findings, "file", None
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as probe:
            is_zip = zipfile.is_zipfile(probe)
        if is_zip:
            with os.fdopen(os.dup(fd), "rb") as source:
                parsed = load_zip(source, target.name, source_sha256)
            return revalidate_parsed_source(fd, opened, source_sha256, parsed)
        with os.fdopen(os.dup(fd), "rb") as probe:
            try:
                with tarfile.open(fileobj=probe, mode="r:*"):
                    pass
            except tarfile.TarError:
                is_tar = False
            else:
                is_tar = True
        if is_tar:
            with os.fdopen(os.dup(fd), "rb") as source:
                parsed = load_tar(source, target.name, source_sha256)
            return revalidate_parsed_source(fd, opened, source_sha256, parsed)
        findings = FindingList()
        if opened.st_size > MAX_FILE_BYTES:
            item = ArtifactFile(
                target.name, "oversized", opened.st_size, stat.S_IMODE(opened.st_mode), source_sha256,
                False, "file limit exceeded",
            )
            add_finding(
                findings, item, "AIS-INTAKE-003", "high", "high", "analyzability", "block",
                f"File exceeds the {MAX_FILE_BYTES}-byte analysis limit.", str(opened.st_size),
            )
            return [item], findings, "file", source_sha256
        os.lseek(fd, 0, os.SEEK_SET)
        content_parts: list[bytes] = []
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            content_parts.append(chunk)
        after_content = os.fstat(fd)
        if not stat_stable(opened, after_content):
            item = changed_item(
                target.name, after_content.st_size, stat.S_IMODE(after_content.st_mode), findings,
                "target changed during content read",
            )
            return [item], findings, "file", None
        content = b"".join(content_parts)
        item = ArtifactFile(
            target.name, "file", len(content), stat.S_IMODE(opened.st_mode), source_sha256, True,
            content=content,
        )
        return [item], findings, "file", source_sha256
    finally:
        os.close(fd)


def decode_text(item: ArtifactFile, findings: list[Finding]) -> str | None:
    if item.content is None:
        return None
    content = item.content
    suffix = PurePosixPath(item.path).suffix.lower()
    if b"\x00" in content[:4096] and suffix not in {".utf16", ".txt"}:
        return None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = content.decode(encoding)
            if encoding == "utf-16" and not content.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeError:
            continue
    if suffix in TEXT_EXTENSIONS or suffix in SCRIPT_EXTENSIONS:
        # A legacy single-byte encoding is common in real documents and is not a
        # reason to refuse the artifact. Analyzing the decoded text is strictly
        # better than blocking it unread, provided the result still looks like
        # text rather than a binary that happens to decode.
        legacy = content.decode("cp1252", "replace")
        printable = sum(1 for char in legacy if char.isprintable() or char in "\r\n\t")
        if legacy and printable / len(legacy) > 0.95 and "�" not in legacy:
            add_finding(
                findings, item, "AIS-TEXT-002", "low", "high", "analyzability", "inform",
                "Text is not UTF-8; it was analyzed as a legacy single-byte encoding.",
                item.path,
            )
            return legacy
        add_finding(
            findings, item, "AIS-TEXT-001", "high", "high", "analyzability", "block",
            "Expected text content could not be decoded.", item.path,
        )
    return None


_LINE_INDEX_CACHE: tuple[str, list[int], list[str]] | None = None


def _line_index(text: str) -> tuple[list[int], list[str]]:
    """Return line-start offsets and split lines for `text`, reusing the last result.

    Deriving a line number by counting newlines from the start of the file is
    linear per lookup, so a file carrying many matches costs time quadratic in
    its size. One artifact within every declared limit can therefore stall a
    scan for hours. Analysis runs one file at a time, so caching the most recent
    text keeps every lookup after the first at logarithmic cost.
    """
    global _LINE_INDEX_CACHE
    cached = _LINE_INDEX_CACHE
    if cached is not None and cached[0] is text:
        return cached[1], cached[2]
    starts = [0]
    position = text.find("\n")
    while position >= 0:
        starts.append(position + 1)
        position = text.find("\n", position + 1)
    lines = text.splitlines()
    _LINE_INDEX_CACHE = (text, starts, lines)
    return starts, lines


def line_for_offset(text: str, offset: int) -> int:
    starts, _ = _line_index(text)
    return bisect.bisect_right(starts, offset)


def line_text(text: str, line: int) -> str:
    _, lines = _line_index(text)
    if 1 <= line <= len(lines):
        return lines[line - 1]
    return ""


SOFTENED_SEVERITY = {"critical": "high", "high": "medium", "medium": "low", "low": "low", "info": "info"}
SOFTENED_DECISION = {"block": "review", "review": "review", "inform": "inform"}


def regex_findings(
    item: ArtifactFile,
    text: str,
    findings: list[Finding],
    rule_id: str,
    pattern: str,
    severity: str,
    confidence: str,
    category: str,
    decision: str,
    message: str,
    flags: int = re.IGNORECASE,
    evidence_text: str | None = None,
    code_lines: set[int] | None = None,
    soften_reason: str | None = None,
) -> None:
    compiled = re.compile(pattern, flags)
    source = evidence_text if evidence_text is not None else text
    for match in compiled.finditer(text):
        line = line_for_offset(text, match.start())
        effective_severity = severity
        effective_decision = decision
        effective_message = message
        reason = soften_reason
        if reason is None and code_lines is not None and line not in code_lines:
            reason = "Found in prose rather than in a code block."
        if reason is not None:
            effective_severity = SOFTENED_SEVERITY[severity]
            effective_decision = SOFTENED_DECISION[decision]
            effective_message = f"{message} {reason}"
        add_finding(
            findings, item, rule_id, effective_severity, confidence, category,
            effective_decision, effective_message,
            line_text(source, line) or match.group(0), line,
        )


def fenced_code_lines(text: str) -> set[int]:
    """Return the 1-based line numbers that sit inside a fenced code block."""
    lines = text.splitlines()
    inside = False
    result: set[int] = set()
    for number, value in enumerate(lines, 1):
        stripped = value.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            inside = not inside
            result.add(number)
            continue
        if inside:
            result.add(number)
    return result


SHELL_ASSIGNMENT_RE = re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=[\"']?([^\s\"'#;|&]+)[\"']?[ \t]*$")


def expand_shell_assignments(text: str) -> str:
    """Substitute simple `VAR=value` assignments into later `$VAR` references.

    Splitting a command across a variable defeats a pattern that expects the
    command name and its pipe target to be adjacent. Assignment values cannot
    contain a newline, so substitution never changes the line count and a match
    offset in the expanded text still resolves to the correct original line.
    """
    values = {name: value for name, value in SHELL_ASSIGNMENT_RE.findall(text)}
    if not values:
        return text
    expanded = text
    for _ in range(3):
        previous = expanded
        for name, value in values.items():
            expanded = expanded.replace(f"${{{name}}}", value)
            expanded = re.sub(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", value.replace("\\", "\\\\"), expanded)
        if expanded == previous:
            break
    return expanded


def scan_unicode(item: ArtifactFile, text: str, findings: list[Finding]) -> None:
    tag_run = 0
    tag_start = 0
    variation_run = 0
    variation_start = 0
    for index, char in enumerate(text):
        code = ord(char)
        if code in BIDI_AND_INVISIBLE:
            line = line_for_offset(text, index)
            add_finding(
                findings, item, "AIS-EVASION-001", "high", "high", "obfuscation", "review",
                "Invisible or bidirectional Unicode control found.", f"U+{code:04X}", line,
            )
        if code in TAG_CHARACTER_RANGE:
            if tag_run == 0:
                tag_start = index
            tag_run += 1
        elif tag_run:
            report_tag_run(item, text, findings, tag_start, tag_run)
            tag_run = 0
        if code in VARIATION_SELECTORS:
            if variation_run == 0:
                variation_start = index
            variation_run += 1
        elif variation_run:
            report_variation_run(item, text, findings, variation_start, variation_run)
            variation_run = 0
    if tag_run:
        report_tag_run(item, text, findings, tag_start, tag_run)
    if variation_run:
        report_variation_run(item, text, findings, variation_start, variation_run)
    for number, line_value in enumerate(text.splitlines(), 1):
        if len(line_value) > 10_000:
            add_finding(
                findings, item, "AIS-EVASION-002", "high", "high", "obfuscation", "review",
                "Extremely long line can hide content from reviewers or bounded scanners.",
                f"line length={len(line_value)}", number,
            )
    if len(text) > 100_000 and len(text.strip()) / max(len(text), 1) < 0.02:
        add_finding(
            findings, item, "AIS-EVASION-003", "high", "high", "obfuscation", "review",
            "Artifact contains extreme whitespace padding.", f"bytes={len(text)}",
        )


def report_tag_run(
    item: ArtifactFile, text: str, findings: list[Finding], start: int, length: int
) -> None:
    line = line_for_offset(text, start)
    add_finding(
        findings, item, "AIS-EVASION-006", "critical", "high", "obfuscation", "block",
        "Unicode tag characters carry text that renders as nothing; they have no "
        "legitimate use in a skill or plugin and can hide instructions from every reader.",
        f"{length} tag characters starting at U+{ord(text[start]):05X}", line,
    )


def report_variation_run(
    item: ArtifactFile, text: str, findings: list[Finding], start: int, length: int
) -> None:
    if length <= MAX_BENIGN_VARIATION_RUN:
        return
    line = line_for_offset(text, start)
    add_finding(
        findings, item, "AIS-EVASION-007", "high", "high", "obfuscation", "review",
        "A long run of Unicode variation selectors can encode hidden text.",
        f"{length} consecutive variation selectors", line,
    )


HOMOGLYPH_FOLD = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    "ѕ": "s", "ј": "j", "ԁ": "d", "һ": "h", "ԛ": "q", "ԝ": "w", "ν": "v", "ο": "o",
    "α": "a", "ρ": "p", "τ": "t", "ι": "i", "κ": "k", "μ": "u", "Α": "A", "Β": "B",
    "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Χ": "X", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "Ѕ": "S",
}

# Deleting rather than replacing is what matters: an invisible character wedged
# inside a keyword splits it for every pattern in the ruleset, so one
# normalization pass restores the keyword for all rules at once instead of
# needing an evasion-aware variant of each rule.
RULE_NORMALIZATION_TABLE = {
    **{code: None for code in BIDI_AND_INVISIBLE},
    **{code: None for code in TAG_CHARACTER_RANGE},
    **{code: None for code in VARIATION_SELECTORS},
    **{ord(source): target for source, target in HOMOGLYPH_FOLD.items()},
}

MIXED_SCRIPT_WORD_RE = re.compile(
    r"\b(?=[A-Za-z]*[" + "".join(HOMOGLYPH_FOLD) + r"])(?=[" + "".join(HOMOGLYPH_FOLD) + r"]*[A-Za-z])"
    r"[A-Za-z" + "".join(HOMOGLYPH_FOLD) + r"]{3,}\b"
)


def normalize_for_rules(text: str) -> str:
    """Remove invisible characters and fold look-alike letters before matching.

    Newlines are untouched, so an offset in the normalized text still resolves
    to the correct line of the original.
    """
    return text.translate(RULE_NORMALIZATION_TABLE)


def scan_homoglyphs(item: ArtifactFile, text: str, findings: list[Finding]) -> None:
    for match in MIXED_SCRIPT_WORD_RE.finditer(text):
        line = line_for_offset(text, match.start())
        add_finding(
            findings, item, "AIS-EVASION-008", "high", "high", "obfuscation", "review",
            "A word mixes Latin with look-alike letters from another script, which reads "
            "as one word but matches as another.",
            match.group(0), line,
        )


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    frequencies = {char: value.count(char) / len(value) for char in set(value)}
    return -sum(probability * math.log2(probability) for probability in frequencies.values())


def scan_encoded_content(item: ArtifactFile, text: str, findings: list[Finding]) -> None:
    encoded = re.compile(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]{80,}={0,2})(?![A-Za-z0-9+/=])")
    danger = re.compile(r"(?i)(curl|wget|/bin/sh|bash\s+-c|os\.system|subprocess|\.ssh|\.env|requests\.post)")
    for match in encoded.finditer(text):
        blob = match.group(1)
        line = line_for_offset(text, match.start())
        if shannon_entropy(blob) < 4.0:
            continue
        try:
            decoded = base64.b64decode(blob, validate=True)
        except (ValueError, base64.binascii.Error):
            decoded = b""
        if decoded and danger.search(decoded.decode("utf-8", "ignore")):
            add_finding(
                findings, item, "AIS-EVASION-004", "critical", "high", "obfuscation", "block",
                "Encoded content decodes to security-sensitive commands or paths.",
                "base64 payload decodes to dangerous content (redacted)", line,
            )
        else:
            add_finding(
                findings, item, "AIS-EVASION-005", "medium", "medium", "obfuscation", "review",
                "High-entropy encoded content requires review.", f"encoded length={len(blob)}", line,
            )


# Instruction rules run against every decodable text, not a chosen set of
# extensions. An injected instruction is read by the agent wherever it sits: a
# JSON tool description, a YAML manifest, a code comment. Restricting these
# rules by file type leaves the format that carries the best-documented attack
# (an MCP tool description) unexamined.
INSTRUCTION_RULES = [
    (
        "AIS-INSTR-001",
        r"\b(ignore|disregard|forget|override|set\s+aside)\b[^\n]{0,80}?"
        r"\b(previous|prior|earlier|above|preceding|system|developer|operator|privileged|higher[- ]priority)\b"
        r"[^\n]{0,60}?\b(instruction|prompt|rule|guidance|direction|polic)"
        r"|\b(ignoruj|nevšímej|nevsimej|zapomeň|zapomen|přepiš|prepis)\b[^\n]{0,80}?"
        r"\b(předchoz|predchoz|výše|vyse|dřívěj|drivej|systémov|systemov|nadřazen|nadrazen)\w*\b"
        r"[^\n]{0,60}?\b(instrukc|pokyn|pravidl|zadán|zadan)",
        "high", "medium", "prompt-injection", "review",
        "Instruction override language found.",
    ),
    (
        "AIS-INSTR-002",
        r"\b(do not|don['’]?t|never|without)\b[^\n]{0,50}?"
        r"\b(tell|mention|reveal|show|inform|disclose|notify|log|report)\b[^\n]{0,50}?"
        r"\b(user|operator|reviewer|human|anyone|owner)\b"
        r"|\b(neříkej|nerikej|neinformuj|nezmiňuj|nezminuj|neukazuj|neprozraď|neprozrad|nehlas)\b"
        r"[^\n]{0,60}?\b(uživatel|uzivatel|operátor|operator|člověk|clovek|nikomu)",
        "high", "medium", "concealment", "review",
        "Instruction requests concealment from the operator.",
    ),
    (
        "AIS-INSTR-003",
        r"\b(scanner|reviewer|audit|analysis|security\s+review)\b[^\n]{0,80}?"
        r"\b(output|return|mark|report|classify|treat|consider)\b[^\n]{0,40}?"
        r"\b(safe|clean|permit|pass|benign|harmless|trusted)\b"
        r"|\b(skener|scanner|kontrol\w*|audit)\b[^\n]{0,80}?\b(označ|oznac|vyhodnoť|vyhodnot|považuj|povazuj)\b"
        r"[^\n]{0,40}?\b(bezpečn|bezpecn|čist|cist|v pořádku|v poradku)",
        "high", "medium", "scanner-evasion", "review",
        "Content attempts to control the scanner or reviewer verdict.",
    ),
    (
        "AIS-INSTR-004",
        r"\b(bypass|circumvent|get\s+around|work\s+around)\b[^\n]{0,50}?"
        r"\b(permission|approval|sandbox|hook|polic|guardrail|safety|security|confirmation)\b"
        r"|\b(obejdi|obejít|obejit|obcház)\w*\b[^\n]{0,50}?"
        r"\b(oprávnění|opravneni|schvál|schval|sandbox|hook|politik|bezpečnost|bezpecnost|potvrzen)",
        "critical", "high", "policy-bypass", "block",
        "Instruction requests bypassing a safety or approval boundary.",
    ),
    (
        # Turning a control off is stated the same way in an attack and in a
        # legitimate workflow ("do not skip the confirmation"), so the weaker
        # verbs carry the same targets at review rather than block.
        "AIS-INSTR-008",
        r"\b(disable|turn\s+off|switch\s+off|skip|suppress|shadow)\b[^\n]{0,50}?"
        r"\b(permission|approval|sandbox|hook|polic|guardrail|safety|security|confirmation)\b"
        r"|\b(vypni|zakaž|zakaz|přeskoč|preskoc|potlač|potlac)\b[^\n]{0,50}?"
        r"\b(oprávnění|opravneni|schvál|schval|sandbox|hook|politik|bezpečnost|bezpecnost|potvrzen)",
        "high", "low", "policy-bypass", "review",
        "Instruction asks for a safety or approval control to be turned off or skipped.",
    ),
    (
        "AIS-INSTR-005",
        r"(~/?\.ssh|~/?\.aws|~/?\.gnupg|~/?\.kube|/etc/sops|browser credential|keychain|\.env\b"
        r"|id_rsa|id_ed25519|\.git-credentials|\.netrc|\.npmrc)",
        "high", "medium", "sensitive-data", "review",
        "Instruction references protected credential material.",
    ),
    (
        "AIS-INSTR-006",
        r"\b(secretly|covertly|silently|quietly|discreetly|in\s+the\s+background)\b[^\n]{0,80}?"
        r"\b(send|upload|post|exfiltrat|copy|collect|transmit|forward|leak)\b"
        r"|\b(tajně|tajne|potichu|nenápadně|nenapadne|skrytě|skryte)\b[^\n]{0,80}?"
        r"\b(pošli|posli|odešli|odesli|nahraj|zkopíruj|zkopiruj|přepošli|preposli|sbírej|sbirej)",
        "critical", "high", "exfiltration", "block",
        "Instruction describes covert collection or transmission.",
    ),
    (
        "AIS-INSTR-007",
        r"\b(curl|wget)\b[^\n]{0,100}\|\s*(?:ba)?sh\b",
        "critical", "high", "remote-code-execution", "block",
        "Instruction contains remote download piped to a shell.",
    ),
]


def scan_instruction_text(
    item: ArtifactFile,
    text: str,
    findings: list[Finding],
    search_text: str | None = None,
    in_source_code: bool = False,
) -> None:
    """Apply instruction rules at full strength wherever prose can reach the agent.

    Fenced-code softening deliberately does not apply here. It exists so that
    documentation may describe a dangerous command, whereas an injected
    instruction in ordinary prose is the primary attack this rule set exists to
    catch, and softening it there would disarm the rule set on its main target.
    """
    # An instruction written into a document or a manifest field reaches the
    # agent directly. The same words inside source code reach it only if the
    # program later emits them, and source that defines or tests detection rules
    # necessarily contains the very phrases being detected.
    soften_reason = (
        "It appears inside source code rather than in a document or manifest the agent reads directly."
        if in_source_code
        else None
    )
    for rule_id, pattern, severity, confidence, category, decision, message in INSTRUCTION_RULES:
        regex_findings(
            item, search_text if search_text is not None else text, findings, rule_id, pattern,
            severity, confidence, category, decision, message,
            flags=re.IGNORECASE | re.DOTALL, evidence_text=text,
            soften_reason=soften_reason,
        )


def node_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = node_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def python_import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name.split(".")[0]] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                if imported.name != "*":
                    aliases[imported.asname or imported.name] = f"{node.module}.{imported.name}"
    return aliases


def python_callable_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, (ast.Name, ast.Attribute)):
        return resolved_node_name(node, aliases)
    if isinstance(node, ast.Call) and resolved_node_name(node.func, aliases) in {"getattr", "builtins.getattr"}:
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            base = resolved_node_name(node.args[0], aliases)
            if base:
                return f"{base}.{node.args[1].value}"
    return ""


def extend_python_callable_aliases(tree: ast.AST, aliases: dict[str, str]) -> None:
    capability_prefixes = (
        "os.getenv", "os.environ", "keyring.", "requests.", "httpx.", "urllib.",
        "aiohttp.", "socket.", "subprocess.", "os.system", "os.popen",
    )
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    for _ in range(3):
        changed = False
        for node in assignments:
            value = node.value
            if value is None:
                continue
            resolved = python_callable_name(value, aliases)
            if not resolved.startswith(capability_prefixes):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != resolved:
                    aliases[target.id] = resolved
                    changed = True
        if not changed:
            break


def python_callable_containers(
    tree: ast.AST, aliases: dict[str, str]
) -> dict[str, dict[object, str]]:
    containers: dict[str, dict[object, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names:
            continue
        entries: dict[object, str] = {}
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for index, element in enumerate(node.value.elts):
                resolved = python_callable_name(element, aliases)
                if resolved:
                    entries[index] = resolved
        elif isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, (str, int)):
                    resolved = python_callable_name(value, aliases)
                    if resolved:
                        entries[key.value] = resolved
        if entries:
            for name in names:
                containers[name] = entries
    return containers


def resolved_call_name(
    node: ast.AST, aliases: dict[str, str], containers: dict[str, dict[object, str]]
) -> str:
    direct = resolved_node_name(node, aliases)
    if direct:
        return direct
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        key_node = node.slice
        if isinstance(key_node, ast.Constant):
            return containers.get(node.value.id, {}).get(key_node.value, "")
    return ""


def resolved_node_name(node: ast.AST, aliases: dict[str, str]) -> str:
    raw = node_name(node)
    if not raw:
        return raw
    first, separator, rest = raw.partition(".")
    replacement = aliases.get(first, first)
    return f"{replacement}.{rest}" if separator else replacement


def python_has_sensitive_path(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if re.search(
                r"(?i)(?:^|/)(?:\.ssh|\.aws|\.gnupg|\.kube|\.env(?:\.|$)|\.npmrc$|\.pypirc$|"
                r"credentials?(?:\.|$)|id_(?:rsa|ed25519|ecdsa|dsa)$|secrets?\.(?:ya?ml|json)$)",
                child.value,
            ):
                return True
    return False


def python_call_is_sensitive_source(
    node: ast.Call,
    aliases: dict[str, str],
    containers: dict[str, dict[object, str]],
) -> bool:
    name = resolved_call_name(node.func, aliases, containers)
    if name in {
        "os.getenv", "os.environ.get", "keyring.get_password", "keyring.get_credential",
    } or name.startswith("keyring."):
        return True
    if name in {"open", "read_text", "read_bytes", "Path.read_text", "Path.read_bytes"}:
        return python_has_sensitive_path(node)
    return False


def python_expr_sensitive(
    node: ast.AST,
    tainted: set[str],
    aliases: dict[str, str],
    containers: dict[str, dict[object, str]],
) -> bool:
    if isinstance(node, ast.Name) and node.id in tainted:
        return True
    if isinstance(node, ast.Subscript) and resolved_node_name(node.value, aliases) == "os.environ":
        return True
    if isinstance(node, ast.Attribute) and resolved_node_name(node, aliases) == "os.environ":
        return True
    if isinstance(node, ast.Call) and python_call_is_sensitive_source(node, aliases, containers):
        return True
    return any(
        python_expr_sensitive(child, tainted, aliases, containers)
        for child in ast.iter_child_nodes(node)
    )


def scan_python(item: ArtifactFile, text: str, findings: list[Finding]) -> None:
    try:
        tree = ast.parse(text, filename=item.path)
    except SyntaxError as error:
        add_finding(
            findings, item, "AIS-PY-001", "high", "high", "analyzability", "block",
            "Python source could not be parsed.", error.msg, error.lineno,
        )
        return
    aliases = python_import_aliases(tree)
    extend_python_callable_aliases(tree, aliases)
    containers = python_callable_containers(tree, aliases)
    tainted: set[str] = set()
    network_calls = {
        "requests.get", "requests.post", "requests.put", "requests.patch", "requests.request",
        "httpx.get", "httpx.post", "urllib.request.urlopen", "socket.socket", "aiohttp.ClientSession",
    }
    process_calls = {
        "os.system", "os.popen", "subprocess.run", "subprocess.call", "subprocess.Popen",
        "subprocess.check_call", "subprocess.check_output",
    }
    destructive_calls = {"shutil.rmtree", "os.remove", "os.unlink", "Path.unlink"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None and python_expr_sensitive(value, tainted, aliases, containers):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        tainted.add(target.id)
                add_finding(
                    findings, item, "AIS-PY-008", "medium", "high", "sensitive-data", "review",
                    "Python code reads sensitive data or credential-related state.",
                    line_text(text, getattr(node, "lineno", 1)), getattr(node, "lineno", None),
                )
        if isinstance(node, ast.Subscript) and resolved_node_name(node.value, aliases) == "os.environ":
            add_finding(
                findings, item, "AIS-PY-008", "medium", "high", "sensitive-data", "review",
                "Python code reads environment state.",
                line_text(text, getattr(node, "lineno", 1)), getattr(node, "lineno", None),
            )
        if not isinstance(node, ast.Call):
            continue
        name = resolved_call_name(node.func, aliases, containers)
        evidence = line_text(text, getattr(node, "lineno", 1)) or name
        line = getattr(node, "lineno", None)
        if python_call_is_sensitive_source(node, aliases, containers):
            add_finding(
                findings, item, "AIS-PY-008", "medium", "high", "sensitive-data", "review",
                "Python code reads sensitive data or credential-related state.", evidence, line,
            )
        if name in {"eval", "exec", "compile", "builtins.eval", "builtins.exec"}:
            add_finding(
                findings, item, "AIS-PY-002", "high", "high", "dynamic-execution", "review",
                "Python dynamic code execution capability found.", evidence, line,
            )
        if name in process_calls or name.endswith(".Popen"):
            add_finding(
                findings, item, "AIS-PY-003", "high", "high", "process-execution", "review",
                "Python subprocess or shell execution capability found.", evidence, line,
            )
            if any(
                python_expr_sensitive(arg, tainted, aliases, containers)
                for arg in [*node.args, *[kw.value for kw in node.keywords]]
            ):
                add_finding(
                    findings, item, "AIS-PY-011", "critical", "high", "exfiltration", "block",
                    "Sensitive data is passed to a subprocess, which can carry it off the host "
                    "through an external command just as a network call would.",
                    evidence, line,
                )
        if name in network_calls or re.match(r"^(requests|httpx|urllib|aiohttp|socket)\.", name):
            add_finding(
                findings, item, "AIS-PY-004", "medium", "high", "network", "review",
                "Python outbound network capability found.", evidence, line,
            )
            if any(
                python_expr_sensitive(arg, tainted, aliases, containers)
                for arg in [*node.args, *[kw.value for kw in node.keywords]]
            ):
                add_finding(
                    findings, item, "AIS-PY-005", "critical", "high", "exfiltration", "block",
                    "Sensitive data can flow directly into a network call.", evidence, line,
                )
        if name in destructive_calls:
            add_finding(
                findings, item, "AIS-PY-006", "high", "high", "destructive-action", "review",
                "Python destructive filesystem capability found.", evidence, line,
            )
        if name in {"os.chmod", "os.chown", "Path.chmod"}:
            add_finding(
                findings, item, "AIS-PY-007", "high", "high", "permission-change", "review",
                "Python permission or ownership change capability found.", evidence, line,
            )
        dynamic_getattr = name in {"getattr", "builtins.getattr"} and (
            len(node.args) < 2
            or not isinstance(node.args[1], ast.Constant)
            or not isinstance(node.args[1].value, str)
        )
        if dynamic_getattr or name in {"__import__", "builtins.__import__", "importlib.import_module"}:
            add_finding(
                findings, item, "AIS-PY-009", "high", "medium", "analysis-evasion", "review",
                "Dynamic attribute or import behavior can evade static capability resolution.", evidence, line,
            )
        if not name and not isinstance(node.func, (ast.Name, ast.Attribute)):
            add_finding(
                findings, item, "AIS-PY-010", "high", "medium", "analysis-evasion", "review",
                "Indirect callable could not be resolved by the static analyzer.", evidence, line,
            )


def shebang_language(text: str) -> str | None:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if not first_line.startswith("#!"):
        return None
    command = first_line[2:].strip()
    if re.match(r"^(?:/usr/bin/env\s+)?(?:bash|sh|zsh|fish)(?:\s|$)", command) or re.match(
        r"^/(?:usr/)?bin/(?:bash|sh|zsh|fish)(?:\s|$)", command
    ):
        return "shell"
    if re.match(r"^(?:/usr/bin/env\s+)?(?:node|deno)(?:\s|$)", command) or re.match(
        r"^/(?:usr/)?bin/(?:node|deno)(?:\s|$)", command
    ):
        return "javascript"
    if re.match(r"^(?:/usr/bin/env\s+)?python(?:3(?:\.\d+)?)?(?:\s|$)", command) or re.match(
        r"^/(?:usr/)?bin/python(?:3(?:\.\d+)?)?(?:\s|$)", command
    ):
        return "python"
    return None


# The outer group is load-bearing: without it the trailing alternative detaches
# from whatever this pattern is concatenated onto, and a rule that should
# require a decoder before the pipe would fire on the pipe alone.
PIPE_TO_INTERPRETER = (
    r"(?:\|\s*(?:sudo\s+|env\s+)?(?:/(?:usr/)?(?:local/)?bin/)?(?:ba|z|k|da)?sh\b"
    r"|\|\s*(?:sudo\s+)?(?:python3?|perl|ruby|node|deno)\b)"
)
DECODER_COMMAND = r"\b(?:base64\s+(?:-d|-D|--decode)|xxd\s+-r|openssl\s+enc\s+-d|uudecode|base32\s+-d)\b"


def scan_shell_or_js(
    item: ArtifactFile,
    text: str,
    findings: list[Finding],
    *,
    force: bool = False,
    code_lines: set[int] | None = None,
    normalized: str | None = None,
) -> None:
    search_text = expand_shell_assignments(normalized if normalized is not None else text)
    rules = [
        ("AIS-EXEC-001", r"\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:ba)?sh\b", "critical", "high", "remote-code-execution", "block", "Remote content is piped directly to a shell."),
        # A recursive delete of a relative build directory is routine; the same
        # command aimed at an absolute path, a home directory, a variable or a
        # glob is the destructive case, and only that one blocks.
        ("AIS-EXEC-002", r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f|-[A-Za-z]*f[A-Za-z]*r)\b[^\n]{0,40}?\s(?:[\"']?(?:/|~|\$|\*))", "critical", "high", "destructive-action", "block", "Recursive forced deletion targets an absolute path, a home directory or an expanded value."),
        ("AIS-EXEC-014", r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f|-[A-Za-z]*f[A-Za-z]*r)\b", "medium", "high", "destructive-action", "review", "Recursive forced deletion command found."),
        ("AIS-EXEC-003", r"\b(?:mkfs(?:\.[a-z0-9]+)?|dd\s+[^\n]*\bof=/dev/|dropdb\b|DROP\s+DATABASE\b)", "critical", "high", "destructive-action", "block", "Host-wide destructive command found."),
        ("AIS-EXEC-004", r"\b(?:eval|exec)\b\s+(?:\$|\(|`|[\"'])", "high", "high", "dynamic-execution", "review", "Dynamic shell or script execution found."),
        ("AIS-EXEC-005", r"\b(?:sudo|doas|pkexec)\b|\bsu\s+-", "high", "high", "privilege-escalation", "review", "Privilege escalation command found."),
        ("AIS-EXEC-006", r"(?:/etc/claude-code|\.claude/(?:settings|hooks))", "critical", "medium", "policy-bypass", "review", "Command references agent safety or global runtime configuration."),
        ("AIS-EXEC-007", r"(?:crontab\b|/etc/cron|systemctl\s+enable|launchctl\s+(?:load|bootstrap)|\.config/systemd|\.git/hooks/|\.bashrc|\.zshrc|profile)", "high", "medium", "persistence", "review", "Persistence or startup configuration surface found."),
        ("AIS-EXEC-008", r"\b(?:curl|wget|fetch\s*\(|axios\.|https?\.request|net\.connect|nc\s|ncat\s)\b", "medium", "high", "network", "review", "Outbound network capability found."),
        ("AIS-EXEC-009", r"(?:process\.env|os\.environ|\.ssh|\.aws|\.gnupg|\.kube|\.env\b|keychain|credential)", "medium", "medium", "sensitive-data", "review", "Sensitive data or credential access surface found."),
        ("AIS-EXEC-010", r"(?:child_process|spawnSync|execSync|subprocess|os\.system)", "high", "high", "process-execution", "review", "Subprocess execution capability found."),
        ("AIS-EXEC-011", PIPE_TO_INTERPRETER, "high", "high", "dynamic-execution", "review", "Content is piped into an interpreter, which executes whatever the producing command emitted."),
        ("AIS-EXEC-012", DECODER_COMMAND + r"[^\n]{0,120}" + PIPE_TO_INTERPRETER, "critical", "high", "remote-code-execution", "block", "Decoded content is piped straight into an interpreter."),
        ("AIS-EXEC-013", r"(?:\bnc\b|\bncat\b|/dev/tcp/|\bsocat\b)[^\n]{0,60}(?:\d{1,5}|\|)", "high", "medium", "network", "review", "Raw socket or reverse-shell style network primitive found."),
    ]
    for rule_id, pattern, severity, confidence, category, decision, message in rules:
        regex_findings(
            item, search_text, findings, rule_id, pattern, severity, confidence, category,
            decision, message, evidence_text=text, code_lines=code_lines,
        )
    has_network = any(f.path == item.path and f.category == "network" for f in findings)
    has_sensitive = any(f.path == item.path and f.category == "sensitive-data" for f in findings)
    if has_network and has_sensitive:
        add_finding(
            findings, item, "AIS-FLOW-001", "high", "medium", "exfiltration", "review",
            "The same executable file combines sensitive-data access with outbound network capability.",
            item.path,
        )
    pipes_to_interpreter = any(
        f.path == item.path and f.rule_id in {"AIS-EXEC-011", "AIS-EXEC-012"} for f in findings
    )
    if pipes_to_interpreter and has_network and code_lines is None:
        add_finding(
            findings, item, "AIS-FLOW-004", "critical", "high", "remote-code-execution", "block",
            "The same file fetches remote content and pipes content into an interpreter, "
            "which is a remote-code-execution chain even when the two are split across "
            "variables or commands.",
            item.path,
        )


COMMAND_KEYS = {"command", "cmd", "run", "exec", "script", "entrypoint", "args"}


def json_command_values(data: Any, depth: int = 0) -> list[str]:
    """Collect command-like strings from anywhere in a decoded manifest."""
    if depth > 12:
        return []
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in COMMAND_KEYS:
                if isinstance(value, str):
                    found.append(value)
                elif isinstance(value, list):
                    parts = [part for part in value if isinstance(part, str)]
                    if parts:
                        found.append(" ".join(parts))
            found.extend(json_command_values(value, depth + 1))
    elif isinstance(data, list):
        for value in data:
            found.extend(json_command_values(value, depth + 1))
    return found


def scan_json_manifest(item: ArtifactFile, text: str, findings: list[Finding]) -> None:
    path = item.path.lower()
    if PurePosixPath(path).suffix != ".json":
        return
    important = any(
        token in path
        for token in ("plugin.json", "hooks.json", ".mcp.json", "mcp.json", "settings.json", "package.json")
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        if important:
            add_finding(
                findings, item, "AIS-MANIFEST-001", "high", "high", "analyzability", "block",
                "Security-relevant JSON manifest is invalid.", error.msg, error.lineno,
            )
        return
    if path.endswith("package.json") and isinstance(data, dict):
        scripts = data.get("scripts", {})
        if isinstance(scripts, dict):
            for key in ("preinstall", "install", "postinstall", "prepare"):
                if key in scripts:
                    add_finding(
                        findings, item, "AIS-SUPPLY-001", "high", "high", "install-hook", "review",
                        "Package lifecycle script executes during dependency installation.", key,
                    )
                    command = scripts[key]
                    if isinstance(command, str):
                        offset = text.find(command)
                        line = line_for_offset(text, offset) if offset >= 0 else 1
                        padded = ("\n" * (line - 1)) + command
                        scan_shell_or_js(item, padded, findings, force=True)
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            dependencies = data.get(section, {})
            if not isinstance(dependencies, dict):
                continue
            for name, version in dependencies.items():
                if isinstance(version, str) and (
                    version in {"*", "latest"} or version.startswith(("git+", "http://", "https://", "github:"))
                ):
                    add_finding(
                        findings, item, "AIS-SUPPLY-002", "medium", "high", "mutable-dependency", "review",
                        "Dependency uses a mutable or remote source.", f"{name}: {version}",
                    )
    declares_hooks = isinstance(data, dict) and isinstance(data.get("hooks"), (dict, list))
    if any(token in path for token in ("hooks.json", "/hooks/")) or declares_hooks:
        add_finding(
            findings, item, "AIS-SURFACE-001", "high", "high", "automatic-execution", "review",
            "Plugin or agent hook configuration can execute automatically.", item.path,
        )
    for command in json_command_values(data):
        if re.search(r"(?:@latest|@\*|#\s*$|(?<![\w.])latest(?![\w.]))", command) or re.search(
            r"\b(?:npx|uvx|pipx)\b[^\n]*\b(?:-y|--yes)\b", command
        ):
            add_finding(
                findings, item, "AIS-SUPPLY-003", "high", "high", "mutable-dependency", "review",
                "Manifest launches a package resolved at run time, so the code that will "
                "execute is not the code that was scanned.",
                command,
            )
    if "mcp" in PurePosixPath(path).name:
        add_finding(
            findings, item, "AIS-SURFACE-002", "medium", "high", "mcp", "review",
            "MCP configuration introduces tools, transport, commands, or remote metadata.", item.path,
        )
    if path.endswith("plugin.json"):
        add_finding(
            findings, item, "AIS-SURFACE-003", "info", "high", "plugin-manifest", "inform",
            "Plugin manifest discovered and inventoried.", item.path,
        )


def scan_runtime_surface(item: ArtifactFile, text: str | None, findings: list[Finding]) -> None:
    path = PurePosixPath(item.path)
    suffix = path.suffix.lower()
    lower = item.path.lower()
    executable_bit = bool(item.mode & 0o111)
    declared_bin = "/bin/" in f"/{lower}" or lower.startswith("bin/")
    language = shebang_language(text) if text is not None else None
    supported_executable = suffix in SCRIPT_EXTENSIONS or language is not None
    if executable_bit:
        add_finding(
            findings, item, "AIS-SURFACE-004", "medium", "high", "executable", "review",
            "File is marked executable.", oct(item.mode),
        )
    if declared_bin:
        add_finding(
            findings, item, "AIS-SURFACE-005", "high", "high", "plugin-binary", "review",
            "Plugin bin content may be placed on the agent command PATH.", item.path,
        )
    if (executable_bit or declared_bin) and not supported_executable:
        add_finding(
            findings, item, "AIS-BINARY-005", "high", "high", "unsupported-executable", "block",
            "Executable or plugin-bin content has no supported language classification.", item.path,
        )
    if suffix in EXECUTABLE_EXTENSIONS:
        add_finding(
            findings, item, "AIS-BINARY-001", "high", "high", "opaque-executable", "block",
            "Opaque executable or compiled artifact cannot be fully analyzed by the static core.", item.path,
        )
    elif text is None and item.kind == "file":
        if suffix in ARCHIVE_EXTENSIONS:
            add_finding(
                findings, item, "AIS-BINARY-002", "high", "high", "nested-artifact", "block",
                "Nested archive or container is not recursively analyzed in v1.", item.path,
            )
        elif suffix in DOCUMENT_EXTENSIONS:
            add_finding(
                findings, item, "AIS-BINARY-003", "medium", "medium", "opaque-document", "review",
                "Document content is not parsed by the deterministic core.", item.path,
            )
        else:
            add_finding(
                findings, item, "AIS-BINARY-004", "medium", "medium", "opaque-content", "review",
                "Binary or unknown content is inventoried but not semantically parsed.", item.path,
            )


def looks_like_python(text: str) -> bool:
    """Report whether `text` parses as Python and actually does something.

    A file name is chosen by whoever ships the artifact, so it cannot decide
    which analyzer runs. Requiring an import, definition or call keeps ordinary
    configuration and prose - which often parse as a trivial Python expression -
    out of the Python analyzer.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    return any(
        isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Call))
        for node in ast.walk(tree)
    )


def scan_item(item: ArtifactFile, findings: list[Finding]) -> None:
    if item.kind != "file" or not item.analyzed:
        return
    text = decode_text(item, findings)
    scan_runtime_surface(item, text, findings)
    if text is None:
        return
    suffix = PurePosixPath(item.path).suffix.lower()
    # Prose documents legitimately describe dangerous commands, so a match
    # outside a fenced code block is softened rather than dropped: an agent can
    # still act on prose, but documentation must not block its own artifact.
    code_lines = fenced_code_lines(text) if suffix in PROSE_EXTENSIONS else None
    rule_text = normalize_for_rules(text)
    language = shebang_language(text)
    declared_python = suffix == ".py" or language == "python"
    declared_other = suffix in REGEX_SCRIPT_EXTENSIONS or language in {"shell", "javascript"}
    scan_unicode(item, text, findings)
    scan_homoglyphs(item, text, findings)
    scan_encoded_content(item, text, findings)
    scan_instruction_text(
        item, text, findings, search_text=rule_text,
        in_source_code=declared_python or declared_other,
    )
    scan_json_manifest(item, text, findings)
    # A declared type wins over inference, because several languages parse as
    # valid Python and would otherwise escape their own analyzer. Only a file
    # that declares nothing is classified by content, and such a file runs both
    # analyzers: guessing wrong must not mean skipping analysis.
    guessed_python = not declared_python and not declared_other and looks_like_python(text)
    if declared_python or guessed_python:
        scan_python(item, text, findings)
    if not declared_python:
        # Python source is exempt from the shell and JavaScript literal patterns
        # because its own string and regex literals would otherwise be reported
        # as executable capabilities; its structural analyzer covers it instead.
        scan_shell_or_js(item, text, findings, code_lines=code_lines, normalized=rule_text)


def correlate_package(files: list[ArtifactFile], findings: list[Finding]) -> None:
    has_sensitive = any(f.category == "sensitive-data" for f in findings)
    has_network = any(f.category == "network" for f in findings)
    has_override = any(f.category in {"prompt-injection", "scanner-evasion", "policy-bypass"} for f in findings)
    has_execution = any(f.category in {"process-execution", "dynamic-execution", "remote-code-execution"} for f in findings)
    if has_sensitive and has_network:
        add_finding(
            findings, None, "AIS-FLOW-002", "high", "medium", "exfiltration", "review",
            "Package combines sensitive-data access with outbound network capability across its files.",
            "package capability correlation",
        )
    if has_override and has_execution:
        add_finding(
            findings, None, "AIS-FLOW-003", "high", "medium", "instruction-execution-chain", "review",
            "Package combines instruction/policy manipulation with code execution capability.",
            "package capability correlation",
        )
    if not any(PurePosixPath(item.path).name.lower() == "skill.md" for item in files):
        plugin_manifest = any(item.path.lower().endswith("plugin.json") for item in files)
        if not plugin_manifest:
            add_finding(
                findings, None, "AIS-STRUCT-001", "medium", "high", "structure", "review",
                "No SKILL.md or recognized plugin manifest was found.", "missing entrypoint",
            )


def apply_policy(findings: list[Finding], policy: str) -> None:
    if policy != "strict":
        return
    for finding in findings:
        if finding.decision == "inform" and SEVERITY_RANK[finding.severity] >= SEVERITY_RANK["medium"]:
            finding.decision = "review"
        if finding.decision == "review" and finding.severity == "critical" and finding.confidence == "high":
            finding.decision = "block"


def report_decision(findings: list[Finding]) -> str:
    maximum = max((DECISION_RANK[finding.decision] for finding in findings), default=0)
    return {0: "permit", 1: "review", 2: "block"}[maximum]


def build_report(target: Path, policy: str) -> dict[str, Any]:
    files, findings, target_type, source_sha256 = load_target(target)
    for item in files:
        scan_item(item, findings)
    correlate_package(files, findings)
    apply_policy(findings, policy)
    digest = artifact_digest(files)
    identity_complete = bool(source_sha256) if target_type in {"file", "zip", "tar"} else all(
        (item.sha256 is not None) or (item.kind == "symlink" and item.note is not None)
        for item in files
    )
    findings.sort(
        key=lambda finding: (
            -DECISION_RANK[finding.decision],
            -SEVERITY_RANK[finding.severity],
            finding.path,
            finding.line or 0,
            finding.rule_id,
        )
    )
    decision = report_decision(findings)
    counts = {
        name: sum(1 for finding in findings if finding.decision == name)
        for name in ("block", "review", "inform")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "scanner": {
            "name": "skill-scanner",
            "version": VERSION,
            "policy": policy,
            "source_sha256": scanner_source_sha256(),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": {"path": str(target.absolute()), "type": target_type},
        "artifact_digest": digest,
        "identity": {
            "canonical_artifact_digest": digest,
            "source_sha256": source_sha256,
            "complete": identity_complete,
            "guarantee": "raw-file-bytes" if source_sha256 else "canonical-tree",
        },
        "decision": decision,
        "counts": counts,
        "coverage": {
            "mode": "static",
            "executed_target_content": False,
            "limits": {
                "max_files": MAX_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_total_bytes": MAX_TOTAL_BYTES,
                "max_source_bytes": MAX_SOURCE_BYTES,
                "max_zip_central_directory_bytes": MAX_ZIP_CENTRAL_DIRECTORY_BYTES,
            },
            "limitations": [
                "Novel semantic attacks and dormant second stages may evade static analysis.",
                "Dependency vulnerability and reputation databases are not queried in v1.",
                "Nested archives, opaque executables, and protected credential files fail closed.",
                "Managed runtime policy remains the containment boundary.",
            ],
        },
        "manifest": [item.manifest_dict() for item in sorted(files, key=lambda value: value.path)],
        "findings": [finding.to_dict() for finding in findings],
    }


def human_report(report: dict[str, Any]) -> str:
    decision = report["decision"].upper()
    lines = [
        f"Skill Scanner {report['scanner']['version']}",
        f"Decision: {decision} WITHIN STATIC COVERAGE" if decision == "PERMIT" else f"Decision: {decision}",
        f"Target: {report['target']['path']}",
        f"Canonical artifact digest: {report['artifact_digest']}",
        f"Files: {len(report['manifest'])} | block={report['counts']['block']} review={report['counts']['review']} inform={report['counts']['inform']}",
    ]
    if report.get("identity", {}).get("source_sha256"):
        lines.insert(4, f"Raw source SHA-256: {report['identity']['source_sha256']}")
    if not report.get("identity", {}).get("complete", False):
        lines.insert(5, "Identity: INCOMPLETE - verify must fail closed")
    if report["findings"]:
        lines.append("")
        lines.append("Findings:")
    for finding in report["findings"]:
        location = finding["path"]
        if finding["line"]:
            location += f":{finding['line']}"
        lines.append(
            f"- [{finding['decision'].upper()} {finding['severity'].upper()}] "
            f"{finding['rule_id']} {location}: {finding['message']}"
        )
        if finding["evidence"]:
            lines.append(f"  Evidence: {finding['evidence']}")
    lines.append("")
    lines.append("Limit: this is a static admission scan, not a safety certificate.")
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise ScanError(f"refusing to overwrite existing evidence: {path}")
    if not path.parent.is_dir():
        raise ScanError(f"evidence directory does not exist: {path.parent}")
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.write("\n")
        os.link(temp_path, path)
        temp_path.unlink()
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


# Reports are read and written only inside one evidence directory, so a crafted path can neither make
# the scanner overwrite an arbitrary file nor load a planted report. The directory must already exist.
EVIDENCE_DIR_VARIABLE = "SKILL_SCANNER_EVIDENCE_DIR"
DEFAULT_EVIDENCE_DIR = Path("~") / ".skill-scanner" / "evidence"


def allowed_evidence_roots() -> tuple[Path, ...]:
    configured = os.environ.get(EVIDENCE_DIR_VARIABLE)
    candidate = Path(configured) if configured else DEFAULT_EVIDENCE_DIR
    candidate = candidate.expanduser()
    if not candidate.is_absolute() or not candidate.is_dir():
        return ()
    return (candidate.resolve(),)


def validate_evidence_path(path: Path, *, must_exist: bool) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise ScanError("evidence path must be absolute")
    resolved = expanded.resolve(strict=must_exist)
    roots = allowed_evidence_roots()
    if not roots or not any(resolved.is_relative_to(root) for root in roots):
        raise ScanError(
            "evidence path must be inside the evidence directory, which must exist: "
            f"${EVIDENCE_DIR_VARIABLE} when set, otherwise {DEFAULT_EVIDENCE_DIR}"
        )
    return resolved


def scanner_source_sha256() -> str:
    source = Path(__file__).resolve()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source, flags)
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def validate_output_path(output: Path, target: Path) -> Path:
    output_path = validate_evidence_path(output, must_exist=False)
    if output_path.exists() or output_path.is_symlink():
        raise ScanError("output evidence path must be new and may not overwrite an existing file")
    try:
        target_stat = target.lstat()
        target_path = target.resolve(strict=True)
    except FileNotFoundError as error:
        raise ScanError(f"target does not exist: {target}") from error
    if stat.S_ISLNK(target_stat.st_mode):
        raise ScanError("output files are not permitted when the scan target is a symlink")
    if output_path == target_path:
        raise ScanError("output path may not overwrite the scan target")
    if stat.S_ISDIR(target_stat.st_mode) and output_path.is_relative_to(target_path):
        raise ScanError("output path must be outside the scanned directory")
    protected_output_roots = [
        Path("/etc").resolve(),
        Path("/root").resolve(),
        (Path.home() / ".claude").resolve(),
        (Path.home() / ".ssh").resolve(),
    ]
    if is_protected_path(output_path.as_posix()) or any(
        output_path.is_relative_to(root) for root in protected_output_roots
    ):
        raise ScanError("output path is inside protected runtime or credential storage")
    return output_path


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    return human_report(report)


def exit_for_decision(decision: str) -> int:
    return {"permit": EXIT_PERMIT, "review": EXIT_REVIEW, "block": EXIT_BLOCK}[decision]


def compare_identity(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    expected_identity = previous.get("identity", {})
    actual_identity = current.get("identity", {})
    complete = bool(expected_identity.get("complete")) and bool(actual_identity.get("complete"))
    canonical_matches = previous["artifact_digest"] == current["artifact_digest"]
    expected_source = expected_identity.get("source_sha256")
    actual_source = actual_identity.get("source_sha256")
    source_matches = expected_source == actual_source if expected_source is not None else True
    return {
        "matches": complete and canonical_matches and source_matches,
        "identity_complete": complete,
        "canonical_matches": canonical_matches,
        "source_matches": source_matches,
        "expected_artifact_digest": previous["artifact_digest"],
        "actual_artifact_digest": current["artifact_digest"],
        "expected_source_sha256": expected_source,
        "actual_source_sha256": actual_source,
        "current_decision": current["decision"],
    }


def load_report(path: Path) -> dict[str, Any]:
    if path.expanduser().is_symlink():
        raise ScanError(f"report may not be a symlink: {path}")
    resolved = validate_evidence_path(path, must_exist=True)
    try:
        if resolved.is_symlink() or not resolved.is_file():
            raise ScanError(f"report is not a regular non-symlink file: {resolved}")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ScanError(f"report exceeds the {MAX_FILE_BYTES}-byte limit: {resolved}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved, flags)
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScanError(f"cannot read report {resolved}: {error}") from error
    if data.get("schema_version") != SCHEMA_VERSION or "artifact_digest" not in data:
        raise ScanError(f"unsupported or invalid report: {resolved}")
    if data.get("scanner", {}).get("source_sha256") != scanner_source_sha256():
        raise ScanError("report was not produced by the currently trusted scanner source")
    return data


def build_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old_manifest = {entry["path"]: entry for entry in previous.get("manifest", [])}
    new_manifest = {entry["path"]: entry for entry in current.get("manifest", [])}
    added = sorted(set(new_manifest) - set(old_manifest))
    removed = sorted(set(old_manifest) - set(new_manifest))
    changed = sorted(
        path for path in set(old_manifest) & set(new_manifest)
        if old_manifest[path].get("sha256") != new_manifest[path].get("sha256")
        or old_manifest[path].get("mode") != new_manifest[path].get("mode")
        or old_manifest[path].get("kind") != new_manifest[path].get("kind")
        or old_manifest[path].get("size") != new_manifest[path].get("size")
        or old_manifest[path].get("note") != new_manifest[path].get("note")
    )
    unverifiable = sorted(
        path for path in set(old_manifest) & set(new_manifest)
        if old_manifest[path].get("sha256") is None
        and old_manifest[path].get("kind") != "symlink"
    )
    old_findings = {finding["fingerprint"]: finding for finding in previous.get("findings", [])}
    new_findings = {finding["fingerprint"]: finding for finding in current.get("findings", [])}
    result = dict(current)
    result["comparison"] = {
        "previous_artifact_digest": previous["artifact_digest"],
        "artifact_changed": previous["artifact_digest"] != current["artifact_digest"],
        "source_changed": previous.get("identity", {}).get("source_sha256")
        != current.get("identity", {}).get("source_sha256"),
        "identity_complete": bool(previous.get("identity", {}).get("complete"))
        and bool(current.get("identity", {}).get("complete")),
        "files_added": added,
        "files_removed": removed,
        "files_changed": changed,
        "unverifiable_paths": unverifiable,
        "findings_added": [new_findings[key] for key in sorted(set(new_findings) - set(old_findings))],
        "findings_resolved": [old_findings[key] for key in sorted(set(old_findings) - set(new_findings))],
    }
    return result


def human_diff(report: dict[str, Any]) -> str:
    base = human_report(report)
    comparison = report["comparison"]
    lines = [base, "", "Comparison:"]
    lines.append(f"- Artifact changed: {comparison['artifact_changed']}")
    lines.append(f"- Raw source changed: {comparison['source_changed']}")
    lines.append(f"- Identity complete: {comparison['identity_complete']}")
    lines.append(f"- Files added: {', '.join(comparison['files_added']) or 'none'}")
    lines.append(f"- Files removed: {', '.join(comparison['files_removed']) or 'none'}")
    lines.append(f"- Files changed: {', '.join(comparison['files_changed']) or 'none'}")
    lines.append(f"- Unverifiable paths: {', '.join(comparison['unverifiable_paths']) or 'none'}")
    lines.append(f"- Findings added: {len(comparison['findings_added'])}")
    lines.append(f"- Findings resolved: {len(comparison['findings_resolved'])}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-scan",
        description="Read-only static admission scanner for AI agent skills and plugins.",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan a local file, directory, or bounded archive")
    scan_parser.add_argument("target", type=Path)
    scan_parser.add_argument("--policy", choices=("default", "strict"), default="default")
    scan_parser.add_argument("--format", choices=("human", "json"), default="human")
    scan_parser.add_argument("--output", type=Path)

    diff_parser = subparsers.add_parser("diff", help="compare a previous JSON report with a target")
    diff_parser.add_argument("previous_report", type=Path)
    diff_parser.add_argument("target", type=Path)
    diff_parser.add_argument("--policy", choices=("default", "strict"), default="default")
    diff_parser.add_argument("--format", choices=("human", "json"), default="human")
    diff_parser.add_argument("--output", type=Path)

    verify_parser = subparsers.add_parser("verify", help="verify target byte identity against a report")
    verify_parser.add_argument("report", type=Path)
    verify_parser.add_argument("target", type=Path)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            output_path = validate_output_path(args.output, args.target) if args.output else None
            report = build_report(args.target, args.policy)
            rendered = serialize_report(report, args.format)
            if output_path:
                atomic_write(output_path, rendered)
            else:
                print(rendered)
            return exit_for_decision(report["decision"])
        if args.command == "diff":
            output_path = validate_output_path(args.output, args.target) if args.output else None
            previous = load_report(args.previous_report)
            current = build_report(args.target, args.policy)
            report = build_diff(previous, current)
            rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) if args.format == "json" else human_diff(report)
            if output_path:
                atomic_write(output_path, rendered)
            else:
                print(rendered)
            return exit_for_decision(report["decision"])
        previous = load_report(args.report)
        current = build_report(args.target, previous.get("scanner", {}).get("policy", "default"))
        result = compare_identity(previous, current)
        print(json.dumps(result, indent=2, sort_keys=True))
        return EXIT_PERMIT if result["matches"] else EXIT_BLOCK
    except ScanError as error:
        print(f"scanner error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f"scanner error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(run())
