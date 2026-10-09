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
                     "available_skills.example.json", "platform_connectors.json",
                     "d3.v7.min.js", "TEST-EVIDENCE.md"}
        self.assertEqual(set(paths.LEGACY_OUTPUT_FILES) & PROTECTED, set())
        self.assertEqual(set(paths.RUNTIME_INPUT_FILES) & PROTECTED, set())
        for d in ("docs", "references", "templates", "tests", "scripts", "inputs"):
            self.assertNotIn(d, paths.CLEAN_DIR_SWEEP)
        # 交付资产保护清单：必须存在、且与任何删除名单零交集（防"保护名单本身被污染"）
        self.assertTrue(paths.CLEAN_PROTECTED_FILES, "保护清单不能为空")
        self.assertEqual(
            set(paths.CLEAN_PROTECTED_FILES)
            & (set(paths.LEGACY_OUTPUT_FILES) | set(paths.RUNTIME_INPUT_FILES)),
            set(), "保护清单内的交付资产绝不允许进入任何删除名单")

    def test_6c_cli_lifecycle_default_is_none(self):
        """⑨c CLI 层三态回归（第 5 轮评标实证的 bug）：--lifecycle/--graph 共用 dest，
        --lifecycle 的 store_true 默认 False 先占位 → 裸跑被当成"强制图谱"，
        意图识别在 CLI 路径上成死代码。裸跑必须是 None（自动识别），
        --lifecycle=True、--graph=False 各归各位。函数层单测测不到这里——
        本测试锁 argparse 层。"""
        import build_index as bi
        p = bi._build_parser()
        bare = p.parse_args(["pipeline", "某任务"])
        self.assertIsNone(bare.force_lifecycle, "裸跑默认必须是 None（自动识别），不得被 False 顶位")
        on = p.parse_args(["pipeline", "--lifecycle", "某任务"])
        self.assertTrue(on.force_lifecycle)
        off = p.parse_args(["pipeline", "--graph", "某任务"])
        self.assertFalse(off.force_lifecycle)

    def test_6b_pack_carries_evidence_dirs(self):
        """⑥b 参赛包必须携带评标证据目录：tests/（测试跑通证据）与 docs/
        （演进 PRD）不得离开 pack 白名单/进入排除名单，conftest.py 同理
        （pytest 依赖）——2026-10-08 用户发现参赛 zip 缺 14 个文件后确立。"""
        import build_index as bi
        self.assertNotIn("tests", bi.PACK_EXCLUDE_DIRS)
        self.assertNotIn("docs", bi.PACK_EXCLUDE_DIRS)
        self.assertIn("tests", bi.PACK_DIRS)
        self.assertIn("docs", bi.PACK_DIRS)
        self.assertIn("conftest.py", bi.PACK_TOP_FILES)
        self.assertNotIn("conftest.py", bi.PACK_EXCLUDE_FILES)
        self.assertIn("output", bi.PACK_EXCLUDE_DIRS)  # 运行产物照旧排除

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
        """⑧ 归属判定必须统一走 _platOf()——inspector chips 和节点过滤不得内联归属逻辑。"""
        import re as _re
        tpl = os.path.join(SCRIPTS, "..", "templates", "dashboard_template.html")
        src = open(tpl, encoding="utf-8").read()
        # _platOf 必须存在（归属判定唯一真源）
        self.assertIn("function _platOf", src, "_platOf 必须存在")
        # _platOf 必须包含 variant_of 继承链
        self.assertIn("d.variant_of", src, "_platOf 必须沿 variant_of 回溯变体归属")
        # 节点过滤必须调 _platOf
        filter_hit = _re.search(r'currentPlat\.type === "platform".*?_platOf\(d\)', src)
        self.assertIsNotNone(filter_hit,
                             "节点过滤必须调 _platOf，不得内联归属逻辑")
        # inspector chips 必须调 setPlatFilter
        self.assertIn('onclick="setPlatFilter', src,
                      "inspector 的 filter-chip 必须调 setPlatFilter")


if __name__ == "__main__":
    unittest.main()
