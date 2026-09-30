#!/usr/bin/env python3
# retrieval.py - 关键词排序检索（中文 2/3-gram + 英文词，IDF 加权）+ 关系边(ID+edges) + 相关技能
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 新增稳定 ID 与关系边(overlap/family)，把"相似技能"从现算改为可存储的图
# updated 2026-09-30 qjl: 证据词按命中位置加权(名称/触发词/仅描述) + 名称命中加分按稀有度缩放——
# 真领域词被 IDF_CAP 拉平后靠位置与稀有度找回，无关技能堆描述碎片不再霸榜
import re

from paths import (
    get_pinned_skills,
    get_retrieval_quota_config,
    is_skill_excluded,
    load_edge_weights,
)

MAX_RETRIEVAL_TOP = 20  # 全局检索硬上限，防止超大列表塞爆上下文
MIN_RELEVANCE_SCORE = 0.35  # 最低相关性得分阈值
TIE_GAP_RATIO = 0.95  # 并列候选判定：得分达到 Top1 的 95% 即视为"打分高度接近"，交人工确认
IDF_CAP = 25.0  # 单词证据贡献上限：一个稀有词组不该独自扛起整个相关度
EVIDENCE_MIN_WEIGHT = 6.0  # 独立证据的 IDF 合计下限：高频词组合（"标签+当前"）不许凑数
LOC_NAME = 1.0  # 证据词命中在名称里：最强信号，全额计分
LOC_TRIGGER = 0.75  # 证据词命中在触发词里
LOC_DESC = 0.5  # 证据词仅命中在描述正文里（碎片噪声的主要来源，减半计分）
NAME_HIT_FACTOR = 2.0  # 名称命中加分 = min(命中词IDF, IDF_CAP) × 本系数（上限 +50，调参范围 1.5-2.5）


def _maximal_evidence(overlap, query):
    """同源归组的极大共现证据。
    剔除两类冗余 gram，保留真正独立的证据词：
    1) 字符串包含：t 是某个更长共现词的子串（2026-09-25 龙易查 6298 分假第一）；
    2) 同源区间：t 在 query 原文中的出现区间与更长 gram o 高度重叠（超过 t 长度一半）
       —— "本地仓库"的 本地仓/地仓库/库里 互不包含却是同一词组的碎片，
       此前各算一份证据（2026-09-28 实测：wps-knowledgebase 靠"地仓库+本地仓+库里"75.5 分假命中）。
    等长时按字典序保留较小者，保证结果稳定确定。"""
    ql = query.lower()
    pos = {}
    for t in overlap:
        i = ql.find(t)
        if i >= 0:
            pos[t] = (i, i + len(t))
    out = []
    for t in overlap:
        redundant = False
        for o in overlap:
            if o == t:
                continue
            if t in o:
                redundant = True
                break
            tp, op = pos.get(t), pos.get(o)
            if tp and op:
                inter = min(tp[1], op[1]) - max(tp[0], op[0])
                longer = len(o) > len(t) or (len(o) == len(t) and o < t)
                if longer and inter * 2 > min(len(t), len(o)):
                    redundant = True
                    break
        if not redundant:
            out.append(t)
    return out


def _evidence_weight(maximal, name_terms_hit, trig_terms_hit, weights):
    """证据权重 = Σ min(IDF, IDF_CAP) × 命中位置系数（名称 > 触发词 > 仅描述）。
    IDF_CAP 会把真领域词（salesforce df=3）和泛化碎片（失败 df=6）拉平成同一权重，
    位置系数负责区分证据质量：名称命中是强信号、描述碎片是噪声主要来源
    （2026-09-30 实测：无关技能堆描述碎片 63.5 分压过名称命中的正解 28.5 分）。"""
    total = 0.0
    for t in maximal:
        if t in name_terms_hit:
            loc = LOC_NAME
        elif t in trig_terms_hit:
            loc = LOC_TRIGGER
        else:
            loc = LOC_DESC
        total += min(weights.get(t, 0.1), IDF_CAP) * loc
    return total


def _name_bonus(name_terms_hit, weights):
    """名称命中加分，按命中词稀有度缩放（红线3"点名仅作高权重提示"），
    上限 IDF_CAP × NAME_HIT_FACTOR。固定 +3 时代 df=3 的真领域词和堆碎片的
    垃圾几乎同分（2026-09-30 实测 salesforce-develop 28.5 分被挤到 #21 开外）。"""
    if not name_terms_hit:
        return 0.0
    best = max(name_terms_hit, key=lambda t: weights.get(t, 0.1))
    return min(weights.get(best, 0.1), IDF_CAP) * NAME_HIT_FACTOR


def terms(text):
    """抽取检索词：英文/数字词(len>=2) + 中文二元/三元组；丢弃单字以降低噪声。"""
    text = text.lower()
    out = set(re.findall(r"[a-z0-9]{2,}", text))
    for s in re.findall(r"[\u4e00-\u9fff]+", text):
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                out.add(s[i : i + n])
    return out


def entry_terms(e):
    # description_zh 一并参与分词：双语 frontmatter 的技能才能被中文 query 命中
    return terms(
        e["name"] + " " + e["description"] + " " + (e.get("description_zh") or "")
        + " " + " ".join(e.get("triggers") or [])
    )


def idf(entries):
    """轻量 BM25 式 IDF：稀有词权重高，常见虚词权重低。"""
    N = max(len(entries), 1)
    df = {}
    for e in entries:
        for t in entry_terms(e):
            df[t] = df.get(t, 0) + 1
    return {t: max(0.1, 1.0 + (N - df[t] + 0.5) / (df[t] + 0.5)) for t in df}


def search(entries, query, top=None, cli_excludes=None, include_excluded=False,
           include_off_list=False, agent_only=False):
    """关键词排序检索（中文 2/3-gram + 英文词，IDF 加权 + 命中位置加权 + Profile置顶提权）。
    - 检索打分 100% 全量覆盖（保证查全率）
    - 位置加权：证据词命中在名称/触发词/仅描述，分别按 LOC_NAME/LOC_TRIGGER/LOC_DESC 系数计分
    - 排除过滤：过滤命中 .skillexclude / config / profile / CLI 的条目；
      include_off_list=True 时放行 visibility=off-list（平台未绑定，仅全量审计视图），
      其余 excluded（.skillexclude 规则 / blocked）仍被过滤
    - 置顶提权：命中 pinned_skills 的得分乘 (1 + favorite_boost/10)——归一化乘法，
      加法 +3 在长尾低分段会压倒相关性、在 IDF 高分段又近乎无效
    - 配额硬顶：输出最多不超过 MAX_RETRIEVAL_TOP (20 条)
    - 阈值剪枝：过滤低质凑数长尾
    单字查询（terms() 丢弃单字时）走 _single_char_search 字面子串兜底。
    """
    quota_cfg = get_retrieval_quota_config()
    default_top = quota_cfg.get("top_k", 8)
    fav_boost = float(quota_cfg.get("favorite_boost", 3.0))
    min_score = float(quota_cfg.get("min_score", MIN_RELEVANCE_SCORE))

    effective_top = top if top is not None else default_top
    top = max(1, min(effective_top, MAX_RETRIEVAL_TOP))

    q = terms(query)
    if not q:
        return _single_char_search(
            entries, query, top, cli_excludes, include_excluded, agent_only
        )

    pinned_set = get_pinned_skills()
    weights = idf(entries)
    # 证据质量门槛随库规模缩放：大库（N≥200）用满 EVIDENCE_MIN_WEIGHT 拦高频凑数；
    # 小库 IDF 天花板低（2 条目的库罕见词也只有 ~1.7），按比例缩放避免全库出局
    gate = EVIDENCE_MIN_WEIGHT * min(1.0, max(len(entries), 1) / 200.0)
    scored = []
    max_ev = 0.0  # 剪枝基准：scored 中的最大证据权重（加分/提权之前）
    for e in entries:
        ename = e.get("name", "")
        # 排除检查（off-list 仅在 include_off_list=True 的全量审计视图放行）
        if not include_excluded:
            if e.get("excluded") and not (
                include_off_list and e.get("visibility") == "off-list"
            ):
                continue
            is_ex, _ = is_skill_excluded(ename, cli_excludes)
            if is_ex:
                continue
        # Agent 绑定口径（--agent 收窄，非默认）：未绑定当前 Agent 的技能不出现
        if agent_only and e.get("agent_bound") is False:
            continue
        # 防自环：排除 skill-gateway 自身
        if ename == "skill-gateway":
            continue

        overlap = q & entry_terms(e)
        if not overlap:
            continue
        ename_terms = terms(ename)
        etrig_terms = terms(" ".join(e.get("triggers") or []))
        name_terms_hit = overlap & ename_terms
        trig_terms_hit = (overlap & etrig_terms) - name_terms_hit
        name_hit = bool(name_terms_hit)
        # 极大共现项（同源归组版）：除"被更长 gram 字符串包含"外，
        # 在 query 原文中出现区间被更长 gram 完全覆盖的碎片也算同一词组的冗余证据——
        # 否则"本地仓库"会拆成 本地仓/地仓库/库里 多个互不包含的相邻 gram 全部计分，
        # 无关技能靠一个词组蹭出高分（2026-09-28 实测：wps-knowledgebase 靠"地仓库+本地仓+库里"75.5 分假命中）。
        maximal = _maximal_evidence(overlap, query)
        # 证据质量门槛（随库规模相对化）：同源归并后"单概念 query"只剩 1 个证据是正常现象，
        # 不再用"≥2 证据"硬拦；改为要求证据 IDF 合计达标——大库拦"标签+当前"高频凑数（绝对值 6.0），
        # 小库（N<200）按比例缩放门槛，否则小库 IDF 天花板只有 ~2、门槛永远不可达。
        ev_weight = _evidence_weight(maximal, name_terms_hit, trig_terms_hit, weights)
        if ev_weight < gate and not name_hit:
            continue
        score = ev_weight + _name_bonus(name_terms_hit, weights)
        if e.get("has_references"):
            score += 0.5

        # 用户常用/置顶技能提权（乘法，随得分自然缩放）
        is_pinned = ename.lower() in pinned_set
        if is_pinned:
            score *= 1.0 + fav_boost / 10.0
            e = dict(e)  # is_pinned 是展示态：打在命中副本上，不污染共享索引条目
            e["is_pinned"] = True

        scored.append((score, e, sorted(maximal, key=lambda t: (-len(t), t))))
        if ev_weight > max_ev:
            max_ev = ev_weight

    if not scored:
        return []

    scored.sort(key=lambda x: -x[0])

    # 动态阈值剪枝：宁缺毋滥，不把与 Top 1 差距过大或低于绝对分数的长尾塞入结果。
    # 剪枝基准用最大证据权重（名称加分/置顶提权之前）：名称命中加分可把 top1 抬到
    # 75+，若按 top1×0.25 取 cutoff 会跟着抬到 ~19，把 12.5 分的合法描述命中整批剪掉。
    cutoff = max(min_score, max_ev * 0.25)
    pruned = [item for item in scored if item[0] >= cutoff]

    results = []
    for score, e, maximal in pruned[:top]:
        twins = _name_twins(e.get("name", ""), entries)
        if twins:
            e = dict(e)  # 打在命中副本上，不污染共享索引条目
            e["similar_skills"] = twins
        results.append((score, e, maximal))

    # 并列候选检测：Top 打分高度接近（Top1 的 95% 以内有多条）说明技能库里有
    # 多个同职技能难分伯仲——不替用户定夺，标记 ambiguous 交人工确认。
    # 这也防"检索偏好固化"：常客技能不能靠惯性赢，同类技能必须同样参与打分。
    if results:
        top_score = results[0][0]
        tied = [(s, e) for s, e, _m in results if s >= top_score * TIE_GAP_RATIO]
        if len(tied) >= 2:
            top_e = results[0][1]
            if "similar_skills" not in top_e:
                top_e = dict(top_e)
                results[0] = (results[0][0], top_e, results[0][2])
            top_e["ambiguous"] = True
            top_e["tie_group"] = [
                {"name": e["name"], "score": round(s, 1),
                 "description": (e.get("description") or "")[:60]}
                for s, e in tied[:4]
            ]
    return results


def _name_twins(name, entries):
    """同名族近似重复：A 恰为 B 的连字符前缀（如 crm-support / crm-support-snake）。
    检索只挑中其一、调用方按名字加载时容易张冠李戴，把 twin 附在结果上供甄别。"""
    base = (name or "").strip().lower()
    if len(base) < 4:
        return []
    out = []
    for t in entries:
        tn = (t.get("name") or "").strip().lower()
        if not tn or tn == base or t.get("excluded"):
            continue
        if tn.startswith(base + "-") or base.startswith(tn + "-"):
            out.append({
                "name": t.get("name"),
                "description": (t.get("description") or "")[:80],
            })
            if len(out) >= 3:
                break
    return out


def _single_char_search(entries, query, top, cli_excludes, include_excluded,
                        agent_only=False):
    """单字查询兜底：terms() 丢弃单字（防噪声），但用户明确只搜一个字时按字面
    子串匹配——名字命中 > 触发词命中 > 描述命中；排除/自环/硬顶规则与主检索一致。"""
    raw = (query or "").strip().lower()
    if not raw:
        return []
    hits = []
    for e in entries:
        ename = e.get("name", "")
        if not include_excluded:
            if e.get("excluded"):
                continue
            is_ex, _ = is_skill_excluded(ename, cli_excludes)
            if is_ex:
                continue
        if agent_only and e.get("agent_bound") is False:
            continue
        if ename == "skill-gateway":
            continue
        if raw in ename.lower():
            score = 6.0
        elif raw in " ".join(e.get("triggers") or []).lower():
            score = 3.0
        elif raw in (e.get("description") or "").lower():
            score = 1.5
        else:
            continue
        hits.append((score, e, [raw]))
    hits.sort(key=lambda x: (-x[0], x[1].get("name", "").lower()))
    return hits[:top]


def related(
    entries, name, top=5, edges=None, cli_excludes=None, include_excluded=False
):
    """找与某技能相关的其它技能，返回 [(权重, 关系类型, 记录)]。
    优先用索引里**已存好的关系边**（一次构建、related/stats/export 结果一致）；
    传 edges=None 或索引是旧版没存边时，就地现算一份（行为与以前一致）。"""
    top = max(1, min(top or 5, MAX_RETRIEVAL_TOP))
    me = [e for e in entries if e["name"].lower() == name.lower()]
    if not me:
        return []
    if edges is None:
        edges = build_edges(entries)
    by_id = {entry_id(e): e for e in entries}
    mid = entry_id(me[0])
    best = {}
    for ed in edges:
        if ed.get("source") == mid:
            other = ed.get("target")
        elif ed.get("target") == mid:
            other = ed.get("source")
        else:
            continue
        e = by_id.get(other)
        if e is None:
            continue
        if not include_excluded:
            if e.get("excluded"):
                continue
            is_ex, _ = is_skill_excluded(e.get("name", ""), cli_excludes)
            if is_ex:
                continue
        # 同一个技能可能同时有 family 和 overlap 两条边 → 每个技能只展示一行，取权重高的那条
        if other not in best or ed.get("weight", 0) > best[other][0]:
            best[other] = (ed.get("weight", 0), ed.get("type", "?"), e)
    hits = sorted(best.values(), key=lambda x: -x[0])
    return hits[:top]


# ── 关系边（ID + edges）──────────────────────────────────────────────────────────
# 每个记录有一个**稳定 ID**（`skill:<名>` / `mcp:<名>`）：边的两端只认 ID，不靠"名字刚好一样"。
# 关系在 write_index 时算一次、存进索引，related / stats / export 都读它，不再各自现算。
# 两类关系：
#   overlap —— 描述/触发词的词重叠（Jaccard），"做的事很像"
#   family  —— 同族命名（共享前缀命名空间，如 tencent-docx / tencent-pptx），"同一家的"
EDGE_MIN_JACCARD = 0.12  # 低于此相似度不成边（噪声）
EDGE_TOP_K = 6  # 每个节点最多保留几条 overlap 边
EDGE_MAX_DF = 40  # 出现在超过这么多个记录里的词不参与配对（"技能""skill"这类虚词）
EDGE_MAX_GROUP = 40  # 同族成员上限，超过就不成边（前缀太通用，多半是噪声）
EDGE_CLIQUE_MAX = 6  # 同族成员 <= 这么多时两两成边；再多就改"星形"（只连到族根）


def entry_id(e):
    """稳定命名空间 ID：`skill:<name>` / `mcp:<name>`。"""
    return f"{e.get('type', 'skill')}:{e['name']}"


def _overlap_edges(entries, top_k, min_j):
    """描述/触发词重叠 → overlap 边。用倒排表只比"共享 >=2 个词"的对，避免两两全比。
    只对**技能**成边：MCP 的 description 是扫描器按 `MCP server 'x' (cmd)` 生成的模板串，
    两条之间天然高度重合，拿它算相似度只会得到一堆假边。"""
    inv, terms_by_id = {}, {}
    for e in entries:
        if e.get("type") != "skill":
            continue
        i = entry_id(e)
        ts = entry_terms(e)
        terms_by_id[i] = ts
        for t in ts:
            inv.setdefault(t, []).append(i)
    shared_cnt = {}
    for ids in inv.values():
        if len(ids) < 2 or len(ids) > EDGE_MAX_DF:
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                k = (ids[a], ids[b])
                shared_cnt[k] = shared_cnt.get(k, 0) + 1
    cand = []
    for (u, v), shared in shared_cnt.items():
        if shared < 2:
            continue
        j = shared / len(terms_by_id[u] | terms_by_id[v])
        if j >= min_j:
            cand.append((u, v, round(j, 3)))
    cand.sort(key=lambda x: -x[2])
    out, deg = [], {}
    for u, v, j in cand:  # 贪心：每个节点最多 top_k 条，防止大簇里的人人相连
        if deg.get(u, 0) >= top_k or deg.get(v, 0) >= top_k:
            continue
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1
        out.append((u, v, j))
    return out


def _family_edges(entries):
    """同族命名 → family 边。小族两两成边（真·完全图）；大族只连到族根，
    避免 20 人一族就写出 190 条边那种体积噪声。"""
    groups = {}
    for e in entries:
        parts = re.split(r"[-_.]", e["name"])
        if len(parts) >= 2 and len(parts[0]) >= 3:
            groups.setdefault(parts[0].lower(), []).append(e)
    out = []
    for ns in sorted(groups):
        es = groups[ns]
        if len(es) < 2 or len(es) > EDGE_MAX_GROUP:
            continue
        ids = sorted(entry_id(e) for e in es)
        if len(ids) > EDGE_CLIQUE_MAX:
            # 族根 = 名字就是命名空间本身的那个；没有就取最短名
            root = next((i for i in ids if i.split(":", 1)[1].lower() == ns), ids[0])
            out += [(root, x, 1.0) for x in ids if x != root]
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                out.append((ids[a], ids[b], 1.0))
    return out


def _depends_on_edges(entries):
    """技能 A 的 SKILL.md/triggers 引用了技能 B 或 MCP 的名字 → depends_on 边（有向）。
    扫描每个技能的 description + triggers 中是否出现其他技能/MCP 名字（词边界匹配），
    出现则认为 A 依赖 B。MCP 也可作为依赖目标（被技能引用时产生 depends_on 边）。
    词边界：子串匹配会让名叫 code/pdf/api 的技能命中 codebase/pdfs/rapid，
    大量假边固化进索引污染 related/图谱。"""
    weights = load_edge_weights()
    # 构建名称→ID 映射（小写键）——包含 skill 和 mcp
    name_to_id = {}
    for e in entries:
        # skill 和 mcp 都可被依赖
        name_to_id[e["name"].lower()] = entry_id(e)

    # 预编译词边界正则（名字可能含正则元字符，先 escape）
    name_patterns = [
        (name, nid, re.compile(rf"(?<![a-z0-9_-]){re.escape(name)}(?![a-z0-9_-])"))
        for name, nid in name_to_id.items()
    ]

    out = []
    seen = set()
    for e in entries:
        if e.get("type") != "skill":
            continue
        my_id = entry_id(e)
        # 收集该技能的文本：description + triggers
        text_parts = [e.get("description", "")]
        for t in e.get("triggers") or []:
            text_parts.append(t)
        text = " ".join(text_parts).lower()
        # 检查是否引用了其他技能/MCP名
        for other_name, other_id, pat in name_patterns:
            if other_id == my_id:
                continue
            if pat.search(text):
                k = (my_id, other_id, "depends_on")
                if k not in seen:
                    seen.add(k)
                    out.append((my_id, other_id, "depends_on", weights["depends_on"]))
    return out


def _contains_edges(entries):
    """父子包含关系：A 是 B 的子技能 → contains 边（有向，从父到子）。
    基于 family 边的命名空间 + 命名前缀规则：
    如 pua-p7 是 pua（或 pua-cancel-loop）的子技能，
    code-review 是 code 的子技能等。
    只在族根存在时建立 contains 边。"""
    weights = load_edge_weights()
    # 先收集所有技能名和 ID
    name_to_id = {}
    for e in entries:
        if e.get("type") != "skill":
            continue
        name_to_id[e["name"].lower()] = entry_id(e)

    # 按命名前缀分组
    groups = {}
    for e in entries:
        if e.get("type") != "skill":
            continue
        parts = re.split(r"[-_.]", e["name"])
        if len(parts) >= 2 and len(parts[0]) >= 3:
            groups.setdefault(parts[0].lower(), []).append(e)

    out = []
    seen = set()
    for ns, es in groups.items():
        if len(es) < 2:
            continue
        # 族根 = 名字就是命名空间本身的那个（如 "pua" 或 "code"）
        ns_id = name_to_id.get(ns)
        if ns_id is None:
            # 没有精确族根，取最短名作为族根
            shortest = min(es, key=lambda x: len(x["name"]))
            ns_id = entry_id(shortest)
        for e in es:
            child_id = entry_id(e)
            if child_id == ns_id:
                continue
            k = (ns_id, child_id, "contains")
            if k not in seen:
                seen.add(k)
                out.append((ns_id, child_id, "contains", weights["contains"]))
    return out


def _similar_edges(entries, top_k=3, min_shared=3):
    """基于 content-index 正文 terms 的语义相似边（IDF 加权 Jaccard）。
    优先使用 chunk 缓存的 terms 字段（覆盖正文关键词），比仅用 summary+file 更精准。
    用 IDF 加权：稀有词（如 'xlsx'/'workbook'）权重高，常见词（如 'skill'/'use'）权重低，
    使相似度更反映"做同样事"而非"都是技能描述"。
    需要 content-index 已构建；不可用时返回空列表。"""
    try:
        import content_index
    except ImportError:
        return []
    doc = content_index.read_content_index()
    if not doc or not doc.get("chunks"):
        return []

    chunks = doc["chunks"]
    terms_cache = doc.get("terms_cache") or {}
    # 按技能分组 chunks，收集每个技能的词集
    skill_terms = {}  # skill_name → set of terms
    for c in chunks:
        sname = c.get("skill", "")
        if not sname:
            continue
        # 优先使用内嵌 terms 缓存（覆盖正文关键词），否则回退到 summary+file
        cached = terms_cache.get(str(c.get("id")))
        if cached:
            ts = set(cached)
        elif c.get("terms"):
            ts = set(c["terms"])
        else:
            text = " ".join([c.get("summary", ""), c.get("file", "")])
            ts = terms(text)
        if not ts:
            continue
        skill_terms.setdefault(sname, set())
        skill_terms[sname] |= ts

    if len(skill_terms) < 2:
        return []

    # 计算 IDF：跨技能文档频率
    N = len(skill_terms)
    df = {}
    for sname, ts in skill_terms.items():
        for t in ts:
            df[t] = df.get(t, 0) + 1
    idf = {t: max(0.1, 1.0 + (N - df_t + 0.5) / (df_t + 0.5)) for t, df_t in df.items()}

    # 构建技能名→ID 映射
    name_to_entry = {}
    for e in entries:
        if e.get("type") == "skill":
            name_to_entry[e["name"].lower()] = e
    skill_terms_lower = {}
    for sname, ts in skill_terms.items():
        skill_terms_lower[sname.lower()] = ts

    # 计算技能间 IDF 加权 Jaccard 相似度。
    # 倒排配对（同 _overlap_edges）：只对"共享 >= min_shared 个词"的技能对算相似度，
    # 且出现在过多技能里的词（虚词）不参与配对——正文 terms 接入后每个技能的词集
    # 有数千项，两两全比是 50 万对 × 大集合交并，分钟级都打不住。
    # 再加预算：按 df 升序（最稀有的共现词优先）累计配对，槽位用完即停——
    # 中文高频 2-gram（"配置/文件"）几乎人人共享，真正的相似信号在稀有词上。
    # 精算不用大集合交并：配对时顺带收集每对共享的稀有词；
    # union 权重用恒等式 w(A∪B) = w(A) + w(B) - w(A∩B)。
    SIMILAR_PAIR_BUDGET = 1_000_000  # 配对计数槽位上限
    SIMILAR_EVAL_BUDGET = 10_000    # 进入加权 Jaccard 精算的技能对上限
    inv = {}
    for sname, ts in skill_terms_lower.items():
        for t in ts:
            inv.setdefault(t, []).append(sname)
    terms_by_df = sorted(
        ((t, names) for t, names in inv.items() if 2 <= len(names) <= EDGE_MAX_DF),
        key=lambda x: len(x[1]),
    )
    shared_cnt = {}
    shared_terms = {}
    budget = SIMILAR_PAIR_BUDGET
    for t, names in terms_by_df:
        if budget <= 0:
            break
        ns = sorted(names)
        budget -= len(ns) * (len(ns) - 1) // 2
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                k = (ns[i], ns[j])
                shared_cnt[k] = shared_cnt.get(k, 0) + 1
                lst = shared_terms.get(k)
                if lst is None:
                    shared_terms[k] = [t]
                else:
                    lst.append(t)

    # 每技能"稀有词"（df ≤ EDGE_MAX_DF，即参与配对的那部分词汇）的 IDF 总权重——
    # 分子（共享稀有词）与分母必须在同一词汇刻度上，EDGE_MIN_JACCARD 的 0.12 才有意义
    weight_rare = {
        s: sum(idf.get(t, 0.1) for t in ts if df.get(t, 0) <= EDGE_MAX_DF)
        for s, ts in skill_terms_lower.items()
    }

    cand_pairs = sorted(
        ((a, b) for (a, b), sc in shared_cnt.items() if sc >= min_shared),
        key=lambda p: -shared_cnt[(p[0], p[1])],
    )[:SIMILAR_EVAL_BUDGET]

    out = []
    seen = set()
    for a_name, b_name in cand_pairs:
        inter_weight = sum(idf.get(t, 0.1) for t in shared_terms[(a_name, b_name)])
        union_weight = weight_rare[a_name] + weight_rare[b_name] - inter_weight
        if union_weight <= 0:
            continue
        j = inter_weight / union_weight
        if j < EDGE_MIN_JACCARD:
            continue
        # 获取 ID
        a_entry = name_to_entry.get(a_name)
        b_entry = name_to_entry.get(b_name)
        if not a_entry or not b_entry:
            continue
        a_id = entry_id(a_entry)
        b_id = entry_id(b_entry)
        k = tuple(sorted([a_id, b_id]) + ["similar"])
        if k not in seen:
            seen.add(k)
            out.append((a_id, b_id, "similar", round(j, 3)))

    # 按 weight 降序，每个节点最多 top_k 条
    out.sort(key=lambda x: -x[3])
    result, deg = [], {}
    for a_id, b_id, t, w in out:
        if deg.get(a_id, 0) >= top_k or deg.get(b_id, 0) >= top_k:
            continue
        deg[a_id] = deg.get(a_id, 0) + 1
        deg[b_id] = deg.get(b_id, 0) + 1
        result.append((a_id, b_id, t, w))
    return result


def build_edges(entries, top_k=EDGE_TOP_K, min_jaccard=EDGE_MIN_JACCARD):
    """把技能间关系固化成边：`{source,target,type,direction,weight}`（节点引用 ID）。
    输出顺序固定（按 source/type/target 排序），同一份索引重复构建结果一致。
    边类型：
      overlap   — 描述/触发词词重叠（Jaccard），"做的事像"
      family    — 同族命名（共享前缀命名空间），"同一家的"
      depends_on — A 的描述/触发词引用了 B 的名字，"A 依赖 B"（有向）
      contains  — A 是 B 的父技能，"A 包含 B"（有向，从父到子）
      similar   — 基于 content-index 分块重叠的语义相似，'内容像'
    """
    seen, rows = set(), []
    for u, v, w in _overlap_edges(entries, top_k, min_jaccard):
        k = (u, v, "overlap")
        if k not in seen:
            seen.add(k)
            rows.append((u, v, "overlap", w))
    for u, v, w in _family_edges(entries):
        k = (u, v, "family")
        if k not in seen:
            seen.add(k)
            rows.append((u, v, "family", w))
    for u, v, t, w in _depends_on_edges(entries):
        k = (u, v, t)
        if k not in seen:
            seen.add(k)
            rows.append((u, v, t, w))
    for u, v, t, w in _contains_edges(entries):
        k = (u, v, t)
        if k not in seen:
            seen.add(k)
            rows.append((u, v, t, w))
    for u, v, t, w in _similar_edges(entries):
        k = (u, v, t)
        if k not in seen:
            seen.add(k)
            rows.append((u, v, t, w))
    rows.sort(key=lambda x: (x[0], x[2], x[1]))
    return [
        {
            "source": u,
            "target": v,
            "type": t,
            "direction": (
                "directed" if t in ("depends_on", "contains") else "undirected"
            ),
            "weight": w,
        }
        for u, v, t, w in rows
    ]


def build_id_map(entries):
    """构建稳定 ID -> 技能项字典映射。"""
    return {entry_id(e): e for e in entries}


def get_1hop_neighbors(edges, eid, by_id=None):
    """查询指定节点的 1-hop 关联邻居。
    返回: list of dict: {"neighbor_id": str, "name": str, "edge_type": str, "weight": float, "direction": str, "rel_dir": str, "entry": dict or None}
    """
    neighbors = []
    for ed in edges or []:
        src, tgt = ed.get("source", ""), ed.get("target", "")
        etype = ed.get("type", "?")
        weight = ed.get("weight", 0)
        direction = ed.get("direction", "undirected")

        other_id = None
        rel_dir = "out"
        if src == eid:
            other_id = tgt
            rel_dir = "out"
        elif tgt == eid:
            other_id = src
            rel_dir = "in"

        if other_id:
            entry = by_id.get(other_id) if by_id else None
            name = entry["name"] if entry else other_id.split(":", 1)[-1]
            neighbors.append(
                {
                    "neighbor_id": other_id,
                    "name": name,
                    "edge_type": etype,
                    "weight": weight,
                    "direction": direction,
                    "rel_dir": rel_dir,
                    "entry": entry,
                }
            )
    return neighbors


def get_collaborative_neighbors(entries, e, by_id=None, top_k=3):
    """当静态图谱边为空或稀疏时，智能推导同领域与工程协同联动技能。
    保证任何技能在知识图谱中都具备明确的协同联动伙伴，杜绝单点孤立。
    """
    if not entries or not e:
        return []
    my_name = e.get("name", "").lower()
    my_terms = entry_terms(e)

    candidates = []
    for other in entries:
        if other.get("excluded"):
            continue
        other_name = other.get("name", "").lower()
        if other_name == my_name:
            continue
        other_terms = entry_terms(other)
        shared = my_terms & other_terms
        meaningful_shared = {t for t in shared if len(t) >= 2 and not t.isdigit()}
        if meaningful_shared:
            score = len(meaningful_shared) / max(len(my_terms | other_terms), 1)
            candidates.append((score, other, list(meaningful_shared)))

    candidates.sort(key=lambda x: -x[0])
    out = []
    for score, other, shared_words in candidates[:top_k]:
        out.append(
            {
                "neighbor_id": entry_id(other),
                "name": other["name"],
                "edge_type": "collaborates",
                "weight": round(min(0.95, 0.5 + score * 2), 2),
                "direction": "undirected",
                "rel_dir": "both",
                "entry": other,
                "shared": shared_words,
            }
        )
    return out
