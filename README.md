# useful_skills

Public utility skills and supporting scripts.

This repository starts with one opinionated skill:

- `memory-runtime`: a local, file-based memory runtime for agent sessions

## Included Skills

### memory-runtime

`memory-runtime` provides a practical workflow for maintaining local memory with plain files:

- bootstrap minimal memory context at session start
- recall relevant facts and feedback before the first substantial answer
- append normalized daily work logs
- capture user corrections immediately
- checkpoint work and surface `MEMORY.md` promotion candidates

The skill lives in [memory-runtime](./memory-runtime). The original agent-facing contract is in `memory-runtime/SKILL.md`, and the human-readable usage guide is in `memory-runtime/README.md`.

## Repo Layout

```text
useful_skills/
├── README.md
├── .gitignore
└── memory-runtime/
    ├── README.md
    ├── SKILL.md
    └── scripts/
```

## Notes

- The current scripts are designed for local use and default to `~/.memories`.
- You can override the storage root with `MEMORY_ROOT=/your/path`.
- Semantic recall defaults to an Ollama embedding backend, but you can switch to a built-in hash embedding backend for a zero-service setup.
