# test_ignore_rules.py - .skillignore 语义回归：** 通配 / 锚定不越界 / 根外尾部匹配
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import paths  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_root")
OTHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_other")


def _ig(rules):
    return paths._Ignore(rules, ROOT)


def test_starstar_prefix_matches_any_level_and_root():
    ig = _ig(["**/deep/"])
    assert ig.match(os.path.join(OTHER, "a", "b", "deep"), True)
    assert ig.match(os.path.join(OTHER, "deep"), True)  # 根层级也该命中


def test_starstar_suffix_requires_relative_base():
    """`a/**` 含目录分隔符：无相对基准（rel_to/技能根）时不匹配任何路径——
    此前的"逐层后缀"近似会被 CI runner 路径里的 D:\a\... 之类段落造成假阳性。"""
    ig = _ig(["a/**"])
    assert not ig.match(os.path.join(OTHER, "a", "b", "c.txt"), False)
    assert not ig.match(os.path.join(OTHER, "b", "c.txt"), False)
    # 给出扫描根后才按 gitignore 相对语义生效
    assert ig.match(os.path.join(OTHER, "a", "b", "c.txt"), False, rel_to=OTHER)
    assert not ig.match(os.path.join(OTHER, "b", "c.txt"), False, rel_to=OTHER)


def test_anchored_rule_confined_to_root():
    ig = _ig(["/anchored/"])
    assert ig.match(os.path.join(ROOT, "anchored"), True)
    # 根外同名目录不应被锚定规则命中（此前退化为任意层级 basename 匹配）
    assert not ig.match(os.path.join(OTHER, "anchored"), True)


def test_multi_segment_rule_relative_to_scan_root():
    ig = _ig(["*/backup/"])
    # rel_to=扫描根：backup 隔层命中
    assert ig.match(os.path.join(OTHER, "foo", "backup"), True, rel_to=OTHER)
    # 直接位于扫描根下的 backup 不含中间层，不该命中（gitignore 语义）
    assert not ig.match(os.path.join(OTHER, "backup"), True, rel_to=OTHER)
    # a/** 按扫描根相对匹配：命中根下 a 的任意后代
    ig2 = _ig(["a/**"])
    assert ig2.match(os.path.join(OTHER, "a", "b", "c.txt"), False, rel_to=OTHER)
    assert not ig2.match(os.path.join(OTHER, "b", "a", "c.txt"), False, rel_to=OTHER)


def test_dir_only_rule_ignores_files():
    ig = _ig(["tmp/"])
    assert ig.match(os.path.join(OTHER, "tmp"), True)
    assert not ig.match(os.path.join(OTHER, "tmp"), False)


def test_negation_last_match_wins():
    ig = _ig(["dist/", "!keep/"])
    assert not ig.match(os.path.join(OTHER, "keep"), True)
    assert ig.match(os.path.join(OTHER, "dist"), True)


def test_plain_basename_matches_any_level():
    ig = _ig(["node_modules"])
    assert ig.match(os.path.join(OTHER, "x", "y", "node_modules"), True)
