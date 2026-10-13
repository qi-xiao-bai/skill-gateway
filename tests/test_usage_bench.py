#!/usr/bin/env python3
# test_usage_bench.py - 注入台账与 A/B 对比（bench 命令底层）
# 2026-10-14：用户口径"先不调用网关跑一遍、再调用网关跑一遍，看两遍 token 差"落地——
# 台账记录网关命令真实注入量（stdout 字符数），bench 汇总台账 / 计算会话差值。
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import usage


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("SKILL_GATEWAY_OUT_DIR")
        os.environ["SKILL_GATEWAY_OUT_DIR"] = tempfile.mkdtemp(prefix="skix-usage-")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
        else:
            os.environ["SKILL_GATEWAY_OUT_DIR"] = self._old

    def test_record_and_load_roundtrip(self):
        self.assertEqual(usage.load_entries(), [])
        usage.record("search", 540)
        usage.record("detail", 8986)
        rows = usage.load_entries()
        self.assertEqual([r["cmd"] for r in rows], ["search", "detail"])
        self.assertEqual([r["chars"] for r in rows], [540, 8986])
        self.assertEqual(usage.load_entries(limit=1)[-1]["chars"], 8986)

    def test_bench_text_ab_comparison(self):
        txt = usage.bench_text(319100, 184000)
        self.assertIn("基线会话（未调用网关）", txt)
        self.assertIn("网关会话（调用网关）", txt)
        self.assertIn("135,100", txt)   # 319100-184000，现场算出
        self.assertIn("42.3%", txt)     # 100*135100/319100
        # 差值为负必须如实呈现，不许反转成"节省"
        neg = usage.bench_text(100000, 150000)
        self.assertIn("差值为负", neg)

    def test_bench_text_empty_ledger_protocol(self):
        txt = usage.bench_text()
        self.assertIn("台账为空", txt)
        self.assertIn("bench <基线Σtoken> <网关Σtoken>", txt)

    def test_bench_text_ledger_summary(self):
        usage.record("search", 400)
        usage.record("detail", 8000)
        txt = usage.bench_text()
        self.assertIn("累计 2 次调用", txt)
        self.assertIn("8,400 字符", txt)          # 400+8000，实测合计
        self.assertIn("search", txt)

    def test_counting_stdout_counts_and_passes_through(self):
        class _Inner:
            def __init__(self):
                self.written = []

            def write(self, s):
                self.written.append(s)

        inner = _Inner()
        tee = usage.CountingStdout(inner)
        tee.write("hello")
        tee.write("世界")
        self.assertEqual(tee.chars, 7)                 # 5 + 2 字符，写多少记多少
        self.assertEqual("".join(inner.written), "hello世界")  # 原样透传
        self.assertEqual(tee.written, inner.written)   # __getattr__ 透传到内层

    def test_bench_not_recorded(self):
        """测量命令自身不得入账（自我膨胀防护）。"""
        self.assertIn("bench", usage.SKIP_RECORD)


if __name__ == "__main__":
    unittest.main()
