#!/usr/bin/env python3
# pipeline.py - 多技能图谱编排引擎：按任务检索 + 依图谱拓扑动态生成协同流水线
# created 2026-09-16 qjl
# updated 2026-09-27 qjl: 移除硬编码生命周期模板（LIFECYCLES 4 套模板 + 角色表 + 阶段文案
#                         + 关键词投票 + dev 兜底）。阶段数量/顺序/文案现在完全由三样真实
#                         数据决定：① 任务检索命中的种子技能 ② 子图内 depends_on 边的拓扑
#                         分层 ③ 技能自身的描述与触发词。图谱里没有依赖关系时，诚实输出
#                         "并行候选"，绝不给任意任务硬套研发流水线。
import index_store
import retrieval
from paths import get_pinned_skills, get_user_directives

SEED_TOP = 6  # 任务检索取几个种子技能
EXPAND_TOP = 9  # 子图节点上限（种子 + depends_on 指向的支撑技能）
MAX_STAGE_SKILLS = 3  # 单阶段并行技能上限（超出按相关度截断）


def _load_context(entries, edges):
    """entries/edges 未显式传入（测试注入用）时从索引读；旧索引没存边就现算。"""
    if entries is None:
        entries = index_store.load_index()
    if edges is None:
        edges = index_store.read_edges()
        if edges is None:
            edges = retrieval.build_edges(entries)
    return entries, edges


def _layer_nodes(node_ids, dep_edges):
    """Kahn 拓扑分层：depends_on 的 target（被依赖方）先于 source（依赖方）。
    返回 (layers, cycle_ids)：有环时把环上剩余节点并入最后一层并如实上报。"""
    nodes = set(node_ids)
    needs = {n: set() for n in nodes}  # n 依赖哪些节点（它们必须先执行）
    for ed in dep_edges:
        s, t = ed.get("source", ""), ed.get("target", "")
        if s in nodes and t in nodes and s != t:
            needs[s].add(t)
    layers = []
    placed = set()
    remaining = set(nodes)
    cycle_ids = []
    while remaining:
        ready = {n for n in remaining if needs[n] <= placed}
        if not ready:
            cycle_ids = sorted(remaining)
            layers.append(cycle_ids)
            break
        layers.append(sorted(ready))
        placed |= ready
        remaining -= ready
    return [l for l in layers if l], cycle_ids


def _desc_of(e, limit=70):
    d = (e.get("description") or "").strip()
    return (d[:limit] + "…") if len(d) > limit else d


def _evidence_str(ev):
    return "、".join(ev[:8]) if ev else "技能名命中"


def _seq_stage(i, members, upstream_names, evidence, is_last, cycle_ids):
    """时序阶段：input/output/handoff 全部从上游成员、depends_on 边证据与技能描述生成。"""
    names = [m["name"] for m in members]
    parallel = len(members) > 1
    title = f"阶段 {i}：{'、'.join(names)}" + ("（并行协同）" if parallel else "")
    if i == 1:
        inp = f"用户任务原文与检索证据（命中词：{_evidence_str(evidence.get(names[0].lower(), []))}）"
    else:
        ups = "、".join(upstream_names) or "上一阶段"
        inp = f"承接上游 {ups} 的产出（图谱依据：depends_on 边，见阶段矩阵）"
    outputs = [f"{m['name']}：{_desc_of(m)}" for m in members]
    out = "；".join(outputs)
    if is_last and cycle_ids:
        handoff = "输出最终交付成果并提示用户验收（注意：图谱存在循环依赖，见编排说明）"
    elif is_last:
        handoff = "输出最终交付成果并提示用户验收"
    else:
        handoff = "将本阶段产出传递给下一阶段（depends_on 拓扑顺序，非固定模板）"
    return {
        "index": i,
        "stage_title": title,
        "skills": members,
        "skill_names": names,
        "parallel": parallel,
        "input": inp,
        "output": out,
        "handoff": handoff,
        "evidence": {m["name"]: evidence.get(m["name"].lower(), []) for m in members},
    }


def _parallel_stage(i, members, evidence):
    """并行候选阶段：图谱中无依赖关系时的诚实输出——列候选、给证据、不伪造顺序。"""
    names = [m["name"] for m in members]
    return {
        "index": i,
        "stage_title": f"阶段 {i}（并行候选·无依赖关系）：{'、'.join(names)}",
        "skills": members,
        "skill_names": names,
        "parallel": True,
        "input": "用户任务原文与各候选的检索证据（见入选依据）",
        "output": "；".join(f"{m['name']}：{_desc_of(m)}" for m in members),
        "handoff": "候选相互独立、无先后约束：由执行 Agent 按任务甄别采纳（采纳即 detail 加载全文），"
                   "不采纳的不加载；本阶段不是流水线，不伪造时序",
        "evidence": {m["name"]: evidence.get(m["name"].lower(), []) for m in members},
    }


def build_pipeline(query, entries=None, edges=None, seed_top=SEED_TOP, _retried=False):
    """图谱驱动编排：
    1. retrieval.search 按任务检索种子技能（证据 = 命中词）；
    2. 沿 depends_on 边把被依赖技能纳入子图（支撑节点）；
    3. 对子图做 Kahn 拓扑分层 → 每层一个阶段（层内技能并行）；
    4. 无依赖关系的种子 → "并行候选"阶段，如实标注、不硬套模板；
    5. 完全无命中 → mode="empty"（触发过一次增量自愈重试）。
    返回 dict(task, mode, basis, stages, skills, notes)。"""
    entries, edges = _load_context(entries, edges)
    global_hits = retrieval.search(entries, query, top=seed_top) if entries else []
    seeds = [e for _s, e, _ov in global_hits]
    evidence = {e["name"].lower(): ov for _s, e, ov in global_hits}

    if not seeds and not _retried:
        refreshed_entries, refreshed_edges, st, has_changes = index_store.auto_update_on_miss(
            query=query, targeted=True
        )
        if has_changes:
            return build_pipeline(
                query, entries=refreshed_entries, edges=refreshed_edges,
                seed_top=seed_top, _retried=True,
            )

    if not seeds:
        return {
            "task": query,
            "mode": "empty",
            "basis": "本地技能库检索无命中（已触发增量自愈重试）",
            "stages": [],
            "skills": [],
            "notes": [
                "可换关键词重试 search / chat；",
                "用 list 浏览现有技能，或 roots 诊断扫描根；",
                "平台不落盘时用 import 导入技能清单。",
            ],
        }

    by_id = retrieval.build_id_map(entries)
    id_of = {e["name"].lower(): retrieval.entry_id(e) for e in seeds}

    # 子图 = 种子 + 种子 depends_on 指向的支撑技能（被依赖方需先执行）
    selected = {id_of[n] for n in id_of}
    for ed in edges:
        if ed.get("type") != "depends_on":
            continue
        s, t = ed.get("source", ""), ed.get("target", "")
        if s in selected and t not in selected and t in by_id and len(selected) < EXPAND_TOP:
            selected.add(t)
    sub_edges = [ed for ed in edges
                if ed.get("source", "") in selected and ed.get("target", "") in selected]
    dep_edges = [ed for ed in sub_edges if ed.get("type") == "depends_on"]

    involved = {n for ed in dep_edges for n in (ed.get("source", ""), ed.get("target", ""))}
    loose_seed_ids = {id_of[n] for n in id_of} - involved
    notes = []
    stages = []

    if involved:
        layers, cycle_ids = _layer_nodes(involved, dep_edges)
        n_layers = 0
        if cycle_ids:
            notes.append(
                "子图中存在循环依赖：" + "、".join(cycle_ids)
                + " ——已并入最后一层，请人工确认真实先后。"
            )
        is_chain_last = not loose_seed_ids
        prev_names = []
        for i, layer in enumerate(layers, 1):
            members = [by_id[n] for n in layer if n in by_id][:MAX_STAGE_SKILLS]
            if not members:
                continue
            n_layers += 1
            stages.append(_seq_stage(
                i, members, prev_names, evidence,
                is_last=(i == len(layers) and is_chain_last), cycle_ids=cycle_ids,
            ))
            prev_names = [m["name"] for m in members]
        if loose_seed_ids:
            members = [by_id[n] for n in sorted(loose_seed_ids) if n in by_id][:MAX_STAGE_SKILLS]
            if members:
                stages.append(_parallel_stage(len(stages) + 1, members, evidence))
                notes.append(
                    "部分种子之间无依赖边，已列为并行候选阶段（不伪造时序）。"
                )
        mode = "topological"
        basis = (
            f"检索种子 {len(id_of)} 个；子图 depends_on 边 {len(dep_edges)} 条；"
            f"拓扑分层 {n_layers} 层"
        )
    else:
        members = seeds[:MAX_STAGE_SKILLS]
        stages.append(_parallel_stage(1, members, evidence))
        if len(seeds) > len(members):
            notes.append(f"候选共 {len(seeds)} 个，按相关度展示前 {len(members)} 个。")
        mode = "parallel-candidates"
        basis = (
            f"检索种子 {len(seeds)} 个；子图内无 depends_on/时序边 ——"
            "不硬套流水线模板，输出并行候选供甄别采纳"
        )

    return {
        "task": query,
        "mode": mode,
        "basis": basis,
        "stages": stages,
        "skills": [n for st_ in stages for n in st_["skill_names"]],
        "notes": notes,
    }


def _mermaid(pipe):
    lines = ["flowchart LR"]
    for st in pipe["stages"]:
        names = "、".join(st["skill_names"])[:40]
        lines.append(f'    subgraph S{st["index"]}["{st["stage_title"].split("：", 1)[-1][:24]}"]')
        for j, m in enumerate(st["skills"], 1):
            lines.append(f'      n{st["index"]}_{j}["{m["name"]}"]')
        lines.append("    end")
    for a, b in zip(pipe["stages"], pipe["stages"][1:]):
        arrow = "并行候选·无固定顺序" if "并行候选" in b["stage_title"] else "Handoff 交付物"
        lines.append(f'    S{a["index"]} -->|{arrow}| S{b["index"]}')
    return "\n".join(lines)


def format_pipeline_markdown(pipe):
    """编排结果 → Markdown + 执行契约。阶段/技能/依据全部来自 pipe（数据驱动）。"""
    lines = [f"# 技能图谱编排流水线：【{pipe['task']}】\n"]
    if pipe["mode"] == "empty":
        lines.append("> ⚠️ **本地技能库无匹配项通知**：检索无命中（已触发实时增量自愈）。")
        lines.append("> " + " ".join(pipe["notes"]))
        lines.append("\n[skill-indexer] 编排未生成：无候选技能，请换词或先 `list` 浏览。")
        return "\n".join(lines)

    if pipe["mode"] == "topological":
        lines.append("> **编排依据**：任务检索种子 + 知识图谱 depends_on 拓扑分层。"
                     "阶段数量与顺序由图谱边决定，不是预设模板。")
    else:
        lines.append("> **编排依据**：图谱中未检出依赖关系 —— 以下为**并行候选**，"
                     "不伪造流水线时序；采纳哪个/哪几个由执行 Agent 甄别。")
    lines.append(f"> **信号统计**：{pipe['basis']}\n")

    directives = get_user_directives()
    if directives:
        lines.append("## 📌 用户重要指令 (User Directives - 全程必遵)\n")
        lines.append("> ⚠️ **执行以下阶段时必须优先严格遵守这些指令**：")
        for d_idx, d_text in enumerate(directives, 1):
            lines.append(f"{d_idx}. **{d_text}**")
        lines.append("")

    pinned = get_pinned_skills()

    # 1. DAG 拓扑概览
    lines.append("## 1. 流水线 DAG 拓扑流向\n")
    lines.append("```mermaid")
    lines.append(_mermaid(pipe))
    lines.append("```\n")

    # 2. 阶段协同矩阵
    lines.append("## 2. 阶段协同与上下文传递矩阵\n")
    lines.append("| 阶段 (Stage) | 执行技能 (Skill) | 依赖输入 (Input) | 阶段产出 (Output) | 接力 (Handoff) |")
    lines.append("|:---|:---|:---|:---|:---|")
    for st in pipe["stages"]:
        snames = "、".join(
            f"`{n}`" + (" ★(常用置顶)" if n.lower() in pinned else "")
            for n in st["skill_names"]
        )
        lines.append(f"| **{st['stage_title']}** | {snames} | {st['input']} | {st['output']} | {st['handoff']} |")
    lines.append("")

    # 3. 阶段详情（入选依据 = 检索证据词，可复核）
    lines.append("## 3. 阶段详细执行指引与上下文接力 (Context Handoff)\n")
    for st in pipe["stages"]:
        lines.append(f"### {st['stage_title']}")
        for m in st["skills"]:
            ev = _evidence_str(st["evidence"].get(m["name"], []))
            pin_tag = " ★ [用户置顶常用技能]" if m["name"].lower() in pinned else ""
            lines.append(f"- **目标技能**：`{m['name']}`{pin_tag}")
            lines.append(f"  - 入选依据：检索命中证据（{ev}）")
            lines.append(f"  - 技能定位：{_desc_of(m, 140)}")
            trigs = "、".join((m.get("triggers") or [])[:4])
            if trigs:
                lines.append(f"  - 触发词：{trigs}")
        lines.append(f"- **输入上下文**：{st['input']}")
        lines.append(f"- **交付产出物**：{st['output']}")
        lines.append(f"- **接力约定 (Handoff)**：{st['handoff']}")
        lines.append("")

    for note in pipe.get("notes", []):
        lines.append(f"> ⚠️ {note}")
    if pipe.get("notes"):
        lines.append("")

    # 4. Agent 执行规范
    lines.append("## 4. Agent 自动化执行规范")
    lines.append(
        "1. **采纳即加载（只加载实际采纳的）**：决定采纳某技能承接阶段时，执行前先 "
        "`detail <该技能名>` 加载其 SKILL.md 全文并遵循其规范（同一技能只加载一次）；"
        "否决的候选、以及通用能力承接的阶段，不加载任何全文。只看工作单不执行 = 编排没有发生；"
    )
    lines.append(
        "2. **按拓扑时序接力**：depends_on 指向的技能先执行，其产出是下游阶段的输入；"
        "并行候选阶段无固定顺序，按任务甄别采纳；"
    )
    lines.append(
        "3. **上下文隔离与聚焦**：每个阶段只注入该阶段所需的最小上下文，"
        "避免前置调试细节污染下游阶段；"
    )
    lines.append(
        "4. **阶段留痕验证**：阶段交接时简要总结当前产物，并告知用户即将流转至下一技能。\n"
    )

    lines.append(footnote_text(pipe))
    return "\n".join(lines)


def footnote_text(pipe):
    """网关留痕脚注（供调用方输出；数字全部来自 pipe 实际数据）。"""
    if pipe["mode"] == "empty":
        return "[skill-indexer] 技能串联编排: 无候选命中 | 未生成流水线 | 建议换词或 list 浏览"
    if pipe["mode"] == "parallel-candidates":
        chain = " / ".join(f"[{n}]" for n in pipe["skills"])
        return (
            f"[skill-indexer] 并行候选编排: {chain} | 图谱无依赖边，不伪造流水线 | "
            "由执行 Agent 甄别采纳"
        )
    chain = " → ".join(f"[{'、'.join(st['skill_names'])}]" for st in pipe["stages"])
    return (
        f"[skill-indexer] 技能串联编排: {chain} | 图谱拓扑自动生成 "
        f"{len(pipe['stages'])} 阶段流水线 | 消除多次人工选型与上下文断层"
    )
