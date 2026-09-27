#!/usr/bin/env python3
# skillmd.py - SKILL.md 解析：name/description（支持单行/引号/YAML 块标量）+ 触发词抽取
# created 2026-09-16 qjl
import re

# 结尾 --- 允许出现在文件末尾（无尾换行也算闭合），否则整块 frontmatter 会被丢弃
_FM_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.S)
# 缩进的 `key:` 行是兄弟键/子结构，不是上一行标量的续行
_NEW_KEY_RE = re.compile(r"^[ \t]+[\w.-]+[ \t]*:")


def _read_text(path):
    """读文件：utf-8-sig 剥 BOM（Windows 记事本默认带 BOM，会把首行 --- 顶掉）；
    换行统一为 \\n，CRLF 的 \\r 不再渗进块标量值。失败返回 None。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read().replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        return None


def _strip_value(v):
    return v.strip().strip('"').strip("'")


def _indent_of(l):
    n = 0
    while n < len(l) and l[n] in (" ", "\t"):
        n += 1
    return n


def _block_value(marker, body):
    """还原 YAML 块标量：> 折叠(换行变空格,空行变回车) / | 字面(保留换行)。"""
    indents = [_indent_of(l) for l in body if l.strip()]
    indent = min(indents) if indents else 0
    body = [l[indent:] if len(l) >= indent else l.lstrip(" \t") for l in body]
    if marker.startswith(">"):
        text = ""
        for l in body:
            s = l.strip()
            if s == "":
                text += "\n"
            else:
                if text and not text.endswith("\n"):
                    text += " "
                text += s
        return text.strip()
    return "\n".join(body).strip()


def frontmatter(text):
    m = _FM_RE.match(text)
    return m.group(1) if m else ""


def body_text(text):
    """正文 = 去掉 frontmatter 块之后的文本；回退描述只从这里取，防止把
    `---\\nname: x\\n---` 当成首段正文。"""
    m = _FM_RE.match(text)
    return text[m.end():] if m else text


def fm_value(fm, key):
    """取 frontmatter 中 key 的值，支持：单行 / 引号 / > 与 | 块标量 / 缩进续行。"""
    m = re.search(r"^" + re.escape(key) + r":", fm, re.M)
    if not m:
        return None
    line_end = fm.find("\n", m.end())
    if line_end == -1:
        line_end = len(fm)
    first = fm[m.end():line_end].strip()
    rest = fm[line_end + 1:] if line_end < len(fm) else ""
    body = []
    for ln in rest.split("\n"):
        if ln.strip() == "":
            body.append(ln)
            continue
        if ln[0] in (" ", "\t"):
            body.append(ln)
        else:
            break
    if first.startswith((">", "|")):
        return _block_value(first, body)
    if first:
        val = _strip_value(first)
        cont = [ln for ln in body if not _NEW_KEY_RE.match(ln)]
        if cont:
            extra = _block_value(">", cont)
            if extra:
                val = (val + " " + extra).strip()
        return val
    if body:
        head = body[0].lstrip(" \t")
        if head.startswith((">", "|")):
            return _block_value(head, body[1:])
        if _NEW_KEY_RE.match(body[0]):
            return None  # 缩进映射是子结构，不是本键的标量值
        return _block_value(">", body)
    return None


def _fallback_name(body):
    h1 = re.search(r"^#\s+(.+)$", body, re.M)
    return h1.group(1).strip() if h1 else None


def _fallback_desc(body):
    paras = [p.strip() for p in body.split("\n\n")
             if p.strip() and not p.strip().startswith("#")]
    return paras[0][:300] if paras else None


def parse_skill_md(path):
    """从 SKILL.md 抽取 name/description（frontmatter 优先，否则 # 标题+首段）。"""
    text = _read_text(path)
    if text is None:
        return None, None
    fm = frontmatter(text)
    body = body_text(text)
    name = desc = None
    if fm:
        name = fm_value(fm, "name")
        desc = fm_value(fm, "description_zh") or fm_value(fm, "description")
    if not name:
        name = _fallback_name(body)
    if not desc:
        desc = _fallback_desc(body)
    return name, desc


def parse_skill_meta(path):
    """从 SKILL.md 抽取完整元数据字典 (name, description, description_zh, description_en, triggers, tools)。"""
    text = _read_text(path)
    if text is None:
        return {}
    fm = frontmatter(text)
    body = body_text(text)
    meta = {}
    if fm:
        for k in ("name", "description", "description_zh", "description_en", "allowed-tools", "tools", "version"):
            val = fm_value(fm, k)
            if val:
                meta[k] = val
        trigs = extract_triggers(meta.get("description_zh") or meta.get("description") or "")
        meta["triggers"] = trigs
    if not meta.get("name"):
        name = _fallback_name(body)
        if name:
            meta["name"] = name
    if "description" not in meta and "description_zh" not in meta:
        d = _fallback_desc(body)
        if d:
            meta["description"] = d
    return meta


def extract_triggers(desc):
    """从描述里解析触发词，供 list / L1 展示与检索加权。
    识别：中文「触发词：」、英文 Triggers: / Use when / TRIGGER when。"""
    if not desc:
        return []
    out = []

    def _split(s):
        return [t.strip() for t in re.split(r"[、,，;；/|\s]+", s) if t.strip()]

    m = re.search(r"触发词[：:]\s*(.+)", desc)
    if m:
        out.extend(_split(m.group(1)))
    if not out:
        m2 = re.search(r"\bTriggers?\b[：:]\s*(.+)", desc, re.I)
        if m2:
            out.extend(_split(m2.group(1)))
    if not out:
        m3 = re.search(r"(?:TRIGGER when|Use when)[：:]?\s*(.+)", desc, re.I)
        if m3:
            out.append(m3.group(1).strip()[:80])
    seen, res = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            res.append(t)
    return res[:8]
