#!/usr/bin/env python3
# hit_ledger.py - 命中率账本：记录检索/编排/采纳事件（jsonl 追加），聚合
# 能力缺口（零命中 query）、技能命中/采纳榜、僵尸技能报表。
# 账本是旁路：record_event 任何失败静默吞掉，绝不影响检索主流程；
# SKILL_GATEWAY_LEDGER=0 关闭；output/ 不进交付包（pack 排除），不出本机。
import json
import os
import time

from paths import out_path

LEDGER_MAX_BYTES = 2_000_000   # 超过即滚动截断
KEEP_RATIO = 0.66              # 截断后保留最新的比例


def enabled():
    return (os.environ.get("SKILL_GATEWAY_LEDGER", "") or "").strip().lower() \
        not in ("0", "false", "no", "off")


def ledger_path():
    return out_path("hit-ledger.jsonl")


def record_event(kind, query, hits, extra=None):
    """追加一条事件。hits 为检索的 [(score, entry, maximal)] 或编排的 [{"name":...}]；
    extra 可带 {"agent":...}/{"skill":...}。任何失败静默吞掉。"""
    if not enabled():
        return
    try:
        top = []
        for item in (hits or [])[:5]:
            if isinstance(item, dict):
                top.append({"name": item.get("name", ""), "score": None, "evidence": []})
            else:
                score, e, maximal = item
                top.append({"name": e.get("name", ""), "score": round(float(score), 1),
                            "evidence": list((maximal or [])[:5])})
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
               "query": query or "", "n_hits": len(hits or []), "top": top}
        if extra:
            rec.update(extra)
        p = ledger_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _maybe_truncate(p)
    # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
    except Exception:
        pass


def _maybe_truncate(p):
    try:
        if os.path.getsize(p) <= LEDGER_MAX_BYTES:
            return
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(lines[-int(len(lines) * KEEP_RATIO):])
        os.replace(p + ".tmp", p)
    # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
    except Exception:
        pass


def load_events(limit=None):
    p = ledger_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []
    return events[-limit:] if limit else events


def ledger_report(events, skill_names=None):
    """聚合报表：能力缺口（零命中 query 按次数）、技能命中榜、采纳榜、僵尸技能
    （skill_names 提供索引技能全集时才输出）。"""
    if skill_names is None:
        skill_names = set()
    n_search = sum(1 for e in events if e.get("kind") in ("search", "chat", "pipeline"))
    gap, hit_cnt, adopt_cnt = {}, {}, {}
    for e in events:
        k = e.get("kind")
        if k in ("search", "chat", "pipeline"):
            if not e.get("n_hits"):
                q = (e.get("query") or "").strip()
                if q:
                    gap[q] = gap.get(q, 0) + 1
            for t in e.get("top") or []:
                nm = t.get("name")
                if nm:
                    hit_cnt[nm] = hit_cnt.get(nm, 0) + 1
        elif k == "adopt":
            nm = (e.get("skill") or e.get("query") or "").strip()
            if nm:
                adopt_cnt[nm] = adopt_cnt.get(nm, 0) + 1

    lines = [f"# 命中率账本（{len(events)} 条事件，其中检索/编排 {n_search} 条）", ""]

    lines.append("## 能力缺口（零命中 query，按次数）——治理工单：补技能或改绑定")
    if gap:
        for q, c in sorted(gap.items(), key=lambda x: (-x[1], x[0]))[:10]:
            lines.append(f"  {c:3d}  {q}")
    else:
        lines.append("  （无）")

    lines.append("")
    lines.append("## 技能命中榜（Top 10）")
    if hit_cnt:
        for nm, c in sorted(hit_cnt.items(), key=lambda x: (-x[1], x[0]))[:10]:
            lines.append(f"  {c:3d}  {nm}")
    else:
        lines.append("  （无）")

    lines.append("")
    lines.append("## 采纳榜（detail 显式取用，Top 10）")
    if adopt_cnt:
        for nm, c in sorted(adopt_cnt.items(), key=lambda x: (-x[1], x[0]))[:10]:
            lines.append(f"  {c:3d}  {nm}")
    else:
        lines.append("  （无）")

    if skill_names:
        zombie = sorted(skill_names - set(hit_cnt))
        lines.append("")
        lines.append(f"## 僵尸技能（索引中有、从未被检索命中：{len(zombie)} 个）")
        if zombie:
            lines.append("  " + "、".join(zombie[:20]) + ("…" if len(zombie) > 20 else ""))
    return "\n".join(lines)
