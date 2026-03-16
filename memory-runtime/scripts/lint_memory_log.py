#!/usr/bin/env python3
"""Lint daily memory log format and quality constraints."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from memory_common import date_str_shanghai, day_log_path

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+0800$")


def target_file(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser()
        return p
    return day_log_path(date_str_shanghai())


def split_entries(text: str) -> list[str]:
    markers = [m.start() for m in re.finditer(r"^### entry\s*$", text, flags=re.MULTILINE)]
    if not markers:
        return []
    entries: list[str] = []
    for i, start in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(text)
        block = text[start + len("### entry") : end].strip()
        if block:
            entries.append(block)
    return entries


def check_entry(entry: str, idx: int, hard_max: int, strict_size: bool) -> tuple[list[str], list[str]]:
    errs = []
    warns = []
    lines = entry.splitlines()
    values = {}
    for key in ["time", "id", "topic", "type", "summary"]:
        m = [ln for ln in lines if ln.startswith(f"- {key}: ")]
        if not m:
            errs.append(f"entry#{idx}: missing '- {key}:'")
            continue
        if len(m) > 1:
            errs.append(f"entry#{idx}: duplicated '- {key}:'")
        values[key] = m[0].replace(f"- {key}: ", "", 1).strip()

    t = values.get("time")
    if t and not TIME_RE.match(t):
        errs.append(f"entry#{idx}: invalid time format '{t}'")

    ev = values.get("evidence", "")
    if ev in {"|", ">"}:
        warns.append(
            f"entry#{idx}: evidence uses block scalar marker '{ev}', "
            "legacy multi-line payload not fully size-checked"
        )

    size = sum(len(values.get(k, "")) for k in ["topic", "summary", "evidence"])
    if size > hard_max:
        msg = f"entry#{idx}: payload too long {size} > {hard_max}"
        if strict_size:
            errs.append(msg)
        else:
            warns.append(msg)

    return errs, warns


def main() -> int:
    p = argparse.ArgumentParser(description="Lint memory daily log")
    p.add_argument("--path", default=None, help="Path to daily log file")
    p.add_argument("--hard-max", type=int, default=1500)
    p.add_argument("--strict-size", action="store_true", help="Treat payload over hard-max as lint error")
    args = p.parse_args()

    path = target_file(args.path)
    if not path.exists():
        print(f"[ERROR] file not found: {path}")
        return 2

    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    if "# Daily Memory Log" not in text:
        errors.append("missing header '# Daily Memory Log'")
    if "## entries" not in text:
        errors.append("missing section '## entries'")

    # Detect malformed blocks like '  topic:' without list marker.
    for i, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^\s+topic:\s", line):
            errors.append(f"line {i}: malformed 'topic:' line (missing '- ')")
        if re.match(r"^\s+summary:\s", line):
            errors.append(f"line {i}: malformed 'summary:' line (missing '- ')")

    entries = split_entries(text)
    for idx, entry in enumerate(entries, start=1):
        entry_errors, entry_warnings = check_entry(entry, idx, args.hard_max, args.strict_size)
        errors.extend(entry_errors)
        warnings.extend(entry_warnings)

    if errors:
        print(f"[FAIL] {path}")
        for err in errors:
            print(f"- {err}")
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"- {warning}")
        print(f"Summary: entries={len(entries)} errors={len(errors)}")
        return 1

    print(f"[PASS] {path}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    print(f"Summary: entries={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
