#!/usr/bin/env python3
"""fill_description_zh.py - 批量把 description_zh 写进技能 SKILL.md frontmatter。

背景：dashboard 双语对照与中文检索依赖索引里的 description_zh 字段，只有
SKILL.md frontmatter 写了双语描述的技能才有；平台技能库绝大多数没写，
切中文后描述原样显示英文（"翻译不能用"的根因是缺数据，不是 UI 坏了）。

用法（两步，翻译由 LLM 完成，本脚本只负责落盘）：
  1. 导出待翻译清单：  python scripts/fill_description_zh.py --export todo.json
  2. 让 LLM 把 todo.json 里的 description 译成中文 description_zh（保持 key 不变），
     然后回填：      python scripts/fill_description_zh.py todo.json [--force] [--dry-run]

--export 生成的 JSON：{"<技能名>": {"description": "<现英文/原文描述>", "path": "<SKILL.md 路径>"}}
回填时把每项补上 "description_zh": "<中文描述>" 即可。
--force 会覆盖已存在的 description_zh；默认跳过已写的。--dry-run 只预览不写。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner  # noqa: E402


def _frontmatter_span(text):
    """返回 (fm_start, fm_end) frontmatter 围栏位置（含 --- 行），无 frontmatter 返回 None。"""
    m = re.match(r"^---\s*\n", text)
    if not m:
        return None
    end = text.find("\n---", m.end())
    if end < 0:
        return None
    return m.start(), end + 1  # end 指向闭合 --- 行首


def _yaml_dq(s):
    """转成可放 YAML 双引号标量的安全字符串。"""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def fill_one(sk_path, zh, force=False):
    with open(sk_path, encoding="utf-8") as f:
        text = f.read()
    span = _frontmatter_span(text)
    if not span:
        return "skip(no-frontmatter)"
    start, end = span
    fm = text[start:end]
    if re.search(r"^description_zh\s*:", fm, re.M):
        if not force:
            return "skip(has-description_zh)"
        fm = re.sub(r"^description_zh\s*:.*$", "description_zh: " + _yaml_dq(zh), fm, flags=re.M)
    else:
        fm = fm.rstrip("\n") + "\ndescription_zh: " + _yaml_dq(zh) + "\n"
    new_text = fm + text[end:]
    if new_text == text:
        return "skip(unchanged)"
    with open(sk_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    return "written"


def main():
    ap = argparse.ArgumentParser(description="批量回填 description_zh 到 SKILL.md frontmatter")
    ap.add_argument("mapping", nargs="?", help="LLM 翻译后的 JSON（含 description_zh 键）")
    ap.add_argument("--export", metavar="OUT", help="导出待翻译清单到 OUT 文件后退出")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 description_zh")
    ap.add_argument("--dry-run", action="store_true", help="只预览要做的改动，不写文件")
    a = ap.parse_args()

    entries = None
    try:
        import index_store
        entries = [e for e in (index_store.load_index() or [])
                   if e.get("type") == "skill" and not e.get("excluded")]
    except Exception:
        entries = None
    if not entries:
        entries = [e for e in scanner.scan_skills() if e.get("type") == "skill"]
    if a.export:
        # 口径 = 索引可见条目（off-list/blocked 不进待译清单——它们不在检索面）
        todo = {
            e["name"]: {"description": e.get("description", ""), "path": e.get("path", "")}
            for e in entries if not e.get("description_zh")
        }
        with open(a.export, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=2)
        print(f"[fill_description_zh] 待补 description_zh 共 {len(todo)} 项 -> {a.export}")
        return

    if not a.mapping:
        ap.error("需要 mapping JSON 或 --export")
    with open(a.mapping, encoding="utf-8") as f:
        data = json.load(f)
    name_to_entry = {e["name"].lower(): e for e in entries}
    done = miss = 0
    for name, item in data.items():
        zh = (item or {}).get("description_zh") if isinstance(item, dict) else item
        if not zh:
            continue
        e = name_to_entry.get(str(name).lower())
        if not e:
            print(f"[miss] {name}: 索引中无此技能")
            miss += 1
            continue
        sk = os.path.join(e["path"], "SKILL.md")
        if not os.path.isfile(sk):
            print(f"[miss] {name}: {sk} 不存在")
            miss += 1
            continue
        act = "DRY " if a.dry_run else ""
        res = "written" if a.dry_run else fill_one(sk, zh, force=a.force)
        if res == "written":
            done += 1
        print(f"[{act}{res}] {name} -> {sk}")
    print(f"[fill_description_zh] 完成：写入 {done}，未匹配 {miss}")


if __name__ == "__main__":
    main()
