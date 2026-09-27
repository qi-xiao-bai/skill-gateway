#!/usr/bin/env python3
# route.py - 意图路由：把用户话术映射到最合适的子命令（用户没说命令名时的兜底）
# 路由表不在代码里写死：规则是数据文件 scripts/route_rules.json（可按环境增删/定制），
# 本文件只负责加载与执行；数据文件缺失/损坏时退回内置最小集，路由永不崩。
# created 2026-09-16 qjl
# updated 2026-09-27 qjl: 规则外置为 route_rules.json + 收紧过宽关键词
import json
import os
import re

_RULES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "route_rules.json"
)

# 内置最小兜底集（仅当数据文件缺失/损坏时使用；正常情况以 route_rules.json 为准）
_FALLBACK = {
    "default": "list",
    "pipeline_cues": ["开发", "排查", "重构", "实现", "端到端"],
    "question_cues": ["？", "?", "怎么", "如何", "为什么", "哪"],
    "rules": [
        ["help", ["哪些命令", "帮助", "\\bhelp\\b"]],
        ["search", ["检索", "搜索", "哪个技能", "找.*技能"]],
        ["list", ["有哪些", "技能清单", "全部技能"]],
        ["chat", ["怎么做", "怎么处理", "\\?|？"]],
    ],
}

_RULES_CACHE = {"key": None, "data": None}


def _load_rules():
    """读 route_rules.json（按 mtime 缓存）。缺失/损坏/格式不符退回内置最小集。"""
    try:
        mt = os.path.getmtime(_RULES_FILE)
    except OSError:
        mt = None
    key = ("file", mt)
    if _RULES_CACHE["key"] == key and _RULES_CACHE["data"] is not None:
        return _RULES_CACHE["data"]
    data = None
    if mt is not None:
        try:
            with open(_RULES_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("rules"), list):
                valid = all(
                    isinstance(r, (list, tuple))
                    and len(r) == 2
                    and isinstance(r[0], str)
                    and isinstance(r[1], list)
                    and all(isinstance(p, str) for p in r[1])
                    for r in raw["rules"]
                )
                if valid:
                    data = raw
        except Exception:
            data = None
    if data is None:
        data = _FALLBACK
    _RULES_CACHE["key"] = key
    _RULES_CACHE["data"] = data
    return data


def extract_target_skill(text: str) -> str:
    """从话术中提取目标技能名（如 @crm-copilot 或 使用「crm-copilot」技能）。
    「调用 xxx」必须带"技能"后缀才算点名——否则"调用crm系统查订单"会把
    crm 当技能名。"""
    if not text:
        return ""
    m = re.search(r"@([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"使用\s*[「【]?([a-zA-Z0-9_-]+)[」【]?\s*技能", text)
    if m:
        return m.group(1)
    m = re.search(r"调用\s*[「【]?([a-zA-Z0-9_-]+)[」【]?\s*技能", text)
    if m:
        return m.group(1)
    return ""


def clean_query(text: str) -> str:
    """剥离 @技能名 或 使用「技能」的前缀，提取纯业务任务/问题内容。"""
    if not text:
        return ""
    q = re.sub(r"^@([a-zA-Z0-9_-]+)\s*", "", text)
    q = re.sub(r"^使用\s*[「【]?([a-zA-Z0-9_-]+)[」【]?\s*技能[：:]?\s*", "", q)
    q = re.sub(r"^调用\s*[「【]?([a-zA-Z0-9_-]+)[」【]?\s*技能[：:]?\s*", "", q)
    return q.strip() or text.strip()


def route(text, top=3):
    """返回 (首选命令, 命中命令列表)。无命中时按数据文件里的
    pipeline_cues / question_cues 兜底到 pipeline / chat，否则用 default。"""
    if not text:
        return _load_rules()["default"], []
    data = _load_rules()
    hits = []
    for cmd, pats in data["rules"]:
        for p in pats:
            if re.search(p, text, re.I):
                if cmd not in hits:
                    hits.append(cmd)
                break

    # 若提到具体技能，补充 explain 备选
    target = extract_target_skill(text)
    if target and "explain" not in hits:
        hits.append("explain")

    if not hits:
        cues = data.get("pipeline_cues", [])
        if any(w in text for w in cues):
            return "pipeline", ["pipeline", "chat", "search"]
        q_cues = data.get("question_cues", [])
        if any(w in text for w in q_cues):
            return "chat", ["chat", "search"]
        return data.get("default", "list"), []
    return hits[0], hits[:top]
