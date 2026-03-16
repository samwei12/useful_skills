#!/usr/bin/env python3
"""Normalize malformed daily memory logs into canonical entry structure.

This script focuses on safe, local formatting fixes:
- Insert missing '### entry' before standalone '- time:' blocks
- Convert indented 'time:/id:/topic:/type:/summary:/evidence:' to list items
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from memory_common import date_str_shanghai, day_log_path


def target_file(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg).expanduser()
    return day_log_path(date_str_shanghai())


def normalize_lines(lines: list[str]) -> tuple[list[str], int]:
    out: list[str] = []
    fixes = 0

    for idx, line in enumerate(lines):
        # Convert malformed indented key lines to list-item lines.
        m = re.match(r"^\s+(time|id|topic|type|summary|evidence):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2)
            line = f"- {key}: {value}".rstrip()
            fixes += 1

        # Insert missing entry marker before standalone '- time:'
        if re.match(r"^- time:\s", line):
            prev_non_empty = None
            for back in range(len(out) - 1, -1, -1):
                if out[back].strip():
                    prev_non_empty = out[back].strip()
                    break
            if prev_non_empty != "### entry":
                if out and out[-1].strip() != "":
                    out.append("")
                out.append("### entry")
                fixes += 1

        out.append(line)

    return out, fixes


def main() -> int:
    p = argparse.ArgumentParser(description="Normalize daily memory log format")
    p.add_argument("--path", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--backup", action="store_true", help="Create .bak.<timestamp> before overwrite")
    args = p.parse_args()

    path = target_file(args.path)
    if not path.exists():
        print(f"[ERROR] file not found: {path}")
        return 2

    original = path.read_text(encoding="utf-8")
    normalized_lines, fixes = normalize_lines(original.splitlines())
    normalized = "\n".join(normalized_lines).rstrip() + "\n"

    if args.dry_run:
        print(f"[DRY-RUN] {path}")
        print(f"- fixes: {fixes}")
        print(f"- changed: {normalized != original}")
        return 0

    if normalized == original:
        print(f"[OK] no changes: {path}")
        return 0

    if args.backup:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        backup.write_text(original, encoding="utf-8")
        print(f"[OK] backup created: {backup}")

    path.write_text(normalized, encoding="utf-8")
    print(f"[OK] normalized: {path}")
    print(f"- fixes: {fixes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
