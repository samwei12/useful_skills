#!/usr/bin/env python3
"""Migrate legacy daily memory log entries to the minimal P0 schema."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from memory_common import make_memory_id, normalize_inline_text

ENTRY_HEADER = "### entry"
LEGACY_KEYS = {"time", "topic", "decision", "evidence", "impact", "confidence"}
P0_KEYS = {"time", "id", "topic", "type", "summary", "evidence"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate legacy memory log entries to P0 schema")
    p.add_argument("--path", required=True, help="Daily log file path")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--in-place", action="store_true")
    return p.parse_args()


def split_blocks(text: str) -> list[str]:
    markers = [m.start() for m in re.finditer(r"^### entry\s*$", text, flags=re.MULTILINE)]
    if not markers:
        return []
    blocks: list[str] = []
    for i, start in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(text)
        blocks.append(text[start:end].strip())
    return blocks


def parse_block(block: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    extras: list[str] = []
    for line in block.splitlines()[1:]:
        m = re.match(r"^- ([A-Za-z0-9_-]+):\s*(.*)$", line.strip())
        if m:
            values[m.group(1)] = normalize_inline_text(m.group(2))
        else:
            extras.append(line)
    return values, extras


def is_p0(values: dict[str, str]) -> bool:
    return {"time", "id", "topic", "type", "summary"}.issubset(values.keys())


def infer_type(topic: str) -> str:
    return "feedback" if topic.startswith("feedback_") else "fact"


def merge_summary(decision: str, impact: str) -> str:
    decision = normalize_inline_text(decision)
    impact = normalize_inline_text(impact)
    if decision and impact:
        return f"{decision} {impact}"
    return decision or impact


def convert_block(block: str) -> tuple[str, bool]:
    values, extras = parse_block(block)
    if is_p0(values):
        return block.strip() + "\n", False

    if not LEGACY_KEYS.intersection(values.keys()):
        return block.strip() + "\n", False

    time_value = values.get("time", "")
    topic = values.get("topic", "")
    summary = merge_summary(values.get("decision", ""), values.get("impact", ""))
    evidence = values.get("evidence", "")
    entry_type = infer_type(topic)
    entry_id = make_memory_id()

    lines = [
        ENTRY_HEADER,
        f"- time: {time_value}",
        f"- id: {entry_id}",
        f"- topic: {topic}",
        f"- type: {entry_type}",
        f"- summary: {summary}",
    ]
    if evidence:
        lines.append(f"- evidence: {evidence}")
    if extras:
        lines.extend(extras)
    return "\n".join(lines).strip() + "\n", True


def migrate_text(text: str) -> tuple[str, int]:
    blocks = split_blocks(text)
    if not blocks:
        return text, 0
    migrated: list[str] = []
    converted = 0
    cursor = 0
    markers = [m.start() for m in re.finditer(r"^### entry\s*$", text, flags=re.MULTILINE)]
    for i, start in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(text)
        migrated.append(text[cursor:start])
        converted_block, changed = convert_block(text[start:end])
        migrated.append(converted_block)
        converted += 1 if changed else 0
        cursor = end
    migrated.append(text[cursor:])
    return "".join(migrated), converted


def main() -> int:
    args = parse_args()
    path = Path(args.path).expanduser()
    original = path.read_text(encoding="utf-8")
    migrated, converted = migrate_text(original)
    print(f"converted_entries={converted}")
    if args.dry_run:
        print(migrated)
        return 0
    if not args.in_place:
        raise SystemExit("use --in-place to overwrite the file")
    path.write_text(migrated, encoding="utf-8")
    print(f"updated_file={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
