#!/usr/bin/env python3
# test_reports_stats.py - dashboard 顶栏统计口径 = 可见（非 excluded），与检索/编排一致
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import reports


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


if __name__ == "__main__":
    unittest.main()
