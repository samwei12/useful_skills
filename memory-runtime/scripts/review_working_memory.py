#!/usr/bin/env python3
"""Render a compact markdown review of minimal working-memory state."""

from __future__ import annotations

import argparse
import json
from typing import Any

from memory_common import WORKING_STATE_FILE, ensure_working_files, iso_ts_shanghai
from promotion_queue import queue_counts, sync_candidates
from suggest_promotions import rank_score, select_candidates
from working_common import load_archive, load_cache


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Review working memory state as markdown')
    p.add_argument('--top-k', type=int, default=8)
    p.add_argument('--checkpoint', action='store_true')
    p.add_argument('--trigger', default='manual')
    return p.parse_args()


def load_state() -> dict[str, Any]:
    if not WORKING_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(WORKING_STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    WORKING_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def persist_review_state(trigger: str, checkpoint: bool) -> dict[str, Any]:
    ensure_working_files()
    state = load_state()
    maintenance = state.setdefault('maintenance', {})
    now = iso_ts_shanghai()
    maintenance['last_counter_update_at'] = now
    maintenance['last_review_trigger'] = trigger
    maintenance['last_review_scheduled_at'] = now
    maintenance['last_review_completed_at'] = now
    if checkpoint:
        maintenance['append_since_review'] = 0
    save_state(state)
    return state


def section_lines(title: str, rows: list[dict[str, Any]], top_k: int) -> list[str]:
    lines = [f'## {title}']
    if not rows:
        lines.extend(['- (none)', ''])
        return lines
    for row in rows[:top_k]:
        lines.append(f"- [{row.get('type','fact')}] {row.get('topic','')} :: {row.get('summary','')}")
    lines.append('')
    return lines


def run_review_checkpoint(top_k: int, trigger: str = 'manual') -> str:
    args = argparse.Namespace(top_k=top_k, checkpoint=True, trigger=trigger)
    cache = load_cache()
    archive = load_archive()
    state = persist_review_state(trigger=trigger, checkpoint=True)
    return render_report(args, cache, archive, state, trigger=trigger)


def render_report(args: argparse.Namespace, cache: list[dict[str, Any]], archive: list[dict[str, Any]], state: dict[str, Any], trigger: str = 'manual') -> str:
    facts = [row for row in cache if str(row.get('type', 'fact')) == 'fact']
    feedback = [row for row in cache if str(row.get('type', 'fact')) == 'feedback']
    promotion_ready = sorted(select_candidates(argparse.Namespace(top_k=args.top_k, include_facts=False, dry_run=False), cache=cache), key=rank_score, reverse=True)
    queue_items = sync_candidates(promotion_ready, trigger=trigger)
    counts = queue_counts(queue_items)
    lines = [
        '# Working Memory Review',
        f'- generated_at: {iso_ts_shanghai()}',
        f'- cache_records: {len(cache)}',
        f'- archive_records: {len(archive)}',
        f'- fact_records: {len(facts)}',
        f'- feedback_records: {len(feedback)}',
        f"- promotion_queue_active: {counts.get('active', 0)}",
        '- promotion_queue_file: ~/.memories/working/promotion_review_queue.jsonl',
        '',
    ]
    lines.extend(section_lines(f'Recent Facts (top {args.top_k})', facts, args.top_k))
    lines.extend(section_lines(f'Recent Feedback (top {args.top_k})', feedback, args.top_k))
    lines.extend(section_lines(f'Promotion Review Queue (top {args.top_k})', promotion_ready, args.top_k))
    return '\n'.join(lines).rstrip() + '\n'


def main() -> int:
    args = parse_args()
    cache = load_cache()
    archive = load_archive()
    state = load_state()
    if args.checkpoint:
        state = persist_review_state(trigger=args.trigger, checkpoint=args.checkpoint)
    report = render_report(args, cache, archive, state, trigger=args.trigger)
    print(report, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
