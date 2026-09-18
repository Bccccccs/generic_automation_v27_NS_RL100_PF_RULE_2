#!/usr/bin/env python3
"""B53 真实 STAR 训练数据独立入口。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flow_control.b53_dataset.builder import main


if __name__ == "__main__":
    raise SystemExit(main())
