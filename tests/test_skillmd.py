# test_skillmd.py - SKILL.md 解析回归：BOM/CRLF/EOF闭合/回退不吞frontmatter/续行
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import skillmd  # noqa: E402


def _write(content, encoding="utf-8"):
    fd, p = tempfile.mkstemp(suffix=".md", prefix="skix-skillmd-")
    os.close(fd)
    with open(p, "w", encoding=encoding, newline="") as f:
        f.write(content)
    return p


def test_bom_frontmatter_still_parsed():
    p = _write("---\nname: bom-skill\ndescription: 带BOM的技能\n---\n\n正文\n", encoding="utf-8-sig")
    try:
        name, desc = skillmd.parse_skill_md(p)
        assert name == "bom-skill"
        assert desc == "带BOM的技能"
    finally:
        os.unlink(p)


def test_crlf_literal_block_keeps_no_cr():
    p = _write("---\r\nname: crlf\r\ndescription: |-\r\n  第一行\r\n  第二行\r\n---\r\n正文\r\n")
    try:
        name, desc = skillmd.parse_skill_md(p)
        assert name == "crlf"
        assert "\r" not in (desc or "")
        assert desc == "第一行\n第二行"
    finally:
        os.unlink(p)


def test_frontmatter_closes_at_eof_without_newline():
    p = _write("---\nname: eof\ndescription: 无尾换行\n---")
    try:
        name, desc = skillmd.parse_skill_md(p)
        assert name == "eof"
        assert desc == "无尾换行"
    finally:
        os.unlink(p)


def test_fallback_desc_not_polluted_by_frontmatter():
    # 有 frontmatter 但缺 description：回退描述必须取正文首段，而不是 YAML 块
    p = _write("---\nname: no-desc\n---\n\n这是正文第一段，应当成为回退描述。\n\n第二段")
    try:
        name, desc = skillmd.parse_skill_md(p)
        assert name == "no-desc"
        assert desc == "这是正文第一段，应当成为回退描述。"
        assert "---" not in (desc or "")
    finally:
        os.unlink(p)


def test_single_line_value_with_indented_continuation():
    fm = "name: x\ndescription: 短描述\n  折叠续行内容\nother: y\n"
    assert skillmd.fm_value(fm, "description") == "短描述 折叠续行内容"


def test_single_line_value_not_merged_with_nested_key():
    fm = "name: x\ndescription: 一句话\n  meta: 不该被并进来\nother: y\n"
    assert skillmd.fm_value(fm, "description") == "一句话"


def test_indented_mapping_is_not_value():
    fm = "name: x\nparent:\n  child: 1\nother: y\n"
    assert skillmd.fm_value(fm, "parent") is None


def test_block_marker_on_next_line():
    fm = "name: x\ndescription:\n  >-\n  跨行块标量\n"
    assert skillmd.fm_value(fm, "description") == "跨行块标量"


def test_tab_indented_block_scalar():
    fm = "name: x\ndescription: |-\n\tTab 缩进行\n"
    assert skillmd.fm_value(fm, "description") == "Tab 缩进行"


def test_meta_fallbacks_mirror_parse_skill_md():
    p = _write("---\nname: meta-no-desc\n---\n\n正文首段")
    try:
        meta = skillmd.parse_skill_meta(p)
        assert meta["name"] == "meta-no-desc"
        assert meta["description"] == "正文首段"
        assert "---" not in meta["description"]
    finally:
        os.unlink(p)
