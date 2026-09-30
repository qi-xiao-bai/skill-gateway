#!/usr/bin/env python3
# test_available_list.py - 权威可用清单模式：visibility 打标、三段合并、off-list 检索放行
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import scanner
import index_store
import importer
import retrieval


def _write_list(root, items):
    os.makedirs(os.path.join(root, "inputs"), exist_ok=True)
    with open(os.path.join(root, "inputs", "available_skills.json"), "w", encoding="utf-8") as f:
        f.write('{"skills": ' + items + "}")


class AvailableListTests(unittest.TestCase):
    def setUp(self):
        self._old_root = scanner.ROOT
        scanner.ROOT = tempfile.mkdtemp(prefix="skix-list-")

    def tearDown(self):
        scanner.ROOT = self._old_root

    def test_visibility_tags_and_passthrough(self):
        _write_list(scanner.ROOT, '''
          [{"name": "crm-support", "description": "CRM ops CRM 运维", "category": "CRM业务",
            "platform": "数字员工-网页端", "status": "ready"},
           {"name": "skill-gateway", "description": "gateway 网关", "status": "blocked"}]
        ''')
        ents = scanner.available_list_entries()
        by = {e["name"]: e for e in ents}
        self.assertEqual(by["crm-support"]["visibility"], "ready")
        self.assertEqual(by["crm-support"]["category"], "CRM业务")
        self.assertEqual(by["crm-support"]["platform"], "数字员工-网页端")
        self.assertNotIn("excluded", by["crm-support"])
        self.assertEqual(by["skill-gateway"]["visibility"], "blocked")
        self.assertTrue(by["skill-gateway"]["excluded"])
        self.assertIn("blocked", by["skill-gateway"]["exclude_reason"])

    def test_scan_skills_list_mode_and_ignore_list(self):
        _write_list(scanner.ROOT, '[{"name": "listed-skill", "description": "listed 清单内"}]')
        listed = scanner.scan_skills()
        self.assertEqual([e["name"] for e in listed], ["listed-skill"])
        # ignore_list=True 绕过清单走常规扫描：结果不含清单模式产物（source 均非 available-list）
        res = scanner.scan_skills(ignore_list=True)
        self.assertTrue(all(e.get("source") != "available-list" for e in res))

    def test_merge_off_list_and_list_priority(self):
        _write_list(scanner.ROOT, '[{"name": "crm-support", "description": "CRM ops CRM 运维"}]')
        avail = scanner.available_list_entries()
        # 磁盘侧：两个真实 SKILL.md（一个与清单同名，一个清单外）——枚举被 mock，解析走真逻辑
        disk_root = tempfile.mkdtemp(prefix="skix-disk-")
        pairs = []
        for nm in ("crm-support", "other-tool"):
            d = os.path.join(disk_root, nm)
            os.makedirs(d)
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {nm}\ndescription: disk {nm}\n---\nbody")
            pairs.append(("disk-root", os.path.join(d, "SKILL.md")))
        with mock.patch.object(scanner, "enumerate_skill_md", return_value=pairs), \
             mock.patch.object(scanner, "scan_mcp", return_value=[]):
            ents, st = index_store._update_from_available_list(None, avail)
        by = {e["name"]: e for e in ents if e["type"] == "skill"}
        self.assertEqual(by["crm-support"].get("visibility"), "ready")
        self.assertFalse(by["crm-support"].get("excluded", False))
        self.assertEqual(by["other-tool"]["visibility"], "off-list")
        self.assertTrue(by["other-tool"]["excluded"])
        self.assertIn("other-tool", st["added"])
        # 清单条目只加一次：off-list 磁盘路径里的同名项被清单优先去重，不重复入 added
        self.assertEqual(st["added"].count("crm-support"), 1)

    def test_search_off_list_gate(self):
        ents = [{"name": "bound-skill", "description": "code review 审查代码", "type": "skill",
                 "path": "", "triggers": [], "visibility": "ready"},
                {"name": "off-skill", "description": "code review 审查代码 未绑定", "type": "skill",
                 "path": "", "triggers": [], "visibility": "off-list", "excluded": True,
                 "exclude_reason": "平台未绑定（不在权威可用清单，仅 --all 全量视图可见）"},
                {"name": "rule-skill", "description": "code review 审查代码 规则排除", "type": "skill",
                 "path": "", "triggers": [], "excluded": True, "exclude_reason": "用户策略"}]
        default_names = {e["name"] for _s, e, _m in
                         retrieval.search(ents, "code review 审查")}
        self.assertIn("bound-skill", default_names)
        self.assertNotIn("off-skill", default_names)
        all_names = {e["name"] for _s, e, _m in
                     retrieval.search(ents, "code review 审查", include_off_list=True)}
        self.assertIn("off-skill", all_names)
        self.assertNotIn("rule-skill", all_names)  # 规则排除不因 --all 放行


    def test_agent_bound_computed(self):
        import io as _io
        os.makedirs(os.path.join(scanner.ROOT, "inputs"), exist_ok=True)
        with open(os.path.join(scanner.ROOT, "inputs", "available_skills.json"),
                  "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "current_agent": "代码审查员",
                "skills": [
                    {"name": "bound-a", "description": "bound 绑定", "agents": ["代码审查员"]},
                    {"name": "bound-b", "description": "bound 多智能体", "agents": ["其他", "代码审查员"]},
                    {"name": "unbound-c", "description": "unbound 未绑定", "agents": []},
                    {"name": "no-info-d", "description": "no agents field 无字段"},
                ]}, ensure_ascii=False))
        old_env = os.environ.pop("SKILL_GATEWAY_AGENT", None)
        try:
            by = {e["name"]: e for e in scanner.available_list_entries()}
            self.assertTrue(by["bound-a"]["agent_bound"])
            self.assertEqual(by["bound-b"]["agents"], ["其他", "代码审查员"])
            self.assertFalse(by["unbound-c"]["agent_bound"])
            self.assertTrue(by["no-info-d"]["agent_bound"])
            # 未声明 current_agent → Agent 维度不生效，全部视为已绑定
            with open(os.path.join(scanner.ROOT, "inputs", "available_skills.json"),
                      "w", encoding="utf-8") as f:
                f.write('{"skills": [{"name": "x", "description": "x", "agents": []}]}')
            e = scanner.available_list_entries()[0]
            self.assertTrue(e["agent_bound"])
        finally:
            if old_env is not None:
                os.environ["SKILL_GATEWAY_AGENT"] = old_env

    def test_search_agent_only_filter(self):
        ents = [{"name": "bound-a", "description": "code review 审查代码", "type": "skill",
                 "path": "", "triggers": [], "visibility": "ready", "agent_bound": True},
                {"name": "unbound-b", "description": "code review 审查代码", "type": "skill",
                 "path": "", "triggers": [], "visibility": "ready", "agent_bound": False}]
        default_names = {e["name"] for _s, e, _m in retrieval.search(ents, "code review 审查")}
        self.assertEqual(default_names, {"bound-a", "unbound-b"})  # 默认平台口径，不收窄
        agent_names = {e["name"] for _s, e, _m in
                       retrieval.search(ents, "code review 审查", agent_only=True)}
        self.assertEqual(agent_names, {"bound-a"})


    def test_agent_index_pipeline(self):
        raw = [
            {"name": "crm-support", "description": "CRM ops CRM 运维", "status": "ready",
             "category": "CRM业务"},
            {"name": "tool-x", "description": "tool 工具", "status": "blocked"},
            {"name": "tool-y", "description": "no status 无状态"},
        ]
        doc = importer.to_available_list(raw, "代码审查员",
                                         old_doc={"skills": [
                                             {"name": "tool-y", "category": "旧分类保留"}]})
        self.assertEqual(doc["current_agent"], "代码审查员")
        by = {it["name"]: it for it in doc["skills"]}
        # 新清单有 category → 新值优先；新清单缺字段 → 旧标注补位（不丢失）
        self.assertEqual(by["crm-support"]["category"], "CRM业务")
        self.assertEqual(by["tool-y"]["category"], "旧分类保留")
        self.assertEqual(by["crm-support"]["agents"], ["代码审查员"])
        self.assertEqual(by["tool-x"]["status"], "blocked")
        self.assertEqual(by["tool-y"]["agents"], ["代码审查员"])
        # parse_source_raw 保留扩展字段
        raw2 = importer.parse_source_raw('{"skills": [{"name": "a", "status": "ready", "category": "c"}]}')
        self.assertEqual(raw2[0]["status"], "ready")
        self.assertEqual(raw2[0]["category"], "c")


if __name__ == "__main__":
    unittest.main()
