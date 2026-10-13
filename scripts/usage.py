#!/usr/bin/env python3
# usage.py - 注入台账与 A/B 对比：网关命令注入量的真实记录（bench 命令的底层）
# created 2026-10-14 qjl
import datetime
import json
import os

import paths

# 台账口径 = stdout 注入字符：命令打印多少，模型就读多少——这是可精确实测的量，
# 不做任何推算。bench 自身不记录（测量命令，记录只会自我膨胀）。
SKIP_RECORD = {"bench"}


def ledger_path():
    # 文件名用字面量传给 out_path：元测试按字面量扫描强制 clean 名单覆盖本产物
    return paths.out_path("usage-ledger.jsonl")


def record(cmd, chars):
    """追加一条注入记录。台账绝不阻塞命令主流程：任何落盘失败静默放过。"""
    try:
        os.makedirs(os.path.dirname(ledger_path()), exist_ok=True)
        with open(ledger_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "cmd": cmd,
                "chars": int(chars),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_entries(limit=None):
    p = ledger_path()
    if not os.path.isfile(p):
        return []
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:] if limit else rows


def est_tokens(chars):
    return int(round(chars / 4.0)) if chars else 0


class CountingStdout:
    """stdout 代理：原样透传打印 + 累计字符数。main() 套上它即得每命令注入量。"""

    def __init__(self, inner):
        self._inner = inner
        self.chars = 0

    def write(self, s):
        self.chars += len(s)
        return self._inner.write(s)

    def flush(self):
        return self._inner.flush()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def bench_text(baseline=None, gateway=None):
    """bench 输出。两个 token 都给 = A/B 会话对比（数字取自平台会话看板的真实读数）；
    都不给 = 注入台账汇总。一半给一半不给不成立，由 cmd_bench 拦。"""
    if baseline is not None and gateway is not None:
        diff = baseline - gateway
        pct = (100.0 * diff / baseline) if baseline else 0.0
        lines = [
            "# Token 对比（bench）",
            f"基线会话（未调用网关）: {baseline:>12,} tokens",
            f"网关会话（调用网关）  : {gateway:>12,} tokens",
            f"会话 token 差值       : {diff:>12,} tokens"
            + (f"（少 {pct:.1f}%）" if diff > 0 else ""),
        ]
        if diff < 0:
            lines.append("  差值为负：网关会话消耗更高，如实呈现")
        return "\n".join(lines)
    rows = load_entries()
    if not rows:
        return "\n".join([
            "# 网关注入台账（bench）",
            "  台账为空：本环境还没有命令执行记录",
            "  A/B 实测跑法：同一任务先不调用网关跑一遍、再调用网关跑一遍，",
            "  两遍会话 Σtoken 从平台会话看板取值，然后运行：",
            "  bench <基线Σtoken> <网关Σtoken>",
        ])
    total = sum(r.get("chars", 0) for r in rows)
    lines = [
        "# 网关注入台账（bench）",
        f"累计 {len(rows)} 次调用 / 注入 {total:,} 字符 ≈ {est_tokens(total):,} tokens",
        "最近 5 条：",
    ]
    for r in rows[-5:]:
        lines.append(f"  {str(r.get('ts', ''))[:19]}  {str(r.get('cmd', '?'))[:24]:<24}"
                     f" {r.get('chars', 0):>9,} 字符")
    lines += [
        "A/B 实测跑法：同一任务先不调用网关跑一遍、再调用网关跑一遍，",
        "两遍会话 Σtoken 从平台会话看板取值，然后运行：",
        "bench <基线Σtoken> <网关Σtoken>",
    ]
    return "\n".join(lines)
