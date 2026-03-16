#!/usr/bin/env python3
"""Inspect local runtime trace logs for recent or specific trace flows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from memory_common import WORKING_TRACE_DIR, ensure_working_files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect runtime trace logs")
    p.add_argument("--trace-id", default="", help="Show only one trace id")
    p.add_argument("--latest-trace", action="store_true", help="Show the latest trace id found in the selected logs")
    p.add_argument("--day", default="", help="YYYY-MM-DD; default reads all runtime-*.log files")
    p.add_argument("--last-events", type=int, default=20, help="How many events to show when not filtering by trace id")
    return p.parse_args()


def trace_files(day: str) -> list[Path]:
    ensure_working_files()
    if day:
        target = WORKING_TRACE_DIR / f"runtime-{day}.log"
        return [target] if target.exists() else []
    return sorted(WORKING_TRACE_DIR.glob("runtime-*.log"))


def load_events(files: list[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            row["_file"] = str(path)
            row["_lineno"] = lineno
            events.append(row)
    return events


def pick_latest_trace_id(events: list[dict[str, Any]]) -> str:
    for row in reversed(events):
        trace_id = str(row.get("trace_id", "")).strip()
        if trace_id:
            return trace_id
    return ""


def format_event(row: dict[str, Any]) -> str:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    detail_bits: list[str] = []
    for key in ("mode", "fallback_reason", "review_code", "memory_candidate_count", "append_returncode"):
        value = details.get(key)
        if value not in ("", None, False):
            detail_bits.append(f"{key}={value}")
    if row.get("duration_ms") is not None:
        detail_bits.append(f"duration_ms={row.get('duration_ms')}")
    if row.get("error"):
        detail_bits.append(f"error={row.get('error')}")
    suffix = f" :: {'; '.join(detail_bits)}" if detail_bits else ""
    return (
        f"- {row.get('at', '')} [{row.get('status', '')}] "
        f"{row.get('action', '')} script={row.get('script', '')}{suffix}"
    )


def render_summary(events: list[dict[str, Any]], *, selected_trace_id: str = "") -> str:
    lines = ["# Runtime Trace Inspect"]
    if not events:
        lines.append("- result: no events found")
        return "\n".join(lines)

    statuses = Counter(str(row.get("status", "")) for row in events)
    actions = [str(row.get("action", "")) for row in events if str(row.get("action", ""))]
    trace_ids = [str(row.get("trace_id", "")) for row in events if str(row.get("trace_id", ""))]

    lines.append(f"- event_count: {len(events)}")
    lines.append(f"- trace_count: {len(set(trace_ids))}")
    if selected_trace_id:
        lines.append(f"- selected_trace_id: {selected_trace_id}")
    lines.append(f"- statuses: {dict(statuses)}")
    if actions:
        lines.append(f"- actions: {', '.join(actions)}")
    lines.append("")
    lines.append("## Events")
    for row in events:
        lines.append(format_event(row))
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    files = trace_files(args.day)
    events = load_events(files)
    selected_trace_id = args.trace_id.strip()

    if args.latest_trace and not selected_trace_id:
        selected_trace_id = pick_latest_trace_id(events)

    if selected_trace_id:
        events = [row for row in events if str(row.get("trace_id", "")).strip() == selected_trace_id]
    elif args.last_events > 0:
        events = events[-args.last_events :]

    print(render_summary(events, selected_trace_id=selected_trace_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
