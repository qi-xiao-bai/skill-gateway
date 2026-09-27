#!/usr/bin/env python3
# doctor.py - 技能体检：坏/空描述、路径缺失、重名（硬）；缺触发词、超大技能（软）
# created 2026-09-16 qjl
import os
from collections import Counter

import index_store

BAD_MARKERS = {"", ">", "|", ">-", "|-", ">+", "|+"}
BIG = 20000          # SKILL.md 超过此大小建议把细节拆到 references/
SOFT = {"缺触发词", "超大技能"}


def _desc_weight(d):
    """描述信息量权重：CJK 字符按 2 计（一字中文的信息量约抵两字符英文）。
    「广告创意、设计方案与文字分镜」这种 14 字的具体描述，
    不应被按英文校准的 15 字符阈值误判为坏描述。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in d)


def diagnose(entries):
    findings = []
    counts = {}
    for e in entries:
        # 描述/触发词是 SKILL.md 的约定，只对技能检查；MCP 的 description 由扫描器生成、
        # 按设计永远没有触发词，检查它们只会产生噪声。
        if e["type"] == "skill":
            d = (e.get("description") or "").strip()
            if d in BAD_MARKERS or _desc_weight(d) < 15:
                findings.append(("坏/空描述", e["name"], f"description=「{d[:30]}」"))
            elif not (e.get("triggers") or []):
                findings.append(("缺触发词", e["name"], "描述里没有可识别的触发词"))
            p = index_store.skill_file(e)
            if p:
                if not os.path.isfile(p):
                    findings.append(("路径缺失", e["name"], f"找不到 {p}"))
                elif os.path.getsize(p) > BIG:
                    findings.append(("超大技能", e["name"],
                                     f"{os.path.getsize(p):,} 字符，建议把细节拆到 references/"))
        # 与扫描去重同口径按小写计数：github/GitHub 是同一技能，不该报重名
        counts[e["name"].lower()] = counts.get(e["name"].lower(), 0) + 1
    for n, c in counts.items():
        if c > 1:
            findings.append(("重名", n, f"出现 {c} 次"))
    return findings


def report_text(entries, sample=10):
    f = diagnose(entries)
    hard = [x for x in f if x[0] not in SOFT]
    soft = [x for x in f if x[0] in SOFT]
    lines = [f"# 技能体检（doctor）：{len(entries)} 项",
             f"硬问题 {len(hard)} 条（坏/空描述、路径缺失、重名）｜软建议 {len(soft)} 条（缺触发词、超大技能）"]
    lines.append("\n## 硬问题（建议修）")
    if hard:
        for kind, name, detail in hard:
            lines.append(f"- [{kind}] {name}: {detail}")
    else:
        lines.append("- 无")
    lines.append("\n## 软建议（可选，提升被发现率 / 加载速度）")
    if soft:
        cnt = Counter(k for k, _, _ in soft)
        lines.append("- 统计: " + ", ".join(f"{k}={v}" for k, v in cnt.most_common()))
        shown = {}
        for kind, name, detail in soft:
            shown.setdefault(kind, [])
            if len(shown[kind]) < sample:
                shown[kind].append(f"{name}")
        for kind, items in shown.items():
            lines.append(f"- {kind} 示例（前 {len(items)}）: " + "、".join(items))
    else:
        lines.append("- 无")
    return "\n".join(lines)


def diagnose_graph(entries, edges):
    """图谱质量体检：孤立节点、边类型分布、有向边方向冲突、高密度节点、悬空父子边。"""
    import retrieval

    by_id = retrieval.build_id_map(entries)

    # 1. 孤立节点
    connected = set()
    for ed in edges:
        connected.add(ed.get("source", ""))
        connected.add(ed.get("target", ""))
    isolated = [e for e in entries if retrieval.entry_id(e) not in connected]

    # 2. 边类型分布
    type_counts = Counter(ed.get("type", "?") for ed in edges)

    # 3. 有向边方向冲突 (depends_on 双向重复)
    directed_pairs = set()
    reverse_conflicts = []
    for ed in edges:
        if ed.get("type") == "depends_on":
            pair = (ed["source"], ed["target"])
            rev = (ed["target"], ed["source"])
            if rev in directed_pairs:
                reverse_conflicts.append((ed["source"], ed["target"]))
            directed_pairs.add(pair)

    # 4. 高密度节点
    deg = Counter()
    for ed in edges:
        deg[ed.get("source", "")] += 1
        deg[ed.get("target", "")] += 1
    avg_deg = sum(deg.values()) / len(deg) if deg else 0
    high_density = []
    if deg:
        high_density = [(nid, d) for nid, d in deg.items() if d > avg_deg * 3 and d >= 5]

    # 5. contains 悬空
    contains_orphans = []
    for ed in edges:
        if ed.get("type") == "contains":
            if ed.get("source") not in by_id or ed.get("target") not in by_id:
                contains_orphans.append(ed)

    return {
        "by_id": by_id,
        "isolated": isolated,
        "type_counts": type_counts,
        "reverse_conflicts": reverse_conflicts,
        "deg": deg,
        "avg_deg": avg_deg,
        "high_density": high_density,
        "contains_orphans": contains_orphans,
    }


def graph_report_text(entries, edges, diag=None):
    """格式化输出图谱质量审查报告。"""
    diag = diag or diagnose_graph(entries, edges)
    by_id = diag["by_id"]
    lines = ["\n## 图谱质量审查\n"]

    # 1. 孤立节点
    isolated = diag["isolated"]
    if isolated:
        lines.append(f"### 孤立节点（{len(isolated)} 个）")
        lines.append("没有任何关系边连接，可能缺少描述或触发词：\n")
        for e in isolated[:15]:
            lines.append(f"- **{e['name']}**: {(e.get('description') or '')[:60]}")
        if len(isolated) > 15:
            lines.append(f"- ... 还有 {len(isolated) - 15} 个")
        lines.append("")
    else:
        lines.append("- 无孤立节点 ✓\n")

    # 2. 边类型分布
    lines.append("### 边类型分布\n")
    for t, c in diag["type_counts"].most_common():
        directed = "有向" if t in ("depends_on", "contains") else "无向"
        lines.append(f"- {t}: {c} 条 ({directed})")
    lines.append("")

    # 3. 有向边方向冲突
    reverse_conflicts = diag["reverse_conflicts"]
    if reverse_conflicts:
        lines.append(f"### 有向边方向冲突（{len(reverse_conflicts)} 对）\n")
        for s, t in reverse_conflicts[:10]:
            sn = by_id.get(s, {}).get("name", s)
            tn = by_id.get(t, {}).get("name", t)
            lines.append(f"- {sn} ↔ {tn}（双向 depends_on，应检查是否合理）")
        lines.append("")
    else:
        lines.append("- 有向边方向一致 ✓\n")

    # 4. 高密度节点
    high_density = diag["high_density"]
    avg_deg = diag["avg_deg"]
    if high_density:
        lines.append(f"### 高密度节点（>{avg_deg * 3:.1f} 条边，{len(high_density)} 个）\n")
        for nid, d in sorted(high_density, key=lambda x: -x[1])[:10]:
            ne = by_id.get(nid)
            nname = ne["name"] if ne else nid
            lines.append(f"- **{nname}**: {d} 条边（平均 {avg_deg:.1f}）")
        lines.append("")
    else:
        lines.append("- 边密度分布均匀 ✓\n")

    # 5. contains 悬空
    contains_orphans = diag["contains_orphans"]
    if contains_orphans:
        lines.append(f"### contains 边悬空（{len(contains_orphans)} 条）\n")
        for ed in contains_orphans[:10]:
            lines.append(f"- {ed.get('source')} → {ed.get('target')}")
        lines.append("")
    else:
        lines.append("- contains 边均有效 ✓\n")

    return "\n".join(lines)


def repair_graph(entries, edges, diag=None):
    """自动修复图谱缺陷：消除方向冲突、剪枝高密度节点冗余边、为孤立节点补偿连接。
    返回: (fixed_edges, fix_count, log_lines)
    """
    import retrieval

    diag = diag or diagnose_graph(entries, edges)
    fixed_edges = list(edges)
    fix_count = 0
    logs = ["## 图谱自动修复\n"]

    # 修复1: 方向冲突 — 保留权重较高的方向
    reverse_conflicts = diag["reverse_conflicts"]
    if reverse_conflicts:
        removed_pairs = set()
        for s, t in reverse_conflicts:
            fwd = next(
                (ed for ed in fixed_edges if ed.get("type") == "depends_on" and ed.get("source") == s and ed.get("target") == t),
                None,
            )
            rev = next(
                (ed for ed in fixed_edges if ed.get("type") == "depends_on" and ed.get("source") == t and ed.get("target") == s),
                None,
            )
            if fwd and rev:
                if fwd.get("weight", 0) >= rev.get("weight", 0):
                    if rev in fixed_edges:
                        fixed_edges.remove(rev)
                    removed_pairs.add((t, s))
                else:
                    if fwd in fixed_edges:
                        fixed_edges.remove(fwd)
                    removed_pairs.add((s, t))
                fix_count += 1
        logs.append(f"- 去除 {len(removed_pairs)} 条方向冲突边（保留权重较高的方向）")

    # 修复2: 清理悬空的 contains 边
    contains_orphans = diag.get("contains_orphans", [])
    if contains_orphans:
        removed_orphans = 0
        for ed in contains_orphans:
            if ed in fixed_edges:
                fixed_edges.remove(ed)
                removed_orphans += 1
        if removed_orphans:
            logs.append(f"- 清理 {removed_orphans} 条悬空的 contains 关系边")
            fix_count += removed_orphans

    # 修复3: 高密度节点剪枝
    deg = diag["deg"]
    if deg:
        avg_deg = sum(deg.values()) / len(deg) if deg else 1
        threshold = max(avg_deg * 3, 5)
        deg2 = Counter()
        for ed in fixed_edges:
            deg2[ed.get("source", "")] += 1
            deg2[ed.get("target", "")] += 1
        high = [(nid, d) for nid, d in deg2.items() if d > threshold]
        pruned = 0
        for nid, d in high:
            node_edges = [
                (i, ed)
                for i, ed in enumerate(fixed_edges)
                if ed.get("source") == nid or ed.get("target") == nid
            ]
            node_edges.sort(key=lambda x: x[1].get("weight", 0))
            to_remove = d - int(threshold)
            for idx, (i, ed) in enumerate(node_edges):
                if idx >= to_remove:
                    break
                if ed in fixed_edges:
                    fixed_edges.remove(ed)
                    pruned += 1
            if pruned > 50:
                break
        if pruned:
            logs.append(f"- 剪枝 {pruned} 条低权重边（高密度节点降度）")
            fix_count += pruned

    # 修复4: 孤立节点重连
    isolated = diag["isolated"]
    if isolated:
        connected2 = set()
        for ed in fixed_edges:
            connected2.add(ed.get("source", ""))
            connected2.add(ed.get("target", ""))
        still_isolated = [e for e in entries if retrieval.entry_id(e) not in connected2]
        if still_isolated:
            new_edges = retrieval._overlap_edges(entries, top_k=1, min_j=0.05)
            added = 0
            for u, v, w in new_edges:
                if u not in connected2 or v not in connected2:
                    if not any(ed.get("source") == u and ed.get("target") == v and ed.get("type") == "overlap" for ed in fixed_edges):
                        fixed_edges.append({
                            "source": u,
                            "target": v,
                            "type": "overlap",
                            "direction": "undirected",
                            "weight": round(w, 3),
                        })
                        connected2.add(u)
                        connected2.add(v)
                        added += 1
            if added:
                logs.append(f"- 为孤立节点添加 {added} 条 overlap 边")
                fix_count += added
            still_isolated2 = [e for e in entries if retrieval.entry_id(e) not in connected2]
            if still_isolated2:
                logs.append(f"- 仍有 {len(still_isolated2)} 个孤立节点无法自动连接")
            else:
                logs.append("- 所有孤立节点已连接 ✓")
        else:
            logs.append("- 方向冲突修复后孤立节点已消除 ✓")

    if fix_count > 0:
        fixed_edges.sort(key=lambda x: (x.get("source", ""), x.get("type", ""), x.get("target", "")))
        logs.append(f"\n共修复 {fix_count} 处，已写入 skills.edges.json")
    else:
        logs.append("- 图谱无需修复 ✓")

    return fixed_edges, fix_count, "\n".join(logs)

