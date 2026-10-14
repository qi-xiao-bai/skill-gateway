#!/usr/bin/env python3
# test_route.py - 话术路由规则回归：动作锚定优先、需求句兜底 chat（10-14 使用场景实测修正）
# 此前 export 规则含裸词「表格/excel/csv」——描述里出现名词即建议 export，
# 「我想处理一些数据表格」被误导；收紧后裸词删除、需求句（我想/我要/想要/需要）
# 落 question_cues 兜底 chat 选型。
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import route


class RouteRulesRegressionTests(unittest.TestCase):
    def test_request_phrase_falls_to_chat(self):
        """需求句无动作锚定 → chat 选型兜底，不再误指 export。"""
        self.assertEqual(route.route("我想处理一些数据表格"),
                         ("chat", ["chat", "search"]))
        self.assertEqual(route.route("我想复盘今天的决策"),
                         ("chat", ["chat", "search"]))

    def test_action_anchored_export_still_matches(self):
        """动作锚定模式不受收紧影响：导出/转成（格式词）仍指 export。"""
        self.assertEqual(route.route("导出 csv")[0], "export")
        self.assertEqual(route.route("帮我把这份数据转成 excel 表格")[0], "export")
        self.assertEqual(route.route("导出 excel 文件")[0], "export")


if __name__ == "__main__":
    unittest.main()
