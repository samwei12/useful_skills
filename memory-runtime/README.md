# memory-runtime

Local file-based memory runtime for agent sessions.

This skill is built around a simple idea: keep durable memory in files you can inspect, diff, back up, and migrate without depending on a hosted database.

## What It Does

- bootstraps a minimal session context from local memory
- stores daily memory logs as append-only Markdown
- maintains derived working state under a local `working/` directory
- supports semantic or lexical recall before answering a real task
- captures user corrections as structured feedback
- surfaces `MEMORY.md` promotion candidates instead of auto-writing them

## Storage Model

By default the runtime writes under:

```text
~/.memories/
├── MEMORY.md
├── runtime_config.toml
├── memories/
│   └── YYYY-MM-DD.md
└── working/
    ├── cache.jsonl
    ├── archive.jsonl
    ├── state.json
    ├── cache_vectors.sqlite3
    ├── recall_session_state.json
    ├── promotion_review_queue.jsonl
    └── traces/
```

To isolate a different workspace, override the root:

```bash
MEMORY_ROOT=/tmp/memory-demo uv run python scripts/session_bootstrap.py --days 3 --mode summary
```

## Prerequisites

- Python 3.11+
- `uv` is recommended for running the scripts
- macOS or Linux is the current target environment

Optional for semantic recall:

- Ollama or another compatible local embedding setup through `runtime_config.toml`

If you want a no-service setup, edit `~/.memories/runtime_config.toml` and switch:

```toml
[embedding]
backend = "simple"
model = "hash"
dim = 128
```

That uses the built-in hash embedding fallback instead of calling Ollama.

## Quick Start

Run commands from this directory:

```bash
cd memory-runtime
```

1. Bootstrap the local runtime:

```bash
uv run python scripts/session_bootstrap.py --days 3 --mode summary
```

This initializes `MEMORY.md`, recent daily logs, and derived working files if they do not exist.

2. Recall memory for the first real user task:

```bash
uv run python scripts/recall_working_memory.py --query "用户想把 GitHub workflow 做成一个 skill" --top-k 4
```

3. Append a fact after completing work:

```bash
uv run python scripts/append_entry.py \
  --topic "github_skill_research" \
  --summary "官方样本里更接近 PR/comment/CI，repo create 需要自己补 workflow" \
  --evidence "调研 openai/skills 和社区 skill 索引" \
  --type fact
```

4. Capture a user correction immediately:

```bash
uv run python scripts/append_feedback.py \
  --topic "prefer_minimal_closed_loop" \
  --summary "如果只是偶发创建 GitHub 仓库，不要先做 skill，直接执行 gh workflow" \
  --evidence "用户明确表示这部分暂时不用抽 skill" \
  --effect corrected \
  --target-query "GitHub repo create skill"
```

5. Save work and surface promotion candidates:

```bash
uv run python scripts/save_work_checkpoint.py \
  --topic "save_work_github_repo_flow" \
  --summary "完成 gh 安装、登录验证和仓库创建闭环" \
  --evidence "本轮工作完成"
```

## Recommended Workflow

Use this skill in a tight loop:

1. At session start, run `session_bootstrap.py`.
2. Before the first substantial answer, run one explicit recall using the raw user task.
3. During work, append durable facts with `append_entry.py`.
4. When the user corrects a rule or preference, capture it with `append_feedback.py`.
5. At the end of a meaningful work block, run `save_work_checkpoint.py`.

## Important Rules

- `MEMORY.md` is not auto-written.
- Daily logs are append-only.
- `working/*` is derived state, not source of truth.
- The scripts optimize for future retrieval, so `topic` and `summary` should be query-oriented.
- Large payloads are blocked or soft-stopped to avoid low-signal memory writes.

## Agent Integration

If you want to use this as an agent skill, keep `SKILL.md` as the contract and call the scripts from the agent runtime. The intended sequence is:

- bootstrap once at session start
- run one recall for the first real task
- reuse current turn context instead of repeatedly recalling
- write facts and feedback during or after work
- use save-work to review and surface promotion candidates
