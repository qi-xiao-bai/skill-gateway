#!/usr/bin/env python3
# content_index.py - 内容索引：技能文件分块 + 倒排 + IDF 加权检索
# created 2026-09-22 qjl
import json
import os
import re
import time

import index_store
import retrieval
import scanner
from paths import ROOT, is_ignored, out_path

# ── 常量 ────────────────────────────────────────────────────────────────────────
# v2 起的变更：① chunk 可携带 src_dir/src_file（跨技能投喂的正文才能真正读出并索引）；
# ② terms_cache 并入主文档（单文件原子写，消除双文件错位）；③ 索引键改用 frontmatter 名。
CONTENT_INDEX_VER = "skill-content-index/v2"
MAX_FILE_BYTES = 500_000
MAX_CHUNKS_PER_SKILL = 200
CHUNK_WINDOW_LINES = 50
CHUNK_OVERLAP_LINES = 5
CHUNK_MIN_LINES = 5
SUMMARY_MAX_LEN = 80
BINARY_EXTENSIONS = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".exe",
        ".bin",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".db",
        ".sqlite",
        ".class",
        ".jar",
        ".wasm",
    }
)

# references/ 目录下可投喂的文件扩展名
FEED_REF_EXTENSIONS = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".sh",
        ".bat",
    }
)
# references/ 目录下每个依赖技能最多投喂的文件数
MAX_REF_FILES_PER_DEP = 5


# ── 二进制检测 ──────────────────────────────────────────────────────────────────
def _is_binary_file(path):
    """扩展名在 BINARY_EXTENSIONS → True；读前 8KB 检测 NUL 字节 → True；否则 False。"""
    _, ext = os.path.splitext(path)
    if ext.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return False


# ── 文件遍历 ────────────────────────────────────────────────────────────────────
def iter_content_files(skill_dir):
    """遍历 skill_dir 下所有可索引文件，跳过 is_ignored / 二进制 / 超限文件。
    返回 [{"rel": 相对路径, "abs": 绝对路径, "mtime": int, "size": int}]"""
    skill_dir = os.path.abspath(skill_dir)
    out = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [
            d
            for d in dirnames
            if not is_ignored(os.path.join(dirpath, d), True, rel_to=skill_dir)
        ]
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            if is_ignored(abs_path, False, rel_to=skill_dir):
                continue
            if _is_binary_file(abs_path):
                continue
            try:
                st = os.stat(abs_path)
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            rel = os.path.relpath(abs_path, skill_dir).replace(os.sep, "/")
            out.append(
                {
                    "rel": rel,
                    "abs": abs_path,
                    "mtime": int(st.st_mtime),
                    "size": st.st_size,
                }
            )
    return out


# ── 文件读取 ────────────────────────────────────────────────────────────────────
def _read_file_lines(abs_path):
    """读取文件所有行，utf-8-sig（剥 BOM）回退 latin-1。返回 list[str]（含换行）。"""
    try:
        with open(abs_path, encoding="utf-8-sig") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with open(abs_path, encoding="latin-1") as f:
            return f.readlines()


# ── 分块 ────────────────────────────────────────────────────────────────────────
def _find_code_blocks(lines):
    """找出 Markdown 文件中所有代码块的行范围（0-based 索引）。
    返回 [(start, end), ...]，start 是 ``` 行，end 是闭合 ``` 行。
    未闭合的代码块忽略。"""
    blocks = []
    in_block = False
    block_start = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_block:
                in_block = True
                block_start = i
            else:
                blocks.append((block_start, i))
                in_block = False
                block_start = -1
    return blocks


def _chunk_markdown(lines, rel_path):
    """按 Markdown 标题分段，代码块感知。
    标题文本做摘要（去 # 前缀，截断 SUMMARY_MAX_LEN）。
    首段无标题时摘要取第一非空行。
    < CHUNK_MIN_LINES 的段合并到上一段。
    代码块（```...```）不会被标题切分——如果标题落在代码块内部，
    该标题不作为分段点。
    空文件返回空列表。"""
    code_blocks = _find_code_blocks(lines)
    code_block_set = set()
    for start, end in code_blocks:
        for i in range(start, end + 1):
            code_block_set.add(i)

    heading_re = re.compile(r"^#{1,6}\s")
    headings = []
    for i, line in enumerate(lines):
        if i in code_block_set:
            continue
        m = heading_re.match(line)
        if m:
            text = line[m.end() :].strip()
            headings.append((i, text))

    if not headings:
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return []
        summary = non_empty[0].strip()[:SUMMARY_MAX_LEN]
        return [{"s": 1, "e": len(lines), "summary": summary}]

    segments = []
    first_head_idx = headings[0][0]
    if first_head_idx > 0:
        non_empty = [l for l in lines[:first_head_idx] if l.strip()]
        summary = non_empty[0].strip()[:SUMMARY_MAX_LEN] if non_empty else "(untitled)"
        segments.append({"s": 1, "e": first_head_idx, "summary": summary})

    for seg_i, (start, text) in enumerate(headings):
        if seg_i + 1 < len(headings):
            end = headings[seg_i + 1][0]
        else:
            end = len(lines)
        summary = text[:SUMMARY_MAX_LEN] if text else "(untitled)"
        segments.append({"s": start + 1, "e": end, "summary": summary})

    merged = []
    for seg in segments:
        line_count = seg["e"] - seg["s"] + 1
        if line_count < CHUNK_MIN_LINES and merged:
            merged[-1]["e"] = seg["e"]
        else:
            merged.append(seg)
    return merged if merged else segments


def _chunk_plain(lines, rel_path):
    """按 CHUNK_WINDOW_LINES 行窗口切分，CHUNK_OVERLAP_LINES 行重叠。
    摘要取窗口内第一非空行，截断 SUMMARY_MAX_LEN。空文件返回空列表。"""
    if not lines:
        return []
    total = len(lines)
    step = CHUNK_WINDOW_LINES - CHUNK_OVERLAP_LINES
    if step <= 0:
        step = 1
    chunks = []
    start = 0
    while start < total:
        end = min(start + CHUNK_WINDOW_LINES, total)
        summary = ""
        for i in range(start, end):
            stripped = lines[i].strip()
            if stripped:
                summary = stripped[:SUMMARY_MAX_LEN]
                break
        if not summary:
            summary = f"(empty {rel_path})"
        chunks.append({"s": start + 1, "e": end, "summary": summary})
        if end >= total:
            break
        start += step
    return chunks


def _chunk_python(lines, rel_path):
    """Python 文件按函数/类定义分段（AST 解析）。
    顶层代码归入 '__module__' 段；每个函数/类一个段。
    解析失败时回退到 _chunk_plain。"""
    import ast

    source = "".join(lines)
    try:
        tree = ast.parse(source, filename=rel_path)
    except (SyntaxError, ValueError):
        return _chunk_plain(lines, rel_path)

    segments = []
    definitions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.append((node.lineno, node.end_lineno or node.lineno, node.name))
        elif isinstance(node, ast.Assign):
            definitions.append((node.lineno, node.end_lineno or node.lineno, None))

    if not definitions:
        non_empty = [l for l in lines if l.strip()]
        summary = (
            non_empty[0].strip()[:SUMMARY_MAX_LEN]
            if non_empty
            else f"(empty {rel_path})"
        )
        return [{"s": 1, "e": len(lines), "summary": summary}]

    first_def_line = min(d[0] for d in definitions)
    if first_def_line > 1:
        non_empty = [l for l in lines[: first_def_line - 1] if l.strip()]
        if non_empty:
            summary = non_empty[0].strip()[:SUMMARY_MAX_LEN]
            segments.append({"s": 1, "e": first_def_line - 1, "summary": summary})

    for i, (start, end, name) in enumerate(definitions):
        if i + 1 < len(definitions):
            next_start = definitions[i + 1][0]
            actual_end = min(end, next_start - 1)
        else:
            actual_end = end
        actual_end = min(actual_end, len(lines))
        line_count = actual_end - start + 1
        if line_count < CHUNK_MIN_LINES and segments:
            segments[-1]["e"] = actual_end
        else:
            if name:
                summary = name[:SUMMARY_MAX_LEN]
            else:
                seg_lines = lines[start - 1 : actual_end]
                non_empty = [l for l in seg_lines if l.strip()]
                summary = (
                    non_empty[0].strip()[:SUMMARY_MAX_LEN]
                    if non_empty
                    else f"(assign {rel_path})"
                )
            segments.append({"s": start, "e": actual_end, "summary": summary})

    return (
        segments
        if segments
        else [{"s": 1, "e": len(lines), "summary": f"(module {rel_path})"}]
    )


def chunk_file(abs_path, rel_path):
    """读取文件并分块。.md 后缀走 _chunk_markdown，.py 走 _chunk_python，其余走 _chunk_plain。
    返回 [{"s":1, "e":25, "summary":"..."}, ...]"""
    lines = _read_file_lines(abs_path)
    if not lines:
        return []
    ext = rel_path.lower().rsplit(".", 1)[-1] if "." in rel_path else ""
    if ext == "md":
        return _chunk_markdown(lines, rel_path)
    if ext == "py":
        return _chunk_python(lines, rel_path)
    return _chunk_plain(lines, rel_path)


# ── 倒排索引 ────────────────────────────────────────────────────────────────────
def _rebuild_inverted(chunks, skill_dirs=None, old_terms_cache=None):
    """遍历 chunks，对每个 chunk 的 summary + file + skill 名 + 正文文本用 retrieval.terms() 分词。
    构建 term → [chunk_id] 倒排。
    增强版：正文文本也参与索引，大幅提升检索召回率。
    分词结果缓存到 terms_cache（{chunk_id: [terms]}），主索引 chunks 不含 terms 字段。
    增量优化：传入 old_terms_cache 时，对 (skill, file, mtime) 未变的 chunk 复用旧缓存，
    只重新计算变更 chunk 的 terms，避免全量读文件。
    skill_dirs: {skill_name: skill_dir} 映射，用于读chunk原文；为None时只索引summary+file+skill。
    返回 (inverted, terms_cache) 元组。"""
    inv = {}
    terms_cache = {}  # {"<chunk_id>": [sorted_terms]} —— 键统一用字符串，与 JSON 落盘形态一致
    # 构建旧缓存复用键：(skill, file, mtime) → terms
    old_terms_by_key = {}
    if old_terms_cache:
        for c in chunks:
            cid = c.get("id")
            if cid is not None and str(cid) in old_terms_cache:
                key = (c.get("skill", ""), c.get("file", ""), c.get("mtime", 0))
                old_terms_by_key[key] = old_terms_cache[str(cid)]

    for c in chunks:
        cid = c["id"]
        # 增量优化：检查是否可复用旧 terms 缓存
        key = (c.get("skill", ""), c.get("file", ""), c.get("mtime", 0))
        cached = old_terms_by_key.get(key)
        if cached:
            terms_cache[str(cid)] = cached
            for t in cached:
                inv.setdefault(t, []).append(cid)
            continue

        # 计算分词：summary + file + skill + 正文
        text_parts = [c.get("summary", ""), c.get("file", ""), c.get("skill", "")]
        # 增强索引：读chunk原文分词（需要skill_dirs映射）
        if skill_dirs:
            skill_dir = skill_dirs.get(c.get("skill", ""))
            if skill_dir:
                chunk_text = read_chunk_text(c, skill_dir)
                if chunk_text:
                    text_parts.append(chunk_text)
        text = " ".join(text_parts)
        computed_terms = retrieval.terms(text)
        # 缓存分词结果到 terms_cache
        sorted_terms = sorted(computed_terms)
        terms_cache[str(cid)] = sorted_terms
        for t in computed_terms:
            inv.setdefault(t, []).append(cid)
    return inv, terms_cache


# ── 跨技能预投喂 ──────────────────────────────────────────────────────────────
def _feed_key_chunks_from_file(
    abs_path, rel_label, dep_name, dep_type, skill_name, key_patterns, dep_dir
):
    """从单个文件中提取关键段作为投喂chunk。返回 list[chunk_dict]。
    chunk 携带 src_dir（依赖技能的真实目录）+ src_file（目录内相对路径），
    read_chunk_text 据此读原文——file 字段里的 `@dep` 只是展示标签，不是路径。"""
    try:
        lines = _read_file_lines(abs_path)
    except OSError:
        return []
    if not lines:
        return []

    ext = os.path.splitext(abs_path)[1].lower()
    if ext == ".md":
        chunks = _chunk_markdown(lines, rel_label)
    elif ext == ".py":
        chunks = _chunk_python(lines, rel_label)
    else:
        chunks = _chunk_plain(lines, rel_label)

    fed = []
    for ch in chunks:
        s = max(ch["s"] - 1, 0)
        e = min(ch["e"], len(lines))
        chunk_text = "".join(lines[s:e])
        if not key_patterns.search(chunk_text):
            continue
        fed.append(
            {
                "skill": skill_name,
                "file": f"{rel_label}@{dep_name}",
                "src_dir": dep_dir,
                "src_file": rel_label,
                "s": ch["s"],
                "e": ch["e"],
                "summary": f"[{dep_type}:{dep_name}] {ch['summary']}",
                "fed_from": dep_name,
                "mtime": 0,
            }
        )
    return fed


def _build_cross_feed_chunks(entries, skill_dirs, name_to_entry):
    """跨技能预投喂：把 depends_on/contains 关联技能的 SKILL.md + references/ 关键段
    复制到当前技能名下，标记 fed_from 来源。

    核心思路：当技能A depends_on 技能B 时，把B的SKILL.md和references/中包含
    触发词/用法说明的关键chunk也索引到A的名下。
    这样查A时就能直接搜到B的关键信息，不用再解锁B。

    投喂范围：SKILL.md 全部关键段 + references/ 下关键文件的关键段。
    关键段 = 包含触发词/When to Use/Usage/用法/触发等关键词的段。
    """
    edges = index_store.read_edges() or []
    if not edges:
        return []

    # 构建技能名→ID映射（包含 skill 和 mcp，MCP 也可被依赖）
    name_to_id = {}
    id_to_name = {}
    for e in entries:
        eid = retrieval.entry_id(e)
        name_to_id[e["name"].lower()] = eid
        id_to_name[eid] = e["name"]

    # 收集每个技能的关联技能（depends_on 和 contains 的 target/source）
    skill_deps = {}
    for ed in edges:
        etype = ed.get("type", "")
        if etype not in ("depends_on", "contains"):
            continue
        src_id = ed.get("source", "")
        tgt_id = ed.get("target", "")
        src_name = id_to_name.get(src_id)
        tgt_name = id_to_name.get(tgt_id)
        if not src_name or not tgt_name:
            continue
        if etype == "depends_on":
            skill_deps.setdefault(src_name, set()).add((tgt_name, "depends_on"))
        if etype == "contains":
            skill_deps.setdefault(src_name, set()).add((tgt_name, "contains"))
            skill_deps.setdefault(tgt_name, set()).add((src_name, "contains"))

    if not skill_deps:
        return []

    # 关键段过滤关键词：包含这些词的段被认为是"关键信息段"
    KEY_PATTERNS = re.compile(
        r"(?i)(trigger|when to use|usage|用法|触发|何时|how to|quick|start|get started|入门|快速|前提|prerequisite|require|依赖|install|安装|配置|config|example|示例|注意|caution|warning|important|限制|limitation|constraint|api|接口|参数|parameter|argument|返回|return|类型|type|class|function|方法|method|属性|property|字段|field)"
    )

    fed_chunks = []
    fed_count_per_skill = {}
    MAX_FED_PER_SKILL = 50  # 扩展后增加配额

    for skill_name, deps in skill_deps.items():
        if skill_name not in skill_dirs:
            continue
        fed_count = fed_count_per_skill.get(skill_name, 0)
        if fed_count >= MAX_FED_PER_SKILL:
            continue

        for dep_name, dep_type in deps:
            dep_entry = name_to_entry.get(dep_name)
            dep_dir = skill_dirs.get(dep_name)

            # 情况1：依赖目标是 MCP（没有 SKILL.md，用 description 投喂）
            if dep_entry and dep_entry.get("type") == "mcp":
                dep_desc = dep_entry.get("description", "")
                if dep_desc:
                    fed_chunks.append(
                        {
                            "skill": skill_name,
                            "file": f"MCP@{dep_name}",
                            "s": 1,
                            "e": 1,
                            "summary": f"[{dep_type}:{dep_name}] {dep_desc[:SUMMARY_MAX_LEN]}",
                            "fed_from": dep_name,
                            "mtime": 0,
                        }
                    )
                    fed_count += 1
                    fed_count_per_skill[skill_name] = fed_count
                continue

            # 情况2：依赖目标是 skill，投喂 SKILL.md + references/ 的关键段
            if not dep_dir:
                continue

            # 2a: 投喂 SKILL.md 的关键段
            skill_md_path = os.path.join(dep_dir, "SKILL.md")
            if os.path.isfile(skill_md_path):
                sk_fed = _feed_key_chunks_from_file(
                    skill_md_path,
                    "SKILL.md",
                    dep_name,
                    dep_type,
                    skill_name,
                    KEY_PATTERNS,
                    dep_dir,
                )
                for fc in sk_fed:
                    if fed_count >= MAX_FED_PER_SKILL:
                        break
                    fed_chunks.append(fc)
                    fed_count += 1
                fed_count_per_skill[skill_name] = fed_count

            # 2b: 投喂 references/ 目录下关键文件的关键段
            ref_dir = os.path.join(dep_dir, "references")
            if os.path.isdir(ref_dir) and fed_count < MAX_FED_PER_SKILL:
                ref_files = []
                for fn in os.listdir(ref_dir):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in FEED_REF_EXTENSIONS:
                        abs_path = os.path.join(ref_dir, fn)
                        if os.path.isfile(abs_path):
                            try:
                                st = os.stat(abs_path)
                                if st.st_size <= MAX_FILE_BYTES:
                                    ref_files.append((fn, abs_path))
                            except OSError:
                                pass
                ref_files.sort()
                ref_count = 0
                for fn, abs_path in ref_files:
                    if ref_count >= MAX_REF_FILES_PER_DEP:
                        break
                    if fed_count >= MAX_FED_PER_SKILL:
                        break
                    ref_fed = _feed_key_chunks_from_file(
                        abs_path,
                        f"references/{fn}",
                        dep_name,
                        dep_type,
                        skill_name,
                        KEY_PATTERNS,
                        dep_dir,
                    )
                    for fc in ref_fed:
                        if fed_count >= MAX_FED_PER_SKILL:
                            break
                        fed_chunks.append(fc)
                        fed_count += 1
                    fed_count_per_skill[skill_name] = fed_count
                    ref_count += 1

    return fed_chunks


# ── 去重 ──────────────────────────────────────────────────────────────────────
def _dedup_chunks(chunks):
    """按 (skill, file, s, e) 去重，保留首次出现的chunk。
    返回去重后的chunk列表。"""
    seen = set()
    result = []
    for c in chunks:
        key = (c.get("skill", ""), c.get("file", ""), c.get("s", 0), c.get("e", 0))
        if key in seen:
            continue
        seen.add(key)
        result.append(c)
    return result


# ── 写入/读取 ────────────────────────────────────────────────────────────────
def write_content_index(doc):
    """原子写入主文档（含内嵌 terms_cache）。terms 与 chunks 同文件落盘，
    不可能再出现"索引写成功、terms 写失败"导致的 chunk_id 静默错位。"""
    p = out_path("skill-content-index.json")
    index_store.atomic_write(p, json.dumps(doc, ensure_ascii=False, separators=(",", ":")))


def read_content_index():
    """读主文档；不存在/损坏/**版本不符**返回 None（触发上层全量重建）。
    兼容 v1：旧版是双文件布局且 chunk 无 src_dir，terms 对不上号，
    一律视同不存在。"""
    p = out_path("skill-content-index.json")
    if not os.path.isfile(p):
        legacy_p = os.path.join(ROOT, "skill-content-index.json")
        if os.path.isfile(legacy_p):
            p = legacy_p
        else:
            return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("version") != CONTENT_INDEX_VER:
        return None
    return d


def read_terms_cache(doc=None):
    """terms 缓存读取：优先取主文档内嵌的 terms_cache；doc 未传时兼容读旧版
    独立文件 skill-content-terms.json（v2 起不再单独落盘）。"""
    if doc is not None and isinstance(doc.get("terms_cache"), dict):
        return doc["terms_cache"]
    p = out_path("skill-content-terms.json")
    if not os.path.isfile(p):
        legacy_p = os.path.join(ROOT, "skill-content-terms.json")
        if not os.path.isfile(legacy_p):
            return {}
        p = legacy_p
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_content_index():
    """读索引；不存在则自动全量构建。"""
    doc = read_content_index()
    if doc is None:
        doc = build_content_index()
    return doc


# ── 收集（全量/增量共用） ────────────────────────────────────────────────────────
def _collect_chunks(broad, entries, old_doc=None, stats=None):
    """遍历技能目录收集 chunks —— build 与 update 的唯一实现（此前两份 80% 复制）。
    old_doc 提供时走增量：mtime 未变的文件直接复用旧 chunks。
    索引键用条目的 frontmatter 名（按真实路径匹配条目），与条目索引/图谱边对齐；
    无条目的目录才退回目录名；不同目录解析出同名条目时保留先见（与 scan_skills 一致）。
    返回 (all_chunks, file_mtimes, total_bytes, skill_dirs, fed_chunks)。"""
    name_to_entry = {}
    path_to_entry = {}
    for e in entries:
        name_to_entry.setdefault(e["name"], e)
        if e.get("path"):
            path_to_entry[os.path.normcase(os.path.realpath(e["path"]))] = e

    old_by_sf = {}
    old_mtimes = {}
    old_skill_names = set()
    if old_doc is not None:
        old_mtimes = old_doc.get("file_mtimes", {})
        old_skill_names = set(old_mtimes.keys())
        for c in old_doc.get("chunks", []):
            old_by_sf.setdefault((c["skill"], c["file"]), []).append(c)

    all_chunks = []
    file_mtimes = {}
    total_bytes = 0
    skill_dirs = {}
    current_skill_names = set()

    seen_dirs = set()
    for root, sk_path in scanner.enumerate_skill_md(broad):
        skill_dir = os.path.dirname(sk_path)
        rp = os.path.normcase(os.path.realpath(skill_dir))
        if rp in seen_dirs:
            continue
        seen_dirs.add(rp)

        entry = path_to_entry.get(rp)
        if entry is not None:
            sk_file = index_store.skill_file(entry)
            if not entry.get("path") or index_store.is_stub_file(sk_file):
                continue

        skill_name = entry["name"] if entry else os.path.basename(skill_dir)
        if skill_name in skill_dirs:
            continue
        current_skill_names.add(skill_name)
        skill_dirs[skill_name] = skill_dir

        files = iter_content_files(skill_dir)
        skill_chunks_count = 0
        skill_mtimes = {}
        for fi in files:
            rel = fi["rel"]
            old_mt = old_mtimes.get(skill_name, {}).get(rel)

            if old_doc is not None and old_mt is not None and old_mt == fi["mtime"]:
                reused = old_by_sf.get((skill_name, rel), [])
                for c in reused:
                    if skill_chunks_count >= MAX_CHUNKS_PER_SKILL:
                        break
                    all_chunks.append(c)
                    skill_chunks_count += 1
                if stats is not None:
                    stats["kept"] += 1
            else:
                chunks = chunk_file(fi["abs"], rel)
                for ch in chunks:
                    if skill_chunks_count >= MAX_CHUNKS_PER_SKILL:
                        break
                    all_chunks.append(
                        {
                            "skill": skill_name,
                            "file": rel,
                            "s": ch["s"],
                            "e": ch["e"],
                            "summary": ch["summary"],
                            "mtime": fi["mtime"],
                        }
                    )
                    skill_chunks_count += 1
                if stats is not None and old_doc is not None:
                    stats["updated" if old_mt is not None else "added"] += 1

            skill_mtimes[rel] = fi["mtime"]
            total_bytes += fi["size"]
            if skill_chunks_count >= MAX_CHUNKS_PER_SKILL:
                break
        if skill_mtimes:
            file_mtimes[skill_name] = skill_mtimes

    if old_doc is not None and stats is not None:
        stats["removed"] = len(old_skill_names - current_skill_names)
        for sn in current_skill_names & old_skill_names:
            old_files = set(old_mtimes.get(sn, {}).keys())
            cur_files = set(file_mtimes.get(sn, {}).keys())
            stats["removed"] += len(old_files - cur_files)

    fed_chunks = _build_cross_feed_chunks(entries, skill_dirs, name_to_entry)
    return all_chunks, file_mtimes, total_bytes, skill_dirs, fed_chunks


def _assemble_doc(all_chunks, file_mtimes, total_bytes, fed_chunks, inverted, terms_cache):
    doc = {
        "version": CONTENT_INDEX_VER,
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "skill_count": len(file_mtimes),
        "chunk_count": len(all_chunks),
        "total_bytes": total_bytes,
        "fed_count": len(fed_chunks),
        "chunks": all_chunks,
        "inverted": inverted,
        "terms_cache": terms_cache,
        "file_mtimes": file_mtimes,
    }
    write_content_index(doc)
    return doc


# ── 全量构建 ────────────────────────────────────────────────────────────────────
def build_content_index(broad="auto"):
    """全量构建内容索引：遍历技能 → 分块 → 跨技能预投喂 → 倒排（含正文分词）→ 原子写入。"""
    entries = index_store.read_index() or []
    all_chunks, file_mtimes, total_bytes, skill_dirs, fed_chunks = _collect_chunks(broad, entries)
    all_chunks = _dedup_chunks(all_chunks)
    for i, c in enumerate(all_chunks):
        c["id"] = i
    inverted, terms_cache = _rebuild_inverted(all_chunks, skill_dirs=skill_dirs)
    return _assemble_doc(all_chunks, file_mtimes, total_bytes, fed_chunks, inverted, terms_cache)


# ── 增量更新 ────────────────────────────────────────────────────────────────────
def update_content_index(broad="auto"):
    """增量更新内容索引：mtime 未变的文件复用旧 chunks 与 terms 缓存，
    只重算变更部分；索引缺失/版本不符时转全量。返回 (doc, stats)。"""
    old_doc = read_content_index()
    if old_doc is None:
        doc = build_content_index(broad)
        return doc, {
            "first": True,
            "added": doc["skill_count"],
            "updated": 0,
            "removed": 0,
            "kept": 0,
        }

    stats = {"first": False, "added": 0, "updated": 0, "removed": 0, "kept": 0}
    entries = index_store.read_index() or []
    all_chunks, file_mtimes, total_bytes, skill_dirs, fed_chunks = _collect_chunks(
        broad, entries, old_doc=old_doc, stats=stats
    )
    all_chunks = _dedup_chunks(all_chunks)
    for i, c in enumerate(all_chunks):
        c["id"] = i

    # 增量优化：复用旧 terms 缓存（v2 起内嵌在主文档；旧版独立文件兼容读取），只重算变更 chunk
    old_terms_cache = old_doc.get("terms_cache") or read_terms_cache()
    inverted, terms_cache = _rebuild_inverted(
        all_chunks, skill_dirs=skill_dirs, old_terms_cache=old_terms_cache
    )
    doc = _assemble_doc(all_chunks, file_mtimes, total_bytes, fed_chunks, inverted, terms_cache)
    return doc, stats


# ── 检索 ────────────────────────────────────────────────────────────────────────
def content_search(doc, query, top=10, skill_dirs=None, terms_cache_override=None):
    """内容检索：retrieval.terms(query) 分词 → 倒排召回候选 chunk_ids →
    IDF 加权得分（overlap 做 maximal 去重——与主检索同一口径，中文 gram 不重复撑分）→
    文件名命中 term 加权 +2 → 跨技能投喂 chunk 加权 +0.5 → 按分排序取 top N。
    返回 [(score, chunk_dict, matched_terms), ...]
    fed_from 标记：搜索结果区分"本技能内容"和"关联技能投喂内容"。
    terms 缓存优先取 doc 内嵌的 terms_cache，其次旧版独立文件；可经 terms_cache_override 直接传入。"""
    q_terms = retrieval.terms(query)
    if not q_terms:
        return []

    chunks = doc.get("chunks", [])
    inverted = doc.get("inverted", {})

    # 懒加载 terms 缓存（优先使用传入的覆盖）
    terms_cache = (
        terms_cache_override if terms_cache_override is not None else read_terms_cache(doc)
    )

    cand_ids = set()
    for t in q_terms:
        for cid in inverted.get(t, []):
            cand_ids.add(cid)

    if not cand_ids:
        return []

    N = max(len(chunks), 1)
    df = {}
    for t, ids in inverted.items():
        df[t] = len(ids)
    idf_weights = {
        t: max(0.1, 1.0 + (N - df_t + 0.5) / (df_t + 0.5)) for t, df_t in df.items()
    }

    scored = []
    for cid in cand_ids:
        if cid >= len(chunks):
            continue
        c = chunks[cid]
        # 优先使用 terms 缓存，否则从文本字段重新计算
        cached_terms = terms_cache.get(str(cid))
        if cached_terms:
            c_terms = set(cached_terms)
        else:
            text_parts = [c.get("summary", ""), c.get("file", ""), c.get("skill", "")]
            fed_from = c.get("fed_from")
            if fed_from:
                text_parts.append(fed_from)
            c_text = " ".join(text_parts)
            c_terms = retrieval.terms(c_text)
        overlap = q_terms & c_terms
        if not overlap:
            continue
        # maximal 去重：被其他命中词包含的短 gram 不重复计分（"数据索引"的
        # 数据/据索/索引/数据索/据索引 只按独立证据计一次），与 retrieval.search 同口径
        max_overlap = {
            t for t in overlap if not any(t != o and t in o for o in overlap)
        }
        score = sum(idf_weights.get(t, 0.1) for t in max_overlap)
        file_terms = retrieval.terms(c.get("file", ""))
        if max_overlap & file_terms:
            score += 2
        if c.get("fed_from"):
            score += 0.5
        scored.append((score, c, sorted(max_overlap, key=len, reverse=True)))

    scored.sort(key=lambda x: -x[0])
    return scored[:top]


# ── 读取 chunk 原文 ────────────────────────────────────────────────────────────
def read_chunk_text(chunk, skill_dir):
    """读 chunk 对应的原文行段。跨技能投喂 chunk 自带 src_dir（依赖技能的真实目录）+
    src_file（目录内相对路径），file 字段里的 `@dep` 只是展示标签；普通 chunk 退回
    skill_dir/file。此前投喂 chunk 拿伪路径去 join，正文永远读不出来。"""
    base = chunk.get("src_dir") or skill_dir
    rel = chunk.get("src_file") or chunk["file"]
    abs_path = os.path.join(base, rel)
    try:
        lines = _read_file_lines(abs_path)
    except OSError:
        return ""
    s = max(chunk["s"] - 1, 0)
    e = min(chunk["e"], len(lines))
    return "".join(lines[s:e])
