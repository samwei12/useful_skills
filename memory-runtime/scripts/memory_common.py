#!/usr/bin/env python3
"""Common helpers for local memory runtime scripts."""

from __future__ import annotations

import copy
import json
import os
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
# Optional override for isolated testing:
#   MEMORY_ROOT=/tmp/memory-test uv run python scripts/...
MEMORY_ROOT = Path(os.environ.get("MEMORY_ROOT", str(Path.home() / ".memories")))
MEMORY_FILE = MEMORY_ROOT / "MEMORY.md"
MEMORY_RUNTIME_CONFIG_FILE = MEMORY_ROOT / "runtime_config.toml"
MEMORIES_DIR = MEMORY_ROOT / "memories"
WORKING_DIR = MEMORY_ROOT / "working"
WORKING_CACHE_FILE = WORKING_DIR / "cache.jsonl"
WORKING_ARCHIVE_FILE = WORKING_DIR / "archive.jsonl"
WORKING_STATE_FILE = WORKING_DIR / "state.json"
WORKING_PROMOTION_QUEUE_FILE = WORKING_DIR / "promotion_review_queue.jsonl"
WORKING_VECTOR_INDEX_FILE = WORKING_DIR / "cache_vectors.sqlite3"
WORKING_RECALL_SESSION_STATE_FILE = WORKING_DIR / "recall_session_state.json"
WORKING_TRACE_DIR = WORKING_DIR / "traces"
WORKING_SCHEMA_VERSION = "v3.2-working"
DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "version": 1,
    "recall": {
        "enabled": True,
        "semantic_enabled": True,
        "first_recall_only_enabled": True,
        "session_dedupe_enabled": True,
        "default_top_k": 4,
        "default_candidate_k": 0,
        "max_inject_feedback": 2,
        "continuation": {
            "short_query_max_chars": 16,
            "candidate_k": 6,
            "inject_k": 2,
            "max_feedback": 1,
        },
    },
    "session_retention": {
        "max_sessions": 10,
        "max_active_sessions_uncompressed": 4,
        "max_recalls_per_inactive_session": 30,
    },
    "embedding": {
        "backend": "ollama",
        "model": "qwen3-embedding:0.6b",
        "base_url": "http://127.0.0.1:11434",
        "dim": 128,
    },
}


@dataclass(frozen=True)
class MemoryPaths:
    root: Path = MEMORY_ROOT
    memory_file: Path = MEMORY_FILE
    runtime_config_file: Path = MEMORY_RUNTIME_CONFIG_FILE
    memories_dir: Path = MEMORIES_DIR
    working_dir: Path = WORKING_DIR
    working_cache_file: Path = WORKING_CACHE_FILE
    working_archive_file: Path = WORKING_ARCHIVE_FILE
    working_state_file: Path = WORKING_STATE_FILE
    working_promotion_queue_file: Path = WORKING_PROMOTION_QUEUE_FILE
    working_vector_index_file: Path = WORKING_VECTOR_INDEX_FILE
    working_recall_session_state_file: Path = WORKING_RECALL_SESSION_STATE_FILE
    working_trace_dir: Path = WORKING_TRACE_DIR


def now_shanghai() -> datetime:
    return datetime.now(tz=SHANGHAI_TZ)


def iso_ts_shanghai(ts: datetime | None = None) -> str:
    base = ts or now_shanghai()
    return base.strftime("%Y-%m-%dT%H:%M:%S%z")


def date_str_shanghai(ts: datetime | None = None) -> str:
    base = ts or now_shanghai()
    return base.strftime("%Y-%m-%d")


def day_log_path(day_str: str) -> Path:
    return MEMORIES_DIR / f"{day_str}.md"


def daily_template(day_str: str) -> str:
    return (
        "# Daily Memory Log\n\n"
        "## metadata\n"
        f"- date: {day_str}\n"
        "- timezone: Asia/Shanghai\n\n"
        "## entries\n"
        "<!-- append-only -->\n"
    )


def default_runtime_config_toml() -> str:
    return (
        "# 本地记忆运行时配置\n"
        "# 这是用户直接维护的入口，用来控制 recall / retention / embedding 的默认行为。\n"
        "# 如需临时调试，少量环境变量仍然可以覆盖这里的部分字段。\n\n"
        "version = 1\n\n"
        "[recall]\n"
        "# 第一层：总召回开关。设为 false 后，本次 recall 会直接跳过，不再执行任何召回检索。\n"
        "enabled = true\n"
        "# 当总召回开启时，是否允许走 semantic recall。\n"
        "semantic_enabled = true\n"
        "# 第二层：同一 session 是否只允许第一次 recall。\n"
        "# 设为 true 后，只要当前 session 已经做过一次 recall，后续同 session 的 recall 会直接跳过。\n"
        "first_recall_only_enabled = true\n"
        "# 第三层：会话内去重开关。\n"
        "# 只有在允许同一 session 多次 recall 时，这个开关才有意义；它会阻止重复注入已经见过的相同 id。\n"
        "session_dedupe_enabled = true\n"
        "# 默认最终注入条数。\n"
        "default_top_k = 4\n"
        "# 默认候选集条数。设为 0 表示由脚本按 top_k 自动推导。\n"
        "default_candidate_k = 0\n"
        "# 最终结果里最多允许多少条 feedback 类型记忆。\n"
        "max_inject_feedback = 2\n\n"
        "[recall.continuation]\n"
        "# 针对“继续 / 展开 / 接着说”这类续聊场景的轻量 recall 参数。\n"
        "# query 长度不超过这个阈值，并且命中续聊判定时，会进入 continuation 轻量模式。\n"
        "short_query_max_chars = 16\n"
        "# continuation 模式下最多取多少候选。\n"
        "candidate_k = 6\n"
        "# continuation 模式下最终最多注入多少条。\n"
        "inject_k = 2\n"
        "# continuation 模式下最多保留多少条 feedback。\n"
        "max_feedback = 1\n\n"
        "[session_retention]\n"
        "# recall_session_state.json 最多保留多少个 session 的去重状态。\n"
        "max_sessions = 10\n"
        "# 最近多少个活跃 session 保留完整 recent_recalls，不做压缩。\n"
        "max_active_sessions_uncompressed = 4\n"
        "# 较旧 session 在压缩后最多保留多少条 recent_recalls 明细。\n"
        "max_recalls_per_inactive_session = 30\n\n"
        "[embedding]\n"
        "# semantic recall 使用的 embedding backend。\n"
        "backend = \"ollama\"\n"
        "# embedding 模型名。\n"
        "model = \"qwen3-embedding:0.6b\"\n"
        "# backend 服务地址；当前 backend=ollama 时用于请求 embed API。\n"
        "base_url = \"http://127.0.0.1:11434\"\n"
        "# simple backend 的向量维度；也可作为部分本地 backend 的默认维度参数。\n"
        "dim = 128\n"
    )


def ensure_runtime_config_file() -> Path:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    if not MEMORY_RUNTIME_CONFIG_FILE.exists():
        MEMORY_RUNTIME_CONFIG_FILE.write_text(default_runtime_config_toml(), encoding="utf-8")
    return MEMORY_RUNTIME_CONFIG_FILE


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_runtime_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    embedding = config.setdefault("embedding", {})
    session_retention = config.setdefault("session_retention", {})

    if os.environ.get("MEMORY_EMBEDDING_BACKEND"):
        embedding["backend"] = os.environ["MEMORY_EMBEDDING_BACKEND"]
    if os.environ.get("MEMORY_EMBEDDING_MODEL"):
        embedding["model"] = os.environ["MEMORY_EMBEDDING_MODEL"]
    if os.environ.get("OLLAMA_BASE_URL"):
        embedding["base_url"] = os.environ["OLLAMA_BASE_URL"]
    if os.environ.get("MEMORY_EMBEDDING_DIM"):
        embedding["dim"] = os.environ["MEMORY_EMBEDDING_DIM"]
    if os.environ.get("MEMORY_RECALL_MAX_SESSIONS"):
        session_retention["max_sessions"] = os.environ["MEMORY_RECALL_MAX_SESSIONS"]
    if os.environ.get("MEMORY_RECALL_MAX_ACTIVE_SESSIONS"):
        session_retention["max_active_sessions_uncompressed"] = os.environ["MEMORY_RECALL_MAX_ACTIVE_SESSIONS"]
    if os.environ.get("MEMORY_RECALL_MAX_RECALLS_PER_INACTIVE_SESSION"):
        session_retention["max_recalls_per_inactive_session"] = os.environ["MEMORY_RECALL_MAX_RECALLS_PER_INACTIVE_SESSION"]
    return config


def load_runtime_config() -> dict[str, Any]:
    config_path = ensure_runtime_config_file()
    text = config_path.read_text(encoding="utf-8").strip()
    if not text:
        return _apply_runtime_env_overrides(copy.deepcopy(DEFAULT_RUNTIME_CONFIG))
    try:
        parsed = tomllib.loads(text)
    except Exception as exc:
        raise RuntimeError(f"invalid runtime config at {config_path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"invalid runtime config at {config_path}: top-level table must be a mapping")
    merged = _deep_merge_dict(DEFAULT_RUNTIME_CONFIG, parsed)
    return _apply_runtime_env_overrides(merged)


def _coerce_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _coerce_int(value: Any, default: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _coerce_str(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip()
    return normalized or default


def get_recall_runtime_config() -> dict[str, Any]:
    loaded = load_runtime_config()
    defaults = DEFAULT_RUNTIME_CONFIG["recall"]
    section = loaded.get("recall", {}) if isinstance(loaded.get("recall"), dict) else {}
    continuation_defaults = defaults["continuation"]
    continuation = section.get("continuation", {}) if isinstance(section.get("continuation"), dict) else {}
    return {
        "enabled": _coerce_bool(section.get("enabled"), defaults["enabled"]),
        "semantic_enabled": _coerce_bool(section.get("semantic_enabled"), defaults["semantic_enabled"]),
        "first_recall_only_enabled": _coerce_bool(section.get("first_recall_only_enabled"), defaults["first_recall_only_enabled"]),
        "session_dedupe_enabled": _coerce_bool(section.get("session_dedupe_enabled"), defaults["session_dedupe_enabled"]),
        "default_top_k": _coerce_int(section.get("default_top_k"), defaults["default_top_k"], minimum=1),
        "default_candidate_k": _coerce_int(section.get("default_candidate_k"), defaults["default_candidate_k"], minimum=0),
        "max_inject_feedback": _coerce_int(section.get("max_inject_feedback"), defaults["max_inject_feedback"], minimum=1),
        "continuation": {
            "short_query_max_chars": _coerce_int(
                continuation.get("short_query_max_chars"),
                continuation_defaults["short_query_max_chars"],
                minimum=1,
            ),
            "candidate_k": _coerce_int(continuation.get("candidate_k"), continuation_defaults["candidate_k"], minimum=1),
            "inject_k": _coerce_int(continuation.get("inject_k"), continuation_defaults["inject_k"], minimum=1),
            "max_feedback": _coerce_int(continuation.get("max_feedback"), continuation_defaults["max_feedback"], minimum=1),
        },
    }


def get_session_retention_config() -> dict[str, int]:
    loaded = load_runtime_config()
    defaults = DEFAULT_RUNTIME_CONFIG["session_retention"]
    section = loaded.get("session_retention", {}) if isinstance(loaded.get("session_retention"), dict) else {}
    return {
        "max_sessions": _coerce_int(section.get("max_sessions"), defaults["max_sessions"], minimum=1),
        "max_active_sessions_uncompressed": _coerce_int(
            section.get("max_active_sessions_uncompressed"),
            defaults["max_active_sessions_uncompressed"],
            minimum=1,
        ),
        "max_recalls_per_inactive_session": _coerce_int(
            section.get("max_recalls_per_inactive_session"),
            defaults["max_recalls_per_inactive_session"],
            minimum=1,
        ),
    }


def get_embedding_runtime_config() -> dict[str, Any]:
    loaded = load_runtime_config()
    defaults = DEFAULT_RUNTIME_CONFIG["embedding"]
    section = loaded.get("embedding", {}) if isinstance(loaded.get("embedding"), dict) else {}
    return {
        "backend": _coerce_str(section.get("backend"), defaults["backend"]).lower(),
        "model": _coerce_str(section.get("model"), defaults["model"]),
        "base_url": _coerce_str(section.get("base_url"), defaults["base_url"]),
        "dim": _coerce_int(section.get("dim"), defaults["dim"], minimum=1),
    }


def sync_recall_session_state_file() -> None:
    retention = get_session_retention_config()
    payload: dict[str, Any] = {"retention": retention, "sessions": {}}
    if WORKING_RECALL_SESSION_STATE_FILE.exists():
        try:
            current = json.loads(WORKING_RECALL_SESSION_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if isinstance(current, dict) and isinstance(current.get("sessions"), dict):
            payload["sessions"] = current["sessions"]
        if current == payload:
            return
    WORKING_RECALL_SESSION_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_memory_file() -> None:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_runtime_config_file()
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text("# Local Memory Core Rules\n\n", encoding="utf-8")


def ensure_day_file(day_str: str) -> Path:
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    p = day_log_path(day_str)
    if not p.exists() or p.stat().st_size == 0:
        p.write_text(daily_template(day_str), encoding="utf-8")
    return p


def ensure_working_files() -> None:
    ensure_runtime_config_file()
    WORKING_DIR.mkdir(parents=True, exist_ok=True)
    WORKING_TRACE_DIR.mkdir(parents=True, exist_ok=True)

    for p in (WORKING_CACHE_FILE, WORKING_ARCHIVE_FILE):
        if not p.exists():
            p.write_text("", encoding="utf-8")

    if not WORKING_PROMOTION_QUEUE_FILE.exists():
        WORKING_PROMOTION_QUEUE_FILE.write_text("", encoding="utf-8")

    sync_recall_session_state_file()

    if not WORKING_STATE_FILE.exists() or WORKING_STATE_FILE.stat().st_size == 0:
        payload = {
            "version": WORKING_SCHEMA_VERSION,
            "description": "Derived working-memory cache; source of truth stays in daily logs",
            "updated_at": iso_ts_shanghai(),
            "schema_version": WORKING_SCHEMA_VERSION,
            "stats": {
                "cache_records": 0,
                "hot_records": 0,
                "warm_records": 0,
                "cold_records": 0,
                "archive_records": 0,
            },
            "thresholds": {
                "hot_min_heat": 60,
                "warm_min_heat": 30,
                "cold_max_heat": 20,
                "cold_min_idle_days": 30,
                "archive_max_heat": 10,
                "archive_min_idle_days": 90,
            },
            "heat_policy": {
                "initial_heat": 60,
                "hit_increment": 15,
                "daily_decay": 2,
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
        WORKING_STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def last_n_days(n: int) -> list[str]:
    today = now_shanghai().date()
    values = []
    for delta in range(n):
        d = today - timedelta(days=delta)
        values.append(d.strftime("%Y-%m-%d"))
    return sorted(values)


def normalize_inline_text(value: str) -> str:
    """Normalize to one-line text to keep entry format stable."""
    return " ".join(value.strip().split())


def make_memory_id(ts: datetime | None = None) -> str:
    base = ts or now_shanghai()
    return f"mem_{base.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def make_trace_id(ts: datetime | None = None) -> str:
    base = ts or now_shanghai()
    return f"trace_{base.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def make_event_id(ts: datetime | None = None) -> str:
    base = ts or now_shanghai()
    return f"evt_{base.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def trace_log_path(ts: datetime | None = None) -> Path:
    ensure_working_files()
    return WORKING_TRACE_DIR / f"runtime-{date_str_shanghai(ts)}.log"
