#!/usr/bin/env python3
"""Static hygiene check of a prompt file.

It never calls a model, never reads environment values and never prints the text it flagged, so a secret found
in a prompt travels no further. PASS means no hygiene problem was detected; it says nothing about whether the
prompt produces good output.

Exit codes: 0 for PASS or WARN, 20 for FAIL (a missing, unreadable or empty file included), 2 for bad arguments.
Python 3.9 or newer, standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


NOTE = (
    "Static hygiene check: PASS means none of its heuristics fired, its keyword checks cannot confirm that an output"
    " contract or fallback is actually usable, and it does not measure whether the prompt produces good output."
)

# Credential checks scan the whole file, header included: a key in a header leaks as surely as one in the body.
SECRET_PATTERNS = (
    r"(?<![A-Za-z0-9_-])sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{12,}",  # OpenAI and Anthropic style API keys
    r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{20,}",  # Google API keys
    r"(?<![A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{10,}",  # Slack tokens
    r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",  # GitHub tokens
    r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![0-9A-Z])",  # AWS access key IDs
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)
CHECKS = {
    "secret_like_literal": (
        re.compile("|".join(SECRET_PATTERNS)),
        "Text that looks like an API key, token or private key (value not printed)",
    ),
    # Only a real-looking value counts: an empty value, a <placeholder> or a $VARIABLE reference is not a credential.
    "environment_assignment": (
        re.compile(
            r"(?mi)^[ \t]*(?:export[ \t]+|set[ \t]+|\$env:)?[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)"
            r"[ \t]*=[ \t]*[\"']?(?=[^\s\"'<>${}%.])"
        ),
        "A line assigns a value to a credential variable such as NAME_API_KEY (value not printed)",
    ),
}
# Keyword heuristics. Czech terms cover prompts the user asked to have written in Czech.
ESCAPE_HATCH = re.compile(
    r"\b(?:not[ _-]?found|uncertain|unverified|unverifiable|unknown|null|n/a|insufficient"
    r"|cannot (?:be )?(?:verified|determined)|(?:do not|don't) know"
    r"|nev[ií]m|nenalezen\w*|nezn[aá]m\w*|neov[eě][rř]en\w*|nejist\w*|nelze (?:ur[cč]it|ov[eě][rř]it))",
    re.IGNORECASE,
)
OUTPUT_CONTRACT = re.compile(
    r"\b(?:return|output|respond with|json|yaml|markdown"
    r"|vra[tť]\w*|v[yý]stup\w*|odpov[eě]z\w*|odpov[ií]dej\w*)",
    re.IGNORECASE,
)
INTERPOLATION = re.compile(r"\{\{[^}]*\}\}")
# A statement that injected content is data, not instructions: wrapping it in a tag alone does not tell the model that.
DATA_DECLARATION = re.compile(
    r"\b(?:treat|handle|regard|consider)\b[^.\n]{0,80}?\bas (?:data|content|untrusted)\b"
    r"|\b(?:not|never)\s+(?:as\s+)?(?:instructions?|commands?)\b"
    r"|\b(?:do not|don't|never)\s+(?:follow|obey|execute|carry out|act on)\b[^.\n]{0,60}?\b(?:instructions?|commands?)\b"
    r"|\b(?:ignore|disregard)\b[^.\n]{0,40}?\binstructions?\b[^.\n]{0,20}?\b(?:in|inside|within|contained|found)\b"
    r"|\buntrusted\b"
    r"|\b(?:ber|berte|br[aá]t|pova[zž]uj|pova[zž]ujte)\b[^.\n]{0,80}?\b(?:jako|za) (?:data|vstup|obsah)\b"
    r"|\b(?:nejsou|nen[ií]|nejde o)\s+(?:to\s+)?(?:pokyn|instrukc|p[rř][ií]kaz)\w*"
    r"|\b(?:ne|nikdy)\s+(?:jako\s+)?(?:pokyny|instrukce|p[rř][ií]kazy)\b"
    r"|\b(?:ne[rř]i[dď]|ne[rř]i[dď]te|nevykon[aá]vej|nevykon[aá]vejte|nepl[nň]|nepl[nň]te|ignoruj|ignorujte)\b"
    r"[^.\n]{0,40}?\b(?:pokyn|instrukc|p[rř][ií]kaz)\w*"
    r"|\bned[uů]v[eě]ryhodn\w*",
    re.IGNORECASE,
)

FRONTMATTER = re.compile(r"\A---[ \t]*\n(?:.*\n)*?(?:---|\.\.\.)[ \t]*(?:\n|\Z)")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

OPEN_TAG = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)(?:\s[^<>]*)?>")
# Stricter shapes for the unclosed-tag check: attributes must be name="value", so a <placeholder in words> never counts.
ATTRIBUTES = r"(?:\s+[\w:.-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*"
SAME_LINE_ELEMENT = re.compile(rf"<([A-Za-z][\w.-]*){ATTRIBUTES}\s*>[^\n]*?</\1\s*>", re.IGNORECASE)
STANDALONE_OPEN = re.compile(rf"(?m)^[ \t]*<([A-Za-z][\w.-]*){ATTRIBUTES}\s*>[ \t]*$")
CLOSE_TAG = re.compile(r"</([A-Za-z][\w.-]*)\s*>")
# HTML elements that never take a closing tag. Names that double as prompt sections (input, source) stay checked.
VOID_ELEMENTS = frozenset({"area", "br", "embed", "hr", "img", "link", "meta", "wbr"})


def is_inside_xml_element(text: str, start: int, end: int) -> bool:
    """True when some <tag> opened before `start` is still open there and closed after `end`.

    Angle-bracket tokens that are not tags (`<jan@example.cz>`, `<https://...>`, `<br/>`) never match OPEN_TAG,
    so they cannot open an element and cannot crash the check.
    """
    before, after = text[:start], text[end:]
    last_open: dict[str, int] = {}
    for match in OPEN_TAG.finditer(before):
        last_open[match.group(1).lower()] = match.start()
    for tag, position in last_open.items():
        closer = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE)
        if not closer.search(before, position) and closer.search(after):
            return True
    return False


def blank_frontmatter(text: str) -> str:
    """Replace a leading YAML header with empty lines, so header keys cannot satisfy a body check and line numbers hold."""
    match = FRONTMATTER.match(text)
    if not match:
        return text
    return "\n" * match.group(0).count("\n") + text[match.end():]


def blank_fenced_code(text: str) -> str:
    """Replace fenced code blocks with empty lines, so a code example is not read as the prompt's own structure."""
    lines = text.split("\n")
    fence = ""
    for index, line in enumerate(lines):
        if not fence:
            match = FENCE.match(line)
            if match:
                fence = match.group(1)
                lines[index] = ""
            continue
        stripped = line.strip()
        if stripped.startswith(fence) and not stripped.strip(fence[0]):
            fence = ""
        lines[index] = ""
    return "\n".join(lines)


def unclosed_tags(text: str) -> list[tuple[str, int]]:
    """Section tags that are opened and never closed, as (tag, line of the opener).

    Only a tag standing alone on its line opens a section. A tag named in a sentence ("treat the text inside
    <email> as data") or opened and closed on one line is not a section boundary and is ignored.
    """
    text = SAME_LINE_ELEMENT.sub("", text)
    events = [(match.start(), 1, match.group(1).lower()) for match in STANDALONE_OPEN.finditer(text)]
    events += [(match.start(), 0, match.group(1).lower()) for match in CLOSE_TAG.finditer(text)]
    open_positions: dict[str, list[int]] = {}
    for position, is_opener, tag in sorted(events):
        if tag in VOID_ELEMENTS:
            continue
        stack = open_positions.setdefault(tag, [])
        if is_opener:
            stack.append(position)
        elif stack:
            stack.pop()
    leftovers = sorted((position, tag) for tag, stack in open_positions.items() for position in stack)
    return [(tag, line_of(text, position)) for position, tag in leftovers]


def line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def finding(level: str, rule: str, message: str, line: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"level": level, "rule": rule, "message": message}
    if line is not None:
        result["line"] = line
    return result


def emit(path: Path, findings: list[dict[str, object]]) -> int:
    status = "FAIL" if any(item["level"] == "FAIL" for item in findings) else "WARN" if findings else "PASS"
    # ASCII-escaped JSON prints on any console encoding, a Windows code page included.
    print(json.dumps({"status": status, "prompt": str(path), "findings": findings, "note": NOTE}))
    return 20 if status == "FAIL" else 0


def check(text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for rule, (pattern, message) in CHECKS.items():
        for match in pattern.finditer(text):
            findings.append(finding("FAIL", rule, message, line_of(text, match.start())))

    body = blank_frontmatter(text)
    if not text.strip():
        findings.append(finding("FAIL", "empty_prompt", "Prompt file is empty"))
        return findings
    if not body.strip():
        findings.append(finding("FAIL", "empty_prompt", "Prompt has a header but no body"))
        return findings

    # A placeholder such as {{ $json.output }} names a variable, not an instruction, so it cannot satisfy a keyword check.
    prose = INTERPOLATION.sub(" ", body)
    if not OUTPUT_CONTRACT.search(prose):
        findings.append(finding("WARN", "missing_output_contract", "No output-contract keyword detected"))
    if not ESCAPE_HATCH.search(prose):
        findings.append(finding("WARN", "missing_escape_hatch", "No escape-hatch keyword detected for missing or unverifiable input"))
    for match in INTERPOLATION.finditer(body):
        if not is_inside_xml_element(body, match.start(), match.end()):
            findings.append(finding("WARN", "dynamic_input_boundary", "Template interpolation detected without an enclosing XML element that marks it as untrusted input", line_of(body, match.start())))
            break
    first_interpolation = INTERPOLATION.search(body)
    if first_interpolation and not DATA_DECLARATION.search(prose):
        findings.append(finding("WARN", "untrusted_input_not_declared", "Template interpolation detected but no statement that injected content is data, not instructions", line_of(body, first_interpolation.start())))
    for tag, line in unclosed_tags(blank_fenced_code(body)):
        findings.append(finding("WARN", "unclosed_xml_tag", f"Section tag <{tag}> is opened but never closed", line))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Static hygiene check of a prompt file. Calls no model.")
    parser.add_argument("--prompt", required=True, help="path to the prompt file (UTF-8 text)")
    args = parser.parse_args()
    path = Path(args.prompt)

    if not path.is_file():
        return emit(path, [finding("FAIL", "missing_file", "Prompt file does not exist")])
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return emit(path, [finding("FAIL", "unreadable_file", "Prompt file is not UTF-8 text")])
    except OSError as error:
        return emit(path, [finding("FAIL", "unreadable_file", f"Prompt file could not be read ({type(error).__name__})")])
    return emit(path, check(text))


if __name__ == "__main__":
    raise SystemExit(main())
