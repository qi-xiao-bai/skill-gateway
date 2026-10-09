#!/usr/bin/env python3
# proactive.py - Self-Improving + Proactive Agent 智能更新与自省决策引擎
# 每次使用完后自动多信号决策是否增量更新，包含毫秒级根目录指纹感知、时间窗口心跳、高频技能自进化置顶
# created 2026-09-23 qjl
import hashlib
import json
import os
import time

import index_store
import paths
from paths import load_skill_profile, out_path

# 增长上限：状态文件与纠偏日志不能无限膨胀
MAX_RECENT_EVENTS = 200       # recent_events 只保留最近 N 条
MAX_CORRECTION_LINES = 400    # corrections.md 超过 N 行时截断保留尾部（按条目对齐）


def get_state_file():
    """主动状态与自省日志文件路径（存放在 output/ 隔离区）。"""
    return out_path(".proactive_state.json")


def get_memory_file():
    """HOT 活跃记忆库文件（存放在 output/ 隔离区）。"""
    return out_path("memory.md")


def get_corrections_file():
    """历史指示与纠偏日志文件（存放在 output/ 隔离区）。"""
    return out_path("corrections.md")


def get_roots_fingerprint():
    """极速计算当前所有候选根目录的轻量变动指纹（毫秒级，不深层递归）。
    利用 os.scandir 原生单次系统调用获取子技能包目录与 mtime。
    """
    roots = paths.candidate_roots()
    parts = []
    for r, tag in roots:
        rp = paths.expand(r)
        if os.path.isdir(rp):
            try:
                st = os.stat(rp)
                sub_info = []
                with os.scandir(rp) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                sub_stat = entry.stat(follow_symlinks=False)
                                sub_info.append(f"{entry.name}:{int(sub_stat.st_mtime)}")
                        # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
                        except OSError:
                            continue
                sub_summary = (
                    hashlib.md5(",".join(sorted(sub_info)).encode("utf-8")).hexdigest()[:8]
                    if sub_info
                    else "none"
                )
                parts.append(
                    f"{os.path.normcase(os.path.realpath(rp))}:{int(st.st_mtime)}:{len(sub_info)}:{sub_summary}"
                )
            # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
            except OSError:
                continue
    raw = "|".join(sorted(parts))
    return hashlib.md5(raw.encode("utf-8")).hexdigest() if raw else "empty"


def load_proactive_state():
    """读取主动状态文件；若不存在则返回初始空状态。"""
    p = get_state_file()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict):
                    return d
        # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
        except Exception:
            pass
    return {
        "last_check_time": 0.0,
        "last_roots_fingerprint": "",
        "interaction_count": 0,
        "skill_frequency": {},
        "promoted_skills": [],
        "recent_events": [],
    }


def save_proactive_state(state):
    """保存主动状态到文件（原子写）。"""
    p = get_state_file()
    try:
        state.setdefault("recent_events", [])
        if len(state["recent_events"]) > MAX_RECENT_EVENTS:
            state["recent_events"] = state["recent_events"][-MAX_RECENT_EVENTS:]
        index_store.atomic_write(
            p, json.dumps(state, ensure_ascii=False, indent=2)
        )
        return True
    # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
    except Exception:
        return False


def get_proactive_config():
    """读取 skill_profile.json 中的主动更新配置。"""
    prof = load_skill_profile()
    cfg = prof.get("proactive_update", {})
    return {
        "enabled": cfg.get("enabled", True),
        "interval_seconds": cfg.get("interval_seconds", 300),
        "auto_promote_frequent_skills": cfg.get(
            "auto_promote_frequent_skills", True
        ),
        "frequent_threshold": cfg.get("frequent_threshold", 3),
    }


def evaluate_update_need(context=None, state=None, cfg=None):
    """多信号智能决策核心（Self-Improving Gate）：
    判定当前命令执行完后，是否应当主动触发增量更新。
    返回: (should_update: bool, reason: str, current_fingerprint: str)
    """
    context = context or {}
    state = state or load_proactive_state()
    cfg = cfg or get_proactive_config()

    if not cfg.get("enabled", True):
        return False, "主动更新策略已在配置中禁用", ""

    current_fp = get_roots_fingerprint()
    last_fp = state.get("last_roots_fingerprint", "")

    # 1. 物理指纹感知门槛 (Disk Mutation Gate)
    if last_fp and current_fp != last_fp:
        return (
            True,
            "检测到技能根目录物理指纹变动（新增、修改或删除了技能包）",
            current_fp,
        )

    # 首次运行记录指纹
    if not last_fp:
        return False, "首次指纹初始化", current_fp

    # 2. 时间窗口心跳门槛 (TTL Heartbeat Gate)
    now = time.time()
    last_check = state.get("last_check_time", 0.0)
    interval = cfg.get("interval_seconds", 300)
    if now - last_check > interval and state.get("interaction_count", 0) > 0:
        return True, f"心跳检测周期到期（> {interval}s），触发例行保鲜同步", current_fp

    # 3. 显式上下文触发
    if context.get("force_update"):
        return True, "上下文显式请求触发增量同步", current_fp

    return False, "技能库状态新鲜，无需更新", current_fp


def record_interaction(cmd, context=None, state=None, cfg=None):
    """自适应进化与经验沉淀 (Self-Improving Memory Loop)：
    1. 统计技能**真实使用**频次——只有显式使用（context["skills"]，如 detail 查看全文、
       用户明确采纳）才计频；**出现在检索结果里不算使用**。否则"被搜到 3 次"就自动写进
       pinned_skills，置顶提权又让它更容易被搜到，形成与用户意图无关的正反馈。
    2. 当某技能频次达到阈值且尚未置顶时，自动进化录入 skill_profile.json 的 pinned_skills。
    返回: newly_promoted_list
    """
    context = context or {}
    state = state if state is not None else load_proactive_state()
    cfg = cfg or get_proactive_config()

    state["interaction_count"] = state.get("interaction_count", 0) + 1
    freq = state.setdefault("skill_frequency", {})

    # 只统计显式使用的技能
    involved_skills = set()
    if "skills" in context and isinstance(context["skills"], list):
        for s in context["skills"]:
            if isinstance(s, str) and s.strip():
                involved_skills.add(s.strip().lower())

    for s in involved_skills:
        freq[s] = freq.get(s, 0) + 1

    promoted = []
    if cfg.get("auto_promote_frequent_skills", True) and involved_skills:
        threshold = cfg.get("frequent_threshold", 3)
        pinned_set = paths.get_pinned_skills()
        prof_path = paths.PROFILE_FILE

        for s in involved_skills:
            count = freq.get(s, 0)
            if count >= threshold and s not in pinned_set:
                # 触发自省晋升
                prof = paths.load_skill_profile()
                if prof is not None:
                    pinned_list = prof.setdefault("pinned_skills", [])
                    if s not in [p.lower() for p in pinned_list]:
                        pinned_list.append(s)
                        try:
                            index_store.atomic_write(
                                prof_path,
                                json.dumps(prof, ensure_ascii=False, indent=2),
                            )
                            paths._PROFILE_CACHE["key"] = None
                            promoted.append(s)
                            pinned_set.add(s)
                            state.setdefault("promoted_skills", []).append(
                                {"skill": s, "count": count, "time": time.time()}
                            )
                            # 自动晋升是画像变更：与手动画像变更同链路——同步活跃记忆
                            # + 写纠偏审计（此前绕过两者：memory.md 停在旧态、进化无审计）
                            try:
                                sync_memory_markdown(prof)
                                log_correction(
                                    f"自动进化：{s} 高频使用 {count} 次晋升置顶",
                                    f"pinned_skills += {s}（阈值 {threshold}）")
                            except Exception:
                                pass
                        except Exception:
                            pass

    return promoted


def proactive_post_hook(cmd, context=None):
    """统一后置执行钩子（每次使用完自动调用）：
    负责在后台闭环自省、更新决策与自动增量对齐。
    返回: dict(updated=bool, promoted=list, msg=str)
    """
    context = context or {}
    cfg = get_proactive_config()
    state = load_proactive_state()

    # 1. 经验自省与频次统计
    promoted = record_interaction(cmd, context, state=state, cfg=cfg)

    # 2. 评估是否需要更新
    should_update, reason, current_fp = evaluate_update_need(
        context, state=state, cfg=cfg
    )

    result = {
        "updated": False,
        "promoted": promoted,
        "reason": reason,
        "msg": "",
    }

    if should_update:
        import index_store

        entries, edges, st, has_changes = index_store.auto_update_on_miss(
            targeted=False
        )
        state["last_check_time"] = time.time()
        state["last_roots_fingerprint"] = current_fp
        state.setdefault("recent_events", []).append(
            {
                "time": time.time(),
                "action": "proactive_update",
                "reason": reason,
                "added": len(st.get("added", [])),
                "updated": len(st.get("updated", [])),
                "removed": len(st.get("removed", [])),
            }
        )
        save_proactive_state(state)

        add_n = len(st.get("added", []))
        upd_n = len(st.get("updated", []))
        rem_n = len(st.get("removed", []))
        if has_changes:
            result["updated"] = True
            result["st"] = st
            result["msg"] = (
                f"{reason}（自动增量同步：新增 {add_n} / 更新 {upd_n} / 移除 {rem_n}），知识图谱已无感自愈。"
            )
    else:
        # 未触发更新也同步状态
        if not state.get("last_roots_fingerprint"):
            state["last_roots_fingerprint"] = current_fp
        save_proactive_state(state)

    return result


# ── Self-Improving 核心：用户重要指令与记忆库沉淀（AI 判断，零正则硬编码）──────

def sync_memory_markdown(prof):
    """将当前画像核心规则同步到 output/memory.md (HOT 记忆层)"""
    mem_file = get_memory_file()
    try:
        os.makedirs(os.path.dirname(mem_file), exist_ok=True)
        lines = [
            "# Self-Improving Active Memory (HOT)",
            "",
            "## Confirmed Directives",
        ]
        directives = prof.get("user_directives", [])
        if directives:
            for d in directives:
                lines.append(f"- {d}")
        else:
            lines.append("- (None)")
        lines.append("")
        lines.append("## Active Scopes & Roots")
        fixed = prof.get("fixed_roots", [])
        only_f = prof.get("only_fixed_roots", False)
        lines.append(f"- Fixed Roots: {', '.join(fixed) if fixed else 'Default discovery'}")
        lines.append(f"- Strict Mode (only_fixed_roots): {'Enabled' if only_f else 'Disabled'}")
        lines.append("")
        lines.append("## Pinned Skills")
        pinned = prof.get("pinned_skills", [])
        lines.append(f"- {', '.join(pinned) if pinned else '(None)'}")
        lines.append("")

        with open(mem_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def log_correction(reason, applied_info):
    """记录一次重要指示或纠偏至 output/corrections.md。
    超过 MAX_CORRECTION_LINES 行时按条目边界截断，只保留最近的记录。"""
    corr_file = get_corrections_file()
    try:
        os.makedirs(os.path.dirname(corr_file), exist_ok=True)
        date_str = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"\n## {date_str}\n"
            f"- [Reason/Context] {reason}\n"
            f"  Applied: {applied_info}\n"
        )
        with open(corr_file, "a", encoding="utf-8") as f:
            f.write(entry)
        # 截断：从超出上限的最早完整条目开始丢弃
        with open(corr_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_CORRECTION_LINES:
            keep = lines[-MAX_CORRECTION_LINES:]
            # 对齐到条目边界（## 开头），避免留半条
            for i, ln in enumerate(keep):
                if ln.startswith("## "):
                    keep = keep[i:]
                    break
            index_store.atomic_write(corr_file, "".join(keep))
    except Exception:
        pass


def update_profile_and_memory(
    set_roots=None,
    add_roots=None,
    only_fixed=None,
    add_directives=None,
    remove_directives=None,
    clear_directives=False,
    pin_skills=None,
    unpin_skills=None,
    add_excludes=None,
    remove_excludes=None,
    reason=None,
):
    """画像与记忆库更新引擎（由 AI Agent 判定并传入明确结构化参数，拒绝正则猜意图）：
    1. 原子化更新 skill_profile.json
    2. 同步生成 output/memory.md (HOT active memory)
    3. 沉淀纠偏日志至 output/corrections.md (纠偏与演变审计)
    返回: dict(success=bool, modified=bool, changes=list, prof=dict)
    """
    prof = dict(paths.load_skill_profile())
    modified = False
    changes = []

    # 1. 扫描根控制
    if set_roots is not None:
        normalized = [r.strip().replace("\\", "/") for r in set_roots if r and r.strip()]
        prof["fixed_roots"] = normalized
        modified = True
        changes.append(f"扫描根设置为: {', '.join(normalized)}")
    elif add_roots:
        roots = prof.setdefault("fixed_roots", [])
        for r in add_roots:
            nr = r.strip().replace("\\", "/")
            if nr and nr not in roots:
                roots.append(nr)
                modified = True
                changes.append(f"添加扫描根: {nr}")

    # 2. 排他性/严格模式
    if only_fixed is not None:
        if prof.get("only_fixed_roots") != bool(only_fixed):
            prof["only_fixed_roots"] = bool(only_fixed)
            modified = True
            changes.append(f"严格模式 (only_fixed_roots) 设置为: {bool(only_fixed)}")

    # 3. 排除规则
    if add_excludes:
        excludes = prof.setdefault("exclude_skills", [])
        for e in add_excludes:
            ne = e.strip()
            if ne and ne not in excludes:
                excludes.append(ne)
                modified = True
                changes.append(f"添加排除规则: {ne}")
    if remove_excludes:
        excludes = prof.get("exclude_skills", [])
        to_rem = {e.strip() for e in remove_excludes if e and e.strip()}
        new_ex = [e for e in excludes if e not in to_rem]
        if len(new_ex) != len(excludes):
            prof["exclude_skills"] = new_ex
            modified = True
            changes.append(f"移除排除规则: {', '.join(to_rem)}")

    # 4. 常用技能置顶
    if pin_skills:
        pinned = prof.setdefault("pinned_skills", [])
        for s in pin_skills:
            ns = s.strip()
            if ns and ns.lower() not in [p.lower() for p in pinned]:
                pinned.append(ns)
                modified = True
                changes.append(f"置顶常用技能: {ns}")
    if unpin_skills:
        pinned = prof.get("pinned_skills", [])
        to_unpin = {s.strip().lower() for s in unpin_skills if s and s.strip()}
        new_pinned = [s for s in pinned if s.lower() not in to_unpin]
        if len(new_pinned) != len(pinned):
            prof["pinned_skills"] = new_pinned
            modified = True
            changes.append(f"取消置顶技能: {', '.join(to_unpin)}")

    # 5. 用户重要执行指令 (user_directives)
    if clear_directives:
        if prof.get("user_directives"):
            prof["user_directives"] = []
            modified = True
            changes.append("清空全部用户重要指令")
    if remove_directives:
        directives = prof.get("user_directives", [])
        to_rem = set(remove_directives) if isinstance(remove_directives, (list, tuple, set)) else {remove_directives}
        new_dir = [d for d in directives if d not in to_rem]
        if len(new_dir) != len(directives):
            prof["user_directives"] = new_dir
            modified = True
            changes.append(f"移除指令: {', '.join(to_rem)}")
    if add_directives:
        directives = prof.setdefault("user_directives", [])
        dirs_to_add = add_directives if isinstance(add_directives, (list, tuple)) else [add_directives]
        for d in dirs_to_add:
            nd = d.strip()
            if nd and nd not in directives:
                directives.append(nd)
                modified = True
                changes.append(f"沉淀用户重要指令: {nd}")

    if modified:
        try:
            index_store.atomic_write(
                paths.PROFILE_FILE,
                json.dumps(prof, ensure_ascii=False, indent=2),
            )
            paths._PROFILE_CACHE["key"] = None
        except Exception as ex:
            return {"success": False, "msg": f"写入画像失败: {ex}"}

    # 同步记忆库并记录纠偏审计日志
    sync_memory_markdown(prof)
    if changes:
        log_correction(reason or "Agent profile & memory update", "; ".join(changes))

    return {
        "success": True,
        "modified": modified,
        "changes": changes,
        "prof": prof,
    }
