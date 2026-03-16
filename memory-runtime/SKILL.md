---
name: memory-runtime
description: >
  Enforce local memory runtime for file-based memory management: bootstrap
  MEMORY.md, append normalized work logs, refresh working cache, recall
  working memory, capture user corrections, and surface save-work
  MEMORY.md candidates.
---

# Memory Runtime

## Purpose

Use this skill for local file-based memory workflow:

- session bootstrap
- recent-memory recall
- append-only daily logs
- immediate feedback capture for user corrections
- save-work checkpoint and `MEMORY.md` candidate surfacing

## Fixed Paths

- root: `~/.memories`
- long-term: `~/.memories/MEMORY.md`
- daily logs: `~/.memories/memories/YYYY-MM-DD.md`
- runtime config: `~/.memories/runtime_config.toml`
- working state:
  - `cache.jsonl`
  - `state.json`
  - `archive.jsonl`
  - `cache_vectors.sqlite3`
  - `recall_session_state.json`
  - `promotion_review_queue.jsonl`
  - `traces/runtime-YYYY-MM-DD.log`
- Current runtime depends on `topic`, `summary`, `type`; legacy rows may be compatibility-mapped from older text fields.
- Recall / retention / embedding 的常用参数默认从 `~/.memories/runtime_config.toml` 读取；缺失字段回退到内置默认值，现有少量 env var 仅保留给临时调试覆盖。
- Recall 开关分两层：
  - `recall.enabled` = 总开关；关闭后本次 recall 直接降级为 disabled，不再做 semantic / p0 检索。
  - `recall.first_recall_only_enabled` = 同一 session 只允许第一次 recall；后续同 session recall 直接跳过。
  - `recall.session_dedupe_enabled` = 同会话去重；只有在允许同一 session 多次 recall 时，才用来避免重复注入已经见过的相同 id。

## Core Rules

- Timestamps use `Asia/Shanghai` and script-generated ISO format.
- Daily logs are append-only.
- `working/*` is derived state, not source of truth.
- `recall_session_state.json` is the single runtime state for session dedupe and recent recall history.
- `MEMORY.md` is never auto-written; only surface distilled candidates for human review.
- `MEMORY.md` promotion is semantic distillation, not a mechanical upgrade from working-memory entries.
- User corrections are captured immediately, not deferred to save-work.
- New writes should optimize `topic/summary` for future retrieval.
- Current retention policy: keep latest `10` sessions; latest `4` active sessions keep uncompressed recent recall history; older retained sessions keep latest `30` full recall records and compress older history into summary only.
- History compaction must not aggressively drop `seen_ids` for retained sessions, or dedupe correctness may regress.

## Workflow

### 1. Session Start

- Run bootstrap first.
- Default preload is `MEMORY.md + working/state.json + 少量 global 高优先级纠偏`.
- `session_bootstrap.py` 现在会同时展示 `runtime_config.toml` 的关键 recall/runtime 配置摘要，便于确认当前生效参数。
- Do not preload all `domain/project` corrections.
- After the first effective user task input in a new session, use the raw user text as the first recall query.
- Treat that first effective task input as the default recall trigger for the session, not only for obviously non-trivial tasks.
- Do not rely on preload alone once the user has entered a real task; before the first substantial answer, normally run one explicit recall unless the message is clearly memory-irrelevant small talk or a pure micro-edit with no task context.
- After this session-start recall has been done, default to reusing current turn context; do not automatically rerun recall later in the same conversation.

```bash
uv run python scripts/session_bootstrap.py --days 3 --mode summary
```

### 2. Recall

- Semantic recall is the default path when local vector index rows are available; lexical / P0 is fallback only when semantic retrieval is unavailable or fails.
- Output is always split into `Relevant Facts` and `Relevant Feedback`.
- Expand `evidence` only when the query explicitly asks for it.
- If runtime context already exposes `provider + session_id`, pass both so recall can avoid re-injecting the same items across turns.
- If session id cannot be resolved, continue recall without cross-turn dedupe; do not ask the user to manually fetch it.
- Use the raw user message as the recall query.
- In the answer, only apply newly recalled items from the current session.
- Default policy: one recall per session start. Later turns should reuse the session context instead of triggering another automatic recall.
- `--target-last-recall` means reading the latest normal task recall from the current session in `recall_session_state.json`, not a global last-recall file or a save-work/control recall.

```bash
uv run python scripts/recall_working_memory.py --query "..." --provider "codex|claude_code" --session-id "..." --top-k 4
uv run python scripts/recall_working_memory.py --semantic --query "..." --provider "codex|claude_code" --session-id "..." --top-k 4
```

- Default final injection budget is `4`; only pass a larger `--top-k` when you intentionally want a wider recall window for debugging or calibration.
- 若要长期修改默认 `top-k` / `candidate-k` / session dedupe / retention / embedding backend，请优先改 `~/.memories/runtime_config.toml`，而不是散落在命令行参数或环境变量里。

Useful filter:

- `--include-evidence`

### 2.1 Recall Trigger

Run recall only when either of the following is true:

1. Current session has no recall history yet.
2. The user explicitly asks to回忆 / 查记忆 / 看之前记录 / 重新召回，且当前上下文不足以完成任务。

Do not automatically rerun recall later in the same conversation just because:

- topic changed
- task phase advanced
- you are entering conclusion / finalization / root-cause / retrospective
- you are unsure whether the turn is a refinement or a new task

Those cases should default to using the current conversation context unless the user explicitly asks for memory retrieval.

Do not trigger a new recall when the user input is only `保存工作` / `写工作日志`. Save-work should reuse the current session context instead of querying those words themselves.

### 3. Normal Work Log

- All fact writes go through `append_entry.py`.
- Successful append auto-refreshes working cache and local vector index.
- Lazy sweep / review counters are maintained in `working/state.json`.
- When writing new entries, `topic` should describe how this memory will likely be queried later, and `summary` should contain the core conclusion or guardrail future recall actually needs.

```bash
uv run python scripts/append_entry.py --topic "..." --summary "..." --evidence "..." --type fact
```

### 4. Immediate User Correction

- Explicit user correction must be captured immediately.
- Use `append_feedback.py`, not save-work; save-work only补查本轮是否还有未落盘纠正，不作为首次捕获入口。
- Prefer exact target binding in this order:
  - `--target-last-recall`
  - `--target-id`
  - `--target-query`
- If you want a structured guardrail, write it directly in `--summary` with `trigger` / `wrong_default` / `correct_action` / `promotion_condition`.

```bash
uv run python scripts/append_feedback.py --topic "rule_fix" --summary "..." --evidence "..." --effect corrected --target-last-recall
```

Guardrail example:

```bash
uv run python scripts/append_feedback.py --topic "use_plan_skill_for_nontrivial_tasks" --summary "当遇到非 trivial 任务时，不要只口头列步骤；应使用 planning-with-files-v2 制定和维护计划。trigger=非 trivial 任务; wrong_default=只口头列步骤; correct_action=使用 planning-with-files-v2 制定和维护计划; promotion_condition=跨会话重复成立且能降低返工时升级到 MEMORY.md" --evidence "用户明确纠正了大任务规划入口" --effect corrected --target-query "非 trivial 任务如何制定计划"
```

### 5. Save Work

- Save-work is the unified exit for work log append, review checkpoint, and `MEMORY.md` candidate surfacing.
- Save-work should reuse the current session context; do not create a fresh recall record by querying `保存工作` / `写工作日志` themselves.
- If a conclusion still needs retrieval support, run recall in the conclusion/finalization turn before save-work, not because save-work was requested.

```bash
uv run python scripts/save_work_checkpoint.py --topic "save_work_xxx" --summary "..." --evidence "..."
```

### 6. Promotion Queue

- `save_work_checkpoint.py` and `review_working_memory.py` automatically refresh the persistent review queue.
- `promotion_review_queue.jsonl` is a materials layer only; the user should review only the final distilled `MEMORY.md` rule text, not raw queue fields.
- Before surfacing any promotion candidate, read current `MEMORY.md`, perform semantic de-duplication, and distill the candidate into the final rule sentence that would actually be written.
- If a candidate is already covered by an existing `MEMORY.md` rule, is only a paraphrase, or is merely a concrete instance of a broader existing rule, do not surface it as a new proposal.
- Queue statuses: `accept` = 可进入 `MEMORY.md`; `defer` = 后续再看; `pending` = 待判断; `reject` = 直接删除。

```bash
uv run python scripts/manage_promotion_queue.py
uv run python scripts/manage_promotion_queue.py --id "mem_xxx" --status accept --note "跨会话稳定成立"
uv run python scripts/manage_promotion_queue.py --id "mem_xxx" --status defer --note "后续再观察"
uv run python scripts/manage_promotion_queue.py --id "mem_xxx" --status reject --note "已过时可删除"
```

### 7. Runtime Trace / Inspect

- Runtime trace 是本地旁路审计层，不是事实源，不参与多设备同步。
- 落点：`~/.memories/working/traces/runtime-YYYY-MM-DD.log`
- trace 写失败只会打印 warning，不应打断主链路。
- 当需要排查“append 后有没有刷新 cache/vector”“recall 走了 semantic 还是 p0/fallback”“save-work 是否真的触发 review”时，优先查看 trace，而不是手工拼多个状态文件。

常用命令：

```bash
uv run python scripts/inspect_runtime_trace.py --latest-trace
uv run python scripts/inspect_runtime_trace.py --trace-id "trace_xxx"
uv run python scripts/inspect_runtime_trace.py --day "2026-03-12" --last-events 20
```

## Length Guardrail

For `append_entry.py` / `append_feedback.py` payloads:

- soft block: `500 < payload <= 1500`
- hard reject: `payload > 1500`
- compress before retry
