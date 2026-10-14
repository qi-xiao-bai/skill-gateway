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


def test_use_when_clause_not_swallowed_whole():
    """「Use when …」句式只取句界内子句并剥功能词——此前 (.+) 贪婪吞整句进触发词
    参与打分（10-14 使用场景实测：antv-g2-chart 吸进「user asks for G2 charts…」
    整句、叙事类技能连中文尾句一起吞）。"""
    trigs = skillmd.extract_triggers(
        "Advanced chart library. Use when user asks for G2 charts, "
        "bar charts, line charts, and pie charts.")
    assert trigs == ["G2 charts", "bar charts", "line charts", "pie charts"]

    trigs2 = skillmd.extract_triggers(
        "Visual tool. Use when users want to create data interpretations. 叙事文本可视化")
    # 句界截断：中文尾句不得被吞进触发词
    assert trigs2 == ["create data interpretations"]

    trigs3 = skillmd.extract_triggers(
        "图表技能。触发词：柱状图、折线图、可视化")
    assert trigs3 == ["柱状图", "折线图", "可视化"]


def test_meta_fallbacks_mirror_parse_skill_md():
    p = _write("---\nname: meta-no-desc\n---\n\n正文首段")
    try:
        meta = skillmd.parse_skill_meta(p)
        assert meta["name"] == "meta-no-desc"
        assert meta["description"] == "正文首段"
        assert "---" not in meta["description"]
    finally:
        os.unlink(p)

if __name__ == "__main__":
    # 裸跑兼容：无 pytest 时直接执行全部 test_* 函数
    import inspect as _insp
    _mod = _insp.getmodule(_insp.currentframe())
    _fails = 0
    for _nm in sorted(dir(_mod)):
        if _nm.startswith("test_") and callable(getattr(_mod, _nm)):
            try:
                getattr(_mod, _nm)()
                print(f"  PASS {_nm}")
            except Exception as _ex:
                _fails += 1
                print(f"  FAIL {_nm}: {_ex}")
    print(chr(10) + ('FAILED ' + str(_fails) if _fails else 'ALL PASS'))
    import sys as _sys
    _sys.exit(1 if _fails else 0)
