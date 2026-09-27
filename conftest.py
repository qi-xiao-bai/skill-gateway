# conftest.py - 让 pytest 裸跑即可导入 scripts/ 下的模块（无需手动设 PYTHONPATH）
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
