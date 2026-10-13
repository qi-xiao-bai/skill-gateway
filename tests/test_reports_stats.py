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
    """stats 内嵌 Token 账本：数字一律本轮现场实算、零写死、输出零说教。
    话术纪律（10-14 用户两轮纠偏）：命令输出=纯数字行+专业标签，
    讲道理/括号讲课/比喻一律不进输出（"容量≠注入""渐进式披露""地址簿"都算）。
    对比口径：原生选型（全清单注入）vs 网关选型（top-K 命中注入）；
    真值=A/B 会话实测（bench），stats 只给可计算的单次对比。"""

    def test_ab_lines_are_live_and_lecture_free(self):
        with tempfile.TemporaryDirectory() as td:
            ents = []
            for i in range(10):
                d = os.path.join(td, f"s{i}")
                os.makedirs(d)
                with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write("# 演示\n" + "细节内容" * 200)
                ents.append({"name": f"live-{i}", "type": "skill",
                             "description": f"实算{i}", "path": d,
                             "triggers": ["演示"], "source": "test"})
            eco = reports.token_economy(ents)
            self.assertIsNotNone(eco["density_pct"])
            self.assertGreater(eco["density_x"], 1.0)
            # 选型对比比例现场实算：10 项池注入 top-K 行 vs 全清单 10 行
            expected = 100.0 * (1.0 - min(eco["top_k"], 10) / 10.0)
            self.assertAlmostEqual(eco["inject_saving_pct"], expected, delta=0.5)
            # 零写死证据：全文加长，密度必须立刻变大
            with open(os.path.join(td, "s0", "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("# 演示\n" + "细节内容" * 400)
            eco2 = reports.token_economy(ents)
            self.assertGreater(eco2["density_x"], eco["density_x"])
            txt = reports.stats_text(ents, [], registry_missing=False)
            # 三行专业标签在场
            self.assertIn("原生选型（全清单注入）", txt)
            self.assertIn("网关选型（top-", txt)
            self.assertIn("单次选型差值", txt)
            # 说教禁令（钉子）：讲课用语不得回潮
            for banned in ("渐进式披露", "容量≠注入", "地址簿", "地图密度", "压缩至", "省 100"):
                self.assertNotIn(banned, txt)
            self.assertIn("bench", txt)  # 累计实测指向 bench

    def test_offlist_fallback_refuses_fake_ratio(self):
        e = {"name": "off-skill", "type": "skill", "description": "d", "path": "",
             "triggers": [], "excluded": True, "source": "test"}
        eco = reports.token_economy([e])
        self.assertIsNone(eco["density_pct"])
        self.assertIsNone(eco["inject_saving_pct"])
        txt = reports.stats_text([e], [], registry_missing=True)
        self.assertIn("无可比基线", txt)
        self.assertNotIn("省 100", txt)

    def test_empty_index_ledger_says_so(self):
        txt = reports.stats_text([], [], registry_missing=False)
        self.assertIn("空索引，无账可算", txt)


if __name__ == "__main__":
    unittest.main()
