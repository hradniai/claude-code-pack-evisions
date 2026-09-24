#!/usr/bin/env python3
"""Claude Code status line: three lines, each split into a left and a right column by the width.

Claude Code writes the status line JSON to stdin. Layout:
  line 1: model, effort, output style, active agent        session tokens in/out, session cost
  line 2: project, worktree, git branch                    context used / window size (percent)
  line 3: 5-hour usage with its reset countdown             7-day usage, shown only when ahead of pace

Python 3.9+ (the plugin's minimum), standard library only, no jq. Any missing field or bad input shortens a line instead of
failing. On an unexpected error it prints the model name (or nothing) and still exits 0, so the
status line never shows a traceback.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
ANSI = re.compile(r"\033\[[0-9;]*m")
DOT = f"{DIM}·{RESET}"

WEEK = 604800
DEFAULT_WIDTH = 100
GIT_TIMEOUT_SECONDS = 2


def field(data, *path):
    """The value at `path`, or None when it is absent, null or false (like jq's `// empty`)."""
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if node is None or node is False:
        return None
    return node


def text(data, *path):
    """A field as display text; objects and lists count as missing."""
    value = field(data, *path)
    if value is None or isinstance(value, (dict, list)):
        return ""
    if value is True:
        return "true"
    return str(value)


def number(value):
    """A finite float from a number or a numeric string, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def epoch_seconds(value):
    """A reset time as Unix seconds: from seconds, milliseconds, a numeric string or ISO 8601 text."""
    result = number(value)
    if result is not None:
        return result / 1000 if result > 1e12 else result
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def rounded(value):
    """Round half to even, like awk's printf "%.0f"."""
    return int(f"{value:.0f}")


def visual_length(value):
    return len(ANSI.sub("", value))


def pad_right(left, right, width):
    """Left text, then right text flush with the right edge; at least one space between them."""
    gap = max(width - visual_length(left) - visual_length(right), 1)
    return f"{left}{' ' * gap}{right}"


def usage_color(percent):
    if percent < 50:
        return GREEN
    if percent < 80:
        return YELLOW
    return RED


def format_tokens(value):
    tokens = number(value) or 0.0
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1000:
        return f"{tokens / 1000:.0f}k"
    return str(int(tokens))


def terminal_width():
    columns = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    return columns if columns > 0 else DEFAULT_WIDTH


def git_branch(project_dir):
    """The checked-out branch of project_dir, asked of git directly; empty when unknown."""
    if not project_dir or not os.path.isdir(project_dir):
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "symbolic-ref", "--short", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


def countdown(seconds, with_days=False):
    """'reset in 2d 3h', 'reset in 3h 12m' or 'reset in 12m'."""
    seconds = int(seconds)
    if with_days and seconds >= 86400:
        return f"reset in {seconds // 86400}d {(seconds % 86400) // 3600}h"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"reset in {hours}h {minutes}m"
    return f"reset in {minutes}m"


def line_one(data, width):
    """model . effort . output style . agent                          tokens in/out . cost"""
    left = f"{CYAN}{text(data, 'model', 'display_name')}{RESET}"
    effort = text(data, "effort", "level")
    if effort:
        shown = f"{YELLOW}{effort}{RESET}" if effort in ("high", "max", "xhigh") else effort
        left += f" {DOT} effort:{shown}"
    left += f" {DOT} {text(data, 'output_style', 'name') or 'default'}"
    agent = text(data, "agent", "name")
    if agent:
        left += f" {DOT} {GREEN}▶ {agent}{RESET}"

    cost = ""
    cost_usd = number(field(data, "cost", "total_cost_usd"))
    if cost_usd is not None:
        color = GREEN if cost_usd < 1 else YELLOW if cost_usd <= 5 else RED
        cost = f"{color}${cost_usd:.2f}{RESET}"

    # Session throughput, cumulative and including subagents.
    throughput = ""
    total_in = field(data, "context_window", "total_input_tokens")
    total_out = field(data, "context_window", "total_output_tokens")
    if total_in is not None or total_out is not None:
        throughput = f"{DIM}{format_tokens(total_in)}↑ {format_tokens(total_out)}↓{RESET}"

    right = f"{throughput} {DOT} {cost}" if throughput and cost else cost or throughput
    return pad_right(left, right, width) if right else left


def line_two(data, width):
    """project [wt:name] branch                                        ctx: used/size (percent)"""
    project_dir = text(data, "workspace", "project_dir")
    current_dir = text(data, "workspace", "current_dir")
    location = (project_dir or current_dir).rstrip("/\\")
    project = re.split(r"[/\\]", location)[-1] if location else ""

    worktree = text(data, "worktree", "name")
    if worktree:
        project += f" {DIM}[wt:{RESET}{MAGENTA}{worktree}{RESET}{DIM}]{RESET}"
    branch = git_branch(project_dir) or text(data, "worktree", "branch")
    if branch:
        project += f" {MAGENTA}⎇ {branch}{RESET}"

    percent = number(field(data, "context_window", "used_percentage"))
    if percent is None:
        return project
    # Current window use is percent x window size; total_input_tokens is cumulative for the session.
    size = number(field(data, "context_window", "context_window_size")) or 0.0
    used_k = rounded(percent / 100 * size / 1000)
    size_k = rounded(size / 1000)
    whole = rounded(percent)
    context = f"ctx: {usage_color(whole)}{used_k}k/{size_k}k ({whole}%){RESET}"
    return pad_right(project, context, width)


def line_three(data, width, now):
    """5h: percent (reset countdown)                                 7d: percent (reset countdown)"""
    five = number(field(data, "rate_limits", "five_hour", "used_percentage"))
    if five is None:
        return ""
    five_whole = rounded(five)
    left = f"5h: {usage_color(five_whole)}{five_whole}%{RESET}"

    five_reset = epoch_seconds(field(data, "rate_limits", "five_hour", "resets_at"))
    if five_reset is not None:
        remaining = int(five_reset - now)
        if remaining > 0:
            timer = countdown(remaining)
            # High use with more than an hour left is the signal to slow down, so it turns red.
            if five_whole >= 80 and remaining > 3600:
                left += f" {DIM}({RESET}{RED}{timer}{RESET}{DIM}){RESET}"
            else:
                left += f" {DIM}({timer}){RESET}"

    right = ""
    seven = number(field(data, "rate_limits", "seven_day", "used_percentage"))
    seven_reset = epoch_seconds(field(data, "rate_limits", "seven_day", "resets_at"))
    if seven is not None and seven_reset is not None:
        elapsed = now - (seven_reset - WEEK)
        if 0 < elapsed < WEEK:
            # The share of the week already used at a linear pace; shown only when usage is ahead of it.
            expected = round(elapsed / WEEK * 100, 2)
            if seven > expected:
                ratio = seven / expected if expected > 0 else 1
                # Up to 1.3 times the pace is slightly ahead (yellow); beyond that clearly ahead (red).
                color = RED if ratio >= 1.3 else YELLOW
                right = f"7d: {color}{rounded(seven)}%{RESET}"
                remaining = int(seven_reset - now)
                if remaining > 0:
                    right += f" {DIM}({countdown(remaining, with_days=True)}){RESET}"

    return pad_right(left, right, width) if right else left


def render(data, width, now):
    output = f"{line_one(data, width)}\n{line_two(data, width)}\n"
    third = line_three(data, width, now)
    return output + third if third else output


def report(where, error):
    """One line on stderr naming what failed. Claude Code does not show a status line's stderr, so this
    costs the user nothing, and it makes a failure diagnosable when the script is run by hand."""
    try:
        sys.stderr.write(f"statusline: {where}: {type(error).__name__}: {error}\n")
    except (OSError, ValueError):
        # stderr itself is closed or unusable, so there is nowhere left to report to.
        return


def emit(value):
    encoded = value.encode("utf-8", "replace")
    try:
        sys.stdout.buffer.write(encoded)
        sys.stdout.flush()
    except AttributeError:
        sys.stdout.write(value)
        sys.stdout.flush()
    except OSError as error:
        # Claude Code stopped reading (a newer refresh replaced this one); only stderr is left.
        report("writing the status line", error)


def main():
    data = {}
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        parsed = json.loads(raw) if raw.strip() else {}
        if isinstance(parsed, dict):
            data = parsed
    except (OSError, ValueError, AttributeError) as error:
        # Unreadable or malformed input still gets a (short) status line from the defaults.
        report("reading the status line JSON", error)
        data = {}
    try:
        output = render(data, terminal_width(), time.time())
    except Exception as error:
        # Degrade to the model name alone rather than show a traceback in the status line.
        report("rendering", error)
        output = text(data, "model", "display_name")
    emit(output)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Last line of defence: whatever went wrong, a status line prints nothing rather than a
        # traceback, and exits 0 so Claude Code keeps calling it.
        report("unexpected failure", error)
    sys.exit(0)
