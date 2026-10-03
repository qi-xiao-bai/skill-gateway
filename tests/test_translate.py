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


if __name__ == "__main__":
    unittest.main()
