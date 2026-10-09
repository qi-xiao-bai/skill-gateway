#!/usr/bin/env python3
# reports.py - Token 账本(audit) 与索引覆盖口径(stats)
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: stats 改为"覆盖口径"（覆盖了什么 / 缺什么 / 关系图），不再只是按来源计数
import os

import index_store
import retrieval


def est_tokens(chars):
    """粗略 token 估算：中英混合约 4 字符/token。"""
    return int(round(chars / 4.0)) if chars else 0


def skill_chars(e):
    """返回 (字符数, 是否桥接"仅描述"占位文件)。
    占位文件（platform_bridge --mode list 落的）**不是完整 SKILL.md**，
    拿它当"全文"算压缩率会得出一个没有意义的数，必须单独标出来。"""
    if e["type"] != "skill":
        return 0, False
    p = index_store.skill_file(e)
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
        except OSError:
            return 0, False
        return len(t), (index_store.MIRROR_STUB_MARK in t)
    return 0, False


def _namelist(names, limit=8):
    shown = "、".join(names[:limit])
    return f"{shown} …(共 {len(names)} 个)" if len(names) > limit else shown


def stats_text(entries, edges=None, registry_missing=None):
    """registry_missing：是否缺权威登记表（available_skills.json）。None=自动探测。
    缺表时一切条目按全机兜底口径视为可用——必须在报告里显著声明，防止被误读为
    权威平台口径（评标第 8 轮观察：重传包会抹掉登记表，兜底态不可见曾致口径误判）。"""
    """索引统计 = **覆盖口径**：这份索引覆盖了什么、每类多少、缺什么。
    `edges=None` 表示索引里没有关系边（旧版索引或还没重建）。"""
    cov = index_store.coverage(entries)
    import datetime as _dt
    out = [f"# 索引统计（覆盖口径）：共 {cov['total']} 项"
           f"（skill {cov['skill']} / mcp {cov['mcp']}）（快照 {_dt.datetime.now():%Y-%m-%d %H:%M:%S}）", ""]

    out.append("## 按来源（谁贡献了多少）")
    for s, c in cov["by_source"].items():
        out.append(f"  {c:5d}  {s}")
    if not cov["by_source"]:
        out.append("  （空索引）")

    out.append("")
    if registry_missing is None:
        import paths as _paths
        registry_missing = not os.path.isfile(
            os.path.join(_paths.ROOT, "inputs", "available_skills.json"))
    out.append("## 按可见性（平台口径）")
    if registry_missing:
        out.append("  ⚠ 当前为全机扫描兜底口径：未检测到登记表（available_skills.json），")
        out.append("    所有扫到的技能均视为平台可用；如平台有绑定清单，")
        out.append("    运行 agent-index 建立权威口径（导入后此提示消失）。")
    vis_names = {"ready": "平台可用（默认检索口径）", "off-list": "平台未绑定（仅 --all 全量视图）",
                 "blocked": "平台标记不可用（已排除）"}
    for v, c in sorted(cov.get("by_visibility", {}).items()):
        out.append(f"  {c:5d}  {vis_names.get(v, v)}")
    if not cov.get("by_visibility"):
        out.append("  （空索引）")

    if cov.get("agent_bound") or cov.get("agent_unbound"):
        out.append("")
        out.append("## Agent 绑定（--agent 口径，非默认）")
        out.append(f"  当前Agent已绑定  {cov['agent_bound']:5d}")
        out.append(f"  未绑定           {cov['agent_unbound']:5d}")



    out.append("")
    out.append("## 全文覆盖（detail 能不能真的取出全文）")
    out.append(f"  完整 SKILL.md      {cov['full_text']:5d}")
    out.append(f"  桥接占位(非全文)    {cov['stub']:5d}")
    out.append(f"  无本地文件(import)  {cov['no_file']:5d}")
    out.append(f"  含 references 的    {cov['with_references']:5d}")
    if cov["stub"]:
        out.append(f"  ⚠ 有占位文件：这些条目的\"全文\"只是描述，不是完整 SKILL.md（audit 不会给压缩率）")
        out.append(f"     {_namelist(cov['stub_names'])}")

    out.append("")
    out.append("## 排除名单（.skillexclude / config 规则屏蔽）")
    n_ex = cov.get("excluded", 0)
    out.append(f"  已排除技能         {n_ex:5d}")
    if n_ex:
        out.append(f"  名单：{_namelist(cov.get('excluded_names', []))}")
        out.append("  （已排除技能不参与 search/chat 检索与流水线编排；在 dashboard 图谱中以虚线高亮展现）")

    out.append("")
    out.append("## 缺什么（直接影响\"被 AI 发现\"的概率）")
    if cov["no_description"]:
        out.append(f"  无描述 {len(cov['no_description']):4d}  {_namelist(cov['no_description'])}")
    if cov["no_triggers"]:
        out.append(f"  无触发词 {len(cov['no_triggers']):4d}  {_namelist(cov['no_triggers'])}")
    if cov["no_file"]:
        out.append(f"  无本地全文 {cov['no_file']:4d}  {_namelist(cov['no_local_text'])}")
        out.append("  ⚠ 这些条目只有描述（import / 平台清单），`detail` 取不到全文")
    if not (cov["no_description"] or cov["no_triggers"] or cov["no_file"]):
        out.append("  无（描述 / 触发词 / 全文都齐）")
    out.append("  → 修这些用 `doctor` 看明细，改 SKILL.md 后 `update` 增量刷新")

    out.append("")
    out.append("## 关系图（技能之间连不连得上）")
    if edges is None:
        out.append("  索引里还没有关系边（旧版索引）→ 跑一次 `index` 或 `update` 即会生成")
    else:
        types = {}
        for ed in edges:
            et = ed.get("type", "?")
            types[et] = types.get(et, 0) + 1
        touched = {ed.get("source", "") for ed in edges} | {ed.get("target", "") for ed in edges}
        ids = {retrieval.entry_id(e) for e in entries}
        iso = sorted(e["name"] for e in entries if retrieval.entry_id(e) not in touched)
        shape = "、".join(f"{k}={v}" for k, v in sorted(types.items())) or "无"
        out.append(f"  节点 {len(ids)} / 边 {len(edges)}（{shape}）")
        out.append(f"  孤立节点 {len(iso)}（不与任何技能相连）" +
                   (f"：{_namelist(iso)}" if iso else ""))
        if not edges:
            out.append("  （没有成边：技能彼此描述差异大，属正常；`related <技能名>` 仍可用）")
    return "\n".join(out)


def audit_text(entries, top=10):
    """量化"索引 vs 全文"的上下文压缩率。
    若基线里有桥接「仅描述」占位文件，**不给压缩率** —— 拿占位文件当全文算出来的百分比没有意义。"""
    rows = []
    for e in entries:
        c, stub = skill_chars(e)
        rows.append((c, stub, e))
    total_full = sum(c for c, _, _ in rows)
    n_stub = sum(1 for _, stub, _ in rows if stub)
    n_skill = sum(1 for e in entries if e["type"] == "skill")
    no_file = sum(1 for c, _, e in rows if e["type"] == "skill" and not c)
    l1 = index_store.dense_text(entries)   # 按**真实加载的整份文件**算（含头块），不虚报
    l0 = index_store.l0_text(entries)
    # 压缩率口径：分子分母必须同集合。分母只有技能全文，那分子也只用技能条目的 L1
    #（此前用整份 l1 含全部 MCP 行，MCP 一多压缩率能算出「省 -3.2%」的荒谬数字）
    skill_only = [e for e in entries if e["type"] == "skill"]
    l1_skills = len(index_store.dense_text(skill_only)) if skill_only else 0

    if n_stub and n_stub == n_skill:
        base = f"- 镜像占位合计(仅描述，非全文) : {total_full:>10,} 字符 ≈ {est_tokens(total_full):>8,} tokens"
    elif n_stub:
        base = (f"- 合计(含 {n_stub} 项占位，非全文) : {total_full:>10,} 字符 "
                f"≈ {est_tokens(total_full):>8,} tokens")
    else:
        base = f"- 全文合计(所有 SKILL.md)  : {total_full:>10,} 字符 ≈ {est_tokens(total_full):>8,} tokens"

    out = [
        "# Token 账本（audit）",
        f"技能 {n_skill} 项 / MCP {len(entries) - n_skill} 项，共 {len(entries)} 项",
        base,
        f"- L1 索引(dense，含头块)   : {len(l1):>10,} 字符 ≈ {est_tokens(len(l1)):>8,} tokens",
        f"- L0 索引(仅名字)          : {len(l0):>10,} 字符 ≈ {est_tokens(len(l0)):>8,} tokens",
    ]
    if n_stub:
        out.append(f"- ⚠ 基线里 {n_stub}/{n_skill} 项是桥接「仅描述」占位文件（--mode list），"
                   "**不是完整 SKILL.md** → 压缩率不可比，故不给出。")
        out.append("  要真实压缩率：回平台逐技能 load 完整 SKILL.md 后按 --mode full 重跑。")
    elif no_file:
        out.append(f"- 注：{no_file} 项没有本地全文（import/平台记录），未计入「全文合计」分母，"
                   "故压缩率会偏乐观")
    if total_full and not n_stub:
        pct = 100 * l1_skills / total_full if total_full else 0
        out.append(f"- 压缩率 L1(仅技能行)/全文: {pct:.1f}%  ⇒ 省 {100 - pct:.1f}% 上下文")
        out.append(f"- 启动字符量约减少 {total_full / max(l1_skills, 1):.0f}×")
    n_clip = sum(1 for e in entries if index_store.desc_clipped(e))
    if n_clip:
        out.append(f"- 注：dense 索引里 {n_clip}/{len(entries)} 条描述被 "
                   f"{index_store.DENSE_QUOTA_DESC} 字符配额截断（`…` 结尾）；"
                   "完整描述仍保留在 skill-index.json，search/detail 不受影响")
    heavy = [r for r in sorted(rows, key=lambda x: -x[0]) if r[0] > 0][:top]
    if heavy:
        title = f"最重的 {len(heavy)} 个技能（detail 会一次性灌入这么多）"
        out.append(f"\n## {title}")
        for chars, stub, e in heavy:
            mark = "（占位文件，非 SKILL.md 全文）" if stub else ""
            out.append(f"  {chars:>8,} 字符 ≈ {est_tokens(chars):>7,} tok   {e['name']}{mark}")
    return "\n".join(out)


def diff_text(entries, edges, name):
    """技能变更影响分析：分析指定技能变更对下游直接、子技能及二级依赖的影响。"""
    by_id = retrieval.build_id_map(entries)
    target_entry = next((e for e in entries if e["name"].lower() == name.lower()), None)
    if not target_entry:
        return f"未找到技能 '{name}'。用 list / search 查看可用名称。"

    eid = retrieval.entry_id(target_entry)
    lines = [
        f"# 变更影响分析：{target_entry['name']}\n",
        f"**技能**: {target_entry['name']}  |  **描述**: {target_entry.get('description', '（无）')[:80]}\n",
    ]

    # 1. 直接下游（depends_on 指向本技能的）与包含的子技能
    dependents = []
    dependent_ids = set()
    children = []
    related = []
    for ed in (edges or []):
        src, tgt = ed.get("source"), ed.get("target")
        etype = ed.get("type")
        weight = ed.get("weight", 0)

        if tgt == eid and etype == "depends_on":
            other = by_id.get(src)
            if other:
                dependents.append((other["name"], other.get("description", "")[:80], weight))
                dependent_ids.add(src)
        elif src == eid and etype == "contains":
            other = by_id.get(tgt)
            if other:
                children.append((other["name"], other.get("description", "")[:80], weight))
        elif src == eid or tgt == eid:
            if etype in ("overlap", "family", "similar"):
                other_id = tgt if src == eid else src
                other = by_id.get(other_id)
                if other:
                    related.append((other["name"], etype, weight, other.get("description", "")[:80]))

    # 2. 二级影响（下游的下游）；直接下游本身不重复计入二级
    level2 = set()
    level2_details = []
    for dep_id in dependent_ids:
        for ed in (edges or []):
            if ed.get("target") == dep_id and ed.get("type") == "depends_on":
                other = by_id.get(ed.get("source"))
                if (other and other["name"] != name
                        and ed.get("source") not in dependent_ids
                        and ed.get("source") != eid
                        and other["name"] not in level2):
                    level2.add(other["name"])
                    level2_details.append((other["name"], by_id[dep_id]["name"],
                                           other.get("description", "")[:80]))

    # 3. 输出格式化
    lines.append("## 直接影响\n")
    if dependents:
        lines.append(f"### 依赖本技能的（{len(dependents)} 个）")
        lines.append("变更可能破坏这些技能的功能：\n")
        for n, d, w in sorted(dependents, key=lambda x: -x[2]):
            lines.append(f"- **{n}** (weight={w:.1f}): {d}")
        lines.append("")
    if children:
        lines.append(f"### 包含的子技能（{len(children)} 个）")
        lines.append("变更可能影响这些子技能的行为：\n")
        for n, d, w in sorted(children, key=lambda x: -x[2]):
            lines.append(f"- **{n}** (weight={w:.1f}): {d}")
        lines.append("")
    if not dependents and not children:
        lines.append("- 无直接下游\n")

    if level2_details:
        lines.append("## 二级影响\n")
        lines.append(f"下游的下游（{len(level2_details)} 个），间接受影响：\n")
        for n, via, d in sorted(level2_details):
            lines.append(f"- **{n}** (via {via}): {d}")
        lines.append("")

    if related:
        lines.append("## 关联技能（可能需要同步调整）\n")
        for n, etype, w, d in sorted(related, key=lambda x: -x[2])[:10]:
            lines.append(f"- **{n}** [{etype}={w:.2f}]: {d}")
        lines.append("")

    total = len(dependents) + len(children) + len(level2_details)
    severity = "高" if total >= 5 else "中" if total >= 2 else "低"
    lines.append("## 影响评估\n")
    lines.append(f"- 影响范围: **{severity}**（直接影响 {len(dependents) + len(children)} 个，间接影响 {len(level2_details)} 个）")
    advice = "变更前务必验证下游技能" if severity == "高" else "建议检查直接下游" if severity == "中" else "影响有限，常规测试即可"
    lines.append(f"- 建议: {advice}\n")
    return "\n".join(lines)


def onboard_text(entries, edges, name):
    """技能上手指南：为指定技能生成结构化入门文档（概述/关联/文件结构/快速上手）。"""
    by_id = retrieval.build_id_map(entries)
    target_entry = next((e for e in entries if e["name"].lower() == name.lower()), None)
    if not target_entry:
        return f"未找到技能 '{name}'。用 list / search 查看可用名称。"

    eid = retrieval.entry_id(target_entry)
    lines = [
        f"# {target_entry['name']} 上手指南\n",
        "## 概述\n",
        f"- **名称**: {target_entry['name']}",
        f"- **类型**: {target_entry['type']}",
        f"- **描述**: {target_entry.get('description', '（无描述）')}",
    ]
    if target_entry.get("triggers"):
        lines.append(f"- **触发词**: {'、'.join(target_entry['triggers'])}")
    lines.append(f"- **路径**: {target_entry.get('path', '（无路径）')}\n")

    # 关联技能
    neighbors = []
    for ed in (edges or []):
        src, tgt = ed.get("source"), ed.get("target")
        etype = ed.get("type", "?")
        weight = ed.get("weight", 0)
        other_id = tgt if src == eid else src if tgt == eid else None
        if other_id and other_id in by_id:
            other_e = by_id[other_id]
            # 有向边要看本节点在哪端：出边 →（我供给它），入边 ←（它供给我）
            if etype in ("depends_on", "contains"):
                direction = "→" if src == eid else "←"
            else:
                direction = "↔"
            neighbors.append((etype, weight, other_e["name"], other_e.get("description", "")[:80], direction))

    if neighbors:
        lines.append("## 关联技能\n")
        neighbors.sort(key=lambda x: -x[1])
        for etype, weight, nname, ndesc, direction in neighbors[:10]:
            lines.append(f"- {direction} **{nname}** [{etype}={weight:.2f}]: {ndesc}")
        lines.append("")

    # 文件结构 (content_index 探测)
    try:
        import content_index as ci
        doc = ci.load_content_index()
        if doc and doc.get("chunks"):
            skill_chunks = [c for c in doc["chunks"] if c.get("skill", "").lower() == name.lower()]
            if skill_chunks:
                files = {}
                for c in skill_chunks:
                    files.setdefault(c.get("file", ""), []).append(c)
                lines.append("## 文件结构\n")
                for fname in sorted(files.keys()):
                    chunks = files[fname]
                    lines.append(f"### {fname}（{len(chunks)} 段）\n")
                    for c in chunks[:8]:
                        lines.append(f"- L{c['s']}-{c['e']}: {c['summary']}")
                    if len(chunks) > 8:
                        lines.append(f"- ... 还有 {len(chunks) - 8} 段")
                    lines.append("")
    except Exception as ex:
        lines.append(f"（文件结构章节不可用：{ex}）\n")

    lines.append("## 快速上手\n")
    lines.append(f"1. **触发技能**: 使用技能名 `{target_entry['name']}` 或触发词激活")
    if target_entry.get("triggers"):
        lines.append(f"2. **触发词**: {'、'.join(target_entry['triggers'][:5])}")
    lines.append(f"3. **查看全文**: `/skill-gateway detail {target_entry['name']}`")
    lines.append(f"4. **查看关联**: `/skill-gateway related {target_entry['name']}`\n")

    if target_entry.get("has_references"):
        lines.append("## 注意\n")
        lines.append("- 该技能含参考资料（references/scripts/assets），建议按需 detail 看全文")
        lines.append("- 大体积参考文件按需加载，避免一次性全部读入上下文\n")

    return "\n".join(lines)


# dashboard 前端模板是独立资产文件（templates/dashboard_template.html）：
# 此前 ~2000 行 HTML/CSS/JS 内嵌在本文件字符串里，lint、格式化、前端调试全被绑架。
_TEMPLATE_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "dashboard_template.html"))


def _load_template():
    with open(_TEMPLATE_FILE, encoding="utf-8") as f:
        tpl = f.read()
    # D3 内联：dashboard 保持单文件自包含（离线/内网/无外网沙箱都能出图）；
    # 库文件缺失或内容异常时回退 CDN 外链，模板占位符绝不裸留在产物里
    d3_path = os.path.join(os.path.dirname(_TEMPLATE_FILE), "d3.v7.min.js")
    try:
        with open(d3_path, encoding="utf-8") as f:
            d3_src = f.read()
        if "</script>" in d3_src.lower() or "__D3_INLINE__" in d3_src:
            raise ValueError("d3 bundleUnsafe: contains script terminator or placeholder")
        tpl = tpl.replace("__D3_INLINE__", d3_src, 1)
    except (OSError, ValueError):
        tpl = tpl.replace(
            "<script>__D3_INLINE__</script>",
            '<script src="https://d3js.org/d3.v7.min.js"></script>', 1)
    if "__D3_INLINE__" in tpl:
        raise RuntimeError("dashboard template: __D3_INLINE__ placeholder unresolved")
    return tpl


# dashboard 注入的展示配额（与 dense 索引同一来源，不另写魔法数）
NODE_DESC_QUOTA = index_store.DENSE_QUOTA_DESC
NODE_TRIGGER_QUOTA = index_store.DENSE_QUOTA_TRIG


def generate_dashboard_html(entries, edges):
    """生成技能图谱可视化 HTML（D3 力导向图，中英双语切换，纯本地展示）。
    返回: (html_str, node_count, link_count, edge_types_seen)
    """
    import json

    nodes = []
    node_ids = set()
    n_skills = 0
    n_mcps = 0
    n_connectors = 0
    n_agents = 0

    for e in entries:
        nid = retrieval.entry_id(e)
        node_ids.add(nid)
        etype = e.get("type", "skill")
        is_conn = bool(e.get("is_connector") or etype == "connector")
        is_ag = bool(e.get("is_agent") or etype == "agent")
        # 顶栏统计只计可见口径（与检索/编排一致）；excluded（off-list/blocked）
        # 的节点仍进图（由 Show Excluded 开关审计），但不冒充可见技能数
        if not e.get("excluded"):
            if is_ag:
                n_agents += 1
            elif is_conn:
                n_connectors += 1
                n_mcps += 1
            elif etype == "mcp":
                n_mcps += 1
            else:
                n_skills += 1

        nodes.append({
            "id": nid,
            "name": e["name"],
            "clean_name": e.get("clean_name", e["name"]),
            "type": etype,
            "is_connector": is_conn,
            "is_agent": is_ag,
            "desc": (e.get("description") or "")[:NODE_DESC_QUOTA],
            "desc_zh": (e.get("description_zh") or e.get("description_cn") or "")[:NODE_DESC_QUOTA],
            "triggers": (e.get("triggers") or [])[:NODE_TRIGGER_QUOTA],
            "excluded": bool(e.get("excluded")),
            "exclude_reason": e.get("exclude_reason", ""),
            "has_references": bool(e.get("has_references")),
            "source": e.get("source", ""),
            "visibility": e.get("visibility", "ready"),
            "category": e.get("category", ""),
            "platform": e.get("platform", ""),
            "agent_bound": e.get("agent_bound"),
            "agents": e.get("agents", []),
            "variant_of": e.get("variant_of", ""),
        })

    links = []
    edge_types_seen = set()
    for ed in (edges or []):
        src = ed.get("source", "")
        tgt = ed.get("target", "")
        if src in node_ids and tgt in node_ids:
            etype = ed.get("type", "collaborates")
            links.append({
                "source": src,
                "target": tgt,
                "type": etype,
                "weight": ed.get("weight", 0.5),
                "direction": ed.get("direction", "directed"),
            })
            edge_types_seen.add(etype)

    edge_colors = {
        "depends_on": "#c084fc",   # Purple-400
        "collaborates": "#38bdf8", # Sky-400
        "family": "#fb923c",       # Orange-400
        "overlap": "#94a3b8",      # Slate-400
        "contains": "#34d399",     # Emerald-400
        "similar": "#f472b6",      # Pink-400
        "?": "#71717a",            # Zinc-500
    }

    stat_parts = []
    if n_mcps > 0:
        if n_connectors > 0:
            stat_parts.append(f"MCP {n_mcps - n_connectors} / 连接器 {n_connectors}")
        else:
            stat_parts.append(f"MCP {n_mcps}")
    if n_agents > 0:
        stat_parts.append(f"数字员工 {n_agents}")
    mcp_stat_str = " · ".join(stat_parts) if stat_parts else str(n_mcps)
    html = _load_template()
    # 顺序：先替换计数占位符（无用户数据），再注入 JSON——若某条描述含字面
    # `__STAT_SKILLS__`，反序会拿数据去污染计数。
    html = html.replace("__STAT_SKILLS__", str(n_skills))
    html = html.replace("__STAT_MCPS__", mcp_stat_str)
    html = html.replace("__STAT_LINKS__", str(len(links)))
    # < 全部转成 <：描述含 `</script>` 会截断脚本块、注入任意脚本
    def _safe_json(obj):
        return json.dumps(obj, ensure_ascii=False).replace("<", "\u003c")
    html = html.replace("__DATA_NODES__", _safe_json(nodes))
    # 当前智能体身份（登记表 current_agent / 环境变量）——面板默认定位到本平台
    cur_agent = ""
    try:
        import scanner as _sc
        cur_agent = _sc.available_list_agent() or ""
    except Exception:
        pass
    html = html.replace("__CURRENT_AGENT__", _safe_json(cur_agent))
    html = html.replace("__DATA_LINKS__", _safe_json(links))
    html = html.replace("__DATA_EDGE_COLORS__", _safe_json(edge_colors))
    html = html.replace("__DATA_EDGE_TYPES__", _safe_json(sorted(edge_types_seen)))

    return html, len(nodes), len(links), sorted(edge_types_seen)
