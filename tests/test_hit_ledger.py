#!/usr/bin/env python3
# test_hit_ledger.py - 命中率账本：记录检索/采纳事件，聚合能力缺口与僵尸技能报表
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import hit_ledger


def _hits():
    return [(90.0, {"name": "crm-support", "type": "skill"}, ["合同双签", "审批"]),
            (12.5, {"name": "crm-copilot", "type": "skill"}, ["code"])]


class HitLedgerTests(unittest.TestCase):
    def setUp(self):
        self._old_out = os.environ.get("SKILL_GATEWAY_OUT_DIR")
        self._old_en = os.environ.pop("SKILL_GATEWAY_LEDGER", None)
        self.tmp = tempfile.mkdtemp(prefix="skix-ledger-")
        os.environ["SKILL_GATEWAY_OUT_DIR"] = os.path.join(self.tmp, "output")

    def tearDown(self):
        if self._old_out is not None:
            os.environ["SKILL_GATEWAY_OUT_DIR"] = self._old_out
        else:
            os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
        if self._old_en is not None:
            os.environ["SKILL_GATEWAY_LEDGER"] = self._old_en
        else:
            os.environ.pop("SKILL_GATEWAY_LEDGER", None)

    def test_1_record_load_roundtrip(self):
        hit_ledger.record_event("search", "CRM 合同审批", _hits())
        events = hit_ledger.load_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "search")
        self.assertEqual(events[0]["query"], "CRM 合同审批")
        self.assertEqual(events[0]["n_hits"], 2)
        self.assertEqual(events[0]["top"][0]["name"], "crm-support")
        self.assertEqual(events[0]["top"][0]["score"], 90.0)

    def test_2_disabled_writes_nothing(self):
        os.environ["SKILL_GATEWAY_LEDGER"] = "0"
        hit_ledger.record_event("search", "q", _hits())
        self.assertEqual(hit_ledger.load_events(), [])
        self.assertFalse(os.path.isfile(hit_ledger.ledger_path()))

    def test_3_report_aggregates_gap_queries(self):
        hit_ledger.record_event("search", "CRM 合同审批", [])
        hit_ledger.record_event("chat", "CRM 合同审批", [])
        hit_ledger.record_event("search", "另一个问题", _hits())
        text = hit_ledger.ledger_report(hit_ledger.load_events())
        self.assertIn("能力缺口", text)
        self.assertIn("CRM 合同审批", text)

    def test_4_report_lists_zombie_skills(self):
        text = hit_ledger.ledger_report([], skill_names={"alpha", "beta"})
        self.assertIn("僵尸技能", text)
        self.assertIn("alpha", text)

    def test_5_report_counts_adoptions(self):
        hit_ledger.record_event("adopt", "crm-support", [], {"skill": "crm-support"})
        text = hit_ledger.ledger_report(hit_ledger.load_events())
        self.assertIn("采纳榜", text)
        self.assertIn("crm-support", text)

    def test_6_ledger_truncates_when_large(self):
        old_max = hit_ledger.LEDGER_MAX_BYTES
        hit_ledger.LEDGER_MAX_BYTES = 600
        try:
            for i in range(30):
                hit_ledger.record_event("search", f"query-{i}", [])
            events = hit_ledger.load_events()
        finally:
            hit_ledger.LEDGER_MAX_BYTES = old_max
        self.assertLess(len(events), 30)
        self.assertGreater(len(events), 0)
        self.assertEqual(events[-1]["query"], "query-29")  # 保留的是最新

    def test_7_record_never_raises(self):
        os.environ["SKILL_GATEWAY_OUT_DIR"] = os.path.join(self.tmp, "not-a-dir", "file.txt")
        hit_ledger.record_event("search", "q", _hits())  # 不应抛异常
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
