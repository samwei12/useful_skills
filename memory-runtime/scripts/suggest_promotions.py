#!/usr/bin/env python3
"""Preview promotion candidates from minimal working cache."""

from __future__ import annotations

import argparse
from typing import Any

from working_common import load_cache

PROMOTION_HINTS = ('trigger=', 'wrong_default=', 'correct_action=', 'promotion_condition=')


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Suggest MEMORY.md promotion candidates')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--include-facts', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    return p.parse_args()


def eligible(row: dict[str, Any], args: argparse.Namespace) -> bool:
    row_type = str(row.get('type', 'fact'))
    summary = str(row.get('summary', ''))
    if row_type == 'feedback':
        return True
    if args.include_facts and summary:
        return True
    return False


def rank_score(row: dict[str, Any]) -> float:
    score = 1.0 if str(row.get('type', 'fact')) == 'feedback' else 0.5
    summary = str(row.get('summary', ''))
    score += 0.5 if any(token in summary for token in PROMOTION_HINTS) else 0.0
    score += min(len(summary), 200) / 1000.0
    return score


def select_candidates(args: argparse.Namespace, cache: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = cache if cache is not None else load_cache()
    picks = [row for row in rows if eligible(row, args)]
    return sorted(picks, key=rank_score, reverse=True)[: args.top_k]


def main() -> int:
    args = parse_args()
    picks = select_candidates(args)
    print('# Promotion Candidates')
    print(f'- eligible: {len(picks)}')
    for i, row in enumerate(picks, start=1):
        print(f"{i}. [{row.get('type','fact')}] topic={row.get('topic','')}")
        print(f"   {row.get('summary','')}")
    if args.dry_run:
        print('- mode: dry-run')
    else:
        print('- mode: preview-only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
