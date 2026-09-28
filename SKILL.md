---
name: skill-indexer
description: 跨技能检索与编排网关。不知道用哪个技能时，或需要多技能串联完成复合任务时，呼叫本技能。触发词：技能索引、技能编排、技能串联、意图路由、技能网关
version: "1.0"
---

# Skill Indexer（元技能：全局技能调度、代理直达与图谱编排网关）

## 🔴 红线契约（4 条，违反即判定调度失职）

1. **前置探查铁律**：接到任务的首个动作必须调用 CLI 检索本地技能库，严禁在未检索前主观假定"不涉及技能"。工程/复合任务 → `pipeline`；单点查询 → `chat`；严禁用 `search` 替代 `pipeline` 处理工程任务。
2. **采纳即加载**：本地命中且**采纳**某技能承接任务/阶段时，先 `detail <技能名>` 加载其 SKILL.md 全文、按其规范真实执行（同一技能只加载一次）；被否决的候选、或"通用能力承接"的阶段**不加载任何全文**。完全无匹配时方可降级为通用能力，并输出降级声明。
3. **零指定原则**：用户只描述需求，**永远不需要、也不应被要求指定目标技能名**——选型是网关的职责；即使用户点名了某技能，也必须先经 `pipeline`/`chat` 检索验证（点名仅作高权重提示），并输出命中依据。
4. **留痕真实性**：脚注仅当真实完成调度时输出，占位符由 CLI 实际返回值填充；严禁输出编造的百分比/命中数，严禁未跑检索就声称"命中"。
5. **编排结果排除自身**：pipeline 编排与检索结果中排除 `skill-indexer` 自身，防止自环套娃。
6. **检索双语化铁律**：构造 chat/search/pipeline 的 query 时，任务中的专名与技术栈**保留原文**（Salesforce、K8s、PDF…），领域概念由调用方 Agent **自行给出中英双语词**再拼接查询（如"用户认证"→"用户认证 user authentication"）。索引分词是语言敏感的：纯中文 query 命不中纯英文条目，反之亦然——这一步由智能体完成，不依赖任何内置词典。

> 红线展开说明见 [`references/governance.md`](references/governance.md#红线契约展开)。

---

## 双模式简述

| 模式 | 触发条件 | 核心命令 | 产出 |
|:---|:---|:---|:---|
| **透明代理直达** | 单一业务领域 / 明确单点操作 | `chat "<问题>"` 或 `route` | 匹配最佳技能，直接代理执行 |
| **图谱串联编排** | 复杂研发 / 故障排查 / 端到端综合需求 | `pipeline "<复合任务>"` | depends_on 拓扑分层的流水线 DAG + 阶段协同矩阵（无预设模板） |

- 单技能命中时强制联动：主技能负责实现 + 关联技能负责规范/审查，禁止单打独斗。
- 流水线阶段严格执行上下文接力（Context Handoff）：阶段数量与顺序由图谱 `depends_on` 边的拓扑分层决定；图谱查不出依赖关系时，输出**并行候选**（列候选 + 检索证据，由执行 Agent 甄别采纳），不得伪造时序。
- 退出码约定：`0` 成功；`2` 预期内未命中/未找到（可换词重试）；`1` 异常错误。

> 双模式详细契约与 Context Handoff 规范见 [`references/governance.md`](references/governance.md#双模式详细契约)。

---

## 脚注模板（仅当通过本网关完成调度时输出）

1. **单技能直达**：`[skill-indexer] 智能直达: 命中 [<目标技能名>] | 消除中间转接`
   *(联动时：`[skill-indexer] 智能直达: 命中 [<目标技能名>] (联动: [<技能B>]、[<技能C>]) | 消除多跳选型`)*
2. **多技能编排（拓扑分层）**：`[skill-indexer] 技能串联编排: [<阶段1技能>] → [<阶段2技能>] → … | 图谱拓扑自动生成 X 阶段流水线 | 消除多次人工选型与上下文断层`
   **（并行候选时）**：`[skill-indexer] 并行候选编排: [<技能A>] / [<技能B>] | 图谱无依赖边，不伪造流水线 | 由执行 Agent 甄别采纳`
3. **纯检索**：`[skill-indexer] 命中 <N>/<Total> | 索引 <IdxChars> vs 全文 <FullChars> | 节省 <Pct>% token`

> 占位符由 CLI 实际返回值填充，请勿硬编码数字。

---

## 目录结构

```
skill-indexer/
├── SKILL.md                      # 本文件（网关定义与调度契约）
├── README.md                     # 设计原理与致谢
├── 使用说明.md                   # 详细使用手册
├── skill_profile.json            # 用户画像（固定根/置顶/排除/全局指令）
├── .skillexclude / .skillignore  # 排除与忽略规则
├── skill_indexer.config.json     # 底层配置（扫描根/边权重）
├── inputs/                       # 外部输入（平台清单等）
├── output/                       # 运行时产物（不入源码包）
├── scripts/                      # 核心脚本（build_index.py 为 CLI 入口）
├── tests/                        # 测试文件
├── docs/                         # 开发过程文档
└── references/                   # 参考文档
    ├── commands.md               #   子命令手册（完整参数与示例）
    ├── index-format.md           #   索引字段与产物说明
    └── governance.md             #   治理机制与详细契约
```

---

## 命令速查

核心命令（在技能根目录执行 `python scripts/build_index.py <命令>`）：

| 类别 | 命令 | 一句话说明 |
|:---|:---|:---|
| 调度 | `chat` / `route` / `pipeline` / `chain` | 意图路由、代理直达、多技能编排 |
| 检索 | `search` / `list` / `related` / `content-find` / `explain` / `detail` / `onboard` / `diff` | 关键词检索、浏览、关联、正文、上手、影响 |
| 索引 | `index` / `update` / `add` / `roots` / `import` / `clean` | 重建（默认元数据档，`--full` 全量）/增量/追加/诊断/导入/清理 |
| 输出 | `audit` / `stats` / `doctor` / `bundle` / `export` / `catalog` / `dashboard` | 账本/口径/体检/打包/导出/总览/图谱 |
| 画像 | `profile` / `memory` | 用户画像与指令管理、活跃记忆与纠偏 |

> 完整参数、示例与意图路由映射见 [`references/commands.md`](references/commands.md)。

---

## 意图路由

| 话术关键词 | → 命令 |
|:---|:---|
| 开发/完整流程/上线 | `pipeline` |
| Bug/诊断/修复 | `pipeline` |
| 业务/状态机 | `chat` |
| 技能清单/有多少 | `list` |
| 怎么用/触发 | `explain <名>` |
| 看全文 | `detail <名>` |
| 相似/关联 | `related <名>` |
| 搜代码 | `content-find` |
| 压缩率 | `audit` |
| 体检 | `doctor` |

> 完整映射见 [`references/commands.md`](references/commands.md#意图路由话术--命令)。

---

## 治理与自愈

网关内建 7 项治理机制（配额硬顶、增量自愈、黑名单、图谱排除控制、用户画像、自省进化、主动自适应）与 3 条"0 项"自愈策略。

> 完整说明见 [`references/governance.md`](references/governance.md)。

---

## Windows 兼容提示

命令含引号/特殊字符时：PowerShell 用 `` `" `` 转义或 `--%` 停止解析；cmd 用 `\"` 转义；推荐将长文本写入临时文件管道传入。
