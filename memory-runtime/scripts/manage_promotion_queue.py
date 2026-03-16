#!/usr/bin/env python3
"""Inspect or update persistent promotion review queue."""

from __future__ import annotations

import argparse

from promotion_queue import load_queue, render_queue_markdown, update_queue_status


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Manage promotion review queue')
    p.add_argument('--id', dest='item_id')
    p.add_argument('--status', choices=['pending', 'accept', 'defer', 'reject'])
    p.add_argument('--note', default='')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.item_id and args.status:
        items = update_queue_status(args.item_id, args.status, note=args.note)
    else:
        items = load_queue()
    print(render_queue_markdown(items), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
