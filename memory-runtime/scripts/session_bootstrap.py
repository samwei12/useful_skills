#!/usr/bin/env python3
"""Bootstrap minimal memory context for each session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memory_common import (
    MEMORY_FILE,
    MEMORY_RUNTIME_CONFIG_FILE,
    WORKING_STATE_FILE,
    ensure_day_file,
    ensure_memory_file,
    ensure_working_files,
    get_embedding_runtime_config,
    get_recall_runtime_config,
    get_session_retention_config,
    iso_ts_shanghai,
    last_n_days,
)
from working_common import load_cache


def main() -> int:
    p = argparse.ArgumentParser(description='Bootstrap minimal memory context')
    p.add_argument('--days', type=int, default=3)
    p.add_argument('--mode', choices=['summary', 'full'], default='summary')
    p.add_argument('--feedback-limit', type=int, default=5)
    p.add_argument('--topic-limit', type=int, default=5)
    args = p.parse_args()

    ensure_memory_file()
    ensure_working_files()
    memory_text = MEMORY_FILE.read_text(encoding='utf-8')
    cache = load_cache()
    recall_config = get_recall_runtime_config()
    retention_config = get_session_retention_config()
    embedding_config = get_embedding_runtime_config()
    days = last_n_days(max(1, args.days))
    for day in days:
        ensure_day_file(day)

    print('# Memory Session Bootstrap')
    print(f'- generated_at: {iso_ts_shanghai()}')
    print(f'- days: {", ".join(days)}')
    print('')
    print('## MEMORY.md')
    print(f'- file: {MEMORY_FILE}')
    print('```markdown')
    if args.mode == 'full':
        print(memory_text.rstrip())
    else:
        lines = [ln for ln in memory_text.splitlines() if ln.strip()][:60]
        print('\n'.join(lines))
    print('```')

    print('')
    print('## runtime_config.toml')
    print(f'- file: {MEMORY_RUNTIME_CONFIG_FILE}')
    print(f"- recall_enabled: {recall_config['enabled']}")
    print(f"- semantic_enabled: {recall_config['semantic_enabled']}")
    print(f"- first_recall_only_enabled: {recall_config['first_recall_only_enabled']}")
    print(f"- session_dedupe_enabled: {recall_config['session_dedupe_enabled']}")
    print(f"- default_top_k: {recall_config['default_top_k']}")
    print(f"- default_candidate_k: {recall_config['default_candidate_k']}")
    print(
        '- session_retention: '
        f"max_sessions={retention_config['max_sessions']} "
        f"active_uncompressed={retention_config['max_active_sessions_uncompressed']} "
        f"inactive_recalls={retention_config['max_recalls_per_inactive_session']}"
    )
    print(
        '- embedding: '
        f"backend={embedding_config['backend']} "
        f"model={embedding_config['model']} "
        f"dim={embedding_config['dim']}"
    )

    state = {}
    if WORKING_STATE_FILE.exists():
        try:
            state = json.loads(WORKING_STATE_FILE.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            state = {}

    print('')
    print('## working/state.json')
    print(f'- file: {WORKING_STATE_FILE}')
    stats = state.get('stats', {}) if isinstance(state, dict) else {}
    print(f'- cache_records: {stats.get("cache_records", len(cache))}')
    print(f'- fact_records: {stats.get("fact_records", sum(1 for row in cache if row.get("type") == "fact"))}')
    print(f'- feedback_records: {stats.get("feedback_records", sum(1 for row in cache if row.get("type") == "feedback"))}')

    recent_feedback = [row for row in cache if str(row.get('type', 'fact')) == 'feedback'][: args.feedback_limit]
    if recent_feedback:
        print('- recent_feedback:')
        for row in recent_feedback:
            print(f"  - {row.get('topic','')} :: {row.get('summary','')}")

    topics = []
    seen = set()
    for row in cache:
        topic = str(row.get('topic', ''))
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
        if len(topics) >= args.topic_limit:
            break
    if topics:
        print('- recent_topics:')
        for topic in topics:
            print(f'  - {topic}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
