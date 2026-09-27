# inputs/ —— 易失期运行时缓存

本目录放**平台侧暂存**和**桥接生成的镜像**。两者都属于**易失层**（随沙箱回收消失）。
注意：**能一条命令重建的只有元数据**（清单/镜像/索引）——**完整 SKILL.md 全文不在这里**，
全文丢了只能回平台重新取。不要把重要东西只放在这里。

| 路径 | 是什么 | 谁写 |
|---|---|---|
| `platform_skills.json` | 平台技能清单（④ 平台侧暂存，大概率在但**不保证**） | 平台通道（`skill_follow(list)` → `ensure_file`） |
| `platform_mirror/` | 清单落成的镜像目录（② 易失），每个技能一个 `<name>/SKILL.md` | `scripts/platform_bridge.py` |
| `platform_mirror/.platform_bridge.json` | 镜像清单 + 来源指纹（判断镜像是否过期） | `scripts/platform_bridge.py` |

```bash
python scripts/platform_bridge.py --status   # 先看：清单/镜像/索引 哪层没了、是不是全量
python scripts/platform_bridge.py            # 重建镜像 + 索引（默认要求清单带完整 SKILL.md 正文）
python scripts/platform_bridge.py --mode list    # 只有元数据时，显式接受降级（得到"元数据索引"）
python scripts/platform_bridge.py --only clean   # 清理镜像（只删它建的）
```

> **持久的资产是 `scripts/` 里的脚本和文档，不是这里的缓存。**
> 清单若也被清了：让平台通道重跑一次 `skill_follow(list)` 存回本目录即可。

---

## 清单格式

- 文件名建议：`platform_skills.json`（也会自动认本目录下任意 `*.json`）
- 也支持每行 `name: 描述` 的纯文本清单

清单是 JSON 时，形如：

```json
{"skills": [
  {"name": "ui-ux-pro-max", "description": "前端 UI/UX 设计。触发词：界面、排版、配色。"},
  {"name": "tencent-docx",  "description": "生成/美化 Word。触发词：写文档、排版。"}
]}
```

若平台还能给到**完整 SKILL.md 正文**，把它放进 `skill_md` / `content` / `body` 字段。
**桥接默认要求每个技能都带正文**（`--mode full`）——缺任何一项都会中止、不写索引；
只有显式 `--mode list` 才接受"仅元数据"降级，那时得到的是**元数据索引（非全量）**，`audit` 会拒绝给出压缩率。
（`index` / `update` 同理分两档：默认元数据档仅定位用，`index --full` 才要求全量、缺正文中止 rc=2。）
