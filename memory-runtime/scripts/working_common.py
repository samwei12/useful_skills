#!/usr/bin/env python3
"""Shared helpers for minimal working-memory scripts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from memory_common import WORKING_ARCHIVE_FILE, WORKING_CACHE_FILE, WORKING_STATE_FILE, SHANGHAI_TZ, ensure_working_files, iso_ts_shanghai

QUERY_STOPWORDS = {
    "这个", "那个", "现在", "当前", "相关", "一下", "问题", "内容", "东西", "怎么", "如何", "需要", "应该",
    "the", "and", "for", "with", "from", "that", "this",
}


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=SHANGHAI_TZ)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(tz=SHANGHAI_TZ)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": str(row.get("id", "") or "").strip(),
        "topic": str(row.get("topic", "") or "").strip(),
        "type": str(row.get("type", "") or "fact").strip().lower() or "fact",
        "summary": str(row.get("summary", "") or "").strip(),
        "logged_at": str(row.get("logged_at", "") or row.get("source_time", "") or "").strip(),
        "evidence": str(row.get("evidence", "") or "").strip(),
    }
    if item["type"] not in {"fact", "feedback"}:
        item["type"] = "fact"
    return item


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = ""
    if rows:
        content = "\n".join(json.dumps(normalize_item(r), ensure_ascii=False, sort_keys=True) for r in rows) + "\n"
    path.write_text(content, encoding="utf-8")


def load_cache() -> list[dict[str, Any]]:
    ensure_working_files()
    return [normalize_item(row) for row in read_jsonl(WORKING_CACHE_FILE)]


def save_cache(rows: list[dict[str, Any]]) -> None:
    ensure_working_files()
    write_jsonl(WORKING_CACHE_FILE, rows)


def load_archive() -> list[dict[str, Any]]:
    ensure_working_files()
    return [normalize_item(row) for row in read_jsonl(WORKING_ARCHIVE_FILE)]


def save_archive(rows: list[dict[str, Any]]) -> None:
    ensure_working_files()
    write_jsonl(WORKING_ARCHIVE_FILE, rows)


def update_state(cache_rows: list[dict[str, Any]], archive_rows: list[dict[str, Any]], note: str | None = None) -> None:
    ensure_working_files()
    payload: dict[str, Any] = {
        "updated_at": iso_ts_shanghai(),
        "schema": "p0-minimal",
        "stats": {
            "cache_records": len(cache_rows),
            "archive_records": len(archive_rows),
            "fact_records": sum(1 for row in cache_rows if str(row.get("type", "fact")) == "fact"),
            "feedback_records": sum(1 for row in cache_rows if str(row.get("type", "fact")) == "feedback"),
        },
        "maintenance": {
            "append_since_sweep": 0,
            "sweep_every_appends": 20,
            "append_since_review": 0,
            "review_every_appends": 10,
            "last_counter_update_at": iso_ts_shanghai(),
            "last_sweep_trigger": "",
            "last_sweep_scheduled_at": "",
            "last_review_trigger": "",
            "last_review_scheduled_at": "",
            "last_review_completed_at": "",
        },
    }
    if WORKING_STATE_FILE.exists():
        try:
            base = json.loads(WORKING_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(base, dict) and isinstance(base.get("maintenance"), dict):
                payload["maintenance"] = {**payload["maintenance"], **base["maintenance"]}
        except json.JSONDecodeError:
            pass
    if note:
        payload["note"] = note
    WORKING_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def contains_evidence_query(text: str) -> bool:
    q = text.lower()
    keywords = ("证据", "日志", "命令", "报错", "error", "trace", "stack", "验证", "如何确认")
    return any(k in q for k in keywords)


def tokenize(text: str) -> list[str]:
    parts = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", text.lower())
    return [p for p in parts if len(p) >= 2]


def extract_query_keywords(query: str, limit: int = 8) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokenize(query):
        if token in QUERY_STOPWORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def infer_intent(query: str) -> str:
    q = query.lower()
    if any(token in q for token in ("纠偏", "纠正", "注意", "避免", "不要", "别再", "规则", "约束", "规范")):
        return "guardrail"
    if any(token in q for token in ("报错", "错误", "故障", "失败", "修复", "排查", "debug", "trace", "日志")):
        return "debugging"
    if any(token in q for token in ("文档", "笔记", "整理", "总结", "draft", "草稿")):
        return "documentation"
    if any(token in q for token in ("设计", "方案", "规划", "架构", "迭代", "计划")):
        return "planning"
    return "general"


def derive_session_profile(query: str, *, include_evidence: bool = False, **_: Any) -> dict[str, Any]:
    keywords = extract_query_keywords(query)
    return {
        "query": query,
        "intent": infer_intent(query),
        "keywords": keywords,
        "topic_hint": " / ".join(keywords[:3]) if keywords else "",
        "evidence_need": include_evidence or contains_evidence_query(query),
        "recall_mode": "p0",
    }


def split_query_terms(query: str) -> list[str]:
    text = str(query or "").strip().lower()
    if not text:
        return []
    parts: list[str] = [text]
    for token in extract_query_keywords(text, limit=8):
        if token not in parts:
            parts.append(token)
        if len(token) >= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in token):
            for size in (4, 3, 2):
                if len(token) > size:
                    for start in range(0, len(token) - size + 1):
                        piece = token[start : start + size]
                        if piece not in parts:
                            parts.append(piece)
    return parts


def p0_match_score(query: str, item: dict[str, Any]) -> float:
    haystack = " ".join([str(item.get("topic", "") or ""), str(item.get("summary", "") or "")]).lower()
    raw_query = str(query or "").strip().lower()
    if not haystack:
        return 0.0
    if raw_query and raw_query in haystack:
        topic = str(item.get("topic", "") or "").lower()
        return 2.0 if raw_query in topic else 1.5
    terms = split_query_terms(raw_query)
    if not terms:
        return 0.0
    matched = 0
    topic_hits = 0
    topic = str(item.get("topic", "") or "").lower()
    for term in terms:
        if term and term in haystack:
            matched += 1
            if term in topic:
                topic_hits += 1
    if not matched:
        return 0.0
    return matched + topic_hits * 0.5
