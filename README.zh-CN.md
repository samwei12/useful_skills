# useful_skills

[English](./README.md) | [简体中文](./README.zh-CN.md)

公开放一些我自己常用、也适合对外分享的 utility skills 和配套脚本。

当前仓库先收一个偏工程化、偏本地化的 skill：

- `memory-runtime`：面向 agent session 的本地文件记忆运行时

## 当前包含

### memory-runtime

`memory-runtime` 主要解决本地记忆闭环：

- 会话开始时 bootstrap 最小上下文
- 第一次真实任务前做一次 recall
- 用标准化格式追加每日工作记忆
- 在用户纠正时即时落反馈
- 在 save-work 阶段生成 `MEMORY.md` 候选，而不是自动改写长期记忆

对应目录在 [memory-runtime](./memory-runtime)。其中：

- `memory-runtime/SKILL.md` 是给 agent 的执行契约
- `memory-runtime/README.md` 是给人读的使用说明

## 仓库结构

```text
useful_skills/
├── README.md
├── README.zh-CN.md
├── LICENSE
├── .gitignore
└── memory-runtime/
    ├── README.md
    ├── README.zh-CN.md
    ├── SKILL.md
    └── scripts/
```

## 说明

- 这套脚本默认写入 `~/.memories`
- 如果你想隔离目录，可以通过 `MEMORY_ROOT` 覆盖根路径
- 如果不想依赖 Ollama，可以把 embedding backend 切到 `simple`
