#!/usr/bin/env python3
# test_gap_notice.py - 能力缺口检测：query CJK 概念在结果证据中零命中时提示缺口
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import retrieval


class GapNoticeTests(unittest.TestCase):
    def test_all_cjk_uncovered_reports_gap(self):
        """行为①：query 的 CJK 概念全部未命中（Top1 仅英文词面命中）→ 报缺口并列出原始片段。"""
        hits = [(75.0, {"name": "decision-review-mirror", "type": "skill"}, ["review"])]
        hit, msg = retrieval.gap_notice("代码审查 code review", hits)
        self.assertTrue(hit)
        self.assertIn("代码审查", msg)
        self.assertIn("能力缺口", msg)

    def test_partial_cjk_coverage_no_gap(self):
        """行为②：CJK 概念部分命中（合同双签已有证据）→ 不报缺口。"""
        hits = [(90.0, {"name": "crm-support", "type": "skill"},
                 ["合同双", "双签", "合同"])]
        hit, msg = retrieval.gap_notice("合同双签 审批流程", hits)
        self.assertFalse(hit)
        self.assertEqual(msg, "")


    def test_pure_english_query_not_judged(self):
        """行为③：纯英文 query 不判缺口（分词语言敏感，EN 未命中属正常）。"""
        hits = [(75.0, {"name": "decision-review-mirror", "type": "skill"}, ["review"])]
        hit, msg = retrieval.gap_notice("code review", hits)
        self.assertFalse(hit)
        self.assertEqual(msg, "")

    def test_empty_hits_no_gap(self):
        """行为④：零命中走既有的未命中消息，缺口检测不参与。"""
        hit, msg = retrieval.gap_notice("代码审查", [])
        self.assertFalse(hit)
        self.assertEqual(msg, "")


if __name__ == "__main__":
    unittest.main()
