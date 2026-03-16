#!/usr/bin/env python3
"""Minimal append-only runtime trace helper for local memory scripts."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import fcntl

from memory_common import MEMORY_ROOT, ensure_working_files, iso_ts_shanghai, make_event_id, make_trace_id, trace_log_path

TRACE_ENABLED_ENV = "MEMORY_RUNTIME_TRACE"
TRACE_ID_ENV = "MEMORY_TRACE_ID"
TRACE_PROVIDER_ENV = "MEMORY_TRACE_PROVIDER"
TRACE_SESSION_ID_ENV = "MEMORY_TRACE_SESSION_ID"

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)\b(bearer\s+)(\S+)"),
)


def trace_enabled() -> bool:
    value = str(os.environ.get(TRACE_ENABLED_ENV, "1")).strip().lower()
    return value not in {"0", "false", "off", "no"}


def current_trace_id() -> str:
    trace_id = str(os.environ.get(TRACE_ID_ENV, "")).strip()
    return trace_id or make_trace_id()


def build_trace_env(*, trace_id: str | None = None, provider: str = "", session_id: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env[TRACE_ID_ENV] = trace_id or current_trace_id()
    if provider:
        env[TRACE_PROVIDER_ENV] = provider
    if session_id:
        env[TRACE_SESSION_ID_ENV] = session_id
    return env


def _mask_sensitive(text: str, *, max_len: int = 120) -> str:
    value = " ".join(str(text or "").split())
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub(r"\1***", value)
    if len(value) > max_len:
        return value[: max_len - 3] + "..."
    return value


def _normalize_paths(paths: list[str | Path] | None) -> list[str]:
    normalized: list[str] = []
    for item in paths or []:
        path = Path(item)
        try:
            if path.is_absolute():
                normalized.append(str(path.relative_to(MEMORY_ROOT)))
            else:
                normalized.append(str(path))
        except ValueError:
            normalized.append(path.name or str(path))
    return normalized


def emit_runtime_trace(
    *,
    action: str,
    status: str,
    script: str,
    trace_id: str | None = None,
    provider: str = "",
    session_id: str = "",
    input_text: str = "",
    artifacts_read: list[str | Path] | None = None,
    artifacts_written: list[str | Path] | None = None,
    details: dict[str, Any] | None = None,
    error: str = "",
    duration_ms: int | None = None,
) -> bool:
    if not trace_enabled():
        return False
    ensure_working_files()
    payload: dict[str, Any] = {
        "at": iso_ts_shanghai(),
        "trace_id": trace_id or current_trace_id(),
        "event_id": make_event_id(),
        "action": action,
        "status": status,
        "script": script,
    }
    provider_value = provider or str(os.environ.get(TRACE_PROVIDER_ENV, "")).strip()
    session_value = session_id or str(os.environ.get(TRACE_SESSION_ID_ENV, "")).strip()
    if provider_value:
        payload["provider"] = provider_value
    if session_value:
        payload["session_id"] = session_value
    if input_text:
        payload["input_preview"] = _mask_sensitive(input_text)
    read_paths = _normalize_paths(artifacts_read)
    written_paths = _normalize_paths(artifacts_written)
    if read_paths:
        payload["artifacts_read"] = read_paths
    if written_paths:
        payload["artifacts_written"] = written_paths
    if duration_ms is not None:
        payload["duration_ms"] = int(duration_ms)
    if details:
        payload["details"] = details
    if error:
        payload["error"] = _mask_sensitive(error, max_len=240)

    try:
        path = trace_log_path()
        with path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return True
    except Exception as exc:
        print(f"[WARN] runtime trace write failed: {exc}", file=sys.stderr)
        return False
