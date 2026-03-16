#!/usr/bin/env python3
"""Append one normalized memory entry to today's log (Asia/Shanghai)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import fcntl

from memory_common import (
    WORKING_STATE_FILE,
    date_str_shanghai,
    ensure_day_file,
    ensure_working_files,
    iso_ts_shanghai,
    make_memory_id,
    normalize_inline_text,
)
from runtime_trace import build_trace_env, current_trace_id, emit_runtime_trace

ALLOWED_TYPES = {"fact", "feedback"}


def load_state_json(path: Path) -> dict:
    ensure_working_files()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        ensure_working_files()
        text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append one memory entry")
    p.add_argument("--topic", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--evidence", default="")
    p.add_argument("--type", default="fact", choices=sorted(ALLOWED_TYPES))
    p.add_argument("--soft-max", type=int, default=500)
    p.add_argument("--hard-max", type=int, default=1500)
    p.add_argument("--skip-working-refresh", action="store_true")
    p.add_argument("--skip-working-sweep", action="store_true")
    p.add_argument("--sweep-every-appends", type=int, default=20)
    p.add_argument("--sweep-decay", type=int, default=2)
    p.add_argument("--skip-working-review", action="store_true")
    p.add_argument("--review-every-appends", type=int, default=10)
    p.add_argument("--review-days", type=int, default=7)
    p.add_argument("--review-top-k", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def build_entry(*, timestamp: str, entry_id: str, topic: str, entry_type: str, summary: str, evidence: str) -> str:
    lines = [
        "### entry",
        f"- time: {timestamp}",
        f"- id: {entry_id}",
        f"- topic: {topic}",
        f"- type: {entry_type}",
        f"- summary: {summary}",
    ]
    if evidence:
        lines.append(f"- evidence: {evidence}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    topic = normalize_inline_text(args.topic)
    summary = normalize_inline_text(args.summary)
    evidence = normalize_inline_text(args.evidence)
    entry_type = normalize_inline_text(args.type).lower()

    required = {"topic": topic, "summary": summary, "type": entry_type}
    for key, value in required.items():
        if not value:
            print(f"[ERROR] {key} is empty after normalization", file=sys.stderr)
            return 2
    if entry_type not in ALLOWED_TYPES:
        print(f"[ERROR] type must be one of {sorted(ALLOWED_TYPES)}", file=sys.stderr)
        return 2

    part_lengths = {"topic": len(topic), "summary": len(summary), "evidence": len(evidence)}
    payload_len = sum(part_lengths.values())
    length_detail = ", ".join(f"{k}={v}" for k, v in part_lengths.items())

    if args.soft_max >= args.hard_max:
        print(f"[ERROR] invalid thresholds: soft-max {args.soft_max} must be less than hard-max {args.hard_max}", file=sys.stderr)
        return 2
    if args.sweep_every_appends <= 0 or args.review_every_appends <= 0:
        print("[ERROR] sweep/review interval must be > 0", file=sys.stderr)
        return 2
    if args.sweep_decay < 0 or args.review_days <= 0 or args.review_top_k <= 0:
        print("[ERROR] invalid maintenance arguments", file=sys.stderr)
        return 2

    if payload_len > args.hard_max:
        print(f"[ERROR][MEMORY_WRITE_BLOCKED_HARD] payload={payload_len} > hard-max={args.hard_max}; {length_detail}", file=sys.stderr)
        print("写入已拒绝。请先压缩为 <= 500 字符后再重试。", file=sys.stderr)
        return 3
    if payload_len > args.soft_max:
        print(f"[WARN][MEMORY_WRITE_BLOCKED_SOFT] payload={payload_len} in ({args.soft_max}, {args.hard_max}]; {length_detail}", file=sys.stderr)
        print("本次写入已暂停。请先压缩到 <= 500 字符后再重试。", file=sys.stderr)
        return 4

    script_name = Path(__file__).name
    trace_id = current_trace_id()
    started_at = time.perf_counter()
    day_path = ensure_day_file(date_str_shanghai())
    timestamp = iso_ts_shanghai()
    entry_id = make_memory_id()
    entry_block = build_entry(
        timestamp=timestamp,
        entry_id=entry_id,
        topic=topic,
        entry_type=entry_type,
        summary=summary,
        evidence=evidence,
    )
    emit_runtime_trace(
        action="append_entry",
        status="started",
        script=script_name,
        trace_id=trace_id,
        input_text=f"{topic} {summary}",
        artifacts_written=[day_path],
        details={"entry_type": entry_type, "entry_id": entry_id},
    )

    if args.dry_run:
        emit_runtime_trace(
            action="append_entry",
            status="dry_run",
            script=script_name,
            trace_id=trace_id,
            input_text=f"{topic} {summary}",
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            details={"entry_type": entry_type, "entry_id": entry_id},
        )
        print(f"[DRY-RUN] target={day_path}")
        print(entry_block)
        return 0

    try:
        with day_path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                current = f.read()
                if current and not current.endswith("\n"):
                    f.write("\n")
                f.write("\n" + entry_block)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        emit_runtime_trace(
            action="append_entry",
            status="failed",
            script=script_name,
            trace_id=trace_id,
            input_text=f"{topic} {summary}",
            artifacts_written=[day_path],
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=str(exc),
            details={"entry_type": entry_type, "entry_id": entry_id},
        )
        raise

    print(f"[OK] appended entry to {day_path}")

    maintenance_updated = False
    maintenance: dict[str, object] = {}
    state_path = None
    if not args.skip_working_sweep or not args.skip_working_review:
        ensure_working_files()
        state_path = WORKING_STATE_FILE
        state = load_state_json(state_path)
        maintenance = state.setdefault("maintenance", {})
        if not args.skip_working_sweep:
            maintenance["append_since_sweep"] = int(maintenance.get("append_since_sweep", 0)) + 1
            maintenance["sweep_every_appends"] = args.sweep_every_appends
        if not args.skip_working_review:
            maintenance["append_since_review"] = int(maintenance.get("append_since_review", 0)) + 1
            maintenance["review_every_appends"] = args.review_every_appends
        maintenance["last_counter_update_at"] = timestamp
        maintenance.setdefault("last_sweep_trigger", "")
        maintenance.setdefault("last_sweep_scheduled_at", "")
        maintenance.setdefault("last_review_trigger", "")
        maintenance.setdefault("last_review_scheduled_at", "")
        maintenance.setdefault("last_review_completed_at", "")
        maintenance_updated = True

    if not args.skip_working_refresh:
        refresh_script = Path(__file__).with_name("refresh_working_cache.py")
        if refresh_script.exists():
            cmd = [
                sys.executable,
                str(refresh_script),
                "--source-time",
                timestamp,
                "--id",
                entry_id,
                "--topic",
                topic,
                "--type",
                entry_type,
                "--summary",
                summary,
                "--evidence",
                evidence,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, env=build_trace_env(trace_id=trace_id))
            if proc.returncode != 0:
                print("[WARN] working cache refresh failed; entry append already committed", file=sys.stderr)
                if proc.stderr.strip():
                    print(proc.stderr.strip(), file=sys.stderr)
            elif proc.stdout.strip():
                print(proc.stdout.strip())

    if maintenance_updated and state_path is not None:
        state = load_state_json(state_path)
        state["maintenance"] = maintenance
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts_written = [day_path]
    if maintenance_updated and state_path is not None:
        artifacts_written.append(state_path)
    emit_runtime_trace(
        action="append_entry",
        status="ok",
        script=script_name,
        trace_id=trace_id,
        input_text=f"{topic} {summary}",
        artifacts_written=artifacts_written,
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        details={
            "entry_type": entry_type,
            "entry_id": entry_id,
            "working_refresh_attempted": not args.skip_working_refresh,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
