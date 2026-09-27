<div align="center">

# skill-indexer

**本地技能智能层 —— 为 Agent 已装好的技能库提供索引、账本、图谱与编排。**

*你的 Agent 装了 1000 个技能。然后呢？*

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-81%20passed-brightgreen.svg)](.github/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#)
[![Deps](https://img.shields.io/badge/dependencies-zero-success.svg)](#设计原则)

[English](README.md) · **简体中文**

</div>

---

注册表与包管理器（`npx skills` 们）解决了技能**怎么被找到和安装**；没人解决**装完之后**的事——几百个 SKILL.md 组成的技能库会悄悄撑爆每次会话的上下文，藏起自己的依赖关系，也说不清这一切的 token 成本。

`skill-indexer` 是一个零依赖的 Python CLI，运行在任意 SKILL.md 技能树旁边（Claude Code、Codex、Cursor、Gemini CLI 等约 70 个宿主），为它补上：

| 能力 | 一句话 |
|---|---|
| 📒 **Token 账本** | 量化技能库全文成本 vs 压缩索引，精确到字符 |
| 🕸️ **知识图谱** | `depends_on` / `contains` / `overlap` / `similar` 关系边，一次构建、处处消费 |
| 🔗 **拓扑编排** | 按图谱分层把技能串成流水线——**零硬编码模板** |
| 🔍 **中文优先检索** | 中文 2/3-gram + 英文 IDF，对中文描述真正可检索 |
| 🩺 **技能体检** | 坏描述/重名/超大技能/断链路全库巡检，可 `--fix` 自动修复 |
| 📊 **可视化图谱** | 纯本地离线的 D3 力导向图，整库拓扑一眼看清 |

真实 1007 技能库实测（Windows / Python 3.13）：

```
$ python scripts/build_index.py audit --top 3

- 全文合计(所有 SKILL.md)  :  7,330,470 字符 ≈ 1,832,618 tokens
- L1 索引(dense，含头块)   :    167,772 字符 ≈      41,943 tokens
- 压缩率 L1(仅技能行)/全文: 2.3%  ⇒ 省 97.7% 上下文
- 启动字符量约减少 44×
```

---

## 快速开始

### 正确的打开方式：装成技能，然后只说话

**你不需要学任何命令。** 把本目录放进宿主的技能目录（如 `~/.claude/skills/skill-indexer`，或用平台的技能管理器安装）——之后对 Agent 说人话即可，宿主会按触发词自动加载本技能：

> **「我有一堆技能，不知道这个任务该用哪个」**
> → Agent 自动执行图谱问答，返回带命中证据的候选技能 + 关联邻居

> **「把这个需求编排成流水线」**
> → Agent 自动生成拓扑编排（或如实标注的并行候选），然后逐阶段执行

> **「现在技能库的上下文成本是多少？」**
> → Agent 自动跑 Token 账本，给出压缩率与启动体积

下方列出的子命令就是 **Agent 在幕后实际调用的命令**——每一步透明可审计（退出码、命中证据、留痕脚注），你随时也可以亲手跑同样的命令，但那不是必须的。

### 也可以当独立 CLI 用（可选）

```bash
python scripts/build_index.py list      # 全库扫描，索引自动建立
python scripts/build_index.py search "海报设计" --top 3
python scripts/build_index.py pipeline "开发一个用户认证系统并做代码审查"
```

无需配置文件、无需服务、无需 API key——第一条读命令自动建索引，之后全部增量更新。

## 为什么叫"智能层"？

```
   find ──▶ add ──▶ use ──▶ check          npx skills（分发侧：已解决 ✔）
   ────────────────────────────────
   index ──▶ audit ──▶ relate ──▶ orchestrate      skill-indexer（空白带：本仓库）
```

`skill-indexer` **不是又一个注册表**。它读取宿主已有的 SKILL.md 树（包括 `npx skills add` 装出来的库），补上生态缺失的智能层：成本账本、关系边、依赖感知的编排。

## 图谱与拓扑编排

关系边**只算一次、固化进索引**，`related` / `stats` / `dashboard` / `diff`（变更影响分析）与编排器全部消费同一份图。

编排器**没有任何硬编码生命周期模板**，阶段完全由你自己的图谱推导：

- 任务检索选出种子技能（命中证据词随结果打印，可复核）；
- `depends_on` 边诱导子图，Kahn 分层成阶段；
- **图里没有依赖边？它就直说**——候选以"并行候选"如实列出（标注"采纳与否由执行 Agent 甄别"），绝不伪造时序。任意任务（比如"写一首诗"）永远不会被硬套 plan→develop→clean→review 模板。

## 中文检索

CJK 2/3-gram + 英文词分词、IDF 封顶加权、maximal 共现去重（"数据索引"不可能靠重叠 gram 刷分）。每条检索结果旁打印命中词——**每个排名都可解释**。

## Doctor：给技能库做 CI

CJK 感知阈值（14 个汉字的具体描述不算"太短"）、跨类型重名检测、元数据镜像占位识别，附 `--fix` 图谱自修复。实测 1007 项技能库：**硬问题 0 条**。

## 设计原则

- **零依赖**：纯 Python 标准库，解压即用。81 个测试，Ubuntu + Windows 双平台 CI（Python 3.10/3.13）。
- **诚实档位，绝不冒充**：元数据索引如实标注并拒绝给出压缩率；`--full` 缺全文即中止（rc=2）并给出取全文步骤，绝不拿占位文件冒充。
- **机器可读契约**：退出码 `0` 成功 / `2` 预期内未命中（可换词重试）/ `1` 异常；`--json` 输出保持纯 JSON（提示走 stderr）；裸跑打印帮助而不是隐式全盘扫描。
- **默认全本地**：dashboard 离线渲染；无遥测、无网络调用——技能描述永远不出你的机器。
- **原子写盘**：索引、terms 缓存、画像、状态文件全部 tmp+rename 落盘，进程被杀不损坏索引。

## 索引格式（为 LLM 上下文预算而生）

机器读的 `skill-index.json` 描述保真并携带图谱边；模型读的 `skill-index.llms.txt` 是一行一条、带硬配额（描述 ≤400 字符、触发词 ≤8 个）的 dense 索引：

```text
#SKILL-INDEX: 1
#Format: skill-index/dense-v1   (plain text; one record per line)
#Count: 1000 skill + 7 mcp = 1007
<name>[sR]: <做什么/何时用，一行写完> | 触发: <t1>、<t2>
```

`bundle` 额外产出 L0（仅名字）/ L1（dense）/ L2（名字→路径）三层分片与 `boot.md`，供需要渐进式披露的宿主使用。格式细节见 [references/index-format.md](references/index-format.md)。

## 命令一览（Agent 幕后实际调用的命令；人类也可手动执行）

| 命令 | 作用 |
|---|---|
| `index` / `update` | 全量重建 / 增量（按 mtime，未变项直接复用） |
| `search` / `chat` / `route` | 排序检索 / 图谱问答 / 话术路由 |
| `pipeline` / `chain` | 图谱拓扑驱动的多技能编排 |
| `detail` / `explain` / `onboard` / `diff` | 单技能全文 / 使用建议 / 上手指南 / 变更影响 |
| `related` / `stats` / `doctor` | 图谱邻居 / 覆盖口径 / 全库体检 |
| `audit` / `bundle` / `dashboard` | Token 账本 / L0-L2 分片 / 离线 D3 图谱 |
| `content-find` | 正文分块检索（文件+行号） |
| `roots` / `import` / `add` | 扫描根探针 / 导入清单 / 追加单技能 |
| `profile` / `memory` | 置顶/排除/全局指令 / 真实使用驱动的自动晋升 |

## Roadmap

- **MCP server 封装** —— 把 CLI 能力暴露为原生 MCP 工具，宿主无需 shell 即可调用（CLI 保留为兜底与审计留痕）；
- **`npx skills` 生态桥接** —— 开箱读取注册表安装的技能库，向技能目录站分发；
- **语义检索层** —— 在 CJK/IDF 核心之上叠加可选的本地向量检索。

## 文档

- [English](README.md) · 简体中文（本文件）
- [命令手册](references/commands.md) · [索引格式](references/index-format.md) · [治理契约](references/governance.md)
- [开发过程文档](docs/)

## 设计来源与致谢（Attribution）

本技能为自建实现，思路参考以下公开项目（均开源/MIT），非照搬源码：

- **AOCI（aoci-spec/aoci-code）** — 索引格式最直接的借鉴对象：纯文本认知索引、定长字段顺序、硬性字符配额、"禁止把全文抄进索引"。
- **Egonex-AI/Understand-Anything** — 稳定节点 ID + 关系边固化、忽略规则写成文件（`.skillignore`）、覆盖口径（meta）回答"这份索引到底覆盖了什么"。
- **rstkit/meta_skill**（Rust）— 元数据抽取 + FTS 检索用于技能。
- **Livus-AI/Skills-MCP**、**ohboyftw/meta-mcp**、**c2s/agent-skills-hub**、**mcp-tool-shop-org/mcp-tool-registry** — 技能/MCP 发现模式。

思路与架构不受版权保护；本仓库为独立实现，并保留上述致谢。

## 许可证

[MIT](LICENSE)

---

以下为原版完整说明（技术细节手册）。

# Skill Indexer（全局技能调度、代理直达与图谱串联编排网关）

一个**服务于全部技能调用的前置网关与元技能（meta-skill）**：在数字员工与 Agent 平台中充当统一的**智能调度、代理直达与多技能编排层**。面对上百个孤立技能时，用户无需记忆复杂技能名，直接呼叫本技能即可实现：

- **能力一：透明代理直达（Transparent Proxy）**：用户直接输入任何业务需求，本技能通过索引和倒排检索**秒级匹配最佳技能并代理执行**，效果等同于精准直调目标技能，彻底消除技能记忆与选型成本。
- **能力二：多技能智能串联编排（Multi-Skill Pipeline Chaining）**：针对复合型复杂需求，基于**知识图谱拓扑边（`depends_on` 依赖、`contains` 包含、`similar` 相似）**做 Kahn 拓扑分层，自动把相关技能编排为端到端**流水线 DAG**。阶段数量与顺序完全由图谱边决定——**没有硬编码的生命周期模板**；图谱里查不出依赖关系时，诚实输出"并行候选"（列候选+检索证据，由执行 Agent 甄别采纳），绝不给任意任务硬套研发流水线。
- **按需加载（渐进式披露）**：平台启动只加载轻量索引（L1），真正要用某技能时才加载全文（L2），避免上下文爆炸。
- **真实提速留痕**：直达调用消除人工试错与多跳选型延迟，流水线编排消除上下文断层。

## 目录结构
```
skill-indexer/
├── SKILL.md                      # 技能定义与双模式调度契约
├── README.md
├── skill_profile.json            # 用户重要指令与技能画像（固定根/置顶技能/全局必遵指令）
├── .skillexclude                 # 技能排除规则（黑名单通配）
├── .skillignore                  # 扫描忽略规则（gitignore 语法）
├── skill_indexer.config.json     # 可选：追加扫描根与边权重
├── inputs/                       # 外部输入（平台清单等）
├── output/                       # 运行时生成物统一隔离存放处（不入源码包）
├── scripts/                      # 核心脚本
│   ├── build_index.py            #    CLI 入口（命令注册表 COMMANDS 驱动 argparse/help/dispatch）
│   ├── pipeline.py               #    多技能图谱串联编排引擎（depends_on 拓扑分层 / 并行候选 / 接力）
│   ├── paths.py                  #    路径/配置/画像/扫描根/忽略规则/产物目录
│   ├── scanner.py                #    扫描技能 + MCP（跨类型同名去重）
│   ├── skillmd.py                #    SKILL.md 解析（BOM/CRLF/块标量健壮）
│   ├── index_store.py            #    索引读写（原子写/版本校验）+ ID/关系边/覆盖口径 + L0/L1
│   ├── importer.py               #    导入外部技能清单（平台不落盘时用）
│   ├── platform_bridge.py        #    平台桥接：清单→镜像目录→交给 indexer
│   ├── retrieval.py              #    排序检索（maximal 去重/词边界建边/归一化置顶提权）
│   ├── reports.py                #    audit / stats / diff / onboard（dashboard 模板已拆分）
│   ├── dashboard_template.html   #    dashboard 前端资产（纯本地，无外发请求）
│   ├── route_rules.json          #    意图路由规则表（数据文件，可定制；route.py 加载）
│   ├── bundle.py                 #    L0/L1/L2 + 关系图 + boot.md
│   ├── doctor.py                 #    技能体检（CJK 感知描述阈值）
│   ├── export.py                 #    多格式导出
│   ├── content_index.py          #    代码/正文分块倒排（terms 内嵌单文件 / 投喂 chunk 带真实源路径）
│   ├── proactive.py              #    Self-Improving：只计"真实使用"频次，状态文件有增长上限
│   └── route.py                  #    意图路由（话术 → 命令，规则外置 route_rules.json）
├── tests/                        # 测试文件
├── docs/                         # 开发过程文档
└── references/
    ├── commands.md               #    子命令手册
    ├── index-format.md           #    索引格式与产物
    └── governance.md             #    治理机制与详细契约
```

## 子命令（能力清单 —— 这些是 Agent 幕后执行的命令；人类可手动执行，均在技能根目录）
| 命令 | 作用 |
|------|------|
| `python scripts/build_index.py profile` | **用户画像与指令管理**：查看/配置固定根、置顶技能、排除规则与全局必遵指令 |
| `python scripts/build_index.py pipeline "<任务>"` | **多技能串联编排**：检索种子 → depends_on 拓扑分层 → 生成阶段/矩阵/执行契约（无预设模板；无依赖边时输出并行候选） |
| `python scripts/build_index.py chain "<任务>"` | `pipeline` 的真别名（argparse alias，参数完全一致） |
| `python scripts/build_index.py chat "<问题>" [--exclude <技能>]` | **智能图谱问答**：返回推荐技能 + 1-hop 关联 + 正文内容片段（支持排除过滤与指令前置） |
| `python scripts/build_index.py route "<用户话术>"` | **意图路由**：把自然语言话术映射到最合适的命令 |
| `python scripts/build_index.py search 关键词 [--top N] [--exclude <技能>]` | **排序检索**：置顶提权 + 配额硬顶（Top 20）+ 动态长尾剪枝 + 0 命中定向增量自愈 |
| `python scripts/build_index.py list [--all] [--excluded]` | 浏览技能与 MCP 工具清单（`--excluded` 专查已排除技能） |
| `python scripts/build_index.py update [--targeted <关键词>]` | **增量索引**：只处理新增/变更/移除，支持定向查询增量（日常用） |
| `python scripts/build_index.py dashboard [--open] [--download] [--serve]` | 生成技能图谱可视化 HTML（D3.js 力导向图，支持已排除节点与显隐开关）。`--open`/`--download` 为显式开关（默认无副作用）；`--serve` 仅绑定 127.0.0.1；面板**纯本地展示，无任何在线翻译/外发请求** |
| `python scripts/build_index.py clean` | **纯净复原**：清理全部运行时产物与缓存，保留纯净源数据状态（发布/打包前用） |
| `python scripts/build_index.py explain <技能名>` | 某技能的**详细使用建议** |
| `python scripts/build_index.py detail <技能名>` | 查看某技能完整 SKILL.md（默认不进上下文） |
| `python scripts/build_index.py related <技能名>` | 查技能之间的关系边（`depends_on` / `contains` / `overlap` / `similar`） |
| `python scripts/build_index.py content-find <query>` | **正文检索**：查代码定义与规则所在文件及行号 |
| `python scripts/build_index.py onboard <技能名>` | 生成技能上手指南（结构化入门） |
| `python scripts/build_index.py diff <技能名>` | 技能变更影响分析（直接下游/二级依赖） |
| `python scripts/build_index.py audit [--top N]` | **Token 账本**：量化上下文压缩率 |
| `python scripts/build_index.py bundle` | 生成 **L0/L1/L2 + 关系图 + `boot.md`**（可上传网页） |
| `python scripts/build_index.py doctor [--graph]` | **技能体检**与图谱质量审查 |
| `python scripts/build_index.py export <格式>` | 多格式导出（`minjson` 含关系边） |
| `python scripts/build_index.py index [--broad]` | **全量重建**索引（首次使用或强制重扫） |
| `python scripts/build_index.py roots` | **扫描根探针**：诊断为什么是 0 项 |
| `python scripts/build_index.py stats` / `catalog` / `help` | **覆盖口径** / Markdown 总览 / 命令一览 |

## 规模化治理与防护机制
针对平台拥有上百乃至数千技能时的性能与安全诉求，内置六项治理机制：
1. **用户重要指令与技能画像 (skill_profile.json)**：支持固定根锁定、置顶常用技能加权提权，并在任何检索/问答/流水线首部强制注入用户全局指令。
2. **配额硬顶截断 (Hard Ceiling)**：全局硬上限 `MAX_RETRIEVAL_TOP = 20`，严防大入参导致大模型上下文溢出。
3. **动态分数剪枝 (Dynamic Pruning)**：底线分 `MIN_RELEVANCE_SCORE = 0.35`，并截断低于 Top 1 分数 25% 的长尾低质技能，强力降噪。
4. **未命中定向自愈 (Targeted Update on Miss)**：检索 0 命中时探测磁盘，若有新装/修改技能，针对查询关键词定向增量编译，避免耗时全量重扫。
5. **多级黑名单与图谱可视化 (.skillexclude / Dashboard)**：支持通配符排除名单；在 D3 拓扑图中以红色虚线/暗色区分已排除节点，并支持页面一键显隐切换。
6. **主动自省进化与无感智能更新 (Self-Improving + Proactive Agent)**：引入后置执行钩子（Post-Execution Hook），每次操作后基于根目录物理变动指纹（`os.scandir` 毫秒级探测）与心跳周期自省决策增量对齐。**计频口径只认"真实使用"**：用户显式 `detail` 查看全文 / 明确采纳才计入频次——出现在检索结果里不算（否则"被搜到 3 次"就自动置顶，置顶提权又让它更容易被搜到，形成与用户意图无关的正反馈）；高频技能达到阈值（默认 3 次）自动晋升至置顶画像（`pinned_skills`），状态文件与纠偏日志均有增长上限。
7. **退出码约定（给调用方/Agent 区分三种结局）**：`0` 成功；`2` 预期内的未命中/未找到（可换词重试，如 detail 找不到技能、检索 0 命中）；`1` 异常错误（顶层兜底打印 traceback）。裸跑 `build_index.py`（无子命令）打印帮助退出，不会隐式触发全量扫描。


## 索引格式（给 AI 读，所以按"高信息密度"设计）
思路借鉴 **AOCI**（见文末致谢）：索引不是源码/摘要的搬运，而是一份**纯文本、定长字段、带硬配额**的定位表。

`skill-index.llms.txt` —— **模型读的那一份**。头块声明格式 + **一条记录占且只占一行**：

```
#SKILL-INDEX: 1
#Format: skill-index/dense-v1   (plain text; one record per line)
#Count: <n> skill + <m> mcp = <total>
#Legend: <name>[tags]: <description> | 触发: <t1>、<t2>    tags: s=skill m=mcp R=has-references
#Quota: description<=400chars, triggers<=8
#Rule: 这里只放「定位用」摘要；SKILL.md 全文属 L2，命中后再取，不要抄进本文件

<技能名>[sR]: <做什么/何时用，一行写完> | 触发: <词1>、<词2>
```

- **头块**：模型不必猜格式，也便于人工核对（`#Count` / `#Quota` / `#Rule` 都是自描述的）。
- **一条一行 + 定长字段顺序**：可整行截取、可按行做预算，不浪费 token 在 JSON 括号与缩进上。
- **紧凑标签**：`[sR]` 一次表达"技能 + 含参考资料"（`m`=MCP）。
- **硬配额**：单条描述 ≤400 字符（约中位数 3 倍，只夹长尾）、触发词 ≤8 个 —— 防止索引随时间被撑成全文副本。

`skill-index.json` —— **机器读的那一份**（紧凑 JSON、不缩进）。`search` / `detail` / `update` 用它，
**描述保真、不截断**。两份分工明确：**模型读的省 token，机器读的保真**。

它还额外携带三样（**只在机器这份里**，绝不为它们多占模型那份的一行 token）：

| 字段 | 是什么 | 谁在用 |
|------|--------|--------|
| `id` | 每条记录的**稳定标识** `skill:<名>` / `mcp:<名>` | 关系边两端引用它 |
| `edges` | **关系边**：`overlap`（描述/触发词重叠，"做的事像"）/ `family`（同族命名） | `related`、`stats`、`export minjson`、`bundle` |
| `meta` | **覆盖口径**：按来源计数、全文覆盖（完整/占位/无本地文件）、缺什么（无描述/无触发词名单）、边数 | `stats` |

边是 `index`/`update` 时**算一次存下来的**，所以到处看到的"相关技能"是同一套结果（旧版索引没存边时 `related` 会就地现算）。

## 分层加载（如何"加速任何平台"）
`bundle` 生成三层制品 + 关系图，接入任意平台时把 `boot.md` 作为系统提示词开头：

| 层 | 文件 | 内容 |
|----|------|------|
| L0 | `skills.L0.txt` | 仅技能名（最省；只想知道"有哪些技能"时用它） |
| L1 | `skills.L1.txt` | 上面那份 dense 索引（含头块，**启动加载这层**） |
| L2 | `skills.L2.map.json` | 名字 → SKILL.md 全文路径（命中才取） |
| 图 | `skills.edges.json` | 关系边（需要"谁和谁相关"时才加载，平时不必进上下文） |

## 覆盖范围（"搜索全部"）
扫描根按优先级合并，**换环境/进沙箱也不会扫出 0 项**：

1. `skill_indexer.config.json` 的 `skill_roots`（显式指定，最高优先）
2. 环境变量 `SKILL_INDEXER_SKILL_DIRS`（路径分隔符分隔）
3. **自身相对**：技能自身所在的父 / 祖父目录——技能被解压到哪，那里往往就是平台的技能仓库
4. 内置约定目录：`~/.claude/skills`、`~/.claude/plugins`、`~/.workbuddy/skills`、`~/.codebuddy/skills`、`~/.cursor/skills`、`~/.config/claude/skills`、`~/.openclaw/skills` 及项目级 `.claude/.workbuddy/.cursor/.codeium/skills` 等
5. **广域兜底**：约定根里除本技能自己外一个技能都没有时，自动从 `~ / /app / /workspace / /opt / /srv` 等起点浅层搜 `skills` / `*-skills` 目录（`index --broad` 可强制开启）

递归只跳过 `.git`/`node_modules` 等垃圾目录，**隐藏技能包（如 `.minimax-skills`）内的技能也会被收录**。
**跳哪些目录由 `.skillignore` 决定**（gitignore 语法，放在技能根目录；不写就用内置默认）——想忽略某个目录不用改代码：

```
*/backup/
!dist                  # 撤销内置默认：不再跳过 dist
tmp/
```
支持 `#` 注释、`!` 否定、`dir/` 只匹配目录、`/x` 从技能根锚定、`*` `?` `**` 通配。规则 = 内置默认 → 本文件，
所以 `!` 能把内置默认跳过的东西重新纳入（与 git 一致：目录一旦被跳过就不再往下走，里面的子项无法单独再纳入）。

MCP：扫描 `~/.claude.json`、`.mcp.json`、`.claude/mcp.json`、`.workbuddy/mcp.json` 等的 `mcpServers`（含 `projects` 嵌套）。

### 换环境后"0 项"怎么办
先分清两类：

**A. 宿主技能目录不可达（平台把 shell 隔离在会话工作区）** —— 这是**运行环境不匹配，不是 indexer 的 bug**。若 shell 被隔离在会话工作区、宿主技能目录一碰就被安全策略拦，扫描根必然全落空 → `{"count": 0, "entries": []}`。**再怎么写扫描逻辑都扫不到**，只能走平台正规通道：让平台把技能导出到 `<技能根>/inputs/platform_skills.json`，再跑桥接：

```bash
python scripts/platform_bridge.py              # 默认全量：清单→镜像→index+audit（缺完整 SKILL.md 正文则中止）
python scripts/platform_bridge.py --status     # 状态：清单/镜像/索引 在不在、是不是全量
python scripts/platform_bridge.py --src -      # 从 stdin 读清单
python scripts/platform_bridge.py --mode list  # 显式接受降级：只建"元数据索引"（非全量）
python scripts/platform_bridge.py --force      # 清单没变也强制重落镜像
python scripts/platform_bridge.py --only clean # 清掉上次镜像（按 manifest，只删它建的）
```
桥接把技能落成 `<技能根>/inputs/platform_mirror/<name>/SKILL.md` 镜像，用 `SKILL_INDEXER_ONLY_DIRS=1` 让 indexer **只扫镜像**，索引结果即平台的技能集。清单没变（按指纹）时跳过重落 → 重复跑幂等。

### 生命周期分层：镜像和索引是易失的，脚本才是资产
| 层 | 内容 | 生命周期 | 跨会话 |
|---|---|---|---|
| ① 索引产物 | `skill-index.json` / `.llms.txt` / L1 / catalog | 技能根目录，**易失** | ❌ 需重建 |
| ② 镜像目录 | `inputs/platform_mirror/` | 技能包内，**易失** | ❌ 需重建 |
| ③ 桥接脚本 + 文档 | `scripts/`、`SKILL.md` | 平台数据库里的技能包，**持久** | ✅ 真正的资产 |
| ④ 清单暂存 | `inputs/platform_skills.json` | 平台侧暂存，**大概率在，不保证**（且通常**只有元数据**） |

⇒ **修复方案把"可重建的逻辑"固化在脚本里，而不是指望 ①② 活着。** 元数据层（镜像 / 索引）确实一条命令就能重算：

```bash
python scripts/platform_bridge.py --status   # 看状态
python scripts/platform_bridge.py            # 重建镜像 + 索引
```
但要分清：这条命令重建的是**元数据**。**完整 SKILL.md 全文从来不在 ①②④ 里** —— 要全量索引，必须先回平台逐技能取回全文（`--mode full` 默认就要求这一点）；**全文一旦丢了是恢复不了的**。
**并且 `index` / `update` / 任何读命令都自带自愈**：`list` / `search` / `stats` 这类命令发现**索引不存在会自动建一次**；只要发现"只扫到本技能自己"且技能包里有平台清单，就自动落镜像并重扫（`SKILL_INDEXER_NO_AUTO=1` 关闭）。**构建分两档，绝不静默冒充**：`index` / `update` 默认建**元数据索引**（一行一技能：名字+描述+触发词，仅定位/路由用，诚实标注、无压缩率数字）；`index --full` / `update --full` 才是**全量构建**——要求清单含完整 SKILL.md 正文，缺任何一项就**中止（rc=2）、不写索引**，并打印"怎么取全文"的步骤。`detail` / `audit` / `content-find` / `bundle` 这些要正文的命令遇到元数据索引会明确提示"需要全文 → 跑 `index --full`"（或按步骤回平台取回），**拿占位元数据冒充"构建完成"是禁止的**。

> **两点诚实提醒**
> 1. 平台 `list` 只给 name/description，**不含完整 SKILL.md 正文**。拿它建出来的是"元数据索引"，`audit` 会**拒绝给出压缩率**（基线不是全文，算了也是假的）。要真索引就按 `--mode full` 提示的步骤先取全文。
> 2. 平台自带 `skill_follow(list/load)` 时，"有哪些技能/怎么用"它就是**实时索引**，不必跑索引。桥接的价值在 indexer 独有能力：`audit`（token 账本）、`bundle`（L0/L1/L2）、`export`（跨平台导出）。

**B. 技能在磁盘上、只是不在约定目录** —— 走扫描/导入：

1. 先 `python scripts/build_index.py roots` 看这台机器上到底有什么（`index` 为 0 时也会自动打印探针）。
2. 有技能目录 → 写进 `skill_indexer.config.json` 的 `skill_roots`，或设 `SKILL_INDEXER_SKILL_DIRS`。
3. 仍拿不到 → 用 `import` 灌清单：`python scripts/build_index.py import skills.txt` / `... import -`（stdin）/ `... import "技能A: 描述A"`。

## 环境变量
| 变量 | 作用 |
|---|---|
| `SKILL_INDEXER_SKILL_DIRS` | 追加技能扫描根（路径分隔符分隔） |
| `SKILL_INDEXER_MCP_CONFIGS` | 追加 MCP 配置路径 |
| `SKILL_INDEXER_OUT_DIR` | 产物输出目录（默认技能根目录） |
| `SKILL_INDEXER_ONLY_DIRS` | `1`=只用显式给的根，不掺自身相对与内置默认（桥接用） |
| `SKILL_INDEXER_NO_AUTO` | `1`=关闭 `index`/`update` 的桥接自愈 |
| `SKILL_INDEXER_BRIDGE_RUN` | 桥接子进程标记（防自愈递归，一般不用手动设） |
| `SKILL_INDEXER_MIRROR` | 桥接的镜像目录（默认 `<技能根>/inputs/platform_mirror`） |
| `PLATFORM_SKILLS_JSON` | 桥接的清单文件路径 |

## 检索质量地基
索引器只抽取、不润色。被索引的每个技能，其 `description` 必须写成「**做什么 + 何时用 + 触发词**」——这是建议准不准的根本。解析支持单行、引号、以及 YAML 块标量 `>` / `|`。

## 设计来源与致谢（Attribution）
本技能为自建实现，思路参考以下公开项目（均开源/MIT），非照搬源码：

- **Livus-AI/Skills-MCP** — 技能名+描述内联实现零成本发现；三级渐进披露。
- **rstkit/meta_skill**（`ms`，Rust）— 元数据抽取 + SQLite FTS5(BM25)+FNV-1a 哈希嵌入 + RRF 混合检索。
- **ohboyftw/meta-mcp** — 跨客户端 MCP 配置统一发现。
- **c2s/agent-skills-hub（UseSkill）** — 多源技能聚合 + 全文检索元注册表。
- **mcp-tool-shop-org/mcp-tool-registry** — data-only 注册表 + 预建搜索索引 + `registry.llms.txt`。
- **aoci-spec/aoci-code（AOCI，AI-Oriented Cognition Infrastructure）** — **本技能索引格式最直接的借鉴对象**。
  它把代码 / 配置 / 数据库结构梳理成给 LLM 读的**纯文本认知索引**：**符号层**（结构、依赖、接口）+ **语义层**（业务语义、非显性约束），
  用紧凑标签与**硬性字符配额**控制信息密度，并明确**禁止把源码或普通摘要抄进索引**；
  还规定了分块交付与"读完什么才算读全"的契约。本技能的索引格式（头块声明 / 一条一行 / 定长字段顺序 / 配额 / 「不抄全文」这条硬规矩）即借鉴其做法。

- **Egonex-AI/Understand-Anything**（MIT）— 把**代码与业务**梳理成带稳定 ID 的**知识图**（节点 + 关系边 + 覆盖率统计），
  并用 `.understandignore` 让用户定制忽略规则。它属于"数据库 + 工具查询"型（图很大，靠工具按需查），与本技能"AOCI 式、读一次的纯文本索引"是两种路线，
  所以**它的整份大图不能照搬**；只借三处做法：① 节点用**稳定 ID**、关系固化成**边**（→ `id` / `edges` / `related`）；
  ② 忽略规则写成**文件**而非硬编码黑名单（→ `.skillignore`）；③ 索引配一份**覆盖口径**，能回答"覆盖了什么、缺什么"（→ `meta` 与 `stats`）。

## 合规说明
- 思路 / 架构 / 模式不受版权保护；本仓库为独立编写代码，并保留上述致谢。
- 如需参赛或发布，建议保留本 README 的致谢章节，避免"看起来像直接复制"。
