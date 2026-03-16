#!/usr/bin/env python3
"""Append one save-work entry, checkpoint review, and preview MEMORY candidates."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from promotion_queue import load_queue, queue_counts
from review_working_memory import run_review_checkpoint
from runtime_trace import build_trace_env, current_trace_id, emit_runtime_trace
from suggest_promotions import rank_score, select_candidates
from working_common import load_cache


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Save work with review checkpoint and MEMORY candidate prompt')
    p.add_argument('--topic', required=True)
    p.add_argument('--summary', required=True)
    p.add_argument('--evidence', default='')
    p.add_argument('--soft-max', type=int, default=500)
    p.add_argument('--hard-max', type=int, default=1500)
    p.add_argument('--sweep-every-appends', type=int, default=20)
    p.add_argument('--sweep-decay', type=int, default=2)
    p.add_argument('--review-top-k', type=int, default=8)
    p.add_argument('--candidate-top-k', type=int, default=8)
    p.add_argument('--include-evidence', action='store_true')
    p.add_argument('--include-facts', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    return p.parse_args()


def load_memory_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    promotion_args = argparse.Namespace(
        top_k=args.candidate_top_k,
        include_facts=args.include_facts,
        include_evidence=args.include_evidence,
        dry_run=False,
    )
    return sorted(select_candidates(promotion_args, cache=load_cache()), key=rank_score, reverse=True)[: args.candidate_top_k]


def render_candidates(rows: list[dict[str, Any]]) -> str:
    lines = ['## MEMORY Candidates']
    if not rows:
        lines.append('- (none)')
        lines.append('- note: 本次保存工作后暂无进入 review queue 的候选。')
        return '\n'.join(lines)
    lines.append('- note: 下面是待审候选，保留人工确认后再写入 `MEMORY.md`。')
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. [{row.get('type', 'fact')}] topic={row.get('topic', '')}")
        lines.append(f"   {row.get('summary', '')}")
    return '\n'.join(lines)


def queue_status_snapshot() -> str:
    items = load_queue()
    counts = queue_counts(items)
    lines = [
        '## Promotion Queue Status',
        '- file: `~/.memories/working/promotion_review_queue.jsonl`',
        f"- active_candidate_count: {counts['active']}",
        f"- accept: {counts['accept']}",
        f"- defer: {counts['defer']}",
        f"- pending: {counts['pending']}",
        '- reject: 删除后不保留',
    ]
    return '\n'.join(lines)


def main() -> int:
    args = parse_args()
    append_entry = Path(__file__).with_name('append_entry.py')
    script_name = Path(__file__).name
    trace_id = current_trace_id()
    started_at = time.perf_counter()

    append_command = [
        sys.executable,
        str(append_entry),
        '--topic',
        args.topic,
        '--summary',
        args.summary,
        '--evidence',
        args.evidence,
        '--type',
        'fact',
        '--soft-max',
        str(args.soft_max),
        '--hard-max',
        str(args.hard_max),
        '--sweep-every-appends',
        str(args.sweep_every_appends),
        '--sweep-decay',
        str(args.sweep_decay),
        '--skip-working-review',
    ]
    if args.dry_run:
        append_command.append('--dry-run')

    emit_runtime_trace(
        action='save_work_checkpoint',
        status='started',
        script=script_name,
        trace_id=trace_id,
        input_text=f'{args.topic} {args.summary}',
        details={
            'review_top_k': args.review_top_k,
            'candidate_top_k': args.candidate_top_k,
            'dry_run': bool(args.dry_run),
        },
    )
    try:
        proc = subprocess.run(append_command, text=True, capture_output=True, env=build_trace_env(trace_id=trace_id))
        if proc.stdout:
            print(proc.stdout, end='')
        if proc.stderr:
            print(proc.stderr, end='', file=sys.stderr)
        if proc.returncode != 0:
            emit_runtime_trace(
                action='save_work_checkpoint',
                status='failed',
                script=script_name,
                trace_id=trace_id,
                input_text=f'{args.topic} {args.summary}',
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                details={'append_returncode': proc.returncode},
                error=proc.stderr.strip(),
            )
            return proc.returncode
        if args.dry_run:
            emit_runtime_trace(
                action='save_work_checkpoint',
                status='dry_run',
                script=script_name,
                trace_id=trace_id,
                input_text=f'{args.topic} {args.summary}',
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                details={'append_returncode': proc.returncode},
            )
            return proc.returncode

        review_code = 0
        review_error = ''
        try:
            print(run_review_checkpoint(top_k=args.review_top_k, trigger='save_work'), end='')
        except Exception as exc:
            review_code = 1
            review_error = str(exc)
            print(f'[WARN] review checkpoint failed after save-work append: {exc}', file=sys.stderr)
        candidates = load_memory_candidates(args)

        print('# Save Work Checkpoint')
        print(f"- review_checkpoint: {'ok' if review_code == 0 else 'failed'}")
        print(f'- memory_candidate_count: {len(candidates)}')
        print('')
        print(queue_status_snapshot())
        print('')
        print(render_candidates(candidates))
        emit_runtime_trace(
            action='save_work_checkpoint',
            status='ok',
            script=script_name,
            trace_id=trace_id,
            input_text=f'{args.topic} {args.summary}',
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            details={
                'append_returncode': proc.returncode,
                'review_code': review_code,
                'memory_candidate_count': len(candidates),
            },
            error=review_error,
        )
        return 0
    except Exception as exc:
        emit_runtime_trace(
            action='save_work_checkpoint',
            status='failed',
            script=script_name,
            trace_id=trace_id,
            input_text=f'{args.topic} {args.summary}',
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            error=str(exc),
        )
        raise


if __name__ == '__main__':
    raise SystemExit(main())
