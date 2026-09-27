# 子命令手册（script: `scripts/build_index.py`）

所有命令都在**技能根目录**执行，产物默认写到技能根目录（可用 `SKILL_INDEXER_OUT_DIR` 覆盖）。

| 命令 | 作用 | 常用参数 |
|------|------|----------|
| `profile` | **用户画像与指令管理**：设置扫描根、置顶技能与全局必遵指令 | `[--init] [--set-roots R ...] [--add-root R] [--only-fixed] [--allow-all-roots] [--pin S] [--unpin S] [--exclude E] [--add-directive D] [--remove-directive D] [--clear-directives] [--reason R] [--sync-memory]` |
| `pipeline` | **多技能串联编排**：检索种子 → depends_on 拓扑分层 → 阶段/矩阵/执行契约；无依赖边时输出并行候选（无预设模板） | `<任务>` `--json` |
| `chain` | `pipeline` 的快速别名 | `<任务>` |
| `chat` | 技能图谱问答：推荐技能 + 1-hop 关联 + 正文片段（含智能自适应进化） | `<问题>` `--top N` `--exclude E` |
| `content-find` | 正文检索：查代码/规则定义在哪个文件哪行 | `<query>` `--top N` `--full` |
| `onboard` | 技能上手指南：生成结构化入门文档 | `<技能名>` |
| `diff` | 变更影响分析：分析下游与二级依赖波及面 | `<技能名>` |
| `dashboard` | 技能图谱可视化面板（D3.js 力导向图，支持排除节点与开关） | — |
| `clean` | **清理全部运行时产物**：一键清空生成的索引与缓存，恢复纯净源数据状态 | — |
| `index` | 重建索引：默认**元数据档**（一行一技能，仅定位/路由用）；`--full` 全量构建（缺完整 SKILL.md 中止 rc=2） | `--broad` `--full` |
| `update` | **增量索引**：只处理新增/变更/移除，未变项直接复用（日常用）；档位语义同 `index` | `--broad` `--full` |
| `add` | 把某个尚未索引的技能扫描并加入索引 | `<技能名\|路径>` |
| `roots` | **扫描根探针**：逐个候选根打印"存在?/找到几个技能"，用于解释"为什么是 0 项" | — |
| `import` | **导入外部技能清单**（平台不落盘时用）；支持 JSON / 每行 `name: 描述` | `<文件\|-\|文本>` |
| `list` | 列出全部技能/MCP + 笼统使用建议 | `--type skill\|mcp` `--with-ref` `--limit N` |
| `search` | 关键词排序检索（IDF 加权 + 命中名加权） | `<关键词>` `--top N` |
| `explain` | 单个技能的详细使用建议 | `<技能名>` |
| `detail` | 按需打印某技能完整 `SKILL.md` | `<技能名>` |
| `related` | 找相关的其他技能（读索引里存好的关系边 `depends_on`/`contains`/`overlap`/`similar`） | `<技能名>` `--top N` |
| `stats` | **覆盖口径**：按来源/类型、全文覆盖、缺什么、关系图 | — |
| `audit` | Token 账本：量化索引 vs 全文压缩率 | `--top N` |
| `bundle` | 生成 L0/L1/L2 分片 + 关系图 + `boot.md` | — |
| `doctor` | 技能体检：坏/空描述、缺触发词、超大技能、重名、路径缺失、图谱连通性 | `--graph` `--fix` |
| `export` | 多格式导出（`minjson` 含关系边） | `minjson\|md\|csv\|txt` |
| `catalog` | 生成可阅读的 `catalog.md` | — |
| `route` | 把用户话术路由到最合适的子命令 | `<用户话术>` |
| `help` | 列出全部命令及作用 | — |

## 意图路由（话术 → 命令）
用户不会说命令名，只会说人话。`route` 给出建议；也可直接按表路由：

| 话术示例 | 命令 |
|---|---|
| 开发一个用户认证需求 / 完整开发流程 / 从需求到上线 | `pipeline` |
| 排查线上慢查询并修复 / 线上 Bug 闭环 | `pipeline` |
| 合同双签后的后续流程是 / 业务状态机怎么走 | `chat` |
| 有哪些技能 / 技能清单 / 有多少个 | `list` |
| 这个任务该用哪个技能 / 帮我做X | `search` |
| 某技能怎么用 / 怎么触发 | `explain <技能名>` |
| 看某技能完整内容 / 全文 | `detail <技能名>` |
| 有没有相似的技能 | `related <技能名>` |
| 我新加了技能 / 改了描述 / 增量更新 | `update` |
| 重建 / 全量刷新索引 | `index` |
| 把这个技能加进索引 / 它还没索引 | `add <技能名\|路径>` |
| 为什么 0 项 / 扫不到技能 / 找不到技能目录 | `roots` |
| 导入技能清单 / 平台没落盘 / 我贴给你 | `import <文件\|-\|文本>` |
| 不想索引某个目录 / 跳过 node_modules / 忽略规则 | 写 `.skillignore`（见下） |
| 生成上传包 / 打包 / boot | `bundle` |
| 省了多少 token / 压缩率 | `audit` |
| 体检技能质量 | `doctor` |
| 导出 csv / json / markdown | `export <minjson\|md\|csv\|txt>` |
| 统计 / 来源分布 / 覆盖了哪些 | `stats` |
| 总览 / 生成文档 | `catalog` |
| 有哪些命令 / 帮助 | `help` |

兜底：有明确动作词 → 对应命令；只有任务描述 → `search`；其他 → `list`。

## 示例
```bash
python scripts/build_index.py index          # 首次：全量重建
python scripts/build_index.py index --broad  # 强制广域发现（扫 skills/*-skills 目录）
python scripts/build_index.py update         # 之后：增量（只处理变化）
python scripts/build_index.py add gif-sticker-maker   # 补一个未索引的技能
python scripts/build_index.py roots          # 诊断：扫的是哪些根、各找到几个
python scripts/build_index.py import "技能A: 描述A
技能B: 描述B"                                  # 平台不落盘时导入清单
python scripts/build_index.py list --type skill --limit 20
python scripts/build_index.py search 文档排版 --top 5
python scripts/build_index.py explain minimax-pdf
python scripts/build_index.py audit
python scripts/build_index.py bundle
python scripts/build_index.py doctor
python scripts/build_index.py export minjson
```

## 平台桥接（独立脚本 `scripts/platform_bridge.py`）
**场景**：平台/沙箱里宿主技能目录不可达（shell 被隔离在会话工作区，碰宿主路径被安全策略拦），
扫描根全落空 → `index` 必然 0 项。**这是运行环境不匹配，不是 indexer 的 bug**。
做法：走平台正规通道取清单（如 `skill_follow(list)` → `ensure_file` 暂存到技能 `inputs/`），
再落成镜像目录交给 indexer。

| 参数 | 说明 |
|------|------|
| `--src <文件\|->` | 清单来源；默认自动找 `inputs/platform_skills.json` 或 `inputs/*.json` |
| `--mirror <目录>` | 镜像目录；默认 `<技能根>/inputs/platform_mirror`（技能包内，不污染 `$HOME`） |
| `--mode full\|list` | **默认 `full`**：要求清单每个技能都带完整 SKILL.md 正文，缺一项就中止、不写索引。`list`=显式接受降级，只建"元数据索引"（非全量） |
| `--status` | 只看生命周期状态（清单/镜像/索引 在不在、是不是全量），不动任何文件 |
| `--force` | 清单没变也强制重落镜像 |
| `--clean` | 落镜像前先清掉上次镜像（按 manifest，只删它建的） |
| `--only index\|audit\|both\|none\|clean` | 只做哪一步；`clean`=只清理并退出 |
| `--python <路径>` | 调 indexer 用的解释器（默认当前解释器） |
| `--keep` | 保留已存在的镜像目录，不覆盖 |

```bash
python scripts/platform_bridge.py                 # 默认全量：找 inputs/*.json → 镜像 → index + audit
python scripts/platform_bridge.py --status        # 先看状态（含"是不是全量"）
python scripts/platform_bridge.py --src -         # 从 stdin 读清单
python scripts/platform_bridge.py --mode list     # 显式接受降级：只建"元数据索引"
python scripts/platform_bridge.py --force         # 清单没变也重落
python scripts/platform_bridge.py --only clean    # 清理上次镜像
```
桥接以子进程调用原 `build_index.py`（**不改 indexer 任何原有逻辑**），并设
`SKILL_INDEXER_SKILL_DIRS=<镜像>` + `SKILL_INDEXER_ONLY_DIRS=1`，**只扫镜像**，索引结果即平台的技能集。
清单指纹（name+description 的 sha1）记在 manifest `.platform_bridge.json` 里，没变就跳过重落 → **幂等**。

### 生命周期分层（镜像/索引易失，脚本持久）
| 层 | 内容 | 跨会话 |
|---|---|---|
| ① 索引产物 | `skill-index.json` / L1 / catalog … | ❌ 随沙箱回收，需重建 |
| ② 镜像目录 | `inputs/platform_mirror/` | ❌ 随沙箱回收，需重建 |
| ③ 脚本 + 文档 | `scripts/`、`SKILL.md`（平台技能包） | ✅ 持久资产 |
| ④ 清单暂存 | `inputs/platform_skills.json` | ⚠️ 大概率在，不保证（且通常**只有元数据**） |

**恢复元数据层 = 一条命令**：`python scripts/platform_bridge.py`；先 `--status` 看哪层没了。
注意：**完整 SKILL.md 全文不在 ①②④ 里** —— 要全量必须先回平台逐技能取回全文，全文丢了恢复不了。
**`index` / `update` / 任何读命令都自带自愈**：`list` / `search` / `stats` 这类命令发现**索引不存在会自动建一次**；
只要发现"只扫到本技能自己"且技能包里有平台清单，就自动落镜像并重扫。
**自愈跟随构建档位（两档，绝不静默冒充）**：`index` / `update` 默认建**元数据索引**（诚实标注"仅定位/路由用"，
不编压缩率数字）；`index --full` / `update --full` 才是**全量构建**——清单缺完整 SKILL.md 就**中止（rc=2）、不写索引**
并打印取全文步骤。读命令自愈默认同元数据档，`SKILL_INDEXER_NO_AUTO=1` 关闭，桥接子进程 `SKILL_INDEXER_BRIDGE_RUN=1` 防递归。
**日常不必手动敲 `index`**，它只在想强制全量重扫时才用。

> 诚实提醒：平台 `list` 只给 name/description，**不含完整 SKILL.md 正文**；拿它建出来的是"元数据索引"，
> `audit` 会**拒绝给出压缩率**（基线不是全文）。要真索引就按 `--mode full` 提示的步骤先取全文。
> 平台自带 `skill_follow(list/load)` 时，它就是"有哪些技能/怎么用"的实时索引；
> 桥接的价值在 `audit`（token 账本）/ `bundle`（L0/L1/L2）/ `export`（跨平台导出）这些 indexer 独有能力。

## 配置（扫描根）
`skill_indexer.config.json`（放在技能根目录）：
```json
{
  "skill_roots": ["/path/to/skills", "~/another-skills-dir"],
  "mcp_configs": ["/path/to/mcp.json"]
}
```
也可用环境变量：`SKILL_INDEXER_SKILL_DIRS` / `SKILL_INDEXER_MCP_CONFIGS`（用系统路径分隔符分隔）。

## 扫描忽略规则（`.skillignore`）
"扫描时跳过哪些目录"写在 `<技能根>/.skillignore`（gitignore 语法），**不用改代码**。
规则叠加顺序：**内置默认 → 本文件**，所以可以用 `!` 撤销内置默认。

内置默认（不写也在）：`.git` `.svn` `.hg` `node_modules` `__pycache__` `.venv` `venv` `.idea` `.vscode` `dist` `build`。
内置默认**故意不跳过隐藏目录**——`.minimax-skills` 这类隐藏包里装的是真技能，要收进来。

```
# 例
*/backup/
!dist                  # 撤销内置默认：不再跳过 dist
tmp/
```
`!` 只能把"整个被跳过的目录"重新纳入；目录一旦被跳过就不再往下走，里面的子项没法单独再纳入（与 git 行为一致）。
改完文件下次 `index`/`update` 即生效（按文件 mtime 自动重载）。
