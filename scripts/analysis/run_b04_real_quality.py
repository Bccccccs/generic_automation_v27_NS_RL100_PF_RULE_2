#!/usr/bin/env python3
"""Command-line entry point for B04 real-case quality delivery generation."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flow_control.star_ingest.b04_real_quality import main


if __name__ == "__main__":
    raise SystemExit(main())
