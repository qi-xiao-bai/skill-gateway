#!/usr/bin/env python3
# export.py - 多格式导出：紧凑JSON / Markdown / CSV / 纯文本（供不同平台直接使用）
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: minjson 带上关系边(edges)，字段同样用短名
import csv
import io
import json

import index_store
from paths import out_path

# 紧凑 JSON 用短字段名，显著减小体积（供网页/上下文直接消费）
KEYMAP = {"name": "n", "description": "d", "triggers": "g",
          "type": "t", "path": "p", "has_references": "r", "source": "s"}
EDGE_KEYMAP = {"source": "s", "target": "t", "type": "k", "weight": "w"}


def _write(name, text):
    p = out_path(name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p, len(text)


def minjson(entries, edges=None):
    """紧凑单文件 JSON：不缩进 + 短字段名（约省四分之一的字符）。有边就一并带上（`edges`）。"""
    data = {"count": len(entries),
            "entries": [{KEYMAP[k]: e[k] for k in KEYMAP if k in e} for e in entries]}
    if edges:
        data["edges"] = [{EDGE_KEYMAP[k]: ed[k] for k in EDGE_KEYMAP if k in ed} for ed in edges]
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return _write("skill-index.min.json", text)


def markdown(entries, edges=None):
    lines = ["# 技能索引", "", f"共 {len(entries)} 项。", "",
             "| 名称 | 类型 | 描述 | 触发 |", "|---|---|---|---|"]
    for e in entries:
        desc = (e["description"] or "").replace("|", "\\|").replace("\n", " ")
        trg = "、".join(e.get("triggers") or []).replace("|", "\\|")
        lines.append(f"| {e['name']} | {e['type']} | {desc} | {trg} |")
    return _write("skills.index.md", "\n".join(lines) + "\n")


def csv_export(entries, edges=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "type", "has_references", "path", "description", "triggers"])
    for e in entries:
        w.writerow([e["name"], e["type"], e["has_references"], e["path"],
                    e["description"], "、".join(e.get("triggers") or [])])
    return _write("skills.index.csv", buf.getvalue())


def text(entries, edges=None):
    """纯文本导出：**复用 dense 索引格式**（一条一行、含触发词），与 `skill-index.llms.txt` 同构。"""
    return _write("skills.index.txt", index_store.dense_text(entries))


FORMATS = {"minjson": minjson, "md": markdown, "csv": csv_export, "txt": text}


def run(entries, fmt, edges=None):
    return FORMATS[fmt](entries, edges)
