#!/usr/bin/env python3
"""Helpers for local semantic recall over working-memory cache."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from memory_common import (
    WORKING_RECALL_SESSION_STATE_FILE,
    WORKING_VECTOR_INDEX_FILE,
    ensure_working_files,
    get_embedding_runtime_config,
    get_session_retention_config,
    iso_ts_shanghai,
)

EMBEDDING_CONFIG = get_embedding_runtime_config()
DEFAULT_EMBEDDING_BACKEND = EMBEDDING_CONFIG["backend"]
DEFAULT_OLLAMA_BASE_URL = EMBEDDING_CONFIG["base_url"]
DEFAULT_EMBEDDING_MODEL = EMBEDDING_CONFIG["model"]
DEFAULT_EMBEDDING_DIM = EMBEDDING_CONFIG["dim"]

def detect_provider(explicit_provider: str | None = None) -> str:
    value = (explicit_provider or "").strip().lower()
    if value:
        return value
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if os.environ.get("CLAUDE_SESSION_ID"):
        return "claude_code"
    return "unknown"


def detect_session_id(provider: str, explicit_session_id: str | None = None) -> str:
    value = (explicit_session_id or "").strip()
    if value:
        return value
    if provider == "codex":
        return os.environ.get("CODEX_THREAD_ID", "").strip()
    if provider == "claude_code":
        return os.environ.get("CLAUDE_SESSION_ID", "").strip()
    return ""


def make_session_key(provider: str, session_id: str) -> str:
    if provider and session_id:
        return f"{provider}:{session_id}"
    return ""

def init_vector_db(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_working_files()
    path = db_path or WORKING_VECTOR_INDEX_FILE
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_vectors (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            evidence TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            embedding_json TEXT NOT NULL,
            norm REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cache_vectors)").fetchall()}
    if "content_hash" not in columns:
        conn.execute("ALTER TABLE cache_vectors ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_vectors_logged_at ON cache_vectors(logged_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_vectors_type ON cache_vectors(type)")
    conn.commit()
    return conn


def _tokenize(text: str) -> list[str]:
    current: list[str] = []
    tokens: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or ch in {"_", "-"}:
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return [token for token in tokens if len(token) >= 2]


def _hash_embedding(text: str, dim: int | None = None) -> list[float]:
    size = dim or get_embedding_runtime_config()["dim"]
    vector = [0.0] * size
    for token in _tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for offset in range(0, min(len(digest), size), 2):
            idx = digest[offset] % size
            sign = 1.0 if digest[offset + 1] % 2 == 0 else -1.0
            vector[idx] += sign
    if not any(vector):
        vector[0] = 1.0
    return _normalize(vector)


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _ollama_embed(texts: list[str], model: str, base_url: str) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ollama embed failed: status={exc.code} detail={detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"ollama embed request failed: {exc}") from exc

    try:
        obj = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid ollama embed response: {body[:200]}") from exc
    embeddings = obj.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise RuntimeError(f"ollama embed response missing embeddings: {obj}")
    result: list[list[float]] = []
    for item in embeddings:
        if not isinstance(item, list):
            raise RuntimeError(f"invalid embedding item: {item}")
        result.append(_normalize([float(v) for v in item]))
    return result


def embed_texts(texts: list[str], *, backend: str | None = None, model: str | None = None) -> list[list[float]]:
    config = get_embedding_runtime_config()
    chosen_backend = (backend or config["backend"]).strip().lower()
    if chosen_backend == "simple":
        return [_hash_embedding(text, dim=config["dim"]) for text in texts]
    if chosen_backend == "ollama":
        return _ollama_embed(texts, model or config["model"], config["base_url"])
    raise RuntimeError(f"unsupported embedding backend: {chosen_backend}")


def upsert_vectors(rows: list[dict[str, Any]], *, backend: str | None = None, model: str | None = None, db_path: Path | None = None) -> tuple[int, int]:
    conn = init_vector_db(db_path)
    updated = 0
    inserted = 0
    rows_to_embed: list[tuple[dict[str, Any], str, str, bool]] = []

    for row in rows:
        row_text = _row_text(row)
        content_hash = _row_content_hash(row_text)
        row_id = str(row.get("id", ""))
        existing = conn.execute("SELECT content_hash FROM cache_vectors WHERE id = ?", (row_id,)).fetchone()
        if existing and str(existing[0] or "") == content_hash:
            continue
        rows_to_embed.append((row, row_text, content_hash, bool(existing)))

    if not rows_to_embed:
        conn.close()
        return 0, 0

    embeddings = embed_texts([item[1] for item in rows_to_embed], backend=backend, model=model)
    for (row, _row_text_value, content_hash, existed), embedding in zip(rows_to_embed, embeddings):
        payload = {
            "id": str(row.get("id", "")),
            "type": str(row.get("type", "fact")),
            "topic": str(row.get("topic", "")),
            "summary": str(row.get("summary", "")),
            "logged_at": str(row.get("logged_at", "")),
            "evidence": str(row.get("evidence", "")),
            "content_hash": content_hash,
            "embedding_json": json.dumps(embedding, ensure_ascii=False),
            "norm": 1.0,
            "updated_at": iso_ts_shanghai(),
        }
        conn.execute(
            """
            INSERT INTO cache_vectors (id, type, topic, summary, logged_at, evidence, content_hash, embedding_json, norm, updated_at)
            VALUES (:id, :type, :topic, :summary, :logged_at, :evidence, :content_hash, :embedding_json, :norm, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type,
                topic=excluded.topic,
                summary=excluded.summary,
                logged_at=excluded.logged_at,
                evidence=excluded.evidence,
                content_hash=excluded.content_hash,
                embedding_json=excluded.embedding_json,
                norm=excluded.norm,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        if existed:
            updated += 1
        else:
            inserted += 1
    conn.commit()
    conn.close()
    return inserted, updated


def rebuild_vectors(rows: list[dict[str, Any]], *, backend: str | None = None, model: str | None = None, db_path: Path | None = None) -> tuple[int, int]:
    conn = init_vector_db(db_path)
    conn.execute("DELETE FROM cache_vectors")
    conn.commit()
    conn.close()
    return upsert_vectors(rows, backend=backend, model=model, db_path=db_path)


def vector_index_sync_status(rows: list[dict[str, Any]], *, db_path: Path | None = None) -> dict[str, Any]:
    conn = init_vector_db(db_path)
    try:
        db_rows = conn.execute("SELECT id, content_hash FROM cache_vectors").fetchall()
    finally:
        conn.close()

    db_hash_by_id = {str(row[0]): str(row[1] or "") for row in db_rows if str(row[0] or "").strip()}
    cache_ids: set[str] = set()
    missing_ids: list[str] = []
    stale_ids: list[str] = []

    for row in rows:
        row_id = str(row.get("id", "") or "").strip()
        if not row_id:
            continue
        cache_ids.add(row_id)
        expected_hash = _row_content_hash(_row_text(row))
        current_hash = db_hash_by_id.get(row_id)
        if current_hash is None:
            missing_ids.append(row_id)
        elif current_hash != expected_hash:
            stale_ids.append(row_id)

    extra_ids = sorted(row_id for row_id in db_hash_by_id if row_id not in cache_ids)
    return {
        "cache_rows": len(cache_ids),
        "db_rows": len(db_hash_by_id),
        "missing_count": len(missing_ids),
        "stale_count": len(stale_ids),
        "extra_count": len(extra_ids),
        "missing_ids": missing_ids,
        "stale_ids": stale_ids,
        "extra_ids": extra_ids,
        "in_sync": not missing_ids and not stale_ids and not extra_ids,
    }


def ensure_vector_index_current(
    rows: list[dict[str, Any]],
    *,
    backend: str | None = None,
    model: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    status = vector_index_sync_status(rows, db_path=db_path)
    result = dict(status)
    result.update({"refresh_action": "none", "inserted": 0, "updated": 0})
    if status["in_sync"]:
        return result

    # Extra rows mean the vector index has drifted beyond pure append/update and
    # needs a rebuild to stay aligned with cache.jsonl as the source-of-truth.
    if status["extra_count"] > 0:
        inserted, updated = rebuild_vectors(rows, backend=backend, model=model, db_path=db_path)
        refresh_action = "rebuild"
    else:
        inserted, updated = upsert_vectors(rows, backend=backend, model=model, db_path=db_path)
        refresh_action = "upsert"

    refreshed = vector_index_sync_status(rows, db_path=db_path)
    refreshed.update({"refresh_action": refresh_action, "inserted": inserted, "updated": updated})
    return refreshed


def _row_text(row: dict[str, Any]) -> str:
    return "\n".join(
        part for part in [
            str(row.get("topic", "") or "").strip(),
            str(row.get("summary", "") or "").strip(),
            str(row.get("evidence", "") or "").strip(),
        ]
        if part
    )


def _row_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _cosine(query_vector: list[float], row_vector: list[float]) -> float:
    return sum(left * right for left, right in zip(query_vector, row_vector))


def search_vectors(query: str, *, top_k: int = 10, backend: str | None = None, model: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    conn = init_vector_db(db_path)
    rows = conn.execute(
        "SELECT id, type, topic, summary, logged_at, evidence, embedding_json FROM cache_vectors"
    ).fetchall()
    conn.close()
    if not rows:
        return []
    query_vector = embed_texts([query], backend=backend, model=model)[0]
    scored: list[dict[str, Any]] = []
    for row in rows:
        row_vector = json.loads(row[6])
        score = _cosine(query_vector, row_vector)
        scored.append(
            {
                "id": row[0],
                "type": row[1],
                "topic": row[2],
                "summary": row[3],
                "logged_at": row[4],
                "evidence": row[5],
                "_semantic_score": score,
            }
        )
    scored.sort(key=lambda item: (float(item.get("_semantic_score", 0.0)), str(item.get("logged_at", ""))), reverse=True)
    return scored[: max(1, top_k)]


def has_vector_index_rows(db_path: Path | None = None) -> bool:
    conn = init_vector_db(db_path)
    try:
        row = conn.execute("SELECT 1 FROM cache_vectors LIMIT 1").fetchone()
    finally:
        conn.close()
    return bool(row)


def _retention_config() -> dict[str, int]:
    config = get_session_retention_config()
    return {
        "max_sessions": max(1, config["max_sessions"]),
        "max_active_sessions_uncompressed": max(1, config["max_active_sessions_uncompressed"]),
        "max_recalls_per_inactive_session": max(1, config["max_recalls_per_inactive_session"]),
    }


def load_session_state() -> dict[str, Any]:
    ensure_working_files()
    if not WORKING_RECALL_SESSION_STATE_FILE.exists():
        return {"retention": _retention_config(), "sessions": {}}
    try:
        data = json.loads(WORKING_RECALL_SESSION_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"retention": _retention_config(), "sessions": {}}
    if not isinstance(data, dict):
        return {"retention": _retention_config(), "sessions": {}}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        data["sessions"] = {}
    data["retention"] = _retention_config()
    return data


def save_session_state(data: dict[str, Any]) -> None:
    ensure_working_files()
    payload = dict(data) if isinstance(data, dict) else {}
    payload["retention"] = _retention_config()
    payload["sessions"] = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    WORKING_RECALL_SESSION_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seen_ids_for_session(provider: str, session_id: str) -> set[str]:
    key = make_session_key(provider, session_id)
    if not key:
        return set()
    data = load_session_state()
    session = data.get("sessions", {}).get(key, {})
    values = session.get("seen_ids", []) if isinstance(session, dict) else []
    if not isinstance(values, list):
        return set()
    return {str(v) for v in values if str(v)}


def latest_recall_ids_for_session(provider: str, session_id: str, limit: int = 1) -> list[str]:
    key = make_session_key(provider, session_id)
    if not key:
        return []
    data = load_session_state()
    session = data.get("sessions", {}).get(key, {})
    if not isinstance(session, dict):
        return []
    recalls = session.get("recent_recalls", [])
    if not isinstance(recalls, list) or not recalls:
        return []
    last_recall = recalls[-1]
    if not isinstance(last_recall, dict):
        return []
    result_ids = last_recall.get("result_ids", [])
    if not isinstance(result_ids, list):
        return []
    return [str(v) for v in result_ids[: max(1, limit)] if str(v)]


def _compress_session_history(session: dict[str, Any], *, max_recalls: int) -> dict[str, Any]:
    recalls = session.get("recent_recalls", [])
    if not isinstance(recalls, list):
        session["recent_recalls"] = []
        return session
    if len(recalls) <= max_recalls:
        return session
    dropped = [item for item in recalls[:-max_recalls] if isinstance(item, dict)]
    kept = [item for item in recalls[-max_recalls:] if isinstance(item, dict)]
    summary = session.get("older_recalls_summary")
    if not isinstance(summary, dict):
        summary = {}
    previous_count = int(summary.get("count", 0) or 0)
    first_at = summary.get("first_at") or (dropped[0].get("at", "") if dropped else "")
    last_at = dropped[-1].get("at", "") if dropped else summary.get("last_at", "")
    summary.update(
        {
            "count": previous_count + len(dropped),
            "compressed_before": iso_ts_shanghai(),
            "first_at": first_at,
            "last_at": last_at,
        }
    )
    session["recent_recalls"] = kept
    session["older_recalls_summary"] = summary
    return session


def _apply_session_retention(data: dict[str, Any]) -> dict[str, Any]:
    sessions = data.get("sessions", {})
    if not isinstance(sessions, dict):
        data["sessions"] = {}
        return data
    retention = _retention_config()
    max_sessions = retention["max_sessions"]
    active_uncompressed = retention["max_active_sessions_uncompressed"]
    max_inactive_recalls = retention["max_recalls_per_inactive_session"]

    ordered = sorted(
        [(key, value) for key, value in sessions.items() if isinstance(value, dict)],
        key=lambda item: (str(item[1].get("updated_at", "")), str(item[0])),
        reverse=True,
    )
    kept = ordered[:max_sessions]
    normalized: dict[str, Any] = {}
    for index, (key, session) in enumerate(kept):
        recalls = session.get("recent_recalls", [])
        session["recent_recalls"] = [item for item in recalls if isinstance(item, dict)] if isinstance(recalls, list) else []
        if index >= active_uncompressed:
            session = _compress_session_history(session, max_recalls=max_inactive_recalls)
        normalized[key] = session
    data["sessions"] = normalized
    data["retention"] = {
        "max_sessions": max_sessions,
        "max_active_sessions_uncompressed": active_uncompressed,
        "max_recalls_per_inactive_session": max_inactive_recalls,
    }
    return data


def update_session_seen(
    provider: str,
    session_id: str,
    seen_ids: list[str],
    *,
    last_result_ids: list[str] | None = None,
    last_query: str = "",
    last_phase: str = "",
    results: list[dict[str, Any]] | None = None,
    duration_ms: int | None = None,
    recall_trace: dict[str, Any] | None = None,
) -> None:
    key = make_session_key(provider, session_id)
    if not key:
        return
    data = load_session_state()
    sessions = data.setdefault("sessions", {})
    current = sessions.get(key)
    if not isinstance(current, dict):
        current = {}
    for legacy_key in ("last_result_ids", "last_query", "last_phase", "last_recall_at"):
        current.pop(legacy_key, None)
    merged = {str(v) for v in current.get("seen_ids", []) if str(v)}
    merged.update(str(v) for v in seen_ids if str(v))
    current.update(
        {
            "provider": provider,
            "session_id": session_id,
            "session_key": key,
            "updated_at": iso_ts_shanghai(),
            "created_at": current.get("created_at") or iso_ts_shanghai(),
            "seen_ids": sorted(merged),
        }
    )
    recent_recalls = current.get("recent_recalls")
    if not isinstance(recent_recalls, list):
        recent_recalls = []
    if last_result_ids:
        recent_recall = {
            "at": iso_ts_shanghai(),
            "phase": (last_phase or "").strip(),
            "query": (last_query or "").strip(),
            "duration_ms": int(duration_ms or 0),
            "result_ids": [str(v) for v in last_result_ids if str(v)],
            "results": [
                {
                    "id": str(row.get("id", "")),
                    "topic": str(row.get("topic", "")),
                    "type": str(row.get("type", "")),
                    "summary": str(row.get("summary", "")),
                    "semantic_score": float(row.get("_semantic_score", 0.0)) if row.get("_semantic_score") is not None else None,
                    "lexical_score": float(row.get("_lexical_score", 0.0)) if row.get("_lexical_score") is not None else None,
                    "final_score": float(row.get("_score", 0.0)) if row.get("_score") is not None else None,
                    "reason": list(row.get("_reason", [])) if isinstance(row.get("_reason"), list) else [],
                }
                for row in (results or [])
                if isinstance(row, dict) and str(row.get("id", ""))
            ],
        }
        if isinstance(recall_trace, dict) and recall_trace:
            recent_recall["trace"] = recall_trace
        recent_recalls.append(recent_recall)
    current["recent_recalls"] = recent_recalls
    sessions[key] = current
    save_session_state(_apply_session_retention(data))
