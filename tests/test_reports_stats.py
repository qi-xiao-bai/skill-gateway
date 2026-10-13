#!/usr/bin/env python3
# test_reports_stats.py - dashboard 顶栏统计口径 = 可见（非 excluded），与检索/编排一致
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import reports
import build_index


def _entries():
    return [
        {"name": "vis-skill", "type": "skill", "description": "可见", "path": "", "triggers": []},
        {"name": "off-skill", "type": "skill", "description": "平台未绑定", "path": "",
         "triggers": [], "excluded": True},
        {"name": "vis-mcp", "type": "mcp", "description": "MCP", "path": "", "triggers": []},
        {"name": "off-mcp", "type": "mcp", "description": "排除的 MCP", "path": "",
         "triggers": [], "excluded": True},
    ]


class DashboardStatsScopeTests(unittest.TestCase):
    def test_header_stats_count_visible_only(self):
        """顶栏 Skills/MCP 计数只计非 excluded（可见口径）——excluded 的不进统计。"""
        html, _n, _l, _e = reports.generate_dashboard_html(_entries(), [])
        self.assertNotIn("__STAT_SKILLS__", html)  # 占位符已全部替换
        # 对拍：可见口径重算（off-skill/off-mcp 不计）
        vis = [e for e in _entries() if not e.get("excluded")]
        vis_skills = sum(1 for e in vis if e["type"] == "skill")
        vis_mcps = sum(1 for e in vis if e["type"] == "mcp")
        self.assertEqual(vis_skills, 1)
        self.assertEqual(vis_mcps, 1)
        self.assertNotIn("__STAT_SKILLS__", html)




class UnboundAnnotationAndFallbackBannerTests(unittest.TestCase):
    """L1 未绑定标注 + F2 兜底口径横幅（评标建议采纳：标注比过滤好）。
    L1：未绑定（off-list）候选亮 [未绑定·可安装]，LLM 据此引导安装而非硬加载撞墙；
    F2：无登记表时 stats 必须显著声明全机兜底口径，防止被误读为权威平台口径。"""

    def test_unbound_tag_marks_off_list_only(self):
        self.assertEqual(build_index._unbound_tag({"visibility": "off-list"}),
                         " [未绑定·可安装]")
        self.assertEqual(build_index._unbound_tag({"visibility": "ready"}), "")
        self.assertEqual(build_index._unbound_tag({}), "")  # 兜底态无字段=无从判定=不标

    def test_stats_text_fallback_banner(self):
        entries = [{"name": "x", "type": "skill", "description": "d", "path": "",
                    "triggers": [], "visibility": "ready", "source": "test",
                    "has_references": False}]
        txt_missing = reports.stats_text(entries, registry_missing=True)
        self.assertIn("全机扫描兜底口径", txt_missing)
        self.assertIn("agent-index", txt_missing)
        txt_normal = reports.stats_text(entries, registry_missing=False)
        self.assertNotIn("全机扫描兜底口径", txt_normal)
class TokenEconomyLiveLedgerTests(unittest.TestCase):
    """stats 内嵌 Token 账本：数字一律本轮现场实算、零写死、同集合铁律。
    - 真文件 → 压缩率现场算出；全文加长，压缩率必须立刻跟着变（写死必然露馅）
    - 兜底态（条目全 off-list / excluded）→ 没有可比基线就明说，
      不许拿「全部技能全文」当分母、「仅可用技能 L1」当分子算出「省 100%」的假账
    - 空索引 → 明说无账可算，不打印无意义的零"""

    def test_ready_skill_compression_is_live(self):
        with tempfile.TemporaryDirectory() as td:
            skmd = os.path.join(td, "SKILL.md")
            with open(skmd, "w", encoding="utf-8") as f:
                f.write("# 演示\n" + "细节内容" * 200)
            e = {"name": "live-skill", "type": "skill", "description": "实算",
                 "path": td, "triggers": ["演示"], "source": "test"}
            eco = reports.token_economy([e])
            self.assertGreater(eco["full_chars"], 800)
            self.assertIsNotNone(eco["compression_pct"])
            self.assertLess(eco["compression_pct"], 100.0)
            self.assertGreater(eco["reduction_x"], 1.0)
            # 零写死证据：全文加长，压缩率必须立刻变小
            with open(skmd, "w", encoding="utf-8") as f:
                f.write("# 演示\n" + "细节内容" * 400)
            eco2 = reports.token_economy([e])
            self.assertLess(eco2["compression_pct"], eco["compression_pct"])
            # stats 文本必须携带账本段 + 本轮实测计时（ms）
            txt = reports.stats_text([e], [], registry_missing=False)
            self.assertIn("Token 账本", txt)
            self.assertIn("ms", txt)

    def test_offlist_fallback_refuses_fake_ratio(self):
        e = {"name": "off-skill", "type": "skill", "description": "d", "path": "",
             "triggers": [], "excluded": True, "source": "test"}
        eco = reports.token_economy([e])
        self.assertIsNone(eco["compression_pct"])
        txt = reports.stats_text([e], [], registry_missing=True)
        self.assertIn("暂无可比基线", txt)
        self.assertNotIn("省 100", txt)

    def test_empty_index_ledger_says_so(self):
        txt = reports.stats_text([], [], registry_missing=False)
        self.assertIn("空索引，无账可算", txt)


if __name__ == "__main__":
    unittest.main()
