#!/usr/bin/env python3
"""Build or refresh the local semantic index from working-memory cache."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from memory_common import WORKING_CACHE_FILE, WORKING_VECTOR_INDEX_FILE
from runtime_trace import current_trace_id, emit_runtime_trace
from vector_recall_common import (
    DEFAULT_EMBEDDING_BACKEND,
    DEFAULT_EMBEDDING_MODEL,
    ensure_vector_index_current,
    rebuild_vectors,
)
from working_common import load_cache


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh local vector index from cache.jsonl")
    p.add_argument("--rebuild", action="store_true", help="Rebuild the whole index")
    p.add_argument("--backend", default=DEFAULT_EMBEDDING_BACKEND)
    p.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    script_name = Path(__file__).name
    trace_id = current_trace_id()
    started_at = time.perf_counter()
    emit_runtime_trace(
        action="refresh_vector_index",
        status="started",
        script=script_name,
        trace_id=trace_id,
        input_text=f"rebuild={args.rebuild} backend={args.backend} model={args.model}",
        artifacts_read=[WORKING_CACHE_FILE],
        artifacts_written=[WORKING_VECTOR_INDEX_FILE],
        details={"rebuild": bool(args.rebuild)},
    )
    try:
        rows = load_cache()
        if not rows:
            emit_runtime_trace(
                action="refresh_vector_index",
                status="ok",
                script=script_name,
                trace_id=trace_id,
                input_text=f"rebuild={args.rebuild} backend={args.backend} model={args.model}",
                artifacts_read=[WORKING_CACHE_FILE],
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                details={"rows": 0, "inserted": 0, "updated": 0, "rebuild": bool(args.rebuild)},
            )
            print("[OK] no cache rows found; vector index unchanged")
            return 0
        refresh_action = "rebuild"
        extra_count = 0
        stale_count = 0
        missing_count = 0
        in_sync = True
        if args.rebuild:
            inserted, updated = rebuild_vectors(rows, backend=args.backend, model=args.model)
        else:
            sync = ensure_vector_index_current(rows, backend=args.backend, model=args.model)
            inserted = int(sync.get("inserted", 0))
            updated = int(sync.get("updated", 0))
            refresh_action = str(sync.get("refresh_action", "none"))
            extra_count = int(sync.get("extra_count", 0))
            stale_count = int(sync.get("stale_count", 0))
            missing_count = int(sync.get("missing_count", 0))
            in_sync = bool(sync.get("in_sync", False))
        emit_runtime_trace(
            action="refresh_vector_index",
            status="ok",
            script=script_name,
            trace_id=trace_id,
            input_text=f"rebuild={args.rebuild} backend={args.backend} model={args.model}",
            artifacts_read=[WORKING_CACHE_FILE],
            artifacts_written=[WORKING_VECTOR_INDEX_FILE],
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            details={
                "rows": len(rows),
                "inserted": inserted,
                "updated": updated,
                "rebuild": bool(args.rebuild),
                "refresh_action": refresh_action,
                "missing_count": missing_count,
                "stale_count": stale_count,
                "extra_count": extra_count,
                "in_sync": in_sync,
            },
        )
        print(
            f"[OK] vector_index rows={len(rows)} inserted={inserted} updated={updated} "
            f"backend={args.backend} model={args.model} action={refresh_action}"
        )
        return 0
    except Exception as exc:
        emit_runtime_trace(
            action="refresh_vector_index",
            status="failed",
            script=script_name,
            trace_id=trace_id,
            input_text=f"rebuild={args.rebuild} backend={args.backend} model={args.model}",
            artifacts_read=[WORKING_CACHE_FILE],
            artifacts_written=[WORKING_VECTOR_INDEX_FILE],
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=str(exc),
            details={"rebuild": bool(args.rebuild)},
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
