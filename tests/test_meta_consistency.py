#!/usr/bin/env python3
# test_meta_consistency.py - 元一致性守卫：把历史上踩过的每类结构性 bug 变成自动化检查
# 覆盖的 bug 类别（2026-09/10 实际发生过）：
#   ① COMMANDS 注册了不存在的 handler（整函数替换吞掉 cmd_ledger/cmd_pack）
#   ② 运行时产物不在清理名单（hit-ledger.jsonl 漏网）
#   ③ 模块存在未定义名称（terms/paths/re/shutil 的 NameError 系列）
#   ④ 模块无法编译/导入
import json
import os
import re
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)


class MetaConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_index
        cls.build_index = build_index

    def test_1_every_command_handler_exists(self):
        """① COMMANDS 里注册的每个 handler 必须真实存在。"""
        missing = [s["name"] for s in self.build_index.COMMANDS
                   if s["handler"] not in vars(self.build_index)]
        self.assertEqual(missing, [])

    def test_2_every_generated_file_in_clean_list(self):
        """② 源码里所有 out_path("X") 产物必须在 clean 清理名单内。"""
        sys.path.insert(0, SCRIPTS)
        import paths
        for f in os.listdir(SCRIPTS):
            if not f.endswith(".py"):
                continue
            src = open(os.path.join(SCRIPTS, f), encoding="utf-8").read()
            for m in re.finditer(r'out_path\("([^"]+)"\)', src):
                self.assertIn(m.group(1), paths.LEGACY_OUTPUT_FILES,
                              f"{f} 生成 {m.group(1)} 但 clean 名单未覆盖")

    def test_2b_runtime_registries_in_clean_scope(self):
        """②b 环境本地登记表（available_skills/agent_list/platform_skills）必须
        在 clean 清扫范围内——登记表是运行时状态，源目录不留存。"""
        import paths
        for name in ("available_skills.json", "agent_list.json", "platform_skills.json"):
            self.assertIn(name, paths.RUNTIME_INPUT_FILES)

    def test_3_no_undefined_names_pyflakes_f821(self):
        """③ 全模块 pyflakes 扫描：不允许任何未定义名称（F821）。"""
        r = subprocess.run([sys.executable, "-m", "pyflakes", SCRIPTS],
                           capture_output=True, text=True)
        f821 = [ln for ln in (r.stdout or "").splitlines() if "undefined name" in ln]
        self.assertEqual(f821, [])

    def test_4_all_modules_compile_and_import(self):
        """④ 所有脚本可编译、可导入。"""
        import py_compile
        for f in os.listdir(SCRIPTS):
            if not f.endswith(".py"):
                continue
            py_compile.compile(os.path.join(SCRIPTS, f), doraise=True)
        for f in os.listdir(SCRIPTS):
            if f.endswith(".py"):
                __import__(f[:-3])

    def test_5_pipeline_stage_dicts_carry_contract_fields(self):
        """⑤ 生命周期阶段字典必须带齐下游消费者需要的全部字段
        （format_pipeline_markdown/_mermaid/clean_pipe 曾经因缺字段崩）。"""
        import pipeline
        ents = [{"name": "plan", "type": "skill", "description": "planning 规划",
                 "path": "", "triggers": []},
                {"name": "ref", "type": "skill", "description": "refactor 重构",
                 "path": "", "triggers": []}]
        pipe = pipeline.build_pipeline("重构开发 deployment 部署", entries=ents, edges=[])
        need = {"index", "stage_title", "skills", "skill_names", "parallel",
                "input", "output", "handoff", "evidence"}
        for st in pipe["stages"]:
            for k in need:
                self.assertIn(k, st, f"阶段字典缺字段 {k}")


    def test_6_clean_never_deletes_source(self):
        """⑥ 不能删一个有用的：删除名单与源文件白名单必须零交集。"""
        import paths
        PROTECTED = {"SKILL.md", "README.md", "使用说明.md", "conftest.py",
                     "skill_gateway.config.json", "skill_profile.json",
                     ".gitignore", ".skillignore", ".skillexclude",
                     "available_skills.example.json", "platform_connectors.json"}
        self.assertEqual(set(paths.LEGACY_OUTPUT_FILES) & PROTECTED, set())
        self.assertEqual(set(paths.RUNTIME_INPUT_FILES) & PROTECTED, set())
        for d in ("docs", "references", "templates", "tests", "scripts", "inputs"):
            self.assertNotIn(d, paths.CLEAN_DIR_SWEEP)

    def test_7_all_temp_creations_use_skix_prefix(self):
        """⑦ 不放过一个测试遗留：所有临时目录/文件创建点必须用 skix- 前缀
        （clean 的系统临时区清扫按该前缀匹配，无前缀 = 扫不到的暗垃圾）。"""
        import re as _re
        base = os.path.dirname(SCRIPTS)
        for fname in os.listdir(base):
            if not fname.endswith(".py"):
                continue
            src = open(os.path.join(base, fname), encoding="utf-8").read()
            for m in _re.finditer(r"mkdtemp\([^)]*\)|mkstemp\([^)]*\)", src):
                call = m.group(0)
                self.assertIn("skix-", call, f"{fname}: {call} 缺 skix- 前缀")


    def test_8_platform_attribution_single_source(self):
        """⑧ 归属判定必须统一走 _platOf()——分组和过滤不得内联归属逻辑。"""
        import re as _re
        tpl = os.path.join(SCRIPTS, "..", "templates", "dashboard_template.html")
        src = open(tpl, encoding="utf-8").read()
        # buildPlatformTree 必须调 _platOf（分组归属走统一函数）
        tree_def = _re.search(r"function buildPlatformTree.*?\n\}", src, _re.S)
        assert tree_def, "buildPlatformTree not found"
        self.assertIn("_platOf(d)", tree_def.group(0),
                      "buildPlatformTree 必须调 _platOf 做归属，不得内联")
        # 节点过滤必须调 _platOf
        filter_hit = _re.search(
            r'currentPlat\.type === "platform" && _platOf\(d\)', src)
        self.assertIsNotNone(filter_hit,
                             "节点过滤必须调 _platOf，不得内联归属逻辑")
        # _platOf 必须包含 variant_of 继承链
        self.assertIn("d.variant_of", src,
                      "_platOf 必须沿 variant_of 回溯变体归属")


if __name__ == "__main__":
    unittest.main()
