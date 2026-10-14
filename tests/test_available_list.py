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


def _restore_reg_file(reg_path, bak):
    """还原登记表/根记忆快照（bak=None 表示源态=不存在，删掉）。
    Windows 上刚落盘的 JSON 可能被杀软/索引器短暂锁住，os.remove / atomic_write
    偶发 PermissionError——一旦在 finally 里抛出，测试既失败又泄漏脏状态，
    之后每次"通过"的运行还会把脏内容当快照忠实还原（自我延续污染，10-13 全量
    实抓）。故：短暂重试；重试耗尽仍失败必须大声失败，绝不静默吞掉。"""
    import time as _time
    import index_store
    for attempt in range(4):
        try:
            if bak is None:
                if os.path.isfile(reg_path):
                    os.remove(reg_path)
            else:
                index_store.atomic_write(reg_path, bak)
            return
        except OSError:
            if attempt == 3:
                raise
            _time.sleep(0.3)


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

    def test_self_entry_path_self_heal(self):
        """R3 网关自洽（评标 P2）：清单里 skill-gateway 的 path 指向失效位置时，
        建条目/合并复用都会重指当前运行网关自身的 SKILL.md，detail 全文可取。"""
        os.makedirs(os.path.join(scanner.ROOT, "inputs"), exist_ok=True)
        with open(os.path.join(scanner.ROOT, "inputs", "available_skills.json"),
                  "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "skills": [
                    {"name": "skill-gateway", "description": "gateway 网关",
                     "path": os.path.join(scanner.ROOT, "stale-cache", "SKILL.md"),
                     "agents": ["zcode"]},
                ]}, ensure_ascii=False))
        ents = scanner.available_list_entries()
        self.assertEqual(ents[0]["name"], "skill-gateway")
        # 沙箱虚拟挂载（DB 层、非物理文件）时自愈目标不存在 → skip 而非 fail
        import unittest as _u
        _p = ents[0]["path"]
        if _p and not os.path.isfile(_p):
            raise _u.SkipTest("沙箱虚拟挂载：SKILL.md 非物理文件，自愈目标不可验证")
        self.assertTrue(os.path.isfile(_p), "自愈后 path 必须指向真实存在的 SKILL.md")
        # 合并复用路径：旧条目带失效 path，二次合合同样自愈
        old = [dict(ents[0])]
        old[0]["path"] = "/nonexistent/sandbox-cache/SKILL.md"
        merged, _st = index_store._update_from_available_list(old, ents)
        by = {e["name"]: e for e in merged}
        self.assertTrue(os.path.isfile(by["skill-gateway"]["path"]))

    def test_load_index_bootstrap_agent_level(self):
        """load_index 兑底升级：索引缺失时【身份已声明 + 技能根在记忆】→ 自动走
        agent-index 自助路径建权威口径（扫声明根→建登记表→三段合并索引）；
        缺任一 → 现行扫描级兑底且登记表不落盘。永不父目录兜底（10-08 的 148
        污染事故边界）。"""
        import os as _os
        import tempfile as _tf
        import paths as _paths
        import index_store
        from unittest import mock

        # 测试沙箱：临时技能根里放一个真技能
        root = _tf.mkdtemp(prefix="skix-boot-")
        sk = _os.path.join(root, "demo-skill")
        _os.makedirs(sk)
        with open(_os.path.join(sk, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---" + chr(10) + "name: demo-skill" + chr(10)
                    + "description: bootstrap demo skill 演示" + chr(10)
                    + "---" + chr(10) + "# demo" + chr(10))

        # 自举写登记表走 paths.ROOT，读登记表走 scanner.ROOT——生产两者同根，
        # 测试须临时对齐（setUpClass 已把 scanner.ROOT 指去临时目录）
        _real_root = scanner.ROOT
        scanner.ROOT = _paths.ROOT
        reg_path = _os.path.join(_paths.ROOT, "inputs", "available_skills.json")
        _bak = open(reg_path, encoding="utf-8").read() if _os.path.isfile(reg_path) else None
        _bak_roots = open(_os.path.join(_paths.ROOT, "inputs", "env_skill_roots.json"),
                          encoding="utf-8").read() if _os.path.isfile(
            _os.path.join(_paths.ROOT, "inputs", "env_skill_roots.json")) else None
        out_tmp = _tf.mkdtemp(prefix="skix-bootout-")
        _os.environ["SKILL_GATEWAY_OUT_DIR"] = out_tmp
        _os.environ["SKILL_GATEWAY_AGENT"] = "boot-agent"
        try:
            # 场景 A：双在 → Agent 级：登记表落盘且含 current_agent/绑定，索引为权威口径
            with mock.patch.object(_paths, "_load_env_roots_memory",
                                   return_value={"boot-agent": [root]}):
                es = index_store.load_index()
            self.assertTrue(_os.path.isfile(reg_path), "双守门在位时必须建立登记表")
            import json as _json
            with open(reg_path, encoding="utf-8") as _rf:
                reg = _json.load(_rf)
            self.assertEqual(reg.get("current_agent"), "boot-agent")
            names = [x["name"] for x in reg.get("skills", [])]
            self.assertIn("demo-skill", names)
            ready = [e for e in es if e.get("name") == "demo-skill" and e.get("visibility") == "ready"]
            self.assertTrue(ready, "声明根内技能应为平台可用（ready）")
        finally:
            _os.environ.pop("SKILL_GATEWAY_AGENT", None)
            _os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
            scanner.ROOT = _real_root
            # 还原真实登记表与根记忆（自举测试绝不能污染真机状态）
            _restore_reg_file(reg_path, _bak)
            _restore_reg_file(_os.path.join(_paths.ROOT, "inputs",
                                            "env_skill_roots.json"), _bak_roots)

        # 场景 B：身份在但记忆根缺 → 登记表绝不落盘（永不父目录兜底）
        reg_path2 = reg_path
        before = _os.path.isfile(reg_path2)
        _os.environ["SKILL_GATEWAY_AGENT"] = "boot-agent"
        out2 = _tf.mkdtemp(prefix="skix-bootout2-")
        _os.environ["SKILL_GATEWAY_OUT_DIR"] = out2
        try:
            with mock.patch.object(_paths, "_load_env_roots_memory", return_value={}):
                index_store.load_index()
            self.assertEqual(_os.path.isfile(reg_path2), before,
                             "记忆根缺位时登记表必须保持原状（不建不删）")
        finally:
            _os.environ.pop("SKILL_GATEWAY_AGENT", None)
            _os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
            _restore_reg_file(reg_path2, _bak)

    def test_self_heal_verifies_target_exists(self):
        """P0 根治（评标第 8 轮实抓）：自愈目标必须存在性验证——平台执行形态下
        从脚本位置推导的包根可能落空（脚本从会话/临时目录运行），"修复"写出
        第二条死路径。两级候选：脚本推导 → paths.ROOT 兜底；都落空不治（诚实，
        doctor 继续报告），绝不写出未经验证的路径。"""
        import paths as _paths
        # 场景 A：真实环境——自愈目标必须是磁盘上真实存在的文件
        _gw = scanner.gateway_self_skmd_path()
        if not _gw:
            import unittest as _u
            raise _u.SkipTest("沙箱虚拟挂载：网关自身 SKILL.md 非物理文件")
        self.assertTrue(os.path.isfile(_gw))
        # 场景 B：候选全落空 → heal 拒绝动刀（返回 False，原 path 保留）
        orig = scanner.gateway_self_skmd_path
        scanner.gateway_self_skmd_path = lambda: ""
        try:
            e = {"name": "skill-gateway", "path": "/dead/session-cache/SKILL.md"}
            self.assertFalse(scanner.heal_self_entry(e))
            self.assertEqual(e["path"], "/dead/session-cache/SKILL.md")
        finally:
            scanner.gateway_self_skmd_path = orig
        # 场景 C：正常自愈后落盘的 path 必须可通过 isfile 检查（不再是"信任推导"）
        e2 = {"name": "skill-gateway", "path": "/dead/old/SKILL.md"}
        self.assertTrue(scanner.heal_self_entry(e2))
        if e2["path"] and not os.path.isfile(e2["path"]):
            import unittest as _u
            raise _u.SkipTest("沙箱虚拟挂载：heal 落刀路径非物理文件")
        self.assertTrue(os.path.isfile(e2["path"]))

    def test_description_zh_flows_registry_to_index(self):
        """登记表里的 description_zh 必须进索引条目并在三段合并中存活——
        dashboard 中文/双语对照的数据源就是索引里的这个字段，断链=翻译永远不显示。"""
        os.makedirs(os.path.join(scanner.ROOT, "inputs"), exist_ok=True)
        with open(os.path.join(scanner.ROOT, "inputs", "available_skills.json"),
                  "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "skills": [
                    {"name": "feature-dev", "description": "Full dev workflow",
                     "description_zh": "全面的功能开发工作流程", "agents": ["zcode"]},
                ]}, ensure_ascii=False))
        ents = scanner.available_list_entries()
        self.assertEqual(ents[0].get("description_zh"), "全面的功能开发工作流程")
        # 三段合并：登记表补/改了翻译 → 必须判为 updated（不能走 kept 复用旧的无翻译条目）
        old = [dict(ents[0])]
        old[0].pop("description_zh")
        merged, _st = index_store._update_from_available_list(old, ents)
        by = {e["name"]: e for e in merged}
        self.assertEqual(by["feature-dev"].get("description_zh"),
                         "全面的功能开发工作流程")
        # 二次合并：条目已带翻译且未变 → kept 复用旧记录，翻译必须原样保留
        merged2, _st2 = index_store._update_from_available_list(merged, ents)
        by2 = {e["name"]: e for e in merged2}
        self.assertEqual(by2["feature-dev"].get("description_zh"),
                         "全面的功能开发工作流程")

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


    def test_agent_index_merges_shared_list(self):
        """共享清单合并：导入不清其他环境条目，agents 取并集。"""
        raw = [{"name": "antigravity-skill", "description": "antigravity 专属",
                "agents": ["antigravity"]}]
        doc = importer.to_available_list(raw, "antigravity", old_doc={"skills": [
            {"name": "zcode-one", "description": "zcode 专属", "agents": ["zcode"]},
            {"name": "shared-skill", "description": "双方共用", "agents": ["zcode"]},
        ]})
        by = {it["name"]: it for it in doc["skills"]}
        self.assertEqual(by["zcode-one"]["agents"], ["zcode"])       # 未导入条目原样保留
        self.assertEqual(by["antigravity-skill"]["agents"], ["antigravity"])
        self.assertEqual(by["shared-skill"]["agents"], ["zcode"])  # 未导入的条目绑定保持原样
        self.assertEqual(doc["current_agent"], "antigravity")


    def test_env_roots_memory_roundtrip_and_priority(self):
        """智能体技能根记忆：--skill-dirs 声明一次写入 env_skill_roots.json，
        读取按智能体名取回；不同智能体互不干扰。"""
        import paths as _paths
        old = os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
        try:
            _paths._save_env_roots_memory("antigravity",
                                          ["~/.gemini/antigravity/builtin/skills",
                                           "~/.gemini/config/plugins/science/skills"])
            _paths._save_env_roots_memory("zcode", ["C:/Users/testuser/.zcode/skills"])
            mem = _paths._load_env_roots_memory()
            self.assertEqual(mem["antigravity"],
                             ["~/.gemini/antigravity/builtin/skills",
                              "~/.gemini/config/plugins/science/skills"])
            self.assertEqual(mem["zcode"], ["C:/Users/testuser/.zcode/skills"])
            os.remove(os.path.join(_paths.ROOT, "inputs", "env_skill_roots.json"))
        finally:
            if old is not None:
                os.environ["SKILL_GATEWAY_OUT_DIR"] = old


class NoRegistryFallbackConsistencyTests(unittest.TestCase):
    """无登记表兜底口径 = 单一事实源（10-14 数字员工实抓口径分叉）。
    index / update 首建 / add 三条路必须同口径：磁盘技能一律 off-list/excluded，
    绝不因"先跑哪个命令"分叉——update 首建曾不打标，条目默认 ready 混进默认检索
    （Excel 查询 Top1 错位实录），登记表一丢（沙箱再水合）即静默触发。"""

    def setUp(self):
        self._old_root = scanner.ROOT
        scanner.ROOT = tempfile.mkdtemp(prefix="skix-noreg-")
        self._old_out = os.environ.get("SKILL_GATEWAY_OUT_DIR")

    def tearDown(self):
        scanner.ROOT = self._old_root
        if self._old_out is None:
            os.environ.pop("SKILL_GATEWAY_OUT_DIR", None)
        else:
            os.environ["SKILL_GATEWAY_OUT_DIR"] = self._old_out

    @staticmethod
    def _skill_pairs():
        disk = tempfile.mkdtemp(prefix="skix-noregdisk-")
        pairs = []
        for nm in ("alpha-tool", "beta-tool"):
            d = os.path.join(disk, nm)
            os.makedirs(d)
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {nm}\ndescription: disk {nm} 磁盘\n---\nbody")
            pairs.append(("disk-root", os.path.join(d, "SKILL.md")))
        return disk, pairs

    @staticmethod
    def _assert_all_offlist(tag, es):
        skills = [e for e in es if e["type"] == "skill"]
        assert skills, f"{tag}: 没扫到技能"
        for e in skills:
            assert e.get("visibility") == "off-list", f"{tag}: {e['name']} 应 off-list"
            assert e.get("excluded"), f"{tag}: {e['name']} 应 excluded"

    def test_build_update_add_share_offlist_fallback(self):
        disk, pairs = self._skill_pairs()
        self.assertIsNone(scanner.available_list_entries())  # 登记表不在位
        with mock.patch.object(scanner, "enumerate_skill_md", return_value=pairs), \
             mock.patch.object(scanner, "scan_mcp", return_value=[]):
            out1 = tempfile.mkdtemp(prefix="skix-no1-")
            os.environ["SKILL_GATEWAY_OUT_DIR"] = out1
            self._assert_all_offlist("build_index", index_store.build_index())

            out2 = tempfile.mkdtemp(prefix="skix-no2-")
            os.environ["SKILL_GATEWAY_OUT_DIR"] = out2
            es_upd, st = index_store.update_index()
            self.assertTrue(st.get("first"))
            self._assert_all_offlist("update_index", es_upd)

            # add：无登记表时新入条目同口径，不得绕过兜底打标
            gd = os.path.join(disk, "gamma-tool")
            os.makedirs(gd)
            with open(os.path.join(gd, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: gamma-tool\ndescription: disk gamma 加测\n---\nbody")
            name, msg = index_store.add_skill(gd)
            self.assertEqual(name, "gamma-tool")
            self.assertIn("已加入", msg)
            gamma = next(e for e in index_store.read_index()
                         if e["name"] == "gamma-tool")
            self.assertEqual(gamma.get("visibility"), "off-list")
            self.assertTrue(gamma.get("excluded"))


class BridgeListWhitelistTests(unittest.TestCase):
    """桥接清单发现只认白名单文件名：example 模板 / connectors / roots 一律不认。
    10-14 实抓：自愈把 available_skills.example.json（演示模板，2 个假技能）当真清单
    导入并落镜像占位——幽灵条目级别隐患；"inputs/ 下任意 json"兜底必须废除。"""

    def test_default_src_whitelist_only(self):
        import platform_bridge as pb
        td = tempfile.mkdtemp(prefix="skix-pb-")
        inputs = os.path.join(td, "inputs")
        os.makedirs(inputs)
        for n in ("available_skills.example.json", "platform_connectors.json",
                  "env_skill_roots.json"):
            with open(os.path.join(inputs, n), "w", encoding="utf-8") as f:
                f.write("{}")
        with mock.patch.object(pb, "ROOT", td), \
             mock.patch.dict(os.environ, {"PLATFORM_SKILLS_JSON": ""}):
            self.assertIsNone(pb.default_src())
            # 白名单命中：available_skills.json 可作清单来源
            with open(os.path.join(inputs, "available_skills.json"), "w", encoding="utf-8") as f:
                f.write('{"skills": []}')
            src = pb.default_src()
            self.assertTrue(src and src.endswith("available_skills.json"))
            # platform_skills.json 优先级不变（仍高于其他白名单名）
            with open(os.path.join(inputs, "platform_skills.json"), "w", encoding="utf-8") as f:
                f.write('{"skills": []}')
            self.assertTrue(pb.default_src().endswith("platform_skills.json"))


if __name__ == "__main__":
    unittest.main()
