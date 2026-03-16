#!/usr/bin/env python3
"""Persist and render promotion review queue."""

from __future__ import annotations

import json
from typing import Any

from memory_common import (
    WORKING_PROMOTION_QUEUE_FILE,
    ensure_working_files,
    iso_ts_shanghai,
)

VALID_STATUSES = ("pending", "accept", "defer")
STATUS_LABELS = {
    "pending": "待判断",
    "accept": "可进入 MEMORY",
    "defer": "后续再看",
}


def load_queue() -> list[dict[str, Any]]:
    ensure_working_files()
    rows: list[dict[str, Any]] = []
    for line in WORKING_PROMOTION_QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(normalize_queue_item(obj))
    return rows


def normalize_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    now = iso_ts_shanghai()
    status = str(item.get("status", "pending") or "pending").strip().lower()
    if status not in VALID_STATUSES:
        status = "pending"
    return {
        "id": str(item.get("id", "") or "").strip(),
        "topic": str(item.get("topic", "") or "").strip(),
        "type": str(item.get("type", "fact") or "fact").strip().lower() or "fact",
        "summary": str(item.get("summary", "") or "").strip(),
        "status": status,
        "active": bool(item.get("active", True)),
        "first_seen_at": str(item.get("first_seen_at", now) or now).strip(),
        "last_seen_at": str(item.get("last_seen_at", now) or now).strip(),
        "seen_count": max(1, int(item.get("seen_count", 1) or 1)),
        "status_updated_at": str(item.get("status_updated_at", "") or "").strip(),
        "status_note": str(item.get("status_note", "") or "").strip(),
    }


def save_queue(items: list[dict[str, Any]]) -> None:
    ensure_working_files()
    lines = [json.dumps(normalize_queue_item(item), ensure_ascii=False, sort_keys=True) for item in items if isinstance(item, dict)]
    WORKING_PROMOTION_QUEUE_FILE.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def sync_candidates(candidates: list[dict[str, Any]], trigger: str = "manual") -> list[dict[str, Any]]:
    payload = load_queue()
    now = iso_ts_shanghai()
    indexed: dict[str, dict[str, Any]] = {}
    for raw in payload:
        item = normalize_queue_item(raw)
        if item["id"]:
            item["active"] = False
            indexed[item["id"]] = item

    for row in candidates:
        item_id = str(row.get("id", "") or "").strip()
        if not item_id:
            continue
        existing = indexed.get(item_id)
        if existing is None:
            indexed[item_id] = normalize_queue_item(
                {
                    "id": item_id,
                    "topic": row.get("topic", ""),
                    "type": row.get("type", "fact"),
                    "summary": row.get("summary", ""),
                    "status": "pending",
                    "active": True,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "seen_count": 1,
                    "status_note": f"created_by={trigger}",
                }
            )
            continue
        existing["topic"] = str(row.get("topic", "") or "").strip()
        existing["type"] = str(row.get("type", "fact") or "fact").strip().lower() or "fact"
        existing["summary"] = str(row.get("summary", "") or "").strip()
        existing["active"] = True
        existing["last_seen_at"] = now
        existing["seen_count"] = max(1, int(existing.get("seen_count", 1) or 1)) + 1

    items = sorted(
        [normalize_queue_item(item) for item in indexed.values()],
        key=lambda item: (
            0 if item.get("active") else 1,
            VALID_STATUSES.index(item.get("status", "pending")),
            -int(item.get("seen_count", 1) or 1),
            item.get("last_seen_at", ""),
        ),
    )
    save_queue(items)
    return items


def update_queue_status(item_id: str, status: str, note: str = "") -> list[dict[str, Any]]:
    payload = load_queue()
    normalized = status.strip().lower()
    if normalized not in (*VALID_STATUSES, "reject"):
        raise ValueError(f"unsupported status: {status}")
    found = False
    now = iso_ts_shanghai()
    items: list[dict[str, Any]] = []
    for raw in payload:
        item = normalize_queue_item(raw)
        if item.get("id") == item_id:
            found = True
            if normalized == "reject":
                continue
            item["status"] = normalized
            item["status_updated_at"] = now
            item["status_note"] = note.strip()
        items.append(item)
    if not found:
        raise ValueError(f"queue item not found: {item_id}")
    save_queue(items)
    return items


def queue_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"accept": 0, "defer": 0, "pending": 0, "active": 0}
    for item in items:
        status = str(item.get("status", "pending"))
        if status in counts:
            counts[status] += 1
        if item.get("active"):
            counts["active"] += 1
    return counts


def render_queue_markdown(items: list[dict[str, Any]]) -> str:
    items = [normalize_queue_item(item) for item in items if isinstance(item, dict)]
    counts = queue_counts(items)
    lines = [
        "# Promotion Review Queue",
        "",
        f"- updated_at: {iso_ts_shanghai()}",
        f"- active_candidate_count: {counts.get('active', 0)}",
        "- file: `~/.memories/working/promotion_review_queue.jsonl`",
        "- statuses: `accept=可进入 MEMORY` `defer=后续再看` `pending=待判断` `reject=直接从队列删除`",
        "",
    ]
    sections = [
        ("accept", "可进入 MEMORY"),
        ("defer", "后续再看"),
        ("pending", "待判断"),
    ]
    for status, title in sections:
        rows = [item for item in items if item.get("status") == status]
        lines.append(f"## {title}")
        if not rows:
            lines.extend(["- (none)", ""])
            continue
        for item in rows:
            active_label = "active" if item.get("active") else "inactive"
            lines.append(
                f"- [{active_label}] id={item.get('id','')} type={item.get('type','fact')} topic={item.get('topic','')}"
            )
            lines.append(f"  - summary: {item.get('summary','')}")
            lines.append(f"  - seen_count: {item.get('seen_count', 1)}")
            lines.append(f"  - first_seen_at: {item.get('first_seen_at', '')}")
            lines.append(f"  - last_seen_at: {item.get('last_seen_at', '')}")
            if item.get("status_updated_at"):
                lines.append(f"  - status_updated_at: {item.get('status_updated_at', '')}")
            if item.get("status_note"):
                lines.append(f"  - note: {item.get('status_note', '')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
