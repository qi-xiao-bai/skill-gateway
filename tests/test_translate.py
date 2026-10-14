#!/usr/bin/env python3
# test_translate.py - 翻译 API 通道：中文直通/缓存命中/失败降级/批量回填
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import translate


class TranslateTests(unittest.TestCase):
    def setUp(self):
        self._old_out = os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
        self.tmp = tempfile.mkdtemp(prefix="skix-translate-")
        os.environ["SKILL_GATEWAY_OUT_DIR"] = os.path.join(self.tmp, "output")
        translate._CACHE = {}  # 每个用例独立缓存

    def tearDown(self):
        if self._old_out is not None:
            os.environ["SKILL_GATEWAY_OUT_DIR"] = self._old_out
        else:
            os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)

    def test_chinese_passthrough(self):
        """本来就是中文 → 原样返回，不调 API。"""
        with mock.patch.object(translate, "DEFAULT_ENDPOINTS", ["https://never.called/{q}"]):
            self.assertEqual(translate.translate_en_zh("中文描述"), "中文描述")
            self.assertEqual(translate.translate_en_zh(""), "")

    def test_api_success_and_cache_hit(self):
        """API 成功 → 返回译文并入缓存；第二次同文本 → 缓存命中不再请求。"""
        calls = []

        def fake_urlopen(req, timeout=8):
            calls.append(req.full_url)

            class _R:
                def read(self):
                    if "googleapis" in req.full_url:
                        return json.dumps([[["智能", "Intelligent"], ["代码优化", " code optimization"]]]).encode()
                    return json.dumps(
                        {"responseData": {"translatedText": "智能代码优化"},
                         "responseStatus": "200"}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _R()

        with mock.patch.object(translate.urllib.request, "urlopen", fake_urlopen):
            out1 = translate.translate_en_zh("Intelligent code optimization")
        self.assertEqual(out1, "智能代码优化")
        self.assertEqual(len(calls), 1)
        # 第二次同文本：缓存命中，零请求
        with mock.patch.object(translate.urllib.request, "urlopen", fake_urlopen):
            out2 = translate.translate_en_zh("Intelligent code optimization")
        self.assertEqual(out2, "智能代码优化")
        self.assertEqual(len(calls), 1)  # 没有第二次网络请求

    def test_api_failure_degrades_to_empty(self):
        """API 不可达 → 返回空串（调用方降级为原文），绝不抛异常。"""
        def boom(req, timeout=8):
            raise OSError("offline")

        with mock.patch.object(translate.urllib.request, "urlopen", boom):
            self.assertEqual(translate.translate_en_zh("Some English text"), "")

    def test_endpoint_breaker_skips_dead_endpoint(self):
        """按端点熔断：同一端点连败 2 次后不再被调用（新文本也不打它），
        可用端点立即接管——此前每条翻译先烧 8 秒超时等死端点（10-14 平台实测根因）。"""
        calls = {"g": 0, "m": 0}

        def fake_urlopen(req, timeout=8):
            if "googleapis" in req.full_url:
                calls["g"] += 1
                raise OSError("dead endpoint")
            calls["m"] += 1

            class _R:
                def read(self):
                    return json.dumps(
                        {"responseData": {"translatedText": "有效译文内容"},
                         "responseStatus": "200"}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _R()

        with mock.patch.object(translate.urllib.request, "urlopen", fake_urlopen):
            translate.translate_en_zh("First english text")    # g 败 1 → m 成
            translate.translate_en_zh("Second english text")   # g 败 2 → 熔断
            translate.translate_en_zh("Third english text")    # g 不再被调
        self.assertEqual(calls["g"], 2)
        self.assertEqual(calls["m"], 3)

    def test_endpoint_breaker_half_open_recovery(self):
        """熔断 TTL 过期后半开：死端点重新被试探，成功即清零恢复。只跳过不恢复
        是死锁设计（网络恢复后端点永远不会再现）。"""
        cache = translate._load_cache()
        tpl = translate.DEFAULT_ENDPOINTS[0]
        cache["_endpoint_fails"] = {
            tpl: {"n": 99, "t": __import__("time").time() - translate.BREAK_TTL - 1}}
        calls = {"n": 0}

        def fake_urlopen(req, timeout=8):
            calls["n"] += 1

            class _R:
                def read(self):
                    return json.dumps([[["恢复成功", "hello"]]]).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _R()

        with mock.patch.object(translate.urllib.request, "urlopen", fake_urlopen):
            out = translate.translate_en_zh("hello world breaker recovery")
        self.assertEqual(out, "恢复成功")
        self.assertEqual(calls["n"], 1)  # 半开试探了第一个端点并成功
        self.assertNotIn(tpl, (cache.get("_endpoint_fails") or {}))  # 清零恢复

    def test_translate_missing_fills_entries(self):
        """批量回填：缺译条目补上 description_zh，已有的不动。"""
        entries = [
            {"name": "a", "description": "English A"},
            {"name": "b", "description": "中文 B"},
            {"name": "c", "description": "English C", "description_zh": "已有"},
        ]

        def fake_urlopen(req, timeout=8):
            class _R:
                def read(self):
                    if "googleapis" in req.full_url:
                        return json.dumps([[["译文A", "English A"]]]).encode()
                    return json.dumps(
                        {"responseData": {"translatedText": "译:" + req.full_url.split("q=")[1][:4]},
                         "responseStatus": "200"}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _R()

        with mock.patch.object(translate.urllib.request, "urlopen", fake_urlopen):
            ok, fail = translate.translate_missing(entries)
        self.assertEqual(ok, 1)
        self.assertEqual(fail, 0)
        self.assertEqual(entries[0]["description_zh"], "译文A")
        self.assertNotIn("description_zh", entries[1])  # 中文原文不翻
        self.assertEqual(entries[2]["description_zh"], "已有")  # 已有不覆盖


    def test_translate_ready_entries_helper(self):
        """共享回填 helper（update/translate 子命令同源）：缺译 ready 条目被填、
        已有中文与 off-list 不动；无登记表环境零写盘（持久层=登记表，本用例隔离）。"""
        import build_index as bi
        import scanner as _sc
        old_root = _sc.ROOT
        _sc.ROOT = tempfile.mkdtemp(prefix="skix-trhelper-")
        try:
            es = [
                {"name": "r1", "type": "skill", "description": "Alpha english",
                 "visibility": "ready", "triggers": []},
                {"name": "r2", "type": "skill", "description": "Beta english",
                 "visibility": "ready", "description_zh": "已有中文", "triggers": []},
                {"name": "off1", "type": "skill", "description": "Gamma english",
                 "visibility": "off-list", "excluded": True, "triggers": []},
            ]
            with mock.patch.object(translate, "translate_en_zh",
                                    side_effect=lambda t, timeout=8: "译:" + t):
                ok, fail, remain = bi._translate_ready_entries(es, budget=5.0, tag="t")
            self.assertEqual((ok, fail, remain), (1, 0, 0))
            self.assertEqual(es[0]["description_zh"], "译:Alpha english")
            self.assertEqual(es[1]["description_zh"], "已有中文")
            self.assertNotIn("description_zh", es[2])
        finally:
            _sc.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
