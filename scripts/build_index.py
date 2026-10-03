#!/usr/bin/env python3
# build_index.py - skill-gateway CLI 入口（子命令分发）
# 子命令: index / update / add / roots / import / list / search / explain / detail / related
#         / stats / audit / bundle / doctor / export / route / catalog / help
# created 2026-09-09, updated 2026-09-16 qjl: 拆分为多模块(scripts/) + 新增 doctor / export
# updated 2026-09-16 qjl: 新增 roots(扫描根探针) / import(导入外部技能清单)
import argparse
import json
import time
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle as bundle_mod  # noqa: E402
import content_index as ci  # noqa: E402
import doctor as doctor_mod  # noqa: E402
import export as export_mod  # noqa: E402
import hit_ledger as ledger_mod  # noqa: E402
import importer as importer_mod  # noqa: E402
import index_store  # noqa: E402
import paths  # noqa: E402
import pipeline as pipeline_mod  # noqa: E402
import platform_bridge as pb_mod  # noqa: E402
import proactive  # noqa: E402
import reports  # noqa: E402
import retrieval  # noqa: E402
import route as route_mod  # noqa: E402
import scanner as scanner_mod  # noqa: E402
from paths import out_path  # noqa: E402

# 退出码约定（给调用方/Agent 区分三种结局）：
#   0 = 成功；2 = 预期内的"未命中/未找到"（可换词重试）；1 = 异常错误（看 stderr）
RC_OK, RC_MISS, RC_ERROR = 0, 2, 1


def _miss(msg):
    """预期内的未命中：打印说明并以 rc=2 退出（区别于成功与崩溃）。"""
    print(msg)
    sys.exit(RC_MISS)


def cmd_index(a):
    broad = True if a.broad else "auto"
    full = bool(a.full)
    mode = "full" if full else "list"
    es, healed = index_store.heal_if_self_only(
        index_store.build_index(broad), mode=mode
    )
    if healed and healed.get("partial") and full:
        # --full 被中止（清单缺完整 SKILL.md）：未写索引，rc=2，绝不拿元数据冒充全量
        print("[index] ❌ 全量构建中止：平台清单里没有完整 SKILL.md 正文（上面已落镜像占位）。")
        pb_mod._print_full_recipe()
        print("[index]   若只需检索/路由，可跑 `index`（默认建元数据索引，仅定位用）。")
        sys.exit(RC_MISS)
    if healed and healed.get("partial"):
        print(
            f"[index] 已索引 {len(es)} 项（元数据索引·仅定位用：查有哪些/用哪个够用；"
            "detail/audit/正文检索需全文 → index --full）"
        )
    else:
        print(
            f"[index] 已索引 {len(es)} 项（全量重建{' + 桥接自愈' if healed else ''}）"
        )
    print(f"        JSON -> {out_path('skill-index.json')}")
    print(f"        LLMS -> {out_path('skill-index.llms.txt')}")
    print(f"        HTML -> {out_path('skill-dashboard.html')}")
    if not es:
        print()
        print(scanner_mod.probe_report())


def cmd_agent_index(a):
    """建立 Agent 级索引：把清单合并进权威可用清单（inputs/available_skills.json）
    并立即重建索引、dashboard 与内容索引。
    不带清单参数 = 自助模式：直接扫描当前环境已挂载技能（约定根+广域发现）并入
    共享清单——跑完即用，不反问、不解释。平台有导出文件时再走文件导入。"""
    t0 = time.time()
    # 身份只能显式声明：--agent > 环境变量。agent 底层知道自己运行在哪个平台，
    # 直接声明即可——不搞路径推测、不设 default 兜底（无主身份会让共享清单的
    # 归属标签跨环境撞车）。
    agent_name = (a.agent or "").strip() or \
        (os.environ.get("SKILL_GATEWAY_AGENT") or "").strip()
    if not agent_name:
        _miss(
            "[agent-index] 请声明你的身份：--agent <平台名或智能体名>（你作为 agent "
            "底层知道自己运行在哪个平台，直接声明即可；也可设环境变量 "
            "SKILL_GATEWAY_AGENT）。不使用路径推测、不设 default 兜底——"
            "无主身份会让共享清单的归属标签跨环境撞车。"
        )
    list_path = os.path.join(paths.ROOT, "inputs", "available_skills.json")
    os.makedirs(os.path.dirname(list_path), exist_ok=True)

    # --prune-agent：清除某环境的绑定污染（agents 去名，无人绑定即移出清单），并重建
    if getattr(a, "prune_agent", ""):
        victims = a.prune_agent.strip()
        doc = scanner_mod.available_list_doc() or {"skills": []}
        kept, removed = [], 0
        for it in doc.get("skills") or []:
            ags = [x for x in (it.get("agents") or []) if x != victims]
            if it.get("agents") and victims in it.get("agents") and not ags:
                removed += 1
                continue  # 绑定清空 = 无人挂载，移出权威清单（磁盘扫描照旧兜底 off-list）
            if ags != it.get("agents"):
                it["agents"] = ags
            kept.append(it)
        index_store.atomic_write(list_path, json.dumps(
            {**doc, "skills": kept}, ensure_ascii=False, indent=2))
        entries, _st = index_store.update_index()
        print(f"[agent-index] 已清除 agents=[{victims}] 的绑定：移除 {removed} 项，保留 {len(kept)} 项")
        print(f"[agent-index] 索引已重建（当前 {len(entries)} 项）。共享清单: {list_path}")
        return

    if a.source:
        raw = importer_mod.parse_source_raw(a.source)
        if not raw:
            _miss(
                "[agent-index] 没解析出技能项。支持 JSON（list 或 {skills:[...]}，如 skill_follow 的返回）/ 每行 `name: 描述`"
            )
    else:
        # 自助模式按"环境声明/父目录"圈定挂载集，绝不回退全机器扫描
        # （全机器灌入会让共享清单被 agents=[default] 的整盘技能污染）
        # 技能根解析（声明一次、永久记忆）：
        #   1) 记忆文件里该智能体已声明过 → 直接用，零参数全自动
        #   2) --skill-dirs 显式声明 → 用它并写入记忆（下次自动）
        #   3) 环境声明文件（仅当本副本住在声明库内才生效）/ 父目录兜底
        #   4) 都没有 → 询问声明，绝不全机器灌入
        remembered = paths._load_env_roots_memory().get(agent_name) or []
        declared = getattr(a, "skill_dirs", "") or ""
        if remembered and not declared:
            env_dirs = [d for d in remembered if os.path.isdir(d)]
            print(f"[agent-index] 按记忆中的 [{agent_name}] 技能根圈定: {env_dirs}")
        elif declared:
            env_dirs = [d for d in (x.strip() for x in re.split(r"[;|]", declared) + declared.split(os.pathsep)) if d and os.path.isdir(d)]
            if env_dirs:
                paths._save_env_roots_memory(agent_name, env_dirs)
                print(f"[agent-index] 已声明技能根并写入记忆（下次零参数自动使用）: {env_dirs}")
        else:
            env_dirs = [d for d in paths.discover_env_skill_roots()
                        if os.path.normcase(paths.ROOT).startswith(os.path.normcase(d) + os.sep)
                        or os.path.normcase(paths.ROOT) == os.path.normcase(d)]
        if not env_dirs:
            # 声明文件是机器级的，但只对"本副本确实住在该声明库里"时生效
            # （其他位置的副本不被别人的声明误圈定）
            parent = os.path.dirname(paths.ROOT)
            skill_md_count = sum(
                1 for _dp, _dn, fn in os.walk(parent)
                if any(f.lower() == "skill.md" for f in fn)) if os.path.isdir(parent) else 0
            if skill_md_count >= 2:
                env_dirs = [parent]
        if not env_dirs:
            _miss(
                f"[agent-index] 首次遇到智能体 [{agent_name}]，请声明你的技能挂载根（只需一次）："
                f"agent-index --agent {agent_name} --skill-dirs \"<根1;根2>\""
                "——你底层知道自己平台把技能挂在哪里；声明后写入 env_skill_roots.json 记忆，"
                "之后零参数全自动。也可设环境变量 SKILL_GATEWAY_SKILL_DIRS。")
        prev_dirs = os.environ.get("SKILL_GATEWAY_SKILL_DIRS")
        prev_only = os.environ.get("SKILL_GATEWAY_ONLY_DIRS")
        os.environ["SKILL_GATEWAY_SKILL_DIRS"] = os.pathsep.join(env_dirs)
        os.environ["SKILL_GATEWAY_ONLY_DIRS"] = "1"
        try:
            raw = scanner_mod.scan_skills(ignore_list=True)
        finally:
            if prev_dirs is None:
                os.environ.pop("SKILL_GATEWAY_SKILL_DIRS", None)
            else:
                os.environ["SKILL_GATEWAY_SKILL_DIRS"] = prev_dirs
            if prev_only is None:
                os.environ.pop("SKILL_GATEWAY_ONLY_DIRS", None)
            else:
                os.environ["SKILL_GATEWAY_ONLY_DIRS"] = prev_only
        if not raw:
            _miss(
                f"[agent-index] 环境技能根 {env_dirs} 中未发现技能。"
                "请检查目录内容，或提供清单：agent-index <文件|-> [--agent 智能体名]。"
            )
        print(f"[agent-index] 自助模式：按环境挂载根 {env_dirs} 圈定，发现 {len(raw)} 项技能")

    # 翻译回传（动态翻译闭环）：调用方 agent 生成的中文描述，网关写入登记表
    _zh = {}
    if getattr(a, "desc_zh", ""):
        try:
            _zh.update(json.loads(a.desc_zh))
        except Exception as ex:
            _miss(f"[agent-index] --desc-zh 不是合法 JSON: {ex}")
    if getattr(a, "desc_zh_file", ""):
        try:
            with open(a.desc_zh_file, encoding="utf-8") as f:
                _zh.update(json.load(f))
        except Exception as ex:
            _miss(f"[agent-index] --desc-zh-file 读取失败: {ex}")

    # 合并语义：agents 并集、未导入条目原样保留（共享清单属于所有智能体环境）
    doc = importer_mod.to_available_list(raw, agent_name, scanner_mod.available_list_doc())
    if _zh:
        _applied = 0
        for _name, _zhv in _zh.items():
            for _it in doc.get("skills", []):
                if _it.get("name", "").lower() == str(_name).strip().lower():
                    _it["description_zh"] = str(_zhv).strip()
                    _applied += 1
                    break
        print(f"[agent-index] 已写入 {_applied} 条中文描述到登记表")
    # 动态翻译闭环：缺中文描述 → 调翻译 API 现场回填（只写登记表，不碰技能库源文件）
    _need_zh = [it for it in doc.get("skills", [])
                if agent_name in (it.get("agents") or []) and not it.get("description_zh")]
    if _need_zh and not getattr(a, "no_translate", False):
        try:
            import translate as _tr
            _ok, _fail = _tr.translate_missing(_need_zh, agent_name)
            if _ok:
                print(f"[agent-index] 翻译 API 现场回填中文描述 {_ok} 条"
                      + (f"（{_fail} 条失败，下次重试）" if _fail else ""))
            elif _fail:
                print(f"[agent-index] 翻译 API 不可达/失败 {_fail} 条（登记表保持原文，下次重试）")
        except Exception as _ex:
            print(f"[agent-index] 翻译通道异常（不影响索引构建）: {_ex}", file=sys.stderr)
    _missing_zh = [it["name"] for it in _need_zh if not it.get("description_zh")]
    if _missing_zh:
        print(f"[agent-index] {len(_missing_zh)} 条缺中文描述（如: {'、'.join(_missing_zh[:5])}"
              f"{'…' if len(_missing_zh) > 5 else ''}）。将调用翻译 API 现场生成并写入登记表"
              f"（不触碰技能库源文件）；也可用 --desc-zh-file 手工回传。")
    index_store.atomic_write(list_path, json.dumps(doc, ensure_ascii=False, indent=2))
    t_list = time.time()
    entries, st = index_store.update_index()
    t_idx = time.time()
    try:
        bundle_mod.write_bundle(entries, index_store.read_edges() or [])
    except Exception as _ex:
        print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
    if getattr(a, "content", False):
        try:
            ci.update_content_index("auto")
            t_ci = time.time()
            ci_note = f"内容索引重建 {t_ci - t_idx:.1f}s"
        except Exception:
            ci_note = "内容索引重建失败（不影响元数据索引）"
    else:
        ci_note = "内容索引未重建（--content 可强制；检索未命中时自愈会自动增量更新）"
    skills = [e for e in entries if e["type"] == "skill"]
    ready = sum(1 for e in skills if e.get("visibility", "ready") == "ready")
    blocked = sum(1 for e in skills if e.get("visibility") == "blocked")
    off = sum(1 for e in skills if e.get("visibility") == "off-list")
    bound = sum(1 for e in skills if e.get("agent_bound") is True)
    cur = scanner_mod.available_list_agent()
    print(f"[agent-index] 共享清单已更新: {list_path}（{t_list - t0:.1f}s）")
    print(f"  当前 Agent: {cur or '（未声明，Agent 维度不生效）'}")
    print(f"  索引重建完成（新增 {len(st.get('added', []))} / 更新 {len(st.get('updated', []))} / 移除 {len(st.get('removed', []))}，{t_idx - t_list:.1f}s）")
    print(f"  平台可用 {ready} ｜ blocked {blocked} ｜ 平台未绑定(off-list) {off}")
    print(f"  当前 Agent 绑定 {bound} 项")
    print(f"  {ci_note}（总耗时 {time.time() - t0:.1f}s）")
    print("> 口径：chat/search/pipeline 默认=平台可用；--agent 收窄当前 Agent；--all 全量审计")
    print("> 验证建议：list --agent（核对绑定集，不产生检索脚注）；如用 search 验证，答复中须说明脚注来源")
    print(f"  dashboard -> {out_path('skill-dashboard.html')}")


def cmd_ledger(a):
    """命中率账本报表：能力缺口（零命中 query）、技能命中/采纳榜、僵尸技能。"""
    events = ledger_mod.load_events(limit=a.limit or None)
    names = {e["name"] for e in index_store.load_index()
             if e["type"] == "skill" and not e.get("excluded")}
    print(ledger_mod.ledger_report(events, skill_names=names))


# pack 交付包口径：测试基建/开发文档/运行产物/缓存不上平台
PACK_EXCLUDE_DIRS = {"tests", "docs", "output", "__pycache__", ".pytest_cache",
                     ".omx", ".zcode", ".git", ".github", "node_modules",
                     "platform_mirror"}
PACK_EXCLUDE_FILES = {"conftest.py", "available_skills.json", "platform_skills.json",
                      "agent_list.json", "skill-gateway.zip"}
PACK_TOP_FILES = ("SKILL.md", "README.md", "使用说明.md", "skill_gateway.config.json",
                  "skill_profile.json", ".skillignore", ".skillexclude", ".gitignore")
PACK_DIRS = ("scripts", "references", "templates", "inputs")


def cmd_pack(a):
    """生成交付包 skill-gateway.zip：先 clean 清运行时产物，再按口径排除
    测试基建/开发文档/缓存/本机清单，输出包内清单与大小供上传前核对。"""
    import zipfile

    print("[pack] 清理运行时产物 ...")
    cmd_clean(a)
    zip_path = os.path.join(paths.ROOT, "skill-gateway.zip")
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in PACK_TOP_FILES:
            fp = os.path.join(paths.ROOT, rel)
            if os.path.isfile(fp):
                z.write(fp, rel)
                count += 1
        for dname in PACK_DIRS:
            base = os.path.join(paths.ROOT, dname)
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in PACK_EXCLUDE_DIRS)
                for fn in sorted(filenames):
                    if fn in PACK_EXCLUDE_FILES or fn.endswith(".pyc"):
                        continue
                    fp = os.path.join(dirpath, fn)
                    z.write(fp, os.path.relpath(fp, paths.ROOT).replace(os.sep, "/"))
                    count += 1
    size = os.path.getsize(zip_path)
    print(f"[pack] 交付包: {zip_path}（{count} 个文件，{size:,} 字节）")
    print("[pack] 包内清单:")
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            print(f"  {info.file_size:8,}  {info.filename}")
    print("> 上传平台后：技能页「重新扫描」→ 沙箱里 agent-index / index 重建索引")


def cmd_roots(a):
    _avail = scanner_mod.available_list_file()
    if _avail:
        n = len(scanner_mod.available_list_entries() or [])
        print(f"[模式] 权威可用清单已激活: {_avail}（{n} 项）——技能索引=清单本身，磁盘扫描让位。\n")
    print(scanner_mod.probe_report())


def cmd_import(a):
    items = importer_mod.parse_source(a.source)
    if not items:
        _miss(
            "[import] 没解析出任何技能项。支持：JSON（list 或 {skills:[...]}）/ 每行 `name: 描述` / `name | 描述`"
        )
    entries = importer_mod.to_entries(items)
    total, added, updated = index_store.merge_import(entries)
    print(f"[import] 解析 {len(items)} 项 -> 索引共 {total} 项")
    print(f"  新增 {len(added)}: {'、'.join(added[:20]) or '无'}")
    print(f"  更新 {len(updated)}: {'、'.join(updated[:20]) or '无'}")


def cmd_update(a):
    broad = True if a.broad else "auto"
    es, st = index_store.update_index(broad)
    full = bool(a.full)
    es2, healed = index_store.heal_if_self_only(es, mode="full" if full else "list")
    if healed and healed.get("partial") and full:
        # --full 被中止（清单缺完整 SKILL.md）：未写索引，rc=2
        print("[update] ❌ 全量构建中止：平台清单里没有完整 SKILL.md 正文。")
        pb_mod._print_full_recipe()
        print("[update]   若只需检索/路由，可跑 `update`（默认建元数据索引，仅定位用）。")
        sys.exit(RC_MISS)
    if healed:
        es, st = es2, {
            "first": True,
            "added": [e["name"] for e in es2],
            "updated": [],
            "removed": [],
            "kept": 0,
        }
        print(
            "[update] 只有本技能自己 -> 已走桥接自愈"
            + ("（元数据索引·仅定位用；要全量 → update --full）" if healed.get("partial") else "（全量）")
        )
    if st.get("first"):
        print(f"[update] 首次：已建立索引 {len(es)} 项（等价全量 index）")
        print(f"        HTML -> {out_path('skill-dashboard.html')}")
        return
    print(f"[update] 增量完成：共 {len(es)} 项")
    print(f"  新增 {len(st['added'])}: {'、'.join(st['added'][:20]) or '无'}")
    print(f"  更新 {len(st['updated'])}: {'、'.join(st['updated'][:20]) or '无'}")
    print(f"  移除 {len(st['removed'])}: {'、'.join(st['removed'][:20]) or '无'}")
    print(f"  未变 {st['kept']}（直接复用，未读文件）")
    print(f"  HTML -> {out_path('skill-dashboard.html')}")


def cmd_add(a):
    name, msg = index_store.add_skill(a.target)
    print(f"[add] {msg}" if name is None else f"[add] {name}: {msg}")


def cmd_list(a):
    es = index_store.load_index()
    if a.excluded:
        excluded_items = [e for e in es if e.get("excluded")]
        print(f"# 已排除技能列表（共 {len(excluded_items)} 项）\n")
        if not excluded_items:
            print("当前无任何被排除的技能（规则文件 .skillexclude / config 均为空）。")
            return
        for e in excluded_items:
            reason = e.get("exclude_reason", "用户策略排除")
            print(f"- ({e['type']}) {e['name']}")
            print(f"    排除规则: {reason}")
            print(f"    描述: {e.get('description', '')[:90]}")
            print(f"    path: {e.get('path', '')}")
        return

    total_all = len(es)
    if a.type:
        es = [e for e in es if e["type"] == a.type]
    if getattr(a, "visibility", "all") != "all":
        es = [e for e in es if e.get("visibility", "ready") == a.visibility]
    if getattr(a, "agent_only", False):
        es = [e for e in es if e.get("agent_bound") is not False]
    if a.with_ref:
        es = [e for e in es if e["has_references"]]
    total = len(es)
    shown = es[: a.limit] if a.limit else es
    head = f"# 共 {total} 项"
    if a.limit and a.limit < total:
        head += f"（显示前 {len(shown)}）"
    if total != total_all:
        head += f"｜总索引 {total_all} 项"
    print(head + "\n")
    for e in shown:
        ref = " [ref]" if e["has_references"] else ""
        ex_tag = " [已排除]" if e.get("excluded") else ""
        cp_tag = "".join(f" [{e[k]}]" for k in ("category", "platform") if e.get(k))
        print(f"- ({e['type']}) {e['name']}{ref}{ex_tag}{cp_tag}")
        print(f"    描述: {e['description']}")
        if e.get("triggers"):
            print(f"    触发: {'、'.join(e['triggers'])}")
        hint = (
            "含参考资料，按需 detail 看全文"
            if e["has_references"]
            else "无大体积参考，可直接用"
        )
        print(
            f"    使用建议(笼统): 用技能名「{e['name']}」触发，或让数字员工检索它；{hint}"
        )
        print(f"    path: {e['path']}")


def cmd_search(a):
    cli_excludes = a.exclude
    entries = index_store.load_index()
    hits = retrieval.search(entries, a.query, a.top, cli_excludes=cli_excludes,
                            include_off_list=getattr(a, "all_items", False),
                            agent_only=getattr(a, "agent_only", False))
    if not hits:
        # 触发按需靶向自愈重试
        refreshed_entries, edges, st, has_changes = index_store.auto_update_on_miss(
            query=a.query, targeted=True
        )
        if has_changes:
            add_n = len(st.get("added", []))
            upd_n = len(st.get("updated", []))
            rem_n = len(st.get("removed", []))
            print(
                f"[skill-gateway 动态自愈] 首次未命中，技能库有变更（新增 {add_n} / 更新 {upd_n} / 移除 {rem_n}），已增量更新并刷新图谱，重新检索。\n"
            )
            hits = retrieval.search(
                refreshed_entries, a.query, a.top, cli_excludes=cli_excludes,
                include_off_list=getattr(a, "all_items", False),
                agent_only=getattr(a, "agent_only", False),
            )
            entries = refreshed_entries
        else:
            fp = index_store.index_footprint(0)
            extra = f"\n\n{fp} | 实时扫描确认无新技能" if fp else ""
            _miss(
                f"未匹配到与「{a.query}」相关的技能（已实时重扫本地技能库，未发现新安装或更新的技能）。可换关键词，或直接用 list 浏览。{extra}"
            )

    if not hits:
        _miss(
            f"未匹配到与「{a.query}」相关的技能（已完成实时技能库重扫与图谱刷新）。可换关键词，或直接用 list 浏览。"
        )

    directives = paths.get_user_directives()
    if directives:
        print(f"📌 [用户重要指令已生效: 共 {len(directives)} 条全局规范]")
    print(f"# 检索「{a.query}」Top {len(hits)}\n")

    if hits[0][1].get("ambiguous"):
        tg = hits[0][1]["tie_group"]
        print("⚠ **并列候选——打分高度接近，需人工确认用哪个**：")
        for t in tg:
            print(f"  - **{t['name']}**({t['score']}) {t['description'][:70]}")
        print()

    edges = index_store.read_edges() or []
    by_id = retrieval.build_id_map(entries)
    all_related = []

    for hi, (score, e, overlap) in enumerate(hits):
        pinned_tag = " [★常用置顶]" if e.get("is_pinned") else ""
        cp_tag = "".join(f" [{e[k]}]" for k in ("category", "platform") if e.get(k))
        weak_tag = " （单证据词命中，弱关联）" if (hi == 0 and len(overlap) == 1) else ""
        print(
            f"{score:6.1f}  ({e['type']}) {e['name']}{pinned_tag}{cp_tag}{weak_tag}: {e['description'][:100]}"
        )
        if e.get("triggers"):
            print(f"        触发: {'、'.join(e['triggers'])}")
        print(f"        命中: {', '.join(overlap[:10])}")
        sim = e.get("similar_skills")
        if sim:
            print(
                "        近似同名: "
                + "；".join(f"{s['name']}({s['description'][:40]})" for s in sim)
                + " （采纳前先 detail 确认变体）"
            )

        # 计算图谱关联联动（优先静态边，若无则基于领域与生命周期动态推导）
        eid = retrieval.entry_id(e)
        neighbors = retrieval.get_1hop_neighbors(edges, eid, by_id)
        if not neighbors:
            neighbors = retrieval.get_collaborative_neighbors(entries, e, by_id)
        if neighbors:
            neighbors.sort(key=lambda x: -x["weight"])
            nb_str = "、".join(
                f"{nb['name']}({nb.get('edge_type', 'collab')}={nb.get('weight', 0.8):.2f})"
                for nb in neighbors[:5]
            )
            print(f"        关联联动: {nb_str}")
            for nb in neighbors:
                if nb["name"] != e["name"] and nb["name"] not in all_related:
                    all_related.append(nb["name"])

    gap, gap_msg = retrieval.gap_notice(a.query, hits)
    if gap:
        print(gap_msg)

    # 复杂工程与研发任务场景下，主动引导 pipeline 流水线串联
    # （只认多字职责词——单字"做/写/修"会让几乎所有中文 query 都触发营销块）
    task_keywords = (
        "开发",
        "实现",
        "构建",
        "设计",
        "优化",
        "重构",
        "排查",
        "修复",
        "上线",
        "game",
        "web",
        "app",
        "fix",
        "create",
        "build",
        "pipeline",
    )
    if any(k in a.query.lower() for k in task_keywords):
        print("\n[skill-gateway] 复合工程任务建议用 pipeline 生成多技能流水线:")
        print(f'  /skill-gateway pipeline "{a.query}"')

    best_hit = hits[0][1]["name"] if hits else "无"
    rel_names_str = "、".join(all_related[:3])
    if rel_names_str:
        print(f"\n[skill-gateway] 检索: 命中 [{best_hit}] (联动: {rel_names_str})")
    else:
        print(f"\n[skill-gateway] 检索: 命中 [{best_hit}]")
    print("> 留痕须原样附在答复末尾；答复简短直接，不复述检索过程。")

    ledger_mod.record_event("search", a.query, hits,
                            extra={"agent": scanner_mod.available_list_agent()})

    # 主动决策增量更新与自省晋升。检索结果不算"使用"（不传 hits），
    # 只有用户显式 detail / 采纳才计频晋升。
    hook_res = proactive.proactive_post_hook("search", {"query": a.query})
    if hook_res.get("updated") and hook_res.get("msg"):
        print(f"\n[skill-gateway] {hook_res['msg']}")


def _find(es, name):
    m = [e for e in es if e["name"].lower() == name.lower()]
    return m[0] if m else None


def cmd_detail(a):
    es = index_store.load_index()
    e = _find(es, a.name)
    if not e:
        _miss(f"未找到技能 '{a.name}'。用 list / search 查看可用名称。")
    print(f"# {e['name']}  ({e['type']})")
    print(f"path: {e['path']}")
    print(f"has_references: {e['has_references']}\n")
    sk = index_store.skill_file(
        e
    )  # 单一来源：path 为空（import/平台记录）返回 ""，不会退化成相对 cwd 的 SKILL.md
    if sk and os.path.isfile(sk):
        with open(sk, encoding="utf-8", errors="replace") as f:
            print(f.read())
    else:
        if e.get("path"):
            print(f"⚠ 本地文件缺失：{e['path']}/SKILL.md 不存在（技能可能已被移动/删除，建议重建索引）。")
        else:
            print("⚠ 平台云技能：无本地 SKILL.md（清单未提供 path）。"
                  "以下仅为索引描述摘要；全文请在平台技能库查看，或让平台落盘后重建索引。")
        print(e["description"])
    ledger_mod.record_event("adopt", e["name"], [], {"skill": e["name"]})
    # detail = 用户显式取用某技能全文，是可信的"真实使用"信号，计入频次晋升
    hook_res = proactive.proactive_post_hook(
        "detail", {"query": a.name, "skills": [e["name"]]}
    )
    if hook_res.get("updated") and hook_res.get("msg"):
        print(f"\n[skill-gateway] {hook_res['msg']}")
    if hook_res.get("promoted"):
        for p_skill in hook_res["promoted"]:
            print(
                f"[自省进化] 技能「{p_skill}」使用频次达标，已自动晋升至常用置顶画像 (pinned_skills)"
            )


def cmd_explain(a):
    e = _find(index_store.load_index(), a.name)
    if not e:
        _miss(f"未找到技能 '{a.name}'。用 list / search 查看可用名称。")
    print(f"## 使用建议（详细）：{e['name']}  ({e['type']})")
    print(f"- 做什么：{e['description']}")
    if e.get("triggers"):
        print(f"- 触发词：{'、'.join(e['triggers'])}")
    else:
        print("- 触发词：（描述里未显式给出，按描述语义/技能名触发）")
    print("- 何时用：看上面描述里的「何时用」；一般为描述中写的触发场景。")
    print(f"- 触发方式：直接用技能名「{e['name']}」触发，或在数字员工里检索该名。")
    print(f"- 位置：{e['path']}")
    if e["has_references"]:
        print(
            f"- 注意：含 references/scripts/assets，按需用 detail 加载全文，避免一股脑全读拖慢速度。"
        )
    else:
        print("- 体积：无大体积参考文件，可放心直接调用。")


def cmd_related(a):
    hits = retrieval.related(
        index_store.load_index(), a.name, a.top, edges=index_store.read_edges()
    )
    if not hits:
        print(f"没有找到与「{a.name}」明显相关的技能（或技能不存在）。")
        return
    print(f"# 与「{a.name}」相关的技能（Top {len(hits)}）\n")
    for score, etype, e in hits:
        print(
            f"{score:.2f} [{etype}]  ({e['type']}) {e['name']}: {e['description'][:90]}"
        )
    fp = index_store.index_footprint(len(hits))
    if fp:
        print(f"\n{fp}")


def cmd_stats(a):
    print(reports.stats_text(index_store.load_index(), index_store.read_edges()))


def cmd_audit(a):
    print(reports.audit_text(index_store.load_index(), a.top))


def cmd_bundle(a):
    res = bundle_mod.write_bundle(index_store.load_index(), index_store.read_edges())
    print("[bundle] 已生成：")
    for label, (path, size) in res.items():
        extra = f" ({size})" if size else ""
        print(f"  {label:<16}: {path}{extra}")


def cmd_export(a):
    path, size = export_mod.run(
        index_store.load_index(), a.format, index_store.read_edges()
    )
    print(f"[export] {a.format} -> {path} ({size:,} 字符)")


def cmd_route(a):
    cmd, hits = route_mod.route(a.text)
    clean_q = route_mod.clean_query(a.text)
    target = route_mod.extract_target_skill(a.text)
    print(f"话术: {a.text}")
    print(f"建议命令: {cmd}")
    if len(hits) > 1:
        print(f"备选: {' / '.join(hits[1:])}")
    if cmd == "platform_bridge":
        print(
            "执行: python scripts/platform_bridge.py   "
            "（宿主目录不可达：平台清单 -> 镜像 -> indexer）"
        )
    elif cmd in ("pipeline", "chain", "chat", "search", "content-find"):
        print(f'执行: /skill-gateway {cmd} "{clean_q}"')
    elif cmd in ("explain", "detail", "onboard", "diff", "related") and target:
        print(f"执行: /skill-gateway {cmd} {target}")
    else:
        print(f"执行: /skill-gateway {cmd}")


def cmd_catalog(a):
    es = index_store.load_index()
    lines = [
        "# 技能与 MCP 总览",
        "",
        f"> 由 skill-gateway 自动生成，共 {len(es)} 项。",
        "",
    ]
    for t in ("skill", "mcp"):
        items = [e for e in es if e["type"] == t]
        if not items:
            continue
        lines.append(f"## {t.upper()}（{len(items)}）")
        lines.append("")
        for e in items:
            ref = " (含参考资料)" if e["has_references"] else ""
            lines.append(f"- **{e['name']}**{ref}: {e['description']}")
            if e.get("triggers"):
                lines.append(f"  - 触发: {'、'.join(e['triggers'])}")
            lines.append(f"  - 位置: `{e['path']}`")
        lines.append("")
    p = out_path("catalog.md")
    index_store.atomic_write(p, "\n".join(lines))
    print(f"[catalog] 已生成 {p}（{len(es)} 项）")


def cmd_dashboard(a):
    """生成技能图谱可视化面板（静态 HTML，D3.js 力导向图）。
    打开浏览器与同步 Downloads 均为显式 opt-in（生成命令不应有静默副作用）；
    --serve 只绑 127.0.0.1（此前绑 0.0.0.0 会把 output/ 暴露给局域网）。"""
    import shutil
    import webbrowser

    entries = index_store.load_index()
    edges = index_store.read_edges() or []
    html, n_nodes, n_links, edge_types = reports.generate_dashboard_html(entries, edges)
    out_file = out_path("skill-dashboard.html")
    index_store.atomic_write(out_file, html)
    print(f"[dashboard] 已生成: {out_file}")
    print(f"  节点: {n_nodes}  边: {n_links}  边类型: {', '.join(edge_types)}")

    # 1. 显式 --download：同步一份到系统下载目录（便于浏览器直接查看）
    dl_copied = None
    if a.download:
        dl_dir = os.environ.get("SKILL_GATEWAY_DOWNLOAD_DIR") or os.path.expanduser(
            "~/Downloads"
        )
        if os.path.isdir(dl_dir):
            try:
                dl_file = os.path.join(dl_dir, "skill-dashboard.html")
                shutil.copy2(out_file, dl_file)
                dl_copied = dl_file
                print(f"  [下载就绪] 已同步副本至系统下载目录: {dl_file}")
            except Exception as _ex:
                print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    # 2. 显式 --open：唤起默认浏览器
    if a.open_browser:
        try:
            if webbrowser.open(os.path.abspath(out_file)):
                print("  [浏览器] 已在默认浏览器中打开图谱面板")
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    # 3. 交付信息卡
    print("\n--- [交付链接] ---")
    print(f"本地地址: file:///{os.path.abspath(out_file).replace(os.sep, '/')}")
    if dl_copied:
        print(f"下载文件: file:///{os.path.abspath(dl_copied).replace(os.sep, '/')}")
    print("------------------\n")

    # 4. 可选轻量本地 Web 预览服务（仅绑定回环地址，不对外网暴露）
    if a.serve:
        import http.server
        import socketserver

        port = a.port or 8765
        out_dir = os.path.dirname(os.path.abspath(out_file))

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=out_dir, **kwargs)

            def log_message(self, format, *args):
                pass

        url = f"http://127.0.0.1:{port}/skill-dashboard.html"
        print(f"  [Web 服务] 预览服务已启动: {url} (仅本机可访问，按 Ctrl+C 退出)")
        webbrowser.open(url)
        try:
            with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
                httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  [Web 服务] 已停止")


def cmd_diff(a):
    """技能变更影响分析：给定技能名，分析其变更会影响哪些下游技能。"""
    entries = index_store.load_index()
    edges = index_store.read_edges() or []
    print(reports.diff_text(entries, edges, a.name))


def cmd_doctor(a):
    """技能体检 + 可选图谱质量审查与自动修复。"""
    entries = index_store.load_index()
    print(doctor_mod.report_text(entries))

    if a.graph:
        edges = index_store.read_edges() or []
        diag = doctor_mod.diagnose_graph(entries, edges)
        print(doctor_mod.graph_report_text(entries, edges, diag=diag))

        if a.fix:
            fixed_edges, fix_count, repair_log = doctor_mod.repair_graph(
                entries, edges, diag=diag
            )
            print(repair_log)
            if fix_count > 0:
                index_store.write_edges(fixed_edges)


def cmd_onboard(a):
    """技能上手指南：为指定技能生成结构化的入门文档。"""
    entries = index_store.load_index()
    edges = index_store.read_edges() or []
    print(reports.onboard_text(entries, edges, a.name))


def cmd_chat(a):
    """技能图谱问答：输入自然语言问题，返回推荐技能 + 关联技能 + 相关内容块。"""
    query = a.query
    top_n = a.top
    cli_excludes = a.exclude
    entries = index_store.load_index()
    edges = index_store.read_edges() or []

    # 1. 检索候选技能
    hits = retrieval.search(entries, query, top=top_n, cli_excludes=cli_excludes,
                            include_off_list=getattr(a, "all_items", False),
                            agent_only=getattr(a, "agent_only", False))

    if not hits:
        # 从 content-index 尝试反查所属技能
        doc = ci.load_content_index()
        if doc and doc.get("chunks"):
            ci_hits = ci.content_search(doc, query, top=top_n)
            if ci_hits:
                seen_sk = set()
                name_map = {
                    e["name"].lower(): e for e in entries if not e.get("excluded")
                }
                for score, ch, _ in ci_hits:
                    sk_name = ch.get("skill", "").lower()
                    if sk_name in name_map and sk_name not in seen_sk:
                        seen_sk.add(sk_name)
                        hits.append((score, name_map[sk_name], [query]))
                        if len(hits) >= top_n:
                            break

    if not hits:
        # 触发按需靶向自愈重试
        refreshed_entries, refreshed_edges, st, has_changes = (
            index_store.auto_update_on_miss(query=query, targeted=True)
        )
        if has_changes:
            add_n = len(st.get("added", []))
            upd_n = len(st.get("updated", []))
            rem_n = len(st.get("removed", []))
            print(
                f"[skill-gateway 动态自愈] 首次未命中，技能库有变更（新增 {add_n} / 更新 {upd_n} / 移除 {rem_n}），已增量更新并刷新图谱，重新问答。\n"
            )
            entries = refreshed_entries
            edges = refreshed_edges
            hits = retrieval.search(
                entries, query, top=top_n, cli_excludes=cli_excludes,
                include_off_list=getattr(a, "all_items", False),
                agent_only=getattr(a, "agent_only", False),
            )

    if not hits:
        fp = index_store.index_footprint(0)
        extra = f"\n\n{fp} | 实时扫描确认无新技能 | 建议换词检索" if fp else ""
        _miss(
            f"未匹配到与「{query}」相关的技能（已实时重扫本地技能库，未发现新安装或更新的技能）。可换关键词，或直接用 list 浏览。{extra}"
        )

    # 2. 构建 ID→entry 映射与 1-hop 关联查询
    by_id = retrieval.build_id_map(entries)

    # 3. 对每个候选技能，找 1-hop 关联
    print(f"# 技能问答：「{query}」\n")

    # 前置输出用户重要指令
    directives = paths.get_user_directives()
    if directives:
        print("## 📌 用户重要指令 (User Directives - 全局必遵)\n")
        print("> ⚠️ 数字员工与 Agent 执行相关任务时，必须严格遵守以下指令：")
        for d_idx, d_text in enumerate(directives, 1):
            print(f"{d_idx}. **{d_text}**")
        print()

    print(f"## 推荐技能（Top {len(hits)}）\n")

    if hits[0][1].get("ambiguous"):
        tg = hits[0][1]["tie_group"]
        print("⚠ **并列候选——打分高度接近，需人工确认用哪个**：")
        for t in tg:
            print(f"  - **{t['name']}**({t['score']}) {t['description'][:70]}")
        print()

    related_names = set()
    for rank, (score, e, overlap) in enumerate(hits, 1):
        weak_tag = " ⚠单证据词命中（弱关联，可能是能力缺口）" if (rank == 1 and len(overlap) == 1) else ""
        eid = retrieval.entry_id(e)
        neighbors = retrieval.get_1hop_neighbors(edges, eid, by_id)
        if not neighbors:
            neighbors = retrieval.get_collaborative_neighbors(entries, e, by_id)
        for nb in neighbors:
            related_names.add(nb["name"])

        # 格式化输出
        desc_short = e.get("description", "")[:120]
        trig = "、".join((e.get("triggers") or [])[:5])
        pinned_tag = " ★ [常用置顶]" if e.get("is_pinned") else ""
        cp_tag = "".join(f" [{e[k]}]" for k in ("category", "platform") if e.get(k))
        print(f"{rank}. **{e['name']}**{pinned_tag}{cp_tag} [{score:.1f}]{weak_tag} — {desc_short}")
        if trig:
            print(f"   触发: {trig}")
        sim = e.get("similar_skills")
        if sim:
            print(
                "   近似同名: "
                + "；".join(f"**{s['name']}**({s['description'][:40]})" for s in sim)
                + " （采纳前先 detail 确认变体）"
            )
        if neighbors:
            neighbors.sort(key=lambda x: -x["weight"])
            nb_str = "、".join(
                f"{nb['name']}({nb.get('edge_type', 'collab')}={nb.get('weight', 0.8):.2f})"
                for nb in neighbors[:5]
            )
            print(f"   关联: {nb_str}")
        print()

    gap, gap_msg = retrieval.gap_notice(query, hits)
    if gap:
        print(gap_msg)
        print()

    # 4. 内容检索补充
    doc = ci.load_content_index()
    if doc and doc.get("chunks"):
        ci_hits = ci.content_search(doc, query, top=5)
        if ci_hits:
            print("## 相关内容片段\n")
            for score, ch, matched in ci_hits:
                fed_tag = f" [←{ch['fed_from']}]" if ch.get("fed_from") else ""
                print(
                    f"- **{ch['skill']}** / {ch['file']}:{ch['s']}-{ch['e']}{fed_tag}"
                )
                print(f"  摘要: {ch['summary']}")
                print(f"  命中: {', '.join(matched[:8])}")
                print()

    # 5. 关联技能摘要
    if related_names:
        print("## 关联技能摘要\n")
        name_to_entry = {e["name"]: e for e in entries}
        shown = 0
        for rn in sorted(related_names):
            re = name_to_entry.get(rn)
            if re and shown < 8:
                desc_short = re.get("description", "")[:80]
                print(f"- **{rn}**: {desc_short}")
                shown += 1
        if len(related_names) > 8:
            print(f"  ... 还有 {len(related_names) - 8} 个关联技能")

    # 6. 索引足迹脚注
    fp = index_store.index_footprint(len(hits))
    if fp:
        best_hit = hits[0][1]["name"] if hits else "无"
        rel_names_str = "、".join(sorted(list(related_names))[:3])
        extra = f" 命中 [{best_hit}]"
        if rel_names_str:
            extra += f" (联动: {rel_names_str})"
        print(f"\n{fp}{extra}")
        print(
            "\n> 留痕须原样附在答复末尾；答复简短直接，不复述检索过程。"
        )

    ledger_mod.record_event("chat", query, hits,
                            extra={"agent": scanner_mod.available_list_agent()})

    # 主动决策增量更新与自省晋升。问答命中不算"使用"（不传 hits），
    # 只有用户显式 detail / 采纳才计频晋升。
    hook_res = proactive.proactive_post_hook("chat", {"query": query})
    if hook_res.get("updated") and hook_res.get("msg"):
        print(f"\n[skill-gateway] {hook_res['msg']}")


def cmd_content_index(a):
    """全量构建内容索引"""
    doc = ci.build_content_index(True if a.broad else "auto")
    print(
        f"[content-index] 已构建：{doc['chunk_count']} 块 / {doc['skill_count']} 技能 / {doc['total_bytes']:,} 字节"
    )
    print(f"        JSON -> {out_path('skill-content-index.json')}")


def cmd_content_update(a):
    """增量更新内容索引"""
    doc, st = ci.update_content_index(True if a.broad else "auto")
    if st.get("first"):
        print(
            f"[content-update] 首次构建：{doc['chunk_count']} 块 / {doc['skill_count']} 技能"
        )
    else:
        print(
            f"[content-update] 增量完成：{doc['chunk_count']} 块 / {doc['skill_count']} 技能"
        )
        print(
            f"  新增文件 {st['added']} / 更新文件 {st['updated']} / 删除 {st['removed']} / 未变 {st['kept']}"
        )


def cmd_content_find(a):
    """内容检索：查'答案在哪'→ 返回文件+行号+摘要"""
    doc = ci.load_content_index()
    hits = ci.content_search(doc, a.query, a.top)
    if not hits:
        print(f"未匹配到与「{a.query}」相关的内容块。")
        return
    print(f"# 内容检索「{a.query}」Top {len(hits)}\n")
    for score, ch, matched in hits:
        fed_tag = f" [←{ch['fed_from']}]" if ch.get("fed_from") else ""
        print(
            f"  {score:5.1f}  {ch['skill']} / {ch['file']}:{ch['s']}-{ch['e']}{fed_tag}"
        )
        print(f"        摘要: {ch['summary']}")
        print(f"        命中: {', '.join(matched[:10])}")
    if a.full:
        print("\n## 全文片段\n")
        # 按条目名（frontmatter 名）映射目录；投喂 chunk 自带 src_dir，read_chunk_text 优先用之
        skill_dir_map = {
            e["name"]: e.get("path", "")
            for e in index_store.load_index()
            if e.get("type") == "skill"
        }
        seen_files = set()
        for _, ch, _ in hits:
            key = (ch["skill"], ch["file"])
            if key in seen_files:
                continue
            seen_files.add(key)
            sdir = skill_dir_map.get(ch["skill"])
            if sdir:
                text = ci.read_chunk_text(ch, sdir)
                print(f"### {ch['skill']} / {ch['file']}:{ch['s']}-{ch['e']}\n")
                print(text)
                print()
    fp = index_store.index_footprint(len(hits))
    if fp:
        print(f"\n{fp}")


def cmd_pipeline(a):
    """技能图谱串联编排：按任务检索 + 依图谱拓扑自动生成多技能协同流水线（无硬编码模板）。"""
    pipe = pipeline_mod.build_pipeline(a.query, include_off_list=getattr(a, "all_items", False),
                                       agent_only=getattr(a, "agent_only", False),
                                       lifecycle=True if getattr(a, "force_lifecycle", False) else None)
    if a.json:
        clean_pipe = {
            "task": pipe["task"],
            "mode": pipe.get("mode", ""),
            "basis": pipe.get("basis", ""),
            "stages": [
                {
                    "title": s["stage_title"],
                    "skills": [sk["name"] for sk in s["skills"]],
                    "parallel": s.get("parallel", False),
                    "input": s["input"],
                    "output": s["output"],
                    "handoff": s["handoff"],
                }
                for s in pipe["stages"]
            ],
            "skills": sorted(pipe.get("skills", [])),
            "skills_meta": [
                {"name": sk["name"],
                 **{k: sk[k] for k in ("category", "platform", "visibility", "agent_bound", "agents") if sk.get(k)}}
                for s in pipe["stages"] for sk in s["skills"]
            ],
        }
        print(json.dumps(clean_pipe, ensure_ascii=False, indent=2))
    else:
        print(pipeline_mod.format_pipeline_markdown(pipe))

    ledger_mod.record_event("pipeline", a.query,
                            [{"name": n} for n in pipe.get("skills", [])],
                            extra={"agent": scanner_mod.available_list_agent()})

    # 主动决策增量更新与自省晋升。
    # 注意：pipeline 的阶段选型是**编排器内部填坑**，不是用户的真实使用——
    # 不能计入晋升频次（否则跑几次 pipeline 就把凑数的无关技能自动置顶，
    # 置顶提权又反过来污染后续选型）。只把 query 传给钩子做更新决策。
    hook_res = proactive.proactive_post_hook("pipeline", {"query": a.query})
    if hook_res.get("updated") and hook_res.get("msg"):
        # --json 是机器可读契约：提示信息走 stderr，不污染 stdout
        print(f"\n[skill-gateway] {hook_res['msg']}", file=sys.stderr)


def cmd_profile(a):
    """用户重要指令与技能画像管理 (Profile)。持久化于 skill_profile.json 并同步 HOT active memory。"""
    prof_path = paths.PROFILE_FILE
    prof = dict(paths.load_skill_profile())

    if a.init:
        if not prof or not os.path.isfile(prof_path):
            template = {
                "fixed_roots": [],
                "only_fixed_roots": False,
                "exclude_skills": [],
                "pinned_skills": [],
                "pinned_mcps": [],
                "user_directives": [],
                "retrieval_quota": {
                    "top_k": 8,
                    "min_score": 0.35,
                    "favorite_boost": 3.0,
                },
                "proactive_update": {
                    "enabled": True,
                    "interval_seconds": 300,
                    "auto_promote_frequent_skills": True,
                    "frequent_threshold": 3,
                },
            }
            index_store.atomic_write(
                prof_path, json.dumps(template, ensure_ascii=False, indent=2)
            )
            print(f"[profile] 已创建标准模板: {prof_path}")
            prof = template
            paths._PROFILE_CACHE["key"] = None
        else:
            print(f"[profile] 配置文件已存在: {prof_path}")

    if getattr(a, "stage_pref", None):
        prof = paths.load_skill_profile()
        sp = prof.get("stage_preferences") or {}
        for item in a.stage_pref:
            if "=" in item:
                stg, sk = item.split("=", 1)
                sp[stg.strip()] = sk.strip()
        prof["stage_preferences"] = sp
        paths._PROFILE_CACHE["key"] = None
        with open(prof_path, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)
        for stg, sk in sp.items():
            print(f"[profile] 阶段偏好已写入记忆: {stg} -> {sk}")
        try:
            proactive.sync_memory_markdown(prof)
            print("[profile] 已同步活跃记忆到 output/memory.md")
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    set_roots = a.set_roots
    add_roots = [a.add_root] if a.add_root else None
    only_fixed = (
        True if a.only_fixed else (False if a.allow_all_roots else None)
    )
    add_directives = [a.add_directive] if a.add_directive else None
    remove_directives = [a.remove_directive] if a.remove_directive else None
    clear_directives = a.clear_directives
    pin_skills = [a.pin] if a.pin else None
    unpin_skills = [a.unpin] if a.unpin else None
    add_excludes = [a.exclude] if a.exclude else None
    reason = a.reason

    res = proactive.update_profile_and_memory(
        set_roots=set_roots,
        add_roots=add_roots,
        only_fixed=only_fixed,
        add_directives=add_directives,
        remove_directives=remove_directives,
        clear_directives=clear_directives,
        pin_skills=pin_skills,
        unpin_skills=unpin_skills,
        add_excludes=add_excludes,
        reason=reason,
    )
    if res.get("modified"):
        for ch in res["changes"]:
            print(f"[profile] 已生效: {ch}")
        prof = res["prof"]
    elif a.sync_memory:
        proactive.sync_memory_markdown(prof)
        print("[profile] 已同步活跃记忆到 output/memory.md")

    # 显示当前配置摘要
    print("# 用户重要指令与技能画像 (Profile)")
    print(
        f"配置文件: {prof_path} ({'已存在' if os.path.isfile(prof_path) else '未创建，可用 --init 创建'})\n"
    )

    print("## 1. 扫描根控制")
    fixed_roots = prof.get("fixed_roots", [])
    only_fixed_val = prof.get("only_fixed_roots", False)
    print(
        f"- 固定扫描目录: {', '.join(fixed_roots) if fixed_roots else '无（使用系统默认发现策略）'}"
    )
    print(
        f"- 严格扫描模式 (only_fixed_roots): {'开启（仅扫固定目录，等价 ONLY_DIRS=1）' if only_fixed_val else '未开启（融合自身相对与默认目录）'}\n"
    )

    print("## 2. 常用技能与 MCP 偏好")
    pinned_skills = prof.get("pinned_skills", [])
    pinned_mcps = prof.get("pinned_mcps", [])
    print(
        f"- 置顶常用技能 ({len(pinned_skills)} 个): {', '.join(pinned_skills) if pinned_skills else '无'}"
    )
    print(
        f"- 常用 MCP ({len(pinned_mcps)} 个): {', '.join(pinned_mcps) if pinned_mcps else '无'}\n"
    )

    print("## 3. 排除规则黑名单")
    excludes = prof.get("exclude_skills", [])
    print(
        f"- 排除规则 ({len(excludes)} 条): {', '.join(excludes) if excludes else '无'}\n"
    )

    print("## 4. 用户全局重要指令 (User Directives)")
    directives = prof.get("user_directives", [])
    if directives:
        for idx, d in enumerate(directives, 1):
            print(f"{idx}. {d}")
    else:
        print("暂无全局指令（可用 --add-directive 追加）")
    print()

    quota = prof.get("retrieval_quota", {})
    if quota:
        print("## 5. 检索容量与打分偏好")
        print(f"- 默认 Top-K: {quota.get('top_k', 8)}")
        print(f"- 最低阈值分 (min_score): {quota.get('min_score', 0.35)}")
        print(f"- 常用技能加权分 (favorite_boost): +{quota.get('favorite_boost', 3.0)}")

    pro_cfg = proactive.get_proactive_config()
    pro_state = proactive.load_proactive_state()
    print("\n## 6. 自适应进化与主动更新 (Self-Improving Agent)")
    print(f"- 智能更新机制: {'已开启' if pro_cfg.get('enabled') else '已关闭'}")
    print(f"- 心跳轮询周期: {pro_cfg.get('interval_seconds')} 秒")
    print(f"- 累计交互记录: {pro_state.get('interaction_count', 0)} 次")
    freq = pro_state.get("skill_frequency", {})
    if freq:
        top_freq = sorted(freq.items(), key=lambda x: -x[1])[:5]
        top_str = "、".join(f"{k}({v}次)" for k, v in top_freq)
        print(f"- 高频调用追踪: {top_str}")
    promoted = pro_state.get("promoted_skills", [])
    if promoted:
        print(f"- 自省晋升置顶: {', '.join([p['skill'] for p in promoted])}")


def cmd_memory(a):
    """查看 Self-Improving 活跃记忆 (output/memory.md) 与历史纠偏审计日志 (output/corrections.md)"""
    mem_file = proactive.get_memory_file()
    corr_file = proactive.get_corrections_file()
    if not os.path.isfile(mem_file):
        prof = paths.load_skill_profile()
        if prof:
            proactive.sync_memory_markdown(prof)

    if os.path.isfile(mem_file):
        with open(mem_file, "r", encoding="utf-8") as f:
            print(f.read().strip())
    else:
        print("# Active Memory: 暂无活跃记忆文件（可通过 profile 自动生成）")

    if os.path.isfile(corr_file):
        print("\n--- 最近纠偏与重要指示审计 (corrections.md) ---")
        with open(corr_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
            lines = content.splitlines()
            if len(lines) > 25:
                print("\n".join(lines[-25:]))
            else:
                print(content)


def cmd_clean(a):
    """清理运行时生成的索引与产物，使技能包恢复为纯净源数据状态（打包发布/外部测试前使用）。
    名单制：只删已知运行时产物（LEGACY_OUTPUT_FILES 文件名），清单之外的文件
    （README.md、.gitkeep、以及任何后续新增的源数据文件）一律不动。"""

    import shutil

    cleaned = []
    # 1. 清理根目录下的遗留产物
    for fname in paths.LEGACY_OUTPUT_FILES:
        p = os.path.join(paths.ROOT, fname)
        if os.path.isfile(p):
            try:
                os.remove(p)
                cleaned.append(f"root/{fname}")
            except Exception as ex:
                print(f"  删除失败 {p}: {ex}")

    # 2. 清理 output/ 目录：只删运行时产物名单内的文件，其余（含新增源数据）保留
    out_d = paths.out_dir()
    if os.path.isdir(out_d):
        for fname in os.listdir(out_d):
            is_tmp = fname.startswith(".idx-tmp-") or fname.endswith(".tmp")
            if fname not in paths.LEGACY_OUTPUT_FILES and not is_tmp:
                continue  # 非产物名单 = 源数据，绝不清理；.tmp 仅清原子写崩溃残留
            fp = os.path.join(out_d, fname)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    cleaned.append(f"output/{fname}")
                except Exception as ex:
                    print(f"  删除失败 {fp}: {ex}")

    # 2.4 环境本地登记表（inputs/ 运行时状态）：源目录不该持有，clean 一并清除
    inputs_d = os.path.join(paths.ROOT, "inputs")
    if os.path.isdir(inputs_d):
        for fname in paths.RUNTIME_INPUT_FILES:
            fp = os.path.join(inputs_d, fname)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    cleaned.append(f"inputs/{fname}")
                except Exception as ex:
                    print(f"  删除失败 {fp}: {ex}", file=sys.stderr)

    # 2.5 平台桥接易失层（inputs/platform_mirror）：可随时由 bridge 重建
    mirror = os.path.join(paths.ROOT, "inputs", "platform_mirror")
    if os.path.isdir(mirror):
        try:
            shutil.rmtree(mirror, ignore_errors=True)
            cleaned.append("inputs/platform_mirror")
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    # 3. 清理编译缓存与会话工具残留（.zcode 会话计划/.omx 计划文件）
    for root, dirs, _files in os.walk(paths.ROOT):
        for d in ("__pycache__", ".pytest_cache", ".zcode", ".omx"):
            if d in dirs:
                target_dir = os.path.join(root, d)
                try:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    cleaned.append(os.path.relpath(target_dir, paths.ROOT))
                except Exception as _ex:
                    print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    # 4. 系统临时区陈旧测试遗留（skix-* 超过 6 小时 = 崩溃/中断的测试残留）
    import glob
    import time as _time
    _now = _time.time()
    _tmp = __import__("tempfile").gettempdir()
    for _p in glob.glob(os.path.join(_tmp, "skix-*")):
        try:
            if os.path.getmtime(_p) < _now - 6 * 3600:
                if os.path.isdir(_p):
                    shutil.rmtree(_p, ignore_errors=True)
                else:
                    os.remove(_p)
                cleaned.append(f"tmp/{os.path.basename(_p)}")
        except Exception:
            pass

    print(
        f"[clean] 清理完成！共清理 {len(cleaned)} 项运行时产物与缓存，技能包已恢复为纯净源数据状态。"
    )
    if cleaned:
        for c in cleaned[:15]:
            print(f"  - 移除: {c}")
        if len(cleaned) > 15:
            print(f"  - ... 另有 {len(cleaned) - 15} 项")


# ── 命令注册表（唯一定义源）──────────────────────────────────────────────────────
# argparse 子命令、help 输出、dispatch 全部由本表驱动；此前同样的描述在三处各写一份，
# 改一处漏两处。args 项格式：((参数名...), {argparse kwargs})。
# ── 命令注册表（唯一定义源）──────────────────────────────────────────────────────
# argparse 子命令、help 输出、dispatch 全部由本表驱动；此前同样的描述在三处各写一份，
# 改一处漏两处。args 项格式：((参数名...), {argparse kwargs})。
COMMANDS = [
    {
        "name": "index",
        "usage": "index [--broad] [--full]",
        "help": "全量重建索引（首次使用 / 彻底刷新时用）",
        "handler": "cmd_index",
        "args": [
            (("--broad",), {"action": "store_true", "help": "强制启用广域发现（扫 skills/*-skills 目录）"}),
            (("--full",), {"action": "store_true", "help": "全量构建：要求技能含完整 SKILL.md 正文，缺即中止（默认建元数据索引，仅定位/路由用）"}),
        ],
    },
    {
        "name": "update",
        "usage": "update [--broad] [--full]",
        "help": "增量索引：只处理新增/变更/移除的技能，未变项直接复用（日常用这个）",
        "handler": "cmd_update",
        "args": [
            (("--broad",), {"action": "store_true", "help": "强制启用广域发现"}),
            (("--full",), {"action": "store_true", "help": "同 index --full"}),
        ],
    },
    {
        "name": "add",
        "usage": "add <技能名|路径>",
        "help": "把某个尚未索引的技能扫描并加入索引（遇到没索引的技能时用）",
        "handler": "cmd_add",
        "args": [(("target",), {})],
    },
    {
        "name": "roots",
        "usage": "roots",
        "help": "扫描根探针：逐个候选根打印 存在?/找到几个技能，用于解释'为什么是 0 项'",
        "handler": "cmd_roots",
        "args": [],
    },
    {
        "name": "import",
        "usage": "import <文件|-|文本>",
        "help": "把外部技能清单导入索引（平台不落盘、只把技能注入提示词时用）",
        "handler": "cmd_import",
        "args": [(("source",), {"help": "文件路径 / - 读 stdin / 直接文本"})],
    },
    {
        "name": "agent-index",
        "usage": "agent-index [<文件|->] [--agent <智能体名>] [--content]",
        "help": "建立 Agent 级索引：不带清单 = 自动扫描当前环境已挂载技能并入共享清单；带文件则合并导入",
        "handler": "cmd_agent_index",
        "args": [
            (("source",), {"nargs": "?", "default": "",
                            "help": "清单来源：文件路径 / - 读 stdin / 直接文本；省略 = 自动扫描当前环境"}),
            (("--agent",), {"help": "当前智能体名（写入 current_agent，并作为每项默认 agents）"}),
            (("--content",), {"action": "store_true",
                               "help": "同时重建内容索引（默认跳过——首次全量最耗时；未命中自愈会自动补）"}),
            (("--skill-dirs",), {"help": "声明当前环境的技能挂载根（分号/路径分隔符分隔多个）。"
                                              "调用方 agent 知道自己的平台把技能挂在哪里，直接声明即可——"
                                              "发现扫描根缺失时用这个参数，不要改代码"}),
            (("--desc-zh",), {"help": "中文描述翻译 JSON（{技能名: 中文描述}）。建索引时对缺译条目"
                                       "当场生成翻译回传，网关写入登记表——每加一个技能自动翻一个，零人工"}),
            (("--desc-zh-file",), {"help": "翻译 JSON 文件路径（大量翻译时用，格式同 --desc-zh）"}),
            (("--no-translate",), {"action": "store_true",
                                    "help": "关闭翻译 API 现场回填（离线/不想外发时用）"}),
            (("--prune-agent",), {"help": "清除指定智能体的绑定（agents 含该名的条目被移出清单），用于清理误导入"}),
        ],
    },
    {
        "name": "list",
        "usage": "list [--type skill|mcp|connector|agent] [--visibility ready|off-list|blocked|all] [--with-ref] [--limit N] [--excluded]",
        "help": "列出全部技能与 MCP + 笼统使用建议；--excluded 仅列出已排除技能",
        "handler": "cmd_list",
        "args": [
            (("--type",), {"choices": ["skill", "mcp", "connector", "agent"]}),
            (("--visibility",), {"choices": ["ready", "off-list", "blocked", "all"],
                                  "default": "ready",
                                  "help": "按可见性过滤：ready=平台可用(默认)；off-list=平台未绑定；blocked=已屏蔽；all=全部"}),
            (("--with-ref",), {"action": "store_true", "help": "仅含参考资料的技能"}),
            (("--limit",), {"type": int, "default": 0, "help": "只显示前 N 条"}),
            (("--agent",), {"action": "store_true", "dest": "agent_only",
                             "help": "只看当前 Agent 绑定的技能（agent_bound=False 的不显示）"}),
            (("--excluded",), {"action": "store_true", "help": "仅查看被排除的技能清单及原因"}),
        ],
    },
    {
        "name": "search",
        "usage": "search <关键词> [--top N] [--exclude <规则>] [--all]",
        "help": "按关键词全量打分检索（IDF加权+硬顶20条配额+低分剪枝+未命中自愈更新）",
        "handler": "cmd_search",
        "args": [
            (("query",), {}),
            (("--top",), {"type": int, "default": 8}),
            (("--exclude",), {"help": "临时排除特定技能（支持逗号分隔或通配符，如 test-*,mock-*）"}),
            (("--all",), {"action": "store_true", "dest": "all_items",
                           "help": "全量审计视图：放行平台未绑定(off-list)技能，不得作为交付依据"}),
            (("--agent",), {"action": "store_true", "dest": "agent_only",
                             "help": "Agent 绑定口径：只出当前 Agent 绑定的技能（非默认，默认平台口径）"}),
        ],
    },
    {
        "name": "explain",
        "usage": "explain <技能名>",
        "help": "某技能的详细使用建议（做什么/触发词/何时用/位置）",
        "handler": "cmd_explain",
        "args": [(("name",), {})],
    },
    {
        "name": "detail",
        "usage": "detail <技能名>",
        "help": "按需读取某技能完整 SKILL.md 全文（默认不进上下文）",
        "handler": "cmd_detail",
        "args": [(("name",), {})],
    },
    {
        "name": "related",
        "usage": "related <技能名> [--top N]",
        "help": "找与该技能相关的其他技能（读索引里存好的关系边 overlap/family/depends_on/contains/similar）",
        "handler": "cmd_related",
        "args": [
            (("name",), {}),
            (("--top",), {"type": int, "default": 5}),
        ],
    },
    {
        "name": "stats",
        "usage": "stats",
        "help": "索引覆盖口径：按来源/类型、全文覆盖、缺什么、关系图",
        "handler": "cmd_stats",
        "args": [],
    },
    {
        "name": "audit",
        "usage": "audit [--top N]",
        "help": "Token 账本：量化索引 vs 全文的上下文压缩率",
        "handler": "cmd_audit",
        "args": [(("--top",), {"type": int, "default": 10})],
    },
    {
        "name": "bundle",
        "usage": "bundle",
        "help": "生成 L0/L1/L2 三层分片 + 关系图 + boot.md（可上传数字员工网页）",
        "handler": "cmd_bundle",
        "args": [],
    },
    {
        "name": "doctor",
        "usage": "doctor [--graph] [--fix]",
        "help": "技能体检：坏/空描述、缺触发词、超大技能、重名、路径缺失；--graph 增加图谱质量审查；--fix 自动修复",
        "handler": "cmd_doctor",
        "args": [
            (("--graph",), {"action": "store_true", "help": "增加图谱质量审查（孤立节点/边分布/方向冲突）"}),
            (("--fix",), {"action": "store_true", "help": "自动修复图谱问题（孤立节点补边/方向冲突去重/高密度节点剪枝）"}),
        ],
    },
    {
        "name": "export",
        "usage": "export <minjson|md|csv|txt>",
        "help": "多格式导出（紧凑 JSON 含关系边 / Markdown / CSV / 纯文本）",
        "handler": "cmd_export",
        "args": [(("format",), {"choices": list(export_mod.FORMATS)})],
    },
    {
        "name": "route",
        "usage": "route <用户话术>",
        "help": "把自然语言话术路由到最合适的子命令（用户没说命令名时的兜底）",
        "handler": "cmd_route",
        "args": [(("text",), {})],
    },
    {
        "name": "catalog",
        "usage": "catalog",
        "help": "生成可阅读的 Markdown 总览 catalog.md",
        "handler": "cmd_catalog",
        "args": [],
    },
    {
        "name": "content-index",
        "usage": "content-index [--broad]",
        "help": "全量构建内容索引（扫描每个技能的全部文本文件并分块）",
        "handler": "cmd_content_index",
        "args": [(("--broad",), {"action": "store_true"})],
    },
    {
        "name": "content-update",
        "usage": "content-update [--broad]",
        "help": "增量更新内容索引（只重扫变更文件）",
        "handler": "cmd_content_update",
        "args": [(("--broad",), {"action": "store_true"})],
    },
    {
        "name": "content-find",
        "usage": "content-find <query> [--full] [--top N]",
        "help": "内容检索：查'答案在哪'→ 返回文件+行号+摘要；--full 打印原文",
        "handler": "cmd_content_find",
        "args": [
            (("query",), {}),
            (("--top",), {"type": int, "default": 10}),
            (("--full",), {"action": "store_true", "help": "打印匹配块的原文"}),
        ],
    },
    {
        "name": "chat",
        "usage": "chat <query> [--top N] [--exclude <规则>] [--all]",
        "help": "技能图谱问答：输入自然语言问题，返回推荐技能 + 关联 + 相关内容",
        "handler": "cmd_chat",
        "args": [
            (("query",), {"help": "自然语言问题"}),
            (("--top",), {"type": int, "default": 5, "help": "推荐技能数量"}),
            (("--exclude",), {"help": "临时排除特定技能（支持逗号分隔或通配符）"}),
            (("--all",), {"action": "store_true", "dest": "all_items",
                           "help": "全量审计视图：放行平台未绑定(off-list)技能"}),
            (("--agent",), {"action": "store_true", "dest": "agent_only",
                             "help": "Agent 绑定口径：只出当前 Agent 绑定的技能（非默认，默认平台口径）"}),
        ],
    },
    {
        "name": "onboard",
        "usage": "onboard <技能名>",
        "help": "技能上手指南：生成结构化入门文档（概述/关联/文件结构/快速上手）",
        "handler": "cmd_onboard",
        "args": [(("name",), {"help": "技能名"})],
    },
    {
        "name": "dashboard",
        "usage": "dashboard [--open] [--download] [--serve] [--port N]",
        "help": "生成技能图谱可视化面板（静态 HTML，D3.js 力导向图）；--open/--download/--serve 均为显式开关",
        "handler": "cmd_dashboard",
        "args": [
            (("--open",), {"dest": "open_browser", "action": "store_true", "default": False,
                           "help": "生成后在默认浏览器中打开（默认不自动打开）"}),
            (("--download",), {"action": "store_true", "default": False,
                               "help": "同步一份到系统下载目录（默认不同步）"}),
            (("--serve",), {"action": "store_true",
                            "help": "启动本地轻量 HTTP 服务器在线预览（仅绑定 127.0.0.1）"}),
            (("--port",), {"type": int, "default": 8765, "help": "HTTP 服务器端口（默认 8765）"}),
        ],
    },
    {
        "name": "diff",
        "usage": "diff <技能名>",
        "help": "变更影响分析：哪些下游技能会受影响（depends_on/contains/二级影响）",
        "handler": "cmd_diff",
        "args": [(("name",), {"help": "技能名"})],
    },
    {
        "name": "pipeline",
        "usage": "pipeline <任务> [--json] [--all]   （别名: chain）",
        "help": "技能图谱串联编排：按任务检索 + 依图谱拓扑（depends_on 分层/相似聚类）动态生成协同流水线",
        "handler": "cmd_pipeline",
        "aliases": ["chain"],
        "args": [
            (("query",), {"help": "任务描述"}),
            (("--json",), {"action": "store_true", "help": "输出 JSON 格式"}),
            (("--all",), {"action": "store_true", "dest": "all_items",
                           "help": "全量审计视图：编排种子放行平台未绑定(off-list)技能"}),
            (("--agent",), {"action": "store_true", "dest": "agent_only",
                             "help": "Agent 绑定口径：编排种子只取当前 Agent 绑定的技能（非默认，默认平台口径）"}),
            (("--lifecycle",), {"action": "store_true", "dest": "force_lifecycle",
                                  "help": "按开发生命周期契约编排（开发/工程任务由调用方 agent 显式声明——CLI 不猜意图）"}),
        ],
    },
    {
        "name": "profile",
        "usage": "profile [--init] [--set-roots R...] [--pin S]...",
        "help": "用户重要指令与技能画像管理：配置固定扫描根、排他模式、置顶技能与全局必遵指令（自动同步记忆库）",
        "handler": "cmd_profile",
        "args": [
            (("--init",), {"action": "store_true", "help": "初始化标准 skill_profile.json 模板"}),
            (("--set-roots",), {"nargs": "+", "help": "覆盖设置固定扫描根目录 (fixed_roots)"}),
            (("--add-root",), {"help": "添加固定扫描根目录 (fixed_roots)"}),
            (("--only-fixed",), {"action": "store_true",
                                  "help": "开启严格排他模式 (only_fixed_roots=true，仅扫描固定根)"}),
            (("--allow-all-roots",), {"action": "store_true",
                                       "help": "关闭严格排他模式 (融合自身相对与默认根)"}),
            (("--pin",), {"help": "将某技能加入常用置顶名单 (pinned_skills)"}),
            (("--unpin",), {"help": "从常用置顶名单中移除某技能"}),
            (("--exclude",), {"help": "添加技能排除通配规则 (exclude_skills)"}),
            (("--add-directive",), {"help": "沉淀全局用户重要指令 (user_directives)"}),
            (("--stage-pref",), {"action": "append", "dest": "stage_pref",
                                  "help": "写入阶段偏好记忆：--stage-pref 「阶段=技能名」（并列候选人工确认后用，可重复）"}),
            (("--remove-directive",), {"help": "移除指定用户全局指令"}),
            (("--clear-directives",), {"action": "store_true", "help": "清空全部用户全局指令"}),
            (("--reason",), {"help": "变更背景/原因（记录到 corrections.md）"}),
            (("--sync-memory",), {"action": "store_true",
                                  "help": "强制同步当前活跃记忆至 output/memory.md"}),
        ],
    },
    {
        "name": "memory",
        "usage": "memory",
        "help": "查看 Self-Improving 活跃记忆库 (output/memory.md) 与历史纠偏审计日志 (corrections.md)",
        "handler": "cmd_memory",
        "args": [],
    },
    {
        "name": "ledger",
        "usage": "ledger [--limit N]",
        "help": "命中率账本报表：零命中 query（能力缺口工单）、技能命中/采纳榜、僵尸技能",
        "handler": "cmd_ledger",
        "args": [
            (("--limit",), {"type": int, "default": 0, "help": "只统计最近 N 条事件（默认全部）"}),
        ],
    },
    {
        "name": "pack",
        "usage": "pack",
        "help": "生成交付包 skill-gateway.zip（自动 clean；排除测试/缓存/运行产物与本机清单）",
        "handler": "cmd_pack",
        "args": [],
    },
    {
        "name": "clean",
        "usage": "clean",
        "help": "清理全部运行时生成的索引与产物文件，恢复为纯净源数据状态（打包分发/外部测试前用）",
        "handler": "cmd_clean",
        "args": [],
    },
    {
        "name": "help",
        "usage": "help",
        "help": "列出全部命令及其作用（本命令）",
        "handler": "cmd_help",
        "args": [],
    },
]


def cmd_help(a):
    print("# skill-gateway 命令一览\n")
    for spec in COMMANDS:
        print(f"- {spec.get('usage') or spec['name']}: {spec['help']}")
    print("\n# 退出码约定：0=成功；2=预期内的未命中/未找到（可换词重试）；1=异常错误")
    print("# 另有独立脚本（不进本 CLI）")
    print(
        "- scripts/platform_bridge.py: 平台宿主目录不可达时，把平台技能清单落成镜像目录再交给 indexer"
    )


def _build_parser():
    p = argparse.ArgumentParser(
        description="skill-gateway: 索引并检索 agent 的全部技能与 MCP"
    )
    sub = p.add_subparsers(dest="cmd")
    for spec in COMMANDS:
        sp = sub.add_parser(
            spec["name"], help=spec["help"], aliases=spec.get("aliases", [])
        )
        for names, kw in spec.get("args", []):
            sp.add_argument(*names, **kw)
    return p


def _dispatch_table():
    return {spec["name"]: globals()[spec["handler"]] for spec in COMMANDS}


def main():
    # Windows 控制台默认 cp936：emoji/中文重定向到管道或文件时直接 UnicodeEncodeError。
    # 统一强制 UTF-8 + replace 兜底（Python 3.13 才对交互控制台默认 UTF-8，管道要到 3.15）。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    p = _build_parser()
    args = p.parse_args()
    # 裸跑（无子命令）打印帮助退出——此前默认执行全量 index，隐式触发全盘扫描
    if not args.cmd:
        p.print_help()
        return RC_OK

    handler = _dispatch_table().get(args.cmd)
    if handler is None:
        p.print_help()
        return RC_ERROR
    try:
        handler(args)
    except SystemExit:
        raise  # 命令内部显式给出的退出码（如 rc=2）原样透传
    except Exception as ex:
        import traceback

        print(f"[error] 命令 '{args.cmd}' 执行失败: {ex}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(RC_ERROR)
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
