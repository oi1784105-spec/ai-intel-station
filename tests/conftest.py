"""pytest 公共配置：把仓库根与 tests/ 加入 `sys.path`，使 `pipeline` 与 `helpers` 可导入。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))