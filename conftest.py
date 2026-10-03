# conftest.py - pytest 全局隔离：
# 1) 让裸跑 pytest 即可导入 scripts/ 下的模块（无需手动设 PYTHONPATH）
# 2) 把运行时产物重定向到临时目录 —— 测试里的自愈用例（auto_update_on_miss 等）
#    会自动建索引，若不隔离就会把 output/ 产物写进源数据目录，弄脏源树
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

_prod = os.environ.setdefault(
    "SKILL_GATEWAY_OUT_DIR",
    os.path.join(tempfile.mkdtemp(prefix="skix-tests-"), "output"),
)


# 测试环境不遗留：会话结束自动清扫本进程产物 + 超过 6 小时的陈旧 skix-* 临时物
def _sweep_test_leftovers():
    import glob
    import shutil
    import time
    now = time.time()
    for p in glob.glob(os.path.join(tempfile.gettempdir(), "skix-*")):
        try:
            if os.path.getmtime(p) < now - 6 * 3600 or p.startswith(os.path.join(tempfile.gettempdir(), "skix-tests-")):
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except Exception:
            pass


import atexit
atexit.register(_sweep_test_leftovers)
