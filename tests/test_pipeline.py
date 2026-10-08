#!/usr/bin/env python3
# test_pipeline.py - 技能串联编排引擎单元测试（图谱拓扑驱动版）
# 2026-09-27 重写：pipeline 不再有 LIFECYCLES 模板/角色表/生命周期检测，
# 阶段由 depends_on 拓扑分层动态生成；无依赖边时诚实输出并行候选。
import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pipeline
import route


def _dep(src, tgt, weight=0.8):
    return {"source": f"skill:{src}", "target": f"skill:{tgt}",
            "type": "depends_on", "direction": "directed", "weight": weight}


class TestPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import paths
        import proactive
        cls.orig_profile_data = None
        if os.path.isfile(paths.PROFILE_FILE):
            with open(paths.PROFILE_FILE, "r", encoding="utf-8") as f:
                cls.orig_profile_data = f.read()
        cls.orig_mem_data = None
        mem_path = proactive.get_memory_file()
        if os.path.isfile(mem_path):
            with open(mem_path, "r", encoding="utf-8") as f:
                cls.orig_mem_data = f.read()
        cls.orig_corr_data = None
        corr_path = proactive.get_corrections_file()
        if os.path.isfile(corr_path):
            with open(corr_path, "r", encoding="utf-8") as f:
                cls.orig_corr_data = f.read()

    @classmethod
    def tearDownClass(cls):
        import paths
        import proactive
        if cls.orig_profile_data is not None:
            with open(paths.PROFILE_FILE, "w", encoding="utf-8") as f:
                f.write(cls.orig_profile_data)
            paths._PROFILE_CACHE["key"] = None
        mem_path = proactive.get_memory_file()
        if cls.orig_mem_data is not None:
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(cls.orig_mem_data)
        elif os.path.isfile(mem_path):
            os.remove(mem_path)
        corr_path = proactive.get_corrections_file()
        if cls.orig_corr_data is not None:
            with open(corr_path, "w", encoding="utf-8") as f:
                f.write(cls.orig_corr_data)
        elif os.path.isfile(corr_path):
            os.remove(corr_path)

    # ── 去硬编码回归：模板机制必须已彻底移除 ──────────────────────────────────
    def test_no_hardcoded_lifecycle_remnants(self):
        """LIFECYCLES / 角色模板 / 生命周期关键词投票 / 模板兜底 一律不得残留。"""
        for forbidden in ("LIFECYCLES", "detect_lifecycle_type",
                          "_match_skill_for_stage", "run_pipeline"):
            self.assertFalse(hasattr(pipeline, forbidden),
                             f"pipeline.{forbidden} 必须删除（不得再写死模板）")

    def test_arbitrary_task_never_gets_dev_template(self):
        """与研发无关的任务（写诗）不得被套上 4 阶段研发流水线。
        图谱无 depends_on 边时只能输出并行候选或空结果。"""
        entries = [
            {"name": "poem-writer", "type": "skill",
             "description": "诗歌写作与创作：写一首关于意境主题的诗",
             "triggers": ["写诗", "诗歌"]},
        ]
        pipe = pipeline.build_pipeline("帮我写一首关于秋天的诗", entries=entries, edges=[])
        self.assertIn(pipe["mode"], ("parallel-candidates", "empty"))
        for st in pipe["stages"]:
            # 不得出现模板时代的角色词
            self.assertNotIn("需求拆解与架构规划", st["stage_title"])
            self.assertNotIn("反AI味", st["stage_title"])

    # ── 拓扑驱动契约 ────────────────────────────────────────────────────────
    def test_topological_ordering_and_support_expansion(self):
        """depends_on 的被依赖方先执行；种子依赖的支撑技能自动纳入上游阶段。"""
        entries = [
            {"name": "app-builder", "type": "skill",
             "description": "构建应用与搭建应用脚手架", "triggers": ["构建", "应用"]},
            {"name": "lib-core", "type": "skill",
             "description": "核心数据结构工具库", "triggers": ["数据"]},
        ]
        edges = [_dep("app-builder", "lib-core")]
        pipe = pipeline.build_pipeline("构建应用", entries=entries, edges=edges)
        self.assertEqual(pipe["mode"], "topological")
        # lib-core 不是种子（检索不命中），但被 app-builder depends_on → 纳入上游
        self.assertEqual(pipe["stages"][0]["skill_names"], ["lib-core"])
        self.assertIn("app-builder", pipe["stages"][1]["skill_names"])

    def test_parallel_candidates_when_no_dep_edges(self):
        """图谱无依赖边 → 诚实输出并行候选（单阶段、不伪造时序）。"""
        entries = [
            {"name": "web-design", "type": "skill", "description": "网页前端页面设计与响应式UI", "triggers": ["网页", "设计"]},
            {"name": "canvas-game-render", "type": "skill", "description": "网页Canvas渲染与游戏动效", "triggers": ["渲染", "游戏", "网页"]},
            {"name": "code-review", "type": "skill", "description": "前端代码质量审查", "triggers": ["审查"]},
        ]
        pipe = pipeline.build_pipeline("网页游戏设计与渲染", entries=entries, edges=[])
        self.assertEqual(pipe["mode"], "parallel-candidates")
        self.assertEqual(len(pipe["stages"]), 1)
        self.assertTrue(pipe["stages"][0]["parallel"])
        self.assertIn("并行候选", pipe["stages"][0]["stage_title"])
        self.assertIn("web-design", pipe["skills"])
        self.assertIn("canvas-game-render", pipe["skills"])

    def test_cycle_dependency_reported_not_hung(self):
        """循环依赖：分层不死循环，环上节点并入最后一层并如实告警。"""
        entries = [
            {"name": "alpha-tool", "type": "skill", "description": "甲工具与乙工具协同", "triggers": ["协同"]},
            {"name": "beta-tool", "type": "skill", "description": "乙工具与甲工具协同", "triggers": ["协同"]},
        ]
        edges = [_dep("alpha-tool", "beta-tool"), _dep("beta-tool", "alpha-tool")]
        pipe = pipeline.build_pipeline("甲乙工具协同", entries=entries, edges=edges)
        self.assertEqual(pipe["mode"], "topological")
        self.assertTrue(any("循环依赖" in n for n in pipe["notes"]))

    def test_empty_index_honest_empty_mode(self):
        """检索无命中 → mode=empty、零阶段、不硬凑通用流水线。"""
        pipe = pipeline.build_pipeline("不存在的领域xyzzy", entries=[], edges=[], _retried=True)
        self.assertEqual(pipe["mode"], "empty")
        self.assertEqual(pipe["stages"], [])
        self.assertEqual(pipe["skills"], [])

    def test_output_format(self):
        entries = [
            {"name": "app-builder", "type": "skill", "description": "构建应用与搭建应用", "triggers": ["构建"]},
            {"name": "lib-core", "type": "skill", "description": "核心数据结构工具库", "triggers": ["数据"]},
        ]
        pipe = pipeline.build_pipeline("构建应用", entries=entries, edges=[_dep("app-builder", "lib-core")])
        md = pipeline.format_pipeline_markdown(pipe)
        self.assertIn("```mermaid", md)
        self.assertIn("flowchart LR", md)
        self.assertIn("阶段协同与上下文传递矩阵", md)
        self.assertIn("[skill-gateway] 技能串联编排:", md)

    def test_footnote_text_modes(self):
        entries = [{"name": "solo-skill", "type": "skill", "description": "独立技能无依赖", "triggers": ["独立"]}]
        foot_par = pipeline.footnote_text(
            pipeline.build_pipeline("独立技能", entries=entries, edges=[]))
        self.assertIn("并行候选", foot_par)
        foot_empty = pipeline.footnote_text(
            pipeline.build_pipeline("不存在xyzzy", entries=[], edges=[], _retried=True))
        self.assertIn("未生成流水线", foot_empty)

    def test_lifecycle_footnote_is_nomination_not_dispatch(self):
        """生命周期脚注是提名留痕不是调用记录：必须带"编排提名"与"提名≠调用"标记，
        实际采纳归执行 Agent 台账（用户规则：只显示实际调用的）。"""
        entries = [
            {"name": "plan", "type": "skill", "description": "planning 规划",
             "path": "", "triggers": []},
            {"name": "tdd", "type": "skill", "description": "测试 tdd test",
             "path": "", "triggers": []},
        ]
        pipe = pipeline.build_pipeline("开发实现 planning 规划 test 测试",
                                        entries=entries, edges=[], lifecycle=True)
        if pipe["mode"] == "lifecycle":
            foot = pipeline.footnote_text(pipe)
            self.assertIn("编排提名", foot)
            self.assertIn("提名≠调用", foot)
            self.assertIn("台账", foot)

    # ── 检索质量回归（原测试保留） ──────────────────────────────────────────
    def test_route_composite_task(self):
        cmd, hits = route.route("开发一个用户认证需求")
        self.assertEqual(cmd, "pipeline")

        cmd2, hits2 = route.route("把从规划到代码审查的技能串联起来")
        self.assertEqual(cmd2, "pipeline")

        cmd3, hits3 = route.route("合同双签后的后续流程是")
        self.assertEqual(cmd3, "chat")

    def test_retrieval_quota_and_pruning(self):
        import retrieval

        mock_entries = [
            {
                "name": f"skill_{i}",
                "type": "skill",
                "description": f"描述 {i} 代码 开发 规划 审查 测试 模块",
                "triggers": ["代码", "开发"],
            }
            for i in range(50)
        ]
        # 1. 验证硬顶配额不超过 20
        hits = retrieval.search(mock_entries, "代码 开发 规划 审查 测试", top=100)
        self.assertLessEqual(len(hits), retrieval.MAX_RETRIEVAL_TOP)

        # 2. 验证长尾低分剪枝：所有命中项得分均满足门槛
        for score, e, _ in hits:
            self.assertGreaterEqual(score, retrieval.MIN_RELEVANCE_SCORE)

    def test_single_char_query_fallback(self):
        """单字查询走字面子串兜底，不再永远空结果。"""
        import retrieval
        entries = [
            {"name": "pdf-tools", "type": "skill", "description": "PDF 处理工具", "triggers": ["pdf"]},
            {"name": "p", "type": "skill", "description": "字母P技能", "triggers": ["字母"]},
        ]
        hits = retrieval.search(entries, "p", top=5)
        self.assertTrue(len(hits) >= 1)
        self.assertEqual(hits[0][1]["name"], "p")  # 名字命中优先

    def test_depends_on_word_boundary(self):
        """depends_on 建边用词边界：code 不得命中 codebase、pdf 不得命中 pdfs。"""
        import retrieval
        entries = [
            {"name": "code", "type": "skill", "description": "编码技能", "triggers": ["编码"]},
            {"name": "writer", "type": "skill",
             "description": "管理 codebase 与 pdfs 文档的写作工具", "triggers": ["写作"]},
        ]
        edges = retrieval.build_edges(entries)
        dep_pairs = [(e["source"], e["target"]) for e in edges if e["type"] == "depends_on"]
        self.assertFalse(any(t == "skill:code" for _, t in dep_pairs),
                         "子串匹配假边：code 不应因 codebase 被依赖")
        self.assertFalse(any(t == "skill:pdf" for _, t in dep_pairs))

    def test_skill_exclusion(self):
        import paths
        import retrieval

        mock_entries = [
            {"name": "plan", "type": "skill", "description": "规划技能", "triggers": ["plan", "规划"]},
            {"name": "feature-dev", "type": "skill", "description": "特性开发", "triggers": ["develop", "开发"]},
        ]
        # 1. CLI 临时排除
        hits_normal = retrieval.search(mock_entries, "plan", top=5)
        hit_names = [e["name"] for _, e, _ in hits_normal]
        self.assertIn("plan", hit_names)

        hits_excluded = retrieval.search(mock_entries, "plan", top=5, cli_excludes=["plan"])
        hit_excluded_names = [e["name"] for _, e, _ in hits_excluded]
        self.assertNotIn("plan", hit_excluded_names)

        # 2. 通配符匹配测试
        self.assertTrue(paths.is_skill_excluded("test-demo", cli_excludes=["test-*"])[0])
        self.assertFalse(paths.is_skill_excluded("feature-dev", cli_excludes=["test-*"])[0])

    def test_auto_update_on_miss(self):
        import index_store

        entries, edges, st, has_changes = index_store.auto_update_on_miss(
            query="test_query", targeted=True
        )
        self.assertIsInstance(entries, list)
        self.assertIsInstance(edges, list)
        self.assertIn("kept", st)

    # ── 检索质量回归（2026-09-30 位置加权 + 名称提权 + 历史事故） ────────────
    def test_name_hit_beats_generic_gram_stack(self):
        """2026-09-30 真库事故等比回归：IDF_CAP 把真领域词（df=3）与泛化碎片
        （df=6-19）拉平成同一权重后，无关技能靠堆描述碎片（失败+排查+自动）压过
        名称命中的正解（实测 salesforce-develop 28.5 分被挤到 #21 开外、top 8 看不见）。
        位置加权 + 名称按稀有度提权后，正解必须回到 top 3 且排第一。"""
        import retrieval

        entries = []
        # 散族 filler：8 条共享 失败/排查（df 小 → raw IDF 高 → 被封顶），其余共享 自动/生成
        for i in range(250):
            if i < 8:
                desc = f"模块{i}运行失败 自动生成报告 排查思路{i}"
            else:
                desc = f"模块{i} 自动生成报告 内容{i}"
            entries.append(
                {"name": f"filler-{i}", "type": "skill", "description": desc, "triggers": []}
            )
        entries.append({
            "name": "salesforce-develop",
            "type": "skill",
            "description": ("Salesforce development workflow and guardrails. Apex classes, "
                            "triggers, controllers, schedulers, object fields, approval-related logic."),
            "triggers": ["salesforce"],
        })
        query = "Salesforce RC_ContractTrigger 合同自动生成失败 FIELD_CUSTOM_VALIDATION 报错排查"
        hits = retrieval.search(entries, query, top=10)
        top3 = [e["name"] for _, e, _ev in hits[:3]]
        self.assertIn("salesforce-develop", top3,
                      "名称命中的正解被堆描述碎片的垃圾挤出 top 3")
        self.assertEqual(hits[0][1]["name"], "salesforce-develop")

    def test_name_bonus_scales_with_rarity(self):
        """名称命中加分按命中词稀有度缩放：同一名称命中机制下，命中稀有词（df=1）
        的条目得分要显著高于命中满库常见词的条目（固定 +3 时代两者差距被压平）。
        同一条目池、两次检索，唯一变量是名称命中词的稀有度。"""
        import retrieval

        entries = [
            {"name": "zetaquartz", "type": "skill",
             "description": "zetaquartz 专用工具", "triggers": []},
            {"name": "config-helper", "type": "skill",
             "description": "config file helper", "triggers": []},
        ]
        entries += [
            {"name": f"pad-{i}", "type": "skill",
             "description": f"config file {i}", "triggers": []}
            for i in range(60)
        ]
        rare_hits = retrieval.search(entries, "zetaquartz", top=3)
        common_hits = retrieval.search(entries, "config", top=3)
        self.assertTrue(rare_hits and common_hits)
        self.assertEqual(rare_hits[0][1]["name"], "zetaquartz")
        self.assertEqual(common_hits[0][1]["name"], "config-helper")
        self.assertGreater(
            rare_hits[0][0], common_hits[0][0] * 5,
            "稀有词名称命中与常见词名称命中的加分差距未拉开",
        )

    def test_single_rare_word_evidence_bounded(self):
        """2026-09-25 龙易查 6298 分假第一回归：单个稀有词的证据贡献有 IDF_CAP 上限，
        不该独自扛起整个相关度；单概念描述命中仍要可见（查全率保留）。
        本夹具的命中是"仅描述"证据（无名称命中加分），故上界 = IDF_CAP×LOC_DESC+0.5。"""
        import retrieval

        entries = [
            {"name": "longfox-onboarding", "type": "skill",
             "description": "龙易查 入职引导 使用说明", "triggers": []},
        ]
        entries += [
            {"name": f"pad-{i}", "type": "skill",
             "description": f"其他内容{i} 使用说明", "triggers": []}
            for i in range(30)
        ]
        hits = retrieval.search(entries, "龙易查", top=5)
        self.assertTrue(hits, "单概念描述命中被过滤，查全率受损")
        for score, _e, _ev in hits:
            self.assertLessEqual(
                score,
                retrieval.IDF_CAP * retrieval.LOC_DESC + 0.5,
                "单个稀有词证据贡献超出上限，假第一风险回归",
            )

    def test_maximal_evidence_groups_adjacent_fragments(self):
        """2026-09-28 wps-knowledgebase 75.5 分假命中回归："本地仓库"拆成的相邻 gram
        （本地仓/地仓库/仓库里…）互不包含却是同一词组的碎片，只计一份证据。"""
        import retrieval

        query = "本地仓库里的配置"
        overlap = retrieval.terms(query) & retrieval.terms("本地仓库配置说明")
        maximal = retrieval._maximal_evidence(overlap, query)
        self.assertLessEqual(
            len(maximal), max(1, len(overlap) // 2),
            f"同源碎片未归组：{sorted(overlap)} → {sorted(maximal)}",
        )

    def test_high_freq_combo_filtered_by_gate(self):
        """"标签+当前"高频凑数回归：全库高频词组合的证据 IDF 合计低于门槛时被过滤。
        注意：本测试同时钉住 LOC_DESC 与 EVIDENCE_MIN_WEIGHT 的耦合
        （ev ≈ 1.01 vs 门槛 1.23，LOC_DESC 抬到 ≥0.62 会翻转）。"""
        import retrieval

        entries = [
            {"name": "wps-knowledgebase", "type": "skill",
             "description": "知识库 标签 当前 文档管理", "triggers": []},
        ]
        entries += [
            {"name": f"pad-{i}", "type": "skill",
             "description": f"知识库 标签 当前 文档{i}", "triggers": []}
            for i in range(40)
        ]
        hits = retrieval.search(entries, "标签 当前", top=10)
        self.assertEqual(hits, [], "高频词组合凑数未被门槛拦截")

    # ── Profile / 画像（原测试保留） ─────────────────────────────────────────
    def test_skill_profile_loading(self):
        import paths

        prof = paths.load_skill_profile()
        self.assertIsInstance(prof, dict)
        self.assertIn("pinned_skills", prof)
        self.assertIn("user_directives", prof)

        directives = paths.get_user_directives()
        self.assertIsInstance(directives, list)

        pinned = paths.get_pinned_skills()
        self.assertIsInstance(pinned, set)

    def test_profile_pinned_skills_boost(self):
        import retrieval
        import paths

        entries = [
            {"name": "ordinary-plan", "type": "skill", "description": "普通规划技能", "triggers": ["plan"]},
            {"name": "plan", "type": "skill", "description": "用户置顶规划技能", "triggers": ["plan"]},
        ]
        old_cache = paths._PROFILE_CACHE["obj"]
        old_key = paths._PROFILE_CACHE["key"]
        try:
            cur_mt = os.path.getmtime(paths.PROFILE_FILE) if os.path.isfile(paths.PROFILE_FILE) else None
            paths._PROFILE_CACHE["key"] = (paths.PROFILE_FILE, cur_mt)
            paths._PROFILE_CACHE["obj"] = {"pinned_skills": ["plan"]}
            hits = retrieval.search(entries, "plan", top=5)
            self.assertGreater(len(hits), 0)
            # 命中第一名应为置顶提权的 plan
            top_hit = hits[0][1]
            self.assertEqual(top_hit["name"], "plan")
            self.assertTrue(top_hit.get("is_pinned"))
            # 原始共享条目不得被污染 is_pinned 展示态
            self.assertNotIn("is_pinned", entries[1])
        finally:
            paths._PROFILE_CACHE["key"] = old_key
            paths._PROFILE_CACHE["obj"] = old_cache

    def test_profile_directives_in_pipeline(self):
        import paths

        old_cache = paths._PROFILE_CACHE["obj"]
        old_key = paths._PROFILE_CACHE["key"]
        try:
            cur_mt = os.path.getmtime(paths.PROFILE_FILE) if os.path.isfile(paths.PROFILE_FILE) else None
            paths._PROFILE_CACHE["key"] = (paths.PROFILE_FILE, cur_mt)
            paths._PROFILE_CACHE["obj"] = {
                "user_directives": ["测试全局指令"],
                "pinned_skills": ["plan"],
            }
            entries = [
                {"name": "plan", "type": "skill", "description": "规划认证需求与架构", "triggers": ["规划", "认证"]},
                {"name": "audit-check", "type": "skill", "description": "认证流程审查", "triggers": ["审查", "认证"]},
            ]
            pipe = pipeline.build_pipeline("规划认证需求", entries=entries, edges=[])
            md = pipeline.format_pipeline_markdown(pipe)
            self.assertIn("用户重要指令", md)
            self.assertIn("User Directives", md)
            # 常用置顶技能在矩阵中应带有置顶标记
            self.assertIn("★(常用置顶)", md)
        finally:
            paths._PROFILE_CACHE["key"] = old_key
            paths._PROFILE_CACHE["obj"] = old_cache

    def test_profile_fixed_roots_override(self):
        import paths

        old_cache = paths._PROFILE_CACHE["obj"]
        old_key = paths._PROFILE_CACHE["key"]
        try:
            cur_mt = os.path.getmtime(paths.PROFILE_FILE) if os.path.isfile(paths.PROFILE_FILE) else None
            paths._PROFILE_CACHE["key"] = (paths.PROFILE_FILE, cur_mt)
            paths._PROFILE_CACHE["obj"] = {
                "fixed_roots": ["/custom/agent/skills"],
                "only_fixed_roots": True,
            }
            self.assertTrue(paths.only_dirs())
            roots = paths.candidate_roots()
            tags = [tag for _, tag in roots]
            self.assertIn("profile:fixed", tags)
            # 严格模式下不应包含 self 或 default
            self.assertNotIn("self", tags)
            self.assertNotIn("default", tags)
        finally:
            paths._PROFILE_CACHE["key"] = old_key
            paths._PROFILE_CACHE["obj"] = old_cache

    # ── Proactive（原测试保留） ──────────────────────────────────────────────
    def test_proactive_fingerprint(self):
        import proactive

        fp1 = proactive.get_roots_fingerprint()
        self.assertIsInstance(fp1, str)
        self.assertTrue(len(fp1) > 0)
        fp2 = proactive.get_roots_fingerprint()
        self.assertEqual(fp1, fp2)

    def test_proactive_decision_gates(self):
        import proactive
        import time

        # 1. 禁用配置测试
        cfg_disabled = {"enabled": False}
        should, reason, _ = proactive.evaluate_update_need(cfg=cfg_disabled)
        self.assertFalse(should)
        self.assertIn("禁用", reason)

        # 2. 物理指纹变动门槛测试
        state_old = {
            "last_roots_fingerprint": "fake_old_fingerprint",
            "last_check_time": time.time(),
        }
        cfg_enabled = {"enabled": True, "interval_seconds": 300}
        should, reason, _ = proactive.evaluate_update_need(
            state=state_old, cfg=cfg_enabled
        )
        self.assertTrue(should)
        self.assertIn("物理指纹变动", reason)

        # 3. 时间窗口心跳门槛测试
        cur_fp = proactive.get_roots_fingerprint()
        state_ttl_expired = {
            "last_roots_fingerprint": cur_fp,
            "last_check_time": time.time() - 400,
            "interaction_count": 5,
        }
        should, reason, _ = proactive.evaluate_update_need(
            state=state_ttl_expired, cfg=cfg_enabled
        )
        self.assertTrue(should)
        self.assertIn("心跳检测周期到期", reason)

        # 4. 状态新鲜未过期测试
        state_fresh = {
            "last_roots_fingerprint": cur_fp,
            "last_check_time": time.time() - 10,
            "interaction_count": 1,
        }
        should, reason, _ = proactive.evaluate_update_need(
            state=state_fresh, cfg=cfg_enabled
        )
        self.assertFalse(should)

        # 5. 上下文强制触发测试
        should, reason, _ = proactive.evaluate_update_need(
            context={"force_update": True}, state=state_fresh, cfg=cfg_enabled
        )
        self.assertTrue(should)
        self.assertIn("显式请求", reason)

    def test_proactive_usage_semantics(self):
        """计频口径回归：检索命中（hits）不算使用；只有显式 skills 才计频。"""
        import proactive

        mock_state = {"interaction_count": 0, "skill_frequency": {}, "promoted_skills": []}
        mock_cfg = {"enabled": True, "auto_promote_frequent_skills": True, "frequent_threshold": 1}
        fake_hits = [(9.9, {"name": "shown-only"}, [])] * 3
        # 只传 hits（旧口径）：不得计频
        proactive.record_interaction("search", context={"hits": fake_hits},
                                     state=mock_state, cfg=mock_cfg)
        self.assertEqual(mock_state["skill_frequency"], {})
        # 显式使用（skills）：计频且达到阈值即晋升
        promoted = proactive.record_interaction("detail", context={"skills": ["real-use"]},
                                                 state=mock_state, cfg=mock_cfg)
        self.assertEqual(mock_state["skill_frequency"]["real-use"], 1)
        self.assertIn("real-use", promoted)

    def test_proactive_frequency_and_promotion(self):
        import paths
        import proactive
        import time

        test_skill = f"test-helper-{int(time.time()*1000)}"
        mock_state = {
            "interaction_count": 0,
            "skill_frequency": {test_skill: 2},
            "promoted_skills": [],
        }
        mock_cfg = {
            "enabled": True,
            "auto_promote_frequent_skills": True,
            "frequent_threshold": 3,
        }

        orig_profile = paths.load_skill_profile().copy()
        try:
            # 第 3 次交互，触发晋升
            promoted = proactive.record_interaction(
                "chat",
                context={"skills": [test_skill]},
                state=mock_state,
                cfg=mock_cfg,
            )
            self.assertEqual(mock_state["skill_frequency"][test_skill], 3)
            self.assertIn(test_skill, promoted)
            # 验证已被持久化到 profile 中
            pinned = paths.get_pinned_skills()
            self.assertIn(test_skill, pinned)
        finally:
            # 恢复 profile
            prof_path = paths.PROFILE_FILE
            if os.path.isfile(prof_path):
                with open(prof_path, "w", encoding="utf-8") as f:
                    json.dump(orig_profile, f, ensure_ascii=False, indent=2)
                paths._PROFILE_CACHE["key"] = None

    def test_update_profile_and_memory(self):
        import paths
        import proactive

        orig_profile = paths.load_skill_profile().copy()
        try:
            res = proactive.update_profile_and_memory(
                set_roots=["/test/agent/skills"],
                only_fixed=True,
                add_directives=["测试执行前必须进行规划审查"],
                reason="单元测试动态更新画像与记忆",
            )
            self.assertTrue(res["success"])
            self.assertTrue(res["modified"])
            self.assertEqual(res["prof"]["fixed_roots"], ["/test/agent/skills"])
            self.assertTrue(res["prof"]["only_fixed_roots"])
            self.assertIn("测试执行前必须进行规划审查", res["prof"]["user_directives"])

            # 验证 output/memory.md 已同步
            mem_file = proactive.get_memory_file()
            self.assertTrue(os.path.isfile(mem_file))
            with open(mem_file, "r", encoding="utf-8") as f:
                mem_content = f.read()
            self.assertIn("/test/agent/skills", mem_content)
            self.assertIn("Enabled", mem_content)

            # 验证 output/corrections.md 已记录纠偏
            corr_file = proactive.get_corrections_file()
            self.assertTrue(os.path.isfile(corr_file))
            with open(corr_file, "r", encoding="utf-8") as f:
                corr_content = f.read()
            self.assertIn("单元测试动态更新画像与记忆", corr_content)
        finally:
            prof_path = paths.PROFILE_FILE
            if os.path.isfile(prof_path):
                with open(prof_path, "w", encoding="utf-8") as f:
                    json.dump(orig_profile, f, ensure_ascii=False, indent=2)
                paths._PROFILE_CACHE["key"] = None

    # ── 协同联动与自省（原测试保留） ─────────────────────────────────────────
    def test_collaborative_neighbors_and_linkage(self):
        """测试即使图谱静态边为空，也能基于领域语义自动推导协同联动技能。"""
        import retrieval
        entries = [
            {"name": "web-design", "type": "skill", "description": "网页前端页面设计与响应式UI组件", "triggers": ["网页", "设计"]},
            {"name": "canvas-game-render", "type": "skill", "description": "网页Canvas高性能渲染引擎与游戏动效设计", "triggers": ["渲染", "游戏", "网页"]},
            {"name": "code-review", "type": "skill", "description": "前端与全栈代码质量审查与规范", "triggers": ["审查", "规范"]},
        ]
        target_skill = entries[0]  # web-design
        collabs = retrieval.get_collaborative_neighbors(entries, target_skill)
        self.assertTrue(len(collabs) > 0)
        collab_names = [c["name"] for c in collabs]
        self.assertIn("canvas-game-render", collab_names)

    def test_pipeline_borrows_global_search_and_avoids_vendor_mismatch(self):
        """任务检索沿用全图谱打分：网页游戏任务命中 web-design，绝不相干技能不入选。"""
        test_entries = [
            {"name": "arch-planner", "type": "skill", "description": "系统架构规划与需求拆解", "triggers": ["规划", "架构"]},
            {"name": "web-design", "type": "skill", "description": "网页前端页面设计与HTML5 Canvas游戏渲染", "triggers": ["网页", "设计", "游戏"]},
            {"name": "salesforce-develop", "type": "skill", "description": "Salesforce CRM apex backend develop", "triggers": ["salesforce", "crm"]},
            {"name": "ai-slop-cleaner", "type": "skill", "description": "代码冗余清理与优化", "triggers": ["清理", "精简"]},
            {"name": "code-review", "type": "skill", "description": "代码质量审查", "triggers": ["审查", "质检"]},
        ]
        task = "开发网页贪吃蛇游戏"
        pipe = pipeline.build_pipeline(task, entries=test_entries, edges=[])
        self.assertIn("web-design", pipe["skills"])
        # 绝不命中毫不相干的 salesforce-develop
        self.assertNotIn("salesforce-develop", pipe["skills"])

    def test_pipeline_rejects_irrelevant_stage_fill(self):
        """回归（2026-09-25）：无关技能（描述堆满"设计/方案/诊断/检查"）不得混进编排。"""
        entries = [
            {"name": "tchouse-ddl", "type": "skill",
             "description": "根据业务场景设计方案，设计分区策略与排序键，支持诊断、优化、"
                            "修复方案与迁移方案设计，覆盖检查与验证，含索引设计与表结构优化",
             "triggers": ["建表", "ddl"]},
            {"name": "arch-planner", "type": "skill",
             "description": "用户认证与权限系统架构规划", "triggers": ["规划", "架构"]},
            {"name": "code-review", "type": "skill",
             "description": "用户认证代码质量审查", "triggers": ["审查"]},
        ]
        task = "开发一个用户认证需求"
        pipe = pipeline.build_pipeline(task, entries=entries, edges=[])
        self.assertNotIn("tchouse-ddl", pipe["skills"],
                         "无关技能靠描述里的职责动词子串混进编排")
        self.assertIn("arch-planner", pipe["skills"])
        self.assertIn("code-review", pipe["skills"])

    def test_dynamic_introspection_and_zero_hardcoding(self):
        """测试彻底删除硬编码字典：验证不存在 KNOWN_CONNECTOR_TITLES/KNOWN_TRIGGERS，且支持纯动态自省。"""
        import scanner

        # 1. 验证绝对没有任何写死字典
        self.assertFalse(hasattr(scanner, "KNOWN_CONNECTOR_TITLES"), "严禁残留 KNOWN_CONNECTOR_TITLES")
        self.assertFalse(hasattr(scanner, "KNOWN_TRIGGERS"), "严禁残留 KNOWN_TRIGGERS")

        # 2. 标识符算法切分测试 (Tokenization)
        tokens = scanner._tokenize_identifier("FinanceAuditWorker-v2")
        self.assertIn("finance", tokens)
        self.assertIn("audit", tokens)
        self.assertIn("worker", tokens)

        # 3. 动态数字员工自省测试
        clean_name, desc, trigs = scanner._introspect_entity_metadata(
            "agent:compliance-auditor",
            {"role": "代码合规审计员", "prompt": "负责对企业内部代码进行安全与许可协议审计。"},
            cfg_path="",
            is_connector=False,
            is_agent=True,
        )
        self.assertEqual(clean_name, "compliance-auditor")
        self.assertIn("【数字员工】", desc)
        self.assertIn("代码合规审计员", desc)

        # 4. 纯动态触发词合成测试（无硬编码字典）
        dyn_trigs = scanner._synthesize_triggers(clean_name, desc, [])
        self.assertIn("compliance-auditor", dyn_trigs)
        self.assertIn("auditor", dyn_trigs)

        # 5. 未知连接器协议自省测试
        c_name, c_desc, c_trigs = scanner._introspect_entity_metadata(
            "connector:custom-erp",
            {"url": "https://erp.internal.company.com/mcp", "type": "streamable-http"},
            cfg_path="",
            is_connector=True,
            is_agent=False,
        )
        self.assertEqual(c_name, "custom-erp")
        self.assertIn("【连接器】", c_desc)
        self.assertIn("https://erp.internal.company.com/mcp", c_desc)


    def test_lifecycle_tie_asks_and_pref_remembers(self):
        """并列提名（词面分差≤10%）→ 网关不裁决，交执行 Agent 按任务语义裁决，
        并提示可 --stage-pref 长期锁定；有记忆偏好 → 自动选用。"""
        entries = [
            {"name": "plan-skill", "type": "skill",
             "description": "需求分析 拆解规划 requirement analysis planning", "triggers": ["规划"]},
            {"name": "refactor-a", "type": "skill",
             "description": "重构 清理 refactor cleanup 重构治理", "triggers": ["重构"]},
            {"name": "refactor-b", "type": "skill",
             "description": "重构 清理 refactor cleanup 优化", "triggers": ["清理"]},
        ]
        stages, notes, _n = pipeline._lifecycle_stages(
            "重构 治理 refactor cleanup", entries, [], {})
        st = next(s for s in stages if s["lifecycle"] == "重构治理")
        self.assertGreaterEqual(len(st["skill_names"]), 2)  # 并列提名都列出
        self.assertTrue(any("执行 Agent 按任务语义裁决" in n for n in notes))
        self.assertTrue(any("stage-pref" in n for n in notes))
        # 记忆偏好 → 自动选用，不再并列
        stages2, notes2, _n2 = pipeline._lifecycle_stages(
            "重构 治理 refactor cleanup", entries, [], {},
            prefs={"重构治理": "refactor-a"})
        st2 = next(s for s in stages2 if s["lifecycle"] == "重构治理")
        self.assertEqual(st2["skill_names"], ["refactor-a"])
        self.assertTrue(any("已按记忆偏好" in n for n in notes2))


    def test_lifecycle_survives_autoheal_retry(self):
        """回归：query 零种子触发自愈重试时，--lifecycle 模式声明不得丢失
        （曾因重试递归丢参退回图谱模式，输出空编排）。"""
        from unittest import mock
        entries = [
            {"name": "plan", "type": "skill", "description": "planning 规划",
             "path": "", "triggers": []},
            {"name": "tdd", "type": "skill", "description": "test-driven 测试",
             "path": "", "triggers": []},
            {"name": "code-review", "type": "skill", "description": "code review 审查",
             "path": "", "triggers": []},
        ]
        with mock.patch.object(pipeline.index_store, "auto_update_on_miss",
                               return_value=(entries, [], {"added": ["x"]}, True)):
            pipe = pipeline.build_pipeline(
                "完全无匹配的查询 zzzq", entries=entries, edges=[],
                lifecycle=True)
        self.assertEqual(pipe["mode"], "lifecycle")
        self.assertTrue(len(pipe["stages"]) >= 3)


if __name__ == "__main__":
    unittest.main()
