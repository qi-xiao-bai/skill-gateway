#!/usr/bin/env python3
# importer.py - 把"外部技能清单"导入索引：当平台不把技能落盘（沙箱/仅注入提示词）时使用
# created 2026-09-16 qjl
import json
import os
import re
import sys

import skillmd


def _clean_name(s):
    s = (s or "").strip().strip("`").strip('"').strip("'")
    return s.strip()


def _item_to_entry(item):
    """把一条清单项（dict / 字符串）转成索引记录。"""
    if isinstance(item, dict):
        name = _clean_name(item.get("name") or item.get("title")
                           or item.get("skill") or item.get("skill_name"))
        desc = (item.get("description") or item.get("desc")
                or item.get("summary") or item.get("when") or "")
        path = item.get("path") or item.get("dir") or item.get("location") or ""
        trig = item.get("triggers") or item.get("trigger") or []
        if isinstance(trig, str):
            trig = [t for t in re.split(r"[、,，;；/|]+", trig) if t.strip()]
        if not name:
            return None
        return {"name": name, "description": desc.strip(), "path": path,
                "triggers": [t.strip() for t in trig if t and t.strip()]}
    if isinstance(item, str):
        return _line_to_entry(item)
    return None


def _line_to_entry(line):
    """把一行文本转成 {name, description, path, triggers}。支持 name: desc / name | desc / name — desc。"""
    raw = line.rstrip()
    s = raw.strip()
    s = re.sub(r"^[-*+•·]\s*", "", s)              # 去列表符号
    s = re.sub(r"^\d+[.)、]\s*", "", s)             # 去序号
    s = s.strip()
    if not s or s.startswith("#"):
        return None
    name = desc = ""
    for sep in (":", "：", "|", "—", "–", " - "):
        if sep in s:
            left, _, right = s.partition(sep)
            if left.strip():
                name, desc = left.strip(), right.strip()
                break
    if not name:
        name = s
    name = _clean_name(name)
    if not name:
        return None
    # 触发词不在这里单独抽：`to_entries` 统一调用 skillmd.extract_triggers(desc)，
    # 行内的「触发词：…」会被它一并识别，避免两处解析规则漂移。
    return {"name": name, "description": desc, "path": "", "triggers": []}


def parse_source(source):
    """source: 文件路径 / '-' 读 stdin / 直接文本。返回 [entry-like dict]。"""
    if source == "-":
        text = sys.stdin.read()
        kind = "text"
    elif os.path.isfile(source):
        with open(source, encoding="utf-8", errors="replace") as f:
            text = f.read()
        kind = "json" if source.lower().endswith(".json") else "text"
    else:
        text = source
        kind = "json" if text.lstrip().startswith(("{", "[")) else "text"

    if kind == "json":
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if data is not None:
            if isinstance(data, dict):
                for k in ("skills", "entries", "items", "list", "available_skills"):
                    if isinstance(data.get(k), list):
                        data = data[k]
                        break
                else:
                    data = [data]
            if isinstance(data, list):
                return [e for e in (_item_to_entry(i) for i in data) if e]

    out = []
    for line in text.splitlines():
        e = _line_to_entry(line)
        if e:
            out.append(e)
    return out


def to_entries(items, source_tag="import"):
    """转成完整的索引记录（type=skill）。"""
    out = []
    for it in items:
        desc = it.get("description") or ""
        trig = it.get("triggers") or skillmd.extract_triggers(desc)
        out.append({
            "name": it["name"],
            "description": desc,
            "triggers": trig,
            "type": "skill",
            "path": it.get("path") or "",
            "has_references": False,
            "source": source_tag,
            "mtime": 0,
            "visibility": "ready",
        })
    return out


def parse_source_raw(source):
    """同 parse_source 的分派（文件/-/文本），但 JSON 项**原样返回**，保留
    status/category/platform/agents 等扩展字段——供 agent-index 生成权威清单。"""
    if source == "-":
        text, kind = sys.stdin.read(), "text"
    elif os.path.isfile(source):
        with open(source, encoding="utf-8", errors="replace") as f:
            text = f.read()
        kind = "json" if source.lower().endswith(".json") else "text"
    else:
        text = source
        kind = "json" if text.lstrip().startswith(("{", "[")) else "text"

    if kind == "json":
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if data is not None:
            if isinstance(data, dict):
                for k in ("skills", "entries", "items", "list", "available_skills"):
                    if isinstance(data.get(k), list):
                        data = data[k]
                        break
                else:
                    data = [data]
            if isinstance(data, list):
                return [it for it in data if isinstance(it, (dict, str))]

    out = []
    for line in text.splitlines():
        e = _line_to_entry(line)
        if e:
            out.append(e)
    return out


def to_available_list(items, agent_name=None, old_doc=None):
    """把平台/Agent 绑定清单转成权威可用清单 doc（inputs/available_skills.json 的内容）。
    - agent_name 写入顶层 current_agent，并作为每项默认 agents；
    - 每项保留 name/description/triggers/path/status/category/platform 原值；
    - 幂等：新清单缺某字段（category/platform/description 等）时用旧清单同名条目的值补位；新值优先。
    """
    old_map, old_order = {}, []
    if isinstance(old_doc, dict):
        for it in old_doc.get("skills") or []:
            if isinstance(it, dict) and it.get("name"):
                k = str(it["name"]).strip().lower()
                if k not in old_map:
                    old_order.append(k)
                old_map[k] = it
    skills = []
    imported = set()
    for it in items:
        if isinstance(it, str):
            it = _line_to_entry(it) or {}
        if not isinstance(it, dict):
            continue
        name = _clean_name(it.get("name") or it.get("skill")
                           or it.get("skill_name") or it.get("title"))
        if not name:
            continue
        old = old_map.get(name.lower(), {})
        rec = {"name": name}
        for k in ("description", "desc", "summary", "triggers", "path",
                  "status", "category", "platform", "description_zh"):
            v = it.get(k)
            if v in (None, ""):
                v = old.get(k)
            if v not in (None, ""):
                rec[k] = v
        agents = list(it.get("agents") or [])
        if not agents:
            agents = list(old.get("agents") or [])
        if agent_name and agent_name not in agents:
            agents.append(agent_name)  # 并集：合并导入，不清其他环境的绑定
        if agents:
            rec["agents"] = agents
        imported.add(name.lower())
        skills.append(rec)
    # 未被本次导入的旧条目：有归属（agents 字段）的保留——共享清单属于所有
    # 智能体环境，合并不裁剪；无归属（无 agents 字段）的视为陈旧残留清扫
    for k in old_order:
        if k not in imported:
            old_it = old_map[k]
            if old_it.get("agents"):
                skills.append(old_it)
    doc = {}
    if agent_name:
        doc["current_agent"] = agent_name
    doc["skills"] = skills
    return doc
