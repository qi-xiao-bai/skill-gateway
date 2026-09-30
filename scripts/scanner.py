#!/usr/bin/env python3
# scanner.py - 扫描技能（任意布局 SKILL.md）与 MCP 配置；支持"只枚举不读取"(供增量索引)
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 新增广域发现 + 扫描根探针(probe_report)，让"0 项"可诊断
# updated 2026-09-16 qjl: 跳过规则改走 .skillignore（内建默认为原 SKIP_DIRS，行为不变）
import json
import os
import re

import skillmd
from paths import (expand, only_dirs, is_ignored, BROAD_MAX_DEPTH, BROAD_MAX_DIRS,
                   BROAD_SKILL_DIRNAMES, ROOT, broad_roots,
                   candidate_roots, candidate_mcp_configs, candidate_mcp_dirs, self_relative_roots)


def iter_skill_md(root, max_depth=5):
    """递归找出 root 下所有 SKILL.md（跳过 .skillignore 命中的目录，遇到技能目录不再下钻）。
    文件名大小写不敏感（Windows/macOS 文件系统本就不区分，skill.md/Skill.md 同样是技能）；
    不跟随符号链接子目录（junction/symlink 环路防护；被链接的根目录本身仍可从扫描根进入）。"""
    root = expand(root)
    if not os.path.isdir(root):
        return
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            names = os.listdir(d)
        except Exception:
            continue
        skill_md = next((n for n in names if n.lower() == "skill.md"), None)
        if skill_md and os.path.isfile(os.path.join(d, skill_md)):
            yield os.path.join(d, skill_md)
            continue
        if depth < max_depth:
            for e in names:
                fp = os.path.join(d, e)
                if os.path.isdir(fp) and not os.path.islink(fp) and not is_ignored(fp, True, rel_to=root):
                    stack.append((fp, depth + 1))


def broad_discover():
    """不知道平台把技能放哪时的兜底：从广域起点浅层搜出"像技能仓库"的目录。
    只认目录名（skills / agent-skills 等），命中即收，不做全盘遍历。"""
    found, seen, visited = [], set(), 0
    for br in broad_roots():
        stack = [(br, 0)]
        while stack:
            d, depth = stack.pop()
            visited += 1
            if visited > BROAD_MAX_DIRS:
                return found
            try:
                names = os.listdir(d)
            except Exception:
                continue
            for nm in names:
                fp = os.path.join(d, nm)
                if not os.path.isdir(fp) or is_ignored(fp, True, rel_to=br):
                    continue
                low = nm.lower()
                if low in BROAD_SKILL_DIRNAMES or low.endswith("-skills"):
                    k = os.path.normcase(os.path.realpath(fp))
                    if k not in seen:
                        seen.add(k)
                        found.append(fp)
                elif depth < BROAD_MAX_DEPTH:
                    stack.append((fp, depth + 1))
    return found


def _iter_from(roots, max_depth=5):
    """从给定根集合枚举 SKILL.md，产出 (source_root, sk_path)。"""
    seen_root = set()
    for r in roots:
        rp = os.path.normcase(os.path.realpath(expand(r)))
        if rp in seen_root:
            continue
        seen_root.add(rp)
        for sk in iter_skill_md(r, max_depth):
            yield rp, sk


def enumerate_skill_md(broad="auto"):
    """按扫描顺序 yield (source_root, skill_md_path)。只做目录遍历，不读文件内容。
    broad=False → 只用约定/配置/自身相对根；
    broad=True  → 追加广域发现（skills / *-skills 目录）；
    broad='auto'→ 约定根 + 广域发现取并集（同名去重，约定根优先）。
    auto 永远并入广域：约定根"非空"不代表"完整"——2026-09-30 网页端实测，沙箱约定
    目录恰好有 101 个技能，/app/data/skills 里的 349 个全被"非空即不兜底"屏蔽。
    先产出约定根、后产出广域根，配合同名去重即保证优先级不变，并集只增不减。
    （only_dirs 开启时仍严格只用显式根，尊重用户的隔离意图。）
    自身相对的祖先目录可能很大，用更浅的 max_depth=3 兜住成本。"""
    base = []
    for r, tag in candidate_roots():
        base += list(_iter_from([r], max_depth=3 if tag == "self" else 5))
    for item in base:
        yield item
    if broad is True or (broad == "auto" and not only_dirs()):
        for item in _iter_from(broad_discover()):
            yield item


def _sane_skill_name(nm):
    """技能名有效性：至少含一个文字字符，且不以 2 个以上符号装饰开头。
    拦截把章节标题/分隔线当技能名的垃圾目录（`------`、`====固定参数====`）。"""
    nm = (nm or "").strip()
    if not nm or not re.search(r"\w", nm):
        return False
    return not re.match(r"^[\W_]{2,}", nm)


# 权威可用清单的候选文件名（相对技能根的 inputs/ 下；后者为兼容名）
AVAILABLE_LIST_FILES = ("available_skills.json", "platform_skills.json")
_BLOCKED_STATUS = {"blocked", "disabled", "unavailable", "off", "停用", "不可用", "已停用"}


def available_list_file():
    """权威可用清单路径：inputs/available_skills.json（兼容 platform_skills.json）。
    存在即视为"本智能体能用什么技能"的完整定义，常规磁盘扫描全部让位。"""
    for nm in AVAILABLE_LIST_FILES:
        p = os.path.join(ROOT, "inputs", nm)
        if os.path.isfile(p):
            return p
    return None


def available_list_doc():
    """读取权威可用清单原始 JSON；不存在/解析失败返回 None。"""
    p = available_list_file()
    if not p:
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, (dict, list)) else None


def available_list_agent():
    """当前 Agent（智能体）名。解析顺序：环境变量 SKILL_GATEWAY_AGENT > 清单顶层 current_agent
    > skill_profile.json 的 current_agent。返回 None 表示未声明——Agent 维度不生效
    （所有条目 agent_bound=True，--agent 无收窄效果）。"""
    v = (os.environ.get("SKILL_GATEWAY_AGENT") or "").strip()
    if v:
        return v
    data = available_list_doc()
    if isinstance(data, dict):
        v = str(data.get("current_agent") or "").strip()
        if v:
            return v
    try:
        import paths
        v = str(paths.load_skill_profile().get("current_agent") or "").strip()
        if v:
            return v
    except Exception:
        pass
    return None


def available_list_entries():
    """解析权威可用清单为索引记录；清单不存在/解析失败返回 None（回退常规扫描）。
    清单格式：JSON 数组或 {"skills": [...], "current_agent": "<名>"}，每项
    {name, description, triggers, path, status, category, platform, agents}。
    agents 数组声明哪些 Agent（智能体）绑定了该技能：
    - 清单完全没写 agents 字段 → Agent 维度未启用，agent_bound 全为 True（--agent 不收窄）
    - 有 agents 字段时：当前 Agent 在数组内 → agent_bound=True；否则 False（--agent 口径不出现）
    status 为 blocked/disabled 等的条目仍入索引（dashboard 已排除页可见）但标记 excluded，
    检索与编排不出现。索引默认口径=平台可见；Agent 绑定是更细一层，非默认。"""
    p = available_list_file()
    if not p:
        # 兼容入口：网页端补丁把绑定名写在 skill_profile.json.authoritative_skills
        # （仅名字数组）。没有完整清单文件时拿它当清单来源（全部 ready、无 agents 信息，
        # 磁盘变体照走业务证据门槛）；要 agents/category/platform 用 agent-index 生成完整清单。
        try:
            import paths
            auth = paths.load_skill_profile().get("authoritative_skills")
        except Exception:
            auth = None
        if isinstance(auth, list) and auth:
            items = [it for it in ({"name": a} for a in auth
                                   if isinstance(a, str) and a.strip()) if it]
            return _entries_from_items(items, source_tag="profile-authoritative")
        return None
    data = available_list_doc()
    if data is None:
        return None
    items = data.get("skills") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return _entries_from_items(items)


def _entries_from_items(items, source_tag="available-list"):
    """清单项 → 索引记录（visibility/agent_bound/category/platform/agents 透传）。"""
    cur_agent = available_list_agent()
    parsed = []
    any_agent_info = False
    for it in items:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        agents = it.get("agents") or []
        if isinstance(agents, str):
            agents = [a.strip() for a in re.split(r"[、,，;；/|]+", agents) if a.strip()]
        agents = [str(a).strip() for a in agents if str(a).strip()]
        has_agents = "agents" in it  # 显式写了 agents 才算绑定信息；缺字段=未知=视为已绑定
        if has_agents:
            any_agent_info = True
        parsed.append((it, agents, has_agents))
    out, seen = [], set()
    for it, agents, has_agents in parsed:
        nm = (it.get("name") or it.get("skill") or it.get("skill_name") or "").strip()
        if not _sane_skill_name(nm) or nm.lower() in seen:
            continue
        seen.add(nm.lower())
        desc = (it.get("description") or it.get("desc") or "").strip()
        trig = it.get("triggers") or []
        if isinstance(trig, str):
            trig = [t.strip() for t in re.split(r"[、,，;；/|]+", trig) if t.strip()]
        status = str(it.get("status") or "").strip().lower()
        blocked = status in _BLOCKED_STATUS
        entry = {
            "name": nm,
            "description": desc,
            "triggers": [t for t in trig if t] or _synthesize_triggers(nm, desc),
            "type": "skill",
            "path": it.get("path") or "",
            "has_references": False,
            "source": source_tag,
            "mtime": 0,
            "visibility": "blocked" if blocked else "ready",
            "agent_bound": True if (cur_agent is None or not any_agent_info
                                    or not has_agents)
                           else (cur_agent in agents),
        }
        if agents:
            entry["agents"] = agents
        if it.get("category"):
            entry["category"] = str(it["category"]).strip()
        if it.get("platform"):
            entry["platform"] = str(it["platform"]).strip()
        if blocked:
            entry["excluded"] = True
            entry["exclude_reason"] = f"blocked（平台绑定状态: {status}，检索/编排不出现）"
        out.append(entry)
    return out


def make_skill_entry(skill_dir, sk_path, source, mtime=None):
    """解析一个技能目录，产出索引记录。
    用 parse_skill_meta（而非 parse_skill_md）：多提取 description_zh/description_en——
    有双语 frontmatter 的技能才能被中文 query 检索到（跨语言可发现性）。
    frontmatter 名无效时回退目录名，两者都无效（纯符号垃圾目录）返回 None 不入索引。
    frontmatter 没写触发词时用 _synthesize_triggers 从名称切分+描述里自动推导，
    否则无触发词技能（平台库 242/349）在检索里几乎不可命中。"""
    meta = skillmd.parse_skill_meta(sk_path)
    nm = meta.get("name") or ""
    if not _sane_skill_name(nm):
        nm = os.path.basename(skill_dir) or ""
    if not _sane_skill_name(nm):
        return None
    desc = meta.get("description") or meta.get("description_zh") or ""
    if mtime is None:
        try:
            mtime = int(os.path.getmtime(sk_path))
        except OSError:
            mtime = 0
    has_ref = any(os.path.isdir(os.path.join(skill_dir, x))
                  for x in ("references", "scripts", "assets", "reference"))
    entry = {
        "name": nm,
        "description": desc,
        "triggers": skillmd.extract_triggers(desc) or _synthesize_triggers(nm, desc),
        "type": "skill",
        "path": skill_dir,
        "has_references": has_ref,
        "source": source,
        "mtime": mtime,
        "visibility": "ready",
    }
    zh = meta.get("description_zh")
    if zh and zh != desc:
        entry["description_zh"] = zh
    return entry


def scan_skills(broad="auto", ignore_list=False):
    # 权威可用清单模式：inputs/available_skills.json 存在时，技能索引=清单本身
    # （本智能体真正绑定可用的技能），约定根/广域/固定根的磁盘扫描全部让位——
    # 平台共享库里的技能再多也不是本智能体能加载的，扫进来只会污染检索口径。
    # ignore_list=True 绕过清单走常规扫描（供 off-list 全量视图采集）。
    avail = None if ignore_list else available_list_entries()
    if avail is not None:
        return avail
    out, seen, seen_names = [], set(), set()
    for root, sk in enumerate_skill_md(broad):
        rk = os.path.realpath(sk)
        if rk in seen:
            continue
        seen.add(rk)
        e = make_skill_entry(os.path.dirname(sk), sk, root)
        if e is None:
            continue  # 纯符号垃圾目录（名称无效），不入索引
        if e["name"].lower() in seen_names:
            continue  # 同名技能只索引一次（多位置安装的去重，大小写不敏感）
        seen_names.add(e["name"].lower())
        out.append(e)
    return out


# 跨类型同名去重的优先级：技能 > MCP > 连接器 > 数字员工。
# 技能与工具撞名时保留技能——本索引器的核心服务对象是技能检索。
_TYPE_PRIORITY = {"skill": 0, "mcp": 1, "connector": 2, "agent": 3}


def dedupe_entries(entries):
    """跨类型同名去重：scan_skills 与 scan_mcp 各自内部去重，但技能 "foo" 与
    MCP "foo" 会双双入索引 —— doctor 的 [重名] 告警、related、图谱全被污染。
    同名时保留类型优先级最高的一条（同优先级保序先见优先）。返回新列表。"""
    best = {}
    order = []
    for e in entries:
        k = e["name"].lower()
        if k not in best:
            best[k] = e
            order.append(k)
            continue
        cur = best[k]
        if _TYPE_PRIORITY.get(e["type"], 9) < _TYPE_PRIORITY.get(cur["type"], 9):
            best[k] = e
    return [best[k] for k in order]


def _tokenize_identifier(name: str):
    """纯算法切分标识符（连字符、下划线、点、冒号、斜杠、驼峰命名拆分）。
    例如: 'tencent-docs' -> ['tencent-docs', 'tencent', 'docs']
          'qq-mail' -> ['qq-mail', 'qq', 'mail']
          'FinanceAuditWorker' -> ['FinanceAuditWorker', 'finance', 'audit', 'worker']
          'tdx_quotes' -> ['tdx_quotes', 'tdx', 'quotes']
    """
    tokens = [name]
    sub = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name)
    parts = re.split(r'[-_:. /]+', sub)
    for p in parts:
        p_clean = p.strip().lower()
        if p_clean and len(p_clean) > 1 and p_clean not in tokens:
            tokens.append(p_clean)
    return tokens


def _find_adjacent_artifact_info(base_dir: str, target_name: str = ""):
    """在指定目录及其伴生子目录中穿透自省元数据文档（SKILL.md, manifest.json, package.json, README.md, instructions.md）。
    返回 (description, tools_list, triggers_list)
    """
    if not base_dir or not os.path.isdir(base_dir):
        return "", [], []

    # 1. 优先探测同级或子级 SKILL.md
    skill_candidates = [
        os.path.join(base_dir, "SKILL.md"),
        os.path.join(base_dir, "skills", "SKILL.md"),
        os.path.join(base_dir, "skill", "SKILL.md"),
    ]
    if target_name:
        skill_candidates.extend([
            os.path.join(base_dir, "skills", target_name, "SKILL.md"),
            os.path.join(base_dir, "skills", f"connector-{target_name}", "SKILL.md"),
            os.path.join(base_dir, f"connector-{target_name}", "SKILL.md"),
            os.path.join(base_dir, target_name, "SKILL.md"),
        ])

    for sc in skill_candidates:
        if os.path.isfile(sc):
            try:
                name, desc = skillmd.parse_skill_md(sc)
                if desc:
                    trigs = skillmd.extract_triggers(desc)
                    return desc, [], trigs
            except Exception:
                pass

    # 2. 探测 manifest.json / package.json / connector.json / agent.json
    for manifest_name in ("connector.json", "manifest.json", "agent.json", "package.json"):
        mp = os.path.join(base_dir, manifest_name)
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8", errors="replace") as f:
                    mdata = json.load(f)
                if isinstance(mdata, dict):
                    desc = (mdata.get("description_zh") or mdata.get("description") or 
                            mdata.get("summary") or mdata.get("displayName") or mdata.get("prompt") or "")
                    trigs = mdata.get("triggers") or mdata.get("keywords") or mdata.get("tags") or []
                    if desc:
                        return desc, [], trigs
            except Exception:
                pass

    # 3. 探测 instructions.md / README.md（提取首段或标题作为自省摘要）
    for doc_name in ("instructions.md", "README.md", "readme.md"):
        doc_p = os.path.join(base_dir, doc_name)
        if os.path.isfile(doc_p):
            try:
                with open(doc_p, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for l in lines:
                    l_s = l.strip().lstrip("#").strip()
                    if l_s and not l_s.startswith("[") and not l_s.startswith("!") and len(l_s) > 6:
                        return l_s, [], []
            except Exception:
                pass

    return "", [], []


def _introspect_entity_metadata(sname: str, spec, cfg_path: str, is_connector: bool, is_agent: bool):
    """4级纯动态自省提取实体（连接器、MCP、数字员工）的真实元数据（0硬编码字典）。
    返回 (clean_name, description, explicit_triggers)
    """
    clean_name = sname
    if clean_name.startswith("connector:"):
        clean_name = clean_name[len("connector:"):].strip()
    elif clean_name.startswith("agent:"):
        clean_name = clean_name[len("agent:"):].strip()

    raw_desc = ""
    raw_triggers = []
    cmd_or_url = ""
    role = ""

    # 第 1 级：原生 JSON 字段自省
    if isinstance(spec, dict):
        raw_desc = (spec.get("description_zh") or spec.get("description") or
                    spec.get("summary") or spec.get("title") or spec.get("prompt") or
                    spec.get("instructions") or "")
        role = spec.get("role") or spec.get("role_name") or ""
        cmd_or_url = spec.get("command") or spec.get("url") or spec.get("type") or ""
        if isinstance(spec.get("triggers"), list):
            raw_triggers.extend(spec["triggers"])
        elif isinstance(spec.get("keywords"), list):
            raw_triggers.extend(spec["keywords"])
    elif isinstance(spec, str):
        cmd_or_url = spec

    # 第 2 级：穿透读取伴生文档自省（同级/子级/包目录）
    if not raw_desc and cfg_path:
        base_dir = os.path.dirname(os.path.abspath(cfg_path))
        doc_desc, _, doc_trigs = _find_adjacent_artifact_info(base_dir, clean_name)
        if doc_desc:
            raw_desc = doc_desc
            raw_triggers.extend(doc_trigs)
        else:
            parent_dir = os.path.dirname(base_dir)
            if parent_dir and parent_dir != base_dir:
                p_desc, _, p_trigs = _find_adjacent_artifact_info(parent_dir, clean_name)
                if p_desc:
                    raw_desc = p_desc
                    raw_triggers.extend(p_trigs)

    # 第 3 级：协议与指令特征动态合成（纯动态，无死字典）
    if is_agent:
        prefix = "【数字员工】"
        base_label = f"{prefix}{clean_name}"
        if role:
            base_label += f"（角色: {role}）"
        if raw_desc:
            desc = f"{base_label} - {raw_desc}" if not raw_desc.startswith("【") else raw_desc
        else:
            desc = base_label
    elif is_connector:
        prefix = "【连接器】"
        if raw_desc:
            desc = f"{prefix}{raw_desc}" if not raw_desc.startswith("【") else raw_desc
            if cmd_or_url and cmd_or_url not in desc:
                desc += f" ({cmd_or_url})"
        else:
            desc = f"{prefix}{clean_name} 连接器服务"
            if cmd_or_url:
                desc += f" ({cmd_or_url})"
    else:
        prefix = "MCP server "
        if raw_desc:
            desc = f"{prefix}'{clean_name}': {raw_desc}"
            if cmd_or_url and cmd_or_url not in desc:
                desc += f" ({cmd_or_url})"
        else:
            desc = f"{prefix}'{clean_name}'"
            if cmd_or_url:
                desc += f" ({cmd_or_url})"

    return clean_name, desc, raw_triggers


def _synthesize_triggers(name: str, desc: str, raw_triggers: list = None):
    """纯动态触发词合成：显式声明 + 标识符语义切分 + 描述关键词自然抽取（0硬编码字典）。"""
    triggers = []
    seen = set()

    def add_t(t):
        if not t:
            return
        t_clean = str(t).strip()
        t_low = t_clean.lower()
        if t_low not in seen and len(t_clean) > 0:
            seen.add(t_low)
            triggers.append(t_clean)

    # 1. 显式声明优先
    for t in (raw_triggers or []):
        add_t(t)

    # 2. 标识符智能切分（tokenize）
    for t in _tokenize_identifier(name):
        add_t(t)

    # 3. 描述文本关键词动态挖掘
    if desc:
        nlp_trigs = skillmd.extract_triggers(desc)
        for t in nlp_trigs:
            add_t(t)

    return triggers[:8]


def scan_mcp():
    out, seen = [], set()
    for cfg in candidate_mcp_configs():
        cfg_p = expand(cfg)
        if not os.path.isfile(cfg_p):
            continue
        try:
            with open(cfg_p, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception:
            continue

        servers = {}
        if isinstance(data, dict):
            # 常规 MCP 服务
            if "mcpServers" in data and isinstance(data["mcpServers"], dict):
                for k, v in data["mcpServers"].items():
                    servers[k] = (v, False, False)
            if "projects" in data and isinstance(data["projects"], dict):
                for p in data["projects"].values():
                    if isinstance(p, dict) and "mcpServers" in p and isinstance(p["mcpServers"], dict):
                        for k, v in p["mcpServers"].items():
                            servers[k] = (v, False, False)
            # 连接器配置
            if "connectors" in data:
                conns = data["connectors"]
                if isinstance(conns, dict):
                    for k, v in conns.items():
                        servers[k] = (v, True, False)
                elif isinstance(conns, list):
                    for it in conns:
                        if isinstance(it, dict) and it.get("name"):
                            servers[it["name"]] = (it, True, False)
            # 数字员工 / 智能体配置 (Agent / Subagents / Digital Workers)
            for agent_key in ("agents", "subagents", "workers", "digital_workers"):
                if agent_key in data:
                    ag_data = data[agent_key]
                    if isinstance(ag_data, dict):
                        for k, v in ag_data.items():
                            servers[k] = (v, False, True)
                    elif isinstance(ag_data, list):
                        for it in ag_data:
                            if isinstance(it, dict) and (it.get("name") or it.get("role")):
                                ag_name = it.get("name") or it.get("role")
                                servers[ag_name] = (it, False, True)

        for sname, (spec, force_conn, force_agent) in servers.items():
            if sname in ("mcpServers", "projects", "connectors", "agents", "subagents", "workers", "digital_workers", "version", "schema", "$schema"):
                continue
            if not isinstance(spec, dict) and not isinstance(spec, str):
                continue

            norm_key = sname.lower()
            if norm_key.startswith("connector:"):
                norm_key = norm_key[len("connector:"):].strip()
            elif norm_key.startswith("agent:"):
                norm_key = norm_key[len("agent:"):].strip()

            if norm_key in seen:
                continue
            seen.add(norm_key)

            is_agent = force_agent or sname.startswith("agent:") or "agent" in sname.lower() or "worker" in sname.lower() or "agent" in cfg_p.lower()
            is_conn = force_conn or (
                sname.startswith("connector:")
                or "connector" in sname.lower()
                or "connector" in cfg_p.lower()
                or (isinstance(spec, dict) and (spec.get("_workbuddyManagedAuth") or spec.get("type") in ("streamable-http", "streamableHttp")))
            )
            if is_agent:
                entity_type = "agent"
            elif is_conn:
                entity_type = "connector"
            else:
                entity_type = "mcp"

            clean_name, desc, raw_trigs = _introspect_entity_metadata(sname, spec, cfg_p, is_conn, is_agent)
            trigs = _synthesize_triggers(clean_name, desc, raw_trigs)

            out.append({
                "name": sname,
                "clean_name": clean_name,
                "description": desc,
                "triggers": trigs,
                "type": entity_type,
                "is_connector": is_conn,
                "is_agent": is_agent,
                "path": cfg_p,
                "has_references": False,
                "source": cfg_p,
                "mtime": 0,
            })

    # 目录型 MCP 服务扫描（例如 Antigravity/Gemini 本地工具集）
    for d in candidate_mcp_dirs():
        dp = expand(d)
        if not os.path.isdir(dp):
            continue
        try:
            sub_entries = os.listdir(dp)
        except Exception:
            continue
        for sname in sub_entries:
            sp = os.path.join(dp, sname)
            if not os.path.isdir(sp):
                continue
            norm_key = sname.lower()
            if norm_key in seen:
                continue
            seen.add(norm_key)

            doc_desc, tools, doc_trigs = _find_adjacent_artifact_info(sp, sname)
            if doc_desc:
                desc = f"MCP server '{sname}' ({doc_desc})"
            else:
                desc = f"MCP server '{sname}' (本地目录型工具服务)"

            trigs = _synthesize_triggers(sname, desc, doc_trigs)

            out.append({
                "name": sname,
                "clean_name": sname,
                "description": desc,
                "triggers": trigs,
                "type": "mcp",
                "is_connector": False,
                "is_agent": False,
                "path": sp,
                "has_references": bool(tools),
                "source": sp,
                "mtime": 0,
            })

    return out



def scan_all(broad="auto"):
    entries = dedupe_entries(scan_skills(broad) + scan_mcp())
    entries.sort(key=lambda e: (e["type"], e["name"].lower()))
    return entries


def probe_report():
    """扫描根探针：逐个候选根打印 存在? / 找到几个技能。用于解释"为什么是 0 项"。"""
    lines = ["# 扫描根探针（找不到技能时看这里）", ""]
    lines.append(f"技能自身目录: {ROOT}")
    lines.append(f"  → 自身相对候选: {self_relative_roots() or '（无）'}")
    lines.append(f"cwd: {os.getcwd()}   HOME: {expand('~')}")
    if only_dirs():
        lines.append("模式: SKILL_GATEWAY_ONLY_DIRS=1（只用显式给的根，不掺自身相对/内置默认）")
    lines.append("")
    lines.append("## 约定/配置的扫描根")
    lines.append(f"{'来源':<8} {'存在':<5} {'技能数':<6} 路径")
    total = 0
    for r, tag in candidate_roots():
        p = expand(r)
        ok = os.path.isdir(p)
        n = len(list(iter_skill_md(p, 3 if tag == "self" else 5))) if ok else 0
        total += n
        lines.append(f"{tag:<8} {'是' if ok else '否':<5} {n:<6} {p}")
    lines.append("")
    lines.append("## 广域发现（skills / *-skills 目录）")
    bd = broad_discover()
    if not bd:
        lines.append("  （未发现）")
    for d in bd:
        n = len(list(iter_skill_md(d)))
        lines.append(f"  {n:<6} {d}")
    lines.append("")
    lines.append("## MCP 与连接器配置")
    mcp_items = scan_mcp()
    conn_count = sum(1 for m in mcp_items if m.get("is_connector"))
    agent_count = sum(1 for m in mcp_items if m.get("is_agent"))
    mcp_count = len(mcp_items) - conn_count - agent_count
    lines.append(f"  动态嗅探配置源: {len(candidate_mcp_configs())} 个，目录源: {len(candidate_mcp_dirs())} 个")
    lines.append(f"  共解析出: {len(mcp_items)} 项（常规 MCP: {mcp_count}，连接器: {conn_count}，数字员工: {agent_count}）")
    for m in mcp_items[:20]:
        tag = "[数字员工]" if m.get("is_agent") else ("[连接器]" if m.get("is_connector") else "[MCP]")
        lines.append(f"  - {tag} {m['name']} -> {m['description']}")
    if len(mcp_items) > 20:
        lines.append(f"    ... 以及其余 {len(mcp_items) - 20} 项")
    lines.append("")
    if total == 0 and not bd:
        lines.append("结论：这台机器上没扫到任何 SKILL.md。三种处理方式：")
        lines.append("  1) 把技能仓库目录写进 skill_gateway.config.json 的 skill_roots；")
        lines.append("  2) 设 SKILL_GATEWAY_SKILL_DIRS 环境变量指向它；")
        lines.append("  3) 平台不把技能落盘时，用 import 把平台的技能清单导进索引。")
    elif total == 0:
        lines.append(f"结论：约定根 0 项，但有 {len(bd)} 个广域候选。本次已自动走广域兜底；"
                     "想固定下来就把对应目录写进 skill_gateway.config.json 的 skill_roots。")
    return "\n".join(lines)
