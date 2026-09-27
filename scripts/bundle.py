#!/usr/bin/env python3
# bundle.py - 生成 L0/L1/L2 三层分片 + 关系图 + boot.md（可上传数字员工网页）
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 增出 skills.edges.json（技能关系图）
import json

import index_store
from paths import out_path


def boot_md(n, l0len, l1len, n_edges):
    return f"""# 技能启动引导（boot）

本平台共索引 **{n}** 个技能 / MCP。**启动时只加载 L1 索引**（`skills.L1.txt`，{l1len} 字符，
格式见其文件头 `#Format: skill-index/dense-v1`：一条一行）。
不要一次性把所有 SKILL.md 读进上下文——那样又慢又占上下文。

## 用法
1. 先读 L1（`名字[标签]: 描述 | 触发: …`），判断该用哪个技能。
2. 真正要用某个技能时，再按需加载它的 SKILL.md 全文（清单见 `skills.L2.map.json`）。
3. 想更省，可只加载 L0（`skills.L0.txt`，仅名字，{l0len} 字符）。
4. 需要"技能之间谁和谁相关"时，查 `skills.edges.json`（{n_edges} 条关系边，键为
   `skill:<名>` / `mcp:<名>`）：`overlap`=做的事像，`family`=同族命名，`depends_on`=功能依赖（有向），
   `contains`=父子包含（有向），`similar`=内容相似。不用时可以不加载。

## 硬规矩
索引里**只放「定位用」摘要**，不要把 SKILL.md 全文抄进索引——那等于把分层白做了。
"""


def write_bundle(entries, edges=None):
    l0 = index_store.l0_text(entries)
    l1 = index_store.dense_text(entries)  # 带 #Format 头块，自成一份可独立交付的说明
    if edges is None:
        edges = index_store.read_edges() or []

    def l2_target(e):
        """L2 指向全文路径；无本地文件（import/平台记录）留空，不写成相对 cwd 的 "SKILL.md"。"""
        if e["type"] != "skill":
            return e["path"]
        return index_store.skill_file(e)

    l2map = {e["name"]: l2_target(e) for e in entries}
    with open(out_path("skills.L0.txt"), "w", encoding="utf-8") as f:
        f.write(l0)
    with open(out_path("skills.L1.txt"), "w", encoding="utf-8") as f:
        f.write(l1)
    with open(out_path("skills.L2.map.json"), "w", encoding="utf-8") as f:
        json.dump(l2map, f, ensure_ascii=False, separators=(",", ":"))
    with open(out_path("skills.edges.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"count": len(edges), "edges": edges},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    with open(out_path("boot.md"), "w", encoding="utf-8") as f:
        f.write(boot_md(len(entries), len(l0), len(l1), len(edges)))
    return {
        "L0 仅名字": (out_path("skills.L0.txt"), f"{len(l0):,} 字符"),
        "L1 dense 索引": (out_path("skills.L1.txt"), f"{len(l1):,} 字符"),
        "L2 全文清单": (out_path("skills.L2.map.json"), f"{len(l2map)} 条"),
        "关系图": (out_path("skills.edges.json"), f"{len(edges)} 条边"),
        "启动引导": (out_path("boot.md"), ""),
    }
