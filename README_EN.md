<div align="center">

# ⚡ Skill Gateway (点将台)

**The Local Skill Intelligence & DAG Orchestration Engine for AI Coding Agents**  
*Orchestrate, Discover, Audit, and Govern Local Agent Skills with Zero External Dependencies*

<p align="center">
  <a href="https://github.com/qi-xiao-bai/skill-gateway/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/qi-xiao-bai/skill-gateway/ci.yml?branch=main&style=flat-square&logo=github&label=CI%20Build" alt="CI Status">
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square" alt="License">
  </a>
  <a href="#">
    <img src="https://img.shields.io/badge/Dependencies-Zero%20(Pure%20Stdlib)-brightgreen.svg?style=flat-square" alt="Dependencies">
  </a>
  <a href="#">
    <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg?style=flat-square" alt="Platforms">
  </a>
  <a href="#">
    <img src="https://img.shields.io/badge/Dashboard-100%25%20Offline%20(D3.js)-purple.svg?style=flat-square" alt="Offline Ready">
  </a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> · [English](README_EN.md)
</p>

</div>

---

<div align="center">
  <img src=".github/assets/dashboard.png" alt="Skill Gateway Interactive Dashboard" width="920" style="border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);">
  <p><sub>Figure: 100% offline single-file D3.js force-directed knowledge graph with real-time multi-dimensional filtering and platform drilling.</sub></p>
</div>

---

## 💡 Why Skill Gateway?

As AI coding agents (**Claude Code, Antigravity, Cursor, WorkBuddy, Dify**, etc.) scale to dozens or hundreds of installed skills and MCP servers, developers inevitably hit three major engineering roadblocks:

1. **Context Window Exhaustion**: Injecting raw `SKILL.md` documents directly into the system prompt wastes hundreds of thousands of tokens, driving up costs and Time-to-First-Token (TTFT) latency.
2. **Orchestration Void**: Complex tasks (e.g., refactoring + testing + code review + delivery) cannot be addressed by an isolated skill. There is no automated dependency chaining or context handoff.
3. **Asset Black Box**: Teams cannot inspect their installed skill inventory, detect broken descriptions or circular dependencies, or measure actual usage.

### The Skill Gateway Solution:
- **97.7% Token Compression (44×)**: Layered indexing (L0/L1/L2) with compact dense formatting and hard quota enforcement.
- **Topological DAG Chaining**: Kahn's topological sorting dynamically decomposes composite tasks into phased execution pipelines with automated context handoff.
- **100% Self-Contained Offline Dashboard**: Single-file D3.js interactive graph visualization with zero external CDN requests.

---

## ✨ Key Features

- 🕸️ **DAG Pipeline Orchestration**: Automatically chains multiple skills into structured workflows based on `depends_on`, `contains`, and `similar` relationship edges.
- ⚡ **Precision Scoring & Proxy Routing**: Bilingual CJK 2/3-gram inverted indexing with IDF term weighting for sub-second skill discovery.
- 📊 **Offline Interactive Graph Dashboard**: Single-file HTML viewer for real-time visualization of all skills, MCP tools, and connectors.
- 💰 **Token Economy Audit & Ledger**: Measures live token savings, logs query hit rates, and turns unhandled queries into capability gap reports.
- 🛡️ **Zero Dependencies & High Robustness**: 100% standard library Python implementation with cross-platform CI matrix coverage (Ubuntu & Windows).

---

## ⚡ Quick Start

### 1. Mount as an Agent Meta-Skill (Recommended)

Clone directly into your Agent's skill directory:

```bash
git clone https://github.com/qi-xiao-bai/skill-gateway.git ~/.claude/skills/skill-gateway
```

Once installed, your agent automatically leverages the skill gateway contract to orchestrate and route tasks!

### 2. Standalone CLI & Interactive Dashboard

```bash
# Clone repository
git clone https://github.com/qi-xiao-bai/skill-gateway.git
cd skill-gateway

# Build local skill index and knowledge graph
python scripts/build_index.py index

# Launch the interactive local D3 dashboard
python scripts/build_index.py dashboard --serve --open
```

Open `http://127.0.0.1:8123/skill-dashboard.html` in your browser to explore your local skill network!

---

## 💻 Essential Commands

| Category | Command | Description |
| :--- | :--- | :--- |
| **Orchestration** | `python scripts/build_index.py pipeline "<task>"` | Generates a multi-skill DAG pipeline with phase matrix |
| | `python scripts/build_index.py chat "<query>"` | Recommends best-fit skills with 1-Hop topological neighbors |
| | `python scripts/build_index.py route "<phrase>"` | Maps natural language phrases to CLI commands |
| **Search** | `python scripts/build_index.py search <term> [--top N]` | High-precision IDF-ranked skill search |
| | `python scripts/build_index.py detail <skill_name>` | Views full `SKILL.md` documentation |
| | `python scripts/build_index.py related <skill_name>` | Inspects topological graph edges |
| **Dashboard** | `python scripts/build_index.py dashboard --serve --open`| Serves interactive D3 force-directed graph |
| **Audit** | `python scripts/build_index.py audit` | Token economy analysis and compression audit |
| | `python scripts/build_index.py doctor` | Health check for broken descriptions & orphan nodes |
| **Maintenance**| `python scripts/build_index.py update` | Millisecond-level incremental sync |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
