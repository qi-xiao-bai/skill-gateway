<div align="center">

# skill-indexer

**本地技能智能层 —— 让 Agent 找得到、用得起、串得起它已安装的技能。**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-81%20passed-brightgreen.svg)](#)
[![Deps](https://img.shields.io/badge/dependencies-zero-success.svg)](#设计原则)

[English](README.md) · **简体中文**

</div>

---

当 Agent 的技能库超过几十个，三个问题立刻出现：**不知道该用哪个**（选型靠猜）、
**上下文被吃光**（几百个 SKILL.md 全文常驻）、**多技能任务没人串**（各干各的）。

`skill-indexer` 装进技能目录后，就是 Agent 的**技能库管家**：一键建立全库索引与知识图谱，
此后 Agent 收到任何任务都能秒级选技能、按 `depends_on` 拓扑自动编排流水线，
而你的上下文成本从 **7,330,470 字符降到 167,772 字符（省 97.7%，44×）**。

## 安装

把本仓库放进任意宿主的技能目录即可：

```bash
# Claude Code / Claude Desktop
git clone https://github.com/qi-xiao-bai/skill-indexer.git ~/.claude/skills/skill-indexer

# 或其他技能目录（~/.cursor/skills、~/.workbuddy/skills …），或解压到平台技能根
```

装完即生效，无需注册、无需配置文件、无需 API key。

## 用法

**你只说话，命令由 Agent 自动执行**——本技能的 `SKILL.md` 内置调度契约
（触发词识别 + 意图路由 + 红线规则），Agent 据此自行选择并执行对应子命令：

| 你对 Agent 说 | Agent 自动执行 | 你得到 |
|---|---|---|
| 「我有一堆技能，这个任务该用哪个？」 | 图谱问答 | 带命中证据的候选技能 + 关联邻居 |
| 「把这个需求编排成流水线」 | 拓扑编排 | 按图谱分层的多阶段执行计划 |
| 「现在技能库的上下文成本是多少？」 | Token 账本 | 压缩率、启动体积、最重的技能 |
| 「刚装了个新技能，刷新一下索引」 | 增量索引 | 只处理新增/变更，秒级完成 |
| 「帮我看下技能库健不健康」 | 技能体检 | 坏描述/重名/断链/超大技能清单 |

一次真实的编排对话（节选）：

```text
你：帮我写一首关于秋天的诗，好像有个技能能做？

Agent：（自动执行 pipeline，节选输出）
  > 编排依据：任务检索种子 + 知识图谱 depends_on 拓扑分层
  > 阶段 2：khazix-writer ← 入选依据：检索命中证据（帮我写、写一）
  [skill-indexer] 技能串联编排: [pdf、skill] → [khazix-writer] | 图谱拓扑自动生成
```

> 💡 **你也可以把 Agent 当运维**：任何时候让 Agent「跑一下技能体检」「看看 dashboard」，
> 它会执行对应命令并把结果整理给你。全部命令见
> [references/commands.md](references/commands.md)——那是写给 Agent 的执行手册，
> 人读只是参考。

## 它做了什么

- **📒 Token 账本** —— 全文 vs 索引的精确成本核算，含配额截断与占位文件审计；
- **🕸️ 知识图谱** —— `depends_on` / `contains` / `overlap` / `similar` 五类边一次构建、处处消费（`related` / `diff` 影响分析 / dashboard / 编排共用同一份图）；
- **🔗 拓扑编排** —— 检索种子 → `depends_on` 分层 → 阶段计划。**零硬编码模板**：图谱无依赖边时如实输出"并行候选"，绝不给"写首诗"硬套研发流水线；
- **🔍 中文优先检索** —— CJK 2/3-gram + IDF 封顶 + maximal 去重，每条结果打印命中词，排名可解释；
- **🩺 技能体检** —— CJK 感知阈值、跨类型重名、占位镜像识别，支持 `--fix` 图谱自愈；
- **📊 离线图谱面板** —— 纯本地 D3 力导向图，无任何网络请求。

## 工作原理

```text
find ──▶ add ──▶ use ──▶ check        npx skills 等（分发侧：已解决）
─────────────────────────────────
index ─▶ audit ─▶ relate ─▶ orchestrate   skill-indexer（装完之后的世界：本仓库）
```

机器读 `skill-index.json`（描述保真 + 图谱边），模型读 `skill-index.llms.txt`
（一行一条、硬配额的 dense 索引）。首次读命令自动全量建索引，之后全部按 mtime 增量更新。
格式详见 [references/index-format.md](references/index-format.md)。

## 设计原则

- **零依赖**：纯 Python 标准库，解压即用；81 个测试，Ubuntu + Windows 双平台 CI；
- **诚实档位**：元数据索引如实标注、拒绝伪造压缩率；缺全文即中止（rc=2）并给出补救步骤；
- **机器可读契约**：退出码 `0` 成功 / `2` 未命中（可换词重试）/ `1` 异常；`--json` 输出纯 JSON；
- **默认全本地**：无遥测、无外发请求；索引、状态文件全部原子写盘。

## Roadmap

- **MCP server 封装** —— 把能力暴露为原生 MCP 工具，宿主无需 shell 即可调用；
- **`npx skills` 生态桥接** —— 开箱读取注册表安装的技能库；
- **语义检索层** —— 在 CJK/IDF 核心之上叠加可选本地向量检索。

## 文档

[命令手册（Agent 执行手册）](references/commands.md) ·
[索引格式](references/index-format.md) ·
[治理契约](references/governance.md) ·
[完整中文手册](使用说明.md) ·
[开发文档](docs/)

## 致谢

自建实现，思路参考（均开源/MIT，未复制代码）：**AOCI**（索引格式：纯文本认知索引/定长字段/硬配额/"不抄全文"）、
**Egonex-AI/Understand-Anything**（稳定 ID + 边固化 + 覆盖口径）、**rstkit/meta_skill**（FTS 检索）、
**Skills-MCP / meta-mcp / agent-skills-hub / mcp-tool-registry**（技能发现模式）。

## 许可证

[MIT](LICENSE)
