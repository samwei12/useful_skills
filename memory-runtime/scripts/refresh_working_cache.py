#!/usr/bin/env python3
"""Refresh working-memory cache from one entry or a memory log file."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from memory_common import WORKING_CACHE_FILE, WORKING_STATE_FILE, iso_ts_shanghai, normalize_inline_text
from runtime_trace import build_trace_env, current_trace_id, emit_runtime_trace
from working_common import load_archive, load_cache, save_cache, update_state


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh working cache from memory entries")
    p.add_argument("--source-time")
    p.add_argument("--id")
    p.add_argument("--topic")
    p.add_argument("--type")
    p.add_argument("--summary")
    p.add_argument("--evidence", default="")
    p.add_argument("--from-log", help="Parse entries from a daily memory log")
    p.add_argument("--latest-only", action="store_true", help="Only process the last entry from --from-log")
    p.add_argument("--skip-vector-refresh", action="store_true", help="Skip semantic vector index upsert after cache refresh")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def parse_entry_blocks_from_log(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"log file not found: {path}")
    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("### entry"):
            if current:
                entries.append(current)
            current = {}
            continue
        if current is None:
            continue
        match = re.match(r"^- (time|id|topic|type|summary|evidence):\s*(.+)$", line)
        if match:
            current[match.group(1)] = normalize_inline_text(match.group(2))
    if current:
        entries.append(current)
    return entries


def build_item(entry: dict[str, str]) -> dict[str, str]:
    return {
        "id": normalize_inline_text(entry.get("id", "")),
        "topic": normalize_inline_text(entry.get("topic", "")),
        "type": normalize_inline_text(entry.get("type", "fact")).lower() or "fact",
        "summary": normalize_inline_text(entry.get("summary", "")),
        "evidence": normalize_inline_text(entry.get("evidence", "")),
        "logged_at": normalize_inline_text(entry.get("time", iso_ts_shanghai())),
    }


def index_by_id(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        rid = str(row.get("id", "")).strip()
        if rid:
            out[rid] = row
    return out


def main() -> int:
    args = parse_args()
    script_name = Path(__file__).name
    trace_id = current_trace_id()
    started_at = time.perf_counter()

    explicit_mode = all(getattr(args, key) for key in ("source_time", "id", "topic", "type", "summary"))
    from_log_mode = bool(args.from_log)

    if explicit_mode and from_log_mode:
        raise SystemExit("choose either explicit fields mode or --from-log mode")
    if not explicit_mode and not from_log_mode:
        raise SystemExit("missing input: provide explicit fields or --from-log")

    entries: list[dict[str, str]]
    if from_log_mode:
        entries = parse_entry_blocks_from_log(Path(args.from_log))
        if args.latest_only and entries:
            entries = [entries[-1]]
    else:
        entries = [
            {
                "time": normalize_inline_text(args.source_time),
                "id": normalize_inline_text(args.id),
                "topic": normalize_inline_text(args.topic),
                "type": normalize_inline_text(args.type).lower(),
                "summary": normalize_inline_text(args.summary),
                "evidence": normalize_inline_text(args.evidence),
            }
        ]
    input_preview = args.from_log or " ".join(
        part for part in (getattr(args, "topic", ""), getattr(args, "summary", "")) if part
    )
    emit_runtime_trace(
        action="refresh_working_cache",
        status="started",
        script=script_name,
        trace_id=trace_id,
        input_text=input_preview,
        artifacts_read=[args.from_log] if args.from_log else [WORKING_CACHE_FILE],
        artifacts_written=[WORKING_CACHE_FILE, WORKING_STATE_FILE],
        details={"from_log_mode": from_log_mode, "latest_only": bool(args.latest_only)},
    )

    try:
        incoming = [build_item(entry) for entry in entries]
        cache = load_cache()
        archive = load_archive()
        cache_idx = index_by_id(cache)

        created = 0
        updated = 0
        for item in incoming:
            if not item["id"] or not item["topic"] or not item["summary"]:
                continue
            old = cache_idx.get(item["id"])
            if old:
                old.update(item)
                updated += 1
                continue
            cache.append(dict(item))
            cache_idx[item["id"]] = item
            created += 1

        cache = sorted(cache, key=lambda row: (str(row.get("logged_at", "")), str(row.get("id", ""))), reverse=True)

        if args.dry_run:
            emit_runtime_trace(
                action="refresh_working_cache",
                status="dry_run",
                script=script_name,
                trace_id=trace_id,
                input_text=input_preview,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                details={"entries": len(entries), "items": len(incoming), "created": created, "updated": updated},
            )
            print(f"[DRY-RUN] refresh_working_cache entries={len(entries)} items={len(incoming)} created={created} updated={updated}")
            return 0

        save_cache(cache)
        update_state(cache, archive, note="refresh_working_cache")

        vector_refresh_returncode = None
        if not args.skip_vector_refresh:
            refresh_vector_script = Path(__file__).with_name("refresh_vector_index.py")
            if refresh_vector_script.exists():
                proc = subprocess.run(
                    [sys.executable, str(refresh_vector_script)],
                    capture_output=True,
                    text=True,
                    env=build_trace_env(trace_id=trace_id),
                )
                vector_refresh_returncode = proc.returncode
                if proc.returncode != 0:
                    print("[WARN] vector index refresh failed; working cache refresh already committed")
                    if proc.stderr.strip():
                        print(proc.stderr.strip())
                elif proc.stdout.strip():
                    print(proc.stdout.strip())

        emit_runtime_trace(
            action="refresh_working_cache",
            status="ok",
            script=script_name,
            trace_id=trace_id,
            input_text=input_preview,
            artifacts_read=[args.from_log] if args.from_log else [WORKING_CACHE_FILE],
            artifacts_written=[WORKING_CACHE_FILE, WORKING_STATE_FILE],
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            details={
                "entries": len(entries),
                "items": len(incoming),
                "created": created,
                "updated": updated,
                "vector_refresh_attempted": not args.skip_vector_refresh,
                "vector_refresh_returncode": vector_refresh_returncode,
            },
        )
        print(f"[OK] refresh_working_cache entries={len(entries)} items={len(incoming)} created={created} updated={updated}")
        return 0
    except Exception as exc:
        emit_runtime_trace(
            action="refresh_working_cache",
            status="failed",
            script=script_name,
            trace_id=trace_id,
            input_text=input_preview,
            artifacts_read=[args.from_log] if args.from_log else [WORKING_CACHE_FILE],
            artifacts_written=[WORKING_CACHE_FILE, WORKING_STATE_FILE],
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=str(exc),
            details={"from_log_mode": from_log_mode, "latest_only": bool(args.latest_only)},
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
