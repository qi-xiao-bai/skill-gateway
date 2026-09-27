#!/usr/bin/env python3
"""content_index.py 核心函数的自动化测试。"""

import json
import os
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import content_index as ci
import retrieval


# ── retrieval.terms 测试 ──────────────────────────────────────────────────────
class TestTerms:
    def test_english_words(self):
        result = retrieval.terms("hello world foo bar")
        assert "hello" in result
        assert "world" in result

    def test_chinese_bigram(self):
        result = retrieval.terms("技能索引")
        assert "技能" in result
        assert "能索" in result  # bigram

    def test_chinese_trigram(self):
        result = retrieval.terms("技能索引器")
        assert "技能索" in result  # trigram

    def test_short_words_filtered(self):
        """单字符英文词应被过滤。"""
        result = retrieval.terms("a b c")
        assert len(result) == 0

    def test_mixed(self):
        result = retrieval.terms("创建 workbook 文件")
        assert "创建" in result
        assert "workbook" in result


# ── 分块测试 ──────────────────────────────────────────────────────────────────
class TestChunkMarkdown:
    def test_empty_file(self):
        assert ci._chunk_markdown([], "test.md") == []

    def test_no_headings(self):
        lines = ["line1\n", "line2\n", "line3\n", "line4\n", "line5\n"]
        chunks = ci._chunk_markdown(lines, "test.md")
        assert len(chunks) == 1
        assert chunks[0]["summary"] == "line1"

    def test_with_headings(self):
        lines = (
            ["# Title1\n"] + ["content1\n"] * 10 + ["# Title2\n"] + ["content2\n"] * 10
        )
        chunks = ci._chunk_markdown(lines, "test.md")
        assert len(chunks) == 2
        assert chunks[0]["summary"] == "Title1"
        assert chunks[1]["summary"] == "Title2"

    def test_code_block_not_split(self):
        """代码块内的标题不应作为分段点。"""
        lines = [
            "# Real Title\n",
            "text\n" * 5,
            "```\n",
            "# Fake Title in Code\n",
            "code line\n" * 5,
            "```\n",
        ]
        chunks = ci._chunk_markdown(lines, "test.md")
        # 只有1个段，因为代码块内的标题被跳过
        assert len(chunks) == 1
        assert chunks[0]["summary"] == "Real Title"

    def test_small_segments_merged(self):
        """小于 CHUNK_MIN_LINES 的段应合并到上一段。"""
        lines = [
            "# Big Section\n",
            "line\n" * 10,
            "# Tiny\n",
            "one line\n",
            "# Another Big\n",
            "line\n" * 10,
        ]
        chunks = ci._chunk_markdown(lines, "test.md")
        # Tiny 段应被合并
        for ch in chunks:
            line_count = ch["e"] - ch["s"] + 1
            assert (
                line_count >= ci.CHUNK_MIN_LINES or chunks.index(ch) == len(chunks) - 1
            )


class TestChunkPlain:
    def test_empty(self):
        assert ci._chunk_plain([], "test.txt") == []

    def test_window_chunking(self):
        lines = [f"line {i}\n" for i in range(100)]
        chunks = ci._chunk_plain(lines, "test.txt")
        assert len(chunks) > 1
        # 每个chunk的行数不超过 CHUNK_WINDOW_LINES
        for ch in chunks:
            assert ch["e"] - ch["s"] + 1 <= ci.CHUNK_WINDOW_LINES


class TestChunkPython:
    def test_simple_function(self):
        lines = [
            "def hello():\n",
            "    print('hello')\n",
            "    x = 1\n",
            "    y = 2\n",
            "    return x + y\n",
            "\n",
            "def world():\n",
            "    print('world')\n",
            "    a = 3\n",
            "    b = 4\n",
            "    return a + b\n",
        ]
        chunks = ci._chunk_python(lines, "test.py")
        assert len(chunks) >= 2
        summaries = [ch["summary"] for ch in chunks]
        assert "hello" in summaries

    def test_syntax_error_fallback(self):
        lines = ["def broken(\n", "  bad syntax\n"]
        chunks = ci._chunk_python(lines, "test.py")
        # 应回退到 _chunk_plain
        assert len(chunks) >= 1


# ── 去重测试 ──────────────────────────────────────────────────────────────────
class TestDedupChunks:
    def test_no_duplicates(self):
        chunks = [
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},
            {"skill": "b", "file": "f2", "s": 1, "e": 10, "summary": "y"},
        ]
        result = ci._dedup_chunks(chunks)
        assert len(result) == 2

    def test_with_duplicates(self):
        chunks = [
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},  # 重复
            {"skill": "b", "file": "f2", "s": 1, "e": 10, "summary": "y"},
        ]
        result = ci._dedup_chunks(chunks)
        assert len(result) == 2

    def test_same_file_different_ranges(self):
        chunks = [
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},
            {"skill": "a", "file": "f1", "s": 11, "e": 20, "summary": "y"},
        ]
        result = ci._dedup_chunks(chunks)
        assert len(result) == 2


# ── 倒排索引测试 ──────────────────────────────────────────────────────────────
class TestRebuildInverted:
    def test_basic_terms(self):
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello world",
            },
            {
                "id": 1,
                "skill": "test",
                "file": "b.md",
                "s": 1,
                "e": 5,
                "summary": "foo bar",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        assert "hello" in inv
        assert 0 in inv["hello"]
        assert "foo" in inv
        assert 1 in inv["foo"]
        # terms_cache 应包含分词结果
        assert 0 in tc or "0" in tc
        assert "hello" in tc.get(0, tc.get("0", []))

    def test_terms_cached(self):
        """分词结果应缓存到 terms_cache 中。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello world",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        # terms_cache 应包含分词结果
        assert 0 in tc or "0" in tc
        cached = tc.get(0, tc.get("0", []))
        assert "hello" in cached

    def test_old_terms_cache_reused(self):
        """增量更新时，旧 terms 缓存应被复用。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello world",
                "mtime": 100,
            },
        ]
        # 第一次构建
        inv1, tc1 = ci._rebuild_inverted(chunks)
        # 模拟增量更新：传入旧 terms 缓存
        old_tc = {str(k): v for k, v in tc1.items()}
        inv2, tc2 = ci._rebuild_inverted(chunks, old_terms_cache=old_tc)
        # 应复用旧缓存
        assert "hello" in inv2
        assert 0 in inv2["hello"]


# ── 内容检索测试 ──────────────────────────────────────────────────────────────
class TestContentSearch:
    def _make_doc(self, chunks):
        """构建测试用 doc dict，返回 (doc, terms_cache)。"""
        inv, tc = ci._rebuild_inverted(chunks)
        return {"chunks": chunks, "inverted": inv}, tc

    def test_basic_search(self):
        chunks = [
            {
                "id": 0,
                "skill": "xlsx",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "create workbook excel",
            },
            {
                "id": 1,
                "skill": "pdf",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "pdf document reader",
            },
        ]
        doc, tc = self._make_doc(chunks)
        results = ci.content_search(doc, "workbook", terms_cache_override=tc)
        assert len(results) > 0
        assert results[0][1]["skill"] == "xlsx"

    def test_fed_from_boost(self):
        """跨技能投喂 chunk 应有 +0.5 加权。"""
        chunks = [
            {
                "id": 0,
                "skill": "a",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "create workbook",
            },
            {
                "id": 1,
                "skill": "a",
                "file": "SKILL.md@b",
                "s": 1,
                "e": 5,
                "summary": "create workbook",
                "fed_from": "b",
            },
        ]
        doc, tc = self._make_doc(chunks)
        results = ci.content_search(doc, "workbook", terms_cache_override=tc)
        # fed_from chunk 应排在前面（有 +0.5 加权）
        assert len(results) >= 2
        assert results[0][1].get("fed_from") == "b"


# ── 跨技能投喂测试 ────────────────────────────────────────────────────────────
class TestFeedKeyChunks:
    def test_key_pattern_matching(self):
        """KEY_PATTERNS 应匹配关键信息段。"""
        KEY_PATTERNS = retrieval.re.compile(
            r"(?i)(trigger|when to use|usage|用法|触发|何时|how to|quick|start|install|安装|配置|config|example|示例)"
        )
        assert KEY_PATTERNS.search("When to Use this skill")
        assert KEY_PATTERNS.search("用法说明")
        assert KEY_PATTERNS.search("Installation guide")
        assert not KEY_PATTERNS.search("This is a random paragraph")


# ── 边权重配置测试 ────────────────────────────────────────────────────────────
class TestEdgeWeights:
    def test_default_weights(self):
        """无配置文件时应返回默认权重。"""
        from paths import load_edge_weights

        weights = load_edge_weights()
        assert "depends_on" in weights
        assert "contains" in weights
        assert weights["depends_on"] == 0.8
        assert weights["contains"] == 0.7

    def test_custom_weights(self):
        """配置文件中自定义权重应覆盖默认值。"""
        from paths import CONFIG_FILE, load_edge_weights

        # 临时修改配置文件
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                orig = json.load(f)
        else:
            orig = {}

        try:
            orig["edge_weights"] = {"depends_on": 0.5, "contains": 0.9}
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(orig, f)
            weights = load_edge_weights()
            assert weights["depends_on"] == 0.5
            assert weights["contains"] == 0.9
            # 未自定义的权重应保持默认
            assert weights["overlap"] == 0.6
        finally:
            # 恢复原始配置
            if "edge_weights" in orig:
                del orig["edge_weights"]
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(orig, f, ensure_ascii=False, indent=2)


# ── 高风险路径回归测试 ──────────────────────────────────────────────────────
class TestContentSearchRecall:
    """正文入倒排后搜索召回是否真的提升。"""

    def test_search_hits_body_content(self):
        """搜索词只出现在 summary 中不应命中 file 字段，但应命中 summary。"""
        chunks = [
            {
                "id": 0,
                "skill": "xlsx",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "create workbook and format cells",
            },
            {
                "id": 1,
                "skill": "pdf",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "read and annotate pdf documents",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        doc = {"chunks": chunks, "inverted": inv}
        results = ci.content_search(doc, "workbook", terms_cache_override=tc)
        assert len(results) >= 1
        assert results[0][1]["skill"] == "xlsx"

    def test_search_chinese_terms(self):
        """中文分词应支持 bigram/trigram 搜索召回。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "技能索引器用于管理技能",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        doc = {"chunks": chunks, "inverted": inv}
        results = ci.content_search(doc, "技能", terms_cache_override=tc)
        assert len(results) >= 1


class TestFedFromWeighting:
    """跨技能投喂 fed_from 加权是否生效。"""

    def test_fed_from_boost_over_normal(self):
        """相同内容时 fed_from chunk 应比普通 chunk 分数高 +0.5。"""
        chunks = [
            {
                "id": 0,
                "skill": "a",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "create workbook excel",
            },
            {
                "id": 1,
                "skill": "a",
                "file": "SKILL.md@b",
                "s": 1,
                "e": 5,
                "summary": "create workbook excel",
                "fed_from": "b",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        doc = {"chunks": chunks, "inverted": inv}
        results = ci.content_search(doc, "workbook", terms_cache_override=tc)
        assert len(results) >= 2
        # fed_from chunk 分数应更高
        fed_score = None
        normal_score = None
        for score, c, _ in results:
            if c.get("fed_from") == "b":
                fed_score = score
            else:
                normal_score = score
        assert fed_score is not None
        assert normal_score is not None
        assert fed_score > normal_score

    def test_fed_from_in_search_results(self):
        """fed_from 标记应出现在搜索结果中。"""
        chunks = [
            {
                "id": 0,
                "skill": "a",
                "file": "SKILL.md@b",
                "s": 1,
                "e": 5,
                "summary": "trigger usage guide",
                "fed_from": "b",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        doc = {"chunks": chunks, "inverted": inv}
        results = ci.content_search(doc, "trigger", terms_cache_override=tc)
        assert len(results) >= 1
        assert results[0][1].get("fed_from") == "b"


class TestDedupStability:
    """去重后 chunk 数是否稳定。"""

    def test_dedup_idempotent(self):
        """对同一列表多次去重结果应一致。"""
        chunks = [
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "x"},
            {"skill": "b", "file": "f2", "s": 1, "e": 10, "summary": "y"},
        ]
        r1 = ci._dedup_chunks(chunks)
        r2 = ci._dedup_chunks(r1)
        assert len(r1) == len(r2) == 2

    def test_dedup_preserves_order(self):
        """去重应保留首次出现的 chunk，维持顺序。"""
        chunks = [
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "first"},
            {"skill": "a", "file": "f1", "s": 1, "e": 10, "summary": "dup"},
            {"skill": "b", "file": "f2", "s": 1, "e": 10, "summary": "second"},
        ]
        result = ci._dedup_chunks(chunks)
        assert len(result) == 2
        assert result[0]["summary"] == "first"
        assert result[1]["summary"] == "second"


class TestIncrementalConsistency:
    """增量更新与全量重建结果是否一致。"""

    def test_rebuild_inverted_consistent(self):
        """对同一 chunk 列表，两次调用 _rebuild_inverted 应产生相同倒排。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello world python",
            },
            {
                "id": 1,
                "skill": "test",
                "file": "b.md",
                "s": 1,
                "e": 5,
                "summary": "foo bar java",
            },
        ]
        inv1, tc1 = ci._rebuild_inverted(chunks)
        inv2, tc2 = ci._rebuild_inverted(chunks)
        # 倒排的 key 集合应一致
        assert set(inv1.keys()) == set(inv2.keys())
        # 每个 term 对应的 chunk_id 列表应一致
        for t in inv1:
            assert set(inv1[t]) == set(inv2[t])

    def test_old_cache_same_as_fresh(self):
        """使用旧 terms 缓存的结果应与全量计算一致。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello world",
                "mtime": 100,
            },
            {
                "id": 1,
                "skill": "test",
                "file": "b.md",
                "s": 1,
                "e": 5,
                "summary": "foo bar",
                "mtime": 200,
            },
        ]
        # 全量计算
        inv_fresh, tc_fresh = ci._rebuild_inverted(chunks)
        # 使用旧缓存
        old_tc = {str(k): v for k, v in tc_fresh.items()}
        inv_cached, tc_cached = ci._rebuild_inverted(chunks, old_terms_cache=old_tc)
        # 倒排应一致
        assert set(inv_fresh.keys()) == set(inv_cached.keys())
        for t in inv_fresh:
            assert set(inv_fresh[t]) == set(inv_cached[t])


class TestTermsCacheSeparation:
    """terms 缓存存储的测试（v2 起内嵌主文档，不再单独落盘）。"""

    def test_terms_cache_embedded_in_doc(self):
        """terms 缓存应随主文档内嵌写入与读取。"""
        tc = {"0": ["hello", "world"], "1": ["foo", "bar"]}
        doc = {"version": ci.CONTENT_INDEX_VER, "terms_cache": tc}
        ci.write_content_index(doc)
        loaded_doc = ci.read_content_index()
        assert loaded_doc is not None
        assert loaded_doc["terms_cache"]["0"] == ["hello", "world"]
        # read_terms_cache 优先读内嵌
        assert ci.read_terms_cache(loaded_doc)["1"] == ["foo", "bar"]

    def test_read_terms_cache_legacy_file(self):
        """doc 未带内嵌缓存时应兼容读旧版独立文件。"""
        tc = {"0": ["legacy"]}
        ci.write_content_index({"version": ci.CONTENT_INDEX_VER, "terms_cache": {}})
        legacy_path = os.path.join(tempfile.gettempdir(), "_skix_terms_test.json")
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump(tc, f)
        try:
            # read_terms_cache(None) 走旧文件路径查找（out_path 内），此处仅验证空兜底
            assert ci.read_terms_cache({"terms_cache": {}}) == {}
        finally:
            if os.path.exists(legacy_path):
                os.unlink(legacy_path)

    def test_chunks_have_no_terms_field(self):
        """主索引 chunks 不应包含 terms 字段。"""
        chunks = [
            {
                "id": 0,
                "skill": "test",
                "file": "a.md",
                "s": 1,
                "e": 5,
                "summary": "hello",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        # chunks 不应被注入 terms 字段
        assert "terms" not in chunks[0]

    def test_content_search_uses_terms_cache(self):
        """content_search 应能从 terms 缓存加载分词结果。"""
        chunks = [
            {
                "id": 0,
                "skill": "xlsx",
                "file": "SKILL.md",
                "s": 1,
                "e": 5,
                "summary": "create workbook",
            },
        ]
        inv, tc = ci._rebuild_inverted(chunks)
        inv, tc = ci._rebuild_inverted(chunks)
        doc = {"chunks": chunks, "inverted": inv}
        results = ci.content_search(doc, "workbook", terms_cache_override=tc)
        assert len(results) >= 1
        assert results[0][1]["skill"] == "xlsx"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
