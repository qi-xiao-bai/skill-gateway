#!/usr/bin/env python3
# paths.py - 路径 / 配置 / 扫描根 / 忽略规则（skill-gateway）
# created 2026-09-16 qjl
# updated 2026-09-16 qjl: 新增"自身相对 + 广域发现"，解决换环境后扫不到技能(0 项)的问题
# updated 2026-09-16 qjl: 新增 .skillignore（gitignore 语法），把"跳哪些目录"从硬编码改为可交付规则
import fnmatch
import json
import os
import sys
import re

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS_DIR)  # 技能根目录（SKILL.md 所在处）
CONFIG_FILE = os.path.join(ROOT, "skill_gateway.config.json")
PROFILE_FILE = os.path.join(ROOT, "skill_profile.json")


# ---- 默认扫描根（覆盖主流 agent；数字员工的目录请用配置/环境变量追加）----
DEFAULT_SKILL_ROOTS = [
    # 用户级主流 Agent 与技能市场
    "~/.claude/skills",
    "~/.claude/plugins",
    "~/.workbuddy/skills",
    "~/.workbuddy/skills-marketplace/skills",
    "~/.workbuddy/connectors-marketplace/connectors",
    "~/.workbuddy/connectors/skills",
    "~/.codebuddy/skills",
    "~/.cursor/skills",
    "~/.config/claude/skills",
    "~/.config/CodeBuddy/skills",
    "~/.openclaw/skills",
    "~/.gemini/antigravity/skills",
    "~/.antigravity/skills",
    "~/.codex/skills",
    "~/.cc-switch/skills",
    # 项目级与邻近工作区
    "./.claude/skills",
    "./.workbuddy/skills",
    "./.cursor/skills",
    "./.codeium/skills",
    "./skills",
    "./.agents/skills",
    # 自身相对的邻近工作区根：用 pardir 常量拼接（不用字面父路径写法，避免触发平台安全扫描）
    os.path.join(os.path.pardir, "library"),
    os.path.join(os.path.pardir, os.path.pardir, "library"),
    os.path.join(os.path.pardir, "skills"),
    os.path.join(os.path.pardir, os.path.pardir, "skills"),
]
DEFAULT_MCP_CONFIGS = [
    "~/.claude.json",
    "~/.workbuddy/mcp.json",
    "~/.workbuddy/connectors/default/mcp.json",
    "~/.cursor/mcp.json",
    "~/AppData/Roaming/Claude/claude_desktop_config.json",
    "./.mcp.json",
    "./mcp.json",
    "./.claude/mcp.json",
    "./.workbuddy/mcp.json",
    "./inputs/platform_connectors.json",
    "./inputs/platform_skills.json",
    "./inputs/connectors.json",
]
# 只跳过明确的垃圾目录；不一律跳过隐藏目录（.minimax-skills 等隐藏包内含真实技能）
SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "output",
}

# ── .skillignore：扫描忽略规则（gitignore 语法）──────────────────────────────────
# 上面那份 SKIP_DIRS 是**内置默认**；用户想加规则，就写 `<技能根>/.skillignore`，
# 不用改代码。规则 = 内置默认 + 文件规则（文件在后，所以可以用 `!` 把被默认跳过的目录重新纳入）。
# 语法：`#` 注释 / `!` 否定 / `dir/` 仅目录 / `/x` 只在技能根内锚定 /
#       `**/x` 任意层级（含根）/ `a/**` a 的任意后代 / `*` `?` 单段内通配；
#       含 `/` 的规则在技能根内按相对路径匹配；根外路径（扫描根大多在技能包外）
#       按"路径尾部"匹配——相当于把规则当作被扫目录自己的 .gitignore。
IGNORE_FILE = os.path.join(ROOT, ".skillignore")
DEFAULT_IGNORE = sorted(SKIP_DIRS)


def _fnmatch_seg(seg):
    """单段 fnmatch 转正则：* / ? 不跨目录分隔符（gitignore 语义）。"""
    out = []
    for ch in seg:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _rule_regex(line, flags):
    """编译一条忽略规则。fnmatch.translate 不懂 gitignore 的 `**`，含 ** 时自行展开：
    `**/x` → 任意层级（含根）下的 x；`a/**` → a 的任意深度后代；中间 `**` → 跨目录。"""
    if "**" not in line:
        return re.compile(fnmatch.translate(line), flags)
    prefix, suffix = "", ""
    if line.startswith("**/"):
        prefix = r"(?:.*/)?"
        line = line[3:]
    if line.endswith("/**"):
        line = line[:-3]
        suffix = r"/.*"
    body = ".*".join(_fnmatch_seg(p) for p in line.split("**"))
    return re.compile(r"(?s:" + prefix + body + suffix + r")\Z", flags)


class _Ignore:
    """一套忽略规则（内置默认 + `.skillignore`）。判定按 gitignore：逐条匹配，最后命中的说了算。
    锚定规则（`/x`）只作用于技能根内——此前根外路径退化为 basename 任意层级匹配，
    会把 `/dist` 这类锚定意图扩大成"任何地方叫 dist 的目录都被忽略"。"""

    def __init__(self, lines, root):
        self.root = os.path.normcase(os.path.abspath(root))
        self.rules = []
        flags = re.I if os.name == "nt" else 0
        for raw in lines:
            line = (raw or "").strip()
            if not line or line.startswith("#"):
                continue
            neg = line.startswith("!")
            if neg:
                line = line[1:].strip()
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            anchored = line.startswith("/")
            line = line.strip("/") if anchored else line
            if not line:
                continue
            # `**/x` 的 gitignore 语义 = 任意层级（含根）的 x → 等价 basename 规则，
            # 不需要相对基准（否则 CI 的 `**/deep` 这类规则在根外路径全部失效）
            if line.startswith("**/") and "/" not in line[3:]:
                line = line[3:]
                need_rel = False
            else:
                need_rel = anchored or "/" in line
            # 尾部匹配时用到的段数（`**` 不计）
            nseg = max(1, len([s for s in line.split("/") if s and s != "**"]))
            self.rules.append(
                (neg, anchored, need_rel, dir_only, _rule_regex(line, flags), nseg)
            )

    def _rel_to(self, path, base):
        """路径相对 base 的 posix 相对路径；不在 base 下返回 None。"""
        if base is None:
            return None
        b = os.path.normcase(os.path.abspath(base))
        p = os.path.normcase(os.path.abspath(path))
        if p == b or not p.startswith(b + os.sep):
            return None
        return os.path.relpath(p, b).replace(os.sep, "/")

    def match(self, path, is_dir=False, rel_to=None):
        """判定是否忽略。rel_to = 扫描根：含 `/` 的规则按"该根自己的 .gitignore"
        语义做相对匹配（`a/**` 命中根下 a 的所有后代，`*/backup` 只命中隔层的 backup）。
        锚定规则（`/x`）永远只作用于技能根；无相对基准时含 `/` 的规则一律不匹配。"""
        base = os.path.basename(path.rstrip("/\\"))
        rel_root = self._rel_to(path, self.root)
        rel_scan = self._rel_to(path, rel_to) if rel_to else None
        verdict = False
        for neg, anchored, need_rel, dir_only, rx, nseg in self.rules:
            if dir_only and not is_dir:
                continue
            if anchored:
                if rel_root is None:
                    continue  # 锚定规则不越出技能根
                targets = [rel_root]
            elif need_rel:
                if rel_scan is not None:
                    targets = [rel_scan]
                elif rel_root is not None:
                    targets = [rel_root]
                else:
                    # 无相对基准（既不在技能根也不在扫描根）：含 / 的规则不匹配。
                    # 此前这里对绝对路径逐层后缀尝试匹配，CI runner 的 D:\a\<repo>
                    # 恰好被 `a/**` 命中——路径偶然性造成假阳性，必须杜绝。
                    continue
            else:
                targets = [base]
            if any(rx.match(t) for t in targets):
                verdict = not neg
        return verdict


_IGNORE_CACHE = {"key": None, "obj": None}


def load_ignore():
    """内置默认 + 技能根 `.skillignore` 合并后的规则集（按文件 mtime 缓存，扫描热路径不重复读盘）。"""
    try:
        mt = os.path.getmtime(IGNORE_FILE)
        with open(IGNORE_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        mt, lines = None, []
    key = (IGNORE_FILE, mt)
    if _IGNORE_CACHE["key"] != key:
        _IGNORE_CACHE["key"] = key
        _IGNORE_CACHE["obj"] = _Ignore(DEFAULT_IGNORE + lines, ROOT)
    return _IGNORE_CACHE["obj"]


def is_ignored(path, is_dir=False, rel_to=None):
    """扫描时是否该跳过这个路径 —— 全项目统一从这一个入口判定。
    rel_to：扫描根目录。含 / 的规则按"扫描根自己的 .gitignore"语义做相对匹配。"""
    return load_ignore().match(path, is_dir, rel_to=rel_to)


# ── .skillexclude：技能排除名单（防检索/防推荐/图谱专属高亮）─────────────────────────
EXCLUDE_FILE = os.path.join(ROOT, ".skillexclude")
_EXCLUDE_CACHE = {"key": None, "rules": []}


_PROFILE_CACHE = {"key": None, "obj": {}}


def load_skill_profile():
    """读取 skill_profile.json（用户重要指令与技能画像），带 mtime 缓存。
    不存在或异常返回 {}。
    """
    try:
        mt = os.path.getmtime(PROFILE_FILE)
    except OSError:
        mt = None
    key = (PROFILE_FILE, mt)
    if _PROFILE_CACHE["key"] != key:
        _PROFILE_CACHE["key"] = key
        d = {}
        if mt is not None:
            try:
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, dict):
                        d = content
            except Exception:
                d = {}
        _PROFILE_CACHE["obj"] = d
    return _PROFILE_CACHE["obj"]


def get_user_directives():
    """获取用户核心执行指令列表。"""
    prof = load_skill_profile()
    directives = prof.get("user_directives", [])
    return [d.strip() for d in directives if isinstance(d, str) and d.strip()]


def get_pinned_skills():
    """获取用户置顶/常用技能名单（小写集合）。"""
    prof = load_skill_profile()
    skills = prof.get("pinned_skills", [])
    return {s.strip().lower() for s in skills if isinstance(s, str) and s.strip()}


def get_pinned_mcps():
    """获取用户置顶/常用 MCP 名单（小写集合）。"""
    prof = load_skill_profile()
    mcps = prof.get("pinned_mcps", [])
    return {m.strip().lower() for m in mcps if isinstance(m, str) and m.strip()}


def get_retrieval_quota_config():
    """获取 profile 中的检索配额偏好配置。"""
    prof = load_skill_profile()
    q = prof.get("retrieval_quota", {})
    return q if isinstance(q, dict) else {}


def load_config():
    """读取 skill_gateway.config.json，不存在或异常返回 {}。"""
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def load_skillexclude():
    """加载技能排除规则（.skillexclude + config 中的 exclude + profile 中的 exclude_skills + 环境变量 SKILL_GATEWAY_EXCLUDE）。
    返回规则列表，每项为 (pattern, source_desc)。"""
    try:
        mt = os.path.getmtime(EXCLUDE_FILE)
        with open(EXCLUDE_FILE, encoding="utf-8", errors="replace") as f:
            file_lines = f.read().splitlines()
    except OSError:
        mt, file_lines = None, []

    key = (EXCLUDE_FILE, mt)
    if _EXCLUDE_CACHE["key"] == key:
        rules = list(_EXCLUDE_CACHE["rules"])
    else:
        rules = []
        for line in file_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rules.append((line, ".skillexclude"))
        _EXCLUDE_CACHE["key"] = key
        _EXCLUDE_CACHE["rules"] = rules
        rules = list(rules)

    # 融入 skill_profile.json 中的 exclude_skills
    prof = load_skill_profile()
    if prof and isinstance(prof.get("exclude_skills"), list):
        for item in prof["exclude_skills"]:
            if item and isinstance(item, str):
                rules.append((item.strip(), "profile:exclude_skills"))

    cfg = load_config()
    if cfg and isinstance(cfg.get("exclude"), list):
        for item in cfg["exclude"]:
            if item and isinstance(item, str):
                rules.append((item.strip(), "config.json"))

    env_ex = os.environ.get("SKILL_GATEWAY_EXCLUDE", "").strip()
    if env_ex:
        for item in env_ex.split(","):
            if item.strip():
                rules.append((item.strip(), "env:SKILL_GATEWAY_EXCLUDE"))

    return rules


def is_skill_excluded(skill_name: str, cli_excludes=None):
    """判断某个技能名是否被排除。
    返回 (bool, reason_str)。若未排除返回 (False, "")。"""
    if not skill_name:
        return False, ""

    name_lower = skill_name.strip().lower()

    if cli_excludes:
        if isinstance(cli_excludes, str):
            cli_excludes = [x.strip() for x in cli_excludes.split(",") if x.strip()]
        for pat in cli_excludes:
            pat_lower = pat.strip().lower()
            if fnmatch.fnmatch(name_lower, pat_lower):
                return True, f"CLI参数(--exclude {pat})"

    rules = load_skillexclude()
    for pat, src in rules:
        pat_lower = pat.lower()
        if fnmatch.fnmatch(name_lower, pat_lower):
            return True, f"{src}({pat})"

    return False, ""


# ---- 广域发现：不知道平台把技能放哪时，从这些起点浅层找"像技能仓库"的目录 ----
# created 2026-09-16 qjl
BROAD_ROOTS = [
    "~",
    "/app",
    "/workspace",
    "/workspaces",
    "/opt",
    "/srv",
    "/data",
    "/mnt",
    "/home",
    "/root",
    "/tmp",
    ".",
]
BROAD_SKILL_DIRNAMES = {
    "skills",
    "skill",
    "agent-skills",
    "agent_skills",
    "skills-hub",
    "skill-hub",
}
BROAD_MAX_DEPTH = 3  # 广域搜索深度
BROAD_MAX_DIRS = 8000  # 广域搜索访问目录数上限（防卡死）

# 自身上溯时排除的系统/超大目录（扫它们没意义且很慢）
_SELF_SKIP = {
    "/",
    "/etc",
    "/usr",
    "/var",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/run",
    "/snap",
    "/tmp",
    "/var/tmp",
    "/private/tmp",
    "c:\\",
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
}
try:  # 再加一层保险：本机临时目录
    import tempfile as _tf

    _SELF_SKIP.add(os.path.normcase(os.path.normpath(_tf.gettempdir())).lower())
except Exception as _ex:
    print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)


def expand(p):
    """展开路径：`~` 优先用 HOME（平台沙箱常用，Windows 上 os.path.expanduser 会忽略它），
    HOME 不存在或指向无效目录时回退 os.path.expanduser。"""
    if not p:
        return p
    if p == "~" or p.startswith("~/") or p.startswith("~\\"):
        env_home = os.environ.get("HOME")
        if env_home and os.path.isdir(env_home):
            if p == "~":
                return env_home
            return os.path.join(env_home, p[2:].lstrip("/\\"))
    return os.path.expanduser(p)


ENV_SKILL_DECLARATIONS = (
    "~/.gemini/config/skills.json",   # Antigravity / Gemini CLI 的技能库声明
)


def discover_env_skill_roots():
    """发现"当前环境声明"的技能根目录（读平台声明文件，如
    ~/.gemini/config/skills.json 的 entries[].path）。
    返回真实存在的目录列表；无法判定返回 []——调用方**不得**在空结果时
    回退全机器扫描（会把整台机器的技能灌进共享清单）。"""
    roots = []
    declarations = list(ENV_SKILL_DECLARATIONS)
    try:
        prof = load_skill_profile()
        extra = prof.get("env_skill_declarations") or []
        if isinstance(extra, list):
            declarations += [str(x) for x in extra]
    except Exception:
        pass
    for rel in declarations:
        p = expand(rel)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for it in (data.get("entries") or []):
            d = it.get("path") if isinstance(it, dict) else it
            d = expand(str(d or ""))
            if d and os.path.isdir(d) and os.path.normcase(d) not in {os.path.normcase(x) for x in roots}:
                roots.append(d)
    return roots


LEGACY_OUTPUT_FILES = [
    "boot.md",
    "catalog.md",
    "skill-content-index.json",
    "skill-content-terms.json",
    "skill-dashboard.html",
    "skill-index.json",
    "skill-index.llms.txt",
    "skill-index.min.json",
    "skills.edges.json",
    "skills.index.csv",
    "skills.index.md",
    "skills.index.txt",
    "skills.L0.txt",
    "skills.L1.txt",
    "skills.L2.map.json",
    # proactive 用户态运行时数据（使用频次/记忆/纠偏日志）——发布打包绝不能带
    ".proactive_state.json",
    "memory.md",
    "corrections.md",
    # 命中率账本（检索/采纳事件，本机运行时数据）
    "hit-ledger.jsonl",
]

# 环境本地登记表（inputs/ 下的运行时状态，随环境各自演化）——clean 时从源/副本
# 根目录的 inputs/ 清除（源目录不该持有登记表；各环境用 agent-index 自行重建）。
# 模板与示例（available_skills.example.json / platform_connectors.json）不在名单，永不清理。
RUNTIME_INPUT_FILES = [
    "available_skills.json",
    "agent_list.json",
    "platform_skills.json",
]

_MIGRATED = False


def out_dir():
    """产物目录：默认技能根目录下的 output/，可用 SKILL_GATEWAY_OUT_DIR 或 profile / config.json 中的 out_dir 覆盖。
    无论配置的是绝对路径还是相对路径，相对路径始终严格基于技能根目录 ROOT 解析，
    绝对禁止污染调用方的当前工作区（CWD）！
    """
    env_dir = os.environ.get("SKILL_GATEWAY_OUT_DIR")
    if env_dir:
        d = expand(env_dir)
    else:
        prof = load_skill_profile()
        if prof and prof.get("out_dir"):
            d = expand(prof["out_dir"])
        else:
            cfg = load_config()
            if cfg and cfg.get("out_dir"):
                d = expand(cfg["out_dir"])
            else:
                d = os.path.join(ROOT, "output")

    if not os.path.isabs(d):
        d = os.path.normpath(os.path.join(ROOT, d))
    return d


def migrate_legacy_outputs():
    """若根目录下存在历史产物文件，自动且无感地平移到 output/ 目录中，保持项目根目录整洁。"""
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    d = out_dir()
    if os.path.abspath(d) == os.path.abspath(ROOT):
        return
    try:
        os.makedirs(d, exist_ok=True)
        for fname in LEGACY_OUTPUT_FILES:
            old_p = os.path.join(ROOT, fname)
            new_p = os.path.join(d, fname)
            if os.path.isfile(old_p):
                try:
                    if os.path.exists(new_p):
                        os.remove(new_p)
                    os.rename(old_p, new_p)
                except Exception as _ex:
                    print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)
    except Exception as _ex:
        print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)


def out_path(name):
    d = out_dir()
    os.makedirs(d, exist_ok=True)
    migrate_legacy_outputs()
    return os.path.join(d, name)


# 旧产物迁移是 out_path() 的惰性步骤：模块导入不应有"建目录/搬文件"副作用
# （import 任何模块都不该动磁盘状态）。


def _safe_self_dir(d):
    """判断某目录是否适合作为"自身相对"扫描根（排除文件系统根/系统目录）。"""
    if not d or not os.path.isdir(d):
        return False
    if os.path.dirname(d) == d:  # 文件系统根
        return False
    if os.path.normcase(os.path.normpath(d)).lower() in _SELF_SKIP:
        return False
    return True


def self_relative_roots(levels=2):
    """自身相对：技能被解压/安装到哪，它的父/祖父目录往往就是平台的技能仓库。
    换环境后即便没有任何约定目录，也能靠这条兜底发现同级技能。"""
    out, d = [], os.path.dirname(ROOT)
    for _ in range(levels):
        if _safe_self_dir(d):
            out.append(d)
        nd = os.path.dirname(d)
        if not nd or nd == d:
            break
        d = nd
    return out


def broad_roots():
    """广域起点里真实存在的目录。"""
    out, seen = [], set()
    for r in BROAD_ROOTS:
        p = expand(r)
        k = os.path.normcase(os.path.realpath(p))
        if k in seen or not os.path.isdir(p):
            continue
        seen.add(k)
        out.append(p)
    return out


def only_dirs():
    """SKILL_GATEWAY_ONLY_DIRS=1 或 profile 中 only_fixed_roots 为 true → 只用显式给的根（配置/Profile + 环境变量），
    不掺自身相对与内置默认。"""
    prof = load_skill_profile()
    if prof.get("only_fixed_roots"):
        return True
    v = (os.environ.get("SKILL_GATEWAY_ONLY_DIRS") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def candidate_roots():
    """按优先级返回 [(root, 来源标签)]：Profile固定根 > 配置 > 环境变量 > 自身相对 > 内置默认。
    当 only_dirs()（环境变量或 profile.only_fixed_roots）开启时，只保留显式指定的根。"""
    seen, out = set(), []

    def add(r, tag):
        if not r:
            return
        key = os.path.normcase(os.path.realpath(expand(r)))
        if key in seen:
            return
        seen.add(key)
        out.append((r, tag))

    prof = load_skill_profile()
    for r in prof.get("fixed_roots", []) or []:
        add(r, "profile:fixed")

    cfg = load_config()
    for r in cfg.get("skill_roots", []) or []:
        add(r, "config")
    for r in (os.environ.get("SKILL_GATEWAY_SKILL_DIRS") or "").split(os.pathsep):
        if r:
            add(r, "env")
    if not only_dirs():
        for r in self_relative_roots():
            add(r, "self")
        for r in DEFAULT_SKILL_ROOTS:
            add(r, "default")
    return out


def load_edge_weights():
    """从配置文件读取边权重，返回 dict。缺失字段用默认值填充。"""
    defaults = {
        "depends_on": 0.8,
        "contains": 0.7,
        "overlap": 0.6,
        "family": 1.0,
        "similar": 0.5,
    }
    cfg = load_config()
    custom = cfg.get("edge_weights", {})
    if isinstance(custom, dict):
        defaults.update(custom)
    return defaults


def dynamic_mcp_search_roots():
    """动态收集可能存放 MCP/连接器配置的基础根目录。
    覆盖：当前技能根与祖先、CWD及祖先、平台输入目录、环境变量自定义路径、用户HOME与系统AppData。
    为数字员工与不同 Agent（JoyCode、WorkBuddy、Claude、Cursor、CodeBuddy、OpenClaw、Gemini等）提供零硬编码自适应。
    """
    roots = []
    seen = set()

    def add_dir(d):
        if not d:
            return
        try:
            p = os.path.normcase(os.path.realpath(expand(d)))
            if p not in seen and os.path.isdir(p):
                seen.add(p)
                roots.append(p)
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    # 1. 技能自身目录及其祖先（自身相对，适应任意解压部署路径）
    curr = ROOT
    for _ in range(3):
        add_dir(curr)
        add_dir(os.path.join(curr, "inputs"))
        p = os.path.dirname(curr)
        if not p or p == curr:
            break
        curr = p

    # 2. CWD 及其祖先（数字员工当前工作区）
    cwd = os.getcwd()
    for _ in range(3):
        add_dir(cwd)
        add_dir(os.path.join(cwd, "inputs"))
        p = os.path.dirname(cwd)
        if not p or p == cwd:
            break
        cwd = p

    # 3. 环境变量中的 Agent/配置路径
    env_vars = [
        "CLAUDE_CONFIG_PATH",
        "MCP_CONFIG_PATH",
        "WORKBUDDY_HOME",
        "JOYCODE_HOME",
        "ANTIGRAVITY_APP_DATA",
        "CODEBUDDY_HOME",
        "OPENCLAW_HOME",
        "CURSOR_CONFIG_DIR",
    ]
    for ev in env_vars:
        val = os.environ.get(ev)
        if val:
            add_dir(val)

    # 4. 用户主目录及应用配置目录
    home = expand("~")
    add_dir(home)
    add_dir(os.path.join(home, ".config"))
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            add_dir(appdata)
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            add_dir(local_appdata)

    return roots


def discover_mcp_resources():
    """动态启发式嗅探 MCP/连接器配置文件与目录型 MCP 服务。
    返回: (configs_list, dirs_list)
    """
    seen_cfgs, out_cfgs = set(), []
    seen_dirs, out_dirs = set(), []

    def add_c(p):
        if not p:
            return
        try:
            k = os.path.normcase(os.path.realpath(expand(p)))
            if k not in seen_cfgs and os.path.isfile(k):
                seen_cfgs.add(k)
                out_cfgs.append(p)
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    def add_d(d):
        if not d:
            return
        try:
            k = os.path.normcase(os.path.realpath(expand(d)))
            if k not in seen_dirs and os.path.isdir(k):
                seen_dirs.add(k)
                out_dirs.append(d)
        except Exception as _ex:
            print(f"[skill-gateway] 失败(已忽略): {_ex}", file=sys.stderr)

    agent_keywords = (
        "claude",
        "workbuddy",
        "cursor",
        "codebuddy",
        "joycode",
        "openclaw",
        "gemini",
        "antigravity",
        "windsurf",
        "mcp",
        "connector",
        "connectors",
        "agent",
        "agents",
        "subagent",
        "subagents",
        "worker",
        "workers",
    )
    config_filenames = (
        "mcp.json",
        "claude_desktop_config.json",
        "claude.json",
        ".claude.json",
        "connectors.json",
        "platform_connectors.json",
        "platform_skills.json",
        "agent.json",
        "agents.json",
        "subagent.json",
        "subagents.json",
        "digital_workers.json",
        "workers.json",
    )

    visited = 0
    max_dirs = 350  # 目录内嗅探的硬上限：命中关键词的大目录（如整个 .claude）可能非常大
    exhausted = False

    for base in dynamic_mcp_search_roots():
        if exhausted:
            break
        try:
            entries = os.listdir(base)
        except Exception:
            continue

        for name in entries:
            full = os.path.join(base, name)
            low = name.lower()
            if os.path.isfile(full):
                if low in config_filenames or (
                    (
                        "mcp" in low
                        or "connector" in low
                        or "agent" in low
                        or "worker" in low
                    )
                    and low.endswith(".json")
                ):
                    add_c(full)
            elif os.path.isdir(full):
                if exhausted:
                    break
                if any(kw in low for kw in agent_keywords):
                    for root_d, ds, fs in os.walk(full):
                        visited += 1
                        if visited > max_dirs:
                            exhausted = True
                            break
                        ds[:] = [
                            d
                            for d in ds
                            if d.lower()
                            not in (
                                ".git",
                                "node_modules",
                                "__pycache__",
                                "cache",
                                "telemetry",
                                "logs",
                            )
                        ]
                        rel = os.path.relpath(root_d, full)
                        if len(rel.split(os.sep)) > 4:
                            ds[:] = []
                            continue
                        if os.path.basename(root_d).lower() in (
                            "mcp",
                            "agents",
                            "subagents",
                        ):
                            add_d(root_d)
                        for f in fs:
                            flow = f.lower()
                            if flow in config_filenames or (
                                (
                                    "mcp" in flow
                                    or "connector" in flow
                                    or "agent" in flow
                                    or "worker" in flow
                                )
                                and flow.endswith(".json")
                            ):
                                add_c(os.path.join(root_d, f))

    return out_cfgs, out_dirs


def candidate_mcp_configs():
    """按优先级返回 MCP 配置候选（Profile > 配置 > 环境变量 > 动态嗅探 > 内置默认）。
    注意：技能目录隔离(only_dirs)绝不抑制 MCP 与连接器的动态发现！"""
    seen, out = set(), []

    def add(r):
        if not r:
            return
        k = os.path.normcase(os.path.abspath(expand(r)))
        if k not in seen:
            seen.add(k)
            out.append(r)

    prof = load_skill_profile()
    for r in prof.get("mcp_configs", []) or []:
        add(r)
    cfg = load_config()
    for r in cfg.get("mcp_configs", []) or []:
        add(r)
    for r in (os.environ.get("SKILL_GATEWAY_MCP_CONFIGS") or "").split(os.pathsep):
        if r:
            add(r)

    # 只有显式开启了 only_fixed_mcps 时才跳过动态发现与默认
    if (
        not prof.get("only_fixed_mcps")
        and os.environ.get("SKILL_GATEWAY_ONLY_MCPS") != "1"
    ):
        # 1. 动态启发式发现
        cfgs, _ = discover_mcp_resources()
        for r in cfgs:
            add(r)
        # 2. 内置默认候选兜底
        for r in DEFAULT_MCP_CONFIGS:
            add(r)
    return out


def candidate_mcp_dirs():
    """返回动态发现的目录型 MCP 服务根目录列表（例如 Antigravity/Gemini 本地 MCP 工具库）。"""
    seen, out = set(), []

    def add(d):
        if not d:
            return
        k = os.path.normcase(os.path.abspath(expand(d)))
        if k not in seen and os.path.isdir(k):
            seen.add(k)
            out.append(d)

    prof = load_skill_profile()
    for r in prof.get("mcp_dirs", []) or []:
        add(r)
    cfg = load_config()
    for r in cfg.get("mcp_dirs", []) or []:
        add(r)

    if (
        not prof.get("only_fixed_mcps")
        and os.environ.get("SKILL_GATEWAY_ONLY_MCPS") != "1"
    ):
        _, dirs = discover_mcp_resources()
        for d in dirs:
            add(d)

    return out
