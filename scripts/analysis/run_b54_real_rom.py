#!/usr/bin/env python3
"""B54 真实 ARX ROM 一键运行入口。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flow_control.rom.b54_real_rom import main


if __name__ == "__main__":
    raise SystemExit(main())
