# useful_skills

## English

Public utility skills and supporting scripts.

This repository currently starts with one opinionated skill:

- `memory-runtime`: a local, file-based memory runtime for agent sessions

### Included Skill

#### memory-runtime

`memory-runtime` provides a practical workflow for maintaining local memory with plain files:

- bootstrap minimal memory context at session start
- recall relevant facts and feedback before the first substantial answer
- append normalized daily work logs
- capture user corrections immediately
- checkpoint work and surface `MEMORY.md` promotion candidates

The skill lives in [memory-runtime](./memory-runtime). The agent-facing contract is in `memory-runtime/SKILL.md`, and the human-readable usage guide is in `memory-runtime/README.md`.

### Repo Layout

```text
useful_skills/
├── README.md
├── LICENSE
├── .gitignore
└── memory-runtime/
    ├── README.md
    ├── SKILL.md
    └── scripts/
```

### Notes

- The current scripts are designed for local use and default to `~/.memories`.
- You can override the storage root with `MEMORY_ROOT=/your/path`.
- Semantic recall defaults to an Ollama embedding backend, but you can switch to a built-in hash embedding backend for a zero-service setup.

## 中文

公开放一些我自己常用、也适合对外分享的 utility skills 和配套脚本。

当前仓库先收一个偏工程化、偏本地化的 skill：

- `memory-runtime`：面向 agent session 的本地文件记忆运行时

### 当前包含

#### memory-runtime

`memory-runtime` 主要解决本地记忆闭环：

- 会话开始时 bootstrap 最小上下文
- 第一次真实任务前做一次 recall
- 用标准化格式追加每日工作记忆
- 在用户纠正时即时落反馈
- 在 save-work 阶段生成 `MEMORY.md` 候选，而不是自动改写长期记忆

对应目录在 [memory-runtime](./memory-runtime)。其中：

- `memory-runtime/SKILL.md` 是给 agent 的执行契约
- `memory-runtime/README.md` 是给人读的使用说明

### 说明

- 这套脚本默认写入 `~/.memories`
- 如果你想隔离目录，可以通过 `MEMORY_ROOT` 覆盖根路径
- 如果不想依赖 Ollama，可以把 embedding backend 切到 `simple`
