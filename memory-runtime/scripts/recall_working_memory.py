#!/usr/bin/env python3
"""Recall working-memory cache with normalized queries, light mode, and traceable ranking."""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from typing import Any

from memory_common import get_embedding_runtime_config, get_recall_runtime_config, iso_ts_shanghai
from vector_recall_common import (
    DEFAULT_EMBEDDING_BACKEND,
    DEFAULT_EMBEDDING_MODEL,
    detect_provider,
    detect_session_id,
    ensure_vector_index_current,
    has_vector_index_rows,
    load_session_state,
    make_session_key,
    search_vectors,
    seen_ids_for_session,
    update_session_seen,
)
from runtime_trace import current_trace_id, emit_runtime_trace
from working_common import contains_evidence_query, derive_session_profile, load_archive, load_cache, save_archive, save_cache, update_state, p0_match_score

FEEDBACK_SCORE_BONUS = 0.35
SUMMARY_SIMILARITY_THRESHOLD = 0.88
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CONTINUATION_MARKERS = (
    "继续",
    "展开",
    "接着上面",
    "接着",
    "基于刚才",
    "再想想",
    "总结一下",
    "解释一下",
    "详细说说",
    "接着刚才",
)
CORRECTION_MARKERS = (
    "不对",
    "我的意思是",
    "你这里错了",
    "不是这个意思",
    "不是这个",
    "说错了",
)


def parse_args() -> argparse.Namespace:
    recall_config = get_recall_runtime_config()
    embedding_config = get_embedding_runtime_config()
    p = argparse.ArgumentParser(description="Recall working memory")
    p.add_argument("--query", default="", help="Recall query")
    p.add_argument("--top-k", type=int, default=recall_config["default_top_k"], help="Maximum number of final injected items")
    p.add_argument(
        "--candidate-k",
        type=int,
        default=recall_config["default_candidate_k"],
        help="Number of candidates to recall before final selection; default is derived from --top-k",
    )
    p.add_argument("--include-evidence", action="store_true")
    p.add_argument("--include-archive", action="store_true")
    p.add_argument("--semantic", action="store_true", help="Use semantic recall over the vector index")
    p.add_argument("--provider", default="")
    p.add_argument("--session-id", default="")
    p.add_argument("--backend", default=embedding_config["backend"] or DEFAULT_EMBEDDING_BACKEND)
    p.add_argument("--model", default=embedding_config["model"] or DEFAULT_EMBEDDING_MODEL)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def resolve_candidate_k(top_k: int, candidate_k: int) -> int:
    if candidate_k > 0:
        return candidate_k
    return max(top_k * 3, 12)


def latest_recall_context(provider: str, session_id: str) -> dict[str, Any]:
    key = make_session_key(provider, session_id)
    if not key:
        return {}
    data = load_session_state()
    session = data.get("sessions", {}).get(key, {})
    if not isinstance(session, dict):
        return {}
    recalls = session.get("recent_recalls", [])
    if not isinstance(recalls, list) or not recalls:
        return {}
    last = recalls[-1]
    return last if isinstance(last, dict) else {}


def normalize_recall_query(
    query: str,
    *,
    last_query: str = "",
    last_phase: str = "",
    short_query_max_chars: int,
) -> tuple[str, str, list[str]]:
    current = str(query or "").strip()
    previous = str(last_query or "").strip()
    lowered = current.lower()
    reasons: list[str] = []
    mode = "normal"
    if any(marker in current for marker in CORRECTION_MARKERS):
        mode = "correction"
        reasons.append("correction_marker")
    elif any(marker in current for marker in CONTINUATION_MARKERS):
        mode = "continuation"
        reasons.append("continuation_marker")
    elif len(current) <= short_query_max_chars and previous and last_phase in {"semantic", "p0", "p0-fallback", "continuation"}:
        mode = "continuation"
        reasons.append("short_query_with_recent_context")

    normalized = current
    if mode in {"continuation", "correction"} and previous and previous != current:
        normalized = f"{previous} {current}".strip()
        reasons.append("merged_last_query")
    return normalized or current, mode, reasons


def effective_budget(top_k: int, candidate_k: int, recall_mode: str, recall_config: dict[str, Any]) -> tuple[int, int, int, bool]:
    continuation_config = recall_config["continuation"]
    if recall_mode == "continuation":
        return (
            min(continuation_config["inject_k"], top_k),
            min(continuation_config["candidate_k"], candidate_k),
            min(continuation_config["max_feedback"], top_k),
            True,
        )
    return top_k, candidate_k, min(recall_config["max_inject_feedback"], top_k), False


def _feedback_bonus(row: dict[str, Any]) -> float:
    if str(row.get("type", "fact")) == "feedback":
        return FEEDBACK_SCORE_BONUS
    return 0.0


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _summary_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = _normalize_text(str(left.get("summary", "")))
    right_text = _normalize_text(str(right.get("summary", "")))
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _is_near_duplicate(candidate: dict[str, Any], selected: dict[str, Any]) -> bool:
    if str(candidate.get("type", "fact")) != str(selected.get("type", "fact")):
        return False
    candidate_id = str(candidate.get("id", ""))
    selected_id = str(selected.get("id", ""))
    if candidate_id and selected_id and candidate_id == selected_id:
        return True
    candidate_topic = _normalize_text(str(candidate.get("topic", "")))
    selected_topic = _normalize_text(str(selected.get("topic", "")))
    if candidate_topic and selected_topic and candidate_topic == selected_topic:
        return True
    similarity = _summary_similarity(candidate, selected)
    if similarity >= SUMMARY_SIMILARITY_THRESHOLD:
        return True
    candidate_summary = _normalize_text(str(candidate.get("summary", "")))
    selected_summary = _normalize_text(str(selected.get("summary", "")))
    if candidate_summary and selected_summary:
        shorter, longer = sorted((candidate_summary, selected_summary), key=len)
        if len(shorter) >= 12 and shorter in longer:
            return True
    return False


def _append_unique(selected_rows: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    for selected in selected_rows:
        if _is_near_duplicate(row, selected):
            return False
    selected_rows.append(row)
    return True


def select_p0_candidates(rows: list[dict[str, Any]], query: str, candidate_k: int, *, seen_ids: set[str] | None = None) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    seen = seen_ids or set()
    for item in rows:
        row_id = str(item.get("id", ""))
        if row_id and row_id in seen:
            continue
        lexical_score = p0_match_score(query, item)
        if lexical_score <= 0:
            continue
        reasons = ["lexical_match"]
        feedback_bonus = _feedback_bonus(item)
        if feedback_bonus:
            reasons.append("feedback_bonus")
        enriched = dict(item)
        enriched["_lexical_score"] = lexical_score
        enriched["_score"] = lexical_score + feedback_bonus
        enriched["_reason"] = reasons
        scored.append(enriched)
    scored.sort(key=lambda row: (float(row.get("_score", 0.0)), str(row.get("topic", ""))), reverse=True)
    return scored[:candidate_k]


def _term_overlap_score(query: str, row: dict[str, Any]) -> float:
    query_lower = (query or "").lower()
    haystack = " ".join([str(row.get("topic", "") or ""), str(row.get("summary", "") or "")]).lower()
    if not query_lower or not haystack:
        return 0.0
    return 0.2 if query_lower in haystack else 0.0


def _recency_bonus(logged_at: str) -> tuple[float, str]:
    if not logged_at:
        return 0.0, "recency_unknown"
    try:
        logged_dt = datetime.fromisoformat(str(logged_at).replace("Z", "+00:00"))
    except ValueError:
        date_hint = str(logged_at)[:10]
        if date_hint == iso_ts_shanghai()[:10]:
            return 0.30, "recency_lt_1d"
        return 0.08, "recency_unparsed"
    now = datetime.now(SHANGHAI_TZ)
    if logged_dt.tzinfo is None:
        logged_dt = logged_dt.replace(tzinfo=SHANGHAI_TZ)
    days = max((now - logged_dt.astimezone(SHANGHAI_TZ)).total_seconds() / 86400.0, 0.0)
    if days <= 1:
        return 0.32, "recency_lt_1d"
    if days <= 3:
        return 0.24, "recency_lt_3d"
    if days <= 7:
        return 0.16, "recency_lt_7d"
    if days <= 30:
        return 0.08, "recency_lt_30d"
    return -0.03, "recency_gte_30d"


def select_semantic_candidates(query: str, candidate_k: int, *, provider: str, session_id: str, backend: str, model: str) -> tuple[list[dict[str, Any]], bool]:
    seen_ids = seen_ids_for_session(provider, session_id) if provider and session_id else set()
    raw_rows = search_vectors(query, top_k=candidate_k, backend=backend, model=model)
    had_candidates = bool(raw_rows)
    rescored: list[dict[str, Any]] = []
    for row in raw_rows:
        row_id = str(row.get("id", ""))
        if row_id and row_id in seen_ids:
            continue
        reasons = ["semantic_match"]
        bonus, recency_reason = _recency_bonus(str(row.get("logged_at", "")))
        if bonus:
            reasons.append(recency_reason)
        feedback_bonus = _feedback_bonus(row)
        if feedback_bonus:
            reasons.append("feedback_bonus")
        term_bonus = _term_overlap_score(query, row)
        if term_bonus:
            reasons.append("term_overlap")
        enriched = dict(row)
        enriched["_score"] = float(row.get("_semantic_score", 0.0)) + bonus + feedback_bonus + term_bonus
        enriched["_reason"] = reasons
        rescored.append(enriched)
    rescored.sort(key=lambda row: (float(row.get("_score", 0.0)), str(row.get("logged_at", ""))), reverse=True)
    return rescored[:candidate_k], had_candidates


def split_result_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts = [row for row in rows if str(row.get("type", "fact")) != "feedback"]
    feedback = [row for row in rows if str(row.get("type", "fact")) == "feedback"]
    return facts, feedback


def finalize_injection(candidates: list[dict[str, Any]], inject_k: int, *, max_feedback: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    ranked_candidates = sorted(
        candidates,
        key=lambda row: (float(row.get("_score", 0.0)), str(row.get("logged_at", "")), str(row.get("topic", ""))),
        reverse=True,
    )
    unique_candidates: list[dict[str, Any]] = []
    dedup_dropped = 0
    for row in ranked_candidates:
        if not _append_unique(unique_candidates, row):
            dedup_dropped += 1
    _, feedback_candidates = split_result_rows(unique_candidates)
    final_rows: list[dict[str, Any]] = []
    for row in feedback_candidates:
        if len([item for item in final_rows if str(item.get("type", "fact")) == "feedback"]) >= max_feedback:
            break
        if _append_unique(final_rows, row):
            row.setdefault("_reason", [])
            if "feedback_budget_pick" not in row["_reason"]:
                row["_reason"].append("feedback_budget_pick")
    for row in unique_candidates:
        if len(final_rows) >= inject_k:
            break
        if _append_unique(final_rows, row):
            row.setdefault("_reason", [])
            if "final_budget_pick" not in row["_reason"]:
                row["_reason"].append("final_budget_pick")
    final_rows = sorted(
        final_rows,
        key=lambda row: (float(row.get("_score", 0.0)), str(row.get("logged_at", "")), str(row.get("topic", ""))),
        reverse=True,
    )
    facts, feedback = split_result_rows(final_rows)
    trace = {
        "candidate_count": len(ranked_candidates),
        "after_dedup_count": len(unique_candidates),
        "dedup_dropped_count": dedup_dropped,
        "inject_count": len(final_rows),
        "max_feedback": max_feedback,
        "inject_limit": inject_k,
    }
    return facts, feedback, final_rows, trace


def print_summary(
    facts: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
    profile: dict[str, Any],
    include_evidence: bool,
    *,
    mode: str,
    provider: str,
    session_id: str,
    notice: str = "",
) -> None:
    total = len(facts) + len(feedback)
    print(f"# Working Memory Recall ({mode})")
    session_bits = []
    if provider:
        session_bits.append(f"provider={provider}")
    if session_id:
        session_bits.append(f"session_id={session_id}")
    session_text = " ".join(session_bits) if session_bits else "session=none"
    print(f"- session_profile: intent={profile.get('intent','general')} mode={mode} topic_hint={profile.get('topic_hint','') or '-'} {session_text}")
    print(f"- result_count: {total}")
    if notice:
        print(f"- note: {notice}")
    print("## Relevant Facts")
    if not facts:
        print("- (none)")
    for row in facts:
        print(f"- [fact] {row.get('topic','')}: {row.get('summary','')}")
        if include_evidence and row.get("evidence"):
            print(f"  evidence: {row.get('evidence','')}")
    print("## Relevant Feedback")
    if not feedback:
        print("- (none)")
    for row in feedback:
        print(f"- [feedback] {row.get('topic','')}: {row.get('summary','')}")
        if include_evidence and row.get("evidence"):
            print(f"  evidence: {row.get('evidence','')}")



def main() -> int:
    args = parse_args()
    if args.top_k <= 0:
        raise SystemExit("--top-k must be > 0")
    if args.candidate_k < 0:
        raise SystemExit("--candidate-k must be >= 0")

    script_name = Path(__file__).name
    trace_id = current_trace_id()
    recall_started_at = time.perf_counter()
    recall_config = get_recall_runtime_config()
    provider = detect_provider(args.provider)
    session_id = detect_session_id(provider, args.session_id)
    first_recall_only_enabled = recall_config["first_recall_only_enabled"]
    session_dedupe_enabled = recall_config["session_dedupe_enabled"]
    session_state_updated = False
    fallback_reason = ""
    semantic_error = ""
    semantic_requested = False
    semantic_available = False
    archive_used = False
    vector_sync: dict[str, Any] = {
        "checked": False,
        "in_sync": False,
        "refresh_action": "none",
        "missing_count": 0,
        "stale_count": 0,
        "extra_count": 0,
    }
    try:
        cache = load_cache()
        archive = load_archive()
        candidate_k = resolve_candidate_k(args.top_k, args.candidate_k)
        last_context = latest_recall_context(provider, session_id) if provider and session_id else {}
        normalized_query, recall_mode, normalize_reasons = normalize_recall_query(
            args.query,
            last_query=str(last_context.get("query", "")) if session_dedupe_enabled else "",
            last_phase=str(last_context.get("phase", "")) if session_dedupe_enabled else "",
            short_query_max_chars=recall_config["continuation"]["short_query_max_chars"],
        )
        inject_k, effective_candidate_k, max_feedback, light_mode_applied = effective_budget(args.top_k, candidate_k, recall_mode, recall_config)
        profile = derive_session_profile(normalized_query, include_evidence=args.include_evidence)
        profile["recall_mode"] = recall_mode
        profile["original_query"] = args.query
        include_evidence = bool(profile.get("evidence_need", False) or args.include_evidence or contains_evidence_query(normalized_query))

        emit_runtime_trace(
            action="recall_working_memory",
            status="started",
            script=script_name,
            trace_id=trace_id,
            provider=provider,
            session_id=session_id,
            input_text=args.query,
            details={
                "semantic_arg": bool(args.semantic),
                "recall_enabled": recall_config["enabled"],
                "semantic_enabled": recall_config["semantic_enabled"],
                "first_recall_only_enabled": first_recall_only_enabled,
                "session_dedupe_enabled": session_dedupe_enabled,
                "top_k": args.top_k,
                "candidate_k_requested": candidate_k,
                "include_archive": bool(args.include_archive),
                "dry_run": bool(args.dry_run),
                "recall_mode": recall_mode,
            },
        )

        mode = recall_mode if recall_mode != "normal" else "p0"
        candidates: list[dict[str, Any]] = []
        final_rows: list[dict[str, Any]] = []
        seen_ids = seen_ids_for_session(provider, session_id) if session_dedupe_enabled and provider and session_id else set()

        if not recall_config["enabled"]:
            duration_ms = int((time.perf_counter() - recall_started_at) * 1000)
            print_summary(
                [],
                [],
                profile,
                include_evidence,
                mode="disabled",
                provider=provider,
                session_id=session_id,
                notice="recall disabled by runtime_config.toml",
            )
            emit_runtime_trace(
                action="recall_working_memory",
                status="ok",
                script=script_name,
                trace_id=trace_id,
                provider=provider,
                session_id=session_id,
                input_text=args.query,
                duration_ms=duration_ms,
                details={
                    "mode": "disabled",
                    "semantic_requested": False,
                    "semantic_available": False,
                    "fallback_reason": "recall_disabled",
                    "candidate_k_requested": candidate_k,
                    "candidate_k_effective": effective_candidate_k,
                    "inject_count": 0,
                    "facts_count": 0,
                    "feedback_count": 0,
                    "archive_used": False,
                    "session_state_updated": False,
                    "dry_run": bool(args.dry_run),
                },
            )
            return 0

        if first_recall_only_enabled and provider and session_id and last_context:
            duration_ms = int((time.perf_counter() - recall_started_at) * 1000)
            print_summary(
                [],
                [],
                profile,
                include_evidence,
                mode="skipped",
                provider=provider,
                session_id=session_id,
                notice="recall already performed once in this session; skipped by first_recall_only_enabled",
            )
            emit_runtime_trace(
                action="recall_working_memory",
                status="ok",
                script=script_name,
                trace_id=trace_id,
                provider=provider,
                session_id=session_id,
                input_text=args.query,
                duration_ms=duration_ms,
                details={
                    "mode": "skipped",
                    "semantic_requested": False,
                    "semantic_available": False,
                    "fallback_reason": "first_recall_only",
                    "candidate_k_requested": candidate_k,
                    "candidate_k_effective": effective_candidate_k,
                    "inject_count": 0,
                    "facts_count": 0,
                    "feedback_count": 0,
                    "archive_used": False,
                    "session_state_updated": False,
                    "dry_run": bool(args.dry_run),
                },
            )
            return 0

        if recall_config["semantic_enabled"] and cache:
            try:
                vector_sync = ensure_vector_index_current(cache, backend=args.backend, model=args.model)
                vector_sync["checked"] = True
            except Exception as exc:
                vector_sync = {
                    "checked": True,
                    "in_sync": False,
                    "refresh_action": "failed",
                    "missing_count": 0,
                    "stale_count": 0,
                    "extra_count": 0,
                    "error": str(exc),
                }
                print(f"[WARN] vector index sync failed before recall: {exc}")
        semantic_available = has_vector_index_rows()
        semantic_requested = bool(args.semantic or (recall_config["semantic_enabled"] and semantic_available))
        if semantic_requested:
            try:
                candidates, had_candidates = select_semantic_candidates(
                    normalized_query,
                    effective_candidate_k,
                    provider=provider,
                    session_id=session_id,
                    backend=args.backend,
                    model=args.model,
                )
                mode = f"semantic-{recall_mode}" if recall_mode != "normal" else "semantic"
            except Exception as exc:
                semantic_error = str(exc)
                fallback_reason = "semantic_exception"
                print(f"[WARN] semantic recall unavailable, fallback to p0: {exc}")
                candidates = select_p0_candidates(cache, normalized_query, effective_candidate_k, seen_ids=seen_ids)
            else:
                if not candidates and not (had_candidates and provider and session_id):
                    fallback_reason = "semantic_empty"
                    candidates = select_p0_candidates(cache, normalized_query, effective_candidate_k, seen_ids=seen_ids)
                    mode = f"p0-fallback-{recall_mode}" if recall_mode != "normal" else "p0-fallback"
        else:
            candidates = select_p0_candidates(cache, normalized_query, effective_candidate_k, seen_ids=seen_ids)
            if recall_mode != "normal":
                mode = f"p0-{recall_mode}"

        facts, feedback, final_rows, finalize_trace = finalize_injection(candidates, inject_k, max_feedback=max_feedback)

        if args.include_archive and len(final_rows) < inject_k:
            archive_used = True
            archive_seen = seen_ids.union({str(row.get("id", "")) for row in candidates if str(row.get("id", ""))})
            archive_candidates = select_p0_candidates(archive, normalized_query, effective_candidate_k, seen_ids=archive_seen)
            facts, feedback, final_rows, finalize_trace = finalize_injection(candidates + archive_candidates, inject_k, max_feedback=max_feedback)

        duration_ms = int((time.perf_counter() - recall_started_at) * 1000)
        print_summary(facts, feedback, profile, include_evidence, mode=mode, provider=provider, session_id=session_id)

        recall_trace = {
            "original_query": args.query,
            "normalized_query": normalized_query,
            "recall_mode": recall_mode,
            "normalize_reasons": normalize_reasons,
            "light_mode_applied": light_mode_applied,
            "candidate_k_requested": candidate_k,
            "candidate_k_effective": effective_candidate_k,
            **finalize_trace,
        }

        if not args.dry_run and (facts or feedback):
            result_ids = [str(row.get("id", "")) for row in final_rows if str(row.get("id", ""))]
            if result_ids and session_dedupe_enabled and provider and session_id:
                update_session_seen(
                    provider,
                    session_id,
                    result_ids,
                    last_result_ids=result_ids,
                    last_query=normalized_query,
                    last_phase=mode,
                    results=final_rows,
                    duration_ms=duration_ms,
                    recall_trace=recall_trace,
                )
                session_state_updated = True
            save_cache(cache)
            save_archive(archive)
            update_state(cache, archive, note=f"recall_working_memory_{mode}")

        emit_runtime_trace(
            action="recall_working_memory",
            status="ok",
            script=script_name,
            trace_id=trace_id,
            provider=provider,
            session_id=session_id,
            input_text=args.query,
            duration_ms=duration_ms,
            details={
                "mode": mode,
                "semantic_requested": semantic_requested,
                "semantic_available": semantic_available,
                "fallback_reason": fallback_reason,
                "candidate_k_requested": candidate_k,
                "candidate_k_effective": effective_candidate_k,
                "inject_count": len(final_rows),
                "facts_count": len(facts),
                "feedback_count": len(feedback),
                "archive_used": archive_used,
                "session_state_updated": session_state_updated,
                "vector_sync_checked": bool(vector_sync.get("checked", False)),
                "vector_sync_in_sync": bool(vector_sync.get("in_sync", False)),
                "vector_sync_action": str(vector_sync.get("refresh_action", "none")),
                "vector_sync_missing": int(vector_sync.get("missing_count", 0)),
                "vector_sync_stale": int(vector_sync.get("stale_count", 0)),
                "vector_sync_extra": int(vector_sync.get("extra_count", 0)),
                "dry_run": bool(args.dry_run),
            },
            error=semantic_error,
        )
        return 0
    except Exception as exc:
        emit_runtime_trace(
            action="recall_working_memory",
            status="failed",
            script=script_name,
            trace_id=trace_id,
            provider=provider,
            session_id=session_id,
            input_text=args.query,
            duration_ms=int((time.perf_counter() - recall_started_at) * 1000),
            details={
                "semantic_requested": semantic_requested,
                "semantic_available": semantic_available,
                "fallback_reason": fallback_reason,
                "session_state_updated": session_state_updated,
            },
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
