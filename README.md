# useful_skills

[English](./README.md) | [简体中文](./README.zh-CN.md)

Public utility skills and supporting scripts.

This repository currently starts with one opinionated skill:

- `memory-runtime`: a local, file-based memory runtime for agent sessions

## Included Skill

### memory-runtime

`memory-runtime` provides a practical workflow for maintaining local memory with plain files:

- bootstrap minimal memory context at session start
- recall relevant facts and feedback before the first substantial answer
- append normalized daily work logs
- capture user corrections immediately
- checkpoint work and surface `MEMORY.md` promotion candidates

The skill lives in [memory-runtime](./memory-runtime). The agent-facing contract is in `memory-runtime/SKILL.md`, and the human-readable usage guide is in `memory-runtime/README.md`.

## Templates

Reusable bilingual README skeletons live under [templates](./templates):

- [templates/repo-readme/README.md.template](./templates/repo-readme/README.md.template)
- [templates/repo-readme/README.zh-CN.md.template](./templates/repo-readme/README.zh-CN.md.template)
- [templates/skill-readme/README.md.template](./templates/skill-readme/README.md.template)
- [templates/skill-readme/README.zh-CN.md.template](./templates/skill-readme/README.zh-CN.md.template)

## Repo Layout

```text
useful_skills/
├── README.md
├── README.zh-CN.md
├── LICENSE
├── .gitignore
├── templates/
└── memory-runtime/
```

## Notes

- The current scripts are designed for local use and default to `~/.memories`.
- You can override the storage root with `MEMORY_ROOT=/your/path`.
- Semantic recall defaults to an Ollama embedding backend, but you can switch to a built-in hash embedding backend for a zero-service setup.
