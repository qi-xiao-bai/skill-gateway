#!/usr/bin/env python3
# platform_bridge.py - 平台桥接：把"平台技能清单"落成镜像目录，再让 indexer 扫镜像
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 镜像默认改到技能包内(inputs/platform_mirror) + 清单指纹 + --status/--force
#                          + mirror_and_reindex() 供 index 自愈调用（易失层不再靠缓存活着）
# updated 2026-09-16 qjl: **--mode 默认改 full**：构建必须是全量的——清单缺完整 SKILL.md 正文就
#                          中止、不写索引（此前默认 list，会把"仅描述占位"当成索引产出，误导）
#
# 为什么需要它：
#   有些平台（数字员工/沙箱）把技能存在平台侧，宿主技能目录对 shell 不可达——
#   scanner 扫遍约定目录也找不到 SKILL.md，index 必然是 0 项（这不是 indexer 的 bug，是数据源不可达）。
#   解决：走平台正规通道拿到技能（如 skill_follow(list) 取清单 + skill_follow(load) 取全文），
#        由本脚本把技能"落成"一个本地镜像目录（每个技能一个 <name>/SKILL.md），
#        再以子进程方式调用原 build_index.py —— 不改 indexer 任何原有逻辑。
#
# 全量 vs 降级（重要）：
#   全量（默认，--mode full）：清单每项都带完整 SKILL.md 正文 → 真索引。缺任何一项就中止。
#   降级（--mode list 显式）：清单只有 name/description → 只能得到"元数据索引"，
#        镜像里的 SKILL.md 是**占位文件**（带 MIRROR_STUB_MARK 标记），**不算一次构建**。
#
# 生命周期分层（哪层易失、哪层持久）：
#   ① 索引产物 skill-index.json / L1 / catalog …  ← 写在技能根目录，**易失**（随沙箱回收）
#   ② 镜像目录 inputs/platform_mirror/            ← 写在技能包内，**易失**（同上）
#   ③ 本脚本 + 文档                                ← 平台数据库里的技能包，**持久**（真正的资产）
#   ④ 清单 inputs/platform_skills.json             ← 平台 side 暂存，**大概率在，不保证**
#   ⇒ 「一条命令重建」**只对 ①② 的元数据成立**：清单/镜像能重落、索引能重算。
#     **完整 SKILL.md 全文从来不在 ①②④ 里** —— 全文丢了恢复不了，必须回平台逐技能 load 重取。
#     重建（日常）：python scripts/platform_bridge.py
#     看状态：     python scripts/platform_bridge.py --status
#
# 用法：
#   python scripts/platform_bridge.py                      # 默认 full：清单→镜像→index+audit（缺正文则中止）
#   python scripts/platform_bridge.py --status             # 生命周期状态：清单/镜像/索引 在不在、是不是全量
#   python scripts/platform_bridge.py --mode list           # 显式接受降级：只建"元数据索引"
#   python scripts/platform_bridge.py --src skills.json
#   python scripts/platform_bridge.py --src -              # 从 stdin 读清单
#   python scripts/platform_bridge.py --force              # 清单没变也强制重落镜像
#   python scripts/platform_bridge.py --mirror /path/to/mirror
#   python scripts/platform_bridge.py --clean              # 按 manifest 清掉上次镜像
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import paths                     # noqa: E402
import index_store               # noqa: E402  (共用"仅描述占位"标记)
from importer import parse_source  # noqa: E402

MANIFEST = ".platform_bridge.json"
FULL_KEYS = ("content", "skill_md", "skillmd", "body", "raw", "text")
# 技能名里不允许出现的字符（路径分隔符/盘符/通配符/控制符）——防止落盘时逃逸出镜像目录
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def safe_dirname(name):
    """把技能名净化成安全的**目录名**：去掉路径分隔符与上跳，杜绝写到镜像目录之外。
    注意：索引里的技能名取自镜像 SKILL.md 的 frontmatter，所以净化目录名不影响技能名。"""
    s = _UNSAFE_CHARS.sub("_", (name or "").strip())
    s = s.replace("..", "__")
    s = s.strip(". ")                      # 去掉首尾点/空格（避免 "."、".."、隐藏名）
    if not s:
        s = "_unnamed"
    return s[:120]


def _unique_dirname(safe, used):
    """同名净化结果去重（a/b 与 a:b 会撞车），撞了就加 -2、-3。"""
    if safe not in used:
        used.add(safe)
        return safe
    i = 2
    while f"{safe}-{i}" in used:
        i += 1
    out = f"{safe}-{i}"
    used.add(out)
    return out


def default_src():
    """默认清单来源：环境变量 → <技能根>/inputs/platform_skills.json → inputs/ 下任意 json。"""
    env = os.getenv("PLATFORM_SKILLS_JSON")
    if env and os.path.isfile(env):
        return env
    inputs = os.path.join(ROOT, "inputs")
    cand = os.path.join(inputs, "platform_skills.json")
    if os.path.isfile(cand):
        return cand
    if os.path.isdir(inputs):
        for n in sorted(os.listdir(inputs)):
            if n.lower().endswith(".json"):
                return os.path.join(inputs, n)
    return None


def default_mirror():
    """默认镜像目录：<技能根>/inputs/platform_mirror —— 放在技能包内，
    与"清单暂存"同一处（都是易失期运行时缓存），且不污染宿主 $HOME。
    可用 --mirror 或 SKILL_GATEWAY_MIRROR 覆盖。"""
    return os.path.join(ROOT, "inputs", "platform_mirror")


def fingerprint(items):
    """清单指纹：只看 name+description，顺序无关。用来判断镜像是否还是最新。"""
    h = hashlib.sha1()
    for it in sorted(items, key=lambda x: (x.get("name") or "").lower()):
        h.update(((it.get("name") or "") + "\x1f" + (it.get("description") or "") + "\x1e").encode("utf-8"))
    return h.hexdigest()[:12]


def read_manifest(mirror):
    p = os.path.join(mirror, MANIFEST)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
    except Exception:
        return None


def _full_body(item):
    """清单项里若带完整 SKILL.md 正文则取出来（mode=full 保真用）。"""
    if not isinstance(item, dict):
        return None
    for k in FULL_KEYS:
        v = item.get(k)
        if isinstance(v, str) and len(v.strip()) > 40 and v.lstrip().startswith("---"):
            return v
    for k in FULL_KEYS:
        v = item.get(k)
        if isinstance(v, str) and len(v.strip()) > 40:
            return v
    return None


def raw_items(text):
    """尽量保留原始清单项（parse_source 只给 name/description，保真还需要原始字段）。"""
    try:
        data = json.loads(text)
    # 旁路设计：此处静默吞错是有意为之（best-effort，不影响主流程）
    except Exception:
        return None
    if isinstance(data, dict):
        for k in ("skills", "entries", "items", "list", "available_skills"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    return data if isinstance(data, list) else None


def write_mirror(items, raws, mirror, overwrite=True, src_path=None, src_fp=None):
    """把清单落成镜像：<mirror>/<净化后的名字>/SKILL.md。
    目录名经 safe_dirname 净化（防路径逃逸）；技能名仍取原名写进 frontmatter。
    返回 (dirs, n_full, manifest)。"""
    os.makedirs(mirror, exist_ok=True)
    dirs, n_full, used = [], 0, set()
    raw_by_name = {}
    for r in (raws or []):
        if isinstance(r, dict):
            nm = (r.get("name") or r.get("title") or r.get("skill")
                  or r.get("skill_name") or "").strip().strip("`\"'")
            if nm:
                raw_by_name[nm] = r
    for it in items:
        name = it["name"]
        dirname = _unique_dirname(safe_dirname(name), used)
        d = os.path.join(mirror, dirname)
        if os.path.isdir(d) and not overwrite:
            dirs.append(dirname)
            continue
        os.makedirs(d, exist_ok=True)
        body = _full_body(raw_by_name.get(name))
        desc = (it.get("description") or "").replace("\n", " ").strip()
        if body:
            text = body if body.lstrip().startswith("---") else \
                f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}"
            n_full += 1
        else:
            text = (f"---\nname: {name}\ndescription: {desc}\n---\n\n"
                    f"# {name}\n\n{desc}\n\n"
                    f"> {index_store.MIRROR_STUB_MARK}（仅描述，非完整 SKILL.md）。\n")
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)
        dirs.append(dirname)
    man = {"created": [i["name"] for i in items], "dirs": dirs,
           "ts": int(time.time()), "count": len(dirs),
           "src_path": src_path, "src_fp": src_fp,
           "src_mode": "full" if n_full else "list", "n_full": n_full,
           "mirror": mirror}
    with open(os.path.join(mirror, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return dirs, n_full, man


def clean_mirror(mirror):
    """按 manifest 清掉上次落下的镜像目录（只删我们建的，不动别的）。
    用 manifest 里的 dirs（净化后的目录名），兼容旧 manifest 的 created。"""
    man = read_manifest(mirror)
    if not man:
        return []
    names = man.get("dirs") or [safe_dirname(n) for n in man.get("created", [])]
    removed = []
    for n in names:
        d = os.path.join(mirror, n)
        sk = os.path.join(d, "SKILL.md")
        if os.path.isfile(sk):
            try:
                os.remove(sk)
                os.rmdir(d)
                removed.append(n)
            except OSError:
                pass
    try:
        os.remove(os.path.join(mirror, MANIFEST))
    except OSError:
        pass
    return removed


def status_text(mirror, src=None):
    """生命周期状态：清单 / 镜像 / 索引 三层易失物各在不在、一不一致。"""
    src = src if src is not None else default_src()
    man = read_manifest(mirror)
    idx = os.path.join(paths.out_dir(), "skill-index.json")
    lines = ["# 平台桥接状态（易失层随沙箱回收消失）", ""]
    # 清单
    if src and os.path.isfile(src):
        try:
            with open(src, encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
            items = parse_source(raw_text)
            fp = fingerprint(items)
            has_full = sum(1 for r in (raw_items(raw_text) or []) if _full_body(r))
            lines.append(f"  清单  {src}")
            lines.append(f"        有 · {len(items)} 项 · fp={fp} · "
                         + (f"含完整正文 {has_full} 项" if has_full
                            else "**只有元数据（无完整 SKILL.md），不支持全量构建**"))
        except Exception as e:
            items, fp = [], None
            lines.append(f"  清单  {src}  读取失败: {e}")
    else:
        items, fp = [], None
        lines.append("  清单  （没找到）—— 需平台通道导出到 inputs/platform_skills.json")
    # 镜像
    n_sk = 0
    if os.path.isdir(mirror):
        n_sk = sum(1 for n in os.listdir(mirror)
                   if os.path.isfile(os.path.join(mirror, n, "SKILL.md")))
    if not os.path.isdir(mirror):
        lines.append(f"  镜像  {mirror}")
        lines.append("        缺（易失层已回收）→ 跑 python scripts/platform_bridge.py 重建")
    else:
        fresh = "未知"
        if man and fp:
            fresh = "与清单一致 ✅" if man.get("src_fp") == fp else "已过期（清单变了）⚠️"
        n_full = (man or {}).get("n_full", 0)
        kind = ("含完整正文 ✅" if man and n_full >= n_sk and n_sk
                else "**仅描述占位 ⚠️ 非全量**" if man else "未知")
        lines.append(f"  镜像  {mirror}")
        lines.append(f"        有 · {n_sk} 个 SKILL.md · {kind} · {fresh}")
    # 索引
    if os.path.isfile(idx):
        try:
            with open(idx, encoding="utf-8") as f:
                d = json.load(f)
            lines.append(f"  索引  {idx}")
            lines.append(f"        有 · {d.get('count', '?')} 项 · {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(idx)))}")
        except Exception:
            lines.append(f"  索引  {idx}  有（读取失败）")
    else:
        lines.append(f"  索引  {idx}")
        lines.append("        缺 → 跑 python scripts/platform_bridge.py 重建")
    lines.append("")
    lines.append("  「一条命令重建」只覆盖元数据层（镜像/索引）：python scripts/platform_bridge.py")
    lines.append("  但完整 SKILL.md 全文从来不在清单里 —— 全文丢了就恢复不了，"
                 "必须回平台逐技能重新取全文再落一次。")
    lines.append("  持久的是本脚本与文档（技能包）；清单的获取方式（平台 skill_follow(list)）随时能重拿。")
    return "\n".join(lines)


def _mirror_steps(python, mirror, steps):
    """以子进程调用原 build_index.py（不改其逻辑）；
    用 SKILL_GATEWAY_SKILL_DIRS 指向镜像 + SKILL_GATEWAY_ONLY_DIRS=1 —— 只扫镜像，不掺本机目录。
    置 SKILL_GATEWAY_BRIDGE_RUN=1 防止 index 的自愈钩子递归。"""
    # 子进程环境按需白名单（不整包传递 os.environ——避免泄漏密钥类变量）
    allow = ("PATH", "HOME", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "APPDATA",
             "LOCALAPPDATA", "USERPROFILE", "PYTHONPATH", "LANG", "LC_ALL",
             "COMSPEC", "PATHEXT", "WINDIR", "PROGRAMFILES")
    env = {k: os.environ[k] for k in allow if k in os.environ}
    env.update({k: v for k, v in os.environ.items()
                if k.startswith(("SKILL_GATEWAY_", "PLATFORM_"))})
    env["SKILL_GATEWAY_SKILL_DIRS"] = mirror
    env["SKILL_GATEWAY_ONLY_DIRS"] = "1"
    env["SKILL_GATEWAY_BRIDGE_RUN"] = "1"
    entry = os.path.join(SCRIPTS_DIR, "build_index.py")
    ok = True
    for step in steps:
        print(f"[bridge] -> build_index.py {step}")
        r = subprocess.run([python, entry, step], cwd=ROOT, env=env)
        if r.returncode != 0:
            ok = False
            print(f"[bridge] {step} 退出码 {r.returncode}")
    return ok


def _dirs_present(mirror, man):
    """manifest 里记的目录是否都还在（可能存在 manifest 在、目录被回收的情况）。"""
    dirs = man.get("dirs") or [safe_dirname(n) for n in man.get("created", [])]
    if not dirs:
        return False
    return all(os.path.isfile(os.path.join(mirror, d, "SKILL.md")) for d in dirs)


def mirror_and_reindex(src=None, mirror=None, steps=("index", "audit"),
                       force=False, keep=False, python=None, quiet=False, mode="full"):
    """可复用入口（index 自愈也调它）：清单 → 镜像 → 跑 indexer。
    mode="full"（默认）：**要求清单里每个技能都带完整 SKILL.md 正文**；只要有一个没带，
        就不写索引、直接返回错误 —— 「构建」必须是全量的，占位元数据不算构建。
    mode="list"：显式接受"仅描述"降级（元数据索引），调用方必须把"非全量"讲清楚。
    force=清单没变也重落；keep=已存在的镜像目录不覆盖。
    返回 dict(ok, items, created, n_full, fresh, partial, error)。"""
    python = python or sys.executable
    mirror = os.path.abspath(paths.expand(mirror or os.environ.get("SKILL_GATEWAY_MIRROR")
                                         or default_mirror()))
    src = src if src is not None else default_src()
    if src is None:
        return {"ok": False, "error": "找不到技能清单（inputs/platform_skills.json）",
                "items": 0, "created": 0, "n_full": 0, "fresh": False, "partial": False}
    sp = src if src == "-" else paths.expand(src)
    if src != "-" and not os.path.isfile(sp):
        return {"ok": False, "error": f"清单文件不存在: {sp}",
                "items": 0, "created": 0, "n_full": 0, "fresh": False, "partial": False}
    if src == "-":
        text = sys.stdin.read()
    else:
        with open(sp, encoding="utf-8", errors="replace") as f:
            text = f.read()
    items = parse_source(text)
    if not items:
        return {"ok": False, "error": "清单解析出 0 项", "items": 0,
                "created": 0, "n_full": 0, "fresh": False, "partial": False}
    raws = raw_items(text)
    # 当前清单实际带完整正文的项数：指纹只看 name+description，清单"补了正文"指纹不变，
    # 所以 freshness 还必须比对正文数 —— 否则按 recipe 取回全文后会永远复用旧占位镜像、永远中止。
    n_full_now = sum(1 for r in (raws or []) if _full_body(r))
    fp = fingerprint(items)
    man = read_manifest(mirror)
    fresh = bool(man and man.get("src_fp") == fp and not force
                 and _dirs_present(mirror, man)
                 and man.get("n_full", 0) == n_full_now)
    if not quiet:
        print(f"[bridge] 平台技能数: {len(items)} · fp={fp}"
              f"{'（镜像已是最新，跳过重落）' if fresh else ''}")
    if fresh:
        dirs = man.get("dirs") or [safe_dirname(n) for n in man.get("created", [])]
        n_full = man.get("n_full", 0)
    else:
        dirs, n_full, _ = write_mirror(items, raws, mirror,
                                       overwrite=not keep,
                                       src_path=(None if src == "-" else sp), src_fp=fp)
        if not quiet:
            print(f"[bridge] 镜像就绪: {mirror} （{len(dirs)} 个 SKILL.md"
                  f"{'，含完整正文 ' + str(n_full) + ' 个' if n_full else '，仅描述占位'}）")
    partial = n_full < len(dirs)
    if mode == "full" and partial:
        return {"ok": False, "items": len(items), "created": len(dirs), "n_full": n_full,
                "fresh": fresh, "partial": True, "mirror": mirror, "fp": fp,
                "error": f"清单里只有 {n_full}/{len(dirs)} 项带完整 SKILL.md 正文，"
                         "不足以做全量构建（已中止，未写索引）"}
    ok = True
    if steps:
        ok = _mirror_steps(python, mirror, steps)
    return {"ok": ok, "items": len(items), "created": len(dirs),
            "n_full": n_full, "fresh": fresh, "partial": partial,
            "mirror": mirror, "fp": fp, "error": None}


def _print_full_recipe():
    """打印"怎么才能拿到完整 SKILL.md 正文"的具体步骤（别再说"materialize 一下"这种空话）。"""
    print("         1) skill_follow(list) 拿技能名清单 —— 这一步只有元数据")
    print("         2) 对每个技能 skill_follow(load, <技能名>) 取回完整 SKILL.md 正文")
    print("         3) 拼成一个 JSON，每项带上正文：")
    print('            {"skills": [{"name": "...", "description": "...", '
          '"content": "<完整 SKILL.md 原文，以 --- 开头>"}]}')
    print("            存为 <技能根>/inputs/platform_skills.json")
    print("         4) python scripts/platform_bridge.py     # 默认 --mode full")


def main():
    ap = argparse.ArgumentParser(description="平台技能清单 → 镜像目录 → 交给 indexer")
    ap.add_argument("--src", default=None, help="清单文件，或 - 读 stdin（默认自动找 inputs/*.json）")
    ap.add_argument("--mirror", default=None, help="镜像目录（默认 <技能根>/inputs/platform_mirror）")
    ap.add_argument("--mode", choices=["full", "list"], default="full",
                    help="full=要求清单含完整 SKILL.md 正文（默认；缺正文即中止，不写索引）；"
                         "list=显式接受「仅描述」降级（非全量，只得到元数据索引）")
    ap.add_argument("--status", action="store_true", help="只看生命周期状态，不动任何文件")
    ap.add_argument("--force", action="store_true", help="清单没变也强制重落镜像")
    ap.add_argument("--clean", action="store_true", help="落镜像前先清掉上次镜像（按 manifest）")
    ap.add_argument("--only", choices=["index", "audit", "both", "none", "clean"], default="both",
                    help="只做哪一步；clean=只清理并退出")
    ap.add_argument("--python", default=sys.executable, help="调 indexer 用的解释器")
    ap.add_argument("--keep", action="store_true", help="保留已存在的镜像目录（不覆盖）")
    a = ap.parse_args()

    mirror = os.path.abspath(paths.expand(a.mirror or os.environ.get("SKILL_GATEWAY_MIRROR")
                                          or default_mirror()))

    if a.status:
        print(status_text(mirror, a.src))
        return 0

    if a.clean or a.only == "clean":
        rm = clean_mirror(mirror)
        print(f"[bridge] 已清理上次镜像 {len(rm)} 个: {'、'.join(rm[:20]) or '无'}")
        if a.only == "clean":
            return 0

    src = a.src if a.src is not None else default_src()
    if src is None:
        print("[bridge] 找不到技能清单。请先让平台通道导出，例如：")
        print("         skill_follow(list) -> ensure_file 暂存为 <技能根>/inputs/platform_skills.json")
        print("         或 --src <文件> / --src - （stdin）；用 --status 可看当前各层状态")
        return 2

    steps = [] if a.only == "none" else \
            (["index"] if a.only == "index" else
             (["audit"] if a.only == "audit" else ["index", "audit"]))
    res = mirror_and_reindex(src=src, mirror=mirror, steps=steps,
                             force=a.force, keep=a.keep, python=a.python, mode=a.mode)
    if res.get("error"):
        print(f"[bridge] 失败: {res['error']}")
        if res.get("partial"):
            print("[bridge] 构建必须是全量的，所以这里**没有**写索引。取全文的办法：")
            _print_full_recipe()
            print("[bridge] 如果你确实只要元数据索引，就显式接受降级：--mode list")
        return 2

    print("[bridge] 说明: 平台自带 skill_follow(list/load) 时，「有哪些技能/怎么用」它就是实时索引；"
          "本桥接的价值在 audit(token 账本) / bundle(L0/L1/L2) / export(跨平台导出) 这些 indexer 独有能力。")
    if res.get("partial"):
        print("[bridge] ⚠ 本次是「元数据索引」（--mode list 降级）：索引里的描述来自平台清单，"
              "不是完整 SKILL.md —— 它**不是**一次全量构建。")
        print("[bridge]   要全量，按下面取全文后重跑（默认 --mode full 就会做全量）：")
        _print_full_recipe()
    print("[bridge] 易失提醒: 镜像/索引可一条命令重建（python scripts/platform_bridge.py）；"
          "但完整 SKILL.md 全文从来不在清单里 —— 全文丢了只能回平台重取。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
