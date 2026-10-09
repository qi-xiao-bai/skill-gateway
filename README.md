# Skill Gateway（全局技能调度、代理直达与图谱串联编排网关）

> **版本**：v4（自建实现，独立开发）
>
> **演进关系声明**：本作品由作者此前作品 skill-indexer（索引器）演进而来——索引底座为继承，**调度契约、生命周期编排、图谱治理、命中率账本、平台/Agent 双口径、dashboard 图谱面板为本作品新增贡献**（演进记录见 `docs/PRD-skill-indexer-enhancement.md`），按"同一作者不同作品独立评分"原则参评。
>
> **测试留痕**：全量测试 123 项全过（pytest，含元一致性守卫、翻译链回归、生命周期编排等），原始输出见 `references/TEST-EVIDENCE.md`。

一个**技能资产中枢型元技能（meta-skill）**：把数字员工与 Agent 平台上"上百个孤立技能"变成**可编排、可看见、可治理**的基础设施。

- **能力一：多技能智能串联编排（Multi-Skill Pipeline Chaining）**：针对开发完整需求、故障排查等复合任务，基于**知识图谱拓扑边（`depends_on` 依赖、`contains` 包含、`similar` 相似）**做 Kahn 拓扑分层，自动把相关技能编排为端到端**流水线 DAG**。阶段数量与顺序由图谱边决定；查不出依赖关系时输出并行候选（列候选与检索证据，由执行 Agent 甄别采纳）。
- **能力二：技能资产可视化报表（Visibility & Reporting）**：**关系图谱 dashboard**（节点/关系边/平台→Agent 双口径下钻，单文件离线可用）+ **命中率账本**（ledger：零命中 query 自动转能力缺口工单、技能命中/采纳榜、僵尸技能盘点）+ **Token 经济审计**（audit：索引 vs 全文压缩率）+ **覆盖口径报表**（stats：平台可用/未绑定/已排除三档与缺口清单）——技能资产现状与治理点一图看全。
- **能力三：检索打分与代理直达（Scoring & Proxy）**：单点需求经 IDF 加权打分**秒级匹配最佳技能并代理执行**；候选打分并列时列证据交人工确认，零命中自动转能力缺口工单——选型有据、缺口可见。
- **按需加载（渐进式披露）**：平台启动只加载轻量索引（L1），真正要用某技能时才加载全文（L2），避免上下文爆炸。

## 目录结构
```
skill-gateway/
├── SKILL.md                      # 技能定义与双模式调度契约
├── README.md
├── skill_profile.json            # 用户重要指令与技能画像（固定根/置顶技能/全局必遵指令）
├── .skillexclude                 # 技能排除规则（黑名单通配）
├── .skillignore                  # 扫描忽略规则（gitignore 语法）
├── skill_gateway.config.json     # 可选：追加扫描根与边权重
├── inputs/                       # 外部输入（平台清单等）
├── output/                       # 运行时生成物统一隔离存放处（不入源码包）
├── templates/                     # 独立资产（dashboard 前端模板）
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

## 子命令（能力，均在技能根目录执行）
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
3. **动态分数剪枝 (Dynamic Pruning)**：底线分 `MIN_RELEVANCE_SCORE = 0.35`，并截断低于最大证据权重 25% 的长尾低质技能（基准取名称加分/置顶提权之前的证据权重，命中加分不连带抬高剪枝线），强力降噪。
4. **未命中定向自愈 (Targeted Update on Miss)**：检索 0 命中时探测磁盘，若有新装/修改技能，针对查询关键词定向增量编译，避免耗时全量重扫。
5. **多级黑名单与图谱可视化 (.skillexclude / Dashboard)**：支持通配符排除名单；在 D3 拓扑图中以红色虚线/暗色区分已排除节点，并支持页面一键显隐切换。
6. **主动自省进化与无感智能更新 (Self-Improving + Proactive Agent)**：引入后置执行钩子（Post-Execution Hook），每次操作后基于根目录物理变动指纹（`os.scandir` 毫秒级探测）与心跳周期自省决策增量对齐。**计频口径只认"真实使用"**：用户显式 `detail` 查看全文 / 明确采纳才计入频次——出现在检索结果里不算（否则"被搜到 3 次"就自动置顶，置顶提权又让它更容易被搜到，形成与用户意图无关的正反馈）；高频技能达到阈值（默认 3 次）自动晋升至置顶画像（`pinned_skills`），状态文件与纠偏日志均有增长上限。
7. **退出码约定（给调用方/Agent 区分三种结局）**：`0` 成功；`2` 预期内的未命中/未找到（可换词重试，如 detail 找不到技能、检索 0 命中）；`1` 异常错误（顶层兜底打印 traceback）。裸跑 `build_index.py`（无子命令）打印帮助退出，不会隐式触发全量扫描。


## 索引格式（给 AI 读，所以按"高信息密度"设计）
思路借鉴 **AOCI**：索引不是源码/摘要的搬运，而是一份**纯文本、定长字段、带硬配额**的定位表。

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

1. `skill_gateway.config.json` 的 `skill_roots`（显式指定，最高优先）
2. 环境变量 `SKILL_GATEWAY_SKILL_DIRS`（路径分隔符分隔）
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
桥接把技能落成 `<技能根>/inputs/platform_mirror/<name>/SKILL.md` 镜像，用 `SKILL_GATEWAY_ONLY_DIRS=1` 让 indexer **只扫镜像**，索引结果即平台的技能集。清单没变（按指纹）时跳过重落 → 重复跑幂等。

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
**并且 `index` / `update` / 任何读命令都自带自愈**：`list` / `search` / `stats` 这类命令发现**索引不存在会自动建一次**；只要发现"只扫到本技能自己"且技能包里有平台清单，就自动落镜像并重扫（`SKILL_GATEWAY_NO_AUTO=1` 关闭）。**构建分两档，绝不静默冒充**：`index` / `update` 默认建**元数据索引**（一行一技能：名字+描述+触发词，仅定位/路由用，诚实标注、无压缩率数字）；`index --full` / `update --full` 才是**全量构建**——要求清单含完整 SKILL.md 正文，缺任何一项就**中止（rc=2）、不写索引**，并打印"怎么取全文"的步骤。`detail` / `audit` / `content-find` / `bundle` 这些要正文的命令遇到元数据索引会明确提示"需要全文 → 跑 `index --full`"（或按步骤回平台取回），**拿占位元数据冒充"构建完成"是禁止的**。

> **两点诚实提醒**
> 1. 平台 `list` 只给 name/description，**不含完整 SKILL.md 正文**。拿它建出来的是"元数据索引"，`audit` 会**拒绝给出压缩率**（基线不是全文，算了也是假的）。要真索引就按 `--mode full` 提示的步骤先取全文。
> 2. 平台自带 `skill_follow(list/load)` 时，"有哪些技能/怎么用"它就是**实时索引**，不必跑索引。桥接的价值在 indexer 独有能力：`audit`（token 账本）、`bundle`（L0/L1/L2）、`export`（跨平台导出）。

**B. 技能在磁盘上、只是不在约定目录** —— 走扫描/导入：

1. 先 `python scripts/build_index.py roots` 看这台机器上到底有什么（`index` 为 0 时也会自动打印探针）。
2. 有技能目录 → 写进 `skill_gateway.config.json` 的 `skill_roots`，或设 `SKILL_GATEWAY_SKILL_DIRS`。
3. 仍拿不到 → 用 `import` 灌清单：`python scripts/build_index.py import skills.txt` / `... import -`（stdin）/ `... import "技能A: 描述A"`。

## 环境变量
| 变量 | 作用 |
|---|---|
| `SKILL_GATEWAY_SKILL_DIRS` | 追加技能扫描根（路径分隔符分隔） |
| `SKILL_GATEWAY_MCP_CONFIGS` | 追加 MCP 配置路径 |
| `SKILL_GATEWAY_OUT_DIR` | 产物输出目录（默认技能根目录） |
| `SKILL_GATEWAY_ONLY_DIRS` | `1`=只用显式给的根，不掺自身相对与内置默认（桥接用） |
| `SKILL_GATEWAY_NO_AUTO` | `1`=关闭 `index`/`update` 的桥接自愈 |
| `SKILL_GATEWAY_BRIDGE_RUN` | 桥接子进程标记（防自愈递归，一般不用手动设） |
| `SKILL_GATEWAY_MIRROR` | 桥接的镜像目录（默认 `<技能根>/inputs/platform_mirror`） |
| `PLATFORM_SKILLS_JSON` | 桥接的清单文件路径 |

## 检索质量地基
索引器只抽取、不润色。被索引的每个技能，其 `description` 必须写成「**做什么 + 何时用 + 触发词**」——这是建议准不准的根本。解析支持单行、引号、以及 YAML 块标量 `>` / `|`。

