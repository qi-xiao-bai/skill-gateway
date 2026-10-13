#!/usr/bin/env python3
"""translate.py - API 现场翻译（英→中），翻译结果只写网关自己的运行时数据层
（登记表/缓存），绝不触碰技能库的源文件。默认端点 MyMemory（免费无密钥），
可经 skill_profile.json 的 translate.endpoint 覆盖。失败/离线静默降级——
翻译是增强能力，绝不能阻断建索引主流程。
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

# 端点链：谷歌非官方网页端点（免费无密钥、质量高）优先，MyMemory 兜底。
# 谷歌官方 Cloud Translation API 是收费的（$20/百万字符）不进默认链；
# profile 的 translate.endpoints 可整链覆盖（如接百度/DeepL 等带密钥端点）。
DEFAULT_ENDPOINTS = [
    "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}",
    "https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN",
]
_CACHE = None


def _cache_path():
    from paths import out_path
    return out_path(".translate-cache.json")


def _load_cache():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            _CACHE = json.load(f)
    except Exception:
        _CACHE = {}
    return _CACHE


def _save_cache():
    try:
        import index_store
        p = _cache_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        index_store.atomic_write(p, json.dumps(_CACHE or {}, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _endpoints():
    """端点链：profile translate.endpoints（单条或数组）> 默认双端点链。"""
    try:
        from paths import load_skill_profile
        eps = (load_skill_profile().get("translate") or {}).get("endpoints")
        if eps:
            return [eps] if isinstance(eps, str) else [str(x) for x in eps]
    except Exception:
        pass
    return DEFAULT_ENDPOINTS


def _parse(tpl, data):
    """按端点解析译文：谷歌 gtx 返回嵌套数组，MyMemory 返回 responseData。"""
    try:
        if "translate.googleapis" in tpl:
            return "".join(seg[0] for seg in data[0] if seg and seg[0])
        return (data.get("responseData") or {}).get("translatedText") or ""
    except Exception:
        return ""


def translate_en_zh(text, timeout=8):
    """英文→中文。命中缓存直接返回；失败返回 ""（调用方降级为原文展示）。"""
    text = (text or "").strip()
    if not text or re.search(r"[\u4e00-\u9fff]", text[:40]):
        return text  # 空文本或本来就是中文 → 原样返回
    key = text[:400]
    cache = _load_cache()
    if key in cache:
        return cache[key]
    for tpl in _endpoints():
        try:
            url = tpl.replace("{q}", urllib.parse.quote(key))
            req = urllib.request.Request(url, headers={"User-Agent": "skill-gateway/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            out = _parse(tpl, data)
            # 端点偶发把错误文案当译文返回，做一次粗糙防御
            if out and len(out) > 2 and "INVALID" not in out.upper()[:30]:
                cache[key] = out
                cache.pop("_endpoint_fail_streak", None)  # 成功一次即清零
                _save_cache()
                time.sleep(0.3)  # 免费端点礼貌间隔
                return out
        except Exception:
            continue
    # 全部端点失败 → 递增连败计数（供 failure_note 可观测性，翻译行为本身不变——静默降级设计）
    cache["_endpoint_fail_streak"] = int(cache.get("_endpoint_fail_streak", 0)) + 1
    _save_cache()
    return ""


def failure_note():
    """端点连续失败时的可观测性提示；正常返回 None。翻译行为本身不变——静默降级设计。"""
    n = (_load_cache() or {}).get("_endpoint_fail_streak") or 0
    return (f"翻译端点不可达（连续失败 {n} 次），中文描述请走自产通道：fill_description_zh.py"
            if n else None)

def translate_missing(entries, time_budget=20.0):
    """给缺中文描述的条目现场翻译并回填（只改内存副本，由调用方落盘到登记表）。
    time_budget: 秒级预算——超时停止翻译并返回部分结果（平台 30s 限制的防御：
    先写登记表再翻译，被杀时登记表已在位，下次从缓存续翻）。
    返回 (翻译成功数, 失败数)。"""
    import time as _time
    _t0 = _time.time()
    ok = fail = 0
    for it in entries:
        if it.get("description_zh") or not it.get("description"):
            continue
        if re.search(r"[一-鿿]", it["description"][:40]):
            continue  # 描述本来就是中文 → 不翻也不标
        if _time.time() - _t0 > time_budget:
            print(f"[translate] 时间预算 {time_budget:.0f}s 已用完，剩余条目下次续翻"
                  f"（已翻 {ok} / 失败 {fail} / 剩余 {len(entries) - ok - fail}）")
            break
        zh = translate_en_zh(it["description"])
        if zh:
            it["description_zh"] = zh
            ok += 1
            _save_cache()  # 每条成功即存缓存——被杀也不丢已翻内容
        else:
            fail += 1
    return ok, fail
