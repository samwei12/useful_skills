# memory-runtime

[English](./README.md) | [简体中文](./README.zh-CN.md)

这是一个面向 agent session 的本地文件记忆运行时。

核心思路很简单：把可沉淀的记忆放进你自己能看、能 diff、能备份、能迁移的文件，而不是依赖托管数据库。

## 它解决什么问题

- 会话开始时先 bootstrap 最小记忆上下文
- 每日记忆以 append-only Markdown 持续累积
- `working/` 下维护派生态，而不是把它当事实源
- 在第一次真实任务前支持 semantic / lexical recall
- 用户纠正可以结构化落为 feedback
- `MEMORY.md` 只出候选，不自动改写

## 存储结构

默认写入位置如下：

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

如果要隔离到别的目录，可以覆盖根路径：

```bash
MEMORY_ROOT=/tmp/memory-demo uv run python scripts/session_bootstrap.py --days 3 --mode summary
```

## 前置条件

- Python 3.11+
- 推荐用 `uv` 运行脚本
- 当前主要面向 macOS / Linux

如果要启用 semantic recall，默认配置会走 `runtime_config.toml` 里的 embedding backend。

如果你不想依赖 Ollama，可以直接改成：

```toml
[embedding]
backend = "simple"
model = "hash"
dim = 128
```

这样会退回内置的 hash embedding。

## 快速上手

在当前目录运行：

```bash
cd memory-runtime
```

1. 初始化运行时：

```bash
uv run python scripts/session_bootstrap.py --days 3 --mode summary
```

2. 用第一次真实任务做 recall：

```bash
uv run python scripts/recall_working_memory.py --query "用户想把 GitHub workflow 做成一个 skill" --top-k 4
```

3. 工作完成后追加事实：

```bash
uv run python scripts/append_entry.py \
  --topic "github_skill_research" \
  --summary "官方样本在 PR、评论处理、CI 修复上更成熟，repo create workflow 需要自己补" \
  --evidence "调研 openai/skills 和社区索引" \
  --type fact
```

4. 用户纠正时即时记录：

```bash
uv run python scripts/append_feedback.py \
  --topic "prefer_minimal_closed_loop" \
  --summary "如果只是偶发创建 GitHub 仓库，优先直接执行 gh workflow，而不是先抽象 skill" \
  --evidence "用户明确偏好直接 gh 路径" \
  --effect corrected \
  --target-query "GitHub repo create skill"
```

5. 收尾时做 save-work checkpoint：

```bash
uv run python scripts/save_work_checkpoint.py \
  --topic "save_work_github_repo_flow" \
  --summary "完成 gh 安装、认证验证和仓库创建闭环" \
  --evidence "本轮工作完成"
```

## 推荐使用顺序

1. 会话开始先跑 `session_bootstrap.py`
2. 第一次实质任务前跑一次 recall
3. 工作中用 `append_entry.py` 记录 durable facts
4. 用户纠正时用 `append_feedback.py` 立刻落反馈
5. 一段工作结束后用 `save_work_checkpoint.py` 收尾

## 关键约束

- `MEMORY.md` 不会自动写入
- daily logs 是 append-only
- `working/*` 是派生层，不是事实源
- `topic` 和 `summary` 应该为未来 recall 优化
- 过长 payload 会被 soft/hard block，避免写入低信号内容
