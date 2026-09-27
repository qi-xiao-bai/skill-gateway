<div align="center">

# skill-indexer

**Local skill intelligence layer — index, audit, relate and orchestrate
the skills your coding agent already has.**

*Your agent installed 1,000 skills. Now what?*

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-81%20passed-brightgreen.svg)](.github/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#)
[![Deps](https://img.shields.io/badge/dependencies-zero-success.svg)](#zero-dependencies)

**English** · [简体中文](README_ZH.md)

</div>

---

Registries and CLIs (`npx skills` and friends) solved how skills are **found and installed**.
Nobody solved what happens **after** — a library of hundreds of SKILL.md files silently
bloats every session's context, hides its own dependencies, and offers no way to know
what any of it costs.

`skill-indexer` is a zero-dependency Python CLI that runs **next to** any SKILL.md tree
(Claude Code, Codex, Cursor, Gemini CLI, and ~70 other hosts) and gives it:

| Capability | One line |
|---|---|
| 📒 **Token Ledger** | Quantify exactly what your skill library costs vs. its compressed index |
| 🕸️ **Knowledge Graph** | `depends_on` / `contains` / `overlap` / `similar` edges, built and persisted |
| 🔗 **Topology Orchestration** | Chains skills into pipelines by graph layers — no hardcoded templates |
| 🔍 **CJK-first Retrieval** | Chinese 2/3-gram + English IDF search that actually works on 中文描述 |
| 🩺 **Doctor** | Lints the library: bad descriptions, duplicates, oversized skills, broken paths |
| 📊 **Dashboard** | Local, offline D3 force-graph of your entire library |

Measured on a real 1,007-skill library (Windows, Python 3.13):

```
$ python scripts/build_index.py audit --top 3

- 全文合计(所有 SKILL.md)  :  7,330,470 字符 ≈ 1,832,618 tokens
- L1 索引(dense，含头块)   :    167,772 字符 ≈      41,943 tokens
- 压缩率 L1(仅技能行)/全文: 2.3%  ⇒ 省 97.7% 上下文
- 启动字符量约减少 44×
```

---

## Quick start

```bash
# 1. unzip / clone anywhere, then:
python scripts/build_index.py list          # scans everything, builds the index automatically

# 2. search it
python scripts/build_index.py search "海报设计" --top 3

# 3. orchestrate a composite task from the graph
python scripts/build_index.py pipeline "开发一个用户认证系统并做代码审查"
```

That's the whole setup. No config file, no server, no API key. The first read command
builds the index; every later command updates it incrementally.

## Why "intelligence layer"?

```
   find ──▶ add ──▶ use ──▶ check          npx skills (distribution: done ✔)
   ────────────────────────────────
   index ──▶ audit ──▶ relate ──▶ orchestrate      skill-indexer (the gap: this repo)
```

`skill-indexer` is **not** another registry. It reads whatever SKILL.md tree your host
already has (including `npx skills add`-installed libraries) and adds the intelligence
layer the ecosystem is missing: cost accounting, relationship edges, and dependency-aware
orchestration.

## The knowledge graph & topology orchestration

Relationship edges (`depends_on`, `contains`, `family`, `overlap`, `similar`) are
**computed once, persisted in the index**, and consumed everywhere — `related`, `stats`,
`dashboard`, `diff` (change impact analysis), and the pipeline orchestrator.

The orchestrator has **no hardcoded lifecycle templates**. Stages are derived from your
own graph:

```text
$ python scripts/build_index.py pipeline "帮我写一首关于秋天的诗" --json
{
  "mode": "topological",
  "stages": [
    { "title": "阶段 1：pdf、skill（并行协同）",   ... },
    { "title": "阶段 2：khazix-writer",          ... }   ← the real writing skill,
    ...                                                    ordered by ITS dependencies
  ]
}
```

- Task retrieval picks the seed skills (evidence terms are printed for auditability);
- `depends_on` edges induce the subgraph; Kahn layering turns it into stages;
- **No dependency edges in the graph? It says so** — candidates are listed as
  parallel, explicitly marked "adoption is up to the executing agent", instead of
  fabricating a fake pipeline. An arbitrary task like *write a poem* never gets
  a plan→develop→clean→review template bolted onto it.

## Retrieval that speaks Chinese

Descriptions are tokenized with CJK 2/3-grams + English words, scored with capped IDF,
and de-duplicated via maximal-term filtering (a phrase like 数据索引 can't inflate its
score with overlapping grams). Query terms that hit are printed next to every result —
every ranking is explainable.

## Doctor: CI for your skill library

```text
$ python scripts/build_index.py doctor

# 技能体检（doctor）：1007 项
硬问题 0 条（坏/空描述、路径缺失、重名）｜软建议 1028 条（缺触发词、超大技能）
```

CJK-aware thresholds (a 14-character Chinese description is not "too short"),
cross-type duplicate detection, stub-file detection for metadata-only mirrors,
and an optional `--fix` that repairs graph defects.

## Design principles

- **Zero dependencies.** Pure Python standard library. unzip → run. (81 tests,
  CI on Ubuntu + Windows, Python 3.10/3.13.)
- **Honest modes, no faking.** A metadata-only index says so and refuses to print
  compression numbers; a missing full-text aborts `--full` with exit code 2 and
  instructions — it never pretends placeholders are the real thing.
- **Machine-readable contract.** Exit codes `0` ok / `2` expected-miss (retry with
  different words) / `1` error. `--json` outputs stay pure JSON (progress goes to
  stderr). Bare invocation prints help instead of triggering a full scan.
- **Local by default.** The dashboard renders offline; no telemetry, no network calls,
  no third-party requests — descriptions never leave your machine.
- **Atomic everything.** Indexes, terms caches, profile writes, and state files all
  land via tmp+rename; a killed process can't corrupt the library's index.

## Index format (built for LLM context budgets)

The machine index (`skill-index.json`) keeps full-fidelity descriptions plus graph
edges; the model-facing index (`skill-index.llms.txt`) is a dense, one-record-per-line
text with hard quotas (description ≤400 chars, triggers ≤8):

```text
#SKILL-INDEX: 1
#Format: skill-index/dense-v1   (plain text; one record per line)
#Count: 1000 skill + 7 mcp = 1007
<name>[sR]: <what/when, one line> | 触发: <t1>、<t2>
```

`bundle` additionally emits L0 (names only) / L1 (dense) / L2 (name→path map) shards
plus `boot.md` for hosts that want progressive disclosure. Format details:
[references/index-format.md](references/index-format.md).

## CLI map

| Command | What it does |
|---|---|
| `index` / `update` | Full rebuild / incremental (mtime-based, reuses unchanged entries) |
| `search` / `chat` / `route` | Ranked retrieval / graph Q&A / NL→command routing |
| `pipeline` / `chain` | Topology-driven multi-skill orchestration |
| `detail` / `explain` / `onboard` / `diff` | Per-skill full text / advice / onboarding / impact analysis |
| `related` / `stats` / `doctor` | Graph neighbors / coverage report / library linting |
| `audit` / `bundle` / `dashboard` | Token ledger / L0-L2 shards / offline D3 graph |
| `content-find` | Full-text chunk search with file:line hits |
| `roots` / `import` / `add` | Root probe (why is my index empty?) / import lists / add one skill |
| `profile` / `memory` | Pins, exclusions, global directives, usage-based auto-promotion |

Exit code contract: `0` success · `2` expected miss (not found / no hits — safe to
retry with other keywords) · `1` error.

## Docs

- [简体中文完整手册 / Full Chinese manual](README_ZH.md)
- [Command reference](references/commands.md) · [Index format](references/index-format.md) · [Governance contract](references/governance.md)
- [Development notes](docs/)

## Attribution & compliance

Original implementation, inspired by (all open-source/MIT, ideas only, no code copied):

- **AOCI (aoci-spec/aoci-code)** — the most direct inspiration for the index format:
  plain-text cognitive indexes, fixed field order, hard character quotas, and the
  "never copy full source into the index" rule.
- **Egonex-AI/Understand-Anything** — stable node IDs + persisted edges, ignore rules
  as a file (`.skillignore`), and a coverage manifest answering "what does this index
  actually cover".
- **rstkit/meta_skill** (Rust) — metadata extraction + FTS-style retrieval for skills.
- **Livus-AI/Skills-MCP**, **ohboyftw/meta-mcp**, **c2s/agent-skills-hub**,
  **mcp-tool-shop-org/mcp-tool-registry** — skill/MCP discovery patterns.

Ideas and architecture are not copyrightable; this repository is an independent
implementation with attribution kept intact.

## License

[MIT](LICENSE)
