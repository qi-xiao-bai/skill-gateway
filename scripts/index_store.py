#!/usr/bin/env python3
# index_store.py - 索引读写 + 全量/增量构建 + L0/L1 文本（skill-gateway）
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 索引里固化 ID / 关系边(edges) / 覆盖口径(meta)，供 related/stats/export 复用
import json
import os
import sys
import tempfile
import time
from collections import Counter

import paths
import retrieval
from paths import ROOT, is_skill_excluded, out_path

# 平台桥接"仅描述"镜像的占位标记（platform_bridge.py 写入；audit 据此判定"这文件不是完整 SKILL.md"）。
# 必须单一来源：改这里，platform_bridge 与 reports 同时生效。
MIRROR_STUB_MARK = "本文件由 platform_bridge.py 依据平台技能清单生成"


def atomic_write(path, text):
    """tmp + os.replace 原子写：进程被杀或并发写不会留下半截 JSON。
    同目录落临时文件保证与目标同一文件系统，os.replace 在 Windows 上也是原子的。"""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".idx-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
        raise


def is_stub_file(path):
    """该 SKILL.md 是否是桥接生成的"仅描述"占位文件（非完整 SKILL.md）。"""
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MIRROR_STUB_MARK in f.read()
    except OSError:
        return False


def skill_file(e):
    """某技能记录的 SKILL.md 路径。
    没有本地文件（如 import 进来的平台记录，path 为空）时返回 ""，
    避免 os.path.join("", "SKILL.md") 退化成相对 cwd 的 "SKILL.md"（会误读到别的文件）。"""
    if e.get("type") != "skill" or not e.get("path"):
        return ""
    return os.path.join(e["path"], "SKILL.md")


INDEX_VER = "skill-index/v3"  # v3：跨类型同名去重 + depends_on 词边界建边 + similar 倒排预算（v2 索引带假边/重名，强制重建）


def coverage(entries):
    """覆盖口径：这份索引**覆盖了什么、缺什么**。
    索引不只是一串条目，还要能回答"每类多少、从哪些来源来、哪些条目信息不全"
    （思路借鉴 Understand Anything 的 meta.json / componentCoverage）。
    单次遍历顺带算出 full_text_chars（全文基线体积），调用方不必再整库重读一遍。"""
    n_skill = sum(1 for e in entries if e["type"] == "skill")
    n_conn = sum(1 for e in entries if e.get("is_connector") or e["type"] == "connector")
    n_agent = sum(1 for e in entries if e.get("is_agent") or e["type"] == "agent")
    n_mcp = len(entries) - n_skill - n_conn - n_agent
    no_desc, no_trig, stub, no_file = [], [], [], []
    full_chars = 0
    for e in entries:
        if e["type"] != "skill":
            continue
        if not (e.get("description") or "").strip():
            no_desc.append(e["name"])
        if not (e.get("triggers") or []):
            no_trig.append(e["name"])
        p = skill_file(e)
        if not p or not os.path.isfile(p):
            no_file.append(e["name"])
            continue
        if is_stub_file(p):
            stub.append(e["name"])
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                full_chars += len(f.read())
        except OSError as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
    return {
        "total": len(entries),
        "skill": n_skill,
        "mcp": n_mcp,
        "connector": n_conn,
        "agent": n_agent,
        "by_source": dict(Counter(e["source"] for e in entries).most_common()),
        "by_visibility": dict(Counter(e.get("visibility", "ready") for e in entries)),
        "agent_bound": sum(1 for e in entries if e.get("agent_bound") is True),
        "agent_unbound": sum(1 for e in entries if e.get("agent_bound") is False),
        "with_references": sum(1 for e in entries if e.get("has_references")),
        # 全文覆盖：detail 能不能真的取出全文（计数 + 名单分开给，别把两者混成一个键）
        "full_text": n_skill - len(stub) - len(no_file),
        "stub": len(stub),
        "no_file": len(no_file),
        # 全文基线体积（含占位文件；index_footprint 据此决定是否给压缩率）
        "full_text_chars": full_chars,
        # 排除名单
        "excluded": sum(1 for e in entries if e.get("excluded")),
        "excluded_names": [e["name"] for e in entries if e.get("excluded")],
        # 缺什么：直接影响"被发现率"（下面几个都是**名单**）
        "no_description": no_desc,
        "no_triggers": no_trig,
        "stub_names": stub,
        "no_local_text": no_file,
    }


# ── 索引文本格式（借鉴 AOCI：纯文本 / 高密度 / 字段顺序固定 / 硬配额）──────────────────
# 分工：机器读 skill-index.json（紧凑、保真，search/detail/audit 都用它）；
#      模型读 skill-index.llms.txt（配额化，启动只加载它）。
# 硬规矩：索引里**只放定位用摘要**，不复制 SKILL.md 全文 —— 全文属 L2，命中后再取。
DENSE_VER = "skill-index/dense-v1"
DENSE_QUOTA_DESC = (
    400  # 单条描述字符上限（约中位数的 3 倍，只夹长尾；真值仍在 skill-index.json）
)
DENSE_QUOTA_TRIG = 8  # 单条触发词个数上限


def _one_line(s):
    """折叠所有空白成一行：一条记录必须占且只占一行，才能按行做预算/截取。"""
    return " ".join((s or "").split())


def dense_line(e):
    """一条记录一行：`<名字>[<标签>]: <描述> | 触发: t1、t2`
    标签：s=skill / m=mcp / c=connector / a=agent，R=含 references。字段顺序固定 —— 机器与模型都能稳定解析。"""
    t = "s" if e["type"] == "skill" else ("a" if (e.get("is_agent") or e["type"] == "agent") else ("c" if e.get("is_connector") else "m"))
    tag = t + ("R" if e.get("has_references") else "")
    desc = _one_line(e.get("description"))
    if len(desc) > DENSE_QUOTA_DESC:
        desc = desc[:DENSE_QUOTA_DESC] + "…"
    trig = [t for t in (e.get("triggers") or []) if t][:DENSE_QUOTA_TRIG]
    tail = f" | 触发: {'、'.join(trig)}" if trig else ""
    return f"{e['name']}[{tag}]: {desc}{tail}"


def desc_clipped(e):
    """该条描述在 dense 索引里是否被配额截断（真值始终完整保留在 skill-index.json 里）。"""
    return len(_one_line(e.get("description"))) > DENSE_QUOTA_DESC


def _retrievable(entries):
    """进入模型可读索引（L0/L1）的条目 = 未被排除的（excluded：.skillexclude 规则 /
    清单 blocked / 平台未绑定 off-list）。检索口径=平台可用，L1 不能泄漏口径外条目，
    否则模型会把不可用技能当候选去选型。"""
    return [e for e in entries if not e.get("excluded")]


def l0_text(entries):
    return "\n".join(e["name"] for e in _retrievable(entries)) + "\n"


def l1_text(entries):
    """L1 = 高密度索引本体（一条一行，不含头块）。只收可用条目。"""
    return "\n".join(dense_line(e) for e in _retrievable(entries)) + "\n"


def dense_text(entries):
    """模型面向的完整索引：头块（声明格式/计数/图例/配额/硬规矩）+ L1 本体。
    头块让模型不必猜格式；「不抄全文」这条硬规矩直接写在文件里，防止后续被膨胀。"""
    entries = _retrievable(entries)
    n_s = sum(1 for e in entries if e["type"] == "skill")
    n_c = sum(1 for e in entries if e.get("is_connector") or e["type"] == "connector")
    n_a = sum(1 for e in entries if e.get("is_agent") or e["type"] == "agent")
    n_m = len(entries) - n_s - n_c - n_a
    parts = [f"{n_s} skill", f"{n_m} mcp"]
    if n_c:
        parts.append(f"{n_c} connector")
    if n_a:
        parts.append(f"{n_a} agent")
    head = [
        "#SKILL-INDEX: 1",
        f"#Format: {DENSE_VER}   (plain text; one record per line)",
        "#Count: " + " + ".join(parts) + f" = {len(entries)}",
        "#Legend: <name>[tags]: <description> | 触发: <t1>、<t2>    tags: s=skill m=mcp c=connector a=agent R=has-references",
        f"#Quota: description<={DENSE_QUOTA_DESC}chars, triggers<={DENSE_QUOTA_TRIG}",
        "#Rule: 这里只放「定位用」摘要；SKILL.md 全文属 L2，命中后再取，不要抄进本文件",
        "",
    ]
    return "\n".join(head) + "\n" + l1_text(entries)


def write_index(entries):
    """写两份索引。机器那份除 entries 外还带：稳定 ID、排除标记(excluded)、关系边(edges)、覆盖口径(meta)。"""
    for e in entries:
        e["id"] = retrieval.entry_id(e)
        if e.get("visibility") in ("blocked", "off-list"):
            # 口径打标优先：清单 blocked / off-list 的排除不按 .skillexclude 重算，
            # 保留 build 时写入的 exclude_reason
            continue
        is_ex, reason = is_skill_excluded(e["name"])
        e["excluded"] = is_ex
        if is_ex:
            e["exclude_reason"] = reason
        elif "exclude_reason" in e:
            del e["exclude_reason"]
    edges = retrieval.build_edges(entries)
    meta = coverage(entries)  # full_text_chars 已在覆盖口径单遍中算出
    meta["edges"] = len(edges)
    # 缓存 L1 索引体积，供 index_footprint() 使用
    l1 = dense_text(entries)
    meta["l1_chars"] = len(l1)
    # ① 机器可读：紧凑 JSON —— 不缩进（缩进只对人好看，进上下文纯属浪费 token）
    doc = {
        "version": INDEX_VER,
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(entries),
        "meta": meta,
        "edges": edges,
        "entries": entries,
    }
    atomic_write(out_path("skill-index.json"), json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    # ② 模型可读：高密度纯文本（启动只加载这一份）
    atomic_write(out_path("skill-index.llms.txt"), l1)
    # ③ 可视化面板：自动生成 skill-dashboard.html（保证索引与可视化始终同步，无需手动额外触发）
    try:
        import reports
        html, _, _, _ = reports.generate_dashboard_html(entries, edges)
        atomic_write(out_path("skill-dashboard.html"), html)
    except Exception as _ex:
        print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)


def read_doc():
    """读整份索引文档（含 meta / edges）；不存在/损坏/**版本不符**返回 None。
    版本校验：旧格式索引缺少 v2 字段（edges/meta）会被新逻辑静默误读，
    所以版本不符一律视同不存在，让上层走重建。"""
    p = out_path("skill-index.json")
    if not os.path.isfile(p):
        legacy_p = os.path.join(paths.ROOT, "skill-index.json")
        if os.path.isfile(legacy_p):
            p = legacy_p
        else:
            return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("version") != INDEX_VER:
        return None
    return d


def _stale_ratio(entries, sample_size=50):
    """采样校验条目路径存在率：返回缺失比例(0~1)。
    从 entries 中抽取最多 sample_size 条 skill 条目，检查其 path 是否仍存在。
    返回 None 表示无法判定（条目不足或无 path 字段）。"""
    import random as _rand

    skills = [e for e in entries if e.get("type") == "skill" and e.get("path")]
    if len(skills) < 3:
        return None  # 条目太少，无法判定
    sample = skills if len(skills) <= sample_size else _rand.sample(skills, sample_size)
    missing = sum(1 for e in sample if not os.path.isdir(e["path"]))
    return missing / len(sample)


def read_index():
    """读条目列表；不存在/损坏/异机陈旧索引返回 None。
    异机检测：采样校验条目 path 存在率，>90% 缺失即判定为异机陈旧索引，
    视同不存在，让 load_index() 转入重建/桥接自愈。"""
    d = read_doc()
    if not d or not isinstance(d.get("entries"), list):
        return None
    es = d["entries"]
    ratio = _stale_ratio(es)
    if ratio is not None and ratio > 0.9:
        print(
            f"[index] ⚠ 异机陈旧索引：采样路径缺失率 {ratio:.0%}，视同不存在 → 自动重建"
        )
        return None
    return es


def read_edges():
    """读关系边。索引里**没有 edges 字段**（旧版索引）返回 None，与"有字段但为空列表"区分开。"""
    d = read_doc()
    if not d or "edges" not in d:
        return None
    return d.get("edges") or []


def write_edges(edges):
    """更新索引中的关系边（覆盖写入）。保留其他字段不变。"""
    d = read_doc()
    if not d:
        return False
    d["edges"] = edges
    # 更新 meta 中的边数
    if "meta" in d and isinstance(d["meta"], dict):
        d["meta"]["edges"] = len(edges)
    atomic_write(out_path("skill-index.json"), json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    return True


def build_index(broad="auto"):
    """全量重建（首次使用 / 想彻底刷新时用）。
    权威清单存在时与 update 同口径走三段合并（清单 ready/blocked + 磁盘 off-list），
    否则单跑 index 会把 off-list 层覆盖丢掉。"""
    import scanner

    avail = scanner.available_list_entries()
    if avail is not None:
        entries, _st = _update_from_available_list(read_index(), avail)
        return entries
    entries = scanner.scan_all(broad)
    write_index(entries)
    return entries


# ── 易失层自愈：索引缺失、或只扫到"本技能自己"时，自动走平台桥接 ────────────────────
def is_self_only(entries):
    """索引里是否"只有本技能自己"（0 项也算）。
    沙箱里技能自己总被解压落盘，所以 index 很少真返回 0，而是"只有自己" —— 用这个判定自愈。"""
    mine = os.path.normcase(os.path.realpath(ROOT))
    for e in entries:
        if e.get("type") != "skill":
            continue
        if os.path.normcase(os.path.realpath(e.get("path") or "")) != mine:
            return False
    return True



def auto_bridge(mode="list"):
    """只扫到"本技能自己"时，若技能包里有平台技能清单，就自动落镜像并改用镜像重扫。
    两档语义（2026-09-25 定稿）：
    - mode="list"（默认）：建**元数据索引**（一行一技能，定位/路由用）——这是合法的索引，
      输出诚实标注"仅定位用"；audit/detail/正文检索这些 L2 能力需要全文，不在此档。
    - mode="full"：**全量构建**——清单缺任何一项完整 SKILL.md 就中止、不写索引（rc=2），
      并把"怎么取全文"讲清楚；绝不拿元数据冒充全量构建。
    SKILL_GATEWAY_NO_AUTO=1 可关闭；桥接子进程里有 SKILL_GATEWAY_BRIDGE_RUN=1 防递归。
    返回 (entries|None, bridge_res|None)；full 被中止时 res 带 partial=True + error。"""
    try:
        import platform_bridge as pb  # 延迟导入：platform_bridge 反向依赖本模块
    except Exception:
        return None, None
    src = pb.default_src()
    if not src:
        return None, None
    print(f"[index] 只扫到本技能自己；发现平台技能清单: {src}")
    print("[index] 自动走桥接：清单 -> 镜像 -> 重扫"
          + ("（--full：要求全量，缺完整 SKILL.md 即中止）" if mode == "full" else "（元数据档：仅定位用）"))
    res = pb.mirror_and_reindex(
        src=src, mirror=pb.default_mirror(), steps=None, mode=mode
    )
    if res.get("error"):
        if res.get("partial"):
            # 全量被中止：不写索引，把原因带回去给调用方（cmd_index 会补打印取全文步骤）
            print(f"[index] ⚠ 全量构建中止：{res['error']}")
            return None, res
        print(f"[index] 桥接失败: {res['error']}")
        return None, None
    if res.get("partial"):
        print(
            f"[index] 元数据索引就绪（{res['n_full']}/{res['created']} 项带全文）："
            "仅定位/路由用；detail/audit/正文检索需全文 → index --full"
        )
    # 只扫镜像重扫；用完把环境变量还原，避免污染同进程内的后续调用
    prev = {
        k: os.environ.get(k)
        for k in ("SKILL_GATEWAY_SKILL_DIRS", "SKILL_GATEWAY_ONLY_DIRS")
    }
    try:
        os.environ["SKILL_GATEWAY_SKILL_DIRS"] = res["mirror"]
        os.environ["SKILL_GATEWAY_ONLY_DIRS"] = "1"
        return build_index(False), res
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def heal_if_self_only(entries, mode="list"):
    """只扫到"本技能自己"且允许自愈时走桥接。返回 (entries, bridge_res|None)。
    mode 语义同 auto_bridge：list=元数据索引（默认，合法且诚实标注）；full=全量构建
    （被中止时把 res（partial=True + error）透传，让 `index --full` 以 rc=2 报告）。"""
    if not is_self_only(entries):
        return entries, None
    if os.environ.get("SKILL_GATEWAY_BRIDGE_RUN") or os.environ.get(
        "SKILL_GATEWAY_NO_AUTO"
    ):
        return entries, None
    got, res = auto_bridge(mode=mode)
    if got and not is_self_only(got):
        return got, res
    if res and res.get("partial"):
        return entries, res
    return entries, None


def index_footprint(hit_count=None):
    """返回索引足迹一行摘要：命中数/总数 | 索引体积 vs 全文体积 | 节省比例。
    hit_count: 本次检索命中数（可选），不传则只显示索引统计。
    从 meta 缓存读取 l1_chars / full_text_chars，无需重新计算。
    基线里含桥接「仅描述」占位文件时**不给节省比例**——拿占位当全文，
    算出来的压缩率严重偏低（真全文远大于占位），只会误导。"""
    d = read_doc()
    if not d:
        return ""
    meta = d.get("meta") or {}
    n = d.get("count", 0)
    l1 = meta.get("l1_chars", 0)
    full = meta.get("full_text_chars", 0)
    n_stub = meta.get("stub", 0)
    parts = []
    if hit_count is not None:
        parts.append(f"命中 {hit_count}/{n}")
    else:
        parts.append(f"索引 {n} 项")
    if l1 and full and n_stub:
        parts.append(f"索引 {l1:,} 字符（基线含 {n_stub} 项占位非全文，压缩率不可比）")
    elif l1 and full:
        pct = 100 * l1 / full
        parts.append(f"索引 {l1:,} vs 全文 {full:,} 字符")
        parts.append(f"节省 {100 - pct:.1f}% token")
    elif l1:
        parts.append(f"索引 {l1:,} 字符")
    return "[skill-gateway] " + " | ".join(parts) if parts else ""


def load_index():
    """读索引；**索引不存在就自动建一次** —— 任何读命令（list / search / stats …）
    因此都**不需要先手动敲 index**。
    注意：索引存在但**只覆盖到本技能自己**时同样要再试一次桥接自愈 ——
    否则会出现"上次建了个只有自己的索引，之后平台清单来了也永远不再自愈"的死角。"""
    es = read_index()
    if es is None:
        es = build_index()
    es, healed = heal_if_self_only(es)
    if healed and healed.get("partial"):
        print(
            "[index] 元数据索引·仅定位用（查有哪些/用哪个够用）；"
            "detail/audit/正文检索需全文 → index --full"
        )
    return es


def update_index(broad="auto", query=None, targeted=False):
    """增量索引：只解析新增/变更的 SKILL.md，未变项直接复用；首次=全量。
    若 targeted=True 且指定 query，当检测到大批量技能变动时，优先增量更新与 query 语义相关的技能，其余暂挂。
    返回 (entries, 统计 dict)。"""
    import scanner

    old = read_index()

    # 权威可用清单模式：inputs/available_skills.json 存在 → 索引=清单本身，
    # 不做磁盘扫描（MCP/连接器仍走配置发现）
    avail = scanner.available_list_entries()
    if avail is not None:
        return _update_from_available_list(old, avail)

    if old is None:
        entries = scanner.scan_all(broad)
        write_index(entries)
        return entries, {
            "first": True,
            "added": [e["name"] for e in entries],
            "updated": [],
            "removed": [],
            "kept": 0,
            "targeted": False,
            "deferred": [],
        }

    old_skill_by_path = {e["path"]: e for e in old if e["type"] == "skill"}
    old_skill_realpath = {
        os.path.realpath(os.path.join(e["path"], "SKILL.md")): e["name"]
        for e in old
        if e["type"] == "skill"
    }

    # 预检：收集磁盘现有技能与变动情况
    disk_skills = []
    seen_real = set()
    changed_count = 0
    for root, sk in scanner.enumerate_skill_md(broad):
        rk = os.path.realpath(sk)
        if rk in seen_real:
            continue
        seen_real.add(rk)
        skill_dir = os.path.dirname(sk)
        try:
            mt = int(os.path.getmtime(sk))
        except OSError:
            mt = 0
        o = old_skill_by_path.get(skill_dir)
        if o is None or o.get("mtime") != mt:
            changed_count += 1
        disk_skills.append((root, sk, skill_dir, mt, o))

    # 判断是否触发大批量变动下的靶向筛选
    q_terms = retrieval.terms(query) if (targeted and query) else set()
    should_target = targeted and bool(q_terms) and changed_count > 5

    out, seen_names = [], set()
    added, updated, kept, deferred = [], [], [], []

    for root, sk, skill_dir, mt, o in disk_skills:
        # 1. 未变 → 直接复用，不读文件
        if o is not None and o.get("mtime") == mt:
            if o["name"] in seen_names:
                continue
            seen_names.add(o["name"])
            kept.append(o["name"])
            out.append(o)
            continue

        # 2. 变更或新增：若处于靶向模式，判断与 query 是否有语义/关键词关联
        if should_target:
            cand_name = (o["name"] if o else os.path.basename(skill_dir)).lower()
            cand_desc = (o.get("description", "") if o else "").lower()
            cand_terms = retrieval.terms(cand_name + " " + cand_desc)
            if not (q_terms & cand_terms):
                # 不相关变动项：推迟更新
                if o is not None:
                    o_copy = dict(o)
                    o_copy["pending_update"] = True
                    if o_copy["name"] not in seen_names:
                        seen_names.add(o_copy["name"])
                        out.append(o_copy)
                    deferred.append(o["name"])
                continue

        # 3. 编译解析该技能
        e = scanner.make_skill_entry(skill_dir, sk, root, mt)
        if e is None:
            continue
        if e["name"] in seen_names:
            continue
        seen_names.add(e["name"])
        (updated if o is not None else added).append(e["name"])
        out.append(e)

    # MCP：配置很小，直接重扫后按名 diff；跨类型同名（技能 "foo" 与 MCP "foo"）在此统一去重，
    # 否则 doctor 的重名告警与 related/图谱全被污染
    mcp = scanner.scan_mcp()
    old_mcp = {e["name"] for e in old if e["type"] == "mcp"}
    new_mcp = {e["name"] for e in mcp}
    added += sorted(new_mcp - old_mcp)
    removed = sorted(
        (old_mcp - new_mcp)
        | {n for rp, n in old_skill_realpath.items() if rp not in seen_real}
    )
    out = scanner.dedupe_entries(out + mcp)
    out.sort(key=lambda e: (e["type"], e["name"].lower()))
    write_index(out)
    return out, {
        "first": False,
        "added": added,
        "updated": updated,
        "removed": removed,
        "kept": len(kept),
        "targeted": should_target,
        "deferred": deferred,
    }


def _update_from_available_list(old, avail):
    """权威清单模式：单索引三段合并——
    ① 清单 ready（平台可用，默认检索口径）② 清单 blocked（excluded，dashboard 已排除可见）
    ③ 磁盘发现但不在清单里 → off-list（excluded，仅 --all 全量视图可见，不得作为交付依据）。
    同名条目清单优先；MCP/连接器仍走配置发现并参与跨类型同名去重。"""
    import scanner
    old_list = {e["name"].lower(): e for e in (old or [])
                if e.get("type") == "skill"
                and e.get("source") in ("available-list", "profile-authoritative")}
    added, updated, kept, out = [], [], [], []
    list_names = set()

    def _sig(e):
        # 变更签名必须含 description_zh：登记表补/改翻译后重算，条目要走 updated
        # 拿到带翻译的新记录，而不是被 kept 判定复用旧的无翻译条目（翻译永远不显示）
        return (e.get("description"), tuple(e.get("triggers") or []), e.get("path"),
                e.get("category"), e.get("platform"), e.get("excluded", False),
                tuple(e.get("agents") or []), e.get("agent_bound"),
                (e.get("description_zh") or ""))

    for e in avail:
        nm = e["name"]
        list_names.add(nm.lower())
        o = old_list.get(nm.lower())
        if o is None:
            added.append(nm)
        elif _sig(o) != _sig(e):
            updated.append(nm)
        else:
            kept.append(nm)
            e = o  # 未变 → 复用旧记录，保留原 mtime 与内容索引指纹
            if scanner.heal_self_entry(e):
                updated.append(nm)  # 自条目 path 失效修复，计变更保证落盘
        out.append(e)
    removed = {n for n in old_list if n not in list_names}
    ready_bases = {e["name"].lower() for e in avail
                   if e.get("visibility") == "ready"}
    bound_by_name = {e["name"].lower(): e for e in avail}

    # ③ 清单外的磁盘发现：入索引但标记 off-list（清单同名优先，不入默认检索口径）。
    # mtime 增量：未变的直接复用旧条目不重读文件——自愈/重复 update 时这段是隐藏大头。
    old_off = {e["name"].lower() for e in (old or []) if e.get("visibility") == "off-list"}
    old_off_by_path = {e.get("path"): e for e in (old or [])
                       if e.get("visibility") == "off-list" and e.get("path")}
    new_off, seen_off, converted = [], set(), set()
    for root, sk in scanner.enumerate_skill_md("auto"):
        skill_dir = os.path.dirname(sk)
        try:
            mt = int(os.path.getmtime(sk))
        except OSError:
            mt = 0
        o = old_off_by_path.get(skill_dir)
        if o is not None and o.get("mtime") == mt:
            e = o  # 未变 → 复用旧条目
        else:
            e = scanner.make_skill_entry(skill_dir, sk, root, mt)
            if e is None:
                continue
            if e["name"].lower() in old_off:
                updated.append(e["name"])
        low = e["name"].lower()
        if low in list_names or low in seen_off:
            continue  # 清单同名优先；同名磁盘技能只收一次
        # 绑定技能的磁盘变体保持可用（如清单绑定 crm-support、磁盘上是 crm-support-snake）：
        # 名字前缀只是**线索**（双方 ≥4 字符），业务证据才是门槛——描述/触发词与绑定技能
        # 真实重叠（共享词 ≥2 且 Jaccard ≥ 图谱 overlap 边阈值）。只蹭名字没业务的照旧 off-list。
        base = None
        if ready_bases:
            cand = [b for b in ready_bases
                    if (low.startswith(b + "-") or b.startswith(low + "-"))
                    and min(len(low), len(b)) >= 4]
            ets = retrieval.terms((e.get("description") or "") + " "
                                  + " ".join(e.get("triggers") or []))
            best = None
            for b in cand:
                be = bound_by_name.get(b)
                if not be:
                    continue
                bts = retrieval.terms((be.get("description") or "") + " "
                                      + " ".join(be.get("triggers") or []))
                shared = ets & bts
                union = ets | bts
                if len(shared) >= 2 and union and len(shared) / len(union) >= retrieval.EDGE_MIN_JACCARD:
                    j = len(shared) / len(union)
                    if best is None or j > best[1]:
                        best = (b, j)
            base = best[0] if best else None
        if base:
            e = dict(e)
            e["visibility"] = "ready"
            e["variant_of"] = base
            e.pop("excluded", None)
            e.pop("exclude_reason", None)
            if low in old_off:
                updated.append(e["name"])
            else:
                added.append(e["name"])
            converted.add(low)
            seen_off.add(low)
            out.append(e)
            continue
        seen_off.add(low)
        e = dict(e)
        e["visibility"] = "off-list"
        e["excluded"] = True
        e["exclude_reason"] = "平台未绑定（不在权威可用清单，仅 --all 全量视图可见）"
        if low not in old_off:
            added.append(low)
        new_off.append(low)
        out.append(e)
    removed |= (old_off - set(new_off)) - converted  # 转 ready 的变体不算移除

    mcp = scanner.scan_mcp()
    old_mcp = {e["name"] for e in (old or []) if e.get("type") == "mcp"}
    new_mcp = {e["name"] for e in mcp}
    added += sorted(new_mcp - old_mcp)
    removed |= old_mcp - new_mcp
    out = scanner.dedupe_entries(out + mcp)
    out.sort(key=lambda e: (e["type"], e["name"].lower()))
    write_index(out)
    return out, {
        "first": old is None,
        "added": added,
        "updated": updated,
        "removed": sorted(removed),
        "kept": len(kept),
        "targeted": False,
        "deferred": [],
    }


def auto_update_on_miss(query="", broad="auto", targeted=True):
    """检索/问答/编排未命中时的自动自愈机制：
    1. 动态比对扫描当前可扫描的技能库是否有变更。
    2. 若有变化，执行增量更新并同步刷新知识图谱边与分片。
    3. 返回 (entries, edges, st, has_changes)。
    """
    entries, st = update_index(broad=broad, query=query, targeted=targeted)
    has_changes = bool(
        st.get("first")
        or st.get("added")
        or st.get("updated")
        or st.get("removed")
    )
    edges = read_edges() or []
    if has_changes:
        try:
            import bundle as _bundle

            _bundle.write_bundle(entries, edges)
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
        ci_path = out_path("skill-content-index.json")
        if os.path.isfile(ci_path):
            try:
                import content_index as _ci

                _ci.update_content_index(broad)
            except Exception as _ex:
                print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
    return entries, edges, st, has_changes


def merge_import(new_entries):
    """把 import 来的记录并入索引（同名覆盖，其余保留）。返回 (总数, 新增, 更新)。"""
    entries = read_index() or []
    by_name = {e["name"].lower(): i for i, e in enumerate(entries)}
    added, updated = [], []
    for e in new_entries:
        k = e["name"].lower()
        if k in by_name:
            old = entries[by_name[k]]
            if (
                old.get("description") == e["description"]
                and old.get("path") == e["path"]
            ):
                continue
            entries[by_name[k]] = e
            updated.append(e["name"])
        else:
            by_name[k] = len(entries)
            entries.append(e)
            added.append(e["name"])
    entries.sort(key=lambda x: (x["type"], x["name"].lower()))
    write_index(entries)
    return len(entries), added, updated


def add_skill(target):
    """把一个尚未索引的技能加入索引：target 为技能目录路径或技能名。
    返回 (name|None, 说明)。"""
    import scanner
    import skillmd

    entries = read_index() or []
    have = {e["name"].lower() for e in entries}

    found = None
    if os.path.isdir(target):
        sk = os.path.join(target, "SKILL.md")
        if os.path.isfile(sk):
            d = os.path.abspath(target)
            found = (d, sk, "manual")
    if found is None:
        for root, sk in scanner.enumerate_skill_md(True):
            d = os.path.dirname(sk)
            nm, _ = skillmd.parse_skill_md(sk)
            if (nm or os.path.basename(d)).lower() == target.lower():
                found = (d, sk, root)
                break
    if found is None:
        return None, "未找到：既不是含 SKILL.md 的目录，也没在扫描根里找到同名技能"

    d, sk, source = found
    e = scanner.make_skill_entry(d, sk, source)
    if e is None:
        return None, "目录名无效（纯符号装饰的垃圾目录），拒绝入索引"
    if e["name"].lower() in have:
        return e["name"], "已在索引中（跳过）"
    entries.append(e)
    entries.sort(key=lambda x: (x["type"], x["name"].lower()))
    write_index(entries)
    return e["name"], "已加入索引"
