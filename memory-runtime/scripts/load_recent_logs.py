#!/usr/bin/env python3
"""Load recent daily memory logs and create missing files automatically."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from memory_common import ensure_day_file, last_n_days


def extract_last_topics(text: str, limit: int = 5) -> list[str]:
    topics = re.findall(r"^- topic: (.+)$", text, flags=re.MULTILINE)
    return topics[-limit:]


def render_summary(day: str, path: Path, text: str, topic_limit: int) -> str:
    entry_count = text.count("### entry")
    topics = extract_last_topics(text, topic_limit)
    lines = [
        f"## {day}",
        f"- file: {path}",
        f"- entry_count: {entry_count}",
    ]
    if topics:
        lines.append("- recent_topics:")
        for t in topics:
            lines.append(f"  - {t}")
    else:
        lines.append("- recent_topics: (none)")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Load recent memory logs")
    p.add_argument("--days", type=int, default=3, help="How many recent days to load")
    p.add_argument("--mode", choices=["summary", "full"], default="summary")
    p.add_argument("--topic-limit", type=int, default=5, help="Topics to show in summary mode")
    args = p.parse_args()

    days = last_n_days(max(1, args.days))
    print("# Recent Memory Logs")
    print(f"- days: {', '.join(days)}")
    print(f"- mode: {args.mode}")

    for day in days:
        path = ensure_day_file(day)
        text = path.read_text(encoding="utf-8")
        print("")
        if args.mode == "full":
            print(f"## {day}")
            print(f"- file: {path}")
            print("\n```markdown")
            print(text.rstrip())
            print("```")
            continue

        print(render_summary(day, path, text, args.topic_limit))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
