#!/usr/bin/env python3
"""B54（任务 B4）：以 B53 数据集训练并独立验证第一版真实 ARX ROM。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flow_control.rom.b54_from_b53 import main


if __name__ == "__main__":
    raise SystemExit(main())
