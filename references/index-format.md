# 索引格式与产物说明

## 两份索引的分工（重要）
索引是**给 AI 读的**，所以按"高信息密度"设计（思路借鉴 AOCI，见 README 致谢）。
同一份索引出两个文件，**别混用**：

| 文件 | 读者 | 格式 | 取舍 |
|------|------|------|------|
| `skill-index.llms.txt` | **模型** | 纯文本 dense（`#Format: skill-index/dense-v1`） | 省 token：一条一行、定长字段、紧凑标签、**硬配额**（描述 ≤400 字符、触发词 ≤8 个） |
| `skill-index.json` | **机器** | 紧凑 JSON（`separators=(",",":")`，不缩进），`skill-index/v2` | 保真：`search`/`detail`/`update`/`audit` 用它，**描述不截断**；另带 `edges`（关系边）与 `meta`（覆盖口径） |

> 关系边与覆盖口径**只在机器那份里**：模型读的那份要省 token，绝不为它们多占一行。

### dense 文本格式（`skill-index.llms.txt`）
```
#SKILL-INDEX: 1
#Format: skill-index/dense-v1   (plain text; one record per line)
#Count: <n> skill + <m> mcp = <total>
#Legend: <name>[tags]: <description> | 触发: <t1>、<t2>    tags: s=skill m=mcp R=has-references
#Quota: description<=400chars, triggers<=8
#Rule: 这里只放「定位用」摘要；SKILL.md 全文属 L2，命中后再取，不要抄进本文件

<技能名>[sR]: <做什么/何时用，压成一行> | 触发: <词1>、<词2>
```
- **头块**必须保留：模型据此知道格式与配额，人工也能一眼核对计数。
- **一条记录占且只占一行**（描述里的换行会被折叠成空格）——才能按行截取、按行做预算。
- **标签**：`s`=skill、`m`=mcp、`R`=含 `references/scripts/assets`。
- 被配额截断的描述以 `…` 结尾；**完整内容始终在 `skill-index.json` 里**，`detail` 取的是全文而非索引。

## 每条记录的字段（`skill-index.json` 的 `entries[]`）
| 字段 | 含义 |
|------|------|
| `name` | 技能名 / MCP server 名（同名只索引一次） |
| `id` | **稳定标识**：`skill:<名字>` / `mcp:<名字>`。关系边两端只引用它，不靠"名字刚好一样"；换平台/重扫也稳定 |
| `description` | 描述（frontmatter `description`，支持 `>`/`|` 块标量；缺失则用首个非标题段落） |
| `triggers` | 从描述里解析出的触发词数组（识别「触发词：」/`TRIGGER when`/`Use when`） |
| `type` | `skill` 或 `mcp` |
| `path` | 技能目录（技能）或配置文件路径（MCP）。**`import` 进来的平台记录无本地文件，此字段为空串**——消费方遇到空串要当作"无本地全文"，不要拼 `SKILL.md` |
| `has_references` | 是否含 `references/` `scripts/` `assets/` 之一 |
| `source` | 从哪个扫描根发现 |
| `mtime` | `SKILL.md` 的修改时间戳；增量 `update` 靠它判断该技能"变了没"（变了才重读） |

## 关系边 `edges[]`（技能之间连不连得上）
每条边形如 `{"source":"skill:a","target":"skill:b","type":"overlap","direction":"undirected","weight":0.667}`，
两端是记录的 `id`。**在 `index`/`update` 时算一次存进索引**，`related` / `stats` / `export` / `bundle` 都读它，
不再各自现算——所以到处看到的"相关技能"是同一套结果。

| `type` | 含义 | 怎么算出来的 |
|--------|------|--------------|
| `overlap` | 做的事很像 | 描述 + 触发词的词重叠（Jaccard ≥ 0.12）；每个节点最多留 6 条（`weight` 就是相似度） |
| `family` | 同一家的（同族命名） | 名字共享前缀命名空间（如 `minimax-pdf` / `minimax-xlsx`）；成员 ≤6 个两两成边，再多只连到族根 |

两条刻意的取舍（都是为了让边"有信息量"而不是"有数量"）：
- **MCP 不参与 `overlap`**：MCP 的描述是扫描器按 `MCP server 'x' (cmd)` 生成的模板串，两条之间天然重合，算出来全是假边。
- **大族不两两成边**：一个 20 人的族两两连是 190 条边，体积噪声；改"星形"（只连族根）。

边缺失时（旧版索引）不必紧张：`related` 会自动现算一份，`stats` 会提示"跑一次 `index`/`update` 即会生成"。

## 覆盖口径 `meta`（这份索引覆盖了什么、缺什么）
`skill-index.json` 的 `meta` 回答四个问题，`stats` 就是把它打印出来：

```json
{"total":111,"skill":106,"mcp":5,
 "by_source":{"<扫描根>":40,"...":1},
 "with_references":35,
 "full_text":106,"stub":0,"no_file":0,
 "no_description":[],"no_triggers":["..."],"stub_names":[],"no_local_text":[],
 "edges":108}
```
| 段落 | 看什么 |
|------|--------|
| 按来源 | 每个扫描根贡献了多少条（换机器后对照 `roots` 排查"为什么少了"） |
| 全文覆盖 | `full_text`（`detail` 能取到完整 SKILL.md）/ `stub`（桥接"仅描述"占位，不是全文）/ `no_file`（`import` 进来的，只有描述） |
| 缺什么 | `no_description` / `no_triggers` 的**名单**——直接影响"被 AI 发现"的概率，配 `doctor` 看明细 |
| 关系图 | `edges` 条数；`stats` 另算孤立节点（不与任何技能相连的技能） |

## 产物一览（默认写在 `output/` 目录，可通过 `skill_gateway.config.json` 的 `out_dir` 或环境变量 `SKILL_GATEWAY_OUT_DIR` 覆盖）
| 文件 | 内容 | 典型用途 |
|------|------|----------|
| `skill-index.llms.txt` | dense 纯文本索引（含头块） | **喂给 LLM / 平台启动加载** |
| `skill-index.json` | 完整索引（紧凑 JSON：entries + edges + meta） | 程序解析（`search`/`detail`/`update`/`related`） |
| `skill-index.min.json` | 紧凑 JSON（短字段 `n/d/g/t/p/r/s`，含 `edges`） | 上传网页 / 进一步省 token（`export minjson`） |
| `skills.L0.txt` | 仅技能名 | 极省预算的启动（`bundle`） |
| `skills.L1.txt` | = dense 索引（含头块） | **平台启动加载这层**（`bundle`） |
| `skills.L2.map.json` | 名字 → 全文路径 | 命中后按需取全文（`bundle`） |
| `skills.edges.json` | 关系边（`{count, edges[]}`） | 需要"谁和谁相关"时按需加载（`bundle`） |
| `boot.md` | 启动引导提示词 | 作为系统提示词开头（`bundle`） |
| `catalog.md` | Markdown 总览 | 人工阅读 / 网页展示 |
| `skills.index.md` / `.csv` / `.txt` | 表格 / CSV / 纯文本 | `export md|csv|txt` |

## 分层加载（渐进式披露）
- **L0**（名字）→ **L1**（dense 索引）→ **L2**（全文）。
- 平台启动只加载 L1，判断要用哪个技能后再取 L2 全文，避免把全部 `SKILL.md` 灌进上下文。
- `audit` 会给出你**这台机器**上的量化结果（全文 vs L1 vs L0 的字符数与压缩比）——数值随技能集与描述长度而变，以实跑为准。
- `audit` 同时会报出 dense 索引里被配额截断的条数（完整描述仍在 `skill-index.json`）。
- 无本地全文的记录（`import`/平台镜像）不计入分子分母，`audit` 会单独标注条数。

## content-index（正文分块倒排）`skill-content-index/v2`

- **单文件**：`terms_cache`（{chunk_id: [terms]}）内嵌在主文档里，与 chunks 同文件原子落盘——不再有"索引写成功、terms 写失败"导致的 chunk_id 静默错位。
- **键统一**：terms_cache 键一律为字符串（与 JSON 落盘形态一致）。
- **投喂 chunk 带真实源路径**：`src_dir`（依赖技能的真实目录）+ `src_file`（目录内相对路径）；`file` 字段里的 `@dep` 只是展示标签。正文检索/读取原文据此可达（此前伪路径导致投喂正文从未被真正索引）。
- **索引键 = 条目 frontmatter 名**（按真实路径匹配条目），与条目索引/图谱边对齐；无条目目录才退回目录名。
- **版本校验**：读取时校验 `version`，不符视同不存在 → 上层全量重建。
- 正文检索打分与主检索同口径（maximal 去重），中文 gram 不重复撑分。
