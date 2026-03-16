#!/usr/bin/env python3
"""Append feedback via the canonical append_entry flow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from memory_common import iso_ts_shanghai, normalize_inline_text
from working_common import load_archive, load_cache, p0_match_score, save_cache, update_state
from vector_recall_common import detect_provider, detect_session_id, latest_recall_ids_for_session

ALLOWED_EFFECTS = ("none", "applied", "corrected")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append feedback through append_entry.py")
    p.add_argument("--topic", required=True, help="Short feedback topic slug")
    p.add_argument("--summary", required=True, help="Feedback or guardrail text; write structured guardrails directly in summary when needed")
    p.add_argument("--evidence", required=True)
    p.add_argument("--effect", default="none", choices=ALLOWED_EFFECTS)
    p.add_argument("--target-id", action="append", default=[])
    p.add_argument("--target-last-recall", action="store_true")
    p.add_argument("--target-last-k", type=int, default=1)
    p.add_argument("--provider", default="")
    p.add_argument("--session-id", default="")
    p.add_argument("--target-query", default="")
    p.add_argument("--target-top-k", type=int, default=3)
    p.add_argument("--soft-max", type=int, default=500)
    p.add_argument("--hard-max", type=int, default=1500)
    p.add_argument("--skip-working-refresh", action="store_true")
    p.add_argument("--skip-working-sweep", action="store_true")
    p.add_argument("--sweep-every-appends", type=int, default=20)
    p.add_argument("--sweep-decay", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def normalize_topic_slug(value: str) -> str:
    normalized = normalize_inline_text(value)
    slug = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", normalized).strip("_")
    return slug or "feedback"


def load_last_recall_ids(limit: int, *, provider_hint: str = "", session_hint: str = "") -> list[str]:
    provider = detect_provider(provider_hint)
    session_id = detect_session_id(provider, session_hint)
    if not provider or not session_id:
        return []
    return latest_recall_ids_for_session(provider, session_id, limit=limit)


def annotate_targets(effect: str, target_ids: list[str], query: str, top_k: int, feedback_topic: str) -> int:
    if effect == "none":
        return 0

    cache = load_cache()
    archive = load_archive()
    target_id_set = {normalize_inline_text(value) for value in target_ids if normalize_inline_text(value)}
    picked: list[dict[str, object]] = []

    if target_id_set:
        for row in cache:
            if str(row.get("type", "fact")) != "fact":
                continue
            if str(row.get("topic", "")) == feedback_topic:
                continue
            if str(row.get("id", "")) in target_id_set:
                picked.append(row)

    if not picked and query:
        scored = []
        for row in cache:
            if str(row.get("type", "fact")) != "fact":
                continue
            if str(row.get("topic", "")) == feedback_topic:
                continue
            score = p0_match_score(query, row)
            if score <= 0:
                continue
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        picked = [row for _, row in scored[: max(1, top_k)]]

    if not picked:
        return 0

    now_ts = iso_ts_shanghai()
    for row in picked:
        if effect == "applied":
            row["apply_count"] = int(row.get("apply_count", 0) or 0) + 1
        elif effect == "corrected":
            row["correction_count"] = int(row.get("correction_count", 0) or 0) + 1
        row["updated_at"] = now_ts

    save_cache(cache)
    update_state(cache, archive, note="append_feedback_effect")
    return len(picked)


def main() -> int:
    args = parse_args()

    summary = normalize_inline_text(args.summary)
    evidence = normalize_inline_text(args.evidence)
    effect = normalize_inline_text(args.effect).lower()
    topic_slug = normalize_topic_slug(args.topic)
    target_ids = [normalize_inline_text(value) for value in args.target_id if normalize_inline_text(value)]
    if args.target_last_recall:
        target_ids.extend(load_last_recall_ids(args.target_last_k, provider_hint=args.provider, session_hint=args.session_id))
    target_query = normalize_inline_text(args.target_query)

    if not summary:
        print("[ERROR] --summary is required", file=sys.stderr)
        return 2
    if effect != "none" and not target_ids and not target_query:
        print("[ERROR] --target-id or --target-query is required when --effect is not none", file=sys.stderr)
        return 2

    feedback_topic = f"feedback_{topic_slug}"
    append_entry = Path(__file__).with_name("append_entry.py")
    command = [
        sys.executable,
        str(append_entry),
        "--topic",
        feedback_topic,
        "--summary",
        summary,
        "--evidence",
        evidence,
        "--type",
        "feedback",
        "--soft-max",
        str(args.soft_max),
        "--hard-max",
        str(args.hard_max),
        "--sweep-every-appends",
        str(args.sweep_every_appends),
        "--sweep-decay",
        str(args.sweep_decay),
    ]
    if args.skip_working_refresh:
        command.append("--skip-working-refresh")
    if args.skip_working_sweep:
        command.append("--skip-working-sweep")
    if args.dry_run:
        command.append("--dry-run")

    proc = subprocess.run(command, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0 or args.dry_run:
        return proc.returncode

    annotated = annotate_targets(effect, target_ids, target_query, args.target_top_k, feedback_topic)
    if effect != "none":
        print(f"[OK] annotated_targets={annotated} effect={effect}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
